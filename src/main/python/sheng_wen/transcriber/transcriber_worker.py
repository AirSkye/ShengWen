import os
import re
import shutil
import json
import ffmpeg
import asyncio
import time
from threading import Lock, Thread
from collections import deque
from typing import Any, Awaitable, Callable, Dict, TYPE_CHECKING
from ..worker import Worker, TaskCancelledError
from .transcriber import Transcriber, TranscriptionResult, TranscriptionCancelled
from ..utils.logger import logger
from ..api import notify_task_update
from ..utils.ffmpeg_helper import FFmpegHelper

# 使用 TYPE_CHECKING 来避免运行时循环导入
if TYPE_CHECKING:
    from ..llm.llm_worker import LLMWorker

_CUDA_DLL_PATTERN = re.compile(
    r"(cublas(?:Lt)?64_(\d+)\.dll|cudart64_(\d+)\.dll|cudnn64(?:_\d+)?\.dll)",
    re.IGNORECASE,
)


def _extract_missing_cuda_runtime_dll(error_text: str) -> tuple[str | None, str | None]:
    match = _CUDA_DLL_PATTERN.search(error_text or "")
    if not match:
        return (None, None)
    dll_name = match.group(1)
    cuda_major = match.group(2) or match.group(3)
    return (dll_name, cuda_major)


def _build_actionable_transcription_error(error_text: str) -> str:
    text = str(error_text or "").strip()
    if not text:
        return "转录失败（无错误详情）。"

    lowered = text.lower()
    missing_dll, cuda_major = _extract_missing_cuda_runtime_dll(text)
    maybe_cuda_runtime_error = bool(missing_dll) or "cuda" in lowered or "cublas" in lowered
    if not maybe_cuda_runtime_error:
        return text

    lines = [text, "", "CUDA 修复建议："]
    lines.append("1) 先切换到 CPU 转录，保证任务可继续。")
    if missing_dll:
        if cuda_major:
            lines.append(f"2) 当前缺失 {missing_dll}（对应 CUDA {cuda_major} 运行库），请安装对应 CUDA Runtime。")
        else:
            lines.append(f"2) 当前缺失 {missing_dll}，请安装对应 CUDA Runtime。")
    else:
        lines.append("2) 检查 CUDA Runtime 与显卡驱动是否安装完整。")
    lines.extend(
        [
            "3) 确认 PATH 包含 CUDA bin 目录（例如 C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.x\\bin）。",
            "4) 重启 ShengWen 后重新选择 CUDA。",
        ]
    )
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """将秒数格式化为 HHMMSS 格式的字符串。"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02d}{minutes:02d}{seconds:02d}"


class TranscriberWorker(Worker):
    """
    一个工作单元，可以从视频中提取音频，然后转录音频文件，
    并将详细的转录结果保存到中间文件，最后将文件路径传递给下一个工作单元。
    """
    def __init__(
        self,
        name: str,
        transcriber: Transcriber,
        next_worker: 'LLMWorker | None' = None,
        next_worker_factory: Callable[[], Awaitable['LLMWorker']] | None = None,
    ):
        """
        初始化 TranscriberWorker。
        """
        super().__init__(name)
        self._transcriber = transcriber
        self._transcriber_lock = Lock()
        self._next_worker = next_worker
        self._next_worker_factory = next_worker_factory

    def update_transcriber(self, transcriber: Transcriber):
        """在运行时替换转录器实例。"""
        with self._transcriber_lock:
            self._transcriber = transcriber
        logger.info(f"[{self.name}] 转录器实例已更新。")

    async def _enqueue_summary(self, payload: Dict[str, Any]):
        next_worker = self._next_worker
        if next_worker is None and self._next_worker_factory is not None:
            next_worker = await self._next_worker_factory()
            self._next_worker = next_worker
        if next_worker is None:
            raise RuntimeError("总结工作单元尚未配置")
        await next_worker.add_task(payload)

    def _build_event_callback(self, task_id: str | None):
        if not task_id:
            return None

        metadata: dict[str, Any] = {"provider": "tingwu"}

        def callback(event: dict[str, Any]):
            stage = str(event.get("stage") or "")
            metadata["stage"] = stage
            metadata["message"] = str(event.get("message") or "")
            metadata["updated_at"] = event.get("timestamp")
            if event.get("transId"):
                metadata["remote_task_id"] = str(event["transId"])
            if stage in {"uploading", "upload_complete"}:
                metadata["upload_progress"] = round(float(event.get("progress") or 0.0) * 100, 1)
                metadata["uploaded_bytes"] = int(event.get("uploadedBytes") or 0)
                metadata["total_bytes"] = int(event.get("totalBytes") or 0)
            if stage == "polling":
                raw_remote_progress = event.get("remoteProgress", event.get("progress", 0.0))
                try:
                    remote_progress = float(raw_remote_progress or 0.0)
                except (TypeError, ValueError):
                    remote_progress = 0.0
                if remote_progress <= 1:
                    remote_progress *= 100
                metadata["remote_progress"] = round(max(0.0, min(remote_progress, 100.0)), 1)
                metadata["remote_status"] = event.get("remoteStatus")
                metadata["poll_count"] = int(event.get("pollCount") or 0)
                metadata["elapsed_seconds"] = float(event.get("elapsedSeconds") or 0.0)
            if stage == "fallback":
                metadata["provider"] = "fast_whisper_fallback"

            from ..task_updater import update_and_notify
            self._submit_coro(
                update_and_notify(
                    task_id,
                    {"transcription_meta": json.dumps(metadata, ensure_ascii=False)},
                )
            )

        return callback

    @staticmethod
    def _load_task_transcription_meta(task_id: str | None) -> dict[str, Any]:
        if not task_id:
            return {}
        try:
            from ..db import db

            task = db.get_task(task_id) or {}
            raw = task.get("transcription_meta")
            if isinstance(raw, dict):
                return dict(raw)
            parsed = json.loads(str(raw)) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _run_transcriber(
        self,
        transcriber: Transcriber,
        audio_file: str,
        task_id: str | None,
        progress_callback,
        cancel_check,
    ) -> TranscriptionResult:
        contextual_transcribe = getattr(transcriber, "transcribe_with_context", None)
        if callable(contextual_transcribe):
            return contextual_transcribe(
                audio_file,
                task_id=task_id,
                progress_callback=progress_callback,
                event_callback=self._build_event_callback(task_id),
                cancel_check=cancel_check,
            )
        return transcriber.transcribe(
            audio_file,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    def transcribe_media_file(
        self,
        media_file: str,
        task_id: str | None = None,
        progress_callback=None,
    ) -> TranscriptionResult:
        """Transcribe an existing audio/media file and return the raw result."""
        audio_file = str(media_file or "")
        if not audio_file:
            raise FileNotFoundError("media_file is empty")

        if os.path.splitext(audio_file)[1].lower() == ".m4s":
            converted_audio_file = self._convert_m4s_to_m4a(audio_file, task_id=task_id)
            if not converted_audio_file:
                raise RuntimeError("m4s 转换 m4a 失败")
            audio_file = converted_audio_file

        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"找不到要转录的音频文件: {audio_file}")

        def cancel_check() -> bool:
            return bool(task_id and self.is_task_cancelled(task_id))

        def wrapped_progress(progress: float):
            if cancel_check():
                raise TaskCancelledError("任务已取消，停止转录。")
            if progress_callback:
                progress_callback(progress)

        with self._transcriber_lock:
            transcriber = self._transcriber
        return self._run_transcriber(
            transcriber,
            audio_file,
            task_id,
            wrapped_progress,
            cancel_check,
        )

    def _extract_audio(self, video_path: str, audio_path: str, task_id: str | None = None) -> bool:
        """
        使用 ffmpeg 从视频文件中提取音频。

        返回:
            如果提取成功，则返回 True，否则返回 False。
        """
        # 使用 FFmpegHelper 配置 ffmpeg 路径
        if not FFmpegHelper.configure_ffmpeg_python():
            logger.error(f"[{self.name}] 错误: FFmpeg 配置失败。")
            logger.error(f"[{self.name}] 请确保已安装 imageio-ffmpeg: pip install imageio-ffmpeg")
            return False

        # 若目标音频已存在且不早于视频文件，优先复用，避免重复提取。
        try:
            if os.path.exists(audio_path):
                audio_size = os.path.getsize(audio_path)
                if audio_size > 0:
                    audio_mtime = os.path.getmtime(audio_path)
                    video_mtime = os.path.getmtime(video_path) if os.path.exists(video_path) else 0.0
                    if audio_mtime >= video_mtime:
                        logger.info(
                            f"[{self.name}] 复用已存在音频文件，跳过提取: {audio_path} "
                            f"(size={audio_size / (1024 * 1024):.1f}MB)"
                        )
                        return True
        except Exception as e:
            logger.debug(f"[{self.name}] 复用音频文件检查失败，回退常规提取: {e}")

        logger.info(f"[{self.name}] 正在从视频 '{video_path}' 中提取音频到 '{audio_path}'...")
        try:
            process = (
                ffmpeg
                .input(video_path)
                .output(audio_path, acodec='mp3', audio_bitrate='192k')
                .overwrite_output()
                .run_async(pipe_stdout=False, pipe_stderr=True)
            )

            stderr_tail: deque[str] = deque(maxlen=120)

            def _drain_stderr():
                if not process.stderr:
                    return
                try:
                    while True:
                        raw = process.stderr.readline()
                        if not raw:
                            break
                        try:
                            line = raw.decode(errors="replace").strip()
                        except Exception:
                            line = str(raw).strip()
                        if line:
                            stderr_tail.append(line)
                except Exception:
                    # 排障信息读取失败不应影响主流程
                    pass

            stderr_thread = Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            heartbeat_start = time.time()
            next_heartbeat_ts = heartbeat_start + 10.0
            while process.poll() is None:
                if task_id and self.is_task_cancelled(task_id):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except Exception:
                        process.kill()
                    raise TaskCancelledError("任务已取消，停止音频提取。")

                now = time.time()
                if now >= next_heartbeat_ts:
                    next_heartbeat_ts = now + 10.0
                    audio_size_mb = 0.0
                    try:
                        if os.path.exists(audio_path):
                            audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                    except Exception:
                        pass
                    logger.info(
                        f"[{self.name}] 音频提取进行中: elapsed={int(now - heartbeat_start)}s, "
                        f"audio_size={audio_size_mb:.1f}MB, task={task_id or '<unknown>'}"
                    )
                # 同步 worker 线程里轮询取消信号，尽量快速中断。
                time.sleep(0.2)

            if stderr_thread.is_alive():
                stderr_thread.join(timeout=0.5)

            if process.returncode != 0:
                stderr_text = "\n".join(stderr_tail).strip()
                if not stderr_text:
                    stderr_text = f"ffmpeg exited with code {process.returncode}"
                raise ffmpeg.Error("ffmpeg", b"", stderr_text.encode("utf-8", errors="replace"))

            logger.info(f"[{self.name}] 音频提取成功。")
            return True
        except ffmpeg.Error as e:
            stderr_text = ""
            if getattr(e, "stderr", None):
                try:
                    stderr_text = e.stderr.decode(errors="replace")
                except Exception:
                    stderr_text = str(e.stderr)
            logger.error(f"[{self.name}] 使用 ffmpeg 提取音频时出错: {stderr_text}")
            return False
        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.name}] 提取音频时发生未知错误: {e}", exc_info=True)
            return False

    def _convert_m4s_to_m4a(self, input_path: str, task_id: str | None = None) -> str | None:
        """
        将 m4s 音频容器转换为 m4a，避免转录器直接处理 m4s 失败。
        等价命令: ffmpeg -i input.m4s -acodec copy output.m4a
        """
        output_path = os.path.splitext(input_path)[0] + ".m4a"

        if not FFmpegHelper.configure_ffmpeg_python():
            logger.error(f"[{self.name}] 错误: FFmpeg 配置失败，无法转换 m4s。")
            return None

        try:
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                output_mtime = os.path.getmtime(output_path)
                input_mtime = os.path.getmtime(input_path) if os.path.exists(input_path) else 0.0
                if output_mtime >= input_mtime:
                    logger.info(f"[{self.name}] 复用已存在的 m4a 文件，跳过 m4s 转换: {output_path}")
                    return output_path
        except Exception as e:
            logger.debug(f"[{self.name}] m4a 复用检查失败，继续转换: {e}")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        logger.info(f"[{self.name}] 正在将 m4s 转换为 m4a: {input_path} -> {output_path}")

        try:
            process = (
                ffmpeg
                .input(input_path)
                .output(output_path, acodec="copy")
                .overwrite_output()
                .run_async(pipe_stdout=False, pipe_stderr=True)
            )

            stderr_tail: deque[str] = deque(maxlen=120)

            def _drain_stderr():
                if not process.stderr:
                    return
                try:
                    while True:
                        raw = process.stderr.readline()
                        if not raw:
                            break
                        try:
                            line = raw.decode(errors="replace").strip()
                        except Exception:
                            line = str(raw).strip()
                        if line:
                            stderr_tail.append(line)
                except Exception:
                    pass

            stderr_thread = Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            while process.poll() is None:
                if task_id and self.is_task_cancelled(task_id):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except Exception:
                        process.kill()
                    raise TaskCancelledError("任务已取消，停止 m4s 转换。")
                time.sleep(0.2)

            if stderr_thread.is_alive():
                stderr_thread.join(timeout=0.5)

            if process.returncode != 0:
                stderr_text = "\n".join(stderr_tail).strip()
                if not stderr_text:
                    stderr_text = f"ffmpeg exited with code {process.returncode}"
                raise ffmpeg.Error("ffmpeg", b"", stderr_text.encode("utf-8", errors="replace"))

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                logger.error(f"[{self.name}] m4s 转换完成但未生成有效 m4a 文件: {output_path}")
                return None

            logger.info(f"[{self.name}] m4s 转换 m4a 成功: {output_path}")
            return output_path
        except ffmpeg.Error as e:
            stderr_text = ""
            if getattr(e, "stderr", None):
                try:
                    stderr_text = e.stderr.decode(errors="replace")
                except Exception:
                    stderr_text = str(e.stderr)
            logger.error(f"[{self.name}] 使用 ffmpeg 转换 m4s 到 m4a 时出错: {stderr_text}")
            return None
        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.name}] 转换 m4s 到 m4a 时发生未知错误: {e}", exc_info=True)
            return None

    def _save_transcription_to_file(self, result: TranscriptionResult, output_path: str):
        """
        将转录结果以特定格式保存到文件。
        """
        logger.info(f"[{self.name}] 正在将转录结果保存到: {output_path}")
        try:
            with open(output_path, "w", encoding="utf-8", errors='replace') as f:
                for seg in result.segments:
                    clean_text = seg['text'].strip()
                    line = f"{_format_duration(seg['start'])}{clean_text}\n"
                    f.write(line)
            logger.info(f"[{self.name}] 成功保存转录文件。")
        except Exception as e:
            logger.error(f"[{self.name}] 保存转录文件时出错: {e}", exc_info=True)

    def process_task(self, payload: Dict[str, Any]):
        """
        处理一个转录任务。
        如果提供了 'video_file'，则先提取音频。
        然后，转录 'audio_file' 并将结果传递下去。
        """
        video_file = payload.get("video_file")
        audio_file = payload.get("audio_file")
        output_file = payload.get("output_file")
        task_id = payload.get("task_id")

        if not audio_file or not output_file:
            error_msg = "payload 中缺少 'audio_file' 或 'output_file'"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_msg}))
            return

        # 如果提供了视频文件，则先提取音频
        if video_file:
            try:
                if not self._extract_audio(video_file, audio_file, task_id=task_id):
                    # 如果提取失败，则终止该任务
                    if task_id:
                        from ..db import TaskStatus
                        from ..task_updater import update_and_notify
                        self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": "音频提取失败"}))
                    return
            except TaskCancelledError:
                logger.info(f"[{self.name}] 任务已取消，停止音频提取: {task_id}")
                return
        
        # 检查音频文件是否存在
        if os.path.splitext(str(audio_file))[1].lower() == ".m4s":
            try:
                converted_audio_file = self._convert_m4s_to_m4a(str(audio_file), task_id=task_id)
                if not converted_audio_file:
                    if task_id:
                        from ..db import TaskStatus
                        from ..task_updater import update_and_notify
                        self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": "m4s 转换 m4a 失败"}))
                    return
                audio_file = converted_audio_file
                payload["audio_file"] = converted_audio_file
            except TaskCancelledError:
                logger.info(f"[{self.name}] 任务已取消，停止 m4s 转换: {task_id}")
                return

        if not os.path.exists(audio_file):
            error_msg = f"找不到要转录的音频文件: {audio_file}"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_msg}))
            return

        try:
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING}))
                self._submit_coro(notify_task_update(task_id))

            logger.info(f"[{self.name}] 开始转录: {audio_file}")
            logger.info(f"[{self.name}] 已注册转录进度回调，等待进度上报: task={task_id or '<unknown>'}")

            last_progress_percent = -1
            last_logged_bucket = -1

            def cancel_check() -> bool:
                return self.is_task_cancelled(task_id)

            def progress_callback(progress: float):
                nonlocal last_progress_percent, last_logged_bucket
                if cancel_check():
                    raise TaskCancelledError("任务已取消，停止转录。")
                if task_id:
                    from ..db import db
                    # 将进度转换为百分比整数 (0-100)
                    clamped_progress = max(0.0, min(float(progress), 1.0))
                    progress_percent = int(clamped_progress * 100)
                    if progress_percent < last_progress_percent:
                        progress_percent = last_progress_percent
                    if progress_percent == last_progress_percent:
                        return
                    last_progress_percent = progress_percent
                    from ..task_updater import update_and_notify
                    self._submit_coro(update_and_notify(task_id, {"progress": progress_percent}))

                    # 每 10% 打点一次，便于快速判断是后端卡住还是前端未刷新。
                    progress_bucket = progress_percent // 10
                    if progress_bucket > last_logged_bucket or progress_percent >= 100:
                        last_logged_bucket = progress_bucket
                        logger.info(f"[{self.name}] 转录进度 task={task_id}: {progress_percent}%")

            with self._transcriber_lock:
                transcriber = self._transcriber
            result = self._run_transcriber(
                transcriber,
                audio_file,
                task_id,
                progress_callback,
                cancel_check,
            )
            logger.info(f"[{self.name}] 转录完成。")

            # 打印性能指标
            logger.info(f"[{self.name}] --- 性能指标 ---")
            logger.info(f"[{self.name}] 模型加载耗时: {result.model_load_time:.2f}s")
            logger.info(f"[{self.name}] 音频时长: {result.audio_duration:.2f}s")
            logger.info(f"[{self.name}] 转录耗时: {result.transcription_time:.2f}s")
            logger.info(f"[{self.name}] 实时率 (RTF): {result.real_time_factor:.2f}")
            logger.info(f"[{self.name}] 检测到的语言: {result.language} (置信度: {result.language_probability:.2f})")

            intermediate_file_path = os.path.splitext(output_file)[0] + ".txt"
            self._save_transcription_to_file(result, intermediate_file_path)

            if task_id:
                from ..db import db, TaskStatus
                # 保存转录文本到数据库供前端查看
                with open(intermediate_file_path, "r", encoding="utf-8") as f:
                    transcript = f.read()
                summary_mode = str(payload.get("summary_mode") or "standard").strip().lower()
                if summary_mode not in {"auto", "standard", "agent"}:
                    summary_mode = "standard"
                summary_style = str(payload.get("summary_style") or "classic").strip().lower()
                if summary_style not in {"classic", "report"}:
                    summary_style = "classic"

                transcription_meta = self._load_task_transcription_meta(task_id)
                transcription_meta.update(
                    {
                        "provider": result.provider,
                        "stage": "completed",
                        "message": "转录与字幕生成完成",
                        **dict(result.artifacts or {}),
                    }
                )

                # 转录完成，准备进入总结阶段
                logger.info(
                    f"[TranscriberWorker] Transcription completed: task_id={task_id}, "
                    f"status: TRANSCRIBING→SUMMARIZING, "
                    f"duration={result.transcription_time:.2f}s, audio_duration={result.audio_duration:.2f}s"
                )

                update_data = {
                    "status": TaskStatus.SUMMARIZING,
                    "progress": 0.0,
                    "transcript": transcript,
                    "transcription_time": result.transcription_time,
                    "audio_duration": result.audio_duration,
                    "transcription_meta": json.dumps(transcription_meta, ensure_ascii=False),
                    "summary_chunk_total": None,
                    "summary_chunk_done": None,
                    "summary_meta": None,
                }
                if summary_mode in {"auto", "standard", "agent"}:
                    update_data["summary_mode"] = summary_mode
                update_data["summary_style"] = summary_style
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, update_data))

            next_payload = {
                "intermediate_file_path": intermediate_file_path,
                **payload
            }
            
            self._submit_coro(self._enqueue_summary(next_payload))

        except (TaskCancelledError, TranscriptionCancelled):
            logger.info(f"[{self.name}] 任务已取消，停止后续转录流程: {task_id}")
        except Exception as e:
            logger.error(f"[{self.name}] 转录过程中发生错误: {e}", exc_info=True)
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.FAILED,
                        "error_message": _build_actionable_transcription_error(str(e)),
                    },
                ))


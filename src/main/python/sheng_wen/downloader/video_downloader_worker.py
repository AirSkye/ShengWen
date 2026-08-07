import asyncio
import glob
import json
import os
import re
import uuid
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Awaitable, Callable, Dict, List, Tuple, Optional
import yt_dlp

from ..worker import Worker, TaskCancelledError
from ..utils.logger import logger
from ..utils.ffmpeg_helper import FFmpegHelper
from .bilibili_author_resolver import resolve_bilibili_author, BilibiliAuthorResolveError

class VideoDownloaderWorker(Worker):
    """
    一个工作单元，用于从给定的 URL 下载视频。
    """
    def __init__(
        self,
        name: str,
        next_worker: Worker = None,
        summary_worker: Worker = None,
        summary_worker_factory: Callable[[], Awaitable[Worker]] | None = None,
        transcription_settings_manager: Any = None,
        output_dir: str = "temp",
    ):
        super().__init__(name)
        self.next_worker = next_worker
        self.summary_worker = summary_worker
        self.summary_worker_factory = summary_worker_factory
        self.transcription_settings_manager = transcription_settings_manager
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    async def _enqueue_summary(self, payload: Dict[str, Any]):
        summary_worker = self.summary_worker
        if summary_worker is None and self.summary_worker_factory is not None:
            summary_worker = await self.summary_worker_factory()
            self.summary_worker = summary_worker
        if summary_worker is None:
            raise RuntimeError("总结工作单元尚未配置")
        await summary_worker.add_task(payload)

    @staticmethod
    def _is_bilibili_url(video_url: str) -> bool:
        try:
            netloc = (urlparse(video_url).netloc or "").lower()
        except Exception:
            return False
        return "bilibili.com" in netloc or "b23.tv" in netloc

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """将秒数格式化为 HHMMSS 字符串。"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        sec = int(seconds % 60)
        return f"{hours:02d}{minutes:02d}{sec:02d}"

    @staticmethod
    def _sanitize_cookie_value(value: str | None) -> str:
        return (value or "").strip().replace("\r", "").replace("\n", "")

    @staticmethod
    def _resolve_final_url(video_url: str) -> str:
        request = Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=15) as response:
            return response.geturl()

    @classmethod
    def _extract_bvid_from_url(cls, video_url: str) -> str:
        candidate = video_url
        if "b23.tv" in video_url:
            try:
                candidate = cls._resolve_final_url(video_url)
            except Exception:
                candidate = video_url

        match = re.search(r"/video/(BV[0-9A-Za-z]+)", candidate)
        if match:
            return match.group(1)

        fallback = re.search(r"(BV[0-9A-Za-z]+)", candidate)
        if fallback:
            return fallback.group(1)

        raise ValueError("无法从链接中提取 BV 号")

    @staticmethod
    def _normalize_subtitle_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            return f"https:{url}"
        return url

    @staticmethod
    def _score_bilibili_subtitle_item(item: Dict[str, Any]) -> int:
        lan = str(item.get("lan") or item.get("lang") or "").strip().lower().replace("_", "-")
        url = str(item.get("subtitle_url") or item.get("url") or "")
        ext = os.path.splitext(url.split("?", 1)[0])[1].lstrip(".").lower()

        score = 0
        if lan == "ai-zh":
            score += 1000
        elif "zh" in lan and "ai" in lan:
            score += 900
        elif lan.startswith("zh"):
            score += 700
        elif "zh" in lan:
            score += 600
        elif lan:
            score += 100

        if ext == "json":
            score += 30
        elif ext:
            score += 10

        return score

    @classmethod
    def _select_bilibili_subtitle_item(cls, subtitle_items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        best_item = None
        best_score = -1
        for item in subtitle_items:
            if not isinstance(item, dict):
                continue
            sub_url = cls._normalize_subtitle_url(str(item.get("subtitle_url") or item.get("url") or ""))
            if not sub_url:
                continue
            score = cls._score_bilibili_subtitle_item(item)
            if score > best_score:
                best_score = score
                best_item = item
        return best_item

    def _resolve_bilibili_sessdata(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        task_override = self._sanitize_cookie_value(str(payload.get("bilibili_sessdata") or ""))
        manager = self.transcription_settings_manager
        if manager is not None and hasattr(manager, "resolve_bilibili_sessdata"):
            try:
                value, source = manager.resolve_bilibili_sessdata(task_override)
                return self._sanitize_cookie_value(value), str(source or "none")
            except Exception as e:
                logger.warning(f"[{self.name}] 读取 B 站 Cookie 设置失败，将回退环境变量: {e}")

        if task_override:
            return task_override, "task"

        env_cookie = self._sanitize_cookie_value(os.getenv("BILIBILI_SESSDATA") or os.getenv("SESSDATA"))
        if env_cookie:
            return env_cookie, "env"
        return "", "none"

    @staticmethod
    def _download_text(url: str, extra_headers: Dict[str, Any] | None = None) -> str:
        target = url
        if target.startswith("//"):
            target = f"https:{target}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        if isinstance(extra_headers, dict):
            for k, v in extra_headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    headers[k] = v
        request = Request(target, headers=headers)
        with urlopen(request, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

    def _build_transcript_from_subtitle_json(self, subtitle_json: Dict[str, Any]) -> str:
        lines: List[str] = []

        body = subtitle_json.get("body")
        if isinstance(body, list):
            for segment in body:
                if not isinstance(segment, dict):
                    continue
                content = str(segment.get("content") or segment.get("text") or "").strip()
                if not content:
                    continue
                start_raw = segment.get("from", segment.get("start", 0.0))
                try:
                    start = float(start_raw)
                except (TypeError, ValueError):
                    start = 0.0
                content = content.replace("\n", " ").strip()
                if content:
                    lines.append(f"{self._format_duration(start)}{content}\n")

            return "".join(lines)

        # 兼容 json3 类格式
        events = subtitle_json.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                start_ms = event.get("tStartMs", event.get("t_start_ms", 0))
                try:
                    start = float(start_ms) / 1000.0
                except (TypeError, ValueError):
                    start = 0.0

                segments = event.get("segs")
                if isinstance(segments, list):
                    text_parts = []
                    for seg in segments:
                        if isinstance(seg, dict):
                            seg_text = str(seg.get("utf8") or "").strip()
                            if seg_text:
                                text_parts.append(seg_text)
                    content = "".join(text_parts).replace("\n", " ").strip()
                else:
                    content = str(event.get("content") or "").replace("\n", " ").strip()

                if content:
                    lines.append(f"{self._format_duration(start)}{content}\n")

            return "".join(lines)

        return ""

    async def _extract_bilibili_subtitle_via_api(
        self, video_url: str, sessdata: str, part_index: int = 0
    ) -> Dict[str, Any] | None:
        """
        提取 B 站视频字幕。

        Args:
            video_url: 视频链接
            sessdata: B 站 Cookie
            part_index: 分P索引（0-based），默认为0（第一个分P或单P视频）

        Returns:
            包含字幕信息的字典，或 None 如果获取失败
        """
        try:
            from bilibili_api import Credential, video
        except Exception as exc:
            raise RuntimeError("未安装 bilibili-api-python，无法进行 B 站字幕直取") from exc

        bvid = self._extract_bvid_from_url(video_url)
        credential = Credential(sessdata=sessdata or None) if sessdata else None

        video_obj = video.Video(bvid=bvid, credential=credential)
        info = await video_obj.get_info()
        cid = await video_obj.get_cid(part_index)
        player_info = await video_obj.get_player_info(cid=cid)

        subtitle_block = player_info.get("subtitle") if isinstance(player_info, dict) else None
        subtitle_items = subtitle_block.get("subtitles") if isinstance(subtitle_block, dict) else []
        if not isinstance(subtitle_items, list) or not subtitle_items:
            return None

        selected = self._select_bilibili_subtitle_item(subtitle_items)
        if not selected:
            return None

        subtitle_url = self._normalize_subtitle_url(str(selected.get("subtitle_url") or selected.get("url") or ""))
        if not subtitle_url:
            return None

        raw_subtitle = self._download_text(subtitle_url)
        subtitle_json = json.loads(raw_subtitle)
        transcript = self._build_transcript_from_subtitle_json(subtitle_json)
        if not transcript.strip():
            return None

        language = str(selected.get("lan") or selected.get("lang") or "")

        # 获取该分P的具体信息
        part_title = None
        part_duration = None
        pages = info.get("pages")
        if isinstance(pages, list) and len(pages) > part_index:
            page_info = pages[part_index]
            if isinstance(page_info, dict):
                part_title = page_info.get("part")
                part_duration = page_info.get("duration")

        return {
            "title": info.get("title"),
            "duration": part_duration or info.get("duration"),
            "transcript": transcript,
            "language": language,
            "is_auto": "ai" in language.lower(),
            "subtitle_url": subtitle_url,
            "part_index": part_index,
            "part_title": part_title,
        }

    async def _extract_bilibili_multi_part_subtitles(
        self, video_url: str, sessdata: str, part_indices: List[int]
    ) -> List[Dict[str, Any]]:
        """
        提取多个分P的字幕。

        Args:
            video_url: 视频链接
            sessdata: B 站 Cookie
            part_indices: 要提取的分P索引列表（0-based）

        Returns:
            成功提取的字幕信息列表
        """
        results = []
        for idx in part_indices:
            try:
                result = await self._extract_bilibili_subtitle_via_api(video_url, sessdata, idx)
                if result:
                    results.append(result)
                    logger.info(
                        f"[{self.name}] 成功提取分P {idx + 1} 字幕: "
                        f"{result.get('part_title') or f'第{idx + 1}P'}"
                    )
                else:
                    logger.warning(f"[{self.name}] 分P {idx + 1} 未找到字幕")
            except Exception as e:
                logger.warning(f"[{self.name}] 提取分P {idx + 1} 字幕失败: {e}")
        return results

    def _find_downloaded_media_file(self, base_path: str) -> str | None:
        for ext in (".m4a", ".m4s", ".mp3", ".mp4", ".webm", ".opus", ".ogg", ".wav", ".flac", ".aac"):
            candidate = base_path + ext
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                return candidate
        matches = [
            path for path in glob.glob(base_path + ".*")
            if os.path.isfile(path) and os.path.getsize(path) > 0
        ]
        if matches:
            matches.sort(key=lambda path: os.path.getmtime(path), reverse=True)
            return matches[0]
        return None

    def _download_bilibili_part_audio(
        self,
        video_url: str,
        part_index: int,
        task_id: str,
    ) -> str | None:
        if self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，跳过分P音频下载: {task_id}")

        base_path = os.path.join(self.output_dir, f"{task_id}_p{part_index + 1}")
        existing = self._find_downloaded_media_file(base_path)
        if existing:
            logger.info(f"[{self.name}] 复用已下载的分P音频: {existing}")
            return existing

        ffmpeg_location = FFmpegHelper.get_yt_dlp_ffmpeg_location()

        def progress_hook(d):
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止分P音频下载: {task_id}")
            if d.get("status") == "error":
                raise yt_dlp.utils.DownloadError(str(d.get("error") or "yt-dlp download error"))

        ydl_opts = {
            "outtmpl": base_path + ".%(ext)s",
            "format": "bestaudio/best",
            "playlist_items": str(part_index + 1),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
            "progress_hooks": [progress_hook],
            "writethumbnail": False,
            "writesubtitles": False,
        }
        if ffmpeg_location:
            ydl_opts["ffmpeg_location"] = ffmpeg_location

        logger.info(f"[{self.name}] 下载分P {part_index + 1} 音频用于 ASR: {video_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(video_url, download=True)

        downloaded = self._find_downloaded_media_file(base_path)
        if not downloaded:
            logger.warning(f"[{self.name}] 分P {part_index + 1} 音频下载完成但未找到输出文件")
            return None
        return downloaded

    def _transcription_result_to_transcript(self, result: Any) -> str:
        lines: List[str] = []
        for seg in getattr(result, "segments", []) or []:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").replace("\n", " ").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start") or 0.0)
            except (TypeError, ValueError):
                start = 0.0
            lines.append(f"{self._format_duration(start)}{text}\n")
        return "".join(lines)

    def _transcribe_bilibili_part_via_asr(
        self,
        video_url: str,
        part_index: int,
        task_id: str,
        video_info: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if self.next_worker is None or not hasattr(self.next_worker, "transcribe_media_file"):
            logger.warning(f"[{self.name}] 无可用转录 worker，无法对分P {part_index + 1} 执行 ASR")
            return None

        audio_path = self._download_bilibili_part_audio(video_url, part_index, task_id)
        if not audio_path:
            return None

        from ..db import TaskStatus
        from ..task_updater import update_and_notify
        self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING}))

        result = self.next_worker.transcribe_media_file(audio_path, task_id=task_id)
        transcript = self._transcription_result_to_transcript(result)
        if not transcript.strip():
            logger.warning(f"[{self.name}] 分P {part_index + 1} ASR 结果为空")
            return None

        pages = video_info.get("pages", [])
        part_title = None
        part_duration = getattr(result, "audio_duration", None)
        if isinstance(pages, list) and len(pages) > part_index:
            page_info = pages[part_index]
            if isinstance(page_info, dict):
                part_title = page_info.get("part")
                part_duration = page_info.get("duration") or part_duration

        return {
            "title": video_info.get("title"),
            "duration": part_duration,
            "transcript": transcript,
            "language": getattr(result, "language", None),
            "is_auto": True,
            "part_index": part_index,
            "part_title": part_title,
            "source": "asr",
            "audio_path": audio_path,
            "transcription_time": getattr(result, "transcription_time", None),
        }

    def _collect_bilibili_multi_part_transcripts(
        self,
        video_url: str,
        sessdata: str,
        task_id: str,
        part_indices: List[int],
        video_info: Dict[str, Any],
        subtitle_fetch_enabled: bool,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx in part_indices:
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止分P处理: {task_id}")

            result: Dict[str, Any] | None = None
            if subtitle_fetch_enabled:
                try:
                    result = asyncio.run(self._extract_bilibili_subtitle_via_api(video_url, sessdata, idx))
                    if result:
                        result["source"] = "subtitle"
                        logger.info(
                            f"[{self.name}] 分P {idx + 1} 使用字幕: "
                            f"{result.get('part_title') or f'P{idx + 1}'}"
                        )
                except Exception as e:
                    logger.warning(f"[{self.name}] 分P {idx + 1} 字幕提取失败，将尝试 ASR: {e}")

            if not result:
                try:
                    result = self._transcribe_bilibili_part_via_asr(video_url, idx, task_id, video_info)
                    if result:
                        logger.info(f"[{self.name}] 分P {idx + 1} 已使用 ASR 转录")
                except Exception as e:
                    logger.warning(f"[{self.name}] 分P {idx + 1} ASR 失败: {e}", exc_info=True)

            if result:
                results.append(result)

        return results

    def _merge_transcripts_with_offset(
        self, subtitle_results: List[Dict[str, Any]], video_info: Dict[str, Any]
    ) -> Tuple[str, float]:
        """
        合并多个分P的字幕，计算时间偏移。

        Args:
            subtitle_results: 多个分P的字幕结果列表
            video_info: 视频信息，包含分P时长

        Returns:
            (合并后的转录文本, 总时长)
        """
        if not subtitle_results:
            return "", 0

        # 按分P索引排序
        sorted_results = sorted(subtitle_results, key=lambda x: x.get("part_index", 0))

        # 获取每个分P的时长用于计算偏移
        pages = video_info.get("pages", [])
        duration_map = {}
        for page in pages:
            if isinstance(page, dict):
                page_idx = page.get("page", 1) - 1  # page 是 1-based
                duration_map[page_idx] = page.get("duration", 0)

        merged_lines = []
        total_duration = 0
        time_offset = 0.0

        for result in sorted_results:
            part_idx = result.get("part_index", 0)
            transcript = result.get("transcript", "")

            # 获取该分P时长
            part_duration = duration_map.get(part_idx, result.get("duration", 0))
            try:
                part_duration = float(part_duration or 0)
            except (TypeError, ValueError):
                part_duration = 0.0
            total_duration += part_duration

            if time_offset > 0 and transcript:
                # 需要调整时间戳
                for line in transcript.split("\n"):
                    if not line.strip():
                        continue
                    # 解析 HHMMSS 格式的时间戳
                    match = re.match(r"^(\d{2})(\d{2})(\d{2})(.+)$", line)
                    if match:
                        h, m, s, text = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)
                        original_seconds = h * 3600 + m * 60 + s
                        new_seconds = original_seconds + time_offset

                        new_h = int(new_seconds // 3600)
                        new_m = int((new_seconds % 3600) // 60)
                        new_s = int(new_seconds % 60)
                        merged_lines.append(f"{new_h:02d}{new_m:02d}{new_s:02d}{text}\n")
                    else:
                        merged_lines.append(line + "\n")
            else:
                merged_lines.append(transcript)
                if not transcript.endswith("\n"):
                    merged_lines.append("\n")

            # 更新时间偏移
            time_offset += part_duration

        return "".join(merged_lines), total_duration

    def _try_extract_bilibili_subtitle(self, video_url: str, sessdata: str) -> Dict[str, Any] | None:
        return asyncio.run(self._extract_bilibili_subtitle_via_api(video_url, sessdata, 0))

    def _try_process_with_bilibili_subtitle(self, payload: Dict[str, Any]) -> bool:
        video_url = str(payload.get("video_url") or "")
        task_id = payload.get("task_id")
        summary_mode = str(payload.get("summary_mode") or "standard").strip().lower()
        if summary_mode not in {"auto", "standard", "agent"}:
            summary_mode = "standard"
        summary_style = str(payload.get("summary_style") or "classic").strip().lower()
        if summary_style not in {"classic", "report"}:
            summary_style = "classic"

        if not video_url or not task_id:
            return False
        if self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，跳过字幕直取: {task_id}")
        if not self._is_bilibili_url(video_url):
            return False
        if self.summary_worker is None and self.summary_worker_factory is None:
            return False

        subtitle_fetch_enabled = True
        if self.transcription_settings_manager is not None:
            try:
                settings = self.transcription_settings_manager.get_settings()
                subtitle_fetch_enabled = bool(settings.get("enable_bilibili_subtitle_fetch", True))
            except Exception as e:
                logger.warning(f"[{self.name}] 读取转录设置失败，继续回退 ASR: {e}")
                subtitle_fetch_enabled = False

        sessdata, cookie_source = self._resolve_bilibili_sessdata(payload)

        # 检查是否有分P配置
        bilibili_parts = payload.get("bilibili_parts")
        if bilibili_parts and isinstance(bilibili_parts, dict):
            mode = bilibili_parts.get("mode")
            indices = bilibili_parts.get("indices")

            if mode == "merge" and isinstance(indices, list) and len(indices) > 0:
                # 合并模式：提取多个分P的字幕并合并
                return self._try_process_bilibili_multi_part_merge(
                    video_url, sessdata, task_id, indices, payload, subtitle_fetch_enabled
                )
            # separate 模式由 API 层处理，这里不应该到达
            # 如果到达这里，说明配置有问题，回退到普通处理
            logger.warning(f"[{self.name}] 未知的分P处理模式或无效配置: {bilibili_parts}")

        if not subtitle_fetch_enabled:
            return False

        logger.info(
            f"[{self.name}] 检测到 B 站 URL，尝试使用 bilibili-api 直取字幕: {video_url}"
            f" (cookie_source={cookie_source}, has_cookie={bool(sessdata)})"
        )

        try:
            subtitle_result = self._try_extract_bilibili_subtitle(video_url, sessdata)
            if not subtitle_result:
                logger.info(f"[{self.name}] 未获取到可用字幕，回退到下载+ASR流程。")
                return False

            transcript = subtitle_result["transcript"]
            intermediate_file_path = os.path.join(self.output_dir, f"{task_id}_subtitle.txt")
            output_file = os.path.join(self.output_dir, f"{task_id}_summary.md")

            with open(intermediate_file_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(transcript)

            from ..db import TaskStatus
            from ..task_updater import update_and_notify
            self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING}))

            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止字幕分支: {task_id}")

            update_data = {
                "title": subtitle_result.get("title"),
                "status": TaskStatus.SUMMARIZING,
                "progress": 0.0,
                "transcript": transcript,
                "transcription_time": 0.0,
                "audio_duration": subtitle_result.get("duration"),
                "summary_mode": summary_mode,
                "summary_style": summary_style,
                "summary_chunk_total": None,
                "summary_chunk_done": None,
                "summary_meta": None,
                "transcription_meta": json.dumps(
                    {
                        "provider": "bilibili_subtitle",
                        "stage": "completed",
                        "message": "已直接获取 B 站字幕",
                        "language": subtitle_result.get("language"),
                    },
                    ensure_ascii=False,
                ),
            }
            self._submit_coro(update_and_notify(task_id, update_data))

            next_payload = payload.copy()
            next_payload.update({
                "intermediate_file_path": intermediate_file_path,
                "output_file": output_file,
            })
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止派发总结: {task_id}")
            self._submit_coro(self._enqueue_summary(next_payload))

            logger.info(
                f"[{self.name}] 已使用B站字幕（{subtitle_result.get('language')}），跳过音频转录。"
            )
            return True
        except Exception as e:
            logger.warning(f"[{self.name}] B 站字幕直取失败，将回退 ASR: {e}")
            return False

    def _try_process_bilibili_multi_part_merge(
        self,
        video_url: str,
        sessdata: str,
        task_id: str,
        part_indices: List[int],
        payload: Dict[str, Any],
        subtitle_fetch_enabled: bool = True,
    ) -> bool:
        """
        处理多P视频合并模式：逐个分P优先取字幕，缺字幕时下载该分P音频并 ASR。
        """
        logger.info(
            f"[{self.name}] 处理多P视频合并模式: {video_url}, 分P: {[i + 1 for i in part_indices]}"
        )

        try:
            # 获取视频信息用于时长计算
            from bilibili_api import Credential, video
            bvid = self._extract_bvid_from_url(video_url)
            credential = Credential(sessdata=sessdata or None) if sessdata else None
            video_obj = video.Video(bvid=bvid, credential=credential)
            video_info = asyncio.run(video_obj.get_info())

            subtitle_results = self._collect_bilibili_multi_part_transcripts(
                video_url=video_url,
                sessdata=sessdata,
                task_id=task_id,
                part_indices=part_indices,
                video_info=video_info,
                subtitle_fetch_enabled=subtitle_fetch_enabled,
            )

            if not subtitle_results:
                logger.warning(f"[{self.name}] 未能获取任何分P转录，回退到下载+ASR流程")
                return False

            # 合并逐P转录
            merged_transcript, total_duration = self._merge_transcripts_with_offset(
                subtitle_results, video_info
            )

            if not merged_transcript.strip():
                logger.warning(f"[{self.name}] 合并后的分P转录为空，回退到下载+ASR流程")
                return False

            # 构建标题（包含分P信息）
            title = video_info.get("title", "")
            subtitle_count = sum(1 for item in subtitle_results if item.get("source") == "subtitle")
            asr_count = sum(1 for item in subtitle_results if item.get("source") == "asr")
            if len(part_indices) == 1:
                # 单分P（拆分模式）：使用分P标题，不走合并标题逻辑
                part_idx = part_indices[0]
                part_title = subtitle_results[0].get("part_title") if subtitle_results else None
                if part_title:
                    title = f"{title} - P{part_idx + 1}: {part_title}"
                else:
                    title = f"{title} - P{part_idx + 1}"
            elif len(subtitle_results) < len(part_indices):
                title = f"{title} (已合并 {len(subtitle_results)}/{len(part_indices)} 个分P，字幕 {subtitle_count} / ASR {asr_count})"
            else:
                title = f"{title} (已合并 {len(part_indices)} 个分P，字幕 {subtitle_count} / ASR {asr_count})"

            intermediate_file_path = os.path.join(self.output_dir, f"{task_id}_subtitle.txt")
            output_file = os.path.join(self.output_dir, f"{task_id}_summary.md")

            with open(intermediate_file_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(merged_transcript)

            from ..db import TaskStatus
            from ..task_updater import update_and_notify
            self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING}))

            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止字幕分支: {task_id}")

            summary_mode = str(payload.get("summary_mode") or "standard").strip().lower()
            if summary_mode not in {"auto", "standard", "agent"}:
                summary_mode = "standard"
            summary_style = str(payload.get("summary_style") or "classic").strip().lower()
            if summary_style not in {"classic", "report"}:
                summary_style = "classic"

            update_data = {
                "title": title,
                "status": TaskStatus.SUMMARIZING,
                "progress": 0.0,
                "transcript": merged_transcript,
                "transcription_time": 0.0,
                "audio_duration": total_duration,
                "summary_mode": summary_mode,
                "summary_style": summary_style,
                "summary_chunk_total": None,
                "summary_chunk_done": None,
                "summary_meta": json.dumps({
                    "input_type": "bilibili_multi_part",
                    "part_total_requested": len(part_indices),
                    "part_total_processed": len(subtitle_results),
                    "subtitle_part_count": subtitle_count,
                    "asr_part_count": asr_count,
                    "part_sources": [
                        {
                            "part_index": item.get("part_index"),
                            "part_title": item.get("part_title"),
                            "source": item.get("source"),
                        }
                        for item in subtitle_results
                    ],
                }, ensure_ascii=False),
                "transcription_meta": json.dumps(
                    {
                        "provider": "bilibili_multipart",
                        "stage": "completed",
                        "message": f"已合并 {len(subtitle_results)} 个分P转录",
                        "subtitle_part_count": subtitle_count,
                        "asr_part_count": asr_count,
                    },
                    ensure_ascii=False,
                ),
            }
            self._submit_coro(update_and_notify(task_id, update_data))

            next_payload = payload.copy()
            next_payload.update({
                "intermediate_file_path": intermediate_file_path,
                "output_file": output_file,
            })
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止派发总结: {task_id}")
            self._submit_coro(self._enqueue_summary(next_payload))

            logger.info(
                f"[{self.name}] 已合并 {len(subtitle_results)} 个分P转录，"
                f"字幕 {subtitle_count} 个，ASR {asr_count} 个，总时长 {total_duration} 秒。"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.name}] 处理多P视频合并失败: {e}", exc_info=True)
            return False

    async def _resolve_and_save_bilibili_author(self, task_id: str, video_url: str):
        if not task_id or not self._is_bilibili_url(video_url):
            return

        try:
            author_info = await resolve_bilibili_author(video_url)
            from ..task_updater import update_and_notify

            await update_and_notify(
                task_id,
                {
                    "author_name": author_info.get("author_name"),
                    "author_url": author_info.get("author_url"),
                },
            )
            logger.info(
                f"[{self.name}] 已解析 B 站作者信息: "
                f"{author_info.get('author_name')} ({author_info.get('author_url')})"
            )
        except BilibiliAuthorResolveError as e:
            logger.info(f"[{self.name}] B 站作者信息解析失败（仅提示，不影响流程）: {e}")
        except Exception as e:
            logger.info(f"[{self.name}] B 站作者信息写入失败（仅提示，不影响流程）: {e}")

    def process_task(self, payload: Any):
        """
        下载视频并将其传递给下一个工作单元。
        
        :param payload: 包含 'video_url' 的字典。
        """
        video_url = payload.get("video_url")
        quality = payload.get("quality", "best")
        task_id = payload.get("task_id") # 用于更新进度

        if not video_url:
            error_msg = "任务负载中缺少 'video_url'"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_msg}))
            return

        try:
            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，跳过下载: {task_id}")

            if self._try_process_with_bilibili_subtitle(payload):
                return
        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
            return

        logger.info(f"[{self.name}] 开始下载视频: {video_url} (质量: {quality})")

        if task_id:
            from ..db import TaskStatus
            from ..task_updater import update_and_notify
            self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.DOWNLOADING}))

        def progress_hook(d):
            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止下载: {task_id}")

            if d['status'] == 'error':
                error_msg = f"yt-dlp 下载时报告错误: {d.get('error', '未知错误')}"
                logger.error(f"[{self.name}] {error_msg}")
                raise yt_dlp.utils.DownloadError(error_msg)

            if d['status'] == 'downloading':
                raw_p = d.get('_percent_str', '0%')
                # 首先清理 ANSI 颜色代码，然后移除百分号和空格
                clean_p = re.sub(r'\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])', '', raw_p)
                p = clean_p.replace('%','').strip()
                try:
                    progress = float(p)
                    if task_id:
                        from ..api import notify_progress_update
                        # 直接广播进度，不再写入数据库
                        self._submit_coro(notify_progress_update(task_id, progress))
                except ValueError:
                    logger.warning(f"[{self.name}] 无法从 yt-dlp 解析进度: '{p}' (原始值: '{raw_p}')")

        try:
            # 配置 ffmpeg 路径（使用 FFmpegHelper）
            ffmpeg_location = FFmpegHelper.get_yt_dlp_ffmpeg_location()
            
            if quality == "audio_only":
                ydl_opts = {
                    'outtmpl': os.path.join(self.output_dir, '%(id)s.%(ext)s'),
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'm4a',
                    }],
                    'progress_hooks': [progress_hook],
                    'writethumbnail': False,
                    'writesubtitles': False,
                }
            else:
                ydl_opts = {
                    'outtmpl': os.path.join(self.output_dir, '%(id)s.%(ext)s'),
                    'format': 'worstvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/worst[ext=mp4]/best',
                    'merge_output_format': 'mp4',
                    'progress_hooks': [progress_hook],
                }
            
            # 如果有 ffmpeg 路径，添加到配置中
            if ffmpeg_location:
                ydl_opts['ffmpeg_location'] = ffmpeg_location

            bilibili_parts = payload.get("bilibili_parts")
            if self._is_bilibili_url(str(video_url)) and isinstance(bilibili_parts, dict):
                indices = bilibili_parts.get("indices")
                if isinstance(indices, list) and len(indices) == 1:
                    try:
                        selected_part = int(indices[0]) + 1
                        ydl_opts["playlist_items"] = str(selected_part)
                        logger.info(f"[{self.name}] 普通下载回退限定到 B 站分P: P{selected_part}")
                    except (TypeError, ValueError):
                        pass

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                video_path = ydl.prepare_filename(info_dict)

            # 第一步：检查原始视频路径是否存在
            if not os.path.exists(video_path):
                logger.info(f"[{self.name}] 原始视频路径不存在，开始尝试替换后缀查找: {video_path}")
                base = os.path.splitext(video_path)[0]
                logger.info(f"[{self.name}] 提取文件基础路径（无后缀）: {base}")
                
                # 遍历支持的后缀逐一尝试
                for ext in ('.m4a', '.m4s', '.mp3', '.mp4', '.webm', '.opus', '.ogg', '.wav', '.flac'):
                    candidate = base + ext
                    logger.info(f"[{self.name}] 尝试查找候选文件: {candidate}")
                    
                    if os.path.exists(candidate):
                        video_path = candidate
                        logger.info(f"[{self.name}] 成功找到匹配后缀的文件: {video_path}")
                        break
                else:
                    # 循环正常结束（未执行break），说明所有后缀都没找到
                    logger.info(f"[{self.name}] 遍历所有支持后缀后，未找到匹配文件")

            # 第二步：如果还是不存在，尝试通过ID匹配输出目录文件
            # 分P视频文件名格式为 BV1XtRxBhEc7_p1、BV1XtRxBhEc7_p2 等
            if not os.path.exists(video_path):
                logger.info(f"[{self.name}] 后缀匹配失败，开始通过文件名ID匹配输出目录文件")
                
                filename_no_ext = os.path.splitext(os.path.basename(video_path))[0]
                logger.info(f"[{self.name}] 提取无后缀文件名: {filename_no_ext}")
                
                id_match = re.search(r'(BV[A-Za-z0-9]+)', filename_no_ext)
                if not id_match:
                    id_match = re.search(r'([A-Za-z0-9_-]+)$', filename_no_ext)
                
                if id_match:
                    video_id = id_match.group(1)
                    logger.info(f"[{self.name}] 成功提取文件ID: {video_id}")
                    logger.info(f"[{self.name}] 开始遍历输出目录查找匹配文件: {self.output_dir}")
                    
                    matched_files = []
                    for f in os.listdir(self.output_dir):
                        file_path = os.path.join(self.output_dir, f)
                        if not os.path.isfile(file_path):
                            continue
                        name_part = os.path.splitext(f)[0]
                        
                        if name_part == video_id or name_part.startswith(video_id + '_p') or name_part.endswith('_' + video_id):
                            matched_files.append(file_path)
                            logger.info(f"[{self.name}] 匹配到文件: {file_path}")
                    
                    if matched_files:
                        matched_files.sort()
                        video_path = matched_files[0]
                        logger.info(f"[{self.name}] 通过ID匹配成功找到文件: {video_path} (共匹配 {len(matched_files)} 个)")
                    else:
                        logger.info(f"[{self.name}] 遍历输出目录完成，未找到ID匹配的文件")
                else:
                    logger.info(f"[{self.name}] 未从文件名中匹配到有效ID，终止查找")


            logger.info(f"[{self.name}] 视频下载成功: {video_path}")

            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止后续处理: {task_id}")

            if task_id and self._is_bilibili_url(str(video_url)):
                self._submit_coro(self._resolve_and_save_bilibili_author(task_id, str(video_url)))

            if task_id:
                logger.info(
                    f"[VideoDownloader] Download completed: task_id={task_id}, "
                    f"status: DOWNLOADING→TRANSCRIBING, video={video_path}"
                )

                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                updates = {"status": TaskStatus.TRANSCRIBING}
                video_title = info_dict.get("title")
                if video_title:
                    updates["title"] = str(video_title)
                self._submit_coro(update_and_notify(task_id, updates))

            if self.next_worker:
                next_payload = payload.copy()
                base_name = os.path.splitext(os.path.basename(video_path))[0]
                file_ext = os.path.splitext(video_path)[1].lower()
                audio_exts = {'.m4a', '.m4s', '.mp3', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma'}
                #打印参数
                logger.info(f"当前选择质量：[{quality}]，当前文件扩展名: {file_ext}")
                if quality == "audio_only" and file_ext in audio_exts:
                    next_payload['video_file'] = None
                    next_payload['audio_file'] = video_path
                else:
                    next_payload['video_file'] = video_path
                    next_payload['audio_file'] = os.path.join(self.output_dir, f"{base_name}.mp3")
                next_payload['output_file'] = os.path.join(self.output_dir, f"{base_name}_summary.md")
                
                self._submit_coro(self.next_worker.add_task(next_payload))

        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
        except Exception as e:
            logger.error(f"[{self.name}] 下载视频时出错: {e}", exc_info=True)
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                # 清理错误信息中的 ANSI 转义序列
                clean_error = re.sub(r'\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])', '', str(e))
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": clean_error}))

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..api import notify_task_update
from ..config.settings import config
from ..summarization.chunked_summarizer import ChunkedSummarizer
from ..summarization.chunker import (
    count_transcript_content_chars,
    measure_transcript_complexity,
    split_transcript_into_chunks,
)
from ..summarization.output_cleaner import clean_summary_output
from ..summarization.fidelity import (
    assess_fidelity,
    candidate_preserves_fidelity,
    format_missing_anchors,
)
from ..utils.logger import logger
from ..worker import TaskCancelledError, Worker
from .llm import LLM, LLMError, LLMMessage


VALID_SUMMARY_MODES = {"auto", "standard", "agent"}
VALID_SUMMARY_STYLES = {"classic", "report"}
REPORT_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompt_report.md"
REPORT_CHUNK_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompt_for_chunk_report.md"
_TIMESTAMP_ANNOTATION_RE = re.compile(
    r"[（(]\s*(?:见\s*)?\d{2}:\d{2}:\d{2}"
    r"(?:\s*[-–—~至]\s*\d{2}:\d{2}:\d{2})?\s*[)）]"
)


class LLMWorker(Worker):
    """
    使用 LLM 将转录文本转为总结文本。
    支持三种策略：
    - standard: 单次总结
    - agent: 分块总结
    - auto: 自动判定（长文本走 agent）
    """

    def __init__(self, name: str, llm_client: LLM):
        super().__init__(name)
        self._llm_client = llm_client
        self.system_prompt: str | None = None
        self._chunk_prompt_cache_path: str | None = None
        self._chunk_prompt_cache_text: str | None = None
        self._prompt_addon_cache: dict[str, str] = {}

    def load_system_prompt(self, prompt_file: str):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
            logger.info(f"[{self.name}] 系统提示已从 {prompt_file} 成功加载。")
        except FileNotFoundError:
            logger.error(f"[{self.name}] 错误: 在 {prompt_file} 未找到提示文件。")
            self.system_prompt = None
        except Exception as e:
            logger.error(f"[{self.name}] 加载提示文件时发生错误: {e}", exc_info=True)
            self.system_prompt = None

    async def process_task(self, payload: dict[str, Any]):
        intermediate_file_path = payload.get("intermediate_file_path")
        output_file = payload.get("output_file")
        task_id = str(payload.get("task_id") or "").strip() or None

        if not intermediate_file_path or not output_file:
            await self._mark_failed(task_id, "payload 中缺少 'intermediate_file_path' 或 'output_file'")
            return

        if task_id and self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，跳过总结: {task_id}")

        try:
            with open(intermediate_file_path, "r", encoding="utf-8") as f:
                transcript_text = f.read()
        except FileNotFoundError:
            await self._mark_failed(task_id, f"找不到中间转录文件 {intermediate_file_path}")
            return
        except Exception as e:
            await self._mark_failed(task_id, f"读取中间文件时出错: {e}")
            return

        task_data = None
        if task_id:
            from ..db import db

            task_data = db.get_task(task_id)
            if task_data is None:
                raise TaskCancelledError(f"任务已被删除，停止总结: {task_id}")

        requested_mode = self._resolve_requested_mode(payload, task_data)
        summary_style = self._resolve_summary_style(payload, task_data)
        effective_mode = self._resolve_effective_mode(
            requested_mode=requested_mode,
            task_data=task_data,
            transcript_text=transcript_text,
        )

        auto_metrics = self._collect_auto_mode_metrics(task_data=task_data, transcript_text=transcript_text)
        logger.info(
            f"[{self.name}] 任务 {task_id or '<unknown>'} 总结模式: "
            f"requested={requested_mode}, effective={effective_mode}, style={summary_style}, "
            f"audio_duration_sec={auto_metrics['audio_duration_sec']:.2f}, "
            f"transcript_timestamp_lines={auto_metrics['timestamp_line_count']}, "
            f"effective_lines={auto_metrics['line_count']}, "
            f"plain_chars={auto_metrics['plain_char_count']}, "
            f"content_chars={auto_metrics['content_char_count']}, "
            f"has_timestamps={auto_metrics['has_timestamps']}"
        )
        if requested_mode == "auto":
            logger.info(
                f"[{self.name}] 任务 {task_id or '<unknown>'} Auto 判定依据: "
                f"audio_duration {auto_metrics['audio_duration_sec']:.2f}s"
                f"{'>=' if auto_metrics['audio_triggered'] else '<'}"
                f"{config.summarization.auto_chunk_min_audio_duration_sec}s, "
                f"effective_lines {auto_metrics['line_count']}"
                f"{'>=' if auto_metrics['line_triggered'] else '<'}"
                f"{config.summarization.auto_chunk_min_transcript_lines}, "
                f"plain_chars {auto_metrics['plain_char_count']}"
                f"{'>=' if auto_metrics['plain_text_triggered'] else '<'}"
                f"{config.summarization.auto_chunk_min_plain_text_chars}, "
                f"result={effective_mode}"
            )

        try:
            if effective_mode == "agent":
                final_summary, topic, summary_meta = await self._run_chunked_summary(
                    transcript_text=transcript_text,
                    task_id=task_id,
                    mode_value=requested_mode,
                    summary_style=summary_style,
                )
                mode_used = "agent"
            else:
                final_summary, topic = await self._run_standard_summary(
                    transcript_text=transcript_text,
                    task_id=task_id,
                    summary_style=summary_style,
                )
                fidelity = assess_fidelity(transcript_text, final_summary)
                summary_meta = {
                    "mode": "standard",
                    "summary_style": summary_style,
                    "summary_detail_level": config.summarization.summary_detail_level,
                    "fidelity": fidelity.as_dict(),
                }
                mode_used = "standard"
        except asyncio.CancelledError:
            logger.info(f"[{self.name}] 任务被取消: {task_id or '<unknown>'}")
            raise
        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
            return
        except Exception as e:
            if effective_mode == "agent" and config.summarization.fallback_to_standard_on_agent_error:
                logger.warning(f"[{self.name}] 分块总结失败，回退标准模式: {e}")
                try:
                    final_summary, topic = await self._run_standard_summary(
                        transcript_text=transcript_text,
                        task_id=task_id,
                        summary_style=summary_style,
                    )
                    summary_meta = {
                        "fallback_triggered": True,
                        "fallback_reason": str(e),
                        "summary_style": summary_style,
                    }
                    mode_used = "standard"
                except Exception as fallback_err:
                    await self._mark_failed(task_id, f"分块总结失败且回退标准模式失败: {fallback_err}")
                    return
            else:
                await self._mark_failed(task_id, f"LLM 处理过程中发生错误: {e}")
                return

        if task_id and self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，停止写入总结结果: {task_id}")

        if not str(final_summary or "").strip():
            await self._mark_failed(task_id, "LLM 返回空摘要，请检查当前模型配置、余额或响应格式。")
            return

        try:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_summary)
        except Exception as e:
            await self._mark_failed(task_id, f"写入总结结果失败: {e}")
            return

        if task_id:
            logger.info(
                f"[LLMWorker] Finalizing task: task_id={task_id}, "
                f"status: SUMMARIZING→COMPLETED"
            )

            from ..db import db, TaskStatus

            update_data: dict[str, Any] = {
                "summary": final_summary,
                "status": TaskStatus.COMPLETED,
                "progress": 100,
                "summary_mode": mode_used,
                "summary_style": summary_style,
                "summary_meta": json.dumps(summary_meta, ensure_ascii=False) if summary_meta else None,
            }
            if topic:
                update_data["topic"] = topic

            if mode_used != "agent":
                update_data["summary_chunk_total"] = None
                update_data["summary_chunk_done"] = None

            existing_summary_meta = _parse_summary_meta((task_data or {}).get("summary_meta"))
            if existing_summary_meta:
                merged_summary_meta = {**existing_summary_meta, **summary_meta}
                update_data["summary_meta"] = json.dumps(merged_summary_meta, ensure_ascii=False)

            # Agent 模式下，等待所有待处理的分块更新完成
            if mode_used == "agent":
                await self._await_pending_updates(timeout=5.0)

            from ..task_updater import update_and_notify
            await update_and_notify(task_id, update_data)

    def _resolve_requested_mode(self, payload: dict[str, Any], task_data: dict[str, Any] | None) -> str:
        payload_mode = str(payload.get("summary_mode") or "").strip().lower()
        if payload_mode in VALID_SUMMARY_MODES:
            return payload_mode

        task_mode = str((task_data or {}).get("summary_mode") or "").strip().lower()
        if task_mode in VALID_SUMMARY_MODES:
            return task_mode

        cfg_mode = str(config.summarization.mode or "auto").strip().lower()
        if cfg_mode in VALID_SUMMARY_MODES:
            return cfg_mode
        return "auto"

    def _resolve_summary_style(self, payload: dict[str, Any], task_data: dict[str, Any] | None) -> str:
        payload_style = str(payload.get("summary_style") or "").strip().lower()
        if payload_style in VALID_SUMMARY_STYLES:
            return payload_style

        task_style = str((task_data or {}).get("summary_style") or "").strip().lower()
        if task_style in VALID_SUMMARY_STYLES:
            return task_style
        return "classic"

    def _resolve_effective_mode(
        self,
        requested_mode: str,
        task_data: dict[str, Any] | None,
        transcript_text: str,
    ) -> str:
        metrics = self._collect_auto_mode_metrics(task_data=task_data, transcript_text=transcript_text)
        if requested_mode == "agent":
            return "agent"
        if requested_mode == "standard":
            if metrics["plain_text_triggered"] and not metrics["has_timestamps"]:
                return "agent"
            return "standard"

        if metrics["audio_triggered"] or metrics["line_triggered"] or metrics["plain_text_triggered"]:
            return "agent"
        return "standard"

    def _collect_auto_mode_metrics(
        self,
        task_data: dict[str, Any] | None,
        transcript_text: str,
    ) -> dict[str, float | int | bool]:
        audio_duration = 0.0
        if task_data:
            try:
                audio_duration = float(task_data.get("audio_duration") or 0.0)
            except (TypeError, ValueError):
                audio_duration = 0.0
        text_metrics = measure_transcript_complexity(transcript_text)
        line_count = int(text_metrics["line_count"])
        plain_char_count = int(text_metrics["plain_char_count"])
        audio_triggered = audio_duration >= config.summarization.auto_chunk_min_audio_duration_sec
        line_triggered = line_count >= config.summarization.auto_chunk_min_transcript_lines
        plain_text_triggered = (
            not bool(text_metrics["has_timestamps"])
            and plain_char_count >= config.summarization.auto_chunk_min_plain_text_chars
        )
        return {
            "audio_duration_sec": audio_duration,
            "line_count": line_count,
            "timestamp_line_count": int(text_metrics["timestamp_line_count"]),
            "plain_char_count": plain_char_count,
            "content_char_count": int(text_metrics["content_char_count"]),
            "has_timestamps": bool(text_metrics["has_timestamps"]),
            "audio_triggered": audio_triggered,
            "line_triggered": line_triggered,
            "plain_text_triggered": plain_text_triggered,
        }

    async def _run_standard_summary(
        self,
        transcript_text: str,
        task_id: str | None,
        summary_style: str,
    ) -> tuple[str, str | None]:
        if not self.system_prompt:
            raise RuntimeError("未加载系统提示词，无法执行标准总结。")

        system_prompt = self._build_system_prompt(
            self.system_prompt,
            summary_style=summary_style,
            report_prompt_file=REPORT_PROMPT_FILE,
        )

        metrics = self._collect_auto_mode_metrics(task_data=None, transcript_text=transcript_text)
        min_output_chars = _estimate_standard_min_output_chars(
            transcript_text=transcript_text,
            detail_level=config.summarization.summary_detail_level,
        )
        user_prompt = _build_standard_user_prompt(
            transcript_text=transcript_text,
            metrics=metrics,
            detail_level=config.summarization.summary_detail_level,
            min_output_chars=min_output_chars,
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        response_chunks: list[str] = []
        llm_error: LLMError | None = None

        # 防抖控制
        last_update_time = 0.0
        update_in_progress = False

        async def flush_partial_summary():
            nonlocal last_update_time, update_in_progress

            if not task_id or update_in_progress:
                return

            # 防抖：距离上次更新不足0.5秒则跳过
            now = asyncio.get_event_loop().time()
            if now - last_update_time < 0.5:
                return

            update_in_progress = True
            try:
                from ..db import db, TaskStatus
                from ..task_updater import update_and_notify

                # 只有当任务仍在 SUMMARIZING 状态时才更新，避免覆盖 COMPLETED 状态
                current_task = db.get_task(task_id)
                if not current_task or current_task.get("status") != TaskStatus.SUMMARIZING:
                    return

                partial_summary = "".join(response_chunks)
                await update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.SUMMARIZING,
                        "summary": partial_summary,
                        "progress": _estimate_standard_stream_progress(
                            partial_summary,
                            min_output_chars,
                        ),
                        "summary_mode": "standard",
                        "summary_style": summary_style,
                        "summary_chunk_total": None,
                        "summary_chunk_done": None,
                    },
                )
                last_update_time = now
            finally:
                update_in_progress = False

        def callback(chunk: str | LLMError):
            nonlocal llm_error
            if task_id and self.is_task_cancelled(task_id):
                raise asyncio.CancelledError()

            if isinstance(chunk, LLMError):
                llm_error = chunk
                return

            response_chunks.append(chunk)
            # 每次收到新内容就尝试更新（防抖在flush内部处理）
            if task_id:
                self._submit_coro(flush_partial_summary())

        await self._llm_client.response(messages=messages, resp_callback=callback)
        if llm_error:
            raise llm_error

        final_summary = clean_summary_output("".join(response_chunks))
        final_summary = await self._expand_standard_summary_if_needed(
            transcript_text=transcript_text,
            draft_summary=final_summary,
            metrics=metrics,
            min_output_chars=min_output_chars,
            system_prompt=system_prompt,
            summary_style=summary_style,
        )
        final_summary = await self._ensure_standard_timestamp_coverage(
            transcript_text=transcript_text,
            draft_summary=final_summary,
            metrics=metrics,
            system_prompt=system_prompt,
        )
        topic = _extract_topic(final_summary)
        return final_summary, topic

    async def _expand_standard_summary_if_needed(
        self,
        transcript_text: str,
        draft_summary: str,
        metrics: dict[str, float | int | bool],
        min_output_chars: int,
        system_prompt: str,
        summary_style: str,
    ) -> str:
        if min_output_chars <= 0:
            return draft_summary
        current_chars = _visible_summary_char_count(draft_summary)
        fidelity = assess_fidelity(transcript_text, draft_summary)
        fidelity_needs_repair = (
            fidelity.needs_repair
            and (
                config.summarization.summary_detail_level >= 5
                or summary_style == "report"
            )
        )
        presentation_needs_repair = (
            summary_style == "report"
            and _report_presentation_needs_repair(draft_summary)
        )
        length_needs_repair = current_chars < int(min_output_chars * 0.9)
        if not length_needs_repair and not fidelity_needs_repair and not presentation_needs_repair:
            return draft_summary

        no_timestamp_rule = ""
        if not bool(metrics.get("has_timestamps")):
            no_timestamp_rule = "\n- 原文没有时间戳，修订稿中不要添加、猜测或伪造任何时间戳。"

        fidelity_rule = ""
        if fidelity_needs_repair:
            fidelity_rule = f"""
- 这是一次保真修订：逐项核对原文中的事实锚点，优先补回数字、范围、单位、引号短语和技术标识。当前检测到尚未明确保留的锚点包括：{format_missing_anchors(fidelity)}。
- 补回事实时必须把它放回对应的问答、案例、步骤或论证上下文，不要把锚点孤零零堆成清单，也不要用重复解释凑长度。
"""

        presentation_rule = ""
        if presentation_needs_repair:
            presentation_rule = """
- 本次开启了结果美化，但当前稿件的报告结构不足。保留全部正文信息，并补齐连续编号的 `##`/`###` 标题层级。
- 在全文分散使用 4-8 个与内容匹配的语义提示块；定义用 `[!DEFINITION]`、推导或机制用 `[!PROCESS]`、案例用 `[!EXAMPLE]`、比较用 `[!COMPARISON]`，不要只使用 `[!KEY]`。
- 原文存在明确步骤、比较、时间线、关系或因果链时，至少使用一处列表、表格或 Mermaid。存在 3 个以上相连节点且图形更直观时，加入一个简洁 Mermaid，同时保留正文解释。
- 视觉组件只是重新组织现有信息，不得把正文压成卡片提纲，也不得为了组件删除数字、例子、论证、限定条件或观点归属。
"""

        repair_prompt = f"""请把下面的标准模式总结扩写成更完整的结构化总结。

要求：
- 输出完整修订版，不要输出修改说明或“补充如下”。
- 保留 `{{...}}` 主题标签和 Markdown 标题层级。
- 当前可读正文约 {current_chars} 字符，目标至少 {min_output_chars} 字符；不要过度压缩。
- 按原文顺序补足遗漏的有效信息，尤其是提问、回答、追问、分歧、观点归属、数字、条件、类型清单、案例与论证过程。
- 不要用空泛概括替代具体内容；只有口头禅、寒暄和语义重复可以删除，不得为了凑长度虚构信息。
- 如果某个小节还没自然结束，先补完该小节，再进入新的一级章节；不要出现小节没写完就跳到下一章的情况。{no_timestamp_rule}
{fidelity_rule}
{presentation_rule}

原始转录文本：
```plaintext
{transcript_text}
```

当前草稿：
```markdown
{draft_summary}
```
"""
        response_chunks: list[str] = []
        llm_error: LLMError | None = None

        def callback(chunk: str | LLMError):
            nonlocal llm_error
            if isinstance(chunk, LLMError):
                llm_error = chunk
                return
            response_chunks.append(chunk)

        try:
            await self._llm_client.response(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=repair_prompt),
                ],
                resp_callback=callback,
            )
        except Exception as e:
            logger.warning(f"[{self.name}] 标准总结扩写失败，保留原结果: {e}")
            return draft_summary
        if llm_error:
            logger.warning(f"[{self.name}] 标准总结扩写失败，保留原结果: {llm_error}")
            return draft_summary

        expanded = clean_summary_output("".join(response_chunks))
        expanded_chars = _visible_summary_char_count(expanded)
        expanded_fidelity = assess_fidelity(transcript_text, expanded)
        if not candidate_preserves_fidelity(transcript_text, draft_summary, expanded):
            logger.info(
                f"[{self.name}] 标准总结扩写未通过保真校验，保留原结果: "
                f"chars={current_chars}->{expanded_chars}, "
                f"anchors={fidelity.covered_count}->{expanded_fidelity.covered_count}"
            )
            return draft_summary

        if fidelity_needs_repair and expanded_fidelity.covered_count <= fidelity.covered_count:
            logger.info(
                f"[{self.name}] 标准总结保真修订未补回事实锚点，保留原结果: "
                f"anchors={fidelity.covered_count}->{expanded_fidelity.covered_count}"
            )
            return draft_summary

        if presentation_needs_repair and _report_presentation_needs_repair(expanded):
            logger.info(f"[{self.name}] 报告结构修订仍不完整，保留原结果")
            return draft_summary

        if expanded_chars <= current_chars and expanded_fidelity.covered_count <= fidelity.covered_count:
            logger.info(
                f"[{self.name}] 标准总结扩写未增加有效内容 "
                f"({current_chars}->{expanded_chars})，保留原结果"
            )
            return draft_summary

        logger.info(
            f"[{self.name}] 标准总结已扩写: "
            f"{current_chars}->{expanded_chars}, target={min_output_chars}, "
            f"anchors={fidelity.covered_count}->{expanded_fidelity.covered_count}"
        )
        return expanded

    async def _ensure_standard_timestamp_coverage(
        self,
        transcript_text: str,
        draft_summary: str,
        metrics: dict[str, float | int | bool],
        system_prompt: str,
    ) -> str:
        if not bool(metrics.get("has_timestamps")):
            return draft_summary

        current_chars = _visible_summary_char_count(draft_summary)
        expected_count = _expected_summary_timestamp_count(current_chars)
        maximum_count = _maximum_summary_timestamp_count(current_chars)
        current_count = _count_summary_timestamps(draft_summary)
        if current_count > maximum_count:
            limited_summary = _limit_summary_timestamp_density(draft_summary, maximum_count)
            limited_count = _count_summary_timestamps(limited_summary)
            if limited_count < current_count:
                logger.info(
                    f"[{self.name}] 标准总结时间戳过密，已收敛: "
                    f"{current_count}->{limited_count}"
                )
                draft_summary = limited_summary
                current_count = limited_count
        if current_count >= expected_count:
            return draft_summary

        repair_prompt = f"""请对下面的精编还原稿做一次局部质量修订，输出完整修订版。

严格要求：
- 保留现有主题标签、Markdown 结构、全部有效信息与观点归属，不删减、不另起炉灶、不输出修改说明。
- 依据原始转录中的六位时间码，在每个主要章节、实质性问答、关键案例/数字与观点转折处补上准确时间戳，格式为 `(见 HH:MM:SS)`；通常每 400-700 字至少一处。
- 当前仅有 {current_count} 个时间戳，建议补到 {expected_count}-{maximum_count} 个之间，最多 {maximum_count} 个；只使用原文真实存在的时间，不得猜测或伪造，也不要逐段机械补标。
- 多人转录没有显式说话人标签时，身份不能唯一确认就使用中性角色，不把上一段姓名自动延续，也不补写单位或头衔。
- 只纠正上下文能够明确判断的同音字、专有名词与转录错误；不确定时保留原意并简短标明，不创造新事实。
- 从 `{{...}}` 主题标签开始输出正文，结尾直接停止，不附自检、覆盖率或处理说明。

原始转录：
```plaintext
{transcript_text}
```

当前稿件：
```markdown
{draft_summary}
```
"""
        response_chunks: list[str] = []
        llm_error: LLMError | None = None

        def callback(chunk: str | LLMError):
            nonlocal llm_error
            if isinstance(chunk, LLMError):
                llm_error = chunk
                return
            response_chunks.append(chunk)

        try:
            await self._llm_client.response(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=repair_prompt),
                ],
                resp_callback=callback,
            )
        except Exception as error:
            logger.warning(f"[{self.name}] 时间戳补标失败，保留原结果: {error}")
            return draft_summary
        if llm_error:
            logger.warning(f"[{self.name}] 时间戳补标失败，保留原结果: {llm_error}")
            return draft_summary

        revised = clean_summary_output("".join(response_chunks))
        revised = _limit_summary_timestamp_density(revised, maximum_count)
        revised_chars = _visible_summary_char_count(revised)
        revised_count = _count_summary_timestamps(revised)
        minimum_acceptable_count = max(3, min(expected_count, max(current_count + 1, expected_count // 2)))
        if (
            revised_chars < int(current_chars * 0.9)
            or revised_count < minimum_acceptable_count
            or not candidate_preserves_fidelity(transcript_text, draft_summary, revised)
        ):
            logger.warning(
                f"[{self.name}] 时间戳补标稿未通过完整性校验: "
                f"chars={current_chars}->{revised_chars}, timestamps={current_count}->{revised_count}, "
                f"expected={expected_count}"
            )
            return draft_summary

        logger.info(
            f"[{self.name}] 标准总结已补标时间戳: "
            f"chars={current_chars}->{revised_chars}, timestamps={current_count}->{revised_count}"
        )
        return revised

    async def _run_chunked_summary(
        self,
        transcript_text: str,
        task_id: str | None,
        mode_value: str,
        summary_style: str,
    ) -> tuple[str, str | None, dict[str, Any]]:
        chunk_prompt = self._load_chunk_prompt(config.summarization.chunk_prompt_file)
        if not chunk_prompt:
            raise RuntimeError("分块提示词为空，无法执行 Agent 增强模式。")
        chunk_prompt = self._build_system_prompt(
            chunk_prompt,
            summary_style=summary_style,
            report_prompt_file=REPORT_CHUNK_PROMPT_FILE,
        )

        task_label = task_id or "<unknown>"
        logger.info(
            f"[{self.name}] 任务 {task_label} 准备执行 Agent 分块总结: "
            f"target={config.summarization.chunk_target_duration_sec}s, "
            f"min={config.summarization.chunk_min_duration_sec}s, "
            f"max={config.summarization.chunk_max_duration_sec}s, "
            f"boundary_jump={config.summarization.boundary_jump_sec}s"
        )
        try:
            preview_chunks = split_transcript_into_chunks(
                transcript_text=transcript_text,
                target_duration_sec=config.summarization.chunk_target_duration_sec,
                min_duration_sec=config.summarization.chunk_min_duration_sec,
                max_duration_sec=config.summarization.chunk_max_duration_sec,
                boundary_jump_sec=config.summarization.boundary_jump_sec,
            )
            logger.info(f"[{self.name}] 任务 {task_label} Agent 预分块结果: total_chunks={len(preview_chunks)}")
            for idx, chunk in enumerate(preview_chunks, start=1):
                logger.info(
                    f"[{self.name}] 任务 {task_label} 分块 {idx}/{len(preview_chunks)}: "
                    f"time={chunk.time_range_hms}, duration={chunk.duration_sec}s, lines={chunk.line_count}"
                )
        except Exception as e:
            logger.warning(f"[{self.name}] 任务 {task_label} Agent 预分块日志失败: {e}")

        def cancel_check() -> bool:
            return bool(task_id and self.is_task_cancelled(task_id))

        last_stream_update = 0.0
        last_stream_summary = ""
        update_in_progress = False

        def update_chunk_progress(done: int, total: int, partial_summary: str):
            nonlocal last_stream_summary
            if not task_id:
                return
            from ..db import db, TaskStatus

            if self.is_task_cancelled(task_id):
                return

            current_task = db.get_task(task_id)
            if not current_task:
                return
            current_status = str(current_task.get("status") or "")
            # 避免晚到的分块进度回写覆盖最终状态（例如已 COMPLETED 却被写回 SUMMARIZING）。
            if current_status != TaskStatus.SUMMARIZING.value:
                return

            progress = int((done / total) * 100) if total > 0 else 0
            from ..task_updater import update_and_notify
            self._submit_coro(update_and_notify(
                task_id,
                {
                    "status": TaskStatus.SUMMARIZING,
                    "summary": partial_summary,
                    "progress": progress,
                    "summary_mode": mode_value if mode_value in {"auto", "agent"} else "agent",
                    "summary_style": summary_style,
                    "summary_chunk_total": total,
                    "summary_chunk_done": done,
                },
            ))
            last_stream_summary = partial_summary

        async def flush_chunk_stream(done: int, total: int, streaming_summary: str):
            """异步防抖更新函数"""
            nonlocal last_stream_update, last_stream_summary, update_in_progress

            if not task_id or not streaming_summary or update_in_progress:
                return

            # 防抖：距离上次更新不足0.5秒则跳过
            now = asyncio.get_event_loop().time()
            if now - last_stream_update < 0.5:
                return

            if streaming_summary == last_stream_summary:
                return

            update_in_progress = True
            try:
                from ..db import db, TaskStatus
                from ..task_updater import update_and_notify

                # 只有当任务仍在 SUMMARIZING 状态时才更新
                current_task = db.get_task(task_id)
                if not current_task:
                    return
                current_status = str(current_task.get("status") or "")
                if current_status != TaskStatus.SUMMARIZING.value:
                    return

                progress = _estimate_agent_stream_progress(
                    done,
                    total,
                    streaming_summary,
                )
                await update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.SUMMARIZING,
                        "summary": streaming_summary,
                        "progress": progress,
                        "summary_mode": mode_value if mode_value in {"auto", "agent"} else "agent",
                        "summary_style": summary_style,
                        "summary_chunk_total": total,
                        "summary_chunk_done": done,
                    },
                )
                last_stream_update = now
                last_stream_summary = streaming_summary
            finally:
                update_in_progress = False

        def update_chunk_stream(done: int, total: int, streaming_summary: str):
            """同步回调函数，每次收到新内容就触发更新"""
            if self.is_task_cancelled(task_id):
                return
            # 每次收到新内容就尝试更新（防抖在flush内部处理）
            self._submit_coro(flush_chunk_stream(done, total, streaming_summary))

        summarizer = ChunkedSummarizer(
            llm_client=self._llm_client,
            chunk_system_prompt=chunk_prompt,
            chunk_target_duration_sec=config.summarization.chunk_target_duration_sec,
            chunk_min_duration_sec=config.summarization.chunk_min_duration_sec,
            chunk_max_duration_sec=config.summarization.chunk_max_duration_sec,
            boundary_jump_sec=config.summarization.boundary_jump_sec,
            prev_tail_timestamp_lines_m=config.summarization.prev_tail_timestamp_lines_m,
            prev_summary_tail_chars_j=config.summarization.prev_summary_tail_chars_j,
            llm_call_retry_max=config.summarization.llm_call_retry_max,
            max_agent_value_chars=config.summarization.max_agent_value_chars,
            summary_detail_level=config.summarization.summary_detail_level,
            cancel_check=cancel_check,
            chunk_debug_dump_enabled=config.summarization.chunk_debug_dump_enabled,
            chunk_debug_dump_dir=config.summarization.chunk_debug_dump_dir,
        )

        result = await summarizer.summarize(
            transcript_text=transcript_text,
            on_chunk_progress=update_chunk_progress,
            on_chunk_stream=update_chunk_stream,
        )
        topic = _extract_topic(result.summary_text)
        summary_meta = {
            "mode": "agent",
            "summary_style": summary_style,
            "chunk_total": result.chunk_total,
            "chunk_done": result.chunk_done,
            "summary_detail_level": config.summarization.summary_detail_level,
            "fidelity": assess_fidelity(transcript_text, result.summary_text).as_dict(),
            "warnings": result.warnings,
            "assembly_logs": result.assembly_logs,
        }
        return result.summary_text, topic, summary_meta

    def _build_system_prompt(
        self,
        base_prompt: str,
        summary_style: str,
        report_prompt_file: Path | None = None,
    ) -> str:
        if summary_style != "report":
            return base_prompt

        addon_path = report_prompt_file or REPORT_PROMPT_FILE
        addon = self._load_prompt_addon(addon_path)
        if not addon:
            logger.warning(f"[{self.name}] 报告化附加提示词为空，继续使用基础提示词。")
            return base_prompt
        return f"{base_prompt.rstrip()}\n\n---\n\n{addon.lstrip()}"

    def _load_prompt_addon(self, prompt_file: Path) -> str:
        normalized = str(prompt_file)
        if normalized in self._prompt_addon_cache:
            return self._prompt_addon_cache[normalized]
        try:
            text = prompt_file.read_text(encoding="utf-8")
        except Exception as error:
            logger.error(f"[{self.name}] 读取附加提示词失败: {normalized}, error={error}")
            return ""
        self._prompt_addon_cache[normalized] = text
        logger.info(f"[{self.name}] 附加提示词已加载: {normalized}")
        return text

    def _load_chunk_prompt(self, prompt_file: str) -> str:
        normalized = str(prompt_file or "").strip()
        if not normalized:
            return ""
        if self._chunk_prompt_cache_path == normalized and self._chunk_prompt_cache_text is not None:
            return self._chunk_prompt_cache_text

        try:
            with open(normalized, "r", encoding="utf-8") as f:
                text = f.read()
            self._chunk_prompt_cache_path = normalized
            self._chunk_prompt_cache_text = text
            logger.info(f"[{self.name}] 分块提示词已加载: {normalized}")
            return text
        except Exception as e:
            logger.error(f"[{self.name}] 读取分块提示词失败: {normalized}, error={e}")
            return ""

    async def _mark_failed(self, task_id: str | None, error_message: str) -> None:
        logger.error(f"[{self.name}] {error_message}")
        if not task_id:
            return
        if self.is_task_cancelled(task_id):
            return
        from ..db import TaskStatus
        from ..task_updater import update_and_notify

        await update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_message})


def _detail_level_label(detail_level: int) -> str:
    labels = {
        1: "精简",
        2: "适中",
        3: "详细",
        4: "很详细",
        5: "尽量完整",
    }
    return labels.get(max(1, min(5, int(detail_level))), "详细")


def _visible_summary_char_count(text: str) -> int:
    cleaned = re.sub(r"```state_ops[\s\S]*?```", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"\{\{chunk_\d+_(?:start|ended)\}\}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\{\{.*?\}\}", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return len(cleaned)


def _count_summary_timestamps(text: str) -> int:
    return len(re.findall(r"(?<!\d)\d{2}:\d{2}:\d{2}(?!\d)", text or ""))


def _expected_summary_timestamp_count(visible_chars: int) -> int:
    return max(3, min(24, max(0, int(visible_chars)) // 600))


def _maximum_summary_timestamp_count(visible_chars: int) -> int:
    return max(3, min(30, (max(0, int(visible_chars)) + 399) // 400))


def _report_presentation_needs_repair(summary: str) -> bool:
    if _visible_summary_char_count(summary) < 4000:
        return False
    heading_count = len(re.findall(r"(?m)^##\s+", summary or ""))
    callout_count = len(re.findall(r"(?mi)^>\s*\[![A-Z][A-Z0-9_-]{0,31}\]", summary or ""))
    has_structured_component = bool(
        re.search(r"(?mi)^(?:>\s*)?(?:[-*+]\s+|\d+[.)]\s+)", summary or "")
        or re.search(r"(?mi)^\s*\|.+\|\s*$", summary or "")
        or re.search(r"```mermaid\b", summary or "", flags=re.IGNORECASE)
    )
    return heading_count < 3 or callout_count < 4 or not has_structured_component


def _estimate_standard_stream_progress(partial_summary: str, target_chars: int) -> int:
    visible_chars = _visible_summary_char_count(partial_summary)
    if visible_chars <= 0:
        return 0
    normalized_target = max(1, int(target_chars))
    ratio = visible_chars / normalized_target
    if ratio <= 1:
        estimated = int(ratio * 70)
    else:
        estimated = int(70 + 25 * ((ratio - 1) / (ratio + 1)))
    return max(3, min(95, estimated))


def _estimate_agent_stream_progress(
    completed_chunks: int,
    total_chunks: int,
    partial_summary: str,
) -> int:
    if not str(partial_summary or "").strip() or total_chunks <= 0:
        return 0
    completed = max(0, min(int(completed_chunks), int(total_chunks)))
    estimated = int((completed / total_chunks) * 100)
    return max(3, min(95, estimated))


def _limit_summary_timestamp_density(text: str, maximum_count: int) -> str:
    current_count = _count_summary_timestamps(text)
    if current_count <= maximum_count:
        return text

    matches = list(_TIMESTAMP_ANNOTATION_RE.finditer(text))
    if not matches:
        return text

    annotation_counts = [_count_summary_timestamps(match.group(0)) for match in matches]
    non_annotation_count = current_count - sum(annotation_counts)
    allowed_annotation_count = max(0, maximum_count - non_annotation_count)
    if allowed_annotation_count <= 0:
        return text

    desired_match_count = min(len(matches), allowed_annotation_count)
    if desired_match_count >= len(matches):
        return text
    if desired_match_count == 1:
        kept_indices = {0}
    else:
        kept_indices = {
            round(position * (len(matches) - 1) / (desired_match_count - 1))
            for position in range(desired_match_count)
        }

    while kept_indices and sum(annotation_counts[index] for index in kept_indices) > allowed_annotation_count:
        removable = sorted(kept_indices - {0, len(matches) - 1}, reverse=True)
        kept_indices.remove(removable[0] if removable else max(kept_indices))

    result_parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        if index in kept_indices:
            continue
        result_parts.append(text[cursor:match.start()])
        cursor = match.end()
    result_parts.append(text[cursor:])
    return re.sub(r"[ \t]{2,}", " ", "".join(result_parts))


def _estimate_standard_min_output_chars(transcript_text: str, detail_level: int) -> int:
    text_chars = count_transcript_content_chars(transcript_text)
    if text_chars <= 0:
        return 0
    ratios = {
        1: 0.08,
        2: 0.14,
        3: 0.24,
        4: 0.42,
        5: 0.58,
    }
    floors = {
        1: 500,
        2: 800,
        3: 1200,
        4: 1800,
        5: 2400,
    }
    level = max(1, min(5, int(detail_level)))
    adaptive_floor = min(floors[level], max(300, int(text_chars * 0.8)))
    target = max(adaptive_floor, int(text_chars * ratios[level]))
    return min(target, 16000)


def _build_standard_user_prompt(
    transcript_text: str,
    metrics: dict[str, float | int | bool],
    detail_level: int,
    min_output_chars: int,
) -> str:
    lines: list[str] = []
    lines.append("请根据以下转录文本输出完整、结构化、可直接阅读的总结正文。")
    lines.append("")
    lines.append("## 详细度要求")
    lines.append(f"- 详细度等级：{max(1, min(5, int(detail_level)))}/5（{_detail_level_label(detail_level)}）。")
    if min_output_chars > 0:
        lines.append(f"- 不要过度压缩，目标正文不少于 {min_output_chars} 个汉字/字符；信息密集时可以更长。")
    if int(detail_level) >= 5:
        lines.append("- 详细度 5 的保真门槛：保留每个新的数字、范围、单位、专有名词、例子、条件、反例、问答回合、因果转折和不确定表述；只删除没有信息增量的口头禅、寒暄与机械重复。")
        lines.append("- 详细度 5 不是堆字数：不要重复同一结论或添加原文没有的解释；增加细节时必须回到原文对应的上下文，保证事实归属、顺序和语气准确。")
    lines.append("- 这是一份精编还原稿，不是提纲式摘要；要写清背景、过程、原因、例子、对比、转折、结果、数据和关键技术细节。")
    lines.append("- 按原文顺序覆盖不同的有效信息点；只有语义真正重复的内容才合并，不能因为观点相近就删除新的例子、条件或回应。")
    lines.append("- 先按实际内容判定主形态，再按局部内容切换整理方式；不得把任何内容默认套成主持人访谈。")
    lines.append("- 访谈、播客、圆桌、连麦、问答、辩论或协商，保住问题/主张—回答/依据—追问、异议或反例—回应、共识与未决分歧的链条和归属。")
    lines.append("- 会议、汇报、评审或研讨，保住议题、背景数据、方案选项、讨论、决策、待办、负责人和期限；原文未明确时不补写纪要字段。")
    lines.append("- 演讲、课程、知识讲解、单人观点或有声书，保住主题/主张—背景或定义—推导—案例/证据—条件、反例与限制—结论。")
    lines.append("- 教程、屏幕录制、实验、操作演示、代码/产品讲解和训练引导，保住目标、前置条件、步骤、工具/参数、理由、反馈、错误风险、验证结果和边界；不能把可复现过程压成结论。")
    lines.append("- 新闻、纪录、调查、传记、复盘、故事或剧情，保住主体/人物、时间线、背景、证据、因果、动机、冲突、选择、转折和结果，并区分事实、引述、角色台词与观点。")
    lines.append("- 影视/书影音解读、社会评论、反应视频、产品评测、发布会、路演或销售讲解，保住具体素材/功能/演示/条件，再保住分析、评价标准、对比、反例、优缺点、价格或承诺边界与结论。")
    lines.append("- Vlog、旅行探店、现场记录、真人秀、游戏/赛事解说、音乐/朗诵等内容按时间或节目推进；只整理转录能确认的画面、动作、音效或舞台信息。")
    lines.append("- 混合直播、合并分P或跨类型内容按自然话题切换上述链条，用短过渡句衔接，不要让后一段覆盖或重复前一段。")
    lines.append("- 数字、专有名词、类型清单、案例发生条件、鲜明比喻和能体现立场的短句优先保留；删除寒暄、口头禅、机械复述和无信息操作。")
    lines.append("- 发言者身份只依赖转录证据：有可靠说话人标签时保持一致；没有标签时，只有自报身份、被点名或上下文唯一确认才使用姓名。身份不唯一时用中性角色，单人内容无需反复添加角色前缀；不补写主持人、嘉宾、单位、头衔或未转录的视听细节。")
    lines.append("- 只输出最终 Markdown 正文：从 `{{...}}` 主题标签开始，不用代码围栏包正文，结尾不附覆盖率、自检结论、规则说明或处理说明。")
    lines.append("- 如果某个小节还没自然结束，先补完该小节，再进入新的一级章节；不要出现小节没写完就跳到下一章的情况。")
    if not bool(metrics.get("has_timestamps")):
        lines.append("- 原文没有时间戳，正文中不要添加、猜测或伪造任何时间戳。")
    else:
        lines.append("- 原文带时间戳时，每个主要章节、实质性问答、关键案例/数字和观点转折都要标注准确时间，通常每 400-700 字至少 1 处，格式为 `(见 HH:MM:SS)`；不要编造不存在的时间。")
    lines.append("")
    lines.append("## 原始转录文本")
    lines.append("```plaintext")
    lines.append(transcript_text)
    lines.append("```")
    return "\n".join(lines)


def _parse_summary_meta(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_topic(summary: str) -> str | None:
    for match in re.finditer(r"\{\{(.*?)\}\}", summary or "", re.IGNORECASE):
        token = (match.group(1) or "").strip()
        if not token:
            continue
        if token.lower().startswith("chunk_"):
            continue
        if token.lower().startswith("opt-tools"):
            continue
        return token
    return None

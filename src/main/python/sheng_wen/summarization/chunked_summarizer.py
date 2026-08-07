from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..llm.llm import LLM, LLMError, LLMMessage
from ..utils.logger import logger
from ..worker import TaskCancelledError
from .assembler import assemble_chunk_summaries
from .chunker import (
    TranscriptChunk,
    count_transcript_content_chars,
    split_transcript_into_chunks,
    tail_timestamp_lines,
)
from .output_cleaner import (
    clean_summary_output,
    count_summary_timestamps,
    limit_summary_timestamp_density,
)
from .fidelity import (
    assess_fidelity,
    candidate_preserves_fidelity,
    format_missing_anchors,
)
from .prompt_builder import build_chunk_user_prompt, generate_structure_overview
from .protocol import parse_state_ops, strip_state_instruction_blocks
from .state_manager import DynamicStateManager


def remove_duplicate_paragraphs(text: str) -> str:
    """移除连续重复的段落"""
    paragraphs = text.split('\n\n')
    result = []
    prev = None
    for para in paragraphs:
        para_stripped = para.strip()
        if para_stripped and para_stripped != prev:
            result.append(para)
            prev = para_stripped
    return '\n\n'.join(result)


def truncate_after_state_ops(text: str) -> str:
    """截断state_ops之后的所有内容"""
    pattern = r'(```state_ops\s*\{[^`]*\}\s*```)'
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if matches:
        last_match = matches[-1]
        truncated = text[:last_match.end()]
        if truncated != text:
            logger.info(f"[ChunkedSummarizer] 截断了state_ops之后的内容，移除了 {len(text) - len(truncated)} 个字符")
        return truncated
    return text


def truncate_discussed_topics(topics: str, max_items: int = 10) -> str:
    """只保留最近的N个标题，避免过长"""
    if not topics:
        return topics

    items = [item.strip() for item in topics.split(';') if item.strip()]
    if len(items) > max_items:
        truncated = '; '.join(items[-max_items:])
        logger.info(f"[ChunkedSummarizer] 截断已讨论主题从 {len(items)} 项到 {max_items} 项")
        return truncated
    return topics


def _visible_summary_char_count(text: str) -> int:
    cleaned = strip_state_instruction_blocks(text or "")
    cleaned = re.sub(r"\{\{chunk_\d+_(?:start|ended)\}\}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\{\{.*?\}\}", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return len(cleaned)


def _maximum_agent_timestamp_count(visible_chars: int) -> int:
    return max(3, min(90, (max(0, int(visible_chars)) + 349) // 350))


def _estimate_chunk_min_output_chars(chunk: TranscriptChunk, detail_level: int) -> int:
    text_chars = count_transcript_content_chars(chunk.text)
    ratios = {
        1: 0.10,
        2: 0.16,
        3: 0.25,
        4: 0.38,
        5: 0.52,
    }
    floors = {
        1: 350,
        2: 500,
        3: 800,
        4: 1200,
        5: 1600,
    }
    level = max(1, min(5, int(detail_level)))
    target = max(floors[level], int(text_chars * ratios[level]))
    return min(target, 6000)


def _merge_document_topic(summary: str, document_topic: str) -> str:
    normalized = (document_topic or "").strip()
    if not normalized:
        return summary
    if not (normalized.startswith("{{") and normalized.endswith("}}")):
        normalized = f"{{{{{normalized.strip('{} ')}}}}}"

    remaining = (summary or "").lstrip()
    leading_topic = re.compile(r"^\{\{(?!chunk_)(?!opt-tools)([^{}\n]+)\}\}\s*", re.IGNORECASE)
    while leading_topic.match(remaining):
        remaining = leading_topic.sub("", remaining, count=1).lstrip()
    return f"{normalized}\n\n{remaining}".rstrip()


def _extract_last_state_ops_block(text: str) -> str:
    matches = list(re.finditer(r"```state_ops\s*\{[\s\S]*?\}\s*```", text or "", re.IGNORECASE))
    if not matches:
        return ""
    return matches[-1].group(0)


def _insert_before_state_ops(text: str, insertion: str) -> str:
    state_block = _extract_last_state_ops_block(text)
    if not state_block:
        return f"{text.rstrip()}\n\n{insertion.strip()}"
    idx = text.rfind(state_block)
    if idx < 0:
        return f"{text.rstrip()}\n\n{insertion.strip()}"
    return f"{text[:idx].rstrip()}\n\n{insertion.strip()}\n\n{text[idx:].lstrip()}"


@dataclass
class ChunkedSummaryResult:
    summary_text: str
    chunk_total: int
    chunk_done: int
    assembly_logs: list[str]
    warnings: list[str]


class ChunkedSummarizer:
    def __init__(
        self,
        llm_client: LLM,
        chunk_system_prompt: str,
        chunk_target_duration_sec: int,
        chunk_min_duration_sec: int,
        chunk_max_duration_sec: int,
        boundary_jump_sec: int,
        prev_tail_timestamp_lines_m: int,
        prev_summary_tail_chars_j: int,
        llm_call_retry_max: int,
        max_agent_value_chars: int,
        summary_detail_level: int = 4,
        cancel_check: Callable[[], bool] | None = None,
        chunk_debug_dump_enabled: bool = False,
        chunk_debug_dump_dir: str = "temp/chunk_debug",
    ):
        self._llm_client = llm_client
        self._chunk_system_prompt = chunk_system_prompt
        self._chunk_target_duration_sec = max(60, int(chunk_target_duration_sec))
        self._chunk_min_duration_sec = max(30, int(chunk_min_duration_sec))
        self._chunk_max_duration_sec = max(self._chunk_target_duration_sec, int(chunk_max_duration_sec))
        self._boundary_jump_sec = max(1, int(boundary_jump_sec))
        self._prev_tail_timestamp_lines_m = max(0, int(prev_tail_timestamp_lines_m))
        self._prev_summary_tail_chars_j = max(0, int(prev_summary_tail_chars_j))
        self._llm_call_retry_max = max(1, int(llm_call_retry_max))
        self._max_agent_value_chars = max(100, int(max_agent_value_chars))
        self._summary_detail_level = max(1, min(5, int(summary_detail_level)))
        self._cancel_check = cancel_check
        self._chunk_debug_dump_enabled = bool(chunk_debug_dump_enabled)
        self._chunk_debug_dump_dir = Path(chunk_debug_dump_dir)

    async def summarize(
        self,
        transcript_text: str,
        on_chunk_progress: Callable[[int, int, str], None] | None = None,
        on_chunk_stream: Callable[[int, int, str], None] | None = None,
    ) -> ChunkedSummaryResult:
        chunks = split_transcript_into_chunks(
            transcript_text=transcript_text,
            target_duration_sec=self._chunk_target_duration_sec,
            min_duration_sec=self._chunk_min_duration_sec,
            max_duration_sec=self._chunk_max_duration_sec,
            boundary_jump_sec=self._boundary_jump_sec,
        )
        state = DynamicStateManager()
        chunk_outputs: list[str] = []
        warnings: list[str] = []

        if on_chunk_progress:
            on_chunk_progress(0, len(chunks), "")

        for idx, chunk in enumerate(chunks):
            self._ensure_not_cancelled()
            self._prepare_program_state(state, chunk, idx, chunks, chunk_outputs)
            structure = generate_structure_overview(chunk_outputs)
            user_prompt = build_chunk_user_prompt(
                chunk=chunk,
                chunk_index=idx,
                total_chunks=len(chunks),
                dynamic_state=state,
                structure_overview=structure,
                detail_level=self._summary_detail_level,
                min_output_chars=_estimate_chunk_min_output_chars(chunk, self._summary_detail_level),
            )
            chunk_prefix = ""
            if on_chunk_stream and chunk_outputs:
                chunk_prefix, _ = assemble_chunk_summaries(chunk_outputs)

            def emit_chunk_stream(partial_chunk_output: str):
                if not on_chunk_stream:
                    return
                preview = partial_chunk_output
                if chunk_prefix.strip():
                    preview = f"{chunk_prefix.rstrip()}\n\n{partial_chunk_output}"
                on_chunk_stream(idx, len(chunks), preview)

            output = await self._call_llm_with_retry(
                user_prompt,
                on_partial=emit_chunk_stream if on_chunk_stream else None,
            )
            output = await self._expand_chunk_if_too_short(
                output=output,
                chunk=chunk,
                chunk_index=idx,
                total_chunks=len(chunks),
                user_prompt=user_prompt,
                min_output_chars=_estimate_chunk_min_output_chars(chunk, self._summary_detail_level),
            )
            chunk_outputs.append(output)

            parsed_ops, parse_warnings = parse_state_ops(output)
            if parse_warnings:
                warnings.extend([f"chunk_{idx}: {msg}" for msg in parse_warnings])
            apply_warnings = state.apply_ops(parsed_ops, max_value_chars=self._max_agent_value_chars)
            if apply_warnings:
                warnings.extend([f"chunk_{idx}: {msg}" for msg in apply_warnings])

            if self._chunk_debug_dump_enabled:
                self._dump_chunk_debug(idx, chunk, user_prompt, output, state.snapshot())

            if on_chunk_progress:
                partial_summary, _ = assemble_chunk_summaries(chunk_outputs)
                on_chunk_progress(idx + 1, len(chunks), partial_summary)

        final_summary, assembly_logs = assemble_chunk_summaries(chunk_outputs)

        # 提取最后一块生成的文档主题（如果有）
        doc_topic = state.snapshot().get("agent", {}).get("文档主题", "").strip()
        if doc_topic:
            final_summary = _merge_document_topic(final_summary, doc_topic)
            assembly_logs.append(f"已合并文档主题: {doc_topic}")
        final_summary = clean_summary_output(final_summary)
        timestamp_count = count_summary_timestamps(final_summary)
        maximum_timestamp_count = _maximum_agent_timestamp_count(
            _visible_summary_char_count(final_summary)
        )
        if timestamp_count > maximum_timestamp_count:
            final_summary = limit_summary_timestamp_density(
                final_summary,
                maximum_timestamp_count,
            )
            assembly_logs.append(
                "已收敛时间戳密度: "
                f"{timestamp_count}->{count_summary_timestamps(final_summary)}"
            )

        return ChunkedSummaryResult(
            summary_text=final_summary,
            chunk_total=len(chunks),
            chunk_done=len(chunks),
            assembly_logs=assembly_logs,
            warnings=warnings,
        )

    def _prepare_program_state(
        self,
        state: DynamicStateManager,
        chunk: TranscriptChunk,
        idx: int,
        all_chunks: list[TranscriptChunk],
        chunk_outputs: list[str],
    ) -> None:
        state.update_program(
            {
                "当前块时间范围": chunk.time_range_hms,
                "流程标记": f"当前块索引={idx}, 总块数={len(all_chunks)}, 是否最后一块={idx == len(all_chunks) - 1}",
            }
        )

        if idx <= 0:
            return

        prev_chunk = all_chunks[idx - 1]
        prev_summary = chunk_outputs[-1] if chunk_outputs else ""
        # 清理 state_ops 块后再取末尾，避免将 state_ops 内容传递给下一块
        prev_summary_cleaned = strip_state_instruction_blocks(prev_summary)

        # 取末尾片段并去重
        prev_summary_tail = prev_summary_cleaned[-self._prev_summary_tail_chars_j:]
        prev_summary_tail = remove_duplicate_paragraphs(prev_summary_tail)

        state.set_program_value(
            "前块转录文本末尾",
            f"```plaintext\n{tail_timestamp_lines(prev_chunk, self._prev_tail_timestamp_lines_m)}\n```",
        )
        state.set_program_value(
            "前块总结片段末尾",
            f"```markdown\n{prev_summary_tail}\n```",
        )

    async def _call_llm_with_retry(
        self,
        user_prompt: str,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        attempt = 0
        last_error: Exception | None = None
        while attempt < self._llm_call_retry_max:
            attempt += 1
            try:
                return await self._call_llm_once(user_prompt, on_partial=on_partial)
            except asyncio.CancelledError:
                raise
            except TaskCancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt >= self._llm_call_retry_max:
                    break
                logger.warning(f"[ChunkedSummarizer] LLM 调用失败，准备重试 ({attempt}/{self._llm_call_retry_max}): {e}")
                await asyncio.sleep(min(2.0, 0.3 * attempt))

        raise RuntimeError(f"分块总结调用失败: {last_error}")

    async def _call_llm_once(
        self,
        user_prompt: str,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        self._ensure_not_cancelled()
        messages = [
            LLMMessage(role="system", content=self._chunk_system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        response_chunks: list[str] = []
        llm_error: LLMError | None = None
        last_emit_at = 0.0
        last_emit_len = 0

        def callback(chunk: str | LLMError):
            nonlocal llm_error, last_emit_at, last_emit_len
            if self._cancel_check and self._cancel_check():
                raise asyncio.CancelledError()
            if isinstance(chunk, LLMError):
                llm_error = chunk
                return
            response_chunks.append(chunk)
            if on_partial:
                now = asyncio.get_event_loop().time()
                if now - last_emit_at >= 0.5:
                    text = "".join(response_chunks)
                    try:
                        on_partial(text)
                    except Exception as e:
                        logger.warning(f"[ChunkedSummarizer] 分块流式回调失败: {e}")
                    last_emit_at = now
                    last_emit_len = len(text)

        await self._llm_client.response(messages=messages, resp_callback=callback)
        if llm_error:
            raise llm_error
        self._ensure_not_cancelled()
        final_text = "".join(response_chunks)

        # 截断state_ops之后的所有内容（防止思考过程泄露）
        final_text = truncate_after_state_ops(final_text)

        if on_partial and len(final_text) != last_emit_len:
            try:
                on_partial(final_text)
            except Exception as e:
                logger.warning(f"[ChunkedSummarizer] 分块最终流式回调失败: {e}")
        return final_text

    async def _expand_chunk_if_too_short(
        self,
        output: str,
        chunk: TranscriptChunk,
        chunk_index: int,
        total_chunks: int,
        user_prompt: str,
        min_output_chars: int,
    ) -> str:
        if min_output_chars <= 0:
            return output
        current_chars = _visible_summary_char_count(output)
        fidelity = assess_fidelity(chunk.text, output)
        detail_five_needs_repair = self._summary_detail_level >= 5 and fidelity.needs_repair
        length_needs_repair = current_chars < int(min_output_chars * 0.9)
        if not length_needs_repair and not detail_five_needs_repair:
            return output

        self._ensure_not_cancelled()
        no_timestamp_rule = ""
        if not chunk.has_timestamps:
            no_timestamp_rule = "\n- 原文没有时间戳，修订稿中不要添加、猜测或伪造任何时间戳。"

        fidelity_rule = ""
        if detail_five_needs_repair:
            fidelity_rule = f"""
- 这是详细度 5 的事实保真修订：逐项核对原文并补回尚未明确保留的数字、范围、单位、引号短语和技术标识，包括：{format_missing_anchors(fidelity)}。
- 把补回的事实放回原文对应的问答、案例、步骤或论证上下文；不要重复结论、堆砌锚点或虚构解释。
"""

        repair_prompt = f"""请把下面这个分块总结扩写成更完整的修订版。

要求：
- 输出完整修订版，不要输出修改说明。
- 保留原有 Markdown 结构、主题标签、`{{{{chunk_{chunk_index}_ended}}}}` 标记和最后的 `state_ops` 代码块。
- 当前可读正文约 {current_chars} 字符，目标至少 {min_output_chars} 字符；按原文顺序补足遗漏的问答链、观点归属、数字、条件、清单、案例、论证过程和必要细节。
- 只有寒暄、口头禅、机械复述与无信息操作可以删除；不要用空泛概括替代具体内容，也不要虚构信息。
- 如果某个小节还没自然结束，先补完该小节，再进入新的一级章节；不要出现小节没写完就跳到下一章的情况。{no_timestamp_rule}
{fidelity_rule}

原始分块提示：
```markdown
{user_prompt}
```

当前草稿：
```markdown
{output}
```
"""
        try:
            expanded = await self._call_llm_with_retry(repair_prompt)
        except Exception as e:
            logger.warning(f"[ChunkedSummarizer] 分块 {chunk_index} 扩写失败，保留原结果: {e}")
            return output

        expanded = truncate_after_state_ops(expanded)
        marker = f"{{{{chunk_{chunk_index}_ended}}}}"
        if marker not in expanded:
            expanded = _insert_before_state_ops(expanded, marker)

        if "```state_ops" not in expanded:
            original_state_ops = _extract_last_state_ops_block(output)
            if original_state_ops:
                expanded = f"{expanded.rstrip()}\n\n{original_state_ops}"

        expanded_chars = _visible_summary_char_count(expanded)
        expanded_fidelity = assess_fidelity(chunk.text, expanded)
        if not candidate_preserves_fidelity(chunk.text, output, expanded):
            logger.info(
                f"[ChunkedSummarizer] 分块 {chunk_index} 修订未通过保真校验，保留原结果: "
                f"chars={current_chars}->{expanded_chars}, "
                f"anchors={fidelity.covered_count}->{expanded_fidelity.covered_count}"
            )
            return output

        if expanded_chars <= current_chars and expanded_fidelity.covered_count <= fidelity.covered_count:
            logger.info(
                f"[ChunkedSummarizer] 分块 {chunk_index} 修订未增加有效内容 "
                f"({current_chars}->{expanded_chars})，保留原结果"
            )
            return output

        logger.info(
            f"[ChunkedSummarizer] 分块 {chunk_index + 1}/{total_chunks} 已扩写: "
            f"{current_chars}->{expanded_chars}, target={min_output_chars}, "
            f"anchors={fidelity.covered_count}->{expanded_fidelity.covered_count}"
        )
        return expanded

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_check and self._cancel_check():
            raise TaskCancelledError("任务已取消，停止分块总结。")

    def _dump_chunk_debug(
        self,
        idx: int,
        chunk: TranscriptChunk,
        input_text: str,
        output_text: str,
        state_snapshot: dict,
    ) -> None:
        try:
            chunk_dir = self._chunk_debug_dump_dir / f"chunk_{idx:02d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            (chunk_dir / "input.md").write_text(input_text, encoding="utf-8")
            (chunk_dir / "output.md").write_text(output_text, encoding="utf-8")
            (chunk_dir / "chunk_meta.json").write_text(
                json.dumps(
                    {
                        "index": idx,
                        "start_timestamp_sec": chunk.start_timestamp_sec,
                        "end_timestamp_sec": chunk.end_timestamp_sec,
                        "line_count": chunk.line_count,
                        "has_timestamps": chunk.has_timestamps,
                        "label": chunk.label,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (chunk_dir / "state_after.json").write_text(
                json.dumps(state_snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ChunkedSummarizer] 分块调试落盘失败: {e}")

from __future__ import annotations

import re

from .protocol import strip_state_instruction_blocks


CHUNK_START_RE = re.compile(r"\{\{chunk_(\d+)_start\}\}", re.IGNORECASE)
CHUNK_END_RE = re.compile(r"\{\{chunk_(\d+)_ended\}\}", re.IGNORECASE)


def remove_program_markers(text: str) -> str:
    """移除所有程序内部标记"""
    # 移除 {{chunk_N_start}} 和 {{chunk_N_ended}}
    text = CHUNK_START_RE.sub("", text)
    text = CHUNK_END_RE.sub("", text)
    # 清理多余的空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def assemble_chunk_summaries(chunk_summaries: list[str]) -> tuple[str, list[str]]:
    if not chunk_summaries:
        return ("", [])

    logs: list[str] = [f"开始组装 {len(chunk_summaries)} 个分块结果"]
    parts: list[str] = []

    for idx, raw in enumerate(chunk_summaries):
        summary = strip_state_instruction_blocks(raw).strip()
        if not summary:
            logs.append(f"块 {idx}: 空内容，跳过")
            continue

        has_start = bool(re.search(rf"\{{\{{chunk_{idx}_start\}}\}}", summary, re.IGNORECASE))
        if idx > 0 and has_start and parts:
            prev_idx = idx - 1
            # 移除前块的 {{chunk_N_ended}} 标记，并清理末尾的省略号和空白
            prev_text = parts[-1]
            prev_text = re.sub(rf"\{{\{{chunk_{prev_idx}_ended\}}\}}", "", prev_text, flags=re.IGNORECASE)
            prev_text = prev_text.rstrip()
            # 如果末尾是省略号（...或…），移除它
            prev_text = re.sub(r'[.。]{2,}$|…+$', '', prev_text).rstrip()
            parts[-1] = prev_text

            # 移除当前块的 {{chunk_N_start}} 标记
            summary = re.sub(rf"^\s*\{{\{{chunk_{idx}_start\}}\}}", "", summary, flags=re.IGNORECASE).lstrip()

            # 无缝拼接（不添加换行），因为这是续写同一个句子
            parts[-1] = parts[-1] + summary
            logs.append(f"块 {idx}: 检测到续写标记，已无缝拼接（移除省略号）")
            continue

        parts.append(summary)
        logs.append(f"块 {idx}: 追加正文")

    final = "\n\n".join(parts).strip()
    # 使用统一的清理函数移除所有程序标记
    final = remove_program_markers(final)
    logs.append(f"组装完成，最终长度: {len(final)} 字符")
    return (final, logs)


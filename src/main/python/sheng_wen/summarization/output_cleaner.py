from __future__ import annotations

import re


_LEADING_TOPIC_FENCE = re.compile(
    r"^\s*```(?:html|markdown|md)?\s*\n(\{\{(?!chunk_)(?!opt-tools)[^{}\n]+\}\})\s*```\s*",
    re.IGNORECASE,
)
_WRAPPING_MARKDOWN_FENCE = re.compile(
    r"^\s*```(?:html|markdown|md)?\s*\n([\s\S]*?)\n```\s*$",
    re.IGNORECASE,
)
_TIMESTAMP_TOKEN_RE = re.compile(r"(?<!\d)\d{2}:\d{2}:\d{2}(?!\d)")
_TIMESTAMP_ANNOTATION_RE = re.compile(
    r"[（(]\s*(?:见\s*)?\d{2}:\d{2}:\d{2}"
    r"(?:\s*[-–—~至]\s*\d{2}:\d{2}:\d{2})?\s*[)）]"
)
_TRAILING_META_MARKERS = (
    "全文精编还原",
    "覆盖原文有效信息",
    "图表仅辅助可视化",
    "无伪造时间戳",
    "已按要求处理",
    "以上为整理结果",
)


def count_summary_timestamps(text: str) -> int:
    return len(_TIMESTAMP_TOKEN_RE.findall(text or ""))


def limit_summary_timestamp_density(text: str, maximum_count: int) -> str:
    current_count = count_summary_timestamps(text)
    if current_count <= maximum_count:
        return text

    matches = list(_TIMESTAMP_ANNOTATION_RE.finditer(text))
    if not matches:
        return text

    annotation_counts = [count_summary_timestamps(match.group(0)) for match in matches]
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


def clean_summary_output(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    cleaned = _LEADING_TOPIC_FENCE.sub(r"\1\n\n", cleaned, count=1).strip()
    wrapped = _WRAPPING_MARKDOWN_FENCE.match(cleaned)
    if wrapped:
        cleaned = wrapped.group(1).strip()

    paragraphs = re.split(r"\n{2,}", cleaned)
    while paragraphs and any(marker in paragraphs[-1] for marker in _TRAILING_META_MARKERS):
        paragraphs.pop()
    return "\n\n".join(paragraphs).strip()

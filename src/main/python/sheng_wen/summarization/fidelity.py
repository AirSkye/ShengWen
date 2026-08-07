from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


_TIMESTAMP_LINE_RE = re.compile(r"(?m)^\s*(?:\d{6}|\[?\d{2}:\d{2}:\d{2}\]?)\s*")
_NUMBER_ANCHOR_RE = re.compile(
    r"(?<![\d])"
    r"(?:\d+(?:[.,]\d+)?\s*(?:(?:到|至|[-~～])\s*\d+(?:[.,]\d+)?)?"
    r"\s*(?:%|％|万|亿|千|百|元|美元|分钟|小时|秒|天|周|月|年|日|个|次|步|分|岁|人|支|项|条|台|倍|层|级|轮|家|场|块|题|点|米|公里|GB|MB|KB|ms|s)?)"
    r"(?![\d])",
    re.IGNORECASE,
)
_QUOTED_ANCHOR_RE = re.compile(r"[“‘『「\"]([^”’』」\"]{2,32})[”’』」\"]")
_ASCII_ANCHOR_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_./+#:@-]{1,31}(?![A-Za-z0-9])")
_MARKUP_RE = re.compile(r"[`*_>#~\[\]{}()（）【】、，。；：:！？!?…·|\\]+")
_CHINESE_NUMBER_TOKEN = r"[零〇一二两三四五六七八九十百千万亿]+"
_NUMBER_UNIT_TOKEN = (
    r"(?:%|％|万|亿|千|百|元|美元|分钟|小时|秒|天|周|月|年|日|个|次|步|分|岁|人|支|项|条|台|倍|层|级|轮|家|场|块|题|点|米|公里|gb|mb|kb|ms|s)"
)
_CHINESE_NUMBER_RANGE_RE = re.compile(
    rf"({_CHINESE_NUMBER_TOKEN})(到|至|[-~～])({_CHINESE_NUMBER_TOKEN})(?=多?{_NUMBER_UNIT_TOKEN})"
)
_CHINESE_NUMBER_WITH_UNIT_RE = re.compile(
    rf"({_CHINESE_NUMBER_TOKEN})(?=多?{_NUMBER_UNIT_TOKEN})"
)
_GENERIC_ASCII = {
    "about",
    "after",
    "again",
    "because",
    "before",
    "between",
    "both",
    "current",
    "first",
    "from",
    "into",
    "more",
    "next",
    "only",
    "that",
    "their",
    "there",
    "these",
    "this",
    "through",
    "with",
    "without",
    "hello",
    "wow",
    "okay",
    "okok",
    "author",
    "export",
    "get",
    "up",
    "as",
}


@dataclass(frozen=True)
class FidelityReport:
    source_chars: int
    summary_chars: int
    anchor_count: int
    covered_count: int
    missing_anchors: tuple[str, ...]

    @property
    def coverage_ratio(self) -> float:
        if self.anchor_count <= 0:
            return 1.0
        return self.covered_count / self.anchor_count

    @property
    def needs_repair(self) -> bool:
        return self.anchor_count >= 4 and self.coverage_ratio < 0.84

    def as_dict(self) -> dict[str, object]:
        return {
            "source_chars": self.source_chars,
            "summary_chars": self.summary_chars,
            "anchor_count": self.anchor_count,
            "covered_count": self.covered_count,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "missing_anchors": list(self.missing_anchors),
        }


def _without_timestamp_prefixes(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in (text or "").splitlines():
        content = _TIMESTAMP_LINE_RE.sub("", raw_line, count=1)
        previous_digits = re.search(r"\d{5,}$", cleaned_lines[-1]) if cleaned_lines else None
        continued_digits = re.match(r"\d{5,}", content)
        if previous_digits and continued_digits:
            cleaned_lines[-1] += content
        else:
            cleaned_lines.append(content)
    return "\n".join(cleaned_lines)


def _chinese_integer_to_arabic(value: str) -> str:
    digit_values = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if not value or any(char not in digit_values and char not in "十百千万亿" for char in value):
        return value
    if all(char in digit_values for char in value):
        return "".join(str(digit_values[char]) for char in value)

    small_units = {"十": 10, "百": 100, "千": 1000}
    large_units = {"万": 10_000, "亿": 100_000_000}
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digit_values:
            number = digit_values[char]
        elif char in small_units:
            section += (number or 1) * small_units[char]
            number = 0
        else:
            section += number
            total += (section or 1) * large_units[char]
            section = 0
            number = 0
    return str(total + section + number)


def _normalize_chinese_unit_numbers(value: str) -> str:
    def replace_range(match: re.Match[str]) -> str:
        return (
            f"{_chinese_integer_to_arabic(match.group(1))}"
            f"{match.group(2)}"
            f"{_chinese_integer_to_arabic(match.group(3))}"
        )

    value = _CHINESE_NUMBER_RANGE_RE.sub(replace_range, value)
    return _CHINESE_NUMBER_WITH_UNIT_RE.sub(
        lambda match: _chinese_integer_to_arabic(match.group(1)),
        value,
    )


def _normalize_for_match(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    value = value.replace("％", "%").replace("～", "~").replace("－", "-")
    value = _normalize_chinese_unit_numbers(value)
    value = re.sub(r"(?<=\d)\s*(?:到|至|~|—|–|-)\s*(?=\d)", "-", value)
    value = re.sub(r"\s+", "", value)
    value = _MARKUP_RE.sub("", value)
    return value


def _clean_anchor(value: str) -> str:
    cleaned = _normalize_for_match(value)
    return cleaned.strip(".,;:!?，。；：！？")


def _is_useful_number(value: str) -> bool:
    digits = re.findall(r"\d+(?:[.,]\d+)?", value)
    if not digits:
        return False
    has_unit = bool(re.search(
        r"(?:%|％|万|亿|千|百|元|美元|分钟|小时|秒|天|周|月|年|日|个|次|步|分|岁|人|支|项|条|台|倍|层|级|轮|家|场|块|题|点|米|公里|GB|MB|KB|ms|s)",
        value,
        re.I,
    ))
    has_range = bool(re.search(r"到|至|[-~～]", value))
    if has_unit or has_range:
        return True
    if any("." in item or "," in item for item in digits):
        return True
    try:
        numeric_values = [float(item.replace(",", "")) for item in digits]
    except ValueError:
        return False
    return any(1900 <= value <= 2100 or value >= 1000 for value in numeric_values)


def _is_useful_ascii(value: str, frequency: int) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered in _GENERIC_ASCII or len(normalized) < 2:
        return False
    if re.search(r"\d|[._/+#:@-]", normalized):
        return True
    if normalized.isupper() and 2 <= len(normalized) <= 12:
        return True
    if re.search(r"[a-z][A-Z]", normalized):
        return True
    return frequency >= 2 and len(normalized) >= 3


def extract_factual_anchors(text: str, max_anchors: int = 120) -> list[str]:
    """Extract compact, checkable facts without treating timestamps as content."""
    source = _without_timestamp_prefixes(text or "")
    candidates: list[tuple[str, int]] = []

    for match in _NUMBER_ANCHOR_RE.finditer(source):
        value = match.group(0).strip()
        if _is_useful_number(value):
            candidates.append((value, 3))

    for match in _QUOTED_ANCHOR_RE.finditer(source):
        value = match.group(1).strip()
        if len(_normalize_for_match(value)) >= 2:
            candidates.append((value, 2))

    ascii_values = [match.group(0).strip() for match in _ASCII_ANCHOR_RE.finditer(source)]
    ascii_frequencies = Counter(value.lower() for value in ascii_values)
    for value in ascii_values:
        if _is_useful_ascii(value, ascii_frequencies[value.lower()]):
            candidates.append((value, 1))

    # Keep the first readable spelling for each normalized token and rank by
    # factual priority plus frequency. This keeps prompts compact on long ASR.
    grouped: dict[str, tuple[str, int, int]] = {}
    frequencies = Counter(_clean_anchor(value) for value, _ in candidates)
    for value, priority in candidates:
        key = _clean_anchor(value)
        if not key or len(key) < 2:
            continue
        current = grouped.get(key)
        count = frequencies[key]
        if current is None or priority > current[1]:
            grouped[key] = (value, priority, count)

    ranked = sorted(
        grouped.values(),
        key=lambda item: (-item[1], -item[2], len(item[0]), item[0]),
    )
    return [item[0] for item in ranked[: max(1, int(max_anchors))]]


def assess_fidelity(source_text: str, summary_text: str, max_anchors: int = 120) -> FidelityReport:
    source_body = _without_timestamp_prefixes(source_text or "")
    summary_body = summary_text or ""
    anchors = extract_factual_anchors(source_body, max_anchors=max_anchors)
    normalized_summary = _normalize_for_match(summary_body)
    covered: list[str] = []
    missing: list[str] = []
    for anchor in anchors:
        if _clean_anchor(anchor) in normalized_summary:
            covered.append(anchor)
        else:
            missing.append(anchor)

    return FidelityReport(
        source_chars=len(re.sub(r"\s+", "", source_body)),
        summary_chars=len(re.sub(r"\s+", "", summary_body)),
        anchor_count=len(anchors),
        covered_count=len(covered),
        missing_anchors=tuple(missing[:24]),
    )


def visible_summary_chars(text: str) -> int:
    cleaned = re.sub(r"```state_ops[\s\S]*?```", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"\{\{.*?\}\}", "", cleaned)
    return len(re.sub(r"\s+", "", cleaned))


def candidate_preserves_fidelity(
    source_text: str,
    baseline_summary: str,
    candidate_summary: str,
    minimum_length_ratio: float = 0.92,
) -> bool:
    baseline = assess_fidelity(source_text, baseline_summary)
    candidate = assess_fidelity(source_text, candidate_summary)
    baseline_chars = visible_summary_chars(baseline_summary)
    candidate_chars = visible_summary_chars(candidate_summary)
    if baseline_chars > 0 and candidate_chars < int(baseline_chars * minimum_length_ratio):
        return False
    if candidate.covered_count < baseline.covered_count:
        return False
    return candidate.coverage_ratio + 0.0001 >= baseline.coverage_ratio


def format_missing_anchors(report: FidelityReport, limit: int = 12) -> str:
    values = [item for item in report.missing_anchors[: max(1, int(limit))] if item]
    return "、".join(values) if values else "（无）"

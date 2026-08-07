from __future__ import annotations

import re
from dataclasses import dataclass


TIMESTAMP_PATTERN = re.compile(r"^(\d{6})")


@dataclass
class TranscriptChunk:
    index: int
    text: str
    start_timestamp_sec: int
    end_timestamp_sec: int
    line_count: int
    has_timestamps: bool = True
    label: str | None = None

    @property
    def duration_sec(self) -> int:
        return max(0, self.end_timestamp_sec - self.start_timestamp_sec)

    @property
    def time_range_hms(self) -> str:
        if self.label:
            return self.label
        return f"{_sec_to_hms(self.start_timestamp_sec)}-{_sec_to_hms(self.end_timestamp_sec)}"


def _sec_to_hms(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_timestamp_sec(line: str) -> int | None:
    match = TIMESTAMP_PATTERN.match((line or "").strip())
    if not match:
        return None
    raw = match.group(1)
    try:
        hours = int(raw[0:2])
        minutes = int(raw[2:4])
        seconds = int(raw[4:6])
    except (TypeError, ValueError):
        return None
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def count_timestamp_lines(transcript_text: str) -> int:
    count = 0
    for line in (transcript_text or "").splitlines():
        if parse_timestamp_sec(line) is not None:
            count += 1
    return count


def count_transcript_content_chars(transcript_text: str) -> int:
    content_lines: list[str] = []
    for line in (transcript_text or "").splitlines():
        stripped = line.strip()
        if parse_timestamp_sec(stripped) is not None:
            stripped = stripped[6:]
        content_lines.append(stripped)
    return len(re.sub(r"\s+", "", "\n".join(content_lines)))


def measure_transcript_complexity(transcript_text: str) -> dict[str, int | bool]:
    raw = transcript_text or ""
    timestamp_line_count = count_timestamp_lines(raw)
    non_empty_lines = [line for line in raw.splitlines() if line.strip()]
    plain_char_count = len(re.sub(r"\s+", "", raw))

    if timestamp_line_count > 0:
        effective_line_count = timestamp_line_count
        has_timestamps = True
    else:
        estimated_lines_from_chars = (plain_char_count + 79) // 80 if plain_char_count else 0
        effective_line_count = max(len(non_empty_lines), estimated_lines_from_chars)
        has_timestamps = False

    return {
        "has_timestamps": has_timestamps,
        "timestamp_line_count": timestamp_line_count,
        "line_count": effective_line_count,
        "plain_char_count": plain_char_count,
        "content_char_count": count_transcript_content_chars(raw),
    }


def split_transcript_into_chunks(
    transcript_text: str,
    target_duration_sec: int,
    min_duration_sec: int,
    max_duration_sec: int,
    boundary_jump_sec: int,
) -> list[TranscriptChunk]:
    lines = []
    for line in (transcript_text or "").splitlines():
        ts = parse_timestamp_sec(line)
        if ts is None:
            continue
        lines.append((ts, line))

    if not lines:
        return _split_plain_text_into_chunks(
            transcript_text=transcript_text,
            target_chars=target_duration_sec,
            min_chars=min_duration_sec,
            max_chars=max_duration_sec,
        )

    if len(lines) == 1:
        only_ts, only_line = lines[0]
        return [
            TranscriptChunk(
                index=0,
                text=only_line,
                start_timestamp_sec=only_ts,
                end_timestamp_sec=only_ts,
                line_count=1,
                has_timestamps=True,
            )
        ]

    chunks: list[TranscriptChunk] = []
    start_idx = 0
    start_ts = lines[0][0]
    i = 1
    while i < len(lines):
        current_ts = lines[i][0]
        duration = current_ts - start_ts

        if duration >= max_duration_sec:
            _append_chunk(chunks, lines, start_idx, i)
            start_idx = i
            start_ts = lines[i][0]
            i += 1
            continue

        if duration >= target_duration_sec:
            split_idx = i
            search_end = min(i + 50, len(lines) - 1)
            j = i
            while j <= search_end:
                gap = lines[j][0] - lines[j - 1][0]
                if gap >= boundary_jump_sec:
                    split_idx = j
                    break
                if lines[j][0] - start_ts >= max_duration_sec:
                    split_idx = j
                    break
                j += 1

            _append_chunk(chunks, lines, start_idx, split_idx)
            start_idx = split_idx
            start_ts = lines[start_idx][0]
            i = start_idx + 1
            continue

        i += 1

    if start_idx < len(lines):
        _append_chunk(chunks, lines, start_idx, len(lines))

    if len(chunks) >= 2 and chunks[-1].duration_sec < min_duration_sec:
        prev = chunks[-2]
        tail = chunks[-1]
        merged_lines = prev.text.splitlines() + tail.text.splitlines()
        chunks[-2] = TranscriptChunk(
            index=prev.index,
            text="\n".join(merged_lines),
            start_timestamp_sec=prev.start_timestamp_sec,
            end_timestamp_sec=tail.end_timestamp_sec,
            line_count=len(merged_lines),
            has_timestamps=True,
        )
        chunks.pop()
        for idx, chunk in enumerate(chunks):
            chunk.index = idx

    return chunks


def _append_chunk(
    chunks: list[TranscriptChunk],
    lines: list[tuple[int, str]],
    start_idx: int,
    end_idx: int,
) -> None:
    if end_idx <= start_idx:
        return
    chunk_lines = lines[start_idx:end_idx]
    text_lines = [line for _, line in chunk_lines]
    chunks.append(
        TranscriptChunk(
            index=len(chunks),
            text="\n".join(text_lines),
            start_timestamp_sec=chunk_lines[0][0],
            end_timestamp_sec=chunk_lines[-1][0],
            line_count=len(text_lines),
            has_timestamps=True,
        )
    )


def _split_plain_text_into_chunks(
    transcript_text: str,
    target_chars: int,
    min_chars: int,
    max_chars: int,
) -> list[TranscriptChunk]:
    target_chars = max(400, int(target_chars))
    min_chars = min(target_chars, max(200, int(min_chars)))
    max_chars = max(target_chars, int(max_chars))

    raw = (transcript_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ValueError("转录文本为空，无法进行分块总结。")

    segments = [line.strip() for line in raw.splitlines() if line.strip()]
    if not segments:
        segments = [raw]

    chunk_texts: list[str] = []
    current_lines: list[str] = []
    current_chars = 0

    def flush_current() -> None:
        nonlocal current_lines, current_chars
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if text:
            chunk_texts.append(text)
        current_lines = []
        current_chars = 0

    for segment in segments:
        for part in _split_long_plain_text_segment(segment, target_chars, max_chars):
            part = part.strip()
            if not part:
                continue
            part_len = len(part)
            would_len = current_chars + (1 if current_lines else 0) + part_len
            if current_lines and would_len > max_chars and current_chars >= min_chars:
                flush_current()
            elif current_lines and current_chars >= target_chars and current_chars >= min_chars:
                flush_current()

            current_lines.append(part)
            current_chars += (1 if len(current_lines) > 1 else 0) + part_len

            if current_chars >= max_chars:
                flush_current()

    flush_current()

    if len(chunk_texts) >= 2 and len(chunk_texts[-1]) < min_chars:
        chunk_texts[-2] = f"{chunk_texts[-2].rstrip()}\n{chunk_texts[-1].lstrip()}"
        chunk_texts.pop()

    chunks: list[TranscriptChunk] = []
    for idx, chunk_text in enumerate(chunk_texts):
        chunks.append(
            TranscriptChunk(
                index=idx,
                text=chunk_text,
                start_timestamp_sec=0,
                end_timestamp_sec=0,
                line_count=len([line for line in chunk_text.splitlines() if line.strip()]),
                has_timestamps=False,
                label=f"无时间戳文本块 {idx + 1}",
            )
        )
    return chunks


def _split_long_plain_text_segment(segment: str, target_chars: int, max_chars: int) -> list[str]:
    text = (segment or "").strip()
    if len(text) <= max_chars:
        return [text] if text else []

    pieces: list[str] = []
    start = 0
    while start < len(text):
        target_end = min(len(text), start + target_chars)
        hard_end = min(len(text), start + max_chars)
        if hard_end >= len(text):
            split_at = len(text)
        else:
            split_at = _find_plain_text_split(text, start, target_end, hard_end)
        if split_at <= start:
            split_at = hard_end
        piece = text[start:split_at].strip()
        if piece:
            pieces.append(piece)
        start = split_at
    return pieces


def _find_plain_text_split(text: str, start: int, target_end: int, hard_end: int) -> int:
    lower_bound = start + max(120, int((target_end - start) * 0.6))
    split_chars = "。！？!?；;，,、. "
    for idx in range(min(hard_end, len(text)) - 1, lower_bound - 1, -1):
        if text[idx] in split_chars:
            return idx + 1
    return target_end


def tail_timestamp_lines(chunk: TranscriptChunk, n: int) -> str:
    lines = chunk.text.splitlines()
    if n <= 0:
        return ""
    return "\n".join(lines[-n:])

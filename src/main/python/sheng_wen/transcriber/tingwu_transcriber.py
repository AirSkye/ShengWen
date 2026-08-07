from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Optional

from tingwu.tingwu_http_transcribe import (
    TingwuCancelledError,
    build_srt_cues,
    transcribe_media,
)

from .transcriber import (
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionResult,
    get_transcriber,
)


ProgressEventCallback = Callable[[dict[str, Any]], None]


def _safe_task_dir_name(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "").strip()).strip("-.")
    return normalized[:120] or uuid.uuid4().hex


def _overall_progress(event: dict[str, Any]) -> float:
    stage = str(event.get("stage") or "")
    raw_progress = event.get("progress", 0.0)
    try:
        stage_progress = max(0.0, min(float(raw_progress or 0.0), 1.0))
    except (TypeError, ValueError):
        stage_progress = 0.0

    if stage in {"creating", "created"}:
        return 0.02
    if stage in {"uploading", "upload_complete"}:
        return 0.05 + stage_progress * 0.40
    if stage == "syncing":
        return 0.48
    if stage == "polling":
        return 0.50 + stage_progress * 0.45
    if stage == "fetching_result":
        return 0.97
    if stage == "completed":
        return 1.0
    return 0.0


class TingwuTranscriber(Transcriber):
    """Adapter that exposes the project-local Tingwu client as a Transcriber."""

    def __init__(
        self,
        config_path: str,
        task_root: str,
        poll_interval_sec: float = 10.0,
        timeout_sec: float = 14400.0,
        fallback_to_whisper: bool = False,
        whisper_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.config_path = Path(config_path).expanduser().resolve()
        self.task_root = Path(task_root).expanduser().resolve()
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self.timeout_sec = max(60.0, float(timeout_sec))
        self.fallback_to_whisper = bool(fallback_to_whisper)
        self.whisper_kwargs = dict(whisper_kwargs or {})
        self._fallback_transcriber: Transcriber | None = None
        self._fallback_lock = Lock()

    def _get_fallback_transcriber(self) -> Transcriber:
        with self._fallback_lock:
            if self._fallback_transcriber is None:
                self._fallback_transcriber = get_transcriber("fast_whisper", **self.whisper_kwargs)
            return self._fallback_transcriber

    @staticmethod
    def _build_result(result: dict[str, Any]) -> TranscriptionResult:
        cues = build_srt_cues(result.get("parsedResult") or {"pg": []})
        segments = [
            {
                "start": max(0.0, float(cue.get("begin", 0)) / 1000.0),
                "end": max(0.0, float(cue.get("end", cue.get("begin", 0))) / 1000.0),
                "text": str(cue.get("text") or ""),
            }
            for cue in cues
            if str(cue.get("text") or "").strip()
        ]

        audio_duration = max((float(segment["end"]) for segment in segments), default=0.0)
        elapsed = max(0.0, float(result.get("elapsedSeconds") or 0.0))
        real_time_factor = elapsed / audio_duration if audio_duration > 0 else 0.0
        artifacts = {
            "provider": "tingwu",
            "remote_task_id": str(result.get("transId") or ""),
            "text_path": str(result.get("textPath") or ""),
            "subtitle_path": str(result.get("srtPath") or ""),
            "task_path": str(result.get("taskPath") or ""),
            "subtitle_count": int(result.get("subtitleCount") or 0),
        }
        return TranscriptionResult(
            segments=segments,
            transcription_time=elapsed,
            real_time_factor=real_time_factor,
            total_time=elapsed,
            model_load_time=0.0,
            audio_duration=audio_duration,
            language="zh",
            language_probability=1.0,
            provider="tingwu",
            artifacts=artifacts,
        )

    def transcribe_with_context(
        self,
        file_path: str,
        task_id: str | None = None,
        progress_callback: Optional[Callable[[float], None]] = None,
        event_callback: ProgressEventCallback | None = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> TranscriptionResult:
        media_path = Path(file_path).expanduser().resolve()
        task_name = _safe_task_dir_name(task_id or f"{media_path.stem}-{uuid.uuid4().hex[:8]}")
        task_dir = self.task_root / task_name

        def handle_event(event: dict[str, Any]):
            if event_callback:
                event_callback(dict(event))
            if progress_callback:
                progress_callback(_overall_progress(event))

        started_at = time.monotonic()
        try:
            result = transcribe_media(
                media_path,
                task_dir,
                config_path=self.config_path,
                poll_interval=self.poll_interval_sec,
                timeout=self.timeout_sec,
                event_callback=handle_event,
                cancel_check=cancel_check,
            )
            return self._build_result(result)
        except TingwuCancelledError as error:
            raise TranscriptionCancelled(str(error)) from error
        except Exception as error:
            if not self.fallback_to_whisper:
                raise TranscriptionError(f"听悟转写失败: {error}") from error

            fallback_event = {
                "stage": "fallback",
                "progress": 0.0,
                "message": f"听悟转写失败，正在回退本地 Whisper：{error}",
                "elapsedSeconds": round(time.monotonic() - started_at, 3),
            }
            if event_callback:
                event_callback(fallback_event)
            fallback = self._get_fallback_transcriber()
            fallback_result = fallback.transcribe(
                str(media_path),
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            fallback_result.provider = "fast_whisper_fallback"
            fallback_result.artifacts = {
                "provider": "fast_whisper_fallback",
                "tingwu_error": str(error),
            }
            return fallback_result

    def transcribe(
        self,
        file_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> TranscriptionResult:
        return self.transcribe_with_context(
            file_path=file_path,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.config import Settings, settings
from app.iaf import IafError, IafParameters, compute_iaf, trim_recording
from app.lsl import LslError, acquire_lsl_recording


TERMINAL_STATES = {"finished", "error"}


@dataclass
class Session:
    id: str
    subject_id: str
    age: int
    created_at: str
    status: str = "created"
    message: str = "Session created."
    stream: dict[str, Any] | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "subject_id": self.subject_id,
                "age": self.age,
                "created_at": self.created_at,
                "status": self.status,
                "message": self.message,
                "stream": self.stream,
                "progress": self.progress,
                "result": self.result,
                "error": self.error,
            }


class SessionManager:
    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def start(self, subject_id: str, age: int) -> Session:
        session = Session(
            id=uuid.uuid4().hex,
            subject_id=subject_id,
            age=age,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._sessions[session.id] = session
        loop = asyncio.get_running_loop()
        asyncio.create_task(asyncio.to_thread(self._run_worker, session, loop))
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def _run_worker(self, session: Session, loop: asyncio.AbstractEventLoop) -> None:
        try:
            self._publish(
                session,
                loop,
                status="connecting",
                message="正在连接 Curry9 LSL EEG 数据流。",
            )
            recording = acquire_lsl_recording(
                stream_type=self.settings.stream_type,
                stream_name=self.settings.stream_name,
                duration_seconds=self.settings.recording_seconds,
                resolve_timeout_seconds=self.settings.resolve_timeout_seconds,
                progress_callback=lambda event: self._handle_lsl_progress(session, loop, event),
            )
            raw = np.asarray(recording.samples, dtype=float).T
            trimmed = trim_recording(
                raw,
                recording.sampling_rate,
                self.settings.trim_start_seconds,
                self.settings.trim_end_seconds,
            )
            result = compute_iaf(
                trimmed,
                recording.sampling_rate,
                recording.channel_names,
                self.settings.target_channels,
                IafParameters(min_valid_channels=self.settings.min_valid_channels),
            )
            result.update(
                {
                    "subject_id": session.subject_id,
                    "age": session.age,
                    "recording_seconds": self.settings.recording_seconds,
                    "trim_start_seconds": self.settings.trim_start_seconds,
                    "trim_end_seconds": self.settings.trim_end_seconds,
                    "stream": {
                        "name": recording.stream_name,
                        "type": recording.stream_type,
                        "sampling_rate_hz": recording.sampling_rate,
                        "channels": list(recording.channel_names),
                    },
                    "computed_at": datetime.now(UTC).isoformat(),
                    "method_reference": "Corcoran et al. restingIAF-style Welch PSD, Savitzky-Golay smoothing, PAF and CoG estimation.",
                }
            )
            self._publish(
                session,
                loop,
                status="finished",
                message="IAF 计算完成。",
                result=result,
                progress={"elapsed_seconds": self.settings.recording_seconds, "remaining_seconds": 0},
            )
        except (IafError, LslError, ValueError) as exc:
            self._publish(session, loop, status="error", message=str(exc), error=str(exc))
        except Exception as exc:  # pragma: no cover - final guard for live acquisition
            self._publish(session, loop, status="error", message=f"Unexpected error: {exc}", error=str(exc))

    def _handle_lsl_progress(
        self,
        session: Session,
        loop: asyncio.AbstractEventLoop,
        event: dict[str, Any],
    ) -> None:
        phase = event.get("phase", session.status)
        status = "recording" if phase == "recording" else phase
        stream = None
        if "stream_name" in event:
            stream = {
                "name": event.get("stream_name"),
                "type": event.get("stream_type"),
                "sampling_rate_hz": event.get("sampling_rate_hz"),
                "channels": event.get("channel_names", []),
            }
        self._publish(
            session,
            loop,
            status=status,
            message=event.get("message", session.message),
            stream=stream,
            progress={key: value for key, value in event.items() if key not in {"phase", "message", "stream_name", "stream_type", "sampling_rate_hz", "channel_names"}},
        )

    def _publish(
        self,
        session: Session,
        loop: asyncio.AbstractEventLoop,
        *,
        status: str | None = None,
        message: str | None = None,
        stream: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with session.lock:
            if status is not None:
                session.status = status
            if message is not None:
                session.message = message
            if stream is not None:
                session.stream = stream
            if progress is not None:
                session.progress = progress
            if result is not None:
                session.result = result
            if error is not None:
                session.error = error
            event = {
                "id": session.id,
                "status": session.status,
                "message": session.message,
                "stream": session.stream,
                "progress": session.progress,
                "result": session.result,
                "error": session.error,
            }
        loop.call_soon_threadsafe(session.queue.put_nowait, event)


def format_sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n\n"


manager = SessionManager()

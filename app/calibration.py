from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import settings
from app.db import dumps_json, get_db, loads_json, new_id, utc_now
from app.eeg import compute_online_feature_windows, individualized_bands, summarize_baseline
from app.iaf import IafError, IafParameters, compute_iaf, trim_recording
from app.lsl import LslError, acquire_lsl_recording


TERMINAL_STATES = {"finished", "error"}


@dataclass
class CalibrationRun:
    id: str
    run_type: str
    session_id: str | None
    status: str = "created"
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "run_type": self.run_type,
                "session_id": self.session_id,
                "status": self.status,
                "message": self.message,
                "result": self.result,
                "error": self.error,
            }


class CalibrationManager:
    def __init__(self) -> None:
        self._runs: dict[str, CalibrationRun] = {}
        self._lock = threading.Lock()

    def start(self, run_type: str, session_id: str | None = None) -> CalibrationRun:
        if run_type not in {"eyes_open", "eyes_closed"}:
            raise ValueError("INVALID_CALIBRATION_TYPE")
        run = CalibrationRun(id=new_id(), run_type=run_type, session_id=session_id)
        with self._lock:
            self._runs[run.id] = run
        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO baseline_runs (id, session_id, run_type, status, created_at)
                VALUES (?, ?, ?, 'created', ?)
                """,
                (run.id, session_id, run_type, utc_now()),
            )
        loop = asyncio.get_running_loop()
        asyncio.create_task(asyncio.to_thread(self._worker, run, loop))
        return run

    def get(self, run_id: str) -> CalibrationRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def _worker(self, run: CalibrationRun, loop: asyncio.AbstractEventLoop) -> None:
        try:
            duration = settings.eyes_open_seconds if run.run_type == "eyes_open" else settings.eyes_closed_seconds
            self._publish(run, loop, status="connecting", message="正在搜索 Curry9 LSL EEG 数据流。")
            recording = acquire_lsl_recording(
                stream_type=settings.stream_type,
                stream_name=settings.stream_name,
                duration_seconds=duration,
                resolve_timeout_seconds=settings.resolve_timeout_seconds,
                progress_callback=lambda event: self._handle_progress(run, loop, event),
            )
            raw = np.asarray(recording.samples, dtype=float).T
            stream_json = {
                "name": recording.stream_name,
                "type": recording.stream_type,
                "sampling_rate_hz": recording.sampling_rate,
                "channels": list(recording.channel_names),
            }
            if run.run_type == "eyes_closed":
                trimmed = trim_recording(raw, recording.sampling_rate, settings.trim_start_seconds, settings.trim_end_seconds)
                result = compute_iaf(
                    trimmed,
                    recording.sampling_rate,
                    recording.channel_names,
                    settings.target_channels,
                    IafParameters(min_valid_channels=settings.min_valid_channels),
                )
                bands = individualized_bands(result.get("paf_hz"))
                result["bands"] = bands
                self._save_result(run, stream_json, result, bands=bands)
                self._publish(run, loop, status="finished", message="闭眼 IAF 校准完成。", result=result)
            else:
                session = _session_bands(run.session_id)
                windows = compute_online_feature_windows(
                    raw,
                    recording.sampling_rate,
                    recording.channel_names,
                    settings.posterior_channels,
                    settings.frontal_channels,
                    session["alpha_band"],
                    session["theta_band"],
                    None,
                )
                summary = summarize_baseline(windows)
                result = {"stream": stream_json, "summary": summary, "window_count": len(windows)}
                self._save_result(run, stream_json, result, baseline_summary=summary)
                self._publish(run, loop, status="finished", message="睁眼屏幕基线完成。", result=result)
        except (IafError, LslError, ValueError) as exc:
            self._save_error(run, str(exc))
            self._publish(run, loop, status="error", message=str(exc), error=str(exc))
        except Exception as exc:  # pragma: no cover
            self._save_error(run, str(exc))
            self._publish(run, loop, status="error", message=f"Unexpected error: {exc}", error=str(exc))

    def _handle_progress(self, run: CalibrationRun, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        phase = event.get("phase", run.status)
        self._publish(run, loop, status="recording" if phase == "recording" else phase, message=event.get("message", run.message), result=event)

    def _save_result(
        self,
        run: CalibrationRun,
        stream_json: dict,
        result: dict,
        *,
        bands: dict | None = None,
        baseline_summary: dict | None = None,
    ) -> None:
        conn = get_db()
        with conn:
            conn.execute(
                """
                UPDATE baseline_runs
                SET status = 'finished', stream_json = ?, summary_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (dumps_json(stream_json), dumps_json(result), utc_now(), run.id),
            )
            if run.run_type == "eyes_closed":
                conn.execute(
                    "INSERT INTO iaf_results (id, session_id, baseline_run_id, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (new_id(), run.session_id, run.id, dumps_json(result), utc_now()),
                )
                if run.session_id and bands:
                    conn.execute(
                        "UPDATE sessions SET alpha_band = ?, theta_band = ?, iaf_result = ?, updated_at = ? WHERE id = ?",
                        (dumps_json(bands["alpha"]), dumps_json(bands["theta"]), dumps_json(result), utc_now(), run.session_id),
                    )
            if run.run_type == "eyes_open" and run.session_id and baseline_summary:
                conn.execute(
                    "UPDATE sessions SET baseline_summary = ?, updated_at = ? WHERE id = ?",
                    (dumps_json(baseline_summary), utc_now(), run.session_id),
                )

    def _save_error(self, run: CalibrationRun, error: str) -> None:
        conn = get_db()
        with conn:
            conn.execute(
                "UPDATE baseline_runs SET status = 'error', error = ?, completed_at = ? WHERE id = ?",
                (error, utc_now(), run.id),
            )

    def _publish(
        self,
        run: CalibrationRun,
        loop: asyncio.AbstractEventLoop,
        *,
        status: str,
        message: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        with run.lock:
            run.status = status
            run.message = message
            if result is not None:
                run.result = result
            if error is not None:
                run.error = error
            event = run.snapshot()
        loop.call_soon_threadsafe(run.queue.put_nowait, event)


def format_sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n\n"


def _session_bands(session_id: str | None) -> dict:
    if not session_id:
        return {"alpha_band": [8.0, 12.0], "theta_band": [4.0, 8.0]}
    row = get_db().execute("SELECT alpha_band, theta_band FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return {"alpha_band": [8.0, 12.0], "theta_band": [4.0, 8.0]}
    return {
        "alpha_band": loads_json(row["alpha_band"], [8.0, 12.0]),
        "theta_band": loads_json(row["theta_band"], [4.0, 8.0]),
    }


manager = CalibrationManager()

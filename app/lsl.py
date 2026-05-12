from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


class LslError(RuntimeError):
    """Raised when LSL stream discovery or acquisition fails."""


@dataclass(frozen=True)
class LslRecording:
    samples: list[list[float]]
    timestamps: list[float]
    channel_names: tuple[str, ...]
    sampling_rate: float
    stream_name: str
    stream_type: str


def acquire_lsl_recording(
    stream_type: str,
    stream_name: str | None,
    duration_seconds: float,
    resolve_timeout_seconds: float,
    progress_callback,
) -> LslRecording:
    try:
        from pylsl import StreamInlet, resolve_byprop, resolve_streams
    except Exception as exc:  # pragma: no cover - depends on local LSL install
        raise LslError(
            "pylsl is not installed or cannot load liblsl. Run `python3 -m pip install -r requirements.txt`."
        ) from exc

    progress_callback({"phase": "connecting", "message": "正在搜索 Curry LSL EEG 数据流..."})
    streams = resolve_byprop("type", stream_type, timeout=resolve_timeout_seconds)
    if stream_name:
        streams = [stream for stream in streams if stream.name() == stream_name]
    if not streams:
        all_streams = resolve_streams(wait_time=1.0)
        names = ", ".join(f"{stream.name()} ({stream.type()})" for stream in all_streams) or "none"
        expected = f"type={stream_type}" + (f", name={stream_name}" if stream_name else "")
        raise LslError(f"No LSL stream found for {expected}. Visible streams: {names}.")

    stream = streams[0]
    inlet = StreamInlet(stream, max_buflen=max(1, int(duration_seconds) + 10))
    info = inlet.info(timeout=resolve_timeout_seconds)
    sampling_rate = float(info.nominal_srate())
    if sampling_rate <= 0:
        raise LslError("The selected LSL stream does not report a nominal sampling rate.")

    channel_names = _channel_names_from_info(info)
    if not channel_names:
        raise LslError("The LSL stream does not expose channel labels; target IAF channels cannot be matched.")

    progress_callback(
        {
            "phase": "recording",
            "message": "已连接 LSL 数据流，开始闭眼静息采集。",
            "stream_name": stream.name(),
            "stream_type": stream.type(),
            "sampling_rate_hz": sampling_rate,
            "channel_names": list(channel_names),
        }
    )

    samples: list[list[float]] = []
    timestamps: list[float] = []
    started_at = time.monotonic()
    next_update = started_at
    max_samples = max(1, int(sampling_rate / 2))

    while True:
        elapsed = time.monotonic() - started_at
        if elapsed >= duration_seconds:
            break
        chunk, times = inlet.pull_chunk(timeout=0.5, max_samples=max_samples)
        if chunk:
            samples.extend(chunk)
            timestamps.extend(times)
        now = time.monotonic()
        if now >= next_update:
            progress_callback(
                {
                    "phase": "recording",
                    "elapsed_seconds": min(duration_seconds, elapsed),
                    "remaining_seconds": max(0.0, duration_seconds - elapsed),
                    "sample_count": len(samples),
                }
            )
            next_update = now + 0.5

    if not samples:
        raise LslError("No samples were received from the LSL stream.")

    progress_callback(
        {
            "phase": "processing",
            "message": "采集完成，正在去除首尾 4 秒并计算 IAF。",
            "sample_count": len(samples),
        }
    )
    return LslRecording(
        samples=samples,
        timestamps=timestamps,
        channel_names=channel_names,
        sampling_rate=sampling_rate,
        stream_name=stream.name(),
        stream_type=stream.type(),
    )


def _channel_names_from_info(info: Any) -> tuple[str, ...]:
    labels: list[str] = []
    channels = info.desc().child("channels")
    channel = channels.child("channel")
    while channel and channel.name() == "channel":
        label = channel.child_value("label") or channel.child_value("name")
        if label:
            labels.append(label)
        channel = channel.next_sibling()
    return tuple(labels)

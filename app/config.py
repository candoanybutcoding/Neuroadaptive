from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_TARGET_CHANNELS = ("P3", "Pz", "PO3", "POz", "PO4", "O1", "O2")
DEFAULT_POSTERIOR_CHANNELS = ("Pz", "PO3", "PO4", "O1", "O2")
DEFAULT_FRONTAL_CHANNELS = ("Fz", "FCz", "AFz")
DEFAULT_EOG_CHANNELS = ("VEOG", "HEOG", "EOG")


def _parse_channels(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_TARGET_CHANNELS
    channels = tuple(part.strip() for part in value.split(",") if part.strip())
    return channels or DEFAULT_TARGET_CHANNELS


@dataclass(frozen=True)
class Settings:
    database_path: str = os.getenv("NEUROADAPTIVE_DB_PATH", "data/experiment.db")
    stream_type: str = os.getenv("NEUROADAPTIVE_LSL_TYPE", "EEG")
    stream_name: str | None = os.getenv("NEUROADAPTIVE_LSL_NAME") or None
    resolve_timeout_seconds: float = float(os.getenv("NEUROADAPTIVE_LSL_TIMEOUT", "10"))
    recording_seconds: float = float(os.getenv("NEUROADAPTIVE_RECORDING_SECONDS", "120"))
    eyes_open_seconds: float = float(os.getenv("NEUROADAPTIVE_EYES_OPEN_SECONDS", "120"))
    eyes_closed_seconds: float = float(os.getenv("NEUROADAPTIVE_EYES_CLOSED_SECONDS", "120"))
    trim_start_seconds: float = float(os.getenv("NEUROADAPTIVE_TRIM_START_SECONDS", "4"))
    trim_end_seconds: float = float(os.getenv("NEUROADAPTIVE_TRIM_END_SECONDS", "4"))
    target_channels: tuple[str, ...] = _parse_channels(os.getenv("NEUROADAPTIVE_TARGET_CHANNELS"))
    posterior_channels: tuple[str, ...] = _parse_channels(
        os.getenv("NEUROADAPTIVE_POSTERIOR_CHANNELS")
    ) if os.getenv("NEUROADAPTIVE_POSTERIOR_CHANNELS") else DEFAULT_POSTERIOR_CHANNELS
    frontal_channels: tuple[str, ...] = _parse_channels(
        os.getenv("NEUROADAPTIVE_FRONTAL_CHANNELS")
    ) if os.getenv("NEUROADAPTIVE_FRONTAL_CHANNELS") else DEFAULT_FRONTAL_CHANNELS
    eog_channels: tuple[str, ...] = _parse_channels(
        os.getenv("NEUROADAPTIVE_EOG_CHANNELS")
    ) if os.getenv("NEUROADAPTIVE_EOG_CHANNELS") else DEFAULT_EOG_CHANNELS
    min_valid_channels: int = int(os.getenv("NEUROADAPTIVE_MIN_VALID_CHANNELS", "2"))
    save_raw_eeg: bool = os.getenv("NEUROADAPTIVE_SAVE_RAW_EEG", "0") == "1"
    mains_frequency_hz: float = float(os.getenv("NEUROADAPTIVE_MAINS_FREQUENCY_HZ", "50"))
    controller_mode: str = os.getenv("NEUROADAPTIVE_CONTROLLER_MODE", "simulation")


settings = Settings()

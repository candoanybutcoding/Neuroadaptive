from __future__ import annotations

import numpy as np
import pytest

from app.config import DEFAULT_TARGET_CHANNELS
from app.iaf import IafError, compute_iaf, trim_recording


def synthetic_alpha(
    frequency_hz: float = 10.0,
    sampling_rate: float = 250.0,
    duration_seconds: float = 24.0,
    channels: int = 7,
) -> np.ndarray:
    rng = np.random.default_rng(42)
    times = np.arange(0, duration_seconds, 1 / sampling_rate)
    data = []
    for idx in range(channels):
        phase = idx * 0.17
        alpha = np.sin(2 * np.pi * frequency_hz * times + phase)
        noise = 0.18 * rng.standard_normal(times.size)
        slow = 0.08 * np.sin(2 * np.pi * 2.0 * times)
        data.append(alpha + noise + slow)
    return np.asarray(data)


def test_10hz_alpha_returns_paf_near_10hz() -> None:
    sampling_rate = 250.0
    data = synthetic_alpha(sampling_rate=sampling_rate)
    result = compute_iaf(data, sampling_rate, DEFAULT_TARGET_CHANNELS)

    assert result["paf_hz"] == pytest.approx(10.0, abs=0.35)
    assert result["cog_hz"] == pytest.approx(10.0, abs=0.6)
    assert result["valid_peak_channels"] >= 2
    assert result["valid_band_channels"] >= 2


def test_trim_recording_removes_first_and_last_four_seconds() -> None:
    sampling_rate = 10.0
    data = np.zeros((2, 200))
    trimmed = trim_recording(data, sampling_rate, 4.0, 4.0)

    assert trimmed.shape == (2, 120)


def test_missing_target_channels_raises_clear_error() -> None:
    sampling_rate = 250.0
    data = synthetic_alpha(sampling_rate=sampling_rate)
    with pytest.raises(IafError, match="Missing target channels"):
        compute_iaf(data, sampling_rate, ("P3", "Pz", "PO3", "POz", "PO4", "O1", "Missing"))


def test_sampling_rate_must_support_analysis_band() -> None:
    data = np.zeros((7, 1000))
    with pytest.raises(IafError, match="too low"):
        compute_iaf(data, 60.0, DEFAULT_TARGET_CHANNELS)

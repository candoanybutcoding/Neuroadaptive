from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import signal

from app.iaf import normalize_channel_name


@dataclass(frozen=True)
class EegProcessingConfig:
    mains_frequency_hz: float = 50.0
    bandpass_low_hz: float = 1.0
    bandpass_high_hz: float = 40.0
    epoch_seconds: float = 2.0
    epoch_overlap_fraction: float = 0.5
    absolute_threshold_uv: float = 100.0
    peak_to_peak_threshold_uv: float = 150.0
    muscle_band_hz: tuple[float, float] = (30.0, 40.0)
    muscle_mad_threshold: float = 3.0


def individualized_bands(paf_hz: float | None) -> dict[str, list[float]]:
    if paf_hz is None or not np.isfinite(paf_hz):
        return {"alpha": [8.0, 12.0], "theta": [4.0, 8.0], "source": "fixed_fallback"}
    alpha_low = max(1.0, float(paf_hz) - 2.0)
    alpha_high = float(paf_hz) + 2.0
    theta_high = min(8.0, alpha_low)
    theta_low = max(4.0, theta_high - 4.0)
    if theta_low >= theta_high:
        theta_low, theta_high = 4.0, 8.0
    return {"alpha": [alpha_low, alpha_high], "theta": [theta_low, theta_high], "source": "iaf"}


def preprocess_eeg(data: np.ndarray, sampling_rate: float, config: EegProcessingConfig | None = None) -> np.ndarray:
    cfg = config or EegProcessingConfig()
    eeg = np.asarray(data, dtype=float)
    if eeg.ndim != 2:
        raise ValueError("EEG data must be channels x samples.")
    reref = eeg - np.nanmean(eeg, axis=0, keepdims=True)
    nyquist = sampling_rate / 2.0
    notch_freq = cfg.mains_frequency_hz
    if 0 < notch_freq < nyquist:
        b_notch, a_notch = signal.iirnotch(notch_freq, Q=30, fs=sampling_rate)
        reref = signal.filtfilt(b_notch, a_notch, reref, axis=-1)
    high = min(cfg.bandpass_high_hz, nyquist - 1)
    if cfg.bandpass_low_hz < high:
        sos = signal.butter(4, [cfg.bandpass_low_hz, high], btype="bandpass", fs=sampling_rate, output="sos")
        reref = signal.sosfiltfilt(sos, reref, axis=-1)
    return reref


def compute_online_feature_windows(
    data: np.ndarray,
    sampling_rate: float,
    channel_names: Iterable[str],
    posterior_channels: Iterable[str],
    frontal_channels: Iterable[str],
    alpha_band: Iterable[float],
    theta_band: Iterable[float],
    baseline_summary: dict | None = None,
    config: EegProcessingConfig | None = None,
) -> list[dict]:
    cfg = config or EegProcessingConfig()
    raw = np.asarray(data, dtype=float)
    eeg = preprocess_eeg(raw, sampling_rate, cfg)
    names = tuple(channel_names)
    posterior_idx = _resolve_indices(names, posterior_channels)
    frontal_idx = _resolve_indices(names, frontal_channels)
    if not posterior_idx:
        raise ValueError("No posterior channels available for alpha feature extraction.")
    if not frontal_idx:
        raise ValueError("No frontal channels available for theta feature extraction.")

    epoch_samples = int(round(cfg.epoch_seconds * sampling_rate))
    step_samples = max(1, int(round(epoch_samples * (1 - cfg.epoch_overlap_fraction))))
    if eeg.shape[1] < epoch_samples:
        raise ValueError("Not enough samples for a 2 second EEG epoch.")

    windows: list[dict] = []
    muscle_baseline = (baseline_summary or {}).get("muscle_median")
    muscle_mad = (baseline_summary or {}).get("muscle_mad") or 0.0

    for start in range(0, eeg.shape[1] - epoch_samples + 1, step_samples):
        stop = start + epoch_samples
        epoch = eeg[:, start:stop]
        raw_epoch = raw[:, start:stop]
        valid, artifact_reasons, muscle_power = _epoch_valid(raw_epoch, sampling_rate, cfg, muscle_baseline, muscle_mad)
        alpha = _log_band_power(epoch[posterior_idx, :], sampling_rate, tuple(alpha_band)) if valid else None
        theta = _log_band_power(epoch[frontal_idx, :], sampling_rate, tuple(theta_band)) if valid else None
        windows.append(
            {
                "window_start_seconds": start / sampling_rate,
                "window_end_seconds": stop / sampling_rate,
                "alpha_log_power": alpha,
                "theta_log_power": theta,
                "muscle_power": muscle_power,
                "usable_epoch_ratio": 1.0 if valid else 0.0,
                "rejected_epoch_ratio": 0.0 if valid else 1.0,
                "valid": valid,
                "artifact_reasons": artifact_reasons,
            }
        )
    return windows


def summarize_baseline(windows: list[dict]) -> dict:
    valid = [window for window in windows if window["valid"]]
    alpha = np.asarray([window["alpha_log_power"] for window in valid if window["alpha_log_power"] is not None])
    theta = np.asarray([window["theta_log_power"] for window in valid if window["theta_log_power"] is not None])
    muscle = np.asarray([window["muscle_power"] for window in windows if window.get("muscle_power") is not None])
    return {
        "valid_epoch_count": len(valid),
        "total_epoch_count": len(windows),
        "usable_epoch_ratio": len(valid) / len(windows) if windows else 0.0,
        "alpha_mean": _finite_float(np.nanmean(alpha)) if alpha.size else None,
        "alpha_sd": _finite_float(np.nanstd(alpha, ddof=1)) if alpha.size > 1 else 0.0,
        "theta_mean": _finite_float(np.nanmean(theta)) if theta.size else None,
        "theta_sd": _finite_float(np.nanstd(theta, ddof=1)) if theta.size > 1 else 0.0,
        "muscle_median": _finite_float(np.nanmedian(muscle)) if muscle.size else None,
        "muscle_mad": _finite_float(np.nanmedian(np.abs(muscle - np.nanmedian(muscle)))) if muscle.size else 0.0,
    }


def normalize_window_features(windows: list[dict], baseline_summary: dict | None) -> list[dict]:
    if not baseline_summary:
        return windows
    alpha_mean = baseline_summary.get("alpha_mean")
    theta_mean = baseline_summary.get("theta_mean")
    normalized = []
    for window in windows:
        item = dict(window)
        item["alpha_norm"] = (
            item["alpha_log_power"] - alpha_mean
            if item.get("alpha_log_power") is not None and alpha_mean is not None
            else None
        )
        item["theta_norm"] = (
            item["theta_log_power"] - theta_mean
            if item.get("theta_log_power") is not None and theta_mean is not None
            else None
        )
        normalized.append(item)
    return normalized


def detect_joint_decrease_trigger(
    windows: list[dict],
    *,
    initiation_seconds: float = 10.0,
    comparison_window_seconds: float = 10.0,
    threshold: float = 0.15,
    ideation_seconds: float = 60.0,
) -> dict:
    comparisons = []
    time = initiation_seconds + comparison_window_seconds
    while time <= ideation_seconds:
        prev_start = time - 2 * comparison_window_seconds
        prev_end = time - comparison_window_seconds
        later_start = time - comparison_window_seconds
        later_end = time
        prev = _aggregate_feature_window(windows, prev_start, prev_end)
        later = _aggregate_feature_window(windows, later_start, later_end)
        valid = prev["valid"] and later["valid"]
        alpha_change = _relative_change(prev["alpha"], later["alpha"]) if valid else None
        theta_change = _relative_change(prev["theta"], later["theta"]) if valid else None
        triggered = (
            valid
            and alpha_change is not None
            and theta_change is not None
            and alpha_change <= -threshold
            and theta_change <= -threshold
        )
        comparison = {
            "previous_window": [prev_start, prev_end],
            "later_window": [later_start, later_end],
            "valid": valid,
            "alpha_change": alpha_change,
            "theta_change": theta_change,
            "triggered": triggered,
        }
        comparisons.append(comparison)
        if triggered:
            return {
                "display_suggestion": True,
                "display_time_seconds": later_end,
                "decision_source": "joint_alpha_theta_decrease",
                "comparisons": comparisons,
                "eeg_valid": True,
                "alpha_change": alpha_change,
                "theta_change": theta_change,
            }
        time += comparison_window_seconds
    any_valid = any(item["valid"] for item in comparisons)
    return {
        "display_suggestion": False,
        "display_time_seconds": None,
        "decision_source": "no_joint_decrease" if any_valid else "invalid_controller_evidence",
        "comparisons": comparisons,
        "eeg_valid": any_valid,
        "alpha_change": None,
        "theta_change": None,
    }


def _resolve_indices(names: tuple[str, ...], wanted: Iterable[str]) -> list[int]:
    lookup = {normalize_channel_name(name): index for index, name in enumerate(names)}
    return [lookup[normalize_channel_name(name)] for name in wanted if normalize_channel_name(name) in lookup]


def _epoch_valid(
    epoch: np.ndarray,
    sampling_rate: float,
    cfg: EegProcessingConfig,
    muscle_baseline: float | None,
    muscle_mad: float,
) -> tuple[bool, list[str], float]:
    reasons: list[str] = []
    if np.nanmax(np.abs(epoch)) > cfg.absolute_threshold_uv:
        reasons.append("absolute_amplitude")
    if np.nanmax(np.ptp(epoch, axis=1)) > cfg.peak_to_peak_threshold_uv:
        reasons.append("peak_to_peak")
    muscle_power = _log_band_power(epoch, sampling_rate, cfg.muscle_band_hz)
    if muscle_baseline is not None and muscle_power > muscle_baseline + cfg.muscle_mad_threshold * muscle_mad:
        reasons.append("muscle_band")
    return not reasons, reasons, muscle_power


def _log_band_power(data: np.ndarray, sampling_rate: float, band: tuple[float, float]) -> float:
    nperseg = min(data.shape[-1], int(round(2.0 * sampling_rate)))
    freqs, pxx = signal.welch(data, fs=sampling_rate, window="hamming", nperseg=nperseg, axis=-1)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not np.any(mask):
        return float("nan")
    power = np.trapezoid(pxx[..., mask], freqs[mask], axis=-1)
    return float(np.log(np.nanmean(power) + np.finfo(float).tiny))


def _aggregate_feature_window(windows: list[dict], start: float, end: float) -> dict:
    selected = [
        item
        for item in windows
        if item["valid"] and item["window_start_seconds"] >= start and item["window_end_seconds"] <= end
    ]
    if not selected:
        return {"valid": False, "alpha": None, "theta": None}
    alpha_values = np.asarray([
        item.get("alpha_norm", item.get("alpha_log_power")) for item in selected
        if item.get("alpha_norm", item.get("alpha_log_power")) is not None
    ])
    theta_values = np.asarray([
        item.get("theta_norm", item.get("theta_log_power")) for item in selected
        if item.get("theta_norm", item.get("theta_log_power")) is not None
    ])
    if not alpha_values.size or not theta_values.size:
        return {"valid": False, "alpha": None, "theta": None}
    return {"valid": True, "alpha": float(np.nanmean(alpha_values)), "theta": float(np.nanmean(theta_values))}


def _relative_change(previous: float, later: float) -> float | None:
    if previous == 0 or previous is None or later is None:
        return None
    return float((later - previous) / abs(previous))


def _finite_float(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import signal

from app.config import DEFAULT_TARGET_CHANNELS


class IafError(ValueError):
    """Raised when a recording cannot produce a valid IAF estimate."""


@dataclass(frozen=True)
class IafParameters:
    analysis_frequency_range: tuple[float, float] = (1.0, 40.0)
    alpha_search_window: tuple[float, float] = (7.0, 13.0)
    welch_window_seconds: float = 4.0
    welch_overlap_fraction: float = 0.5
    min_power_sd: float = 1.0
    min_peak_difference: float = 0.20
    savgol_window: int = 11
    savgol_polyorder: int = 5
    min_valid_channels: int = 2


def normalize_channel_name(name: str) -> str:
    return "".join(name.strip().upper().split())


def trim_recording(
    data: np.ndarray,
    sampling_rate: float,
    trim_start_seconds: float = 4.0,
    trim_end_seconds: float = 4.0,
) -> np.ndarray:
    samples = np.asarray(data)
    if samples.ndim != 2:
        raise IafError("EEG data must be a 2D array shaped channels x samples.")
    if sampling_rate <= 0:
        raise IafError("Sampling rate must be positive.")

    start = int(round(trim_start_seconds * sampling_rate))
    end_trim = int(round(trim_end_seconds * sampling_rate))
    end = samples.shape[1] - end_trim if end_trim else samples.shape[1]
    if start >= end:
        raise IafError("Recording is too short after trimming the first and last 4 seconds.")
    return samples[:, start:end]


def compute_iaf(
    data: np.ndarray,
    sampling_rate: float,
    channel_names: Iterable[str],
    target_channels: Iterable[str] = DEFAULT_TARGET_CHANNELS,
    parameters: IafParameters | None = None,
) -> dict:
    params = parameters or IafParameters()
    eeg = np.asarray(data, dtype=float)
    names = tuple(channel_names)
    targets = tuple(target_channels)

    _validate_inputs(eeg, sampling_rate, names, params)
    target_indices, missing = _resolve_target_channels(names, targets)
    if missing:
        available = ", ".join(names) if names else "none"
        raise IafError(
            "Missing target channels: "
            + ", ".join(missing)
            + f". Available LSL channels: {available}."
        )

    selected = eeg[target_indices, :]
    freqs, psd = _welch_psd(selected, sampling_rate, params)
    channel_estimates = []
    smoothed_spectra = []

    for name, channel_psd in zip(targets, psd, strict=True):
        estimate = _estimate_channel(freqs, channel_psd, params)
        estimate["channel"] = name
        channel_estimates.append(estimate)
        smoothed_spectra.append(estimate.pop("_smoothed"))

    smoothed = np.asarray(smoothed_spectra)
    valid_bounds = [item for item in channel_estimates if item["alpha_low_hz"] is not None]
    valid_peaks = [item for item in channel_estimates if item["paf_hz"] is not None]

    result: dict[str, object] = {
        "sampling_rate_hz": float(sampling_rate),
        "samples_used": int(selected.shape[1]),
        "duration_used_seconds": float(selected.shape[1] / sampling_rate),
        "target_channels": list(targets),
        "parameters": {
            "analysis_frequency_range_hz": list(params.analysis_frequency_range),
            "alpha_search_window_hz": list(params.alpha_search_window),
            "welch_window_seconds": params.welch_window_seconds,
            "welch_overlap_fraction": params.welch_overlap_fraction,
            "savgol_window": params.savgol_window,
            "savgol_polyorder": params.savgol_polyorder,
            "min_valid_channels": params.min_valid_channels,
        },
        "channel_estimates": channel_estimates,
        "valid_peak_channels": len(valid_peaks),
        "valid_band_channels": len(valid_bounds),
        "paf_hz": None,
        "paf_std_hz": None,
        "cog_hz": None,
        "cog_std_hz": None,
        "alpha_window_hz": None,
        "frequency_bins_hz": freqs.tolist(),
    }

    if len(valid_bounds) >= params.min_valid_channels:
        alpha_low = _nearest_frequency_index(
            freqs, np.nanmean([item["alpha_low_hz"] for item in valid_bounds])
        )
        alpha_high = _nearest_frequency_index(
            freqs, np.nanmean([item["alpha_high_hz"] for item in valid_bounds])
        )
        if alpha_low > alpha_high:
            alpha_low, alpha_high = alpha_high, alpha_low
        cogs = _channel_cogs(freqs, smoothed, alpha_low, alpha_high)
        for item, cog in zip(channel_estimates, cogs, strict=True):
            item["cog_hz"] = _finite_or_none(cog)
        finite_cogs = np.asarray([value for value in cogs if np.isfinite(value)])
        if finite_cogs.size >= params.min_valid_channels:
            result["cog_hz"] = float(np.nanmean(finite_cogs))
            result["cog_std_hz"] = float(np.nanstd(finite_cogs, ddof=1)) if finite_cogs.size > 1 else 0.0
            result["alpha_window_hz"] = [float(freqs[alpha_low]), float(freqs[alpha_high])]
    else:
        for item in channel_estimates:
            item["cog_hz"] = None

    if len(valid_peaks) >= params.min_valid_channels:
        peaks = np.asarray([item["paf_hz"] for item in valid_peaks], dtype=float)
        weights = np.asarray([item["peak_quality"] for item in valid_peaks], dtype=float)
        if np.nanmax(weights) > 0:
            weights = weights / np.nanmax(weights)
        else:
            weights = np.ones_like(peaks)
        result["paf_hz"] = float(np.nansum(peaks * weights) / np.nansum(weights))
        result["paf_std_hz"] = float(np.nanstd(peaks, ddof=1)) if peaks.size > 1 else 0.0

    if result["paf_hz"] is None and result["cog_hz"] is None:
        raise IafError("No reliable IAF estimate: too few target channels had a valid alpha peak or band.")

    return result


def _validate_inputs(
    data: np.ndarray,
    sampling_rate: float,
    channel_names: tuple[str, ...],
    params: IafParameters,
) -> None:
    if data.ndim != 2:
        raise IafError("EEG data must be a 2D array shaped channels x samples.")
    if data.shape[0] != len(channel_names):
        raise IafError("Channel name count does not match EEG channel count.")
    if sampling_rate <= 2 * params.analysis_frequency_range[1]:
        raise IafError(
            f"Sampling rate {sampling_rate:g} Hz is too low for "
            f"{params.analysis_frequency_range[1]:g} Hz analysis."
        )
    required_samples = int(round(params.welch_window_seconds * sampling_rate))
    if data.shape[1] < required_samples:
        raise IafError(
            f"Recording has {data.shape[1]} samples; at least {required_samples} are required."
        )
    if not np.isfinite(data).all():
        raise IafError("Recording contains NaN or infinite values.")


def _resolve_target_channels(
    channel_names: tuple[str, ...],
    target_channels: tuple[str, ...],
) -> tuple[list[int], list[str]]:
    lookup = {normalize_channel_name(name): idx for idx, name in enumerate(channel_names)}
    indices: list[int] = []
    missing: list[str] = []
    for target in target_channels:
        key = normalize_channel_name(target)
        if key in lookup:
            indices.append(lookup[key])
        else:
            missing.append(target)
    return indices, missing


def _welch_psd(
    data: np.ndarray,
    sampling_rate: float,
    params: IafParameters,
) -> tuple[np.ndarray, np.ndarray]:
    window_samples = int(round(params.welch_window_seconds * sampling_rate))
    overlap = int(round(window_samples * params.welch_overlap_fraction))
    nfft = 1 << (window_samples - 1).bit_length()
    freqs, pxx = signal.welch(
        data,
        fs=sampling_rate,
        window="hamming",
        nperseg=window_samples,
        noverlap=overlap,
        nfft=nfft,
        axis=-1,
        detrend="constant",
        scaling="density",
    )
    low, high = params.analysis_frequency_range
    mask = (freqs >= low) & (freqs <= high)
    freqs = freqs[mask]
    pxx = pxx[:, mask]
    means = np.nanmean(pxx, axis=1, keepdims=True)
    means[means == 0] = 1.0
    return freqs, pxx / means


def _estimate_channel(freqs: np.ndarray, pxx: np.ndarray, params: IafParameters) -> dict:
    smoothed = _smooth_spectrum(pxx, params)
    d1 = _smooth_spectrum(pxx, params, derivative=1, delta=_frequency_delta(freqs))
    d2 = _smooth_spectrum(pxx, params, derivative=2, delta=_frequency_delta(freqs))
    min_power = _minimum_power_threshold(freqs, pxx, params.min_power_sd)
    alpha_low, alpha_high = params.alpha_search_window
    alpha_mask = (freqs >= alpha_low) & (freqs <= alpha_high)
    alpha_indices = np.flatnonzero(alpha_mask)

    peaks, _ = signal.find_peaks(smoothed[alpha_indices])
    peak_indices = alpha_indices[peaks]
    peak_indices = np.asarray(
        [idx for idx in peak_indices if np.log10(max(smoothed[idx], np.finfo(float).tiny)) > min_power[idx]],
        dtype=int,
    )

    if peak_indices.size == 0:
        return _empty_channel(smoothed)

    sorted_peaks = peak_indices[np.argsort(smoothed[peak_indices])[::-1]]
    primary = int(sorted_peaks[0])
    paf_hz: float | None = float(freqs[primary])
    if sorted_peaks.size > 1:
        second = int(sorted_peaks[1])
        if smoothed[primary] * (1.0 - params.min_peak_difference) <= smoothed[second]:
            paf_hz = None

    left_idx, right_idx = _alpha_bounds(smoothed, d1, freqs, primary, params.alpha_search_window)
    peak_quality = _peak_quality(freqs, smoothed, d2, primary, left_idx, right_idx)
    return {
        "_smoothed": smoothed,
        "paf_hz": paf_hz,
        "cog_hz": None,
        "alpha_low_hz": float(freqs[left_idx]),
        "alpha_high_hz": float(freqs[right_idx]),
        "peak_quality": float(peak_quality),
        "peak_power": float(smoothed[primary]),
    }


def _smooth_spectrum(
    values: np.ndarray,
    params: IafParameters,
    derivative: int = 0,
    delta: float = 1.0,
) -> np.ndarray:
    window = min(params.savgol_window, values.size if values.size % 2 else values.size - 1)
    min_window = params.savgol_polyorder + 2
    if min_window % 2 == 0:
        min_window += 1
    if window < min_window:
        window = min_window
    if window > values.size:
        window = values.size if values.size % 2 else values.size - 1
    if window <= params.savgol_polyorder:
        return values.copy()
    return signal.savgol_filter(
        values,
        window_length=window,
        polyorder=min(params.savgol_polyorder, window - 1),
        deriv=derivative,
        delta=delta,
        mode="interp",
    )


def _minimum_power_threshold(freqs: np.ndarray, pxx: np.ndarray, sd_multiplier: float) -> np.ndarray:
    safe_power = np.maximum(pxx, np.finfo(float).tiny)
    log_power = np.log10(safe_power)
    slope, intercept = np.polyfit(freqs, log_power, 1)
    fitted = slope * freqs + intercept
    residual_sd = np.std(log_power - fitted, ddof=1)
    return fitted + sd_multiplier * residual_sd


def _alpha_bounds(
    smoothed: np.ndarray,
    d1: np.ndarray,
    freqs: np.ndarray,
    peak_index: int,
    alpha_window: tuple[float, float],
) -> tuple[int, int]:
    minima, _ = signal.find_peaks(-smoothed)
    left_candidates = minima[minima < peak_index]
    right_candidates = minima[minima > peak_index]

    lower_default = _nearest_frequency_index(freqs, alpha_window[0])
    upper_default = _nearest_frequency_index(freqs, alpha_window[1])
    left = int(left_candidates[-1]) if left_candidates.size else lower_default
    right = int(right_candidates[0]) if right_candidates.size else upper_default

    if left >= peak_index:
        left = lower_default
    if right <= peak_index:
        right = upper_default
    left = max(0, min(left, peak_index - 1))
    right = min(freqs.size - 1, max(right, peak_index + 1))

    left = _nearest_shallow_or_minimum(d1, left, peak_index, direction=-1)
    right = _nearest_shallow_or_minimum(d1, peak_index, right, direction=1)
    return left, right


def _nearest_shallow_or_minimum(d1: np.ndarray, start: int, stop: int, direction: int) -> int:
    segment = range(stop - 1, start, -1) if direction < 0 else range(start + 1, stop)
    for idx in segment:
        if idx + 1 >= d1.size:
            continue
        if np.sign(d1[idx]) < np.sign(d1[idx + 1]) or abs(d1[idx]) < 1.0:
            return idx
    return start if direction < 0 else stop


def _peak_quality(
    freqs: np.ndarray,
    smoothed: np.ndarray,
    d2: np.ndarray,
    peak_index: int,
    left_bound: int,
    right_bound: int,
) -> float:
    left = left_bound
    for idx in range(peak_index - 1, left_bound, -1):
        if np.sign(d2[idx]) > np.sign(d2[idx + 1]):
            left = idx
            break

    right = right_bound
    for idx in range(peak_index + 1, right_bound):
        if np.sign(d2[idx]) < np.sign(d2[idx + 1]):
            right = idx
            break

    if right <= left:
        left, right = left_bound, right_bound
    area = np.trapezoid(smoothed[left : right + 1], freqs[left : right + 1])
    width = max(freqs[right] - freqs[left], _frequency_delta(freqs))
    return max(float(area / width), 0.0)


def _channel_cogs(
    freqs: np.ndarray,
    smoothed: np.ndarray,
    alpha_low_index: int,
    alpha_high_index: int,
) -> list[float]:
    cogs: list[float] = []
    band_freqs = freqs[alpha_low_index : alpha_high_index + 1]
    for spectrum in smoothed:
        band_power = spectrum[alpha_low_index : alpha_high_index + 1]
        total_power = np.sum(band_power)
        if total_power <= 0 or not np.isfinite(total_power):
            cogs.append(float("nan"))
        else:
            cogs.append(float(np.sum(band_power * band_freqs) / total_power))
    return cogs


def _empty_channel(smoothed: np.ndarray) -> dict:
    return {
        "_smoothed": smoothed,
        "paf_hz": None,
        "cog_hz": None,
        "alpha_low_hz": None,
        "alpha_high_hz": None,
        "peak_quality": 0.0,
        "peak_power": None,
    }


def _nearest_frequency_index(freqs: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(freqs - value)))


def _frequency_delta(freqs: np.ndarray) -> float:
    if freqs.size < 2:
        return 1.0
    return float(np.median(np.diff(freqs)))


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None

from __future__ import annotations

import numpy as np

from app.controller import simulated_neuroadaptive_decision, yoked_sham_decision
from app.eeg import compute_online_feature_windows, detect_joint_decrease_trigger, individualized_bands


def test_individualized_bands_from_iaf() -> None:
    bands = individualized_bands(10.0)

    assert bands["alpha"] == [8.0, 12.0]
    assert bands["theta"] == [4.0, 8.0]


def test_joint_decrease_controller_triggers_at_later_window_end() -> None:
    windows = []
    for start in range(0, 40, 2):
        if start < 10:
            alpha = theta = 1.0
        elif start < 20:
            alpha = theta = 0.8
        else:
            alpha = theta = 0.8
        windows.append(
            {
                "window_start_seconds": float(start),
                "window_end_seconds": float(start + 2),
                "alpha_log_power": alpha,
                "theta_log_power": theta,
                "valid": True,
            }
        )

    decision = detect_joint_decrease_trigger(windows, ideation_seconds=40)

    assert decision["display_suggestion"]
    assert decision["display_time_seconds"] == 20


def test_simulated_and_yoked_decisions_are_deterministic() -> None:
    first = simulated_neuroadaptive_decision("P-001", "trial-001", 1)
    second = simulated_neuroadaptive_decision("P-001", "trial-001", 1)

    assert first == second
    assert yoked_sham_decision("P-001", "seed-a", 0)["display_suggestion"] is True
    assert yoked_sham_decision("P-001", "seed-a", 1)["display_suggestion"] is False


def test_feature_extraction_rejects_large_artifact_epoch() -> None:
    sampling_rate = 250.0
    t = np.arange(0, 4, 1 / sampling_rate)
    data = np.vstack(
        [
            np.sin(2 * np.pi * 10 * t),
            np.sin(2 * np.pi * 10 * t),
            np.sin(2 * np.pi * 6 * t),
            np.sin(2 * np.pi * 6 * t),
        ]
    )
    data[:, :20] = 200.0
    windows = compute_online_feature_windows(
        data,
        sampling_rate,
        ("Pz", "O1", "Fz", "FCz"),
        ("Pz", "O1"),
        ("Fz", "FCz"),
        (8.0, 12.0),
        (4.0, 8.0),
    )

    assert any(not window["valid"] for window in windows)

from __future__ import annotations

from app.eeg import detect_joint_decrease_trigger
from app.state_machine import hash_string


YOKED_SCHEDULES: dict[str, list[dict]] = {
    "seed-a": [
        {"display_suggestion": True, "display_time_seconds": 20},
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": True, "display_time_seconds": 40},
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": True, "display_time_seconds": 30},
    ],
    "seed-b": [
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": True, "display_time_seconds": 30},
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": True, "display_time_seconds": 50},
        {"display_suggestion": True, "display_time_seconds": 20},
    ],
    "seed-c": [
        {"display_suggestion": True, "display_time_seconds": 50},
        {"display_suggestion": True, "display_time_seconds": 20},
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": False, "display_time_seconds": None},
        {"display_suggestion": True, "display_time_seconds": 40},
    ],
}


def choose_yoked_seed(participant_id: str) -> str:
    seeds = sorted(YOKED_SCHEDULES)
    return seeds[hash_string(participant_id) % len(seeds)]


def simulated_neuroadaptive_decision(participant_id: str, trial_id: str, trial_order: int) -> dict:
    seed = hash_string(f"{participant_id}:{trial_id}:{trial_order}")
    display = seed % 100 < 55
    time_options = [20.0, 30.0, 40.0, 50.0]
    display_time = time_options[(seed // 7) % len(time_options)] if display else None
    alpha_change = -0.18 if display else -0.05
    theta_change = -0.17 if display else -0.04
    return {
        "mode": "simulation",
        "alpha_change": alpha_change,
        "theta_change": theta_change,
        "eeg_valid": True,
        "display_suggestion": display,
        "display_time_seconds": display_time,
        "decision_source": "deterministic_simulation",
    }


def yoked_sham_decision(participant_id: str, seed_schedule_id: str, sequence_position: int) -> dict:
    schedule = YOKED_SCHEDULES.get(seed_schedule_id) or YOKED_SCHEDULES["seed-a"]
    index = sequence_position % len(schedule)
    item = schedule[index]
    return {
        "mode": "yoked",
        "alpha_change": None,
        "theta_change": None,
        "eeg_valid": True,
        "display_suggestion": bool(item["display_suggestion"]),
        "display_time_seconds": item["display_time_seconds"],
        "decision_source": "preloaded_yoked_seed_schedule",
        "donor_participant_id": f"donor-{choose_yoked_seed(participant_id + ':donor')}",
        "seed_schedule_id": seed_schedule_id,
        "sequence_position": index,
    }


def real_controller_decision(windows: list[dict]) -> dict:
    decision = detect_joint_decrease_trigger(windows)
    return {
        "mode": "real",
        "alpha_change": decision["alpha_change"],
        "theta_change": decision["theta_change"],
        "eeg_valid": decision["eeg_valid"],
        "display_suggestion": decision["display_suggestion"],
        "display_time_seconds": decision["display_time_seconds"],
        "decision_source": decision["decision_source"],
        "comparisons": decision["comparisons"],
    }

from __future__ import annotations

from dataclasses import dataclass


Condition = str
TrialStage = str

CONDITIONS: tuple[Condition, ...] = (
    "no_ai",
    "fixed_early",
    "fixed_delayed",
    "neuroadaptive",
    "yoked_sham",
)

CONDITION_LABELS: dict[Condition, str] = {
    "no_ai": "无AI",
    "fixed_early": "构思前AI",
    "fixed_delayed": "构思后AI",
    "neuroadaptive": "神经自适应AI",
    "yoked_sham": "安慰剂",
}

OFFICIAL_DURATIONS_SECONDS: dict[str, float | None] = {
    "reading": 15.0,
    "ideation": 60.0,
    "suggestion": 15.0,
    "writing": None,
    "rating": None,
    "break_short": 90.0,
    "break_long": 300.0,
}

DEV_DURATIONS_SECONDS: dict[str, float | None] = {
    "reading": 3.0,
    "ideation": 12.0,
    "suggestion": 4.0,
    "writing": None,
    "rating": None,
    "break_short": 3.0,
    "break_long": 5.0,
}

LATIN_SQUARE_5: tuple[tuple[Condition, ...], ...] = (
    ("no_ai", "fixed_early", "fixed_delayed", "neuroadaptive", "yoked_sham"),
    ("fixed_early", "fixed_delayed", "yoked_sham", "no_ai", "neuroadaptive"),
    ("fixed_delayed", "neuroadaptive", "no_ai", "yoked_sham", "fixed_early"),
    ("neuroadaptive", "yoked_sham", "fixed_early", "fixed_delayed", "no_ai"),
    ("yoked_sham", "no_ai", "neuroadaptive", "fixed_early", "fixed_delayed"),
)

SCHEDULE_SLOTS: tuple[str, ...] = tuple(
    f"theme{theme_index}-sub{sub_index}"
    for theme_index in range(1, 6)
    for sub_index in range(1, 5)
)

_PRACTICE_CODE = "P"
_SCHEDULE_CODE_TO_CONDITION: dict[str, Condition] = {
    "N": "no_ai",
    "E": "fixed_early",
    "D": "fixed_delayed",
    "A": "neuroadaptive",
    "Y": "yoked_sham",
}

_OFFICIAL_SCHEDULE_ROWS: dict[int, str] = {
    1: "PPPPPNEDAYNEDAYNEDAY",
    2: "YPPPPPNEDAYNEDAYNEDA",
    3: "AYPPPPPNEDAYNEDAYNED",
    4: "DAYPPPPPNEDAYNEDAYNE",
    5: "EDAYPPPPPNEDAYNEDAYN",
    6: "NEDAYPPPPPNEDAYNEDAY",
    7: "YNEDAYPPPPPNEDAYNEDA",
    8: "AYNEDAYPPPPPNEDAYNED",
    9: "DAYNEDAYPPPPPNEDAYNE",
    10: "EDAYNEDAYPPPPPNEDAYN",
    11: "NEDAYNEDAYPPPPPNEDAY",
    12: "YNEDAYNEDAYPPPPPNEDA",
    13: "AYNEDAYNEDAYPPPPPNED",
    14: "DAYNEDAYNEDAYPPPPPNE",
    15: "EDAYNEDAYNEDAYPPPPPN",
    16: "NEDAYNEDAYNEDAYPPPPP",
    17: "PNEDAYNEDAYNEDAYPPPP",
    18: "PPNEDAYNEDAYNEDAYPPP",
    19: "PPPNEDAYNEDAYNEDAYPP",
    20: "PPPPNEDAYNEDAYNEDAYP",
}


@dataclass(frozen=True)
class TimelineSegment:
    stage: str
    duration_seconds: float | None
    role: str = "task"


@dataclass(frozen=True)
class ScheduleCell:
    slot_id: str
    phase: str
    condition: Condition


def hash_string(value: str) -> int:
    hash_value = 2166136261
    for char in value:
        hash_value ^= ord(char)
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return hash_value


def block_orders_for_participant(participant_id: str, blocks: int = 3) -> list[list[Condition]]:
    start = hash_string(participant_id) % len(LATIN_SQUARE_5)
    return [list(LATIN_SQUARE_5[(start + block) % len(LATIN_SQUARE_5)]) for block in range(blocks)]


def parse_official_participant_id(participant_id: str | int) -> int:
    text = str(participant_id).strip()
    if not text.isdigit():
        raise ValueError("PARTICIPANT_ID_OUT_OF_SCHEDULE_RANGE")
    participant_number = int(text)
    if participant_number not in _OFFICIAL_SCHEDULE_ROWS:
        raise ValueError("PARTICIPANT_ID_OUT_OF_SCHEDULE_RANGE")
    return participant_number


def official_schedule_for_participant(participant_id: str | int) -> list[ScheduleCell]:
    participant_number = parse_official_participant_id(participant_id)
    row = _OFFICIAL_SCHEDULE_ROWS[participant_number]
    first_practice = row.index(_PRACTICE_CODE)
    slot_order = list(range(first_practice, len(row))) + list(range(first_practice))
    practice_index = 0
    schedule: list[ScheduleCell] = []
    for slot_index in slot_order:
        code = row[slot_index]
        slot_id = SCHEDULE_SLOTS[slot_index]
        if code == _PRACTICE_CODE:
            condition = CONDITIONS[practice_index % len(CONDITIONS)]
            practice_index += 1
            schedule.append(ScheduleCell(slot_id=slot_id, phase="practice", condition=condition))
        else:
            schedule.append(ScheduleCell(slot_id=slot_id, phase="formal", condition=_SCHEDULE_CODE_TO_CONDITION[code]))
    return schedule


def official_condition_blocks_for_participant(participant_id: str | int) -> list[list[Condition]]:
    formal_conditions = [
        cell.condition
        for cell in official_schedule_for_participant(participant_id)
        if cell.phase == "formal"
    ]
    return [formal_conditions[index:index + 5] for index in range(0, len(formal_conditions), 5)]


def stage_durations(timer_preset: str = "official") -> dict[str, float | None]:
    return DEV_DURATIONS_SECONDS if timer_preset == "dev" else OFFICIAL_DURATIONS_SECONDS


def planned_timeline(
    condition: Condition,
    *,
    timer_preset: str = "official",
    display_suggestion: bool = True,
    trigger_time_seconds: float | None = None,
) -> list[TimelineSegment]:
    durations = stage_durations(timer_preset)
    reading = TimelineSegment("reading", durations["reading"])
    ideation_duration = float(durations["ideation"] or 0)
    suggestion = TimelineSegment("suggestion", durations["suggestion"])
    writing = TimelineSegment("writing", None)
    rating = TimelineSegment("rating", None)

    if condition == "no_ai":
        return [reading, TimelineSegment("ideation", ideation_duration), writing, rating]
    if condition == "fixed_early":
        return [reading, suggestion, TimelineSegment("ideation", ideation_duration), writing, rating]
    if condition == "fixed_delayed":
        return [reading, TimelineSegment("ideation", ideation_duration), suggestion, writing, rating]
    if condition in {"neuroadaptive", "yoked_sham"}:
        if not display_suggestion:
            return [reading, TimelineSegment("ideation", ideation_duration), writing, rating]
        trigger = min(max(trigger_time_seconds or ideation_duration, 0.0), ideation_duration)
        remaining = max(0.0, ideation_duration - trigger)
        segments = [reading, TimelineSegment("ideation", trigger)]
        segments.append(suggestion)
        if remaining > 0:
            segments.append(TimelineSegment("ideation_resume", remaining))
        segments.extend([writing, rating])
        return segments
    raise ValueError(f"Unknown condition: {condition}")


def next_break_seconds(completed_trials: int, total_trials: int, timer_preset: str = "official") -> float:
    if completed_trials >= total_trials:
        return 0.0
    durations = stage_durations(timer_preset)
    if completed_trials % 5 == 0:
        return float(durations["break_long"] or 0)
    if completed_trials % 2 == 0:
        return float(durations["break_short"] or 0)
    return 0.0

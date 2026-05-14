from __future__ import annotations

from collections import Counter

import pytest

from app.state_machine import (
    CONDITIONS,
    block_orders_for_participant,
    official_schedule_for_participant,
    planned_timeline,
)
from app.text_validation import validate_four_sentence_continuation


def test_formal_blocks_contain_each_condition_once() -> None:
    blocks = block_orders_for_participant("P-001")

    assert len(blocks) == 3
    for block in blocks:
        assert set(block) == set(CONDITIONS)


def test_formal_15_trials_have_three_per_condition() -> None:
    blocks = block_orders_for_participant("P-001")
    counts = Counter(condition for block in blocks for condition in block)

    assert counts == {condition: 3 for condition in CONDITIONS}


def test_official_participant_1_schedule_starts_with_five_practice_trials() -> None:
    schedule = official_schedule_for_participant("1")

    assert [cell.phase for cell in schedule[:5]] == ["practice"] * 5
    formal_counts = Counter(cell.condition for cell in schedule if cell.phase == "formal")
    assert formal_counts == {condition: 3 for condition in CONDITIONS}


def test_official_participant_14_schedule_wraps_to_left_edge() -> None:
    schedule = official_schedule_for_participant("14")

    assert [cell.slot_id for cell in schedule[:8]] == [
        "theme4-sub2",
        "theme4-sub3",
        "theme4-sub4",
        "theme5-sub1",
        "theme5-sub2",
        "theme5-sub3",
        "theme5-sub4",
        "theme1-sub1",
    ]
    assert [cell.phase for cell in schedule[:5]] == ["practice"] * 5
    assert schedule[7].condition == "fixed_delayed"


@pytest.mark.parametrize("participant_id", ["0", "21", "P-001"])
def test_official_schedule_rejects_out_of_range_ids(participant_id: str) -> None:
    with pytest.raises(ValueError, match="PARTICIPANT_ID_OUT_OF_SCHEDULE_RANGE"):
        official_schedule_for_participant(participant_id)


def test_neuroadaptive_trigger_pauses_and_resumes_ideation() -> None:
    timeline = planned_timeline(
        "neuroadaptive",
        timer_preset="official",
        display_suggestion=True,
        trigger_time_seconds=20,
    )

    assert [segment.stage for segment in timeline] == [
        "reading",
        "ideation",
        "suggestion",
        "ideation_resume",
        "writing",
        "rating",
    ]
    assert timeline[1].duration_seconds == 20
    assert timeline[3].duration_seconds == 40


def test_four_sentence_validation() -> None:
    result = validate_four_sentence_continuation("第一句。第二句！第三句？第四句。")

    assert result["valid"]
    assert result["sentence_count"] == 4


def test_four_sentence_override_records_flag() -> None:
    result = validate_four_sentence_continuation("一句。两句。", override=True)

    assert result["valid"]
    assert not result["raw_valid"]
    assert result["override_used"]

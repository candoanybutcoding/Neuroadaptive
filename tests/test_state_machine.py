from __future__ import annotations

from collections import Counter

from app.state_machine import CONDITIONS, block_orders_for_participant, planned_timeline
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

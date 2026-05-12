from __future__ import annotations

from app.materials import validate_material_rows


def material_rows(practice: int = 5, formal: int = 20) -> list[dict[str, str]]:
    rows = []
    for phase, count in (("practice", practice), ("formal", formal)):
        for index in range(count):
            rows.append(
                {
                    "phase": phase,
                    "prompt_id": f"{phase}-{index:02d}",
                    "theme": "主题",
                    "subpremise_id": str(index),
                    "premise_text": f"材料 {index}",
                    "suggestion_text": f"建议 {index}",
                    "suggestion_model": "fixed",
                    "suggestion_generated_at": "2026-05-12",
                    "generation_prompt_version": "v1",
                }
            )
    return rows


def test_material_validation_accepts_required_counts() -> None:
    result = validate_material_rows(material_rows())

    assert result.ok
    assert result.counts == {"practice": 5, "formal": 20}


def test_material_validation_rejects_missing_columns() -> None:
    rows = material_rows()
    for row in rows:
        row.pop("suggestion_model")

    result = validate_material_rows(rows)

    assert not result.ok
    assert any("Missing required columns" in error for error in result.errors)


def test_material_validation_rejects_duplicate_prompt_ids() -> None:
    rows = material_rows()
    rows[1]["prompt_id"] = rows[0]["prompt_id"]

    result = validate_material_rows(rows)

    assert not result.ok
    assert any("Duplicate prompt_id" in error for error in result.errors)


def test_material_validation_requires_formal_count() -> None:
    result = validate_material_rows(material_rows(formal=19))

    assert not result.ok
    assert any("At least 20 formal" in error for error in result.errors)

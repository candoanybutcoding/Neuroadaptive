from __future__ import annotations

import csv
import io
from typing import Any

import sqlite3

from app.controller import (
    choose_yoked_seed,
    real_controller_decision,
    simulated_neuroadaptive_decision,
    yoked_sham_decision,
)
from app.db import dumps_json, loads_json, new_id, row_to_dict, utc_now
from app.materials import (
    MIN_FORMAL_MATERIALS,
    MIN_PRACTICE_MATERIALS,
    MaterialValidation,
    validate_material_rows,
)
from app.state_machine import (
    CONDITIONS,
    block_orders_for_participant,
    next_break_seconds,
    official_condition_blocks_for_participant,
    official_schedule_for_participant,
    parse_official_participant_id,
    planned_timeline,
)
from app.text_validation import validate_four_sentence_continuation


def material_status(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT phase, COUNT(*) AS n FROM materials WHERE active = 1 GROUP BY phase"
    ).fetchall()
    counts = {"practice": 0, "formal": 0}
    for row in rows:
        counts[row["phase"]] = row["n"]
    return {
        "counts": counts,
        "ready": counts["practice"] >= MIN_PRACTICE_MATERIALS
        and counts["formal"] >= MIN_FORMAL_MATERIALS,
        "required": {"practice": MIN_PRACTICE_MATERIALS, "formal": MIN_FORMAL_MATERIALS},
    }


def import_materials(conn: sqlite3.Connection, rows: list[dict[str, object]]) -> MaterialValidation:
    validation = validate_material_rows(rows)
    if not validation.ok:
        return validation

    now = utc_now()
    with conn:
        conn.execute("UPDATE materials SET active = 0")
        for row in validation.rows:
            conn.execute(
                """
                INSERT INTO materials (
                    id, phase, prompt_id, theme, subpremise_id, premise_text, suggestion_text,
                    suggestion_model, suggestion_generated_at, generation_prompt_version,
                    difficulty, condition_slot, participant_slot, notes, active, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(prompt_id) DO UPDATE SET
                    phase = excluded.phase,
                    theme = excluded.theme,
                    subpremise_id = excluded.subpremise_id,
                    premise_text = excluded.premise_text,
                    suggestion_text = excluded.suggestion_text,
                    suggestion_model = excluded.suggestion_model,
                    suggestion_generated_at = excluded.suggestion_generated_at,
                    generation_prompt_version = excluded.generation_prompt_version,
                    difficulty = excluded.difficulty,
                    condition_slot = excluded.condition_slot,
                    participant_slot = excluded.participant_slot,
                    notes = excluded.notes,
                    active = 1,
                    imported_at = excluded.imported_at
                """,
                (
                    new_id(),
                    row["phase"],
                    row["prompt_id"],
                    row["theme"],
                    row["subpremise_id"],
                    row["premise_text"],
                    row["suggestion_text"],
                    row["suggestion_model"],
                    row["suggestion_generated_at"],
                    row["generation_prompt_version"],
                    row.get("difficulty", ""),
                    row.get("condition_slot", ""),
                    row.get("participant_slot", ""),
                    row.get("notes", ""),
                    now,
                ),
            )
    return validation


def create_session(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    status = material_status(conn)
    if not status["ready"]:
        raise ValueError("FORMAL_MATERIALS_NOT_READY")

    mode = payload.get("mode") or "official"
    if mode not in {"dev", "official"}:
        raise ValueError("INVALID_SESSION_MODE")
    timer_preset = payload.get("timer_preset") or ("dev" if mode == "dev" else "official")
    controller_mode = payload.get("controller_mode") or ("simulation" if mode == "dev" else "real")
    if controller_mode not in {"simulation", "real"}:
        raise ValueError("INVALID_CONTROLLER_MODE")

    participant_id = (payload.get("participant_id") or payload.get("subject_id") or "").strip()
    if mode == "dev" and not participant_id:
        participant_id = "1"
    if mode == "official":
        parse_official_participant_id(participant_id)

    age_payload = payload.get("age")
    if mode == "dev" and (age_payload is None or age_payload == ""):
        age_payload = 1
    try:
        age = int(age_payload or 0)
    except (TypeError, ValueError):
        raise ValueError("AGE_OUT_OF_RANGE") from None
    if age < 1 or age > 120:
        raise ValueError("AGE_OUT_OF_RANGE")

    session_id = new_id()
    now = utc_now()
    block_order = (
        official_condition_blocks_for_participant(participant_id)
        if mode == "official"
        else block_orders_for_participant(participant_id)
    )
    yoked_seed = choose_yoked_seed(participant_id)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO participants (
                id, age, native_language, vision_status, neurological_history,
                psychiatric_history, genai_usage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM participants WHERE id = ?), ?))
            """,
            (
                participant_id,
                age,
                payload.get("native_language", ""),
                payload.get("vision_status", ""),
                payload.get("neurological_history", ""),
                payload.get("psychiatric_history", ""),
                payload.get("genai_usage", ""),
                participant_id,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO sessions (
                id, participant_id, age, mode, timer_preset, controller_mode, status, stage,
                block_order, current_trial_index, yoked_seed_id, alpha_band, theta_band,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', 'dat', ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                participant_id,
                age,
                mode,
                timer_preset,
                controller_mode,
                dumps_json(block_order),
                yoked_seed,
                dumps_json([8.0, 12.0]),
                dumps_json([4.0, 8.0]),
                now,
                now,
            ),
        )
        _create_schedule(conn, session_id, participant_id, mode, block_order)
    return get_session_state(conn, session_id)


def get_session_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError("SESSION_NOT_FOUND")
    session = row_to_dict(row) or {}
    session["block_order"] = loads_json(session.get("block_order"), [])
    session["alpha_band"] = loads_json(session.get("alpha_band"), [8.0, 12.0])
    session["theta_band"] = loads_json(session.get("theta_band"), [4.0, 8.0])
    session["baseline_summary"] = loads_json(session.get("baseline_summary"), None)
    session["iaf_result"] = loads_json(session.get("iaf_result"), None)
    completed = conn.execute(
        "SELECT COUNT(*) AS n FROM trials WHERE session_id = ? AND status = 'completed'",
        (session_id,),
    ).fetchone()["n"]
    total = conn.execute("SELECT COUNT(*) AS n FROM trial_schedule WHERE session_id = ?", (session_id,)).fetchone()["n"]
    session["completed_trials"] = completed
    session["total_trials"] = total
    session["material_status"] = material_status(conn)
    return {"session": session}


def save_dat_response(conn: sqlite3.Connection, session_id: str, payload: dict[str, Any]) -> dict:
    words = payload.get("words") or []
    if not isinstance(words, list):
        raise ValueError("DAT_WORDS_MUST_BE_LIST")
    with conn:
        conn.execute(
            """
            INSERT INTO dat_responses (id, session_id, words_json, raw_score, external_score, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                session_id,
                dumps_json(words),
                payload.get("raw_score"),
                payload.get("external_score"),
                utc_now(),
            ),
        )
        conn.execute("UPDATE sessions SET stage = 'calibration', updated_at = ? WHERE id = ?", (utc_now(), session_id))
    return get_session_state(conn, session_id)


def next_trial(conn: sqlite3.Connection, session_id: str) -> dict:
    session = _session_row(conn, session_id)
    schedule = conn.execute(
        """
        SELECT * FROM trial_schedule
        WHERE session_id = ? AND trial_order = ?
        """,
        (session_id, session["current_trial_index"] + 1),
    ).fetchone()
    if schedule is None:
        return {"session_complete": True, "trial": None}

    trial = conn.execute("SELECT * FROM trials WHERE schedule_id = ?", (schedule["id"],)).fetchone()
    if trial is None:
        trial_id = new_id()
        now = utc_now()
        with conn:
            conn.execute(
                """
                INSERT INTO trials (
                    id, schedule_id, session_id, participant_id, material_id, phase, condition,
                    block_index, trial_order, total_trials, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?)
                """,
                (
                    trial_id,
                    schedule["id"],
                    session_id,
                    session["participant_id"],
                    schedule["material_id"],
                    schedule["phase"],
                    schedule["condition"],
                    schedule["block_index"],
                    schedule["trial_order"],
                    schedule["total_trials"],
                    now,
                ),
            )
        trial = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    return {"session_complete": False, "trial": _trial_config(conn, trial)}


def write_trial_events(conn: sqlite3.Connection, trial_id: str, payload: dict[str, Any]) -> dict:
    trial = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    if trial is None:
        raise ValueError("TRIAL_NOT_FOUND")
    with conn:
        for event in payload.get("phase_events", []) or payload.get("stageEvents", []) or []:
            conn.execute(
                """
                INSERT INTO phase_events (trial_id, stage, event, timestamp, remaining_ms, detail_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trial_id,
                    event.get("stage"),
                    event.get("event"),
                    event.get("timestamp") or utc_now(),
                    event.get("remaining_ms") if "remaining_ms" in event else event.get("remainingMs"),
                    dumps_json(event.get("detail", {})),
                ),
            )
        for event in payload.get("keystroke_events", []) or payload.get("keystrokeEvents", []) or []:
            conn.execute(
                """
                INSERT INTO keystroke_events (trial_id, timestamp, key, cursor_position, action)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    trial_id,
                    event.get("timestamp") or utc_now(),
                    event.get("key", ""),
                    int(event.get("cursor_position", event.get("cursorPosition", 0))),
                    event.get("action", "type"),
                ),
            )
        for event in payload.get("suggestion_events", []) or payload.get("suggestionEvents", []) or []:
            conn.execute(
                """
                INSERT INTO suggestion_events (trial_id, timestamp, action, suggestion_text)
                VALUES (?, ?, ?, ?)
                """,
                (
                    trial_id,
                    event.get("timestamp") or utc_now(),
                    event.get("action", "ignored"),
                    event.get("suggestion_text", event.get("suggestionText", "")),
                ),
            )
        for event in payload.get("system_events", []) or payload.get("systemEvents", []) or []:
            conn.execute(
                """
                INSERT INTO system_logs (session_id, trial_id, timestamp, level, message, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trial["session_id"],
                    trial_id,
                    event.get("timestamp") or utc_now(),
                    event.get("level", "info"),
                    event.get("message", ""),
                    event.get("detail"),
                ),
            )
    return {"ok": True}


def controller_decision(conn: sqlite3.Connection, trial_id: str, payload: dict[str, Any] | None = None) -> dict:
    payload = payload or {}
    trial = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    if trial is None:
        raise ValueError("TRIAL_NOT_FOUND")
    session = _session_row(conn, trial["session_id"])
    condition = trial["condition"]

    if condition == "fixed_early":
        decision = {"mode": "fixed", "display_suggestion": True, "display_time_seconds": 0.0, "decision_source": "fixed_early", "eeg_valid": True, "alpha_change": None, "theta_change": None}
    elif condition == "fixed_delayed":
        decision = {"mode": "fixed", "display_suggestion": True, "display_time_seconds": 60.0, "decision_source": "fixed_delayed", "eeg_valid": True, "alpha_change": None, "theta_change": None}
    elif condition == "no_ai":
        decision = {"mode": "fixed", "display_suggestion": False, "display_time_seconds": None, "decision_source": "no_ai", "eeg_valid": True, "alpha_change": None, "theta_change": None}
    elif condition == "yoked_sham":
        donor = _donor_decision(conn, session["participant_id"], int(payload.get("sequence_position", trial["trial_order"] - 1)))
        decision = donor or yoked_sham_decision(session["participant_id"], session["yoked_seed_id"], int(payload.get("sequence_position", trial["trial_order"] - 1)))
    elif session["controller_mode"] == "real" and payload.get("windows"):
        decision = real_controller_decision(payload["windows"])
    else:
        decision = simulated_neuroadaptive_decision(session["participant_id"], trial_id, trial["trial_order"])

    with conn:
        for window in payload.get("windows", []) or []:
            conn.execute(
                """
                INSERT INTO controller_windows (
                    trial_id, window_start_seconds, window_end_seconds, alpha_log_power, theta_log_power,
                    usable_epoch_ratio, rejected_epoch_ratio, valid, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trial_id,
                    window.get("window_start_seconds", 0),
                    window.get("window_end_seconds", 0),
                    window.get("alpha_log_power"),
                    window.get("theta_log_power"),
                    window.get("usable_epoch_ratio", 0),
                    window.get("rejected_epoch_ratio", 1),
                    1 if window.get("valid") else 0,
                    dumps_json(window),
                    utc_now(),
                ),
            )
        conn.execute(
            """
            INSERT INTO controller_decisions (
                trial_id, condition, mode, alpha_change, theta_change, eeg_valid, display_suggestion,
                display_time_seconds, decision_source, donor_participant_id, seed_schedule_id,
                sequence_position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trial_id,
                condition,
                decision.get("mode", session["controller_mode"]),
                decision.get("alpha_change"),
                decision.get("theta_change"),
                1 if decision.get("eeg_valid", True) else 0,
                1 if decision.get("display_suggestion") else 0,
                decision.get("display_time_seconds"),
                decision.get("decision_source", ""),
                decision.get("donor_participant_id"),
                decision.get("seed_schedule_id"),
                decision.get("sequence_position"),
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE trials SET display_suggestion = ?, suggestion_display_time_seconds = ? WHERE id = ?",
            (1 if decision.get("display_suggestion") else 0, decision.get("display_time_seconds"), trial_id),
        )
    return decision


def complete_trial(conn: sqlite3.Connection, trial_id: str, payload: dict[str, Any]) -> dict:
    trial = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    if trial is None:
        raise ValueError("TRIAL_NOT_FOUND")
    override = bool(payload.get("text_validity_override") or payload.get("override"))
    final_text = payload.get("final_text", payload.get("finalText", ""))
    validity = validate_four_sentence_continuation(final_text, override=override)
    if not validity["valid"]:
        raise ValueError("FOUR_SENTENCE_REQUIREMENT_NOT_MET")
    suggestion_action = payload.get("suggestion_action", payload.get("suggestionAction"))
    planning_notes = payload.get("planning_notes", payload.get("planningNotes", ""))
    ratings = payload.get("ratings", payload.get("surveyResponses", {})) or {}
    now = utc_now()
    with conn:
        conn.execute(
            """
            UPDATE trials
            SET status = 'completed', completed_at = ?, planning_notes = ?, final_text = ?,
                sentence_count = ?, four_sentence_valid = ?, text_validity_override = ?,
                suggestion_action = ?
            WHERE id = ?
            """,
            (
                now,
                planning_notes,
                final_text,
                validity["sentence_count"],
                1 if validity["raw_valid"] else 0,
                1 if validity["override_used"] else 0,
                suggestion_action,
                trial_id,
            ),
        )
        conn.execute(
            "UPDATE sessions SET current_trial_index = ?, updated_at = ? WHERE id = ?",
            (trial["trial_order"], now, trial["session_id"]),
        )
        for key, value in ratings.items():
            conn.execute(
                "INSERT INTO ratings (trial_id, session_id, item, value, created_at) VALUES (?, ?, ?, ?, ?)",
                (trial_id, trial["session_id"], key, str(value), now),
            )
    total = trial["total_trials"]
    break_seconds = next_break_seconds(trial["trial_order"], total, _session_row(conn, trial["session_id"])["timer_preset"])
    return {"session_complete": trial["trial_order"] >= total, "next_break_seconds": break_seconds, "text_validity": validity}


def save_closing_ratings(conn: sqlite3.Connection, session_id: str, ratings: dict[str, Any]) -> dict:
    now = utc_now()
    with conn:
        for key, value in ratings.items():
            conn.execute(
                "INSERT INTO ratings (session_id, item, value, created_at) VALUES (?, ?, ?, ?)",
                (session_id, key, str(value), now),
            )
        conn.execute("UPDATE sessions SET stage = 'complete', status = 'completed', updated_at = ? WHERE id = ?", (now, session_id))
    return get_session_state(conn, session_id)


def export_session(conn: sqlite3.Connection, session_id: str, fmt: str) -> Any:
    session = get_session_state(conn, session_id)["session"]
    tables = {}
    for table in (
        "dat_responses",
        "trial_schedule",
        "trials",
        "baseline_runs",
        "iaf_results",
        "phase_events",
        "keystroke_events",
        "suggestion_events",
        "controller_windows",
        "controller_decisions",
        "ratings",
        "system_logs",
    ):
        if table in {"trial_schedule", "trials", "baseline_runs", "iaf_results", "ratings", "system_logs"}:
            rows = conn.execute(f"SELECT * FROM {table} WHERE session_id = ?", (session_id,)).fetchall()
        elif table == "dat_responses":
            rows = conn.execute("SELECT * FROM dat_responses WHERE session_id = ?", (session_id,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE trial_id IN (SELECT id FROM trials WHERE session_id = ?)",
                (session_id,),
            ).fetchall()
        tables[table] = [row_to_dict(row) for row in rows]
    payload = {"exported_at": utc_now(), "session": session, **tables}
    conn.execute("INSERT INTO exports (session_id, format, created_at) VALUES (?, ?, ?)", (session_id, fmt, utc_now()))
    conn.commit()
    if fmt == "json":
        return payload
    if fmt != "csv":
        raise ValueError("UNSUPPORTED_EXPORT_FORMAT")
    return _trials_csv(payload["trials"], payload["ratings"])


def _create_schedule(
    conn: sqlite3.Connection,
    session_id: str,
    participant_id: str,
    mode: str,
    block_order: list[list[str]],
) -> None:
    schedule: list[tuple[str, int, str, sqlite3.Row]] = []
    if mode == "official":
        formal_index = 0
        for cell in official_schedule_for_participant(participant_id):
            material = _select_material_by_slot(conn, cell.slot_id)
            if cell.phase == "practice":
                block_index = 0
            else:
                block_index = formal_index // 5 + 1
                formal_index += 1
            schedule.append((cell.phase, block_index, cell.condition, material))
    else:
        practice_materials = _select_materials(conn, "practice", 5, participant_id)
        formal_materials = _select_materials(conn, "formal", 15, participant_id)
        for index, condition in enumerate(CONDITIONS):
            schedule.append(("practice", 0, condition, practice_materials[index]))
        formal_index = 0
        for block_index, conditions in enumerate(block_order, start=1):
            for condition in conditions:
                schedule.append(("formal", block_index, condition, formal_materials[formal_index]))
                formal_index += 1
    total = len(schedule)
    for order, (phase, block_index, condition, material) in enumerate(schedule, start=1):
        conn.execute(
            """
            INSERT INTO trial_schedule (
                id, session_id, material_id, phase, condition, block_index, trial_order, total_trials
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id(), session_id, material["id"], phase, condition, block_index, order, total),
        )


def _select_material_by_slot(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM materials WHERE active = 1 AND condition_slot = ?",
        (slot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"MATERIAL_SLOT_NOT_FOUND:{slot_id}")
    return row


def _select_materials(conn: sqlite3.Connection, phase: str, count: int, participant_id: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM materials WHERE active = 1 AND phase = ? ORDER BY prompt_id",
        (phase,),
    ).fetchall()
    if len(rows) < count:
        raise ValueError(f"NOT_ENOUGH_{phase.upper()}_MATERIALS")
    offset = sum(ord(char) for char in participant_id) % len(rows)
    rotated = rows[offset:] + rows[:offset]
    return rotated[:count]


def _trial_config(conn: sqlite3.Connection, trial: sqlite3.Row) -> dict:
    material = conn.execute("SELECT * FROM materials WHERE id = ?", (trial["material_id"],)).fetchone()
    session = _session_row(conn, trial["session_id"])
    display = trial["display_suggestion"]
    display_bool = None if display is None else bool(display)
    return {
        "trial_id": trial["id"],
        "session_id": trial["session_id"],
        "participant_id": trial["participant_id"],
        "phase": trial["phase"],
        "condition": trial["condition"],
        "block_index": trial["block_index"],
        "trial_order": trial["trial_order"],
        "total_trials": trial["total_trials"],
        "status": trial["status"],
        "display_suggestion": display_bool,
        "suggestion_display_time_seconds": trial["suggestion_display_time_seconds"],
        "material": row_to_dict(material),
        "timeline": [
            segment.__dict__
            for segment in planned_timeline(
                trial["condition"],
                timer_preset=session["timer_preset"],
                display_suggestion=True if display_bool is None else display_bool,
                trigger_time_seconds=trial["suggestion_display_time_seconds"],
            )
        ],
    }


def _session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError("SESSION_NOT_FOUND")
    return row


def _donor_decision(conn: sqlite3.Connection, participant_id: str, sequence_position: int) -> dict | None:
    donor = conn.execute(
        """
        SELECT cd.*, t.participant_id FROM controller_decisions cd
        JOIN trials t ON t.id = cd.trial_id
        WHERE t.condition = 'neuroadaptive' AND t.participant_id != ?
        ORDER BY cd.created_at
        LIMIT 1 OFFSET ?
        """,
        (participant_id, max(0, sequence_position)),
    ).fetchone()
    if donor is None:
        return None
    return {
        "mode": "yoked",
        "alpha_change": None,
        "theta_change": None,
        "eeg_valid": True,
        "display_suggestion": bool(donor["display_suggestion"]),
        "display_time_seconds": donor["display_time_seconds"],
        "decision_source": "donor_neuroadaptive_sequence",
        "donor_participant_id": donor["participant_id"],
        "seed_schedule_id": None,
        "sequence_position": sequence_position,
    }


def _trials_csv(trials: list[dict], ratings: list[dict]) -> str:
    ratings_by_trial: dict[str, dict[str, str]] = {}
    for rating in ratings:
        trial_id = rating.get("trial_id")
        if trial_id:
            ratings_by_trial.setdefault(trial_id, {})[rating["item"]] = rating["value"]
    headers = [
        "trial_id",
        "participant_id",
        "phase",
        "condition",
        "trial_order",
        "block_index",
        "material_id",
        "display_suggestion",
        "suggestion_display_time_seconds",
        "sentence_count",
        "four_sentence_valid",
        "text_validity_override",
        "suggestion_action",
        "planning_notes",
        "final_text",
        "ratings_json",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for trial in trials:
        writer.writerow([
            trial.get("id"),
            trial.get("participant_id"),
            trial.get("phase"),
            trial.get("condition"),
            trial.get("trial_order"),
            trial.get("block_index"),
            trial.get("material_id"),
            trial.get("display_suggestion"),
            trial.get("suggestion_display_time_seconds"),
            trial.get("sentence_count"),
            trial.get("four_sentence_valid"),
            trial.get("text_validity_override"),
            trial.get("suggestion_action"),
            trial.get("planning_notes"),
            trial.get("final_text"),
            dumps_json(ratings_by_trial.get(trial.get("id"), {})),
        ])
    return output.getvalue()

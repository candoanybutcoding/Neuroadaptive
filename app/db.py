from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: str | None = None) -> sqlite3.Connection:
    db_path = Path(path or settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_connection: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = connect()
        init_db(_connection)
    return _connection


def reset_connection() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
    _connection = None


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS participants (
            id TEXT PRIMARY KEY,
            age INTEGER NOT NULL,
            native_language TEXT DEFAULT '',
            vision_status TEXT DEFAULT '',
            neurological_history TEXT DEFAULT '',
            psychiatric_history TEXT DEFAULT '',
            writing_experience TEXT DEFAULT '',
            genai_usage TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            participant_id TEXT NOT NULL,
            age INTEGER NOT NULL,
            mode TEXT NOT NULL,
            timer_preset TEXT NOT NULL,
            controller_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            block_order TEXT NOT NULL,
            current_trial_index INTEGER NOT NULL DEFAULT 0,
            yoked_seed_id TEXT NOT NULL,
            alpha_band TEXT,
            theta_band TEXT,
            baseline_summary TEXT,
            iaf_result TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS dat_responses (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            words_json TEXT NOT NULL,
            raw_score REAL,
            external_score REAL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS materials (
            id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            prompt_id TEXT NOT NULL UNIQUE,
            theme TEXT NOT NULL,
            subpremise_id TEXT NOT NULL,
            premise_text TEXT NOT NULL,
            suggestion_text TEXT NOT NULL,
            suggestion_model TEXT NOT NULL,
            suggestion_generated_at TEXT NOT NULL,
            generation_prompt_version TEXT NOT NULL,
            difficulty TEXT DEFAULT '',
            condition_slot TEXT DEFAULT '',
            participant_slot TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trial_schedule (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            material_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            condition TEXT NOT NULL,
            block_index INTEGER NOT NULL,
            trial_order INTEGER NOT NULL,
            total_trials INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (material_id) REFERENCES materials(id)
        );

        CREATE TABLE IF NOT EXISTS trials (
            id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            material_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            condition TEXT NOT NULL,
            block_index INTEGER NOT NULL,
            trial_order INTEGER NOT NULL,
            total_trials INTEGER NOT NULL,
            status TEXT NOT NULL,
            display_suggestion INTEGER,
            suggestion_display_time_seconds REAL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            planning_notes TEXT DEFAULT '',
            final_text TEXT DEFAULT '',
            sentence_count INTEGER,
            four_sentence_valid INTEGER,
            text_validity_override INTEGER DEFAULT 0,
            suggestion_action TEXT,
            FOREIGN KEY (schedule_id) REFERENCES trial_schedule(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (material_id) REFERENCES materials(id)
        );

        CREATE TABLE IF NOT EXISTS baseline_runs (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            stream_json TEXT,
            summary_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS iaf_results (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            baseline_run_id TEXT,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (baseline_run_id) REFERENCES baseline_runs(id)
        );

        CREATE TABLE IF NOT EXISTS phase_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            event TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            remaining_ms INTEGER,
            detail_json TEXT,
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS keystroke_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            key TEXT NOT NULL,
            cursor_position INTEGER NOT NULL,
            action TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS suggestion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            suggestion_text TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS controller_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT NOT NULL,
            window_start_seconds REAL NOT NULL,
            window_end_seconds REAL NOT NULL,
            alpha_log_power REAL,
            theta_log_power REAL,
            usable_epoch_ratio REAL NOT NULL,
            rejected_epoch_ratio REAL NOT NULL,
            valid INTEGER NOT NULL,
            detail_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS controller_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT NOT NULL,
            condition TEXT NOT NULL,
            mode TEXT NOT NULL,
            alpha_change REAL,
            theta_change REAL,
            eeg_valid INTEGER NOT NULL,
            display_suggestion INTEGER NOT NULL,
            display_time_seconds REAL,
            decision_source TEXT NOT NULL,
            donor_participant_id TEXT,
            seed_schedule_id TEXT,
            sequence_position INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id TEXT,
            session_id TEXT,
            item TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (trial_id) REFERENCES trials(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            trial_id TEXT,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            detail TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (trial_id) REFERENCES trials(id)
        );

        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            format TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        """
    )
    conn.commit()


def new_id() -> str:
    return uuid.uuid4().hex


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def loads_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)

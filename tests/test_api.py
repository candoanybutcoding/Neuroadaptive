from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app import db
from app.db import connect, init_db
from app.main import app
from tests.test_materials import material_rows


@pytest.fixture()
def client(tmp_path):
    conn = connect(str(tmp_path / "experiment.db"))
    init_db(conn)
    db._connection = conn
    yield TestClient(app)
    db.reset_connection()


def csv_bytes() -> bytes:
    rows = material_rows()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def test_official_session_requires_materials(client: TestClient) -> None:
    response = client.post("/api/sessions", json={"participant_id": "P-001", "age": 25})

    assert response.status_code == 409
    assert response.json()["detail"] == "FORMAL_MATERIALS_NOT_READY"


def test_session_trial_controller_completion_and_export_flow(client: TestClient) -> None:
    upload = client.post(
        "/api/materials/import",
        files={"file": ("materials.csv", csv_bytes(), "text/csv")},
    )
    assert upload.status_code == 200
    assert upload.json()["ok"]

    created = client.post(
        "/api/sessions",
        json={
            "participant_id": "P-001",
            "age": 25,
            "mode": "dev",
            "timer_preset": "dev",
            "controller_mode": "simulation",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    dat = client.post(f"/api/sessions/{session_id}/dat", json={"words": [f"词{i}" for i in range(10)]})
    assert dat.status_code == 200

    next_trial = client.get(f"/api/sessions/{session_id}/next-trial")
    assert next_trial.status_code == 200
    trial = next_trial.json()["trial"]

    events = client.post(
        f"/api/trials/{trial['trial_id']}/events",
        json={"phase_events": [{"stage": "reading", "event": "start", "timestamp": "2026-05-12T00:00:00Z"}]},
    )
    assert events.status_code == 200

    decision = client.post(f"/api/trials/{trial['trial_id']}/controller-decision", json={})
    assert decision.status_code == 200
    assert "display_suggestion" in decision.json()

    completion = client.post(
        f"/api/trials/{trial['trial_id']}/complete",
        json={
            "planning_notes": "计划",
            "final_text": "第一句。第二句。第三句。第四句。",
            "ratings": {"autonomy": 5},
        },
    )
    assert completion.status_code == 200

    exported = client.get(f"/api/export/{session_id}.json")
    assert exported.status_code == 200
    assert exported.json()["session"]["id"] == session_id

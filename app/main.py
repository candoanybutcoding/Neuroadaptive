from __future__ import annotations

import asyncio
from typing import Any
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.calibration import TERMINAL_STATES as CALIBRATION_TERMINAL_STATES
from app.calibration import format_sse as calibration_sse
from app.calibration import manager as calibration_manager
from app.db import get_db
from app.experiment import (
    complete_trial,
    controller_decision,
    create_session,
    export_session,
    get_session_state,
    import_materials,
    material_status,
    next_trial,
    save_closing_ratings,
    save_dat_response,
    write_trial_events,
)
from app.materials import parse_material_file, validate_material_rows
from app.state_machine import CONDITION_LABELS, DEV_DURATIONS_SECONDS, OFFICIAL_DURATIONS_SECONDS


ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Neuroadaptive Experiment System", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


class SessionRequest(BaseModel):
    participant_id: str = Field(default="", max_length=80)
    age: int | str | None = None
    native_language: str = ""
    vision_status: str = ""
    neurological_history: str = ""
    psychiatric_history: str = ""
    genai_usage: str = ""
    mode: str = "official"
    timer_preset: str | None = None
    controller_mode: str | None = None


class DatRequest(BaseModel):
    words: list[str]
    raw_score: float | None = None
    external_score: float | None = None


class CalibrationStartRequest(BaseModel):
    session_id: str | None = None


class TrialEventsRequest(BaseModel):
    phase_events: list[dict[str, Any]] = []
    keystroke_events: list[dict[str, Any]] = []
    suggestion_events: list[dict[str, Any]] = []
    system_events: list[dict[str, Any]] = []


class TrialCompletionRequest(BaseModel):
    planning_notes: str = ""
    final_text: str
    suggestion_action: str | None = None
    text_validity_override: bool = False
    ratings: dict[str, Any] = {}


class ControllerDecisionRequest(BaseModel):
    sequence_position: int | None = None
    windows: list[dict[str, Any]] = []


class ClosingRatingsRequest(BaseModel):
    ratings: dict[str, Any]


@app.get("/")
def index() -> FileResponse:
    dist_index = DIST_DIR / "index.html"
    if dist_index.exists():
        return FileResponse(dist_index)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    return {
        "app": "Neuroadaptive Experiment System",
        "version": "0.2.0",
        "materials": material_status(get_db()),
        "conditions": CONDITION_LABELS,
        "durations": {
            "official": OFFICIAL_DURATIONS_SECONDS,
            "dev": DEV_DURATIONS_SECONDS,
        },
        "eyes_open_seconds": settings.eyes_open_seconds,
        "eyes_closed_seconds": settings.eyes_closed_seconds,
        "trim_start_seconds": settings.trim_start_seconds,
        "trim_end_seconds": settings.trim_end_seconds,
        "target_channels": list(settings.target_channels),
        "posterior_channels": list(settings.posterior_channels),
        "frontal_channels": list(settings.frontal_channels),
        "lsl": {
            "stream_type": settings.stream_type,
            "stream_name": settings.stream_name,
            "resolve_timeout_seconds": settings.resolve_timeout_seconds,
        },
    }


@app.post("/api/materials/validate")
async def validate_materials(file: UploadFile = File(...)) -> dict:
    try:
        rows = parse_material_file(file.filename or "materials.csv", await file.read())
        validation = validate_material_rows(rows)
        return validation.__dict__
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/materials/import")
async def import_materials_endpoint(file: UploadFile = File(...)) -> dict:
    try:
        rows = parse_material_file(file.filename or "materials.csv", await file.read())
        validation = import_materials(get_db(), rows)
        return {**validation.__dict__, "material_status": material_status(get_db())}
    except Exception as exc:
        raise _http_error(exc)


@app.get("/api/materials/status")
def get_material_status() -> dict:
    return material_status(get_db())


@app.post("/api/sessions")
def create_session_endpoint(payload: SessionRequest) -> dict:
    try:
        return create_session(get_db(), payload.model_dump())
    except Exception as exc:
        raise _http_error(exc)


@app.get("/api/sessions/{session_id}/state")
def session_state(session_id: str) -> dict:
    try:
        return get_session_state(get_db(), session_id)
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/sessions/{session_id}/dat")
def submit_dat(session_id: str, payload: DatRequest) -> dict:
    try:
        return save_dat_response(get_db(), session_id, payload.model_dump())
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/calibration/eyes-open/start")
def start_eyes_open(payload: CalibrationStartRequest) -> dict:
    try:
        return calibration_manager.start("eyes_open", payload.session_id).snapshot()
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/calibration/eyes-closed/start")
def start_eyes_closed(payload: CalibrationStartRequest) -> dict:
    try:
        return calibration_manager.start("eyes_closed", payload.session_id).snapshot()
    except Exception as exc:
        raise _http_error(exc)


@app.get("/api/calibration/{run_id}/events")
async def calibration_events(run_id: str) -> StreamingResponse:
    run = calibration_manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="CALIBRATION_RUN_NOT_FOUND")

    async def event_stream():
        yield calibration_sse(run.snapshot())
        while True:
            if run.snapshot()["status"] in CALIBRATION_TERMINAL_STATES and run.queue.empty():
                break
            try:
                event = await asyncio.wait_for(run.queue.get(), timeout=15)
                yield calibration_sse(event)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/sessions/{session_id}/next-trial")
def next_trial_endpoint(session_id: str) -> dict:
    try:
        return next_trial(get_db(), session_id)
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/trials/{trial_id}/events")
def trial_events(trial_id: str, payload: TrialEventsRequest) -> dict:
    try:
        return write_trial_events(get_db(), trial_id, payload.model_dump())
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/trials/{trial_id}/controller-decision")
def trial_controller_decision(trial_id: str, payload: ControllerDecisionRequest) -> dict:
    try:
        return controller_decision(get_db(), trial_id, payload.model_dump())
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/trials/{trial_id}/complete")
def complete_trial_endpoint(trial_id: str, payload: TrialCompletionRequest) -> dict:
    try:
        return complete_trial(get_db(), trial_id, payload.model_dump())
    except Exception as exc:
        raise _http_error(exc)


@app.post("/api/sessions/{session_id}/closing-ratings")
def closing_ratings(session_id: str, payload: ClosingRatingsRequest) -> dict:
    try:
        return save_closing_ratings(get_db(), session_id, payload.ratings)
    except Exception as exc:
        raise _http_error(exc)


@app.get("/api/export/{session_id}.{fmt}", response_model=None)
def export_endpoint(session_id: str, fmt: str):
    try:
        payload = export_session(get_db(), session_id, fmt)
        if fmt == "csv":
            return Response(
                payload,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
            )
        return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="session-{session_id}.json"'})
    except Exception as exc:
        raise _http_error(exc)


def _http_error(exc: Exception) -> HTTPException:
    message = str(exc)
    status = 500
    if message.endswith("_NOT_FOUND"):
        status = 404
    elif message in {
        "FORMAL_MATERIALS_NOT_READY",
        "FOUR_SENTENCE_REQUIREMENT_NOT_MET",
        "AGE_OUT_OF_RANGE",
        "PARTICIPANT_ID_OUT_OF_SCHEDULE_RANGE",
        "INVALID_SESSION_MODE",
        "INVALID_CONTROLLER_MODE",
        "INVALID_CALIBRATION_TYPE",
    } or message.startswith("NOT_ENOUGH_") or message.startswith("MATERIAL_SLOT_NOT_FOUND"):
        status = 409
    elif message.startswith("Unsupported material") or message.startswith("Missing required"):
        status = 422
    return HTTPException(status_code=status, detail=message)

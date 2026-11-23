"""HTTP API for clinicians: ask about one patient, or summarise their history.

Every request must name the user (X-User header), and every answer is written
to the audit log. In this local version the header is trusted; the AWS version
takes the user from a verified Keycloak token instead, and limits each
clinician to their own patients.
"""

import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clinical_rag import answer as qa
from clinical_rag.clients import FhirClient, ModelClient
from clinical_rag.config import Settings
from clinical_rag.store import Store
from clinical_rag.suggestions import suggest

PATIENT_ID = re.compile(r"[A-Za-z0-9\-.]{1,64}")
state: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = Settings.from_env()
    state["settings"] = settings
    state["store"] = Store(settings.database_url, settings.embed_dimensions)
    state["store"].init_schema()
    state["fhir"] = FhirClient(settings.fhir_url)
    state["chat"] = ModelClient(settings.llm_base_url, settings.llm_model, settings.llm_backend)
    state["embed"] = ModelClient(settings.embed_base_url, settings.embed_model)
    yield


app = FastAPI(title="private-clinical-ai", version="0.2.0", lifespan=lifespan)
# The clinician page: plain HTML and JavaScript, served from here so it needs nothing from the internet.
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def current_user(x_user: str | None = Header(default=None)) -> str:
    if not x_user or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", x_user):
        raise HTTPException(status_code=401, detail="X-User header required")
    return x_user


def checked_patient(patient_id: str) -> str:
    if not PATIENT_ID.fullmatch(patient_id):
        raise HTTPException(status_code=400, detail="invalid patient id")
    if not state["store"].by_kind(patient_id, ["patient"], 1):
        raise HTTPException(status_code=404, detail="patient not indexed")
    return patient_id


def respond(result: qa.Answer, user: str, patient_id: str, action: str, question: str | None) -> dict:
    model = state["settings"].llm_model
    state["store"].audit(user, patient_id, action, question, result.chunks_used, result.latency_ms, model)
    return {
        "patient_id": patient_id,
        "answer": result.answer,
        "sources": result.sources,
        "invalid_citations": result.invalid_citations,
        "records_used": result.chunks_used,
        "latency_ms": result.latency_ms,
        "model": model,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "indexed_patients": state["store"].indexed_patients(), "model": state["settings"].llm_model}


@app.get("/patients")
def patients(_user: str = Depends(current_user)) -> list[dict]:
    """Synthetic patients, for picking one to ask about."""
    return [
        {
            "id": p["id"],
            "gender": p.get("gender"),
            "birth_date": p.get("birthDate"),
            "deceased": "deceasedDateTime" in p,
        }
        for p in state["fhir"].patients()
    ]


@app.post("/patients/{patient_id}/ask")
def ask(patient_id: str, body: Question, user: str = Depends(current_user)) -> dict:
    pid = checked_patient(patient_id)
    k = state["settings"].top_k
    result = qa.ask(state["store"], state["chat"], state["embed"], pid, body.question, k)
    return respond(result, user, pid, "ask", body.question)


@app.post("/patients/{patient_id}/summary")
def summary(patient_id: str, user: str = Depends(current_user)) -> dict:
    pid = checked_patient(patient_id)
    started = time.monotonic()
    result = qa.summarise(state["store"], state["chat"], pid)
    result.latency_ms = result.latency_ms or int((time.monotonic() - started) * 1000)
    return respond(result, user, pid, "summary", None)


@app.get("/patients/{patient_id}/suggestions")
def suggestions(patient_id: str, user: str = Depends(current_user)) -> dict:
    """Questions built from this patient's own records (no model involved). Reading a record's contents is
    access to it, so this is audited like a question."""
    pid = checked_patient(patient_id)
    started = time.monotonic()
    records = state["store"].outline(pid)
    questions = suggest(records)
    latency_ms = int((time.monotonic() - started) * 1000)
    state["store"].audit(user, pid, "suggestions", None, len(records), latency_ms, "none (rules)")
    return {"patient_id": pid, "questions": questions}


@app.get("/audit")
def audit(_user: str = Depends(current_user)) -> list[dict]:
    """Recent audit entries: who asked about which patient. Never the question or the answer."""
    return state["store"].recent_audit()

"""API tests with fakes in place of the database and the model.

The TestClient is used without its context manager, so the startup hook that
connects to PostgreSQL and the model servers never runs.
"""

import pytest
from fastapi.testclient import TestClient

from clinical_rag import api
from clinical_rag.config import Settings


class FakeStore:
    def __init__(self):
        self.audited = []

    def by_kind(self, patient_id, kinds, limit):
        if patient_id != "7945":
            return []
        return [{"kind": "patient", "date": "1967-01-03", "content": "Patient: male.", "refs": ["Patient/7945"]}]

    def current(self, patient_id):
        return []

    def outline(self, patient_id):
        return [
            {
                "kind": "condition",
                "date": "2024-06-01",
                "content": "Condition: Pulmonary tuberculosis (disorder). Onset 2024-06-01. Clinical status: active.",
            }
        ]

    def search(self, patient_id, embedding, k):
        return [
            {"kind": "condition", "date": "2025-04-08", "content": "Condition: Diabetes.", "refs": ["Condition/c1"]}
        ]

    def audit(self, user_id, patient_id, action, question, chunks_used, latency_ms, model):
        self.audited.append({"user": user_id, "patient": patient_id, "action": action, "question": question})


class FakeModel:
    def chat(self, messages, max_tokens=800):
        return "Diagnosed on 2025-04-08 [2]."

    def embed(self, texts):
        return [[0.0, 1.0] for _ in texts]


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(
        api,
        "state",
        {"settings": Settings.from_env(), "store": store, "chat": FakeModel(), "embed": FakeModel(), "fhir": None},
    )
    return TestClient(api.app), store


def test_every_request_must_name_the_user(client):
    http, _ = client

    response = http.post("/patients/7945/ask", json={"question": "When was diabetes diagnosed?"})

    assert response.status_code == 401


def test_patient_ids_are_validated(client):
    http, _ = client

    response = http.post("/patients/7945;DROP/ask", headers={"X-User": "dr.a"}, json={"question": "anything?"})

    assert response.status_code == 400


def test_unknown_patients_are_not_answered(client):
    http, _ = client

    response = http.post("/patients/1/ask", headers={"X-User": "dr.a"}, json={"question": "anything?"})

    assert response.status_code == 404


def test_answers_carry_their_sources_and_are_audited(client):
    http, store = client

    response = http.post(
        "/patients/7945/ask", headers={"X-User": "dr.a"}, json={"question": "When was diabetes diagnosed?"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["sources"][0]["refs"] == ["Condition/c1"]
    assert store.audited == [
        {"user": "dr.a", "patient": "7945", "action": "ask", "question": "When was diabetes diagnosed?"}
    ]


def test_overlong_questions_are_rejected(client):
    http, _ = client

    response = http.post("/patients/7945/ask", headers={"X-User": "dr.a"}, json={"question": "x" * 501})

    assert response.status_code == 422


def test_suggestions_come_from_the_patients_records_and_are_audited(client):
    http, store = client

    response = http.get("/patients/7945/suggestions", headers={"X-User": "dr.a"})

    assert response.status_code == 200
    assert response.json()["questions"][0] == "When was pulmonary tuberculosis diagnosed?"
    assert store.audited == [{"user": "dr.a", "patient": "7945", "action": "suggestions", "question": None}]


def test_suggestions_need_a_user_and_an_indexed_patient(client):
    http, _ = client

    assert http.get("/patients/7945/suggestions").status_code == 401
    assert http.get("/patients/1/suggestions", headers={"X-User": "dr.a"}).status_code == 404

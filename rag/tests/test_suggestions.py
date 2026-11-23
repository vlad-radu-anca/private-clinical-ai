import json
from pathlib import Path

from clinical_rag.documents import build_chunks
from clinical_rag.suggestions import suggest

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "handmade-patient.json"


def coded(display, code="x"):
    return {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": display}]}


def condition(cid, name, onset, active=True):
    resource = {
        "resourceType": "Condition",
        "id": cid,
        "code": coded(name),
        "onsetDateTime": f"{onset}T10:00:00+00:00",
        "clinicalStatus": {"coding": [{"code": "active" if active else "resolved"}]},
    }
    if not active:
        resource["abatementDateTime"] = f"{onset}T20:00:00+00:00"
    return resource


def medication(mid, name, authored, status="active"):
    return {
        "resourceType": "MedicationRequest",
        "id": mid,
        "status": status,
        "authoredOn": f"{authored}T10:00:00+00:00",
        "medicationCodeableConcept": coded(name),
    }


def observation(oid, name, when, value):
    return {
        "resourceType": "Observation",
        "id": oid,
        "code": coded(name),
        "effectiveDateTime": f"{when}T10:00:00+00:00",
        "valueQuantity": {"value": value, "unit": "x"},
    }


def questions_for(*resources):
    patient = {"resourceType": "Patient", "id": "p1", "gender": "female", "birthDate": "1970-01-01"}
    chunks = build_chunks([patient, *resources])
    return suggest([{"kind": c.kind, "date": c.date, "content": c.text} for c in chunks])


def test_questions_name_the_patients_own_diagnoses_newest_first():
    questions = questions_for(
        condition("c1", "Essential hypertension (disorder)", "2015-03-01"),
        condition("c2", "Pulmonary tuberculosis (disorder)", "2024-06-01"),
    )

    assert questions[:2] == ["When was pulmonary tuberculosis diagnosed?", "When was essential hypertension diagnosed?"]


def test_diseases_the_patient_never_had_are_not_suggested():
    questions = questions_for(condition("c1", "Pulmonary tuberculosis (disorder)", "2024-06-01"))

    assert not any("diabetes" in q for q in questions)


def test_social_findings_are_not_treated_as_diagnoses():
    questions = questions_for(
        condition("c1", "Full-time employment (finding)", "2020-01-01"),
        condition("c2", "Medication review due (situation)", "2024-01-01"),
    )

    assert not any("employment" in q or "review" in q for q in questions)
    assert "Which conditions has the patient been diagnosed with?" in questions


def test_a_resolved_illness_is_asked_about_as_past():
    questions = questions_for(condition("c1", "Acute bronchitis (disorder)", "2022-02-01", active=False))

    assert "When did the patient have acute bronchitis, and when did it resolve?" in questions
    assert "When was acute bronchitis diagnosed?" not in questions


def test_current_medicines_use_the_name_a_person_would_say():
    questions = questions_for(
        medication("m1", "24 HR metformin hydrochloride 500 MG Extended Release Oral Tablet", "2023-01-01"),
        medication("m2", "Amoxicillin 250 MG Oral Capsule", "2019-01-01", status="stopped"),
    )

    assert "Which medications is the patient currently taking?" in questions
    assert "Since when has the patient been taking metformin hydrochloride?" in questions
    assert not any("Amoxicillin" in q for q in questions)


def test_brand_names_are_preferred():
    questions = questions_for(medication("m1", "Insulin isophane 70 UNT/ML [Humulin]", "2023-01-01"))

    assert "Since when has the patient been taking Humulin?" in questions


def test_the_most_telling_series_becomes_a_trend_question():
    questions = questions_for(
        observation("o1", "Body Weight", "2024-01-01", 80),
        observation("o2", "Hemoglobin A1c/Hemoglobin.total in Blood", "2024-01-01", 6.1),
    )

    assert "How has the patient's HbA1c changed over time?" in questions
    assert not any("body weight" in q for q in questions)


def test_no_more_than_six_and_no_repeats():
    questions = questions_for(
        *[condition(f"c{i}", f"Disease {i} (disorder)", f"20{10 + i}-01-01") for i in range(5)],
        medication("m1", "Lisinopril 10 MG Oral Tablet", "2023-01-01"),
        observation("o1", "Blood pressure panel with all children optional", "2024-01-01", 120),
        observation("o2", "Body mass index (BMI) [Ratio]", "2024-01-01", 31),
        {"resourceType": "AllergyIntolerance", "id": "a1", "code": coded("Peanut (substance)"), "criticality": "high"},
    )

    assert len(questions) == 6
    assert len(set(questions)) == len(questions)
    assert "Which allergies does the patient have, and how critical are they?" in questions


def test_the_handmade_example_gets_questions_about_its_own_contents():
    bundle = json.loads(EXAMPLE.read_text())
    resources = [entry["resource"] for entry in bundle["entry"]]
    for resource in resources:
        resource.setdefault("id", "h1")  # HAPI assigns IDs on load
    chunks = build_chunks(resources)

    questions = suggest([{"kind": c.kind, "date": c.date, "content": c.text} for c in chunks])

    assert "When was diabetes mellitus type 2 diagnosed?" in questions
    assert "How has the patient's HbA1c changed over time?" in questions

import base64

import pytest

from clinical_rag.documents import ACTIVE_CONDITION, ACTIVE_MEDICATION, MAX_VALUES_PER_SERIES, build_chunks


def patient(**extra):
    return {"resourceType": "Patient", "id": "p1", "gender": "male", "birthDate": "1967-01-03", **extra}


def coded(display=None, code="x"):
    coding = {"system": "http://snomed.info/sct", "code": code}
    if display:
        coding["display"] = display
    return {"coding": [coding]}


def hba1c(obs_id, when, value):
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "code": coded("Hemoglobin A1c", "4548-4"),
        "effectiveDateTime": f"{when}T10:00:00+00:00",
        "valueQuantity": {"value": value, "unit": "%"},
    }


def by_kind(chunks, kind):
    return [c for c in chunks if c.kind == kind]


def test_condition_uses_the_status_code_when_there_is_no_display_text():
    # Synthea's clinicalStatus is {"code": "active"} with no display: it must not read as "unknown".
    cond = {
        "resourceType": "Condition",
        "id": "c1",
        "code": coded("Diabetes mellitus type 2 (disorder)", "44054006"),
        "onsetDateTime": "2025-04-08T10:00:00+00:00",
        "clinicalStatus": {"coding": [{"code": "active"}]},
    }
    (chunk,) = by_kind(build_chunks([patient(), cond]), "condition")

    assert chunk.text == "Condition: Diabetes mellitus type 2 (disorder). Onset 2025-04-08. Clinical status: active."
    assert chunk.refs == ["Condition/c1"]


def test_condition_with_a_display_text_still_counts_as_active():
    # Data from other systems usually carries a display ("Active"); the code decides, so it stays current.
    cond = {
        "resourceType": "Condition",
        "id": "c1",
        "code": coded("Essential hypertension (disorder)", "59621000"),
        "clinicalStatus": {"coding": [{"code": "active", "display": "Active"}]},
    }
    (chunk,) = by_kind(build_chunks([patient(), cond]), "condition")

    assert ACTIVE_CONDITION in chunk.text


def test_observations_are_grouped_per_code_in_date_order():
    resources = [patient(), hba1c("o2", "2025-04-08", 6.79), hba1c("o1", "2022-11-08", 6.31)]

    (chunk,) = by_kind(build_chunks(resources), "observation")

    assert "2022-11-08 6.31 %; 2025-04-08 6.79 %" in chunk.text
    assert chunk.refs == ["Observation/o1", "Observation/o2"]
    assert chunk.date == "2025-04-08"


def test_long_series_keep_the_most_recent_values_and_say_how_many_were_dropped():
    values = [hba1c(f"o{i}", f"20{10 + i}-01-01", 5.0 + i / 10) for i in range(MAX_VALUES_PER_SERIES + 3)]

    (chunk,) = by_kind(build_chunks([patient(), *values]), "observation")

    assert len(chunk.refs) == MAX_VALUES_PER_SERIES
    assert "3 earlier not shown" in chunk.text
    assert chunk.refs[-1] == f"Observation/o{MAX_VALUES_PER_SERIES + 2}"


def test_blood_pressure_components_are_kept():
    bp = {
        "resourceType": "Observation",
        "id": "bp",
        "code": coded("Blood pressure panel", "85354-9"),
        "effectiveDateTime": "2024-01-01T10:00:00+00:00",
        "component": [
            {"code": coded("Systolic Blood Pressure"), "valueQuantity": {"value": 128, "unit": "mm[Hg]"}},
            {"code": coded("Diastolic Blood Pressure"), "valueQuantity": {"value": 82, "unit": "mm[Hg]"}},
        ],
    }

    (chunk,) = by_kind(build_chunks([patient(), bp]), "observation")

    assert "Systolic Blood Pressure 128 mm[Hg], Diastolic Blood Pressure 82 mm[Hg]" in chunk.text


def test_billing_resources_are_skipped():
    claim = {"resourceType": "Claim", "id": "cl1"}
    eob = {"resourceType": "ExplanationOfBenefit", "id": "e1"}

    chunks = build_chunks([patient(), claim, eob])

    assert [c.kind for c in chunks] == ["patient"]


def test_clinical_notes_are_decoded_and_split():
    text = "# Chief Complaint\n- Blurred vision\n" + "x" * 2000
    note = {
        "resourceType": "DocumentReference",
        "id": "d1",
        "date": "2024-04-02T10:00:00+00:00",
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain; charset=utf-8",
                    "data": base64.b64encode(text.encode()).decode(),
                }
            }
        ],
    }

    notes = by_kind(build_chunks([patient(), note]), "note")

    assert len(notes) == 2
    assert notes[0].text.startswith("Clinical note 2024-04-02: # Chief Complaint")
    assert all(n.refs == ["DocumentReference/d1"] for n in notes)


def test_a_record_must_belong_to_exactly_one_patient():
    with pytest.raises(ValueError):
        build_chunks([patient(), patient(id="p2")])


def test_active_records_are_marked_with_the_phrases_the_store_selects_on():
    cond = {
        "resourceType": "Condition",
        "id": "c1",
        "code": coded("Hypertension"),
        "onsetDateTime": "2016-01-01T00:00:00+00:00",
        "clinicalStatus": {"coding": [{"code": "active"}]},
    }
    med = {
        "resourceType": "MedicationRequest",
        "id": "m1",
        "status": "active",
        "authoredOn": "2026-04-30T10:00:00+00:00",
        "medicationCodeableConcept": coded("lisinopril 10 MG Oral Tablet"),
    }
    stopped = {**med, "id": "m2", "status": "completed"}

    chunks = build_chunks([patient(), cond, med, stopped])

    texts = {c.refs[0]: c.text for c in chunks}
    assert texts["Condition/c1"].endswith(ACTIVE_CONDITION)
    assert ACTIVE_MEDICATION in texts["MedicationRequest/m1"]
    assert ACTIVE_MEDICATION not in texts["MedicationRequest/m2"]


def test_the_handmade_example_patient_is_fully_indexed():
    # docs/examples/handmade-patient.json is the example in docs/working-with-data.md: every kind the
    # index understands, written the way most systems write FHIR (display texts, plain dates).
    import json
    from pathlib import Path

    bundle = json.loads((Path(__file__).parents[2] / "docs/examples/handmade-patient.json").read_text())
    resources = [dict(entry["resource"], id=f"r{i}") for i, entry in enumerate(bundle["entry"])]
    resources[0]["id"] = "p1"

    chunks = build_chunks(resources)
    kinds = sorted(c.kind for c in chunks)

    expected = ["allergy", "condition", "condition", "immunization", "medication", "observation", "observation"]
    assert kinds == [*expected, "patient"]
    assert all(ACTIVE_CONDITION in c.text for c in by_kind(chunks, "condition"))
    assert ACTIVE_MEDICATION in by_kind(chunks, "medication")[0].text
    (hba1c_series,) = [c for c in by_kind(chunks, "observation") if "Hemoglobin A1c" in c.text]
    assert "2024-01-15 7.4 %; 2025-01-20 6.9 %" in hba1c_series.text
    (pressure,) = [c for c in by_kind(chunks, "observation") if "Blood pressure" in c.text]
    assert "Systolic blood pressure 138 mm[Hg], Diastolic blood pressure 86 mm[Hg]" in pressure.text

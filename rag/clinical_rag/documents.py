"""Turn a patient's FHIR resources into chunks of text that can be searched and cited.

Every chunk keeps the references of the FHIR resources it was built from, so an
answer can point at the exact records behind each statement.

Observations, procedures and immunizations repeat the same code many times over
a patient's life, so they are grouped per code into one chunk with the values in
date order. That keeps the index small and makes trends ("how did the HbA1c
change") a single retrieval instead of dozens. Billing resources (Claim,
ExplanationOfBenefit) carry no clinical meaning and are skipped.
"""

import base64
from collections import defaultdict
from dataclasses import dataclass, field

NOTE_CHUNK_CHARS = 1500
MAX_VALUES_PER_SERIES = 12

# Phrases that mark a record as current. The store selects current conditions and medications by these,
# so they are defined once, here, next to the code that writes them.
ACTIVE_CONDITION = "Clinical status: active."
ACTIVE_MEDICATION = "Status: active."


@dataclass
class Chunk:
    patient_id: str
    kind: str
    text: str
    refs: list[str]
    date: str | None = None
    metadata: dict = field(default_factory=dict)


def _display(codeable: dict | None) -> str:
    if not codeable:
        return "unknown"
    codings = codeable.get("coding", [])
    for coding in codings:
        if coding.get("display"):
            return coding["display"]
    if codeable.get("text"):
        return codeable["text"]
    # Some codes carry no display text: Synthea's clinicalStatus is just {"code": "active"}.
    return next((c["code"] for c in codings if c.get("code")), "unknown")


def _code_key(codeable: dict | None) -> str:
    coding = (codeable or {}).get("coding", [{}])[0]
    return f"{coding.get('system', '')}|{coding.get('code', '')}|{_display(codeable)}"


def _day(value: str | None) -> str | None:
    return value[:10] if value else None


def _ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _quantity(value: dict) -> str:
    number = value.get("value")
    if isinstance(number, float):
        number = round(number, 2)
    return f"{number} {value.get('unit', '')}".strip()


def _observation_value(obs: dict) -> str | None:
    if "valueQuantity" in obs:
        return _quantity(obs["valueQuantity"])
    if "valueCodeableConcept" in obs:
        return _display(obs["valueCodeableConcept"])
    if "valueString" in obs:
        return obs["valueString"]
    if "component" in obs:
        parts = [
            f"{_display(c.get('code'))} {_quantity(c['valueQuantity'])}"
            for c in obs["component"]
            if "valueQuantity" in c
        ]
        return ", ".join(parts) or None
    return None


def patient_chunk(patient: dict) -> Chunk:
    status = "deceased " + (_day(patient.get("deceasedDateTime")) or "") if patient.get("deceasedDateTime") else "alive"
    text = f"Patient: {patient.get('gender', 'unknown')} born {patient.get('birthDate', 'unknown')}, {status.strip()}."
    return Chunk(patient["id"], "patient", text, [_ref(patient)], patient.get("birthDate"))


def condition_chunk(pid: str, cond: dict) -> Chunk:
    onset = _day(cond.get("onsetDateTime"))
    resolved = _day(cond.get("abatementDateTime"))
    status = _display(cond.get("clinicalStatus"))
    text = f"Condition: {_display(cond.get('code'))}. Onset {onset or 'unknown'}."
    if resolved:
        text += f" Resolved {resolved}."
    elif status == "active":
        text += f" {ACTIVE_CONDITION}"
    else:
        text += f" Clinical status: {status}."
    return Chunk(pid, "condition", text, [_ref(cond)], onset)


def medication_chunk(pid: str, med: dict) -> Chunk:
    name = _display(med.get("medicationCodeableConcept"))
    authored = _day(med.get("authoredOn"))
    dosage = (med.get("dosageInstruction") or [{}])[0].get("text")
    status = med.get("status", "unknown")
    text = f"Medication: {name}. Prescribed {authored or 'unknown'}. "
    text += ACTIVE_MEDICATION if status == "active" else f"Status: {status}."
    if dosage:
        text += f" Dosage: {dosage}."
    return Chunk(pid, "medication", text, [_ref(med)], authored)


def allergy_chunk(pid: str, allergy: dict) -> Chunk:
    text = f"Allergy: {_display(allergy.get('code'))}. Criticality: {allergy.get('criticality', 'unknown')}."
    return Chunk(pid, "allergy", text, [_ref(allergy)], _day(allergy.get("recordedDate")))


def series_chunks(pid: str, resources: list[dict], kind: str, label: str) -> list[Chunk]:
    """Group repeated observations, procedures or immunizations per code, values in date order."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for res in resources:
        code = res.get("vaccineCode") if kind == "immunization" else res.get("code")
        groups[_code_key(code)].append(res)

    chunks = []
    for key, items in groups.items():
        dated = []
        for res in items:
            when = _day(res.get("effectiveDateTime") or res.get("performedDateTime") or res.get("occurrenceDateTime"))
            if kind == "procedure" and not when:
                when = _day((res.get("performedPeriod") or {}).get("start"))
            value = _observation_value(res) if kind == "observation" else None
            dated.append((when or "unknown", value, _ref(res)))
        dated.sort()
        recent = dated[-MAX_VALUES_PER_SERIES:]
        name = key.split("|", 2)[2]
        entries = "; ".join(f"{when} {value}" if value else when for when, value, _ in recent)
        earlier = f" ({len(dated) - len(recent)} earlier not shown)" if len(dated) > len(recent) else ""
        text = f"{label}: {name}. {len(dated)} records{earlier}: {entries}."
        chunks.append(Chunk(pid, kind, text, [ref for _, _, ref in recent], recent[-1][0]))
    return chunks


def note_chunks(pid: str, doc: dict) -> list[Chunk]:
    """Clinical notes, split into pieces small enough to embed and retrieve precisely."""
    chunks = []
    for content in doc.get("content", []):
        attachment = content.get("attachment", {})
        if not attachment.get("contentType", "").startswith("text/plain") or "data" not in attachment:
            continue
        text = base64.b64decode(attachment["data"]).decode("utf-8", errors="replace").strip()
        date = _day(doc.get("date"))
        for start in range(0, len(text), NOTE_CHUNK_CHARS):
            piece = text[start : start + NOTE_CHUNK_CHARS].strip()
            if piece:
                chunks.append(Chunk(pid, "note", f"Clinical note {date}: {piece}", [_ref(doc)], date))
    return chunks


def build_chunks(resources: list[dict]) -> list[Chunk]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for res in resources:
        by_type[res["resourceType"]].append(res)

    patients = by_type.get("Patient", [])
    if len(patients) != 1:
        raise ValueError(f"expected exactly one Patient, got {len(patients)}")
    pid = patients[0]["id"]

    chunks = [patient_chunk(patients[0])]
    chunks += [condition_chunk(pid, r) for r in by_type.get("Condition", [])]
    chunks += [medication_chunk(pid, r) for r in by_type.get("MedicationRequest", [])]
    chunks += [allergy_chunk(pid, r) for r in by_type.get("AllergyIntolerance", [])]
    chunks += series_chunks(pid, by_type.get("Observation", []), "observation", "Observation")
    chunks += series_chunks(pid, by_type.get("Procedure", []), "procedure", "Procedure")
    chunks += series_chunks(pid, by_type.get("Immunization", []), "immunization", "Immunization")
    for doc in by_type.get("DocumentReference", []):
        chunks += note_chunks(pid, doc)
    return chunks

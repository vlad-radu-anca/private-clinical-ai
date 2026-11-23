"""Questions worth asking about one patient, built from that patient's own indexed records.

A fixed list of example questions ("When was diabetes diagnosed?") fits some patients and reads as noise for the
rest. These come from what is actually in the record: the patient's diagnoses, current prescriptions, the most
telling measurement series and any allergies. Plain rules over the chunk texts, no model call: instant, free, and
every suggestion names something the record contains.

The patterns below read the sentences written by documents.py; the tests build their records with documents.py
too, so a change in either breaks a test instead of silently emptying the suggestions.
"""

import re

from clinical_rag.documents import ACTIVE_CONDITION, ACTIVE_MEDICATION

MAX_SUGGESTIONS = 6

CONDITION = re.compile(r"^Condition: (?P<name>.+)\. Onset ")
MEDICATION = re.compile(r"^Medication: (?P<name>.+)\. Prescribed ")
ALLERGY = re.compile(r"^Allergy: (?P<name>.+)\. Criticality: ")
SERIES = re.compile(r"^Observation: (?P<name>.+)\. (?P<count>\d+) records")

# SNOMED CT ends each name with its semantic tag. Diagnoses are "(disorder)"; most "(finding)" and "(situation)"
# entries in Synthea are social or administrative ("Full-time employment", "Medication review due"), which make
# poor questions. Names without a tag (common in hand-written data) are kept.
SEMANTIC_TAG = re.compile(r"\s*\((?P<tag>[a-z][a-z ]*)\)$")
DIAGNOSIS_TAGS = {None, "disorder"}

# Measurement series in order of how much they say about a patient, with a readable label. The first one present
# becomes a trend question.
TRENDS = [
    (re.compile(r"hemoglobin a1c|hba1c", re.I), "HbA1c"),
    (re.compile(r"glomerular filtration", re.I), "kidney function (eGFR)"),
    (re.compile(r"^cholesterol \[", re.I), "total cholesterol"),
    (re.compile(r"body mass index", re.I), "BMI"),
    (re.compile(r"^body weight", re.I), "body weight"),
]
BLOOD_PRESSURE = re.compile(r"blood pressure", re.I)

# RxNorm names carry the strength and form: "24 HR metformin hydrochloride 500 MG Extended Release Oral Tablet".
BRAND = re.compile(r"\[(?P<brand>[^\]]+)\]")
LEADING_AMOUNT = re.compile(r"^(?:[\d.]+ [A-Z/]+ )+")


def _plain(name: str) -> tuple[str, str | None]:
    """The name without its semantic tag, and the tag."""
    match = SEMANTIC_TAG.search(name)
    if not match:
        return name, None
    return name[: match.start()], match.group("tag")


def _in_sentence(name: str) -> str:
    """Lower-case the first letter for use mid-sentence, unless the name starts with an acronym (COVID-19)."""
    return name[0].lower() + name[1:] if name[:1].isupper() and not name[:2].isupper() else name


def _drug(name: str) -> str:
    """The drug as a person would say it: the brand if there is one, else the name before the strength."""
    if brand := BRAND.search(name):
        return brand.group("brand")
    name = LEADING_AMOUNT.sub("", name)
    return re.split(r" [\d.]+ ", name, maxsplit=1)[0]


def _newest_first(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: r.get("date") or "", reverse=True)


def suggest(records: list[dict], limit: int = MAX_SUGGESTIONS) -> list[str]:
    """Questions for one patient from their condition, medication, allergy and observation records."""
    active, resolved, current_meds, allergies, series = [], [], [], [], []
    for record in _newest_first(records):
        text = record["content"]
        if record["kind"] == "condition" and (m := CONDITION.match(text)):
            name, tag = _plain(m.group("name"))
            if tag in DIAGNOSIS_TAGS:
                (active if text.endswith(ACTIVE_CONDITION) else resolved).append(name)
        elif record["kind"] == "medication" and ACTIVE_MEDICATION in text and (m := MEDICATION.match(text)):
            current_meds.append(_drug(m.group("name")))
        elif record["kind"] == "allergy" and (m := ALLERGY.match(text)):
            allergies.append(m.group("name"))
        elif record["kind"] == "observation" and (m := SERIES.match(text)):
            series.append(m.group("name"))

    past = [n for n in resolved if n not in active]
    trend = next((label for pattern, label in TRENDS if any(pattern.search(s) for s in series)), None)

    # In order of importance, so the limit drops the least useful ones.
    questions = [f"When was {_in_sentence(name)} diagnosed?" for name in active[:2]]
    if current_meds:
        questions.append("Which medications is the patient currently taking?")
    if allergies:
        questions.append("Which allergies does the patient have, and how critical are they?")
    if trend:
        questions.append(f"How has the patient's {trend} changed over time?")
    if past:
        questions.append(f"When did the patient have {_in_sentence(past[0])}, and when did it resolve?")
    if not active and not past:
        questions.append("Which conditions has the patient been diagnosed with?")
    if current_meds:
        questions.append(f"Since when has the patient been taking {current_meds[0]}?")
    if any(BLOOD_PRESSURE.search(s) for s in series):
        questions.append("What was the most recent blood pressure?")

    return list(dict.fromkeys(questions))[:limit]

"""Evaluate the running RAG API against answers known from the FHIR data.

    python -m clinical_rag.evals --patients 10
    python -m clinical_rag.evals --patients 10 --min-score 0.8   # exit 1 below 80%

Expected answers are computed from the structured FHIR records, never from the
model, so the suite measures the whole pipeline: retrieval, prompt and model.
For each patient it asks:

- diagnosis:  "When was the patient diagnosed with X?" must give the onset date and cite that Condition.
- medication: "Has the patient been prescribed Y?" must confirm it.
- current:    "Which medications is the patient currently taking?" must name every active prescription.
              A list question: similarity search alone returns only the closest chunks and misses some.
- lab:        "What was the most recent Z?" must give the latest value.
- absent:     a condition the patient does not have must be answered with "not found". This is the
              hallucination test: a confident answer about a disease the patient never had fails.

A citation to a record the model was not given fails any case.
"""

import argparse
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field

import httpx

from clinical_rag.answer import NOT_FOUND
from clinical_rag.clients import FhirClient
from clinical_rag.config import Settings
from clinical_rag.documents import _day, _display, _quantity

# Administrative or generic entries that are not meaningful diagnoses to ask about.
SKIP_CONDITIONS = ("(situation)", "(finding)", "Medication review due", "Full-time employment", "Part-time employment")


@dataclass
class Case:
    patient_id: str
    kind: str
    question: str
    expect: str
    expect_ref: str | None = None
    expect_all: list[str] = field(default_factory=list)
    passed: bool = False
    answer: str = ""
    seconds: float = 0.0


def drug_key(name: str) -> str:
    """The word that identifies a drug in an answer: the first real word of its name.

    Medication names start with packaging and codes ("24 HR Metformin...", "NDA020800 0.3 ML Epinephrine...",
    "{7 (Ethinyl Estradiol...") that an answer may rightly leave out, so those are skipped.
    """
    words = re.findall(r"[A-Za-z][A-Za-z-]+", name)
    skip = {"hr", "ml", "mg", "actuat", "abuse-deterrent", "day", "pack"}
    for word in words:
        lower = word.lower()
        if lower not in skip and not lower.startswith("nda") and len(lower) >= 4:
            return lower
    return words[0].lower() if words else name.lower()


def is_diagnosis(cond: dict) -> bool:
    name = _display(cond.get("code"))
    return bool(cond.get("onsetDateTime")) and not any(s in name for s in SKIP_CONDITIONS)


def build_cases(resources: list[dict], all_diagnoses: set[str], rng: random.Random) -> list[Case]:
    pid = next(r["id"] for r in resources if r["resourceType"] == "Patient")
    by_type: dict[str, list[dict]] = {}
    for r in resources:
        by_type.setdefault(r["resourceType"], []).append(r)
    cases = []

    diagnoses = [c for c in by_type.get("Condition", []) if is_diagnosis(c)]
    names = {_display(c.get("code")) for c in diagnoses}
    # Only names that occur once, so "the" onset date is unambiguous.
    unique = [c for c in diagnoses if sum(_display(d.get("code")) == _display(c.get("code")) for d in diagnoses) == 1]
    if unique:
        cond = rng.choice(unique)
        name = _display(cond.get("code"))
        cases.append(
            Case(
                pid,
                "diagnosis",
                f"When was the patient diagnosed with {name}?",
                _day(cond["onsetDateTime"]),
                f"Condition/{cond['id']}",
            )
        )

    meds = by_type.get("MedicationRequest", [])
    if meds:
        med = _display(rng.choice(meds).get("medicationCodeableConcept"))
        cases.append(Case(pid, "medication", f"Has the patient ever been prescribed {med}?", drug_key(med)))

    active = sorted(
        {drug_key(_display(m.get("medicationCodeableConcept"))) for m in meds if m.get("status") == "active"}
    )
    if active:
        cases.append(
            Case(
                pid,
                "current",
                "Which medications is the patient currently taking?",
                ", ".join(active),
                expect_all=active,
            )
        )

    series: dict[str, list[tuple[str, dict]]] = {}
    for obs in by_type.get("Observation", []):
        if "valueQuantity" in obs and obs.get("effectiveDateTime"):
            series.setdefault(_display(obs.get("code")), []).append((obs["effectiveDateTime"], obs))
    if series:
        name, values = rng.choice(sorted(series.items()))
        latest = max(values, key=lambda v: v[0])[1]["valueQuantity"]
        number = _quantity(latest).split()[0]
        cases.append(Case(pid, "lab", f"What was the most recent value of {name}?", number))

    absent = sorted(all_diagnoses - names)
    if absent:
        other = rng.choice(absent)
        cases.append(Case(pid, "absent", f"When was the patient diagnosed with {other}?", NOT_FOUND))
    return cases


def grade(case: Case, response: dict) -> bool:
    if response.get("invalid_citations"):
        return False
    answer = response["answer"]
    if case.kind == "absent":
        return answer.strip() == NOT_FOUND
    if answer.strip() == NOT_FOUND:
        return False
    if case.kind == "current":
        return all(name in answer.lower() for name in case.expect_all)
    if case.kind == "diagnosis":
        cited_refs = {ref for source in response["sources"] for ref in source["refs"]}
        return case.expect in answer and case.expect_ref in cited_refs
    return case.expect in answer.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--patients", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--output", default="", help="write detailed results as JSON to this file")
    args = parser.parse_args()

    settings = Settings.from_env()
    fhir = FhirClient(settings.fhir_url)
    rng = random.Random(args.seed)
    ids = sorted(fhir.patient_ids(), key=int)[: args.patients]
    records = {pid: fhir.everything(pid) for pid in ids}
    all_diagnoses = {
        _display(r.get("code"))
        for resources in records.values()
        for r in resources
        if r["resourceType"] == "Condition" and is_diagnosis(r)
    }
    cases = [c for pid in ids for c in build_cases(records[pid], all_diagnoses, rng)]

    api = httpx.Client(base_url=args.api, timeout=300, headers={"X-User": "eval-suite"})
    for case in cases:
        start = time.monotonic()
        response = api.post(f"/patients/{case.patient_id}/ask", json={"question": case.question}).json()
        case.seconds = round(time.monotonic() - start, 1)
        case.answer = response.get("answer", "")
        case.passed = grade(case, response)
        print(f"{'PASS' if case.passed else 'FAIL'}  {case.kind:<10} patient {case.patient_id:<6} {case.seconds:>5}s")

    print("\nscore by kind:")
    for kind in ("diagnosis", "medication", "current", "lab", "absent"):
        subset = [c for c in cases if c.kind == kind]
        if subset:
            print(f"  {kind:<10} {sum(c.passed for c in subset)}/{len(subset)}")
    score = sum(c.passed for c in cases) / len(cases)
    print(f"  overall    {sum(c.passed for c in cases)}/{len(cases)} = {score:.0%}")
    if args.output:
        with open(args.output, "w") as fh:
            json.dump([asdict(c) for c in cases], fh, indent=2)
    return 0 if score >= args.min_score else 1


if __name__ == "__main__":
    sys.exit(main())

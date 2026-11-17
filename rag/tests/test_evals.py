"""The grader has to be right before its score means anything."""

import pytest

from clinical_rag.answer import NOT_FOUND
from clinical_rag.evals import Case, drug_key, grade


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet", "metformin"),
        ("NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector", "epinephrine"),
        ("Abuse-Deterrent 12 HR Oxycodone Hydrochloride 10 MG Extended Release Oral Tablet", "oxycodone"),
        ("{7 (Ethinyl Estradiol 0.035 MG / Norethindrone 0.4 MG Oral Tablet) } Pack [Balziva 28 Day]", "ethinyl"),
        ("amLODIPine 2.5 MG Oral Tablet", "amlodipine"),
    ],
)
def test_drug_names_are_identified_by_the_drug_not_the_packaging(name, key):
    assert drug_key(name) == key


def test_current_medications_need_every_active_drug():
    case = Case(
        "p1", "current", "Which medications?", "lisinopril, amlodipine", expect_all=["lisinopril", "amlodipine"]
    )

    assert grade(case, {"answer": "Lisinopril [2] and amLODIPine [3].", "sources": [], "invalid_citations": []})
    assert not grade(case, {"answer": "Lisinopril [2].", "sources": [], "invalid_citations": []})


def test_absent_conditions_must_be_answered_with_not_found():
    case = Case("p1", "absent", "When was asthma diagnosed?", NOT_FOUND)

    assert grade(case, {"answer": NOT_FOUND, "sources": [], "invalid_citations": []})
    assert not grade(
        case, {"answer": "Asthma was diagnosed on 2020-01-01 [2].", "sources": [], "invalid_citations": []}
    )


def test_an_invented_citation_fails_any_case():
    case = Case("p1", "lab", "Latest HbA1c?", "6.79")

    assert not grade(case, {"answer": "6.79 % [9]", "sources": [], "invalid_citations": [9]})

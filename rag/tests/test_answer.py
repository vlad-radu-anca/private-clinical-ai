from clinical_rag.answer import ANSWER_TOKENS, NOT_FOUND, SUMMARY_TOKENS, answer_from_records, ask, cited, summarise

RECORDS = [
    {
        "kind": "patient",
        "date": "1967-01-03",
        "content": "Patient: male born 1967-01-03, alive.",
        "refs": ["Patient/p1"],
    },
    {
        "kind": "condition",
        "date": "2025-04-08",
        "content": "Condition: Diabetes mellitus type 2 (disorder). Onset 2025-04-08.",
        "refs": ["Condition/c1"],
    },
]


class FakeModel:
    def __init__(self, reply="", vector=None):
        self.reply = reply
        self.vector = vector or [0.1, 0.2]
        self.messages = None

    def chat(self, messages, max_tokens=800):
        self.messages = messages
        self.max_tokens = max_tokens
        return self.reply

    def embed(self, texts):
        return [self.vector for _ in texts]


CURRENT_MED = {
    "kind": "medication",
    "date": "2026-04-30",
    "content": "Medication: lisinopril 10 MG Oral Tablet. Prescribed 2026-04-30. Status: active.",
    "refs": ["MedicationRequest/m1"],
}


class FakeStore:
    def __init__(self, current=None):
        self.searched_for = None
        self.current_records = current or []

    def current(self, patient_id):
        return self.current_records

    def search(self, patient_id, embedding, k):
        self.searched_for = patient_id
        return RECORDS[1:]

    def by_kind(self, patient_id, kinds, limit):
        return RECORDS[:1]


def test_citations_map_back_to_fhir_references():
    sources, invalid = cited("Diagnosed on 2025-04-08 [2]. Male [1][2].", RECORDS)

    assert [s["refs"] for s in sources] == [["Patient/p1"], ["Condition/c1"]]
    assert invalid == []


def test_citations_to_records_that_were_never_given_are_reported():
    sources, invalid = cited("See [2] and [7].", RECORDS)

    assert [s["n"] for s in sources] == [2]
    assert invalid == [7]


def test_the_model_sees_numbered_records_and_the_question():
    model = FakeModel("Diagnosed on 2025-04-08 [2].")

    result = answer_from_records(model, "When was diabetes diagnosed?", RECORDS)

    prompt = model.messages[-1]["content"]
    assert "[2] (condition, 2025-04-08) Condition: Diabetes" in prompt
    assert prompt.endswith("Question: When was diabetes diagnosed?")
    assert result.sources[0]["refs"] == ["Condition/c1"]
    assert result.chunks_used == 2


def test_no_records_means_not_found_without_calling_the_model():
    model = FakeModel("should not be used")

    result = answer_from_records(model, "anything", [])

    assert result.answer == NOT_FOUND
    assert model.messages is None


def test_search_is_always_scoped_to_the_requested_patient():
    store = FakeStore()

    ask(store, FakeModel("ok [1]"), FakeModel(), "p1", "question", k=8)

    assert store.searched_for == "p1"


def test_current_facts_are_always_given_even_when_search_misses_them():
    # A list question ("which medications...") needs every active prescription, not the k most similar chunks.
    model = FakeModel("Lisinopril [3].")

    ask(FakeStore(current=[CURRENT_MED]), model, FakeModel(), "p1", "Which medications is the patient taking?", k=8)

    prompt = model.messages[-1]["content"]
    assert "Medication: lisinopril 10 MG Oral Tablet" in prompt
    # Most similar records before the current facts: the order an 8B model handled best.
    assert prompt.index("(condition, 2025-04-08)") < prompt.index("(medication, 2026-04-30)")


def test_records_found_twice_are_given_once():
    model = FakeModel("ok")
    duplicate = dict(RECORDS[1])

    ask(FakeStore(current=[duplicate]), model, FakeModel(), "p1", "question", k=8)

    assert model.messages[-1]["content"].count("Condition: Diabetes mellitus type 2") == 1


def test_summaries_get_a_larger_answer_budget_than_questions():
    model = FakeModel("Summary [1].")
    store = FakeStore()

    summarise(store, model, "p1")

    assert model.max_tokens == SUMMARY_TOKENS > ANSWER_TOKENS

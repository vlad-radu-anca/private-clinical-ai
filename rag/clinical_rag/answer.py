"""Answer questions about one patient from that patient's records, with citations.

The model only sees numbered records retrieved for this patient and must cite
them as [n]. Each [n] is mapped back to the FHIR resources behind that record,
so a clinician can check every statement against the source. A citation to a
number that was never provided is reported rather than silently dropped: it
means the model invented a source.
"""

import re
import time
from dataclasses import dataclass, field

NOT_FOUND = "The records do not contain this information."

SYSTEM_PROMPT = f"""You answer questions about one patient, using only the numbered records provided.

Rules:
- Use only facts stated in the records. Do not add general medical knowledge, interpretation of values,
  or anything the records do not say.
- Cite every statement with the number of the record it comes from, like [3]. A statement that combines
  records cites all of them, like [2][5].
- If the records contain nothing about the subject of the question, reply exactly: "{NOT_FOUND}"
- If there is no exact match but some records are directly about the same subject (for example a related
  diagnosis), say that the exact item is not recorded, then state what those records do show, with citations.
  Mention only records whose own text names the subject. Never say how records might be related or what
  one might cause or suggest: that is clinical judgement, and it belongs to the clinician.
- Write dates as YYYY-MM-DD, exactly as they appear in the records.
- Be concise: a few sentences, or a short list when the answer has several items.

How to read the records:
- Condition: "Onset" is the date the condition was diagnosed or first recorded. "Resolved" means it has ended;
  "Clinical status: active" means it is ongoing.
- Medication: "Prescribed" is the date of the prescription. "Status: active" means it is still prescribed;
  "stopped" or "completed" means it is not.
- Observation: values over time, oldest first, each as "date value unit".
- Clinical note: the narrative written at a visit, dated by that visit. Its "Medications" section lists what
  was taken at that visit, not now.
- "Current" or "currently" means now: answer only from Medication records marked "Status: active" and
  Condition records marked "Clinical status: active". List every one of them, not a selection."""

SUMMARY_QUESTION = (
    "Summarise this patient's clinical history: main diagnoses with their dates, current and past medications, "
    "and how the key lab values have changed over time. List each medication once, with the dates of its first "
    "and latest prescription, rather than once per prescription."
)
ANSWER_TOKENS = 800
# A whole history needs more room than a single answer: at 800 tokens summaries stopped mid-sentence.
SUMMARY_TOKENS = 2000

CITATION = re.compile(r"\[(\d+)\]")


@dataclass
class Answer:
    answer: str
    sources: list[dict]
    invalid_citations: list[int] = field(default_factory=list)
    chunks_used: int = 0
    latency_ms: int = 0


def format_records(records: list[dict]) -> str:
    return "\n".join(
        f"[{n}] ({r['kind']}, {r.get('date') or 'undated'}) {r['content']}" for n, r in enumerate(records, 1)
    )


def cited(answer: str, records: list[dict]) -> tuple[list[dict], list[int]]:
    numbers = sorted({int(n) for n in CITATION.findall(answer)})
    sources = [
        {"n": n, "kind": records[n - 1]["kind"], "date": records[n - 1].get("date"), "refs": records[n - 1]["refs"]}
        for n in numbers
        if 1 <= n <= len(records)
    ]
    invalid = [n for n in numbers if not 1 <= n <= len(records)]
    return sources, invalid


def answer_from_records(model, question: str, records: list[dict], max_tokens: int = ANSWER_TOKENS) -> Answer:
    start = time.monotonic()
    if not records:
        return Answer(NOT_FOUND, [], latency_ms=0)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Records:\n{format_records(records)}\n\nQuestion: {question}"},
    ]
    text = model.chat(messages, max_tokens=max_tokens)
    sources, invalid = cited(text, records)
    return Answer(text, sources, invalid, len(records), int((time.monotonic() - start) * 1000))


def ask(store, chat_model, embed_model, patient_id: str, question: str, k: int) -> Answer:
    """Free-text question about one patient.

    The model gets the demographics, the k records most similar to the question, then everything
    current (active conditions, active medications, allergies). Current facts are always included
    because list questions need all of them, which similarity search cannot guarantee. The most
    similar records come first: tested on an 8B model, putting the long list of current facts first
    made it miss a diagnosis that was in its context.
    """
    vector = embed_model.embed([question])[0]
    records, seen = [], set()
    candidates = store.by_kind(patient_id, ["patient"], 1) + store.search(patient_id, vector, k)
    for record in candidates + store.current(patient_id):
        if record["content"] not in seen:
            seen.add(record["content"])
            records.append(record)
    return answer_from_records(chat_model, question, records)


def summarise(store, chat_model, patient_id: str) -> Answer:
    """Whole-history summary: built from the structured records, not from similarity search,
    because "everything important" is not something an embedding can retrieve."""
    records = (
        store.by_kind(patient_id, ["patient"], 1)
        + store.by_kind(patient_id, ["condition"], 40)
        + store.by_kind(patient_id, ["medication"], 15)
        + store.by_kind(patient_id, ["allergy"], 10)
        + store.by_kind(patient_id, ["observation"], 15)
    )
    return answer_from_records(chat_model, SUMMARY_QUESTION, records, max_tokens=SUMMARY_TOKENS)

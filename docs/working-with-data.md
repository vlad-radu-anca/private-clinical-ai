# Working with the data

How the assistant sees a patient's record, how to add patients, and how to teach it a new kind of record.
Everything here works the same locally and on AWS; the commands differ only where noted.

All data in this project is synthetic. Do not load real patient data: the controls in this repository are a
demonstration, not a certified clinical system.

## What you can ask about

When a patient is indexed, the RAG service reads everything HAPI returns for that patient (`Patient/<id>/$everything`)
and turns the clinical resources into short text records ("chunks") that are embedded and stored in pgvector.

| FHIR resource | Becomes | Example questions |
| --- | --- | --- |
| Patient | one record: gender, birth date, alive or deceased | How old is the patient? Is the patient alive? |
| Condition | one record per diagnosis: name, onset, resolved or active | When was diabetes diagnosed? Which conditions are active? |
| MedicationRequest | one record per prescription: drug, date, status, dosage | Which medications is the patient currently taking? Was metformin ever prescribed? |
| AllergyIntolerance | one record per allergy: substance, criticality | Does the patient have any allergies? |
| Observation | one record per code with the values in date order (vitals, labs, surveys) | How has the HbA1c changed? What was the most recent blood pressure? |
| Procedure | one record per code with the dates | When was the last colonoscopy? |
| Immunization | one record per vaccine with the dates | Has the patient had a flu vaccine this year? |
| DocumentReference (plain-text notes) | the note text, in pieces of up to 1,500 characters | What did the last visit note say about smoking? |

**Not indexed today:** Encounter (visits, apart from their notes), CarePlan, Goal, CareTeam, Device, ImagingStudy,
DiagnosticReport, and the billing resources Claim and ExplanationOfBenefit. HAPI stores them, and they are visible
through the FHIR record link, but the assistant answers "The records do not contain this information." The last
section shows how to add one.

**How an answer is put together.** For each question the model gets the patient's demographics, the 8 records most
similar to the question (`TOP_K`), and always the current facts: active medications (up to 40), active conditions
(up to 40) and allergies (up to 20). It must cite every statement with the record numbers, and citations to records it
was not given are flagged. **Summarise history** does not search: it takes the demographics, up to 40 conditions,
15 prescriptions, 10 allergies and 15 observation series, because "everything important" is not something a
similarity search can find. Procedures, immunizations and notes are not in the summary.

**Suggested questions.** When you pick a patient, the buttons under the question box come from that patient's own
records (`GET /patients/<id>/suggestions`, rules in `rag/clinical_rag/suggestions.py`). A patient with tuberculosis
gets "When was pulmonary tuberculosis diagnosed?". A patient who never had diabetes is not offered a diabetes question.
Up to six, in this order of importance:

1. the two most recent active diagnoses;
2. the current medications, as a list;
3. allergies, if there are any;
4. one trend, from the first series the patient has: HbA1c, eGFR, total cholesterol, BMI, body weight;
5. the most recent resolved illness;
6. the newest current medicine by name (the brand if there is one, else the drug without its strength);
7. the most recent blood pressure.

Diagnoses are conditions tagged `(disorder)` in SNOMED CT, or with no tag at all, as in most hand-written data. Most
of Synthea's `(finding)` and `(situation)` entries are social or administrative ("Full-time employment", "Medication
review due") and are left out. A patient without diagnoses gets "Which conditions has the patient been diagnosed
with?". No model is involved: the suggestions appear at once and cost nothing. Reading a record is still access, so
each request is written to the audit log (action `suggestions`).

You can still type any question. Asking about something the record does not contain gives "The records do not
contain this information", and the evaluation checks exactly that (the "absent" questions).

**Limits worth knowing.**

- **One patient at a time.** Every question is about the selected patient; the search filters on the patient ID in
  SQL, so other patients' records never reach the model. "Which patients have tuberculosis?" is a question across
  patients: a structured search such as FHIR `Condition?code=<SNOMED code>` or SQL answers it exactly, and it needs
  its own access rules and audit. It is deliberately not part of this assistant.
- A series keeps the 12 most recent values; the record says how many earlier ones were left out.
- "Active" comes from the FHIR status codes: `Condition.clinicalStatus` = `active` (without an end date) and
  `MedicationRequest.status` = `active`. Data that never sets these looks historical to the assistant.
- Only plain-text notes are read; PDFs and other attachments are skipped.

## Add more synthetic patients

Synthea generates whole lifetimes of synthetic medical history. The defaults (100 people, seed 42, 10 years of
history, Massachusetts) give the dataset used in the demos. Change them with environment variables:

```bash
POPULATION=20 SEED=43 OUT_DIR=$PWD/synthea/output-2 ./synthea/generate.sh     # 20 different people
```

A different `SEED` gives different people; the same seed gives the same people again. A separate `OUT_DIR` keeps the
first batch untouched. `YEARS_OF_HISTORY` (default 10) and `STATE` (any US state) are the other useful settings.

**Locally:**

```bash
make load SOURCE=synthea/output-2/fhir     # patients already in HAPI are skipped
make index                                 # re-indexes everyone; existing patients are replaced, not duplicated
```

**On AWS** (with the stack running and `REGION` exported):

```bash
make aws-data RUN=run-2 SOURCE=synthea/output-2/fhir    # a new S3 folder, so the first batch stays as it was
make aws-index
```

The loader asks HAPI whether each Synthea patient already exists before sending it, so loading the same folder twice,
or resuming after an interrupted load, does not create duplicates.

## Add a patient by hand

[examples/handmade-patient.json](examples/handmade-patient.json) is a complete, small FHIR R4 transaction bundle: one
patient with two active conditions, a current prescription, an allergy, two HbA1c results, a blood pressure and a flu
vaccine. It is written the way most systems write FHIR (display texts, plain dates), and a unit test checks that every
part of it is indexed.

Load it, find the ID HAPI gave the patient, and index just that patient:

```bash
make load SOURCE=docs/examples
curl -s 'http://localhost:8080/fhir/Patient?identifier=https://example.org/mrn|DEMO-0001&_elements=id' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"][0]["resource"]["id"])'
make index ONLY=<id>
make ask PATIENT=<id> Q="Which medications is the patient currently taking?"
```

On AWS, HAPI is reachable on `localhost:8080` while `make aws-ui` runs; load with
`make aws-data RUN=handmade SOURCE=docs/examples`, then `make aws-index` (the AWS index job always indexes everyone).

To write your own, copy the example and change the values. The rules that matter:

- **One patient per bundle**, `"type": "transaction"`. Other entries point at the patient through its `fullUrl`
  (`urn:uuid:...`), and HAPI replaces those with real IDs.
- **Give codes a `display`.** That text is what the model reads ("Essential hypertension (disorder)"). A bare code
  appears as the code itself.
- **Set the statuses** described above, or nothing will count as current.
- **Make the patient unique**: `ifNoneExist` on the Patient entry (as in the example) stops a second load from creating
  a second patient. The other entries are plain `POST`s, so load a hand-written bundle once, or remove the patient in
  HAPI before loading a corrected version.

You can also post a bundle straight to HAPI without the loader:

```bash
curl -s -X POST http://localhost:8080/fhir -H 'Content-Type: application/fhir+json' \
  --data-binary @docs/examples/handmade-patient.json | python3 -m json.tool | head
```

## See what the assistant sees

```bash
curl -s "http://localhost:8080/fhir/Patient/<id>/\$everything?_count=200" | python3 -m json.tool | less
```

That is the raw input. The page's **FHIR record** link opens the patient in HAPI, and every citation in an answer links
to the exact resource it came from.

## Teach it a new kind of record

Everything happens in `rag/clinical_rag/documents.py`, which turns resources into text. As an example, visits
(Encounter):

1. Write a function that turns one resource into a `Chunk`: a short, self-contained sentence the model can read
   without context, the resource reference for the citation, and a date.

   ```python
   def encounter_chunk(pid: str, enc: dict) -> Chunk:
       start = _day((enc.get("period") or {}).get("start"))
       kind = _display((enc.get("type") or [None])[0])
       reason = _display((enc.get("reasonCode") or [None])[0])
       text = f"Visit: {kind} on {start or 'unknown'}. Reason: {reason}."
       return Chunk(pid, "encounter", text, [_ref(enc)], start)
   ```

   Resources that repeat the same code many times (like Observation) read better grouped with `series_chunks`.

2. Add it to `build_chunks`:
   `chunks += [encounter_chunk(pid, r) for r in by_type.get("Encounter", [])]`.
3. Add a test in `rag/tests/test_documents.py` with a small hand-written resource and the exact text you expect.
4. If the new kind has a notion of "current" (an open care plan, for example), add it to `Store.current` with its own
   limit, and tell the model in `SYSTEM_PROMPT` how to read it.
   If it deserves a suggested question, add the kind to `Store.outline` and a rule to `suggestions.py`.
5. Rebuild the RAG image and re-index: `make up` and `make index` locally; `make aws-push`, `make aws-deploy` and
   `make aws-index` on AWS.
6. Add questions for it to `clinical_rag/evals.py`, with the expected answer computed from the FHIR data, so
   `make eval` measures whether the model actually uses the new records.

Synthea encounters are numerous (dozens per year for some patients), so for visits the better design is probably one
record per year or per type; the test is where to find out.

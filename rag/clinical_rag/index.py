"""Index patients from the FHIR server into pgvector.

    python -m clinical_rag.index               # every patient
    python -m clinical_rag.index --limit 10    # the first 10
    python -m clinical_rag.index --patient 7945

Re-running is safe: each patient is replaced atomically. Logs show server IDs
and counts only, never record content.
"""

import argparse
import logging
import time

from clinical_rag.clients import FhirClient, ModelClient
from clinical_rag.config import Settings
from clinical_rag.documents import build_chunks
from clinical_rag.store import Store

BATCH = 32
log = logging.getLogger("indexer")


def index_patient(fhir: FhirClient, embedder: ModelClient, store: Store, patient_id: str) -> int:
    chunks = build_chunks(fhir.everything(patient_id))
    vectors = []
    for start in range(0, len(chunks), BATCH):
        vectors += embedder.embed([c.text for c in chunks[start : start + BATCH]])
    store.replace_patient(patient_id, chunks, vectors)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="index only the first N patients (0 = all)")
    parser.add_argument("--patient", help="index a single patient by server ID")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = Settings.from_env()
    fhir = FhirClient(settings.fhir_url)
    embedder = ModelClient(settings.embed_base_url, settings.embed_model)
    store = Store(settings.database_url, settings.embed_dimensions)
    store.init_schema()

    ids = [args.patient] if args.patient else fhir.patient_ids(args.limit)
    log.info("indexing %d patients", len(ids))
    start, total = time.monotonic(), 0
    for n, pid in enumerate(ids, 1):
        count = index_patient(fhir, embedder, store, pid)
        total += count
        log.info("[%d/%d] patient %s: %d chunks", n, len(ids), pid, count)
    log.info("done: %d patients, %d chunks in %.0fs", len(ids), total, time.monotonic() - start)


if __name__ == "__main__":
    main()

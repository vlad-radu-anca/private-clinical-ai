"""Chunks, embeddings and the audit log, in PostgreSQL with pgvector.

Privacy is enforced here, not in the prompt: every search requires a patient
ID and filters on it in SQL, so another patient's records can never reach the
model, whatever the question says.

The audit log records who asked about which patient, when, and how long it
took. It never stores the question or the answer: both can contain patient
data, and an audit trail must be safe to read.
"""

import hashlib
import json

import psycopg
import psycopg.conninfo
import psycopg.sql
from pgvector.psycopg import register_vector

from clinical_rag.documents import ACTIVE_CONDITION, ACTIVE_MEDICATION, Chunk


def ensure_database(database_url: str) -> None:
    """Create the target database if it does not exist yet. On RDS the instance is created with one
    database (HAPI's); the RAG index lives in its own, created on first start."""
    info = psycopg.conninfo.conninfo_to_dict(database_url)
    name = info.get("dbname")
    try:
        psycopg.connect(database_url).close()
    except psycopg.OperationalError as exc:
        if f'database "{name}" does not exist' not in str(exc):
            raise
        maintenance = psycopg.conninfo.make_conninfo(database_url, dbname="postgres")
        with psycopg.connect(maintenance, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))


class Store:
    def __init__(self, database_url: str, dimensions: int):
        self.dimensions = dimensions
        ensure_database(database_url)
        self.conn = psycopg.connect(database_url, autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)

    def init_schema(self) -> None:
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id          bigserial PRIMARY KEY,
                patient_id  text NOT NULL,
                kind        text NOT NULL,
                date        text,
                content     text NOT NULL,
                refs        text[] NOT NULL,
                embedding   vector({self.dimensions}) NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_patient ON chunks (patient_id);
            CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

            CREATE TABLE IF NOT EXISTS audit_log (
                id              bigserial PRIMARY KEY,
                at              timestamptz NOT NULL DEFAULT now(),
                user_id         text NOT NULL,
                patient_id      text NOT NULL,
                action          text NOT NULL,
                question_sha256 text,
                chunks_used     int NOT NULL,
                latency_ms      int NOT NULL,
                model           text NOT NULL
            );
            """
        )

    def replace_patient(self, patient_id: str, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Re-index one patient atomically: readers see either the old chunks or the new ones."""
        with self.conn.transaction():
            self.conn.execute("DELETE FROM chunks WHERE patient_id = %s", (patient_id,))
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (patient_id, kind, date, content, refs, embedding) VALUES (%s,%s,%s,%s,%s,%s)",
                    [
                        (c.patient_id, c.kind, c.date, c.text, c.refs, e)
                        for c, e in zip(chunks, embeddings, strict=True)
                    ],
                )

    def search(self, patient_id: str, embedding: list[float], k: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT kind, date, content, refs, embedding <=> %s::vector AS distance
            FROM chunks
            WHERE patient_id = %s
            ORDER BY distance
            LIMIT %s
            """,
            (embedding, patient_id, k),
        ).fetchall()
        return [dict(zip(("kind", "date", "content", "refs", "distance"), row, strict=True)) for row in rows]

    def by_kind(self, patient_id: str, kinds: list[str], limit: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT kind, date, content, refs FROM chunks
            WHERE patient_id = %s AND kind = ANY(%s)
            ORDER BY date DESC NULLS LAST
            LIMIT %s
            """,
            (patient_id, kinds, limit),
        ).fetchall()
        return [dict(zip(("kind", "date", "content", "refs"), row, strict=True)) for row in rows]

    def current(self, patient_id: str) -> list[dict]:
        """Active medications, active conditions and allergies: the facts a list question needs in full.

        Similarity search returns only the k closest chunks, so "which medications is the patient taking"
        could miss most of them. These records are few per patient and always relevant, so they are
        given to the model with every question instead of being left to search.

        Each kind has its own cap, so a long list of one kind can never push another out: a single
        shared limit once dropped the medications of patients with many conditions.
        """
        selections = [
            ("medication", f"%{ACTIVE_MEDICATION}%", 40),
            ("condition", f"%{ACTIVE_CONDITION}", 40),
            ("allergy", "%", 20),
        ]
        records = []
        for kind, pattern, limit in selections:
            rows = self.conn.execute(
                """
                SELECT kind, date, content, refs FROM chunks
                WHERE patient_id = %s AND kind = %s AND content LIKE %s
                ORDER BY date DESC NULLS LAST
                LIMIT %s
                """,
                (patient_id, kind, pattern, limit),
            ).fetchall()
            records += [dict(zip(("kind", "date", "content", "refs"), row, strict=True)) for row in rows]
        return records

    def indexed_patients(self) -> int:
        return self.conn.execute("SELECT count(DISTINCT patient_id) FROM chunks").fetchone()[0]

    def audit(
        self,
        user_id: str,
        patient_id: str,
        action: str,
        question: str | None,
        chunks_used: int,
        latency_ms: int,
        model: str,
    ) -> None:
        digest = hashlib.sha256(question.encode()).hexdigest() if question else None
        self.conn.execute(
            """
            INSERT INTO audit_log (user_id, patient_id, action, question_sha256, chunks_used, latency_ms, model)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, patient_id, action, digest, chunks_used, latency_ms, model),
        )

    def recent_audit(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT at, user_id, patient_id, action, chunks_used, latency_ms, model"
            " FROM audit_log ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        keys = ("at", "user_id", "patient_id", "action", "chunks_used", "latency_ms", "model")
        return [json.loads(json.dumps(dict(zip(keys, row, strict=True)), default=str)) for row in rows]

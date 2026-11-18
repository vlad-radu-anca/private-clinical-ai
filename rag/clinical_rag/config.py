"""Settings from environment variables, so the same image runs on a laptop and in AWS."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    fhir_url: str
    database_url: str
    llm_base_url: str
    llm_model: str
    llm_backend: str
    embed_base_url: str
    embed_model: str
    embed_dimensions: int
    top_k: int

    @classmethod
    def from_env(cls) -> "Settings":
        llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
        return cls(
            fhir_url=os.getenv("FHIR_URL", "http://localhost:8080/fhir"),
            database_url=database_url(),
            llm_base_url=llm_base_url,
            llm_model=os.getenv("LLM_MODEL", "qwen3:8b"),
            # "ollama" on a laptop, "vllm" in AWS. They switch off Qwen3's thinking mode differently.
            llm_backend=os.getenv("LLM_BACKEND", "ollama"),
            embed_base_url=os.getenv("EMBED_BASE_URL", llm_base_url),
            embed_model=os.getenv("EMBED_MODEL", "qwen3-embedding:0.6b"),
            embed_dimensions=int(os.getenv("EMBED_DIMENSIONS", "1024")),
            top_k=int(os.getenv("TOP_K", "8")),
        )


def database_url() -> str:
    """DATABASE_URL locally. On EKS, DATABASE_SECRET_ARN instead: the password is read from Secrets
    Manager through the pod's identity, so it is never stored in Kubernetes."""
    secret_arn = os.getenv("DATABASE_SECRET_ARN")
    if not secret_arn:
        return os.getenv("DATABASE_URL", "postgresql://rag:change-me-locally@localhost:5433/rag")
    from clinical_rag.aws import database_url_from_secret

    return database_url_from_secret(secret_arn, os.environ["DATABASE_HOST"], os.getenv("DATABASE_NAME", "rag"))

"""HTTP clients for the FHIR server and for an OpenAI-compatible model server.

Ollama on a laptop and vLLM in AWS both expose the OpenAI API, so the only
difference between the two is a base URL and how Qwen3's thinking mode is
switched off. Thinking makes answers slower without making them more grounded,
because the answer has to come from the retrieved records anyway.
"""

import httpx

TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class FhirClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=TIMEOUT, headers={"Accept": "application/fhir+json"})

    def _pages(self, url: str, params: dict | None = None):
        while url:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            bundle = response.json()
            yield bundle
            url = next((link["url"] for link in bundle.get("link", []) if link["relation"] == "next"), None)
            params = None

    def everything(self, patient_id: str) -> list[dict]:
        """Every resource in one patient's record, following pagination."""
        url = f"{self.base_url}/Patient/{patient_id}/$everything"
        return [e["resource"] for page in self._pages(url, {"_count": 1000}) for e in page.get("entry", [])]

    def patient_ids(self, limit: int = 0) -> list[str]:
        ids = []
        for page in self._pages(f"{self.base_url}/Patient", {"_elements": "id", "_count": 200}):
            ids += [e["resource"]["id"] for e in page.get("entry", [])]
            if limit and len(ids) >= limit:
                return ids[:limit]
        return ids

    def patients(self, limit: int = 50) -> list[dict]:
        url = f"{self.base_url}/Patient"
        page = next(self._pages(url, {"_count": limit, "_elements": "id,gender,birthDate,deceasedDateTime"}))
        return [e["resource"] for e in page.get("entry", [])]


class ModelClient:
    def __init__(self, base_url: str, model: str, backend: str = "ollama"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.backend = backend
        self.http = httpx.Client(timeout=TIMEOUT)

    def _no_thinking(self) -> dict:
        if self.backend == "vllm":
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return {"reasoning_effort": "none"}

    def chat(self, messages: list[dict], max_tokens: int = 800) -> str:
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        body.update(self._no_thinking())
        response = self.http.post(f"{self.base_url}/chat/completions", json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.http.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts})
        response.raise_for_status()
        return [item["embedding"] for item in sorted(response.json()["data"], key=lambda d: d["index"])]

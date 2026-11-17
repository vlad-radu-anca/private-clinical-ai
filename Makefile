# Local stack: HAPI FHIR, pgvector and the RAG API in Docker, the model in Ollama on the host.
# Needs Docker, Java 17+ (for Synthea) and Ollama.

COMPOSE := docker compose -f local/docker-compose.yml
# One-off containers, so rebuilding or restarting the API never interrupts a long job.
JOB     := $(COMPOSE) run --rm --no-deps rag
API     := http://localhost:8000
USER    ?= dr.demo
PATIENT ?= 7945
Q       ?= Summarise the main diagnoses with their dates.

.DEFAULT_GOAL := help
.PHONY: help models up down generate load index health patients ask summary audit eval test ui

help: ## Show the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

models: ## Download the chat and embeddings models into Ollama
	ollama pull qwen3:8b
	ollama pull qwen3-embedding:0.6b

up: ## Start HAPI, pgvector and the RAG API
	$(COMPOSE) up -d --build
	@until curl -sf $(API)/health >/dev/null; do sleep 2; done; curl -s $(API)/health; echo

down: ## Stop everything (data is kept in Docker volumes)
	$(COMPOSE) down

generate: ## Generate 100 synthetic patients with Synthea
	./synthea/generate.sh

load: ## Load the generated patients into HAPI
	python3 loader/load_fhir.py --source synthea/output/fhir --fhir-url http://localhost:8080/fhir

index: ## Index every patient into pgvector (re-running is safe)
	$(JOB) python -m clinical_rag.index

ui: ## Open the clinician page in your browser
	open $(API)/ui/

health: ## Service status and number of indexed patients
	@curl -s $(API)/health; echo

patients: ## List patients to ask about
	@curl -s -H 'X-User: $(USER)' $(API)/patients | python3 -m json.tool

ask: ## Ask about a patient: make ask PATIENT=7945 Q="When was diabetes diagnosed?"
	@curl -s -X POST $(API)/patients/$(PATIENT)/ask -H 'X-User: $(USER)' -H 'Content-Type: application/json' \
	  -d "$$(python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$(Q)")" | python3 -m json.tool

summary: ## Summarise a patient's history: make summary PATIENT=7945
	@curl -s -X POST $(API)/patients/$(PATIENT)/summary -H 'X-User: $(USER)' | python3 -m json.tool

audit: ## Who asked about which patient (never the question or the answer)
	@curl -s -H 'X-User: $(USER)' $(API)/audit | python3 -m json.tool

eval: ## Score the RAG pipeline against answers known from the FHIR data
	$(JOB) python -m clinical_rag.evals --api http://rag:8000 --patients 10

test: ## Lint and unit tests, in a container like CI
	docker run --rm -v "$(CURDIR)":/w -w /w python:3.13-slim sh -c \
	  "pip install -q --root-user-action=ignore -r requirements-dev.txt && ruff check . && ruff format --check . && pytest -q"

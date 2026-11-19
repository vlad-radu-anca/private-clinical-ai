# Local stack: HAPI FHIR, pgvector and the RAG API in Docker, the model in Ollama on the host.
# Needs Docker, Java 17+ (for Synthea) and Ollama.

COMPOSE := docker compose -f local/docker-compose.yml
# One-off containers, so rebuilding or restarting the API never interrupts a long job.
JOB     := $(COMPOSE) run --rm --no-deps rag
API     := http://localhost:8000
USER    ?= dr.demo
PATIENT ?= 7945
Q       ?= Summarise the main diagnoses with their dates.
# Where FHIR bundles are loaded from; a second Synthea batch or your own bundles (docs/working-with-data.md).
SOURCE  ?= synthea/output/fhir
# make index ONLY=<patient id> re-indexes one patient instead of all of them.
ONLY    ?=
# Name of the S3 folder a batch is uploaded to on AWS; a new name keeps earlier batches.
RUN     ?= run-1

.DEFAULT_GOAL := help
.PHONY: help models up down generate load index health patients ask summary audit eval test ui \
        aws-init aws-plan aws-apply aws-import aws-push aws-kubeconfig aws-deploy aws-data aws-index aws-eval \
        aws-ui aws-status aws-gpu-off aws-destroy aws-leftovers region-guard

help: ## Show the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

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

load: ## Load FHIR bundles into HAPI (SOURCE=folder; patients already loaded are skipped)
	python3 loader/load_fhir.py --source $(SOURCE) --fhir-url http://localhost:8080/fhir

index: ## Index every patient into pgvector, or one with ONLY=<id> (re-running is safe)
	$(JOB) python -m clinical_rag.index $(if $(ONLY),--patient $(ONLY))

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

# ---------- AWS (phase 2). The first time, run in the order of README "Deploy phase 2 to AWS" ----------
TF       := terraform -chdir=terraform
KUBE     := kubectl -n clinical-ai
HAPI_TAG := v8.12.0-2
GPU      ?= 1
HAPI     ?= 1
# Where to deploy: any region with GPU quota ("Running On-Demand G and VT instances", 4 vCPUs). The default
# stays in the EU for data residency; eu-west-1 is the usual fallback when Frankfurt has no GPU capacity.
REGION   ?= eu-central-1
# Your current public IP becomes the only address allowed to reach the Kubernetes API.
AWS_VARS  = -var hapi_image_tag=$(HAPI_TAG) -var loader_image_tag=$$(scripts/image-tag.sh loader) \
            -var hapi_desired_count=$(HAPI) -var gpu_node_count=$(GPU) -var region=$(REGION) \
            -var admin_cidr=$$(curl -fsS https://checkip.amazonaws.com)/32

# Refuse to plan or apply in one region while the stack still exists in another: Terraform would build a
# second copy and lose track of the first.
region-guard:
	@deployed=$$($(TF) output -raw region 2>/dev/null || true); \
	if [ -n "$$deployed" ] && [ "$$deployed" != "$(REGION)" ]; then \
	  echo "The stack is deployed in $$deployed. Use REGION=$$deployed, or destroy it there first." >&2; exit 1; \
	fi

aws-init: ## Initialise Terraform with the S3 backend (terraform/backend.hcl)
	$(TF) init -backend-config=backend.hcl

aws-plan: region-guard ## Show what aws-apply would change
	$(TF) plan $(AWS_VARS)

aws-apply: region-guard ## Create or update the stack. First time: make aws-apply HAPI=0 GPU=0
	$(TF) apply $(AWS_VARS)

aws-import: ## Mirror pinned images to ECR and models to S3 (CodeBuild, about 20 minutes the first time)
	scripts/aws-import.sh "$$($(TF) output -raw import_project)"

aws-push: ## Build and push the loader and RAG images
	scripts/push-images.sh

aws-kubeconfig: ## Add the cluster to your kubeconfig
	$$($(TF) output -raw kubeconfig_command)

aws-deploy: ## Deploy vLLM, TEI and the RAG API, and wait until they are ready
	scripts/k8s-overlay.sh
	kubectl apply -k k8s/build/app
	$(KUBE) rollout status deploy/tei --timeout=10m
	$(KUBE) rollout status deploy/rag --timeout=10m
	$(KUBE) rollout status deploy/vllm --timeout=20m

aws-data: ## Upload bundles and load them into HAPI (RUN=name SOURCE=folder for another batch)
	@test -d $(SOURCE) || ./synthea/generate.sh
	scripts/upload-bundles.sh $(RUN) $(SOURCE)
	scripts/run-loader.sh $(RUN) --workers 2

aws-index: ## Index every patient into pgvector (Kubernetes job, logs followed)
	scripts/run-job.sh index

aws-eval: ## Run the evaluation suite against the deployed API (Kubernetes job, logs followed)
	scripts/run-job.sh eval

aws-ui: ## Clinician page on http://localhost:8000 and HAPI on http://localhost:8080 (Ctrl-C stops both)
	scripts/aws-ui.sh

aws-status: ## Nodes and pods
	kubectl get nodes -L workload,node.kubernetes.io/instance-type
	$(KUBE) get pods -o wide

aws-gpu-off: region-guard ## Stop the GPU node, the largest cost, keeping everything else
	$(TF) apply $(AWS_VARS) -var gpu_node_count=0

aws-destroy: region-guard ## Delete everything this stack created
	-$(KUBE) delete namespace clinical-ai --ignore-not-found --wait=true
	$(TF) destroy $(AWS_VARS)

aws-leftovers: ## List anything still tagged with this project (should be empty after aws-destroy)
	aws resourcegroupstaggingapi get-resources --region $(REGION) --tag-filters Key=Project,Values=clinical-ai \
	  --query 'ResourceTagMappingList[].ResourceARN' --output table

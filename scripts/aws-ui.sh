#!/usr/bin/env bash
# Open private tunnels to the deployed services. Nothing is exposed to the internet: the RAG API is
# reached through the Kubernetes API, and HAPI through the SSM bastion.
#   http://localhost:8000   clinician page and API
#   http://localhost:8080   HAPI FHIR, where the page's citation links point
# Ctrl-C closes both.
set -euo pipefail
cd "$(dirname "$0")/.."

kubectl -n clinical-ai port-forward svc/rag 8000:8000 &
rag=$!
# The bastion's port forward to the internal load balancer, as printed by Terraform.
read -r -a hapi_cmd <<<"$(terraform -chdir=terraform output -raw port_forward_command)"
"${hapi_cmd[@]}" &
hapi=$!

trap 'kill "$rag" "$hapi" 2>/dev/null' INT TERM EXIT
echo "clinician page: http://localhost:8000   HAPI: http://localhost:8080   (Ctrl-C to stop)"
wait

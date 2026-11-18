#!/usr/bin/env bash
# Build the images that come from this repository (the loader and the RAG service) and push them to
# ECR. Third-party images (HAPI, vLLM, TEI) are mirrored by the import job instead (terraform/import.tf).
#
#   ./scripts/push-images.sh
#
# Each image is tagged with the last commit that changed its source folder, so the tag only moves when
# the code does. ECR tags are immutable: an image already pushed under its tag is skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

TF="terraform -chdir=terraform"
REPOS=$($TF output -json ecr_repositories)
REGION=$($TF output -raw region)
repo() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$REPOS" "$1"; }
REGISTRY="$(repo rag)"
REGISTRY="${REGISTRY%%/*}"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

push() {
  local name=$1 dir=$2 tag
  tag=$(scripts/image-tag.sh "$dir")
  if aws ecr describe-images --region "$REGION" --repository-name "$(repo "$name" | cut -d/ -f2-)" \
    --image-ids imageTag="$tag" >/dev/null 2>&1; then
    echo "$name:$tag already in ECR, skipping"
    return
  fi
  # Fargate and the EKS nodes are x86_64, whatever the laptop is.
  docker build --platform linux/amd64 -t "$(repo "$name"):$tag" "$dir"
  docker push "$(repo "$name"):$tag"
}

push fhir-loader loader
push rag rag
echo "Images pushed. ECR scans them on push: aws ecr describe-image-scan-findings --repository-name <name> --image-id imageTag=<tag>"

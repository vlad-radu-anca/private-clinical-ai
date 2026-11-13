#!/usr/bin/env bash
# Mirror the HAPI image and build the loader image into ECR.
# The VPC has no internet access, so ECS can only pull from ECR.
#   HAPI_TAG=<pinned-version> LOADER_TAG=v1 ./scripts/push-images.sh
set -euo pipefail
: "${HAPI_TAG:?set HAPI_TAG to a pinned hapiproject/hapi tag}"
LOADER_TAG="${LOADER_TAG:-v1}"

cd "$(dirname "$0")/.."
TF="terraform -chdir=terraform"
ECR_HAPI=$($TF output -raw ecr_hapi)
ECR_LOADER=$($TF output -raw ecr_loader)
REGISTRY="${ECR_HAPI%%/*}"
REGION=$(echo "$REGISTRY" | cut -d. -f4)

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker pull --platform linux/amd64 "hapiproject/hapi:${HAPI_TAG}"
docker tag "hapiproject/hapi:${HAPI_TAG}" "${ECR_HAPI}:${HAPI_TAG}"
docker push "${ECR_HAPI}:${HAPI_TAG}"

docker build --platform linux/amd64 -t "${ECR_LOADER}:${LOADER_TAG}" loader/
docker push "${ECR_LOADER}:${LOADER_TAG}"

echo "Images pushed. ECR scan-on-push results: aws ecr describe-image-scan-findings ..."

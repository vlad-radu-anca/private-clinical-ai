#!/usr/bin/env bash
# Upload generated Synthea bundles to the KMS-encrypted bucket.
#   ./scripts/upload-bundles.sh [run-name]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:-run-$(date +%Y%m%d-%H%M)}"
BUCKET=$(terraform -chdir=terraform output -raw data_bucket)
KMS=$(terraform -chdir=terraform output -raw kms_key_arn)

aws s3 sync synthea/output/fhir "s3://${BUCKET}/synthea/${RUN}/" \
  --sse aws:kms --sse-kms-key-id "$KMS" --only-show-errors

echo "Uploaded to s3://${BUCKET}/synthea/${RUN}/"
echo "Next: ./scripts/run-loader.sh ${RUN}"

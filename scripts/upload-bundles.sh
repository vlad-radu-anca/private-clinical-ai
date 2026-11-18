#!/usr/bin/env bash
# Upload generated Synthea bundles to the KMS-encrypted bucket.
#   ./scripts/upload-bundles.sh [run-name]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:-run-$(date +%Y%m%d-%H%M)}"
# The stack's region, not the CLI default (which may be another region).
AWS_REGION=$(terraform -chdir=terraform output -raw region)
export AWS_REGION
BUCKET=$(terraform -chdir=terraform output -raw data_bucket)
KMS=$(terraform -chdir=terraform output -raw kms_key_arn)

aws s3 sync synthea/output/fhir "s3://${BUCKET}/synthea/${RUN}/" \
  --sse aws:kms --sse-kms-key-id "$KMS" --only-show-errors

echo "Uploaded to s3://${BUCKET}/synthea/${RUN}/"
echo "Next: ./scripts/run-loader.sh ${RUN}"

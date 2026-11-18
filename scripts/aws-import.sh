#!/usr/bin/env bash
# Start the import job (terraform/import.tf) and wait for it: pinned images to ECR, pinned models to S3.
#   scripts/aws-import.sh <codebuild-project-name>
# About 20 minutes the first time; a minute when everything is already imported.
set -euo pipefail
project="${1:?usage: aws-import.sh <codebuild-project-name>}"

build=$(aws codebuild start-build --project-name "$project" --query build.id --output text)
echo "started $build"
while :; do
  status=$(aws codebuild batch-get-builds --ids "$build" --query 'builds[0].buildStatus' --output text)
  phase=$(aws codebuild batch-get-builds --ids "$build" --query 'builds[0].currentPhase' --output text)
  echo "$(date +%H:%M:%S)  $status  $phase"
  [[ "$status" == IN_PROGRESS ]] || break
  sleep 30
done

if [[ "$status" != SUCCEEDED ]]; then
  echo "import $status. Logs: aws logs tail /clinical-ai-dev/import --since 1h" >&2
  exit 1
fi
echo "import succeeded"

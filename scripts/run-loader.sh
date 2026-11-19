#!/usr/bin/env bash
# Run the loader as a one-off Fargate task inside the private VPC.
# Data goes S3 -> loader -> internal ALB -> HAPI -> RDS, never leaving the VPC.
#   ./scripts/run-loader.sh <run-name> [extra loader args, e.g. --limit 50]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: run-loader.sh <run-name> [--limit N]}"; shift || true

TF="terraform -chdir=terraform"
# The stack's region, not the CLI default (which may be another region).
AWS_REGION=$($TF output -raw region)
export AWS_REGION
CLUSTER=$($TF output -raw ecs_cluster)
TASKDEF=$($TF output -raw loader_task_definition)
BUCKET=$($TF output -raw data_bucket)
SUBNETS=$($TF output -json private_subnets | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin)))')
SG=$($TF output -raw client_security_group)

CMD=$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1:]))' --source "s3://${BUCKET}/synthea/${RUN}/" "$@")

# HAPI takes a few minutes to start after a scale-up; loading before that only burns retries.
echo "waiting for HAPI to be running and healthy"
aws ecs wait services-stable --cluster "$CLUSTER" --services hapi

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASKDEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"loader\",\"command\":${CMD}}]}" \
  --query 'tasks[0].taskArn' --output text)

echo "Started loader task: $TASK_ARN"
echo "Full logs: aws logs tail /${CLUSTER}/loader --follow"

# Wait for the task to finish (the next step, indexing, needs every patient in HAPI) and report its
# result. The CLI's own waiter gives up after 10 minutes; a full load can take longer.
started=$(date +%s)
while :; do
  status=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" --query 'tasks[0].lastStatus' --output text)
  [[ "$status" == STOPPED ]] && break
  progress=$(aws logs tail "/${CLUSTER}/loader" --since 2m --format short 2>/dev/null | grep -E 'progress|loaded reference' | tail -1 || true)
  echo "$(( ($(date +%s) - started) / 60 )) min  ${status}  ${progress##* INFO }"
  sleep 60
done

aws logs tail "/${CLUSTER}/loader" --since 2h --format short | grep -E 'done:|ERROR|WARNING' | tail -10 || true
exit_code=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" --query 'tasks[0].containers[0].exitCode' --output text)
if [[ "$exit_code" != 0 ]]; then
  echo "loader failed (exit code $exit_code); a re-run resumes: patients already in HAPI are skipped" >&2
  exit 1
fi
echo "loader finished"

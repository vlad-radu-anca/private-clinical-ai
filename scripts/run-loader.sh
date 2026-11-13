#!/usr/bin/env bash
# Run the loader as a one-off Fargate task inside the private VPC.
# Data goes S3 -> loader -> internal ALB -> HAPI -> RDS, never leaving the VPC.
#   ./scripts/run-loader.sh <run-name> [extra loader args, e.g. --limit 50]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="${1:?usage: run-loader.sh <run-name> [--limit N]}"; shift || true

TF="terraform -chdir=terraform"
CLUSTER=$($TF output -raw ecs_cluster)
TASKDEF=$($TF output -raw loader_task_definition)
BUCKET=$($TF output -raw data_bucket)
SUBNETS=$($TF output -json private_subnets | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin)))')
SG=$($TF output -raw client_security_group)

CMD=$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1:]))' --source "s3://${BUCKET}/synthea/${RUN}/" "$@")

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASKDEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"loader\",\"command\":${CMD}}]}" \
  --query 'tasks[0].taskArn' --output text)

echo "Started loader task: $TASK_ARN"
echo "Follow logs: aws logs tail /${CLUSTER}/loader --follow"

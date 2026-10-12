#!/usr/bin/env bash
# Deploy pre-apply step: wait until the cycle state machine has no RUNNING execution, so no
# cycle straddles two image versions. Fails after 15 minutes. A missing state machine (first
# deploy) passes.
# Usage: scripts/wait_for_cycle.sh <state-machine-name>
set -euo pipefail

export AWS_REGION="${AWS_REGION:-eu-west-1}"
NAME="${1:?state machine name, e.g. ags-live-cycle}"

ARN=$(aws stepfunctions list-state-machines \
  --query "stateMachines[?name=='${NAME}'].stateMachineArn | [0]" --output text)
if [ "$ARN" = "None" ] || [ -z "$ARN" ]; then
  echo "no state machine ${NAME} yet"
  exit 0
fi

for _ in $(seq 1 60); do
  RUNNING=$(aws stepfunctions list-executions --state-machine-arn "$ARN" --status-filter RUNNING \
    --query 'length(executions)' --output text)
  if [ "$RUNNING" = "0" ]; then
    exit 0
  fi
  echo "${RUNNING} execution(s) of ${NAME} still RUNNING; waiting"
  sleep 15
done
echo "a cycle of ${NAME} is still RUNNING after 15 minutes" >&2
exit 1

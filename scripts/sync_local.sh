#!/usr/bin/env bash
# Pull-only mirror of the official S3 record into data/, logs/, audits/ and portfolio/.
# Nothing here ever writes to the bucket: S3 is the source of truth, local is a read-only copy.
# Usage: scripts/sync_local.sh [--dryrun]   (AGS_BUCKET overrides the default ags-live-data-<account>)
set -euo pipefail

export AWS_REGION="${AWS_REGION:-eu-west-1}"
AGS_BUCKET="${AGS_BUCKET:-ags-live-data-$(aws sts get-caller-identity --query Account --output text)}"
cd "$(dirname "$0")/.."

DRYRUN="${1:-}"

aws s3 sync "s3://${AGS_BUCKET}/data/" data/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/logs/" logs/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/audits/" audits/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/portfolio/" portfolio/ $DRYRUN

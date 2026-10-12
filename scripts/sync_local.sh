#!/usr/bin/env bash
# Pull-only mirror of the official S3 record into data/, logs/, audits/ and portfolio/.
# Nothing here ever writes to the bucket: S3 is the source of truth, local is a read-only copy.
# Usage: AGS_BUCKET=ags-data-123456789012 scripts/sync_local.sh [--dryrun]
set -euo pipefail

: "${AGS_BUCKET:?set AGS_BUCKET to the data bucket name}"
cd "$(dirname "$0")/.."

DRYRUN="${1:-}"

aws s3 sync "s3://${AGS_BUCKET}/data/" data/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/logs/" logs/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/audits/" audits/ $DRYRUN
aws s3 sync "s3://${AGS_BUCKET}/portfolio/" portfolio/ $DRYRUN

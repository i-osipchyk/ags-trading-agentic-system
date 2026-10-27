#!/usr/bin/env bash
# Pull-only mirror of the official S3 record into data/, logs/, audits/, portfolio/ and attempts/.
# Nothing here ever writes to the bucket: S3 is the source of truth, local is a read-only copy.
# Usage: scripts/sync_local.sh [--dryrun] [prefix...]   (no prefix = all of them; the dashboard pulls
#        portfolio logs attempts). AGS_BUCKET overrides the default ags-live-data-<account>.
set -euo pipefail

export AWS_REGION="${AWS_REGION:-eu-west-1}"
cd "$(dirname "$0")/.."

DRYRUN=""
PREFIXES=()
for arg in "$@"; do
  case "$arg" in
    --dryrun) DRYRUN="--dryrun" ;;
    data|logs|audits|portfolio|attempts) PREFIXES+=("$arg") ;;
    *) echo "unknown prefix: $arg (expected data, logs, audits, portfolio or attempts)" >&2; exit 2 ;;
  esac
done

AGS_BUCKET="${AGS_BUCKET:-ags-live-data-$(aws sts get-caller-identity --query Account --output text)}"

want() { [ ${#PREFIXES[@]} -eq 0 ] || [[ " ${PREFIXES[*]} " == *" $1 "* ]]; }

want data && aws s3 sync "s3://${AGS_BUCKET}/data/" data/ $DRYRUN
want logs && aws s3 sync "s3://${AGS_BUCKET}/logs/" logs/ $DRYRUN
want audits && aws s3 sync "s3://${AGS_BUCKET}/audits/" audits/ $DRYRUN
want portfolio && aws s3 sync "s3://${AGS_BUCKET}/portfolio/" portfolio/ $DRYRUN
want attempts && aws s3 sync "s3://${AGS_BUCKET}/attempts/" attempts/ $DRYRUN
exit 0

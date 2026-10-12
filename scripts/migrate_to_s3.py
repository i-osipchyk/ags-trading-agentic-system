"""One-off: seed the data bucket from a pre-v3 local checkout (see ags.migrate).

Usage: AWS_REGION=... uv run python scripts/migrate_to_s3.py --bucket ags-data-123456789012
Safe to re-run; never overwrites objects already in the bucket.
"""

import argparse
from pathlib import Path

from ags.migrate import migrate_to_storage
from ags.storage import S3Storage

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args()
    result = migrate_to_storage(ROOT, S3Storage(args.bucket))
    print(f"{result['uploaded']} uploaded, {result['skipped']} already present")


if __name__ == "__main__":
    main()

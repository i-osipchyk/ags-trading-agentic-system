"""One-off: seed the S3 bucket from a pre-v3 local checkout.

`data/` (the PIT cache is still valid) goes to `data/`. Pre-v3 `logs/` and `audits/` go to
`archive/pre-v3/` so they stay out of the new track record, which only reads `logs/` and `audits/`.
Uses put_if_absent throughout: safe to re-run, and never overwrites anything already in the bucket.
"""

from pathlib import Path

from ags.storage import Storage

_LAYOUT = {"data": "data", "logs": "archive/pre-v3/logs", "audits": "archive/pre-v3/audits"}


def migrate_to_storage(source: Path, storage: Storage) -> dict:
    uploaded = skipped = 0
    for local_dir, key_prefix in _LAYOUT.items():
        root = Path(source) / local_dir
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            key = f"{key_prefix}/{path.relative_to(root).as_posix()}"
            if storage.put_if_absent(key, path.read_bytes()):
                uploaded += 1
            else:
                skipped += 1
    return {"uploaded": uploaded, "skipped": skipped}

import json
from datetime import date
from pathlib import Path


def read_pit(data_dir: Path, *, source: str, report: str, symbol: str, as_of: date) -> dict | None:
    dir_path = Path(data_dir) / source / report / symbol
    if not dir_path.exists():
        return None

    qualifying_files = [f for f in dir_path.glob("*.json") if date.fromisoformat(f.stem) <= as_of]
    if not qualifying_files:
        return None

    latest_file = max(qualifying_files, key=lambda f: date.fromisoformat(f.stem))
    return json.loads(latest_file.read_text())

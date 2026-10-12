import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "sync_local.sh"


def _sync_lines() -> list[str]:
    return [l.strip() for l in SCRIPT.read_text().splitlines() if "aws s3" in l and not l.strip().startswith("#")]


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_every_sync_pulls_from_the_bucket_and_none_pushes():
    lines = _sync_lines()
    assert lines, "no aws s3 commands found"
    for line in lines:
        # `aws s3 sync <source> <dest>`: the bucket must be the source, never the destination.
        assert re.search(r'aws s3 sync\s+"?s3://', line), f"not pull-only: {line}"
        assert not re.search(r's3://\S+"?\s*$', line.replace("--dryrun", "").replace("$DRYRUN", "").strip()), f"pushes: {line}"
    assert not any(re.search(r"aws s3 (cp|mv|rm|rb)\b", l) for l in lines)
    assert "--delete" not in SCRIPT.read_text()


def test_it_mirrors_exactly_the_four_official_prefixes():
    synced = {m.group(1) for l in _sync_lines() for m in [re.search(r's3://\S+?/(\w+)/', l)] if m}
    assert synced == {"data", "logs", "audits", "portfolio"}
    assert "archive" not in SCRIPT.read_text().replace("# ", "")


def test_local_and_portfolio_mirror_dirs_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "/local/" in ignored and "/portfolio/" in ignored
    assert any(l.startswith(".terraform") or l == "**/.terraform/" for l in ignored)

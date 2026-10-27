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


def test_it_mirrors_exactly_the_five_official_prefixes():
    synced = {m.group(1) for l in _sync_lines() for m in [re.search(r's3://\S+?/(\w+)/', l)] if m}
    assert synced == {"data", "logs", "audits", "portfolio", "attempts"}
    assert "archive" not in SCRIPT.read_text().replace("# ", "")


def test_local_and_portfolio_mirror_dirs_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "/local/" in ignored and "/portfolio/" in ignored
    assert any(l.startswith(".terraform") or l == "**/.terraform/" for l in ignored)


def _run_with_fake_aws(tmp_path, *args):
    """Run the script with an `aws` that only records its arguments; returns the synced S3 prefixes."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "aws"
    fake.write_text(f'#!/usr/bin/env bash\necho "$@" >> {calls}\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "AGS_BUCKET": "bkt", "AWS_REGION": "eu-west-1"}
    result = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return result, [l.split("s3://bkt/")[1].split("/")[0] for l in lines]


def test_without_arguments_it_syncs_every_prefix(tmp_path):
    result, synced = _run_with_fake_aws(tmp_path)

    assert result.returncode == 0
    assert sorted(synced) == ["attempts", "audits", "data", "logs", "portfolio"]


def test_prefix_arguments_limit_the_sync_to_those_prefixes(tmp_path):
    result, synced = _run_with_fake_aws(tmp_path, "portfolio", "logs", "attempts")

    assert result.returncode == 0
    assert sorted(synced) == ["attempts", "logs", "portfolio"]


def test_dryrun_still_works_alongside_prefixes(tmp_path):
    _, synced = _run_with_fake_aws(tmp_path, "--dryrun", "portfolio")
    assert synced == ["portfolio"]
    assert "--dryrun" in (tmp_path / "calls.txt").read_text()


def test_an_unknown_prefix_is_refused_and_nothing_is_synced(tmp_path):
    result, synced = _run_with_fake_aws(tmp_path, "secrets")

    assert result.returncode != 0
    assert synced == []

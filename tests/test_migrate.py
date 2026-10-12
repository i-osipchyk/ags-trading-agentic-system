import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from ags.migrate import migrate_to_storage
from ags.storage import LocalStorage, Prefixed, S3Storage
from ags.tools.track_record import read_track_record


def _local_tree(root):
    (root / "data" / "prices").mkdir(parents=True)
    (root / "data" / "prices" / "corn.json").write_text('{"bars": 1}')
    (root / "logs").mkdir()
    run = {"seq": 0, "agent": "coordinator", "event_type": "output", "timestamp": "2026-05-01T20:00:00+00:00",
           "payload": {"call": "bullish", "conviction": 4, "thesis": "t", "key_drivers": [],
                       "supporting_analysts": [], "dissenting_analysts": [], "invalidation_conditions": []}}
    (root / "logs" / "corn_2026-05-01T20-00-00Z.jsonl").write_text(json.dumps(run) + "\n")
    (root / "audits" / "corn_2026-05-01T20-00-00Z").mkdir(parents=True)
    (root / "audits" / "corn_2026-05-01T20-00-00Z" / "1w.json").write_text("{}")


def test_data_goes_to_data_and_logs_and_audits_go_to_the_pre_v3_archive(tmp_path):
    _local_tree(tmp_path / "src")
    target = LocalStorage(tmp_path / "bucket")

    migrate_to_storage(tmp_path / "src", target)

    assert target.get("data/prices/corn.json") == b'{"bars": 1}'
    assert target.get("archive/pre-v3/logs/corn_2026-05-01T20-00-00Z.jsonl") is not None
    assert target.get("archive/pre-v3/audits/corn_2026-05-01T20-00-00Z/1w.json") == b"{}"
    assert target.list("logs/") == [] and target.list("audits/") == []


def test_a_fresh_track_record_sees_no_pre_v3_runs(tmp_path):
    _local_tree(tmp_path / "src")
    target = LocalStorage(tmp_path / "bucket")

    migrate_to_storage(tmp_path / "src", target)

    record = read_track_record(
        Prefixed(target, "logs"), Prefixed(target, "audits"), commodity="corn", lookback_weeks=52,
        as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert record == []


def test_rerunning_is_safe_and_never_overwrites_what_is_already_in_the_bucket(tmp_path):
    _local_tree(tmp_path / "src")
    target = LocalStorage(tmp_path / "bucket")
    migrate_to_storage(tmp_path / "src", target)
    target.put("data/prices/corn.json", b'{"bars": "changed upstream"}')

    result = migrate_to_storage(tmp_path / "src", target)

    assert result == {"uploaded": 0, "skipped": 3}
    assert target.get("data/prices/corn.json") == b'{"bars": "changed upstream"}'


@mock_aws
def test_it_works_against_s3():
    boto3.client("s3", region_name="eu-west-1").create_bucket(
        Bucket="migrate-test-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
    )
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        _local_tree(Path(d))
        result = migrate_to_storage(Path(d), S3Storage("migrate-test-bucket", region_name="eu-west-1"))

    assert result == {"uploaded": 3, "skipped": 0}

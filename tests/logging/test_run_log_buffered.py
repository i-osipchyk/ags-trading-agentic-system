import boto3
import pytest
from moto import mock_aws

from ags.logging.run_log import RunLog
from ags.storage import S3Storage

RUN_ID = "corn_2026-09-11T18-30-00Z"


@pytest.fixture
def s3():
    with mock_aws():
        boto3.client("s3", region_name="eu-west-1").create_bucket(
            Bucket="test-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
        )
        yield S3Storage("test-bucket", region_name="eu-west-1")


def test_buffered_log_is_uploaded_once_at_close_not_per_event(s3):
    log = RunLog(s3, RUN_ID, buffered=True)

    log.append(agent="technical", event_type="output", payload={"trend": "up"})
    assert s3.get(f"{RUN_ID}.jsonl") is None

    log.close()

    events = RunLog(s3, RUN_ID).read()
    assert [e["agent"] for e in events] == ["technical"]


def test_a_run_that_raises_midway_still_uploads_its_partial_log(s3):
    with pytest.raises(RuntimeError):
        with RunLog(s3, RUN_ID, buffered=True) as log:
            log.append(agent="technical", event_type="output", payload={"trend": "up"})
            log.append(agent="news", event_type="output", payload={"headlines": []})
            raise RuntimeError("boom")

    events = RunLog(s3, RUN_ID).read()
    assert [e["seq"] for e in events] == [0, 1]


def test_buffered_log_keeps_events_already_stored_for_the_run(s3):
    RunLog(s3, RUN_ID).append(agent="technical", event_type="output", payload={})

    with RunLog(s3, RUN_ID, buffered=True) as log:
        log.append(agent="news", event_type="output", payload={})

    assert [e["seq"] for e in RunLog(s3, RUN_ID).read()] == [0, 1]

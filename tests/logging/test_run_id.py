from datetime import datetime, timezone

from ags.logging.run_id import generate_run_id


def test_generate_run_id_formats_commodity_and_timestamp():
    trigger_timestamp = datetime(2026, 9, 11, 18, 30, 0, tzinfo=timezone.utc)

    run_id = generate_run_id("corn", trigger_timestamp)

    assert run_id == "corn_2026-09-11T18-30-00Z"

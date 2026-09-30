from ags.logging.run_log import RunLog


def test_new_instance_appends_after_existing_events_without_overwriting(tmp_path):
    run_id = "corn_2026-09-11T18-30-00Z"

    RunLog(log_dir=tmp_path, run_id=run_id).append(
        agent="technical", event_type="output", payload={"trend": "up"}
    )
    RunLog(log_dir=tmp_path, run_id=run_id).append(
        agent="news", event_type="output", payload={"headlines": []}
    )

    events = RunLog(log_dir=tmp_path, run_id=run_id).read()

    assert [e["seq"] for e in events] == [0, 1]
    assert [e["agent"] for e in events] == ["technical", "news"]

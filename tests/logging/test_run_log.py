from ags.logging.run_log import RunLog


def test_append_then_read_round_trips_events_in_order(tmp_path):
    log = RunLog(log_dir=tmp_path, run_id="corn_2026-09-11T18-30-00Z")

    log.append(agent="technical", event_type="output", payload={"trend": "up"})
    log.append(agent="news", event_type="output", payload={"headlines": []})

    events = log.read()

    assert [e["seq"] for e in events] == [0, 1]
    assert [e["agent"] for e in events] == ["technical", "news"]
    assert [e["event_type"] for e in events] == ["output", "output"]
    assert [e["payload"] for e in events] == [{"trend": "up"}, {"headlines": []}]
    assert all("timestamp" in e for e in events)

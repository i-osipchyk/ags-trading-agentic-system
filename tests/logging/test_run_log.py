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


def test_run_log_over_a_storage_persists_each_event_and_a_reopened_log_continues_the_sequence(tmp_path):
    from ags.storage import LocalStorage, Prefixed

    store = Prefixed(LocalStorage(tmp_path), "logs")
    log = RunLog(log_dir=store, run_id="corn_2026-09-11T18-30-00Z")
    log.append(agent="technical", event_type="output", payload={"a": 1})

    reopened = RunLog(log_dir=store, run_id="corn_2026-09-11T18-30-00Z")
    reopened.append(agent="news", event_type="output", payload={"b": 2})

    assert [e["seq"] for e in reopened.read()] == [0, 1]
    assert len((tmp_path / "logs" / "corn_2026-09-11T18-30-00Z.jsonl").read_text().splitlines()) == 2

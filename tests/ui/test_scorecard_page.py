from streamlit.testing.v1 import AppTest

from tests.test_scoring import CALL_TIME, _falling_history, _log


def _page(log_dir, data_dir, jobs_dir, pull):
    from ags.ui.scorecard_page import render_scorecard_page

    render_scorecard_page(log_dir=log_dir, data_dir=data_dir, jobs_dir=jobs_dir, pull=pull)


def _app(tmp_path, pull=lambda: None):
    return AppTest.from_function(
        _page, kwargs={"log_dir": tmp_path / "logs", "data_dir": tmp_path / "data", "jobs_dir": tmp_path / "jobs", "pull": pull}
    ).run(timeout=30)


def test_the_pull_button_is_there_even_with_an_empty_mirror_and_calls_pull(tmp_path):
    pulled = []

    app = _app(tmp_path, pull=lambda: pulled.append(1))
    app.button[0].click().run(timeout=30)

    assert app.button[0].label == "Pull latest data"
    assert pulled == [1]


def test_with_nothing_in_the_mirror_it_says_to_pull(tmp_path):
    app = _app(tmp_path)

    assert not app.exception
    assert "Pull the latest data" in app.info[0].value


def test_it_shows_the_verdict_the_tables_and_warns_when_closes_lag_the_calls(tmp_path):
    _falling_history(tmp_path / "data")
    _log(tmp_path / "logs", "corn", CALL_TIME, "bearish", 100.0)
    _log(tmp_path / "logs", "corn", CALL_TIME.replace(day=30), "bearish", 100.0)

    app = _app(tmp_path)

    assert not app.exception
    assert [t.value for t in app.title] == ["Scorecard"]
    assert any("Not yet testable" in i.value for i in app.info)
    assert any("Daily closes run through" in w.value for w in app.warning)
    assert len(app.dataframe) >= 5


def test_the_app_registers_the_scorecard_page():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "scripts" / "run_chat.py").read_text()

    assert 'title="Scorecard"' in source


def _page_with_challenger(log_dir, data_dir, jobs_dir, pull, challenger_root):
    from ags.ui.scorecard_page import render_scorecard_page

    render_scorecard_page(log_dir=log_dir, data_dir=data_dir, jobs_dir=jobs_dir, pull=pull, challenger_root=challenger_root)


def _challenger_app(tmp_path):
    return AppTest.from_function(
        _page_with_challenger, kwargs={
            "log_dir": tmp_path / "logs", "data_dir": tmp_path / "data", "jobs_dir": tmp_path / "jobs",
            "pull": lambda: None, "challenger_root": tmp_path / "replays" / "analysts",
        },
    ).run(timeout=30)


def test_the_challenger_section_says_how_to_make_a_batch_when_there_is_none(tmp_path):
    _falling_history(tmp_path / "data")
    _log(tmp_path / "logs", "corn", CALL_TIME, "bearish", 100.0)

    app = _challenger_app(tmp_path)

    assert not app.exception
    assert any("No challenger runs yet" in c.value for c in app.caption)


def test_the_challenger_section_compares_a_batch_with_the_official_calls(tmp_path):
    from tests.test_scoring import _rerun_log

    _falling_history(tmp_path / "data")
    _log(tmp_path / "logs", "corn", CALL_TIME, "bullish", 100.0)
    run_id = f"corn_{CALL_TIME:%Y-%m-%dT%H-%M-%SZ}"
    _rerun_log(tmp_path / "replays" / "analysts" / "aligned-v1" / "logs", run_id, "bearish",
               {"technical": -2, "news": 0, "weather": 0, "supply_demand": 1}, code_bias=-2)

    app = _challenger_app(tmp_path)

    assert not app.exception
    assert any("re-made 1 of 1 official runs" in c.value for c in app.caption)
    assert any("Challenger vs the live cycle" in s.value for s in app.subheader)
    labels = [m.label for m in app.metric]
    assert "Challenger − official, per call" in labels and "Same call as the official run" in labels

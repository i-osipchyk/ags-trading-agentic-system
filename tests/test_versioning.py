import re

from ags import versioning
from ags.agents import coordinator
from tests.test_pipeline import _events, _run


def test_code_version_prefers_the_images_git_sha(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")

    assert versioning.code_version() == "abc1234"


def test_code_version_falls_back_when_the_image_has_no_sha(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "unknown")

    assert isinstance(versioning.code_version(), str)


def test_prompt_hashes_change_when_a_prompt_changes(monkeypatch):
    before = versioning.prompt_hashes()
    monkeypatch.setattr(coordinator, "SYSTEM_PROMPT_TEMPLATE", coordinator.SYSTEM_PROMPT_TEMPLATE + " changed")
    monkeypatch.setitem(versioning._PROMPTS, "coordinator", coordinator.SYSTEM_PROMPT_TEMPLATE)
    after = versioning.prompt_hashes()

    assert set(before) == {"technical", "news", "weather", "coordinator", "all"}
    assert before["technical"] == after["technical"]
    assert before["coordinator"] != after["coordinator"]
    assert before["all"] != after["all"]
    assert all(re.fullmatch(r"[0-9a-f]{12}", h) for h in before.values())


def test_every_cycle_logs_its_code_version_and_prompt_hashes_first(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    result = _run(tmp_path)

    first = _events(tmp_path, result["run_id"])[0]

    assert (first["agent"], first["event_type"]) == ("pipeline", "version")
    assert first["payload"]["code_version"] == "abc1234"
    assert first["payload"]["prompt_hashes"] == versioning.prompt_hashes()

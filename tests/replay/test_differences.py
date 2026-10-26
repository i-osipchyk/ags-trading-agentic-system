from ags.logging.run_log import RunLog
from ags.replay import ReplaySpec, describe_differences, save_spec
from ags.storage import LocalStorage, Prefixed

SPEC = ReplaySpec.from_mapping({
    "replay_id": "r1", "fork_at": "2026-10-20T08:00:00+00:00",
    "coordinator": {"variant": "default", "model": "deepseek-reasoner"},
    "manager": {"variant": "default", "model": "deepseek-chat"},
})
RUN = "corn_2026-10-20T20-00-00Z"
CYCLE = "cycle_2026-10-20T20-00-00Z"


def _prompt(store, log_key, agent, system, model):
    with RunLog(store, log_key) as log:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "data"}]
        log.append(agent=agent, event_type="prompt", payload={"messages": messages, "model": model})


def test_the_differences_name_a_changed_model_and_diff_a_changed_prompt_against_the_official_run(tmp_path):
    store = LocalStorage(tmp_path)
    save_spec(store, SPEC)
    _prompt(Prefixed(store, "logs"), RUN, "coordinator", "You are the Coordinator.\nBe careful.", "deepseek-chat")
    _prompt(Prefixed(store, "replays/r1/logs"), RUN, "coordinator", "You are the Coordinator.\nBe bold.", "deepseek-reasoner")
    _prompt(Prefixed(store, "portfolio/logs"), CYCLE, "manager", "You are the Portfolio Manager.", "deepseek-chat")
    _prompt(Prefixed(store, "replays/r1/portfolio/logs"), CYCLE, "manager", "You are the Portfolio Manager.", "deepseek-chat")

    text = describe_differences(store, "r1", code_version="abc1234 (uncommitted changes)")

    coordinator, manager = text.split("## Manager")
    assert "deepseek-chat → deepseek-reasoner" in coordinator
    assert "-Be careful." in coordinator and "+Be bold." in coordinator
    assert RUN in coordinator
    assert "Model: deepseek-chat (unchanged)" in manager and "System prompt: no differences" in manager
    assert CYCLE in manager
    assert "abc1234 (uncommitted changes)" in text

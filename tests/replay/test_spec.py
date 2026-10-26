import pytest

from ags.replay import ReplaySpec, load_spec, save_spec
from ags.storage import LocalStorage

SPEC = {
    "replay_id": "coord-v2",
    "fork_at": "2026-10-20T08:00:00+00:00",
    "coordinator": {"variant": "default", "model": "deepseek-chat"},
    "manager": {"variant": "default", "model": "deepseek-chat"},
}


def test_a_saved_spec_loads_back_and_saving_it_again_is_a_no_op(tmp_path):
    store = LocalStorage(tmp_path)
    spec = ReplaySpec.from_mapping(SPEC)

    save_spec(store, spec)
    save_spec(store, spec)

    assert load_spec(store, "coord-v2") == spec


def test_a_different_spec_under_an_existing_replay_id_is_refused(tmp_path):
    store = LocalStorage(tmp_path)
    save_spec(store, ReplaySpec.from_mapping(SPEC))
    changed = ReplaySpec.from_mapping({**SPEC, "manager": {"variant": "default", "model": "deepseek-reasoner"}})

    with pytest.raises(ValueError, match="coord-v2"):
        save_spec(store, changed)


@pytest.mark.parametrize("override", [
    {"coordinator": {"variant": "nope", "model": "deepseek-chat"}},
    {"fork_at": "2026-10-20T08:00:00"},  # no timezone
    {"replay_id": "Has Spaces"},
])
def test_an_invalid_spec_is_rejected(override):
    with pytest.raises(ValueError):
        ReplaySpec.from_mapping({**SPEC, **override})

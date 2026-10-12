import pytest

from ags.storage import LocalStorage, Prefixed


@pytest.fixture(params=["local"])
def storage(request, tmp_path):
    return LocalStorage(tmp_path)


def test_get_returns_what_was_put(storage):
    storage.put("a/b.json", b"hello")

    assert storage.get("a/b.json") == b"hello"


def test_get_returns_none_for_a_missing_key(storage):
    assert storage.get("nope.json") is None


def test_put_overwrites_an_existing_key(storage):
    storage.put("k", b"one")
    storage.put("k", b"two")

    assert storage.get("k") == b"two"


def test_put_if_absent_writes_a_new_key_and_reports_it(storage):
    written = storage.put_if_absent("a/new.json", b"first")

    assert written is True
    assert storage.get("a/new.json") == b"first"


def test_put_if_absent_is_a_noop_when_the_key_exists(storage):
    storage.put_if_absent("k", b"original")

    written = storage.put_if_absent("k", b"later")

    assert written is False
    assert storage.get("k") == b"original"


def test_put_if_absent_does_not_replace_a_key_written_with_put(storage):
    storage.put("k", b"original")

    assert storage.put_if_absent("k", b"later") is False
    assert storage.get("k") == b"original"


def test_list_returns_sorted_keys_under_a_prefix_including_nested(storage):
    for key in ("runs/b.jsonl", "runs/a.jsonl", "runs/sub/c.json", "other/z.json"):
        storage.put(key, b"x")

    assert storage.list("runs/") == ["runs/a.jsonl", "runs/b.jsonl", "runs/sub/c.json"]


def test_list_matches_on_a_partial_filename_prefix(storage):
    for key in ("logs/corn_1.jsonl", "logs/corn_2.jsonl", "logs/soy_1.jsonl"):
        storage.put(key, b"x")

    assert storage.list("logs/corn_") == ["logs/corn_1.jsonl", "logs/corn_2.jsonl"]


def test_list_of_an_unknown_prefix_is_empty(storage):
    assert storage.list("nothing/") == []


def test_prefixed_view_scopes_keys_to_its_prefix(storage):
    scoped = Prefixed(storage, "data")

    scoped.put("x/1.json", b"1")

    assert storage.get("data/x/1.json") == b"1"
    assert scoped.get("x/1.json") == b"1"
    assert scoped.list("x/") == ["x/1.json"]
    assert scoped.put_if_absent("x/1.json", b"2") is False

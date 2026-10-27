import pytest

import ags.config
from ags import blockers


@pytest.fixture(autouse=True)
def _no_blockers_between_tests():
    """The blocker record is per process; entry.py clears it per invocation, tests per test."""
    blockers.clear()
    yield
    blockers.clear()


@pytest.fixture(autouse=True)
def _no_local_ssm_read(request, monkeypatch):
    """Unit tests never read the real /platform/shared cTrader pair; integration tests do."""
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(ags.config, "_load_shared_ctrader", lambda: None)

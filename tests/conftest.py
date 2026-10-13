import pytest

import ags.config


@pytest.fixture(autouse=True)
def _no_local_ssm_read(request, monkeypatch):
    """Unit tests never read the real /platform/shared cTrader pair; integration tests do."""
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(ags.config, "_load_shared_ctrader", lambda: None)

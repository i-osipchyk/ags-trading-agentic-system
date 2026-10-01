from pathlib import Path

import run_auditor


def test_main_takes_no_arguments_and_audits_the_repo_logs_and_audits_dirs(monkeypatch, capsys):
    seen = {}

    class FakeConfig:
        deepseek_model = "stub-model"

    monkeypatch.setattr(run_auditor.Config, "from_env", classmethod(lambda cls: FakeConfig()))
    monkeypatch.setattr(run_auditor, "DeepSeekChatClient", lambda config: "client")

    def fake_run_auditor(client, **kwargs):
        seen["client"], seen["kwargs"] = client, kwargs
        return {"graded": [("corn_2026-06-05T18-30-00Z", 1)], "failed": [(("corn_2026-06-05T18-30-00Z", 2), "ValueError: boom")]}

    monkeypatch.setattr(run_auditor, "run_auditor", fake_run_auditor)

    run_auditor.main([])

    root = Path(run_auditor.__file__).parent
    assert seen["client"] == "client"
    assert seen["kwargs"]["log_dir"] == root / "logs"
    assert seen["kwargs"]["audit_dir"] == root / "audits"
    assert seen["kwargs"]["data_dir"] == root / "data"
    assert seen["kwargs"]["model"] == "stub-model"
    out = capsys.readouterr().out
    assert "corn_2026-06-05T18-30-00Z" in out and "1w" in out
    assert "ValueError: boom" in out

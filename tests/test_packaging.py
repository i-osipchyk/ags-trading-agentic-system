import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def _names(requirements):
    return {re.split(r"[<>=\[ ]", r)[0] for r in requirements}


def test_streamlit_is_only_in_the_ui_group_so_the_lambda_image_excludes_it():
    assert "streamlit" not in _names(PYPROJECT["project"]["dependencies"])
    assert "streamlit" in _names(PYPROJECT["dependency-groups"]["ui"])


def test_local_uv_sync_still_installs_the_ui_and_dev_groups():
    assert set(PYPROJECT["tool"]["uv"]["default-groups"]) >= {"dev", "ui"}


def test_nothing_outside_the_ui_package_imports_streamlit():
    offenders = [
        str(p.relative_to(ROOT)) for p in (ROOT / "src" / "ags").rglob("*.py")
        if "ui" not in p.relative_to(ROOT / "src" / "ags").parts and re.search(r"^\s*(import|from)\s+streamlit", p.read_text(), re.M)
    ]
    assert offenders == []


def test_dockerfile_builds_an_arm64_lambda_image_without_the_ui_group():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM\s+--platform=linux/arm64\s+public\.ecr\.aws/lambda/python:3\.12", dockerfile, re.M)
    assert "--no-group ui" in dockerfile or "--no-default-groups" in dockerfile
    assert "--frozen" in dockerfile  # from uv.lock


def test_llm_calls_go_through_llmkit_pinned_to_a_platform_release():
    assert "llmkit" in _names(PYPROJECT["project"]["dependencies"])
    assert "openai" not in _names(PYPROJECT["project"]["dependencies"])
    source = PYPROJECT["tool"]["uv"]["sources"]["llmkit"]
    assert source == {"git": "https://github.com/i-osipchyk/platform", "subdirectory": "llmkit", "tag": "v0.1.1"}


def test_only_the_llm_client_module_imports_an_llm_sdk():
    offenders = [
        str(p.relative_to(ROOT)) for p in (ROOT / "src" / "ags").rglob("*.py")
        if p.name != "client.py" and re.search(r"^\s*(import|from)\s+(openai|anthropic|llmkit)\b", p.read_text(), re.M)
    ]
    assert offenders == []

import hashlib
import os
import subprocess
from pathlib import Path

from ags.agents import coordinator, news, technical, weather

# The model-driven agents of a cycle; supply/demand is code and has no prompt.
_PROMPTS = {
    "technical": technical.SYSTEM_PROMPT_TEMPLATE,
    "news": news.SYSTEM_PROMPT_TEMPLATE,
    "weather": weather.SYSTEM_PROMPT_TEMPLATE,
    "coordinator": coordinator.SYSTEM_PROMPT_TEMPLATE,
}


def code_version() -> str:
    """The git SHA the image was built from (`GIT_SHA`), else the local checkout's HEAD, else "unknown"."""
    sha = os.environ.get("GIT_SHA")
    if sha and sha != "unknown":
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def prompt_hashes() -> dict[str, str]:
    """A short hash per agent's system prompt template, plus `all` over the four, so runs can be cut by prompt version."""
    hashes = {agent: _digest(template) for agent, template in _PROMPTS.items()}
    hashes["all"] = _digest("".join(hashes[agent] for agent in _PROMPTS))
    return hashes


def version_stamp() -> dict:
    return {"code_version": code_version(), "prompt_hashes": prompt_hashes()}

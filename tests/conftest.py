from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "BAATEIN_DATABASE__URL",
        "BAATEIN_LLM__MODEL",
        "BAATEIN_VOICE__DEFAULT_LANGUAGE",
        "BAATEIN_AUTH__JWT_SECRET",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_AGENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

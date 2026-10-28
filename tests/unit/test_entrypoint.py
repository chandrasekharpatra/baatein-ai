from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from injector import Injector
from livekit.agents import AgentSession, vad

from baatein.agent import entrypoint
from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.container import build_root
from baatein.voice.languages import LanguageProfile, ProviderSpec
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory

PROFILE = LanguageProfile("hinglish", ProviderSpec("s"), ProviderSpec("t"), "Hinglish")


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SARVAM_API_KEY", "CARTESIA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self, **kwargs: Any) -> None:
        self.calls.append(("start", kwargs))

    async def generate_reply(self, **kwargs: Any) -> None:
        self.calls.append(("generate_reply", kwargs))


class FakeFactory:
    def __init__(self, pipeline: VoicePipeline) -> None:
        self.pipeline = pipeline
        self.requested: list[str] = []

    def build(self, language_code: str) -> VoicePipeline:
        self.requested.append(language_code)
        return self.pipeline


def test_prewarm_migrates_and_loads_injector_and_vad() -> None:
    proc = SimpleNamespace(userdata={})

    entrypoint.prewarm(proc)  # type: ignore[arg-type]

    assert isinstance(proc.userdata["injector"], Injector)
    assert isinstance(proc.userdata["vad"], vad.VAD)
    assert Path("data/baatein.db").exists()


async def test_create_session_wires_pipeline_components() -> None:
    injector = build_root(settings=Settings(_env_file=None))  # type: ignore[call-arg]
    pipeline = injector.get(VoicePipelineFactory).build("hinglish")

    session = entrypoint.create_session(pipeline, SimpleNamespace())  # type: ignore[arg-type]

    assert isinstance(session, AgentSession)
    assert session.stt is pipeline.stt
    assert session.llm is pipeline.llm
    assert session.tts is pipeline.tts


async def test_entrypoint_starts_assistant_for_default_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = VoicePipeline(PROFILE, stt=object(), llm=object(), tts=object())  # type: ignore[arg-type]
    factory = FakeFactory(pipeline)
    session = FakeSession()
    created: list[tuple[VoicePipeline, object]] = []

    def fake_create_session(p: VoicePipeline, v: object) -> FakeSession:
        created.append((p, v))
        return session

    monkeypatch.setattr(entrypoint, "create_session", fake_create_session)
    injector = Injector()
    injector.binder.bind(Settings, to=Settings(_env_file=None))  # type: ignore[call-arg]
    injector.binder.bind(VoicePipelineFactory, to=factory)  # type: ignore[arg-type]
    vad_model, room = object(), object()
    ctx = SimpleNamespace(
        proc=SimpleNamespace(userdata={"injector": injector, "vad": vad_model}), room=room
    )

    await entrypoint.entrypoint(ctx)  # type: ignore[arg-type]

    assert factory.requested == ["hinglish"]
    assert created == [(pipeline, vad_model)]
    (start, start_kwargs), (reply, reply_kwargs) = session.calls
    assert start == "start"
    assert start_kwargs["room"] is room
    assert isinstance(start_kwargs["agent"], Assistant)
    assert reply == "generate_reply"
    assert reply_kwargs == {"instructions": entrypoint.GREETING}


def test_main_runs_cli_with_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[object] = []
    monkeypatch.setattr(entrypoint.cli, "run_app", ran.append)

    entrypoint.main()

    assert ran == [entrypoint.server]

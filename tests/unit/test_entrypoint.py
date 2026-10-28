import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from injector import Injector, InstanceProvider
from livekit.agents import AgentSession, vad

from baatein.agent import entrypoint
from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.container import build_root
from baatein.ports.repos import UserRepo
from baatein.voice.languages import LanguageProfile, ProviderSpec
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory
from tests.factories import make_user
from tests.fakes.repos import InMemoryState, InMemoryUserRepo

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
        proc=SimpleNamespace(userdata={"injector": injector, "vad": vad_model}),
        room=room,
        job=SimpleNamespace(metadata=""),
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


def test_main_loads_dotenv_then_runs_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    Path(".env").write_text("LIVEKIT_URL=ws://from-dotenv:7880\nSARVAM_API_KEY=from-dotenv\n")
    monkeypatch.setenv("SARVAM_API_KEY", "from-shell")
    monkeypatch.setenv("LIVEKIT_URL", "restored-after-test")
    monkeypatch.delenv("LIVEKIT_URL")
    seen: list[tuple[object, str | None, str | None]] = []
    monkeypatch.setattr(
        entrypoint.cli,
        "run_app",
        lambda server: seen.append(
            (server, os.environ.get("LIVEKIT_URL"), os.environ.get("SARVAM_API_KEY"))
        ),
    )

    entrypoint.main()

    assert seen == [(entrypoint.server, "ws://from-dotenv:7880", "from-shell")]


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ('{"user_id": "u1"}', "u1"),
        ('{"user_id": ""}', None),
        ('{"user_id": 42}', None),
        ('["u1"]', None),
        ("not json", None),
        ("", None),
        (None, None),
    ],
)
def test_dispatch_user_id(metadata: str | None, expected: str | None) -> None:
    assert entrypoint.dispatch_user_id(metadata) == expected


async def test_entrypoint_uses_dispatched_users_language_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = InMemoryState()
    user = make_user(display_name="Asha", language="english")
    await InMemoryUserRepo(state).add(user)
    session = FakeSession()
    monkeypatch.setattr(entrypoint, "create_session", lambda p, v: session)
    english = LanguageProfile("english", ProviderSpec("s"), ProviderSpec("t"), "Speak English.")
    factory = FakeFactory(VoicePipeline(english, stt=object(), llm=object(), tts=object()))  # type: ignore[arg-type]
    injector = Injector()
    injector.binder.bind(Settings, to=Settings(_env_file=None))  # type: ignore[call-arg]
    injector.binder.bind(VoicePipelineFactory, to=factory)  # type: ignore[arg-type]
    injector.binder.bind(UserRepo, to=InstanceProvider(InMemoryUserRepo(state)))  # type: ignore[type-abstract]
    ctx = SimpleNamespace(
        proc=SimpleNamespace(userdata={"injector": injector, "vad": object()}),
        room=object(),
        job=SimpleNamespace(metadata=json.dumps({"user_id": user.id})),
    )

    await entrypoint.entrypoint(ctx)  # type: ignore[arg-type]

    assert factory.requested == ["english"]
    agent = session.calls[0][1]["agent"]
    assert "You are talking with Asha." in agent.instructions


async def test_resolve_user_returns_none_for_unknown_user() -> None:
    injector = Injector()
    injector.binder.bind(UserRepo, to=InstanceProvider(InMemoryUserRepo(InMemoryState())))  # type: ignore[type-abstract]

    assert await entrypoint.resolve_user(injector, '{"user_id": "ghost"}') is None
    assert await entrypoint.resolve_user(injector, None) is None

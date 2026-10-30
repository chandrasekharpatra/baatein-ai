import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from injector import Injector, InstanceProvider
from livekit.agents import AgentSession, llm, vad
from livekit.agents.metrics import (
    AgentSessionUsage,
    EOTModelUsage,
    LLMModelUsage,
    STTModelUsage,
    TTSModelUsage,
)
from livekit.agents.voice import FunctionToolsExecutedEvent
from livekit.agents.worker import ServerEnvOption

from baatein.agent import entrypoint
from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.container import build_root
from baatein.domain.models import Pace, Preferences, Role
from baatein.memory.service import MemoryService
from baatein.metrics import CallMetrics
from baatein.ports.repos import UserRepo
from baatein.storage.sqlite.db import Database
from baatein.voice.languages import LanguageProfile, ProviderSpec
from baatein.voice.personas import CASUAL, ELDER, PersonaRegistry, default_persona_registry
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory
from tests.factories import make_thread, make_user
from tests.fakes.repos import InMemoryState, InMemoryUserRepo
from tests.fakes.world import build_world

PROFILE = LanguageProfile(
    "hinglish", ProviderSpec("s"), ProviderSpec("t"), "Hinglish", honorific="ji"
)


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SARVAM_API_KEY", "CARTESIA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, Callable[[Any], None]] = {}
        self.usage = AgentSessionUsage(model_usage=[])

    def on(self, event: str, handler: Callable[[Any], None] | None = None) -> Any:
        def register(handler: Callable[[Any], None]) -> Callable[[Any], None]:
            self.handlers[event] = handler
            return handler

        return register(handler) if handler else register

    async def start(self, **kwargs: Any) -> None:
        self.calls.append(("start", kwargs))

    async def generate_reply(self, **kwargs: Any) -> None:
        self.calls.append(("generate_reply", kwargs))


class FakeDatabase:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeFactory:
    def __init__(self, pipeline: VoicePipeline) -> None:
        self.pipeline = pipeline
        self.requested: list[str] = []
        self.paces: list[float] = []

    def build(self, language_code: str, pace: float = 1.0) -> VoicePipeline:
        self.requested.append(language_code)
        self.paces.append(pace)
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
    assert session.conn_options.stt_conn_options.max_retry == 5
    assert session.conn_options.max_unrecoverable_errors == 7


async def test_entrypoint_starts_assistant_for_default_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = VoicePipeline(PROFILE, stt=object(), llm=object(), tts=object())  # type: ignore[arg-type]
    factory = FakeFactory(pipeline)
    session = FakeSession()
    created: list[tuple[VoicePipeline, object]] = []

    def fake_create_session(p: VoicePipeline, v: object, settings: object) -> FakeSession:
        created.append((p, v))
        return session

    monkeypatch.setattr(entrypoint, "create_session", fake_create_session)
    injector = Injector()
    injector.binder.bind(Settings, to=Settings(_env_file=None))  # type: ignore[call-arg]
    injector.binder.bind(VoicePipelineFactory, to=factory)  # type: ignore[arg-type]
    database = FakeDatabase()
    injector.binder.bind(Database, to=database)  # type: ignore[arg-type]
    shutdown: list[Callable[[str], Awaitable[None]]] = []
    vad_model, room = object(), object()
    ctx = SimpleNamespace(
        proc=SimpleNamespace(userdata={"injector": injector, "vad": vad_model}),
        room=room,
        job=SimpleNamespace(metadata=""),
        add_shutdown_callback=shutdown.append,
    )

    await entrypoint.entrypoint(ctx)  # type: ignore[arg-type]

    assert factory.requested == ["hinglish"]
    assert created == [(pipeline, vad_model)]
    (start, start_kwargs), (reply, reply_kwargs) = session.calls
    assert start == "start"
    assert start_kwargs["room"] is room
    assert isinstance(start_kwargs["agent"], Assistant)
    assert reply == "generate_reply"
    assert reply_kwargs == {"instructions": CASUAL.greeting}
    assert {"error", "user_input_transcribed"} <= set(session.handlers)
    assert factory.paces == [1.0]
    [close] = shutdown
    await close("done")
    assert database.closed


async def test_the_job_ends_as_soon_as_the_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit.agents.voice import CloseEvent
    from livekit.agents.voice.events import CloseReason

    pipeline = VoicePipeline(PROFILE, stt=object(), llm=object(), tts=object())  # type: ignore[arg-type]
    session = FakeSession()
    monkeypatch.setattr(entrypoint, "create_session", lambda *_: session)
    injector = Injector()
    injector.binder.bind(Settings, to=Settings(_env_file=None))  # type: ignore[call-arg]
    injector.binder.bind(VoicePipelineFactory, to=FakeFactory(pipeline))  # type: ignore[arg-type]
    reasons: list[str] = []
    ctx = SimpleNamespace(
        proc=SimpleNamespace(userdata={"injector": injector, "vad": object()}),
        room=object(),
        job=SimpleNamespace(metadata=""),
        add_shutdown_callback=lambda _: None,
        shutdown=lambda reason: reasons.append(reason),
    )
    await entrypoint.entrypoint(ctx)  # type: ignore[arg-type]

    session.handlers["close"](CloseEvent(reason=CloseReason.PARTICIPANT_DISCONNECTED))

    assert reasons == ["session closed: participant_disconnected"]


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
    user = make_user(
        display_name="Asha",
        language="english",
        persona="elder",
        preferences=Preferences(pace=Pace.FAST),
    )
    await InMemoryUserRepo(state).add(user)
    session = FakeSession()
    monkeypatch.setattr(entrypoint, "create_session", lambda p, v, s: session)
    english = LanguageProfile(
        "english", ProviderSpec("s"), ProviderSpec("t"), "Speak English.", honorific="ji"
    )
    factory = FakeFactory(VoicePipeline(english, stt=object(), llm=object(), tts=object()))  # type: ignore[arg-type]
    injector = Injector()
    injector.binder.bind(Settings, to=Settings(_env_file=None))  # type: ignore[call-arg]
    injector.binder.bind(VoicePipelineFactory, to=factory)  # type: ignore[arg-type]
    injector.binder.bind(UserRepo, to=InstanceProvider(InMemoryUserRepo(state)))  # type: ignore[type-abstract]
    injector.binder.bind(PersonaRegistry, to=default_persona_registry())
    world = build_world()
    world.state.users[user.id] = user
    thread = make_thread(user.id, "Ask how the interview went")
    world.state.threads[thread.id] = thread
    injector.binder.bind(MemoryService, to=world.service)
    database = FakeDatabase()
    injector.binder.bind(Database, to=database)  # type: ignore[arg-type]
    shutdown: list[Callable[[str], Awaitable[None]]] = []
    ctx = SimpleNamespace(
        proc=SimpleNamespace(userdata={"injector": injector, "vad": object()}),
        room=SimpleNamespace(name="baatein-room"),
        job=SimpleNamespace(metadata=json.dumps({"user_id": user.id})),
        add_shutdown_callback=shutdown.append,
    )

    await entrypoint.entrypoint(ctx)  # type: ignore[arg-type]
    await ctx.proc.userdata["warm_up"]

    assert (factory.requested, factory.paces) == (["english"], [1.15])
    agent = session.calls[0][1]["agent"]
    assert 'You are talking with Asha. Address them as "Asha ji".' in agent.instructions
    assert ELDER.style in agent.instructions
    assert session.calls[1][1]["instructions"].startswith(ELDER.greeting + " It's ")
    assert f"[{thread.id}] Ask how the interview went" in agent.instructions
    assert "Today's one check-in." in agent.instructions
    [call] = world.state.sessions.values()
    assert (call.user_id, call.room_name) == (user.id, "baatein-room")

    on_item = session.handlers["conversation_item_added"]
    on_item(SimpleNamespace(item=llm.ChatMessage(role="user", content=["Kal interview hai"])))
    on_item(SimpleNamespace(item=llm.ChatMessage(role="assistant", content=["All the best!"])))
    session.handlers["function_tools_executed"](
        FunctionToolsExecutedEvent(function_calls=[], function_call_outputs=[])
    )
    session.usage = AgentSessionUsage(
        model_usage=[LLMModelUsage(provider="anthropic", model="m", input_tokens=900)]
    )
    [close] = shutdown
    await close("user_left")

    assert [(t.role, t.text, t.language) for t in world.state.turns] == [
        (Role.USER, "Kal interview hai", "english"),
        (Role.ASSISTANT, "All the best!", "english"),
    ]
    assert world.state.sessions[call.id].end_reason == "user_left"
    assert world.state.metrics[call.id]["llm_input_tokens"] == 900
    assert database.closed


def test_resolve_persona_falls_back_to_casual(caplog: pytest.LogCaptureFixture) -> None:
    injector = Injector()
    injector.binder.bind(PersonaRegistry, to=default_persona_registry())

    assert entrypoint.resolve_persona(injector, None) is CASUAL
    assert entrypoint.resolve_persona(injector, make_user(persona="elder")) is ELDER
    assert entrypoint.resolve_persona(injector, make_user(persona="pirate")) is CASUAL
    assert "unknown persona 'pirate'" in caplog.text


async def test_end_call_closes_database_even_if_session_end_fails() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)

    async def locked(*_: object, **__: object) -> None:
        raise RuntimeError("database is locked")

    world.deps.sessions.end = locked  # type: ignore[method-assign]
    injector = Injector()
    database = FakeDatabase()
    injector.binder.bind(Database, to=database)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        await entrypoint.end_call(injector, memory, "error")

    assert database.closed


@pytest.mark.parametrize(
    "item",
    [
        llm.ChatMessage(role="system", content=["hidden"]),
        llm.ChatMessage(role="user", content=["   "]),
        llm.ChatMessage(role="user", content=[]),
        object(),
    ],
)
async def test_record_item_skips_non_conversation_items(item: object) -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)

    entrypoint.record_item(memory, item)
    await memory.flush()

    assert world.state.turns == []


async def test_record_item_saves_interrupted_flag() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)

    message = llm.ChatMessage(role="assistant", content=[" Accha "], interrupted=True)
    entrypoint.record_item(memory, message)
    await memory.flush()

    [turn] = world.state.turns
    assert (turn.text, turn.interrupted) == ("Accha", True)


def test_latency_is_noted_per_message() -> None:
    metrics = CallMetrics()
    user = llm.ChatMessage(role="user", content=["Kal interview hai"])
    user.metrics = {"transcription_delay": 0.75, "end_of_turn_delay": 0.3}
    reply = llm.ChatMessage(role="assistant", content=["All the best!"], interrupted=True)
    reply.metrics = {"e2e_latency": 1.2, "llm_node_ttft": 0.6}

    entrypoint.note_latency(metrics, user)
    entrypoint.note_latency(metrics, reply)

    assert (metrics.transcript_ms, metrics.turn_end_ms) == ([750], [300])
    assert (metrics.reply_ms, metrics.llm_first_token_ms, metrics.tts_first_audio_ms) == (
        [1200],
        [600],
        [],
    )
    assert (metrics.user_turns, metrics.replies, metrics.interrupted) == (1, 1, 1)


def test_usage_copies_the_sessions_tokens_audio_and_characters() -> None:
    metrics = CallMetrics()
    usage = AgentSessionUsage(
        model_usage=[
            LLMModelUsage(
                provider="anthropic",
                model="m",
                input_tokens=1000,
                input_cached_tokens=800,
                output_tokens=50,
            ),
            STTModelUsage(provider="sarvam", model="s", audio_duration=12.5),
            TTSModelUsage(provider="cartesia", model="t", characters_count=42),
            EOTModelUsage(provider="livekit", model="e"),
        ]
    )

    entrypoint.note_usage(metrics, usage)

    assert (metrics.llm_input_tokens, metrics.llm_cached_tokens, metrics.llm_output_tokens) == (
        1000,
        800,
        50,
    )
    assert (metrics.stt_audio_seconds, metrics.tts_characters) == (12.5, 42)


def test_tools_are_counted_and_logged_short_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
    metrics = CallMetrics()
    calls = [
        llm.FunctionCall(call_id="1", name="remember", arguments='{"text": "OTP 482913"}'),
        llm.FunctionCall(call_id="2", name="recall", arguments='{"query": "' + "x" * 300 + '"}'),
        llm.FunctionCall(call_id="3", name="recall", arguments='{"query": "Ramesh"}'),
    ]
    outputs = [
        llm.FunctionCallOutput(call_id=c.call_id, name=c.name, output="Saved.", is_error=False)
        for c in calls
    ]

    with caplog.at_level(logging.INFO):
        entrypoint.note_tools(
            metrics, FunctionToolsExecutedEvent(function_calls=calls, function_call_outputs=outputs)
        )

    assert metrics.tools == {"remember": 1, "recall": 2}
    first, second, _ = caplog.messages
    assert first.startswith("tool remember(")
    assert "482913" not in first
    assert first.endswith("→ Saved.")
    assert len(second) < 160
    assert "…" in second


async def test_resolve_user_returns_none_for_unknown_user() -> None:
    injector = Injector()
    injector.binder.bind(UserRepo, to=InstanceProvider(InMemoryUserRepo(InMemoryState())))  # type: ignore[type-abstract]

    assert await entrypoint.resolve_user(injector, '{"user_id": "ghost"}') is None
    assert await entrypoint.resolve_user(injector, None) is None


def test_a_deployed_worker_keeps_two_call_processes_warm() -> None:
    """One per CPU core (LiveKit's default) is ~250 MB each; two keep a server near 1 GB."""
    idle = entrypoint.server._num_idle_processes

    assert ServerEnvOption.getvalue(idle, devmode=False) == 2
    assert ServerEnvOption.getvalue(idle, devmode=True) == 0

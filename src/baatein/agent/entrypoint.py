import asyncio
import json
import logging

from dotenv import find_dotenv, load_dotenv
from injector import Injector
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, llm, vad
from livekit.agents.metrics import (
    AgentSessionUsage,
    LLMModelUsage,
    STTModelUsage,
    TTSModelUsage,
)
from livekit.agents.voice import (
    CloseEvent,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
)
from livekit.plugins import silero

from baatein.agent.assistant import Assistant
from baatein.agent.resilience import SpeechGuard, connect_options
from baatein.config import LiveKitSettings, Settings, VoiceSettings
from baatein.container import build_root
from baatein.domain.models import Role, User
from baatein.memory.context_builder import opening, render_context
from baatein.memory.redact import redact
from baatein.memory.service import CallMemory, MemoryService
from baatein.metrics import CallMetrics
from baatein.ports.repos import UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import migrate
from baatein.voice.personas import (
    CASUAL,
    Persona,
    PersonaRegistry,
    UnknownPersonaError,
    effective_pace,
)
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory

logger = logging.getLogger(__name__)

LOGGED_CHARS = 120
"""How much of a tool's arguments and result goes into its log line."""


def prewarm(proc: JobProcess) -> None:
    settings = Settings()
    migrate(settings.database.sqlite_path())
    proc.userdata["injector"] = build_root(settings=settings)
    proc.userdata["vad"] = silero.VAD.load()
    # One INFO line per embedding request ("Generating embeddings…") says nothing the
    # memory search line doesn't.
    logging.getLogger("pinecone").setLevel(logging.WARNING)


def dispatch_user_id(metadata: str | None) -> str | None:
    try:
        data = json.loads(metadata or "")
    except ValueError:
        return None
    user_id = data.get("user_id") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, str) and user_id else None


async def resolve_user(injector: Injector, metadata: str | None) -> User | None:
    user_id = dispatch_user_id(metadata)
    if user_id is None:
        return None
    users: UserRepo = injector.get(UserRepo)  # type: ignore[type-abstract]
    return await users.get(user_id)


def resolve_persona(injector: Injector, user: User | None) -> Persona:
    """The user's persona, or the casual one if theirs is no longer registered."""
    if user is None:
        return CASUAL
    try:
        return injector.get(PersonaRegistry).get(user.persona)
    except UnknownPersonaError:
        logger.warning("unknown persona %r for user %s, using casual", user.persona, user.id)
        return CASUAL


def create_session(
    pipeline: VoicePipeline, vad_model: vad.VAD, settings: VoiceSettings | None = None
) -> AgentSession[None]:
    return AgentSession(
        vad=vad_model,
        stt=pipeline.stt,
        llm=pipeline.llm,
        tts=pipeline.tts,
        conn_options=connect_options(settings or VoiceSettings()),
    )


async def end_call(injector: Injector, memory: CallMemory | None, reason: str) -> None:
    """Finish the session, then close the database so the job process can exit."""
    try:
        if memory is not None:
            await memory.close(reason)
    finally:
        await injector.get(Database).close()


def record_item(memory: CallMemory, item: object) -> None:
    """Save a finished user or assistant message from the conversation as a turn, and note
    the latency LiveKit measured for it."""
    if not isinstance(item, llm.ChatMessage) or item.role not in ("user", "assistant"):
        return
    if text := (item.text_content or "").strip():
        memory.record(Role(item.role), text, interrupted=item.interrupted)
    note_latency(memory.metrics, item)


def _ms(report: llm.MetricsReport, key: str) -> float | None:
    value = report.get(key)
    return round(value * 1000) if isinstance(value, int | float) else None


def note_latency(metrics: CallMetrics, item: llm.ChatMessage) -> None:
    report = item.metrics
    pairs: tuple[tuple[str, list[float]], ...]
    if item.role == "user":
        metrics.user_turns += 1
        pairs = (
            ("transcription_delay", metrics.transcript_ms),
            ("end_of_turn_delay", metrics.turn_end_ms),
        )
    else:
        metrics.replies += 1
        metrics.interrupted += item.interrupted
        pairs = (
            ("e2e_latency", metrics.reply_ms),
            ("llm_node_ttft", metrics.llm_first_token_ms),
            ("tts_node_ttfb", metrics.tts_first_audio_ms),
        )
    for key, values in pairs:
        if (value := _ms(report, key)) is not None:
            values.append(value)


def note_usage(metrics: CallMetrics, usage: AgentSessionUsage) -> None:
    """Copy what the call used, from LiveKit's totals for the session, into its metrics."""
    for model in usage.model_usage:
        if isinstance(model, LLMModelUsage):
            metrics.llm_input_tokens += model.input_tokens
            metrics.llm_cached_tokens += model.input_cached_tokens
            metrics.llm_output_tokens += model.output_tokens
        elif isinstance(model, STTModelUsage):
            metrics.stt_audio_seconds += model.audio_duration
        elif isinstance(model, TTSModelUsage):
            metrics.tts_characters += model.characters_count


def _short(text: str) -> str:
    text = " ".join(redact(text).split())
    return text if len(text) <= LOGGED_CHARS else text[: LOGGED_CHARS - 1] + "…"


def note_tools(metrics: CallMetrics, event: FunctionToolsExecutedEvent) -> None:
    """Count each tool call, and log it in one line: name, arguments and result, shortened
    and with codes and long numbers redacted."""
    for call, output in zip(event.function_calls, event.function_call_outputs, strict=False):
        metrics.tools[call.name] = metrics.tools.get(call.name, 0) + 1
        logger.info("tool %s(%s) → %s", call.name, _short(call.arguments), _short(output.output))


# INFO by default, also in `dev` (LiveKit's dev default is DEBUG: every SQLite call and STT
# frame). `baatein-agent dev --log-level debug` brings it back.
server = AgentServer(setup_fnc=prewarm, log_level="INFO")


@server.rtc_session(agent_name=LiveKitSettings().agent_name)
async def entrypoint(ctx: JobContext) -> None:
    injector: Injector = ctx.proc.userdata["injector"]
    user = await resolve_user(injector, ctx.job.metadata)
    voice = injector.get(Settings).voice
    language = user.language if user else voice.default_language
    persona = resolve_persona(injector, user)
    pace = effective_pace(persona, user.preferences) if user else 1.0
    pipeline = injector.get(VoicePipelineFactory).build(language, pace)
    session = create_session(pipeline, ctx.proc.userdata["vad"], voice)
    SpeechGuard(session, voice.stt_max_errors, pipeline.profile.cannot_hear)
    user_name = user.display_name if user else None
    memory: CallMemory | None = None
    greeting = persona.greeting

    async def on_shutdown(reason: str) -> None:
        if memory is not None:
            note_usage(memory.metrics, session.usage)
        await end_call(injector, memory, reason)

    ctx.add_shutdown_callback(on_shutdown)

    @session.on("close")
    def _on_close(event: CloseEvent) -> None:
        # Otherwise the job lingers until LiveKit closes the empty room, about 20 s after a
        # hang-up: the call ended late, every end reason said "parent process shutdown",
        # and its summary waited.
        ctx.shutdown(reason=f"session closed: {event.reason.value}")

    if user is None:
        assistant = Assistant(pipeline.profile, user_name=user_name, persona=persona)
    else:
        service = injector.get(MemoryService)
        # Runs alongside the greeting; kept in userdata so the task isn't garbage-collected.
        ctx.proc.userdata["warm_up"] = asyncio.create_task(service.warm_up())
        memory = call_memory = await service.open_call(
            user, room_name=ctx.room.name, language=language
        )

        @session.on("conversation_item_added")
        def _on_item(event: ConversationItemAddedEvent) -> None:
            record_item(call_memory, event.item)

        @session.on("function_tools_executed")
        def _on_tools(event: FunctionToolsExecutedEvent) -> None:
            note_tools(call_memory.metrics, event)

        known = await memory.context()
        logger.info(
            "call started: user %s, %s, %s; context: %d memories, %d storylines (%s),"
            " %d reminders due, last call %s",
            user.id,
            language,
            persona.code,
            len(known.facts),
            len(known.threads),
            "a check-in" if known.check_in else "no check-in",
            sum(d.alerted for d in known.reminders),
            f"{known.last_call.started_at.astimezone(known.now.tzinfo):%d %b %H:%M}"
            if known.last_call
            else "none",
        )
        context = render_context(known)
        greeting = opening(persona.greeting, known)
        assistant = Assistant(
            pipeline.profile,
            user_name=user_name,
            memory=memory,
            memory_context=context,
            persona=persona,
        )

    await session.start(agent=assistant, room=ctx.room)
    await session.generate_reply(instructions=greeting)


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    cli.run_app(server)


if __name__ == "__main__":
    main()

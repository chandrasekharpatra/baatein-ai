import asyncio
import json
import logging

from dotenv import find_dotenv, load_dotenv
from injector import Injector
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, llm, vad
from livekit.agents.voice import ConversationItemAddedEvent
from livekit.plugins import silero

from baatein.agent.assistant import Assistant
from baatein.agent.resilience import SpeechGuard, connect_options
from baatein.config import LiveKitSettings, Settings, VoiceSettings
from baatein.container import build_root
from baatein.domain.models import Role, User
from baatein.memory.context_builder import render_context
from baatein.memory.service import CallMemory, MemoryService
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


def prewarm(proc: JobProcess) -> None:
    settings = Settings()
    migrate(settings.database.sqlite_path())
    proc.userdata["injector"] = build_root(settings=settings)
    proc.userdata["vad"] = silero.VAD.load()


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
    """Save a finished user or assistant message from the conversation as a turn."""
    if not isinstance(item, llm.ChatMessage) or item.role not in ("user", "assistant"):
        return
    if text := (item.text_content or "").strip():
        memory.record(Role(item.role), text, interrupted=item.interrupted)


server = AgentServer(setup_fnc=prewarm)


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

    async def on_shutdown(reason: str) -> None:
        await end_call(injector, memory, reason)

    ctx.add_shutdown_callback(on_shutdown)

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

        context = render_context(await memory.context())
        assistant = Assistant(
            pipeline.profile,
            user_name=user_name,
            memory=memory,
            memory_context=context,
            persona=persona,
        )

    await session.start(agent=assistant, room=ctx.room)
    await session.generate_reply(instructions=persona.greeting)


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    cli.run_app(server)


if __name__ == "__main__":
    main()

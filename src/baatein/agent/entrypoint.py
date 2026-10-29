import asyncio
import json

from dotenv import find_dotenv, load_dotenv
from injector import Injector
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, llm, vad
from livekit.agents.voice import ConversationItemAddedEvent
from livekit.plugins import silero

from baatein.agent.assistant import Assistant
from baatein.config import LiveKitSettings, Settings
from baatein.container import build_root
from baatein.domain.models import Role, User
from baatein.memory.context_builder import render_context
from baatein.memory.service import CallMemory, MemoryService
from baatein.ports.repos import UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import migrate
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory

GREETING = "Greet the user warmly in one short sentence and ask how their day is going."


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


def create_session(pipeline: VoicePipeline, vad_model: vad.VAD) -> AgentSession[None]:
    return AgentSession(vad=vad_model, stt=pipeline.stt, llm=pipeline.llm, tts=pipeline.tts)


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
    language = user.language if user else injector.get(Settings).voice.default_language
    pipeline = injector.get(VoicePipelineFactory).build(language)
    session = create_session(pipeline, ctx.proc.userdata["vad"])
    user_name = user.display_name if user else None
    memory: CallMemory | None = None

    async def on_shutdown(reason: str) -> None:
        await end_call(injector, memory, reason)

    ctx.add_shutdown_callback(on_shutdown)

    if user is None:
        assistant = Assistant(pipeline.profile, user_name=user_name)
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
            pipeline.profile, user_name=user_name, memory=memory, memory_context=context
        )

    await session.start(agent=assistant, room=ctx.room)
    await session.generate_reply(instructions=GREETING)


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    cli.run_app(server)


if __name__ == "__main__":
    main()

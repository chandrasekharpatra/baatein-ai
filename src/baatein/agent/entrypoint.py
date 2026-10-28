from injector import Injector
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, vad
from livekit.plugins import silero

from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.container import build_root
from baatein.voice.providers import VoicePipeline, VoicePipelineFactory

GREETING = "Greet the user warmly in one short sentence and ask how their day is going."


def prewarm(proc: JobProcess) -> None:
    proc.userdata["injector"] = build_root()
    proc.userdata["vad"] = silero.VAD.load()


def create_session(pipeline: VoicePipeline, vad_model: vad.VAD) -> AgentSession[None]:
    return AgentSession(vad=vad_model, stt=pipeline.stt, llm=pipeline.llm, tts=pipeline.tts)


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    injector: Injector = ctx.proc.userdata["injector"]
    language = injector.get(Settings).voice.default_language
    pipeline = injector.get(VoicePipelineFactory).build(language)

    session = create_session(pipeline, ctx.proc.userdata["vad"])
    await session.start(agent=Assistant(pipeline.profile), room=ctx.room)
    await session.generate_reply(instructions=GREETING)


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()

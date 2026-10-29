import argparse
import asyncio
import json
import urllib.error
import urllib.request
import uuid
from typing import Any

import aiohttp
from dotenv import find_dotenv, load_dotenv
from livekit import rtc
from livekit.plugins import sarvam

SILENCE_SECONDS = 3


def post(api: str, path: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{api}{path}", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request) as response:
        result: dict[str, Any] = json.load(response)
        return result


async def synthesize(text: str) -> list[rtc.AudioFrame]:
    async with aiohttp.ClientSession() as http:
        tts = sarvam.TTS(
            target_language_code="hi-IN", model="bulbul:v3", speaker="ritu", http_session=http
        )
        return [event.frame async for event in tts.synthesize(text)]


PASSWORD = "probe-password"


def access_token(api: str, email: str | None) -> str:
    """Log in as `email` (signing it up on first use), or sign up a throwaway user."""
    if email:
        try:
            return str(
                post(api, "/auth/login", {"email": email, "password": PASSWORD})["access_token"]
            )
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
    body = {
        "email": email or f"probe-{uuid.uuid4().hex[:8]}@example.com",
        "password": PASSWORD,
        "display_name": "Probe",
    }
    return str(post(api, "/auth/signup", body)["access_token"])


async def probe(api: str, say: str | None, wait: float, email: str | None) -> None:
    frames = await synthesize(say) if say else []

    token = access_token(api, email)
    grant = post(api, "/livekit/token", {}, token)
    print(f"room: {grant['room_name']}")

    room = rtc.Room()
    transcripts: list[str] = []

    async def read(reader: rtc.TextStreamReader, identity: str) -> None:
        text = await reader.read_all()
        if (reader.info.attributes or {}).get("lk.transcription_final") == "true":
            who = "user " if identity == room.local_participant.identity else "agent"
            transcripts.append(f"{who}: {text}")
            print(transcripts[-1])

    pending: set[asyncio.Task[None]] = set()

    def on_transcription(reader: rtc.TextStreamReader, identity: str) -> None:
        task = asyncio.ensure_future(read(reader, identity))
        pending.add(task)
        task.add_done_callback(pending.discard)

    room.register_text_stream_handler("lk.transcription", on_transcription)
    room.on("participant_connected", lambda p: print(f"joined: {p.identity}"))
    await room.connect(grant["server_url"], grant["participant_token"])

    if frames:
        source = rtc.AudioSource(frames[0].sample_rate, frames[0].num_channels)
        track = rtc.LocalAudioTrack.create_audio_track("probe-mic", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(track, options)

    await asyncio.sleep(wait)
    if frames:
        print(f"speaking: {say}")
        for frame in frames:
            await source.capture_frame(frame)
        rate, channels = frames[0].sample_rate, frames[0].num_channels
        silence = rtc.AudioFrame.create(rate, channels, rate // 100)
        for _ in range(SILENCE_SECONDS * 100):
            await source.capture_frame(silence)
        await asyncio.sleep(wait)

    agents = [p.identity for p in room.remote_participants.values()]
    print(f"remote participants: {agents or 'none'}")
    if not transcripts:
        print("no transcripts received")
    await room.disconnect()


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    parser = argparse.ArgumentParser(description="Place a headless test call to Baatein.")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--say", default="Haan main theek hoon. Aaj office mein bahut kaam tha.")
    parser.add_argument("--no-speech", action="store_true", help="only listen for the greeting")
    parser.add_argument("--wait", type=float, default=8.0, help="seconds to wait per phase")
    parser.add_argument("--email", help="call as this user (signed up on first use) to test memory")
    args = parser.parse_args()
    asyncio.run(probe(args.api, None if args.no_speech else args.say, args.wait, args.email))


if __name__ == "__main__":
    main()

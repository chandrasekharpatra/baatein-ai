---
name: livekit-agents
description: Working on the Baatein voice agent's LiveKit side — AgentSession, plugins (Sarvam, Anthropic, Cartesia), agent dispatch, room tokens, transcripts, LiveKit test helpers, or debugging a call where the agent doesn't join, hear or reply. Covers how to look up LiveKit docs, the API facts and gotchas verified in this repo, and a headless script that places a real test call.
---

# LiveKit Agents in Baatein

## Look things up, don't guess

LiveKit's API changes between minor versions. Before writing LiveKit code:

1. **Docs MCP**: if the `livekit-docs` MCP server is connected (config in `.agent/mcp.json`, URL `https://docs.livekit.io/mcp`), search the docs with its tools.
2. **Otherwise fetch docs directly**: every page has a Markdown version. Append `.md`, e.g. `https://docs.livekit.io/agents/build/events.md`. The full index is `https://docs.livekit.io/llms.txt`.
3. **Installed code wins**: the pinned version is `livekit-agents ~=1.8`. When docs and behaviour disagree, read the source in `.venv/lib/python3.12/site-packages/livekit/` or introspect it with `uv run python -c "import inspect; ..."`.

## Where LiveKit is wired in this repo

| Concern | File |
|---|---|
| Worker, prewarm, per-call entrypoint | `src/baatein/agent/entrypoint.py` |
| Agent instructions, per-turn recall (`on_user_turn_completed`) | `src/baatein/agent/assistant.py` |
| Memory tools (`recall` with its "Hmm…", `remember`, storylines, boundaries, reminders, preferences) | `src/baatein/agent/tools.py` |
| STT/TTS/LLM registries + `VoicePipelineFactory` | `src/baatein/voice/providers.py` |
| Language profiles (which STT/TTS per language) | `src/baatein/voice/languages.py` |
| Room tokens with agent dispatch | `src/baatein/auth/livekit.py` |
| Web client (livekit-client UMD, pinned) | `src/baatein/api/static/index.html` |

To add a provider: register its plugin class in the right registry in `voice/providers.py`, then reference it by name from a language profile or `BAATEIN_LLM__PROVIDER`. `tests/unit/test_default_providers.py` builds every default profile with the real plugin classes and dummy keys, so invalid options fail fast.

## Verified facts (livekit-agents 1.8.x)

- **Dispatch**: the worker registers with `@server.rtc_session(agent_name="baatein")` (explicit dispatch). `/livekit/token` adds `RoomConfiguration(agents=[RoomAgentDispatch(agent_name, metadata='{"user_id": ...}')])`. The agent reads `ctx.job.metadata`. Each call uses a fresh room `baatein-<user>-<ulid>`.
- **Turn detection**: `turn_detection` is left unset so the session picks automatically. `livekit.plugins.turn_detector` is deprecated in favour of `livekit.agents.inference.TurnDetector`.
- **Transcripts**: agents publish text streams on topic `lk.transcription`. The attributes `lk.segment_id` and `lk.transcription_final` identify the segment and mark it final. The browser needs `livekit-client` ≥ 2.9 for `registerTextStreamHandler`; this repo pins 2.22.3.
- **Sarvam**: `STT(model="saaras:v4", mode="codemix", language="hi-IN")` returns Hindi words in Devanagari. Other modes: `transcribe`, `translate`, `verbatim`, `translit`. TTS speakers depend on the model: `bulbul:v3` accepts `shubh`, `ritu`, `rahul`, `pooja`, …, not v2 voices like `anushka`. The plugin validates this when it's constructed.
- **Shutdown callbacks run in parallel** (`asyncio.gather` in `job_proc_lazy_main.py`). Put ordered cleanup (end session, then close the DB) in one callback. An unclosed `aiosqlite` connection keeps a non-daemon thread alive and the job process gets killed after 10 s.
- **Mid-conversation system messages**: a `system` message added to `turn_ctx` in `on_user_turn_completed` is sent to Anthropic inline at its position (`convert_mid_conversation_instructions`), not merged into the system prompt. `turn_ctx` is a copy used for that reply only.
- **Tool schemas and enums**: in strict mode the plugin makes every parameter with a default nullable (`"type": ["string", "null"]`), and Anthropic then rejects any `enum` on it, even one that includes `null`. Keep `Literal[...]` only on required parameters; use `str | None` plus code-side validation for optional ones.
- **Anthropic**: the plugin sends `temperature`/`top_k` only if they're given. Claude 5.5 models reject `temperature`, so `BAATEIN_LLM__TEMPERATURE` is unset by default.

## Gotchas already hit

- **Don't hold the reply for a slow per-turn lookup.** `on_user_turn_completed` blocks the reply. Starting the search early, on each *final* `user_input_transcribed` event, gained only about 25 ms in live calls (Sarvam's final transcript arrives just before the turn commits) and cost an extra search per transcript segment, so it was removed. Let a slow search finish in the background and add its result with `update_chat_ctx` for the next turn.
- **Speech during a tool works.** LiveKit marks the reply's generation done before it runs tools, so `context.session.say("Hmm…", add_to_chat_ctx=False)` from inside a tool plays while it works and stays out of the history.
- **The job doesn't end at hang-up by itself.** After the participant leaves, the session closes but the job lingers until LiveKit closes the empty room (~20 s), and the shutdown reason is "parent process shutdown". `entrypoint` calls `ctx.shutdown()` from the session's `close` event.
- **`turn_ctx` in `on_user_turn_completed` is thrown away after that reply.** Anything added only there is gone next turn. Per-turn recall added memories only there, so Claude later saw its own answer without the memory behind it and apologised for "inventing" it. `Assistant` now also writes each recalled memory into the history once (`update_chat_ctx`).
- **Text-mode `session.run(user_input=...)` skips `on_user_turn_completed`.** The behaviour tests call the hook themselves before each run (`turn()` in `tests/agent/test_assistant_behaviour.py`), so per-turn recall is exercised; do the same in new tests.
- **STT outages end calls silently by default.** LiveKit closes the session after a few unrecoverable STT errors. `agent/resilience.py` (`connect_options`, `SpeechGuard`) retries longer and apologises over TTS before ending. Hinglish TTS is now Cartesia (`language="hi"`, option `speed` for pace); Sarvam is STT only.

- **`.env` isn't loaded by the LiveKit CLI.** `entrypoint.main()` calls `load_dotenv(find_dotenv(usecwd=True))` before `cli.run_app`. Without it: `ws_url is required, or set LIVEKIT_URL`, and plugins miss their API keys.
- **The test judge sends `temperature=0.0`** for any model whose name doesn't contain `gpt-5` (`livekit/agents/voice/run_result.py`). The Anthropic SDK rejects it. Use the `JudgeLLM` subclass in `tests/agent/test_assistant_behaviour.py`, which strips it.
- **`AgentSession` needs a running event loop** when constructed, so tests that build one must be `async`.
- **WSL and browser audio**: if the page connects but no audio flows, WebRTC media isn't crossing from Windows into WSL. Set `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`, then run `wsl --shutdown`.

## Debugging a call

Isolate the failing leg before changing code:

1. **Is the backend healthy?** Run the bundled probe. It needs `livekit-server --dev`, `uv run baatein-agent dev` and `uv run baatein serve` running, with keys in `.env`:

   ```bash
   uv run python .claude/skills/livekit-agents/scripts/probe_call.py
   uv run python .claude/skills/livekit-agents/scripts/probe_call.py --say "Kal mera interview hai"
   ```

   It signs up a throwaway user, gets a token from the API, joins the room headlessly, speaks the sentence via Sarvam TTS, and prints both sides' final transcripts. Pass `--email x@example.com` to reuse one user across calls (signed up on first use) when testing memory, and repeat `--say` to speak several sentences in turn (e.g. a story, then "Haan, yaad rakhna"). The saved turns, memories (`facts`) and people are in `data/baatein.db`.
   - **No agent joins:** dispatch problem. Check that the agent names match and that the worker logs "registered worker".
   - **Agent greets but no user transcript:** STT problem.
   - **User transcript but no reply:** LLM/TTS problem. Check the worker terminal.
2. **The backend works but the browser doesn't**: the web client shows connection state ("Baatein joined", "Listening…", "Baatein is speaking") and a "Tap to hear Baatein" button when autoplay is blocked; it shows no transcript, but `window.baateinTranscript` in the browser console has both sides. Use those to tell mic, playback and network apart (see WSL above).
3. **Server-side room state**: `livekit.api.LiveKitAPI()` with `room.list_rooms`, `room.list_participants` and `agent_dispatch.list_dispatch` shows whether the agent joined and published audio.

## Testing conventions

- Default `pytest` makes no network calls. Agent behaviour tests use `@pytest.mark.llm` and run with `uv run pytest -m llm` (needs `ANTHROPIC_API_KEY`).
- Behaviour tests run in text mode: `async with JudgeLLM(model=...) as llm, AgentSession(llm=llm) as session:`, then `await session.start(Assistant(...))`, `result = await session.run(user_input=...)`, and `result.expect.next_event().is_message(role="assistant").judge(llm, intent=...)`.
- Unit-test the entrypoint by monkeypatching `create_session` and passing a `SimpleNamespace` context with `proc.userdata`, `room` and `job.metadata`. Bind fakes to `Protocol` ports with `InstanceProvider`.

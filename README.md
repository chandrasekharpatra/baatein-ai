# Baatein AI

A Hinglish voice companion that remembers you. Talk to it in the browser in the mix of Hindi and English you actually speak, and over time it builds up memory of your life so it can follow up ("Friday ka interview kaisa gaya?") instead of starting from scratch every call.

Built with [LiveKit Agents](https://github.com/livekit/agents), Claude, Sarvam and SQLite. Python 3.12, no Docker.

> **Status:** All six phases are built. Talk to it live in Hinglish or English:
> - **Memory with consent:** it saves only with your agreement ("Kya main yeh yaad rakhun?").
> - **Follow-ups:** it keeps storylines to check in on later, and dated reminders it brings up in the next call.
> - **Summaries:** it writes a short summary after each call for the next one.
> - **Your control:** memories can be corrected or forgotten, and "Forget me" erases everything.
> - **Metrics:** every call keeps its latency, memory and usage numbers (`baatein metrics`).
>
> See [Roadmap](#roadmap), [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/PLAN.md](docs/PLAN.md).

## How it works

```
 Browser ──1. sign up / log in──▶ FastAPI ──▶ SQLite (users, calls, turns, facts + vectors,
   │      ◀── access token ────                         people, storylines, reminders)
   │──2. POST /livekit/token ─▶  room token + agent dispatch {"user_id": ...}
   │
   └──3. WebRTC audio ─▶ LiveKit server ──dispatches──▶ Agent worker
                                                      Sarvam STT → Claude → Cartesia/Sarvam TTS
```

1. The web client signs you up or logs you in and gets an access token.
2. `/livekit/token` creates a fresh room and a LiveKit token that dispatches the `baatein` agent, signed with your user id.
3. The agent worker joins the room, loads your profile (name and language) and what it remembers about you, opens a session and talks with you in real time. Every message is saved as it happens (with codes and long numbers redacted); on each of your turns it searches your saved memories without making the reply wait: what it finds joins the conversation by its next reply, and when it needs a memory right away it looks it up with the `recall` tool (saying "Hmm…" if that takes a moment). It opens by picking up from earlier calls and any reminder that's due. It asks before saving anything, can correct or forget a memory when you ask, and after the call `baatein serve` writes a short summary for next time.

## Tech stack

| Concern | Choice |
|---|---|
| Realtime voice | LiveKit Agents 1.8, self-hosted `livekit-server` |
| Speech-to-text | Sarvam `saaras:v4` in `codemix` mode (Hindi, Odia, Kannada mixed with English) |
| LLM | Claude Haiku 5.5 (configurable) |
| Text-to-speech | Cartesia `sonic-3` for Hinglish, English and Kannada; Sarvam `bulbul:v3` for Odia (Cartesia has no Odia) |
| Storage | SQLite (WAL) via `aiosqlite`, numbered SQL migrations |
| Memory search | `sqlite-vec` vectors + FTS5 keyword search, fused with reciprocal rank fusion |
| Embeddings | Pinecone hosted `llama-text-embed-v2` (2048-d); FastEmbed local models also work (see [docs/RETRIEVAL_BENCHMARK.md](docs/RETRIEVAL_BENCHMARK.md)) |
| API and auth | FastAPI, Argon2 password hashing, JWT access tokens |
| Dependency injection | [`injector`](https://github.com/python-injector/injector) |
| Tooling | `uv`, ruff, mypy (strict), pytest with 100% branch coverage |

## Getting started

For a step-by-step walkthrough from a fresh machine, including installing LiveKit, getting API keys, WSL notes and troubleshooting, see **[docs/RUNNING.md](docs/RUNNING.md)**. The short version follows.

### Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- `livekit-server`: see the [LiveKit install instructions](https://github.com/livekit/livekit#linux) (Linux: `curl -sSL https://get.livekit.io | bash`, macOS: `brew install livekit`)
- API keys: [Anthropic](https://console.anthropic.com/), [Sarvam](https://dashboard.sarvam.ai/) (speech-to-text, and the Odia voice), [Cartesia](https://play.cartesia.ai/) (the Hinglish, English and Kannada voices) and [Pinecone](https://app.pinecone.io/) (memory embeddings)

### Install

```bash
uv sync
cp .env.example .env
uv run pre-commit install
```

Fill in `.env`:

- `ANTHROPIC_API_KEY`, `SARVAM_API_KEY`, `CARTESIA_API_KEY`, `PINECONE_API_KEY`
- `BAATEIN_AUTH__JWT_SECRET`: a long random string, e.g. `openssl rand -hex 32`
- Keep the `LIVEKIT_*` defaults; they match `livekit-server --dev`.

### Run

Start three processes from the project root:

```bash
livekit-server --dev          # LiveKit on ws://localhost:7880
uv run baatein-agent dev      # agent worker (hot reload)
uv run baatein serve          # API + web client on http://127.0.0.1:8000
```

Open http://localhost:8000, sign up, allow the microphone and tap the green call button. The orb follows whoever is speaking; the gear opens settings (erase memories, log out).

To try the agent without a server or browser, run `uv run baatein-agent console`. It uses your local mic and speakers and the default language.

## Configuration

Settings are read from the environment and `.env`. App settings use the `BAATEIN_` prefix, with `__` separating the section from the field.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude (read by the LiveKit plugin) |
| `SARVAM_API_KEY` | — | Sarvam STT (every language) and TTS (Odia) |
| `CARTESIA_API_KEY` | — | Cartesia TTS (Hinglish, English, Kannada) |
| `PINECONE_API_KEY` | — | Hosted memory embeddings (the default embedder) |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit server the agent and tokens point to |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | — | Sign room tokens; register the worker |
| `LIVEKIT_AGENT_NAME` | `baatein` | Name used for explicit agent dispatch |
| `LIVEKIT_TOKEN_TTL_MINUTES` | `15` | Lifetime of a room token |
| `BAATEIN_AUTH__JWT_SECRET` | — | Required by the API to issue access tokens |
| `BAATEIN_AUTH__TOKEN_TTL_MINUTES` | `10080` (7 days) | Access token lifetime |
| `BAATEIN_LLM__PROVIDER` / `__MODEL` | `anthropic` / `claude-haiku-5-5` | Conversation LLM |
| `BAATEIN_LLM__TEMPERATURE` | unset | Only sent when set; Claude 5.5 models reject it |
| `BAATEIN_VOICE__DEFAULT_LANGUAGE` / `__DEFAULT_PERSONA` | `hinglish` / `casual` | Used when a call has no known user (e.g. `console`) |
| `BAATEIN_VOICE__STT_MAX_ERRORS` | `6` | Speech-to-text failures in a row (about 10 s each) before Baatein apologises and ends the call |
| `BAATEIN_DATABASE__URL` | `sqlite:///data/baatein.db` | Database file (relative to the working directory) |
| `BAATEIN_EMBEDDING__PROVIDER` / `__MODEL` / `__DIMENSIONS` | `pinecone` / `llama-text-embed-v2` / `2048` | Fact embeddings (needs `PINECONE_API_KEY`); run `baatein reembed` after changing |
| `BAATEIN_EMBEDDING__MIN_SIMILARITY` | measured per model | Similarity cut-offs by language, e.g. `{"english": 0.2, "default": 0.12}`; by default the values measured for the model in `config.py:MEASURED_CUT_OFFS` |
| `BAATEIN_EMBEDDING__DUPLICATE_SIMILARITY` | measured per model | Similarity at which a new memory counts as already saved (llama: 0.80); unmeasured models skip the check |
| `BAATEIN_EMBEDDING__CACHE_DIR` | `data/models` | Where FastEmbed downloads a local model |
| `BAATEIN_MEMORY__CONTEXT_FACTS` / `__CONTEXT_THREADS` / `__CONTEXT_DECLINED` | `10` / `10` / `10` | How much memory goes into the agent's instructions at call start |
| `BAATEIN_MEMORY__RECALL_LIMIT` | `5` | Facts returned by per-turn recall and the `recall` tool |
| `BAATEIN_MEMORY__CHECK_IN_GAP_DAYS` / `__CHECK_IN_MAX_OFFERS` | `3` / `3` | A storyline is checked in on at most every 3 days, and offered at most 3 times |
| `BAATEIN_VOICE__TTS` | unset | Another voice for a language, as JSON by language code (e.g. a Sarvam `bulbul:v4-flash` expressive voice for a demo); unset keeps each profile's own |
| `BAATEIN_MEMORY__RECALL_TIMEOUT_SECONDS` | `0` | How long a reply waits for per-turn recall; a slower search's memories join the next turn. Set `0.1` with a local FastEmbed model |
| `BAATEIN_MEMORY__THINKING_AFTER_SECONDS` | `0.3` | If the `recall` tool takes longer, Baatein says "Hmm…" meanwhile |
| `BAATEIN_DEV__SEED_ENABLED` | `false` | Development only: `/dev/seed` and Settings → "Test memories" in the web client |
| `BAATEIN_DEV__CLOCK_FILE` | unset | Demos only: memory and reminders take "now" from this file (`baatein demo-date`); never set in production |
| `BAATEIN_FINALIZE__MODEL` / `__EFFORT` | `claude-haiku-5-5` / `low` | The model that writes each call's summary |
| `BAATEIN_FINALIZE__INTERVAL_SECONDS` | `10` | How often `baatein serve` summarises finished calls; `0` turns it off |
| `BAATEIN_FINALIZE__MAX_ATTEMPTS` / `__RETRY_BASE_SECONDS` | `5` / `60` | Failed summaries are retried, waiting 1, 2, 4… minutes (up to an hour) |

## Commands

| Command | What it does |
|---|---|
| `uv run baatein serve [--host] [--port]` | Run the API and web client (migrates first) |
| `uv run baatein migrate` | Create or upgrade the database schema |
| `uv run baatein reembed` | Rebuild the fact vector index for the current embedding model |
| `uv run --env-file .env baatein finalize [--session ID]` | Summarise finished calls now (`serve` also does this every 10 s); `--session` re-runs one call |
| `uv run baatein metrics [--days 7] [--email E]` | Reply latency (and where it goes), memory search and usage of recent calls |
| `uv run baatein demo-date YYYY-MM-DD [--time 10:00] [--tz Asia/Kolkata] \| --clear` | Demos only (needs `BAATEIN_DEV__CLOCK_FILE`): make it a chosen day for memory, reminders and summaries, so one recording session can show several days |
| `uv run baatein forget-user EMAIL --yes` | Erase everything Baatein remembers about a user (memories, people, storylines, reminders, calls); the account stays |
| `uv run baatein seed FILE [--replace]` | Create a test account with artificial memories (see `docs/SEED_DATA.md`) |
| `uv run baatein-agent dev` | Run the agent worker against a LiveKit server, with hot reload |
| `uv run baatein-agent start` | Run the agent worker in production mode |
| `uv run baatein-agent console` | Talk to the agent in the terminal with local audio |

Migrations also run automatically when the API or an agent worker starts.

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Web client |
| `GET` | `/health` | — | Liveness check |
| `POST` | `/auth/signup` | — | `{email, password, display_name?, language?, persona?}` → access token + user; `language` is `hinglish`, `english`, `odia` or `kannada` |
| `POST` | `/auth/login` | — | `{email, password}` → access token + user |
| `GET` | `/me` | Bearer | Current user |
| `POST` | `/me/forget` | Bearer + `{password}` | "Forget me": erase everything Baatein remembers; the account stays (204) |
| `POST` | `/livekit/token` | Bearer | `{server_url, participant_token, room_name}` for a new call |
| `GET` / `POST` | `/dev/seed` | — / Bearer | Development only (`BAATEIN_DEV__SEED_ENABLED`): check it's on / load a seed file into your account |

Errors use `409` for a duplicate email, `401` for bad credentials or tokens, and `422` for invalid input (weak password, unknown language, bad email). Interactive docs are at http://127.0.0.1:8000/docs.

## Project layout

```
src/baatein/
  agent/          LiveKit worker: prewarm, per-call entrypoint, Assistant, memory tools
  api/            FastAPI app and static web client
  auth/           Argon2 hashing, JWT tokens, LiveKit call tokens, AuthService
  domain/         Models (User, Session, Turn, Fact, Person, Thread, Boundary, Reminder…), errors, ids
  memory/         MemoryService (per-call memory), context and greeting, embedders, ranking,
                  end-of-call summary (analyzer, finalizer), reminder dates, redaction, seeds
  ports/          Protocols every adapter implements (repos, memory, embedder, analyzer, auth, clock)
  storage/sqlite/ Connection, migrations, repositories, memory store with sqlite-vec
  voice/          Language profiles and STT/TTS/LLM provider registries
  config.py       Settings
  metrics.py      Per-call numbers and the `baatein metrics` report
  container.py    Dependency injection modules
  cli.py          `baatein` command
tests/
  unit/ contract/ integration/ api/ agent/   (contract suites run against SQLite and in-memory fakes)
  fakes/ factories.py
docs/PLAN.md      Architecture, status and roadmap
docs/DECISIONS.md Decision log: what was chosen, the options, and why
docs/RUNNING.md   Step-by-step setup from a fresh machine
docs/             Also: memory design, Phase 5 slices, seed data, retrieval benchmark
.claude/skills/   Claude Code skill for LiveKit work, with a headless call probe
```

The code uses ports and adapters: business logic depends on the `Protocol`s in `ports/`, and `container.py` binds them to concrete adapters. Tests and future backends (e.g. Postgres) swap adapters without touching callers.

## Testing

```bash
uv run pytest              # unit, contract, integration and API tests, with coverage; no network
uv run pytest -m model     # the local FastEmbed model (downloads ~0.22 GB to data/models once)
uv run pytest -m llm       # agent behaviour against real Claude (needs ANTHROPIC_API_KEY)
uv run ruff check && uv run ruff format --check && uv run mypy
```

Coverage is gated at 95% with branch coverage and currently sits at 100%. Pre-commit and CI run ruff, mypy and the default pytest suite.

To test the live stack end to end without a browser, the bundled probe signs up a throwaway user, joins a call, speaks a Hinglish sentence and prints both sides' transcripts:

```bash
uv run python .claude/skills/livekit-agents/scripts/probe_call.py --say "Kal mera interview hai"
```

Pass `--email someone@example.com` to call as the same user every time (signed up on first use), which is how to check that the agent remembers things between calls.

## Extending

- **New language**: add a `LanguageProfile` in `src/baatein/voice/languages.py` with its STT, TTS, reply style, and the respectful address the elder persona uses (`honorific` after a name, `formal_you`). Add it to the signup `<select>` in `api/static/index.html` so users can pick it (Odia and Kannada are built but not offered there yet). Check the TTS provider really supports it: Cartesia rejected Odia on every model.
- **New STT/TTS/LLM provider**: register the LiveKit plugin class in `src/baatein/voice/providers.py`, then reference it by name from a language profile or `BAATEIN_LLM__PROVIDER`.
- **New embedding provider**: implement the `Embedder` protocol, register it in `src/baatein/memory/embeddings/registry.py`, set `BAATEIN_EMBEDDING__*` and run `baatein reembed`. Measure the model's cut-offs and duplicate similarity (`bench/retrieval`) and add them to `MEASURED_CUT_OFFS` / `MEASURED_DUPLICATES` in `config.py`: without them the cut-off falls back to 0.3 and the duplicate check is skipped.
- **Schema change**: add `src/baatein/storage/sqlite/migrations/NNNN_name.sql`, numbered after the last one, without `BEGIN`/`COMMIT`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ws_url is required, or set LIVEKIT_URL` | Create `.env` from `.env.example` and run commands from the project root |
| `` `temperature` is deprecated for this model `` | Unset `BAATEIN_LLM__TEMPERATURE` |
| API fails with `BAATEIN_AUTH__JWT_SECRET must be set` | Set it in `.env` |
| Page connects but you hear nothing (WSL) | Add `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown` |
| "Tap to hear Baatein" appears | The browser blocked autoplay; tap it |
| The agent never joins | Check the worker logs `registered worker` with agent name `baatein`, and that `LIVEKIT_AGENT_NAME` matches in both processes |

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold, settings, provider registries, DI, console agent | ✅ |
| 1 | SQLite layer, migrations, repositories | ✅ |
| 2 | Fact memory: embeddings, sqlite-vec + FTS5 hybrid search | ✅ |
| 3 | Email/password auth, LiveKit tokens, web client | ✅ |
| 4 | Call lifecycle: save transcripts, load context at call start, per-turn memory retrieval, memory tools | ✅ |
| 5 | Consent-based memory layer: consent, storylines, persona, reminders, end-of-call summary, scenario evals | ✅ |
| 6 | "Forget me", per-call metrics, web client redesign, demo clock and multi-day demo | ✅ built; demo video to record |

Design decisions, with the options considered and why, are in [docs/DECISIONS.md](docs/DECISIONS.md); the architecture is in [docs/PLAN.md](docs/PLAN.md).

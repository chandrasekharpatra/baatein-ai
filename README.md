# Baatein AI

A Hinglish voice companion that remembers you. Talk to it in the browser in the mix of Hindi and English you actually speak, and over time it builds up memory of your life so it can follow up ("Friday ka interview kaisa gaya?") instead of starting from scratch every call.

Built with [LiveKit Agents](https://github.com/livekit/agents), Claude, Sarvam and SQLite. Python 3.12, no Docker.

> **Status:** Phases 0–4 of 6 are done. You can sign up, start a call and have a live Hinglish voice conversation. Calls are saved turn by turn, the agent starts with what it remembers, looks up relevant facts on every turn, and saves follow-ups ("ask how Friday's interview went") for the next call. Turning finished calls into summaries and facts is Phase 5. See [Roadmap](#roadmap) and [docs/PLAN.md](docs/PLAN.md).

## How it works

```
 Browser ──1. sign up / log in──▶ FastAPI ──▶ SQLite (users, sessions, turns,
   │      ◀── access token ────                         follow-ups, facts + vectors)
   │──2. POST /livekit/token ─▶  room token + agent dispatch {"user_id": ...}
   │
   └──3. WebRTC audio ─▶ LiveKit server ──dispatches──▶ Agent worker
                                                      Sarvam STT → Claude → Sarvam TTS
```

1. The web client signs you up or logs you in and gets an access token.
2. `/livekit/token` creates a fresh room and a LiveKit token that dispatches the `baatein` agent, signed with your user id.
3. The agent worker joins the room, loads your profile (name and language) and what it remembers about you, opens a session and talks with you in real time. Every message is saved as it happens; on each of your turns it searches your facts, and it can save or resolve follow-ups.

## Tech stack

| Concern | Choice |
|---|---|
| Realtime voice | LiveKit Agents 1.8, self-hosted `livekit-server` |
| Speech-to-text | Sarvam `saaras:v4` in `codemix` mode (Hinglish) |
| LLM | Claude Haiku 5.5 (configurable) |
| Text-to-speech | Sarvam `bulbul:v3` for Hinglish, Cartesia `sonic-3` for English |
| Storage | SQLite (WAL) via `aiosqlite`, numbered SQL migrations |
| Memory search | `sqlite-vec` vectors + FTS5 keyword search, fused with reciprocal rank fusion |
| Embeddings | FastEmbed `paraphrase-multilingual-MiniLM-L12-v2` (local, 384-d) |
| API and auth | FastAPI, Argon2 password hashing, JWT access tokens |
| Dependency injection | [`injector`](https://github.com/python-injector/injector) |
| Tooling | `uv`, ruff, mypy (strict), pytest with 100% branch coverage |

## Getting started

For a step-by-step walkthrough from a fresh machine, including installing LiveKit, getting API keys, WSL notes and troubleshooting, see **[docs/RUNNING.md](docs/RUNNING.md)**. The short version follows.

### Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- `livekit-server`: see the [LiveKit install instructions](https://github.com/livekit/livekit#linux) (Linux: `curl -sSL https://get.livekit.io | bash`, macOS: `brew install livekit`)
- API keys: [Anthropic](https://console.anthropic.com/), [Sarvam](https://dashboard.sarvam.ai/), and [Cartesia](https://play.cartesia.ai/) (Cartesia is only used by the English voice)

### Install

```bash
uv sync
cp .env.example .env
uv run pre-commit install
```

Fill in `.env`:

- `ANTHROPIC_API_KEY`, `SARVAM_API_KEY`, `CARTESIA_API_KEY`
- `BAATEIN_AUTH__JWT_SECRET`: a long random string, e.g. `openssl rand -hex 32`
- Keep the `LIVEKIT_*` defaults; they match `livekit-server --dev`.

### Run

Start three processes from the project root:

```bash
livekit-server --dev          # LiveKit on ws://localhost:7880
uv run baatein-agent dev      # agent worker (hot reload)
uv run baatein serve          # API + web client on http://127.0.0.1:8000
```

Open http://localhost:8000, sign up, allow the microphone and press **Start talking**. The page shows live transcripts of both sides.

To try the agent without a server or browser, run `uv run baatein-agent console`. It uses your local mic and speakers and the default language.

## Configuration

Settings are read from the environment and `.env`. App settings use the `BAATEIN_` prefix, with `__` separating the section from the field.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude (read by the LiveKit plugin) |
| `SARVAM_API_KEY` | — | Sarvam STT and TTS |
| `CARTESIA_API_KEY` | — | Cartesia TTS (English profile) |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit server the agent and tokens point to |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | — | Sign room tokens; register the worker |
| `LIVEKIT_AGENT_NAME` | `baatein` | Name used for explicit agent dispatch |
| `BAATEIN_AUTH__JWT_SECRET` | — | Required by the API to issue access tokens |
| `BAATEIN_AUTH__TOKEN_TTL_MINUTES` | `10080` (7 days) | Access token lifetime |
| `BAATEIN_LLM__PROVIDER` / `__MODEL` | `anthropic` / `claude-haiku-5-5` | Conversation LLM |
| `BAATEIN_LLM__TEMPERATURE` | unset | Only sent when set; Claude 5.5 models reject it |
| `BAATEIN_VOICE__DEFAULT_LANGUAGE` | `hinglish` | Used when a call has no known user (e.g. `console`) |
| `BAATEIN_DATABASE__URL` | `sqlite:///data/baatein.db` | Database file (relative to the working directory) |
| `BAATEIN_EMBEDDING__PROVIDER` / `__MODEL` / `__DIMENSIONS` | `fastembed` / MiniLM / `384` | Fact embeddings; run `baatein reembed` after changing |
| `BAATEIN_EMBEDDING__CACHE_DIR` | `data/models` | Where the embedding model is downloaded |
| `BAATEIN_MEMORY__MIN_SIMILARITY` | `0.3` | Cosine threshold for semantic fact matches |
| `BAATEIN_MEMORY__CONTEXT_SUMMARIES` / `__CONTEXT_FOLLOWUPS` / `__CONTEXT_FACTS` | `3` / `5` / `10` | How much memory goes into the agent's instructions at call start |
| `BAATEIN_MEMORY__RECALL_LIMIT` | `5` | Facts returned by per-turn recall and the `recall_memory` tool |
| `BAATEIN_MEMORY__RECALL_TIMEOUT_SECONDS` | `0.3` | Per-turn recall is skipped if it takes longer |

## Commands

| Command | What it does |
|---|---|
| `uv run baatein serve [--host] [--port]` | Run the API and web client (migrates first) |
| `uv run baatein migrate` | Create or upgrade the database schema |
| `uv run baatein reembed` | Rebuild the fact vector index for the current embedding model |
| `uv run baatein-agent dev` | Run the agent worker against a LiveKit server, with hot reload |
| `uv run baatein-agent start` | Run the agent worker in production mode |
| `uv run baatein-agent console` | Talk to the agent in the terminal with local audio |

Migrations also run automatically when the API or an agent worker starts.

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Web client |
| `GET` | `/health` | — | Liveness check |
| `POST` | `/auth/signup` | — | `{email, password, display_name?, language?}` → access token + user |
| `POST` | `/auth/login` | — | `{email, password}` → access token + user |
| `GET` | `/me` | Bearer | Current user |
| `POST` | `/livekit/token` | Bearer | `{server_url, participant_token, room_name}` for a new call |

Errors use `409` for a duplicate email, `401` for bad credentials or tokens, and `422` for invalid input (weak password, unknown language, bad email). Interactive docs are at http://127.0.0.1:8000/docs.

## Project layout

```
src/baatein/
  agent/          LiveKit worker: prewarm, per-call entrypoint, Assistant, memory tools
  api/            FastAPI app and static web client
  auth/           Argon2 hashing, JWT tokens, LiveKit call tokens, AuthService
  domain/         Models (User, Session, Turn, FollowUp, Fact), errors, ids
  memory/         MemoryService (per-call memory), context rendering, embedders, ranking
  ports/          Protocols every adapter implements (repos, memory, embedder, auth, clock)
  storage/sqlite/ Connection, migrations, repositories, memory store with sqlite-vec
  voice/          Language profiles and STT/TTS/LLM provider registries
  config.py       Settings
  container.py    Dependency injection modules
  cli.py          `baatein` command
tests/
  unit/ contract/ integration/ api/ agent/   (contract suites run against SQLite and in-memory fakes)
  fakes/ factories.py
docs/PLAN.md      Architecture, decisions, status and roadmap
.claude/skills/   Claude Code skill for LiveKit work, with a headless call probe
```

The code uses ports and adapters: business logic depends on the `Protocol`s in `ports/`, and `container.py` binds them to concrete adapters. Tests and future backends (e.g. Postgres) swap adapters without touching callers.

## Testing

```bash
uv run pytest              # unit, contract, integration and API tests, with coverage; no network
uv run pytest -m model     # real embedding model (downloads ~0.22 GB to data/models once)
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

- **New language**: add a `LanguageProfile` in `src/baatein/voice/languages.py` with its STT, TTS and reply style. Users pick it at signup.
- **New STT/TTS/LLM provider**: register the LiveKit plugin class in `src/baatein/voice/providers.py`, then reference it by name from a language profile or `BAATEIN_LLM__PROVIDER`.
- **New embedding provider**: implement the `Embedder` protocol, register it in `src/baatein/memory/embeddings/registry.py`, set `BAATEIN_EMBEDDING__*` and run `baatein reembed`.
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
| 5 | End-of-call analyzer: summary, follow-ups, fact updates; proactive opener | Next |
| 6 | "Forget me", observability, evals, demo polish | |

Design decisions, including why the project uses its own memory store instead of Mem0, are in [docs/PLAN.md](docs/PLAN.md).

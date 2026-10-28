# Baatein AI

Hinglish voice companion with long-term memory, built on [LiveKit Agents](https://github.com/livekit/agents). See [docs/PLAN.md](docs/PLAN.md) for the architecture and roadmap.

## Setup

```bash
uv sync
cp .env.example .env   # fill in ANTHROPIC_API_KEY, SARVAM_API_KEY, CARTESIA_API_KEY
uv run pre-commit install
```

## Run

Quick check with your mic and speakers, no server needed:

```bash
uv run baatein-agent console
```

Full flow (web client → login → voice call), three terminals:

```bash
livekit-server --dev            # install: curl -sSL https://get.livekit.io | bash
uv run baatein-agent dev        # agent worker, joins rooms it is dispatched to
uv run baatein serve            # API + web client on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000, sign up, and press "Start talking". The token endpoint dispatches the `baatein` agent with your user id, so the agent knows who it is talking to.

Other commands: `uv run baatein migrate`, `uv run baatein reembed`.

## Test

```bash
uv run pytest            # fast suite with coverage, no network
uv run pytest -m llm     # agent behaviour tests against real Claude
uv run ruff check && uv run mypy
```

## Configuration

App settings use the `BAATEIN_` prefix with `__` for nesting, e.g. `BAATEIN_LLM__MODEL=claude-sonnet-5-5`, `BAATEIN_VOICE__DEFAULT_LANGUAGE=english`. Languages live in `src/baatein/voice/languages.py`; providers are registered in `src/baatein/voice/providers.py`.

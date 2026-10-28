# Baatein AI

Hinglish voice companion with long-term memory, built on [LiveKit Agents](https://github.com/livekit/agents). See [docs/PLAN.md](docs/PLAN.md) for the architecture and roadmap.

## Setup

```bash
uv sync
cp .env.example .env   # fill in ANTHROPIC_API_KEY, SARVAM_API_KEY, CARTESIA_API_KEY
uv run pre-commit install
```

## Run

```bash
uv run baatein-agent console   # talk to the agent with your mic/speakers, no server needed
uv run baatein-agent dev       # connect to a LiveKit server (LIVEKIT_URL, ...)
```

For `dev`, run a local server with `livekit-server --dev` (install: `curl -sSL https://get.livekit.io | bash`).

## Test

```bash
uv run pytest            # fast suite with coverage, no network
uv run pytest -m llm     # agent behaviour tests against real Claude
uv run ruff check && uv run mypy
```

## Configuration

App settings use the `BAATEIN_` prefix with `__` for nesting, e.g. `BAATEIN_LLM__MODEL=claude-sonnet-5-5`, `BAATEIN_VOICE__DEFAULT_LANGUAGE=english`. Languages live in `src/baatein/voice/languages.py`; providers are registered in `src/baatein/voice/providers.py`.

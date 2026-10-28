# Baatein AI — Voice Agent with Long-Term Memory

Hackathon plan. Python, LiveKit Agents (self-hosted), Claude, Sarvam/Cartesia, SQLite + `sqlite-vec`, FastEmbed, `injector`. No Docker. Coverage-first testing.

## Status (2026-10-29)

**Done:** Phases 0–3. You can sign up in the web client, start a call, and have a live Hinglish voice conversation (Sarvam STT → Claude Haiku 5.5 → Sarvam TTS). Verified end to end on 2026-10-29, both from the browser and with a headless test client.

**Not yet:** nothing from a call is saved or remembered. Every call starts fresh. The storage for this (sessions, turns, follow-ups, facts with hybrid search) exists, but nothing calls it during a call.

**Next: Phase 4** (see §10):
1. `SessionModule` + child injector per call (`CurrentUser`, session id).
2. On call start: create the `sessions` row; on `close`/shutdown: `SessionRepo.end`.
3. Persist each `conversation_item_added` message as a `Turn` (ordered `seq`, role, interrupted flag).
4. `MemoryService.load_context`: last summaries, open follow-ups, top active facts → `context_builder` → initial `ChatContext`.
5. `Assistant.on_user_turn_completed`: `MemoryStore.search` under a ~300 ms timeout, inject results as hidden context.
6. Tools: `recall_memory`, `add_followup`, `resolve_followup`.
7. Decide STT script: Sarvam `codemix` returns Devanagari for Hindi words; try `translit` mode (Roman) for consistent transcripts/search.

**How to run** (see README): `cp .env.example .env` and fill keys, then `livekit-server --dev`, `uv run baatein-agent dev`, `uv run baatein serve`, open http://localhost:8000.

**Gotchas found so far**
- Claude 5.5 rejects `temperature`; `BAATEIN_LLM__TEMPERATURE` is unset by default and only sent when set.
- LiveKit's test judge hardcodes `temperature=0.0`; `tests/agent/test_assistant_behaviour.py` uses a `JudgeLLM` subclass that strips it.
- The agent CLI doesn't read `.env` by itself; `entrypoint.main()` loads it from the current directory before LiveKit spawns workers.
- `injector` can't bind a plain instance to a `Protocol`; use a `@provider` method or `InstanceProvider`.
- Python's `executescript` commits implicitly, so migrations run statement by statement.
- WSL: if the browser connects but no audio flows, enable `networkingMode=mirrored` in `.wslconfig`.

## 1. Goals

- Real-time Hinglish voice agent, extensible to other languages via config.
- Remembers users across calls and **moves the conversation forward**: opens with relevant follow-ups ("Friday ka interview kaisa gaya?") and uses past facts mid-conversation.
- Modular (ports & adapters + DI + provider registries), high test coverage, SQLite now, Postgres later without rewriting business logic.

## 2. Stack decisions

| Concern | Choice | Notes |
|---|---|---|
| Realtime transport | LiveKit server, self-hosted native binary (`livekit-server --dev`), no Docker | `console` mode needs no server at all; swap to LiveKit Cloud via env vars |
| Agent framework | `livekit-agents ~=1.8` | `AgentSession`, `Agent`, `@function_tool`, `on_user_turn_completed`, text-mode tests |
| LLM (conversation) | Claude via `livekit-plugins-anthropic` | Default `claude-haiku-5-5` for latency; `claude-sonnet-5-5` via env |
| LLM (memory/analysis) | Claude `claude-haiku-5-5` | One structured call per finished session: summary, follow-ups, fact changes |
| STT | Sarvam `STT(mode="codemix")` | Code-mixed Hinglish |
| TTS | Sarvam Bulbul or Cartesia Sonic | Chosen per language profile |
| Long-term memory | **Own `MemoryStore`** on SQLite: `facts` table + `sqlite-vec` + FTS5 | Replaces Mem0 (see §3) |
| Embeddings | FastEmbed `paraphrase-multilingual-MiniLM-L12-v2` (384-d, 0.22 GB, in-process) | Behind `Embedder` port; Pinecone `llama-text-embed-v2` later (see §12) |
| App DB | SQLite via `aiosqlite`, raw SQL, repository pattern | WAL, FK on, busy_timeout; migrations via `PRAGMA user_version` |
| Auth | Email + password, Argon2 (`argon2-cffi`), JWT | FastAPI service issues LiveKit tokens |
| DI | `python-injector` | Root injector per process, child injector per call |
| Tooling | `uv`, Python 3.12, ruff, mypy (strict on `src/`), pytest, pytest-asyncio, pytest-cov | |

**Postgres migration path:** repositories and `MemoryStore` get Postgres adapters that pass the same contract suites; `sqlite-vec` → `pgvector`, FTS5 → `tsvector`.

## 3. Memory design (why not Mem0)

Mem0 would give us fact extraction, ADD/UPDATE/DELETE reconciliation and vector search, but it has no SQLite vector store (forcing extra infra without Docker), costs two extra LLM calls per add, needs an internal-attribute hack for custom embedders, and doesn't cover follow-ups, summaries or transcripts anyway. We build the same pattern ourselves:

| Mem0 capability | Our equivalent |
|---|---|
| Fact extraction | Session analyzer (Claude, structured output) at end of call |
| Reconciliation (ADD/UPDATE/DELETE/NOOP) | Same analyzer call, given the transcript + the user's most similar existing facts; applied via `MemoryStore.add` / `supersede` / `retire` |
| Vector store + search | `facts` + per-model `sqlite-vec` table (partitioned by user) + FTS5, fused with reciprocal rank fusion |
| History | Facts are never hard-deleted: `superseded_by` + `retired_at` |

The `MemoryStore` port stays provider-agnostic, so Mem0 Platform or Zep could be plugged in later.

### `MemoryStore` (implemented in Phase 2)

```python
class MemoryStore(Protocol):
    async def add(self, fact: Fact) -> None: ...
    async def get(self, fact_id: str) -> Fact | None: ...
    async def supersede(self, fact_id: str, replacement: Fact) -> None: ...
    async def retire(self, fact_id: str, at: datetime) -> None: ...
    async def active(self, user_id: str, limit: int) -> list[Fact]: ...
    async def search(self, user_id: str, query: str, limit: int) -> list[ScoredFact]: ...
```

- `Fact(id, user_id, text, category, importance 1–5, source_session_id, superseded_by, retired_at)`; facts are stored normalized in English for consistent retrieval across Hinglish/Hindi/English.
- **Hybrid search**: vector KNN (cosine, `min_similarity` threshold, default 0.3) + FTS5 keyword match, each taking `4 × limit` candidates, merged with reciprocal rank fusion (k=60); ties broken by importance then recency.
- **Search terms** (`baatein/text.py`): Unicode-aware tokenizer that keeps Devanagari vowel signs, strips possessives, and drops English/Hinglish/Hindi stopwords. Used by both fact and transcript search.
- **Per-model vector tables**: `facts_vec_<model_slug>_<dims>`, created on first use, with a delete trigger so user deletion cascades to vectors. Only active facts have vectors. Switching embedder = new table + `uv run baatein reembed`.
- `sqlite-vec` is loaded on every connection (runtime and migrations).

### Embeddings

```python
class Embedder(Protocol):
    model_id: str
    dimensions: int

    async def embed(
        self, texts: Sequence[str], kind: Literal["passage", "query"]
    ) -> list[list[float]]: ...
```

- `FastEmbedEmbedder` loads the model lazily (first call, in a worker thread), routes `kind` to FastEmbed's `query_embed`/`passage_embed`, and validates dimensions.
- `EmbedderRegistry` selects the adapter from `BAATEIN_EMBEDDING__PROVIDER` / `__MODEL` / `__DIMENSIONS` / `__CACHE_DIR`.
- Alternatives evaluated: Ollama `bge-m3` (shared local server, better quality), Gemini / Voyage / OpenAI (hosted), `multilingual-e5-large` (too heavy per process).

## 4. Architecture

```
 Browser (login + LiveKit client)
   │  1. POST /auth/login  → JWT
   │  2. POST /livekit/token (JWT) → LiveKit token, identity = user_id
   ▼
 FastAPI (api/) ──── SQLite (users, sessions, turns, followups, facts, vectors)
   │
 LiveKit server (self-hosted) ── dispatches job ──▶ Agent worker (agent/)
                                                    │  STT Sarvam → Claude → TTS
                                                    │  MemoryService
                                                    │   ├─ MemoryStore (facts, hybrid search)
                                                    │   └─ repos (sessions, turns, follow-ups)
                                                    ▼
                                             Finalizer (Claude: summary, follow-ups, fact ops)
```

### Data ownership (all SQLite)

| Data | Why |
|---|---|
| Facts + vectors | Long-term semantic memory, hybrid search |
| Users, credentials, language preference | Relational, auth |
| Sessions + full transcripts (turns) | Source of truth, audit, replay into the finalizer on failure |
| Follow-ups (text, due_at, status) | Status/due-date queries for the proactive opener |
| Session summaries | Cheap "last time we talked about…" context |

### Conversation lifecycle

```
CALL START  identity → user_id → child injector (CurrentUser, SessionId)
            load in parallel: profile, last N summaries, open follow-ups,
                              MemoryStore.active(top importance)
            context_builder → instructions + initial ChatContext (token budget)
            generate_reply("greet by name; pick the most relevant follow-up")

EACH TURN   on_user_turn_completed:
              MemoryStore.search(new_message, user_id, top_k=5) with ~300ms timeout
              → inject as hidden context (skip silently on timeout)
            conversation_item_added → append turn to SQLite
            tools: recall_memory(query), add_followup(text, due), resolve_followup(id)

CALL END    shutdown callback → close session, status=pending
            Finalizer (one Claude call, structured output):
              input: transcript, open follow-ups, similar existing facts
              output: summary, follow-ups (new/resolved), fact ops (add/supersede/retire)
            status=done | failed (retry worker re-runs from stored transcript)
```

## 5. Dependency injection (`injector`)

- **Composition root only** (`container.py`): domain/memory code is plain classes with constructor args, no `@inject`.
- **Root injector per worker process**, built in LiveKit `prewarm` (after migrations):
  - `CoreModule` → `Settings`, `Clock`
  - `StorageModule` → `Database`, repos
  - `MemoryModule` → `EmbedderRegistry`, `Embedder`, `SqliteMemoryStore` / `MemoryStore`
  - `AuthModule` → `PasswordHasher` (Argon2), `AccessTokens` (JWT, needs `BAATEIN_AUTH__JWT_SECRET`), `CallTokenIssuer` (LiveKit, needs `LIVEKIT_API_KEY/SECRET`), `AuthService`
  - `VoiceModule` → `LanguageRegistry`, STT/TTS/LLM registries, `VoicePipelineFactory`
- **Identity → agent**: `/livekit/token` issues a token for a fresh room `baatein-<user>-<ulid>` with explicit agent dispatch (`agent_name="baatein"`) whose metadata is `{"user_id": ...}`. The metadata is signed by our API, so the agent trusts it, loads the user, and picks their language and name. Without metadata (e.g. `console`) it falls back to the default language.
- **Child injector per call** (Phase 4): `root.create_child_injector([SessionModule(user, session_id)])`.
- **Tests**: `build_root(OverrideModule(), settings=...)` rebinds any port to a fake.

## 6. Project layout

```
src/baatein/
  config.py                 pydantic-settings (BAATEIN_<SECTION>__<FIELD>)
  container.py              injector modules, build_root()
  registry.py               ProviderSpec, ProviderRegistry
  text.py                   search tokenizer + stopwords
  cli.py                    baatein migrate | reembed
  domain/                   models.py, errors.py, ids.py
  ports/                    clock.py, repos.py, embedder.py, memory.py
  storage/sqlite/
    db.py, vec.py, migrate.py, migrations/0001_init.sql, 0002_facts.sql
    repos.py, memory_store.py
  memory/
    ranking.py              reciprocal rank fusion
    embeddings/             registry.py, fastembed.py
    service.py              (Phase 4) MemoryService
    context_builder.py      (Phase 4)
    analyzer.py             (Phase 5) session analyzer
  voice/                    languages.py, providers.py
  agent/                    assistant.py, entrypoint.py
  auth/                     passwords.py, tokens.py, livekit.py, service.py
  api/                      app.py (FastAPI), static/index.html (web client)
  workers/finalizer.py      (Phase 5)
tests/
  fakes/                    in-memory repos, InMemoryMemoryStore, FakeEmbedder (bag-of-words)
  factories.py
  unit/                     pure logic, adapters with stubs, container, CLI, entrypoint
  contract/                 one suite per port, run against SQLite and fakes
  integration/              SQLite specifics, migrations, real model (-m model)
  agent/                    LiveKit text-mode behaviour tests (-m llm)
```

## 7. SQLite schema

`0001_init.sql`:

```sql
users(id TEXT PK, email TEXT UNIQUE COLLATE NOCASE, password_hash, display_name,
      language DEFAULT 'hinglish', created_at)
sessions(id TEXT PK, user_id FK ON DELETE CASCADE, room_name, started_at, ended_at,
         end_reason, summary, finalize_status CHECK IN ('active','pending','done','failed'),
         finalize_attempts, memory_synced_seq)
turns(id INTEGER PK, session_id FK ON DELETE CASCADE, seq, role CHECK IN ('user','assistant'),
      text, language, interrupted, created_at, UNIQUE(session_id, seq))
turns_fts USING fts5(text, content='turns', tokenize='unicode61 remove_diacritics 0') + triggers
followups(id TEXT PK, user_id FK ON DELETE CASCADE, text, due_at,
          status CHECK IN ('open','done','dropped'), priority,
          source_session_id FK ON DELETE SET NULL, created_at, resolved_at)
```

`0002_facts.sql`:

```sql
facts(pk INTEGER PK, id TEXT UNIQUE, user_id FK ON DELETE CASCADE, text,
      category CHECK IN (personal, relationship, work, health, preference, event, other),
      importance CHECK 1..5, source_session_id FK ON DELETE SET NULL,
      superseded_by, created_at, retired_at)
facts_fts USING fts5(text, content='facts', content_rowid='pk') + triggers
-- runtime, per embedder:
facts_vec_<model>_<dims> USING vec0(user_id TEXT PARTITION KEY, embedding FLOAT[dims] cosine)
```

- `facts.pk` is an `INTEGER PRIMARY KEY` so the FTS and vector rowids stay stable across `VACUUM`.
- IDs are ULID text. Timestamps are UTC ISO-8601 with microseconds; naive datetimes are rejected.
- `remove_diacritics 0` keeps Devanagari vowel signs intact so Hindi search works.
- Migrations: `NNNN_name.sql`, contiguous, each in its own `BEGIN IMMEDIATE` transaction (statements split with `sqlite3.complete_statement`, because `executescript` commits implicitly), safe for concurrent worker processes.
- Runtime: one `aiosqlite` connection per process behind an `asyncio.Lock`; writes use `BEGIN IMMEDIATE`, reads skip the write lock.

## 8. Languages

`LanguageProfile(code, stt: ProviderSpec, tts: ProviderSpec, reply_style)`; `hinglish` = Sarvam STT `codemix` + Sarvam Bulbul v3 + Roman-script Hinglish style; `english` = Sarvam STT + Cartesia. New language = new registry entry; `users.language` selects it per call.

## 9. Testing (coverage-first)

Tests don't have to be written before the code, but every change ships with tests and coverage stays as close to 100% as practical. Bug fixes come with a regression test.

| Layer | Speed / deps | What it proves |
|---|---|---|
| Unit | ms, no I/O | registries, text/ranking, adapters with stubs, container wiring, CLI, entrypoint |
| Contract | fast | every port's real adapter and fake behave the same: repos, `MemoryStore` |
| Integration | fast (SQLite) | migrations, transactions, cascades, Devanagari search, vector index lifecycle |
| Model (`-m model`) | downloads 0.22 GB once to `data/models` | real FastEmbed: Hinglish query closer to matching English fact |
| Agent (`-m llm`) | real Claude | conversational behaviour via `session.run` + `.judge` |
| Evals | real Claude, manual | Hinglish transcripts → expected facts/follow-ups |

- Coverage gate: `fail_under = 95` with branch coverage; currently 100%.
- Default `pytest` makes no network calls.
- Pre-commit and CI: ruff format, ruff check, mypy strict, pytest with coverage.

## 10. Phases

| # | Key tests | Deliverable |
|---|---|---|
| 0 ✅ | settings, registries, container, entrypoint wiring, real plugin construction | Scaffold, settings, language + provider registries, DI container, Hinglish agent in `console` |
| 1 ✅ | repo contract suite, migrations, transactions, cascades, Devanagari search | SQLite layer, repos, `StorageModule`, `baatein migrate` |
| 2 ✅ | `MemoryStore` contract (SQLite + fake), vector index lifecycle, embedder adapter, real model | `Embedder` port + FastEmbed, `facts` + `sqlite-vec` + FTS5 hybrid search, `MemoryModule`, `baatein reembed` |
| 3 ✅ | auth service, Argon2, JWT expiry via clock, LiveKit token claims, API routes, identity → agent | `AuthService`, `/auth/signup`, `/auth/login`, `/me`, `/livekit/token`, `AuthModule`, `baatein serve`, web client at `/` with live transcripts; agent resolves the user from dispatch metadata. Verified live end to end |
| 4 | MemoryService unit tests, lifecycle tests | Session lifecycle, transcript persistence, call-start context, per-turn retrieval, memory tools |
| 5 | finalizer state-machine tests, analyzer evals, agent opener test | Session analyzer (summary + follow-ups + fact ops), retry worker, proactive opener |
| 6 | evals, deletion tests | "Forget me", logging/metrics, demo polish |

## 11. Risks

- **Latency**: per-turn retrieval under a hard timeout (~300ms); embedder prewarmed per process; Haiku for conversation.
- **Embedding quality on Romanized Hinglish**: MiniLM is the weakest option; mitigations are English-normalized facts, keyword fusion, and an easy switch to `bge-m3`, Gemini or Pinecone. Fallback: normalize the query to English before search.
- **Memory per process**: each LiveKit job process loads the embedder (~0.22 GB); keep idle process count low.
- **Reconciliation quality**: the analyzer prompt decides add/supersede/retire; covered by evals on fixed transcripts.

## 12. Open items (discuss later)

- **Pinecone `llama-text-embed-v2`** — confirm `pc.inference.embed` parameters (`input_type`, `dimension`, `truncate`) and latency from India, then add a `PineconeEmbedder` adapter. With the `Embedder` port and per-model vector tables, switching is config + `baatein reembed`.

# Baatein AI — Voice Agent with Long-Term Memory

Hackathon plan. Python, LiveKit Agents (self-hosted), Claude, Sarvam/Cartesia, Mem0, Pinecone embeddings, SQLite, `injector`. No Docker. Coverage-first testing.

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
| LLM (memory/analysis) | Claude `claude-haiku-5-5` | Mem0 extraction (`anthropic` provider) + our session analyzer |
| STT | Sarvam `STTRealtime` / `STT(mode="codemix")` | Code-mixed Hinglish |
| TTS | Sarvam Bulbul or Cartesia Sonic | Chosen per language profile |
| Long-term memory | Mem0 OSS `AsyncMemory` (pinned `mem0ai==2.2.x`) | Fact extraction, dedup/update, semantic search |
| Embeddings | **Pinecone Inference `llama-text-embed-v2`** behind our `Embedder` port | Multilingual (incl. Hindi); asymmetric `passage`/`query` input types; dims configurable. **Integration details deferred — see §12** |
| Vector store | Via Mem0 config, **choice open — see §12** | No Docker. Qdrant embedded mode locks its dir to one process and LiveKit runs one process per call, so: Qdrant native binary, or Pinecone index, or `JobExecutorType.THREAD` |
| App DB | SQLite via `aiosqlite`, raw SQL, repository pattern | WAL, FK on, busy_timeout; migrations via `PRAGMA user_version` |
| Auth | Email + password, Argon2 (`argon2-cffi`), JWT | FastAPI service issues LiveKit tokens |
| DI | `python-injector` | Root injector per process, child injector per call |
| Tooling | `uv`, Python 3.12, ruff, mypy (strict on `src/`), pytest, pytest-asyncio, pytest-cov | |

**Postgres migration path:** app DB → Postgres adapter for the same repository ports (contract tests prove parity); Mem0 vector store → `pgvector` (config change). Both in one Postgres instance, Qdrant removed.

## 3. Extensibility: ports + provider registries

Every external capability is a **port** (a `Protocol`) with one or more **adapters**, chosen by config through a **registry**. Adding a provider = write adapter + register it + make it pass the port's contract tests. Nothing else changes.

| Port | Adapters (now → later) | Selected by |
|---|---|---|
| `Embedder` | `PineconeEmbedder` (llama-text-embed-v2) → OpenAI, FastEmbed, Bedrock… | `EMBEDDING__PROVIDER`, `EMBEDDING__MODEL`, `EMBEDDING__DIMENSIONS` |
| `MemoryStore` | `Mem0MemoryStore` → Mem0 Platform, Letta, own impl | `MEMORY__PROVIDER` |
| Vector store | Mem0 config: `qdrant` → `pgvector` / `pinecone` | `MEMORY__VECTOR_STORE__*` |
| `UserRepo`, `SessionRepo`, `TurnRepo`, `FollowUpRepo` | SQLite → Postgres | `DATABASE__URL` scheme |
| STT / TTS / LLM factories | Sarvam, Cartesia, Anthropic → any LiveKit plugin | `LanguageProfile` + `VOICE__*` |
| `SessionAnalyzer` | Claude structured output | `ANALYZER__MODEL` |
| `PasswordHasher` | Argon2 | — |

```python
# ports/embedder.py
EmbedKind = Literal["passage", "query"]


class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...
    @property
    def model_id(self) -> str: ...
    def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]: ...
```

```python
# memory/embeddings/registry.py
EMBEDDERS: dict[str, Callable[[EmbeddingSettings], Embedder]] = {
    "pinecone": PineconeEmbedder.from_settings,
}
```

### Mem0 ↔ Embedder bridge

Mem0's config validator only accepts its built-in embedder names, and its `langchain` bridge always uses `embed_query` (loses the passage/query distinction llama-text-embed-v2 needs). So:

- `Mem0EmbedderBridge(EmbeddingBase)` wraps our `Embedder`, mapping Mem0's `memory_action` → `kind`: `add`/`update` → `passage`, `search` → `query`; implements native `embed_batch`.
- `build_mem0()` constructs `AsyncMemory` and installs the bridge as `memory.embedding_model`. This touches one Mem0 internal attribute, so it is isolated in one function, Mem0 is pinned, and a **guard test** asserts Mem0 actually routes `add`/`search` through our bridge — an upgrade that breaks this fails CI instead of production.
- Vector collection name encodes the embedding model + dims (e.g. `memories__llama-text-embed-v2__1024`), so switching embedder never mixes incompatible vectors; a `reembed` command rebuilds a collection from Mem0's stored memories.
- Mem0 calls embedders via `asyncio.to_thread`, so the sync Pinecone SDK is fine.

## 4. Architecture

```
 Browser (login + LiveKit client)
   │  1. POST /auth/login  → JWT
   │  2. POST /livekit/token (JWT) → LiveKit token, identity = user_id
   ▼
 FastAPI (api/) ──── SQLite (users, sessions, turns, followups)
   │
 LiveKit server (self-hosted) ── dispatches job ──▶ Agent worker (agent/)
                                                    │  STT Sarvam → Claude → TTS
                                                    │  MemoryService
                                                    │   ├─ MemoryStore (Mem0 → Qdrant,
                                                    │   │   Pinecone embeddings, Claude)
                                                    │   └─ SQLite repos
                                                    ▼
                                             Finalizer (summary + follow-ups)
```

### Memory responsibilities

| Data | Owner | Why |
|---|---|---|
| Semantic facts ("sister Priya's wedding is in Dec") | **Mem0** | Extraction, ADD/UPDATE/DELETE reconciliation, vector search, history |
| Users, credentials, language preference | SQLite | Relational, auth |
| Sessions + full transcripts (turns) | SQLite | Source of truth, audit, replay into Mem0 on failure |
| Follow-ups (text, due_at, status) | SQLite | Needs status/due-date queries Mem0 doesn't model |
| Session summaries | SQLite | Cheap "last time we talked about…" context |

Facts are stored **normalized in English** (custom Mem0 extraction prompt) for consistent retrieval across Hinglish/Hindi/English; the agent replies in the user's language profile.

### Conversation lifecycle

```
CALL START  identity → user_id → child injector (CurrentUser, SessionId)
            load in parallel: profile, last N summaries, open follow-ups,
                              MemoryStore.search(top_k=10, "important facts about user")
            context_builder → instructions + initial ChatContext (token budget)
            generate_reply("greet by name; pick the most relevant follow-up")

EACH TURN   on_user_turn_completed:
              MemoryStore.search(new_message, user_id, top_k=5) with ~300ms timeout
              → inject as hidden context (skip silently on timeout)
            conversation_item_added → append turn to SQLite (async write queue)
            every N user turns → background MemoryStore.add(new turns)
            tools: recall_memory(query), add_followup(text, due), resolve_followup(id)

CALL END    shutdown callback → close session, status=pending
            Finalizer (Claude, structured output):
              - flush remaining turns to MemoryStore
              - summary
              - new / resolved follow-ups
            status=done | failed (retry worker re-runs from stored transcript)
```

## 5. Dependency injection (`injector`)

- **Composition root only** (`container.py`): domain/memory code is plain classes with constructor args, no `@inject`, so tests build them directly.
- **Root injector per worker process**, built in LiveKit `prewarm`:
  - `ConfigModule` → `Settings`
  - `DatabaseModule` → `Database`, repos
  - `EmbeddingModule` → `Embedder` from registry by `settings.embedding.provider`
  - `MemoryModule` → `AsyncMemory` (with bridge), `MemoryStore`, `SessionAnalyzer`, `MemoryService`
  - `VoiceModule` → `LanguageRegistry`, STT/TTS/LLM factories
- **Child injector per call**: `root.create_child_injector([SessionModule(user, session_id)])` → `CurrentUser`, `SessionContext`, `Assistant`.
- **Tests**: `Injector([*prod_modules, TestOverrides()])` rebinding ports to fakes. A container test asserts every port resolves with default settings.
- `injector` is sync: async resources (DB connect, migrations) are initialized at startup and bound as instances.

## 6. Project layout

```
src/baatein/
  config.py                     pydantic-settings, nested (EMBEDDING__*, MEMORY__*, …)
  container.py                  injector modules, build_root(), build_session()
  domain/models.py              User, Session, Turn, FollowUp, Memory, LanguageProfile
  ports/                        embedder.py, memory.py, repos.py, analyzer.py, auth.py, clock.py
  storage/sqlite/
    db.py, migrate.py, migrations/0001_init.sql, repos.py
  memory/
    embeddings/
      registry.py
      pinecone.py               PineconeEmbedder (llama-text-embed-v2)
    mem0/
      bridge.py                 Mem0EmbedderBridge(EmbeddingBase)
      factory.py                build_mem0(settings, embedder) -> AsyncMemory
      store.py                  Mem0MemoryStore(MemoryStore)
      prompts.py                Hinglish → English fact extraction prompt
    service.py                  MemoryService
    context_builder.py          pure: memory → instructions/ChatContext within budget
    analyzer.py                 Claude structured output: summary + follow-ups
  voice/
    languages.py                LanguageProfile registry
    providers.py                STT/TTS/LLM registries + factories
  agent/
    assistant.py, tools.py, entrypoint.py
  api/
    app.py, auth.py, livekit_tokens.py
  workers/finalizer.py
  cli.py                        migrate, reembed, finalize-pending
tests/
  fakes/                        FakeEmbedder (deterministic hash vectors), InMemoryMemoryStore,
                                in-memory repos, FakeAnalyzer, FrozenClock
  unit/                         pure logic + services against fakes
  contract/                     one suite per port, parametrized over fake + real adapters
  integration/                  Mem0 bridge guard, SQLite migrations, Qdrant, Pinecone (marked)
  agent/                        LiveKit text-mode: session.run + expect + judge, mock_tools
  evals/                        seeded memory scenarios; extraction quality on fixed transcripts
```

## 7. SQLite schema (0001_init)

```sql
users(id TEXT PK, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      display_name TEXT, language_profile TEXT NOT NULL DEFAULT 'hinglish',
      created_at TEXT NOT NULL)
sessions(id TEXT PK, user_id FK → users ON DELETE CASCADE, room_name TEXT,
         started_at, ended_at, end_reason, summary TEXT,
         mem0_synced_seq INTEGER NOT NULL DEFAULT 0,
         finalize_status TEXT CHECK IN ('active','pending','done','failed'),
         finalize_attempts INTEGER DEFAULT 0)
turns(id INTEGER PK, session_id FK ON DELETE CASCADE, seq INTEGER, role TEXT,
      text TEXT, language TEXT, interrupted INTEGER, created_at,
      UNIQUE(session_id, seq))
followups(id TEXT PK, user_id FK ON DELETE CASCADE, text TEXT, due_at TEXT NULL,
          status TEXT CHECK IN ('open','done','dropped'), priority INTEGER,
          source_session_id FK, created_at, resolved_at)
turns_fts USING fts5(text, content='turns', content_rowid='id')  + sync triggers
indexes: sessions(user_id, started_at), followups(user_id, status, due_at)
```

IDs are ULID text (portable to Postgres). Timestamps ISO-8601 UTC.

## 8. Languages

`LanguageProfile(code, stt: {provider, language, mode}, tts: {provider, voice, language}, prompt_style)`; `hinglish` = Sarvam STT `codemix` + Sarvam/Cartesia Hindi voice + "reply in natural Roman-script Hinglish". New language = new registry entry; `users.language_profile` selects it per call.

## 9. Testing (coverage-first)

Tests don't have to be written before the code, but every change ships with tests and coverage stays as close to 100% as practical. Bug fixes come with a regression test.

**Test layers**

| Layer | Speed / deps | What it proves |
|---|---|---|
| Unit | ms, no I/O | context builder budgeting, language/provider registries, auth hashing/JWT, MemoryService orchestration, finalizer state machine (with fakes + FrozenClock) |
| Contract | fast for fakes; real adapters marked | every adapter (and its fake) honours the port: repos (CRUD, cascades, ordering), `Embedder` (dims, batch order, kind), `MemoryStore` (add → search isolation by user) |
| Integration | needs local services/keys, marked | migrations up from empty, Mem0 bridge guard, vector store, embedder round-trip |
| Agent | real Claude, marked `llm` | opener raises the due follow-up (`.judge`), `recall_memory` called when user references the past, tool failures handled (`mock_tools`), memory timeout doesn't block reply |
| Evals | real Claude, manual | Hinglish transcripts → expected facts/follow-ups; tracks prompt regressions |

**Rules**
- Coverage gate: `fail_under = 95` with branch coverage (`pyproject.toml`); currently 100%.
- Fakes live in `tests/fakes/` and run through the same contract suites as real adapters, so they can't drift.
- Default `pytest` makes no network calls; `-m llm` / `-m integration` opt in.
- Real plugin construction is tested with dummy keys, so a wrong option name (e.g. an invalid Sarvam speaker) fails the fast suite.
- Pre-commit and CI: ruff format, ruff check, mypy strict, pytest with coverage.

## 10. Phases

| # | Key tests | Deliverable |
|---|---|---|
| 0 ✅ | settings, registries, container, entrypoint wiring, real plugin construction | Scaffold (uv, ruff, mypy, pytest, pre-commit, CI), settings, language + provider registries, DI container, Hinglish agent runnable in `console`. Pending: live check with real keys (Sarvam STT/TTS, Claude 5.5 ID) |
| 1 | repo contract suite, migration tests | SQLite layer, migrations, repos, DI container |
| 2 | `Embedder` contract, bridge guard, `MemoryStore` contract | Embedder registry + Pinecone adapter, Mem0 bridge/factory/store |
| 3 | auth unit + API tests | Signup/login, token endpoint, identity → agent; minimal web client |
| 4 | MemoryService unit tests, lifecycle tests | Session lifecycle, transcript persistence, incremental ingest, per-turn retrieval, memory tools |
| 5 | finalizer state-machine tests, agent opener test | Summary + follow-ups, retry worker, proactive opener |
| 6 | evals, deletion tests | "Forget me" (MemoryStore delete_all + SQL cascade), logging/metrics, demo polish |

## 11. Risks

- **Latency**: a remote embedder (e.g. Pinecone) is a network hop on every retrieval — hard timeout (~300ms), skip injection on miss; Haiku for conversation.
- **Mem0 internals**: bridge relies on `AsyncMemory.embedding_model`; pinned version + guard test.
- **Embedding switch**: incompatible vectors → per-model collection names + `reembed` command.
- **Mem0 extraction cost**: batch adds every N turns, not every turn.
- **Hinglish retrieval quality**: validate in spike with real transcripts; fallback = normalize query to English before search.

## 12. Open items (discuss later)

- **Pinecone `llama-text-embed-v2` integration** — confirm `pc.inference.embed` parameters (`input_type`, `dimension`, `truncate`), latency from India, and whether to also use Pinecone as the vector store (removes the need for a local vector DB). The `Embedder` port + Mem0 bridge design in §3 stands either way.
- **Vector store without Docker** — Qdrant native binary vs Pinecone index vs thread-based job executor with embedded Qdrant.

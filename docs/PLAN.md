# Baatein AI — Voice Agent with Long-Term Memory

Hackathon plan. Python, LiveKit Agents (self-hosted), Claude, Sarvam, SQLite + `sqlite-vec`, Pinecone embeddings, `injector`. No Docker. Coverage-first testing.

## Status (2026-10-30)

**All six phases are built** on the `memory-layer` branch. What each one added:
- **Phases 0–4:** web client, auth, LiveKit calls, turns saved live, the call-start context, and per-turn memory search.
- **Phase 5:** the consent-based memory layer, in six slices (§13, `docs/PHASE5_SLICES.md`, design in `docs/MEMORY_DESIGN.md`):
  - consent-only saving, with correct and forget;
  - storylines, boundaries and the one check-in per call;
  - persona and preferences;
  - reminders delivered in conversation;
  - an end-of-call summary with retry;
  - real-Claude scenario evals.
- **Phase 6:**
  - "Forget me" (erase memories, keep the account);
  - per-call metrics with `baatein metrics`;
  - a redesigned web client without the transcript;
  - a demo clock and a voice override for demos;
  - the multi-day demo recorder (`demo/days/`, untracked).

**Retrieval:** Pinecone `llama-text-embed-v2` with per-language cut-offs. Replies don't wait for the search, and a slow `recall` says "Hmm…". Filler-only turns aren't searched. Keyword matches need a little shared meaning, and near-duplicate saves are caught. Every decision, with the options and measurements, is in `docs/DECISIONS.md`.

**Still to do:**
- live voice runs of the multi-call scenarios (#1, #34);
- recording the demo video;
- the deferred items in `docs/DECISIONS.md` ("Later");

**Phase 4 notes**
- Per-call state is a `CallMemory` object created by the process-wide `MemoryService` (`memory/service.py`), not a child injector: it only needs the session and the repos, and a plain object is easier to test.
- Turns are numbered and queued synchronously from `conversation_item_added`, written in order in the background, and flushed before the session ends.
- Per-turn recall waits at most `BAATEIN_MEMORY__RECALL_TIMEOUT_SECONDS` (0 by default since 2026-10-30; 0.1 s for a local embedder) for `MemoryStore.search`; a slower search keeps running and its memories join the next turn, and an error is logged and skipped (see §3 Embeddings, Latency). The embedder is warmed up at call start, in parallel with the greeting.
- One shutdown callback ends the session and then closes the database; LiveKit runs shutdown callbacks in parallel, and an open `aiosqlite` thread kept job processes from exiting.

**How to run** (see README): `cp .env.example .env` and fill keys, then `livekit-server --dev`, `uv run baatein-agent dev`, `uv run baatein serve`, open http://localhost:8000.

**Gotchas found so far**
- Anthropic rejects a tool parameter that has an enum and a nullable type (`Enum value 'fact' does not match declared type ['string', 'null']`). The LiveKit plugin makes every defaulted parameter nullable in strict mode, so optional parameters with fixed choices are plain `str | None` and mapped with `agent/tools.py:choice`. `tests/unit/test_tools.py` checks the generated schemas.
- A Sarvam STT outage (websocket connects timing out, 2026-10-30) closed the LiveKit session about 20 s in, silently, with the default retry limits. `agent/resilience.py` retries longer (`BAATEIN_VOICE__STT_MAX_ERRORS`, default 6 failures of ~10 s each) and, past that, says the profile's `cannot_hear` line over TTS before ending the call.
- TTS for Hinglish is Cartesia `sonic-3.6`, voice Siya (D31); its pace option is `speed` (0.6–1.5; the elder persona uses 0.85). English and Kannada use Sarvam `bulbul:v4-flash` (D29), pace option `pace` (0.5–2.0).
- Anthropic's strict tool schemas allow at most 16 nullable (optional) parameters across all tools (`Schemas contains too many parameters with union types`). Baatein sends non-strict schemas; see `voice/providers.py:anthropic_llm`.
- `Agent.on_user_turn_completed` is not called in LiveKit's text-mode tests (`session.run(user_input=...)`). Logic that must count user turns reads `RunContext.session.history` in the tool instead.
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
| LLM (memory/analysis) | Claude `claude-haiku-5-5` | One structured call per finished session: summary, whether it ended mid-story (and the topic), up to 2 "maybe ask next time" notes. It never creates memories |
| STT | Sarvam `STT(mode="codemix")` | Code-mixed Hinglish |
| TTS | Cartesia `sonic-3.6` for Hinglish; Sarvam `bulbul:v4-flash` (Odia `bulbul:v3`) for the rest | Chosen per language profile |
| Long-term memory | **Own `MemoryStore`** on SQLite: `facts` table + `sqlite-vec` + FTS5 | Replaces Mem0 (see §3) |
| Embeddings | Pinecone hosted `llama-text-embed-v2` (2048-d) | Behind the `Embedder` port; FastEmbed local models still work (see §3 Embeddings, D11) |
| App DB | SQLite via `aiosqlite`, raw SQL, repository pattern | WAL, FK on, busy_timeout; migrations via `PRAGMA user_version` |
| Auth | Email + password, Argon2 (`argon2-cffi`), JWT | FastAPI service issues LiveKit tokens |
| DI | `python-injector` | Root injector per process; per-call state is a plain `CallMemory` |
| Tooling | `uv`, Python 3.12, ruff, mypy (strict on `src/`), pytest, pytest-asyncio, pytest-cov | |

**Postgres migration path:** repositories and `MemoryStore` get Postgres adapters that pass the same contract suites; `sqlite-vec` → `pgvector`, FTS5 → `tsvector`.

## 3. Memory design (why not Mem0)

Mem0 would give us fact extraction, ADD/UPDATE/DELETE reconciliation and vector search, but it has no SQLite vector store (forcing extra infra without Docker), costs two extra LLM calls per add, needs an internal-attribute hack for custom embedders, and doesn't cover follow-ups, summaries or transcripts anyway. We build the same pattern ourselves:

| Mem0 capability | Our equivalent |
|---|---|
| Fact extraction | With strict consent (Phase 5), only the `remember` tool, after the user's "yes"; the end-of-call analyzer writes a summary, never memories |
| Reconciliation (ADD/UPDATE/DELETE/NOOP) | `correct_memory` supersedes, `forget` tombstones; `remember` won't save a near copy (`add_unless_similar`) |
| Vector store + search | `facts` + per-model `sqlite-vec` table (partitioned by user) + FTS5, fused with reciprocal rank fusion |
| History | Facts are never hard-deleted: `superseded_by` + `retired_at` |

The `MemoryStore` port stays provider-agnostic, so Mem0 Platform or Zep could be plugged in later.

### `MemoryStore` (implemented in Phase 2)

```python
class MemoryStore(Protocol):
    async def add(self, fact: Fact) -> None: ...
    async def add_unless_similar(self, fact: Fact, min_similarity: float) -> list[ScoredFact]: ...
    async def get(self, fact_id: str) -> Fact | None: ...
    async def supersede(self, fact_id: str, replacement: Fact) -> None: ...
    async def retire(self, fact_id: str, at: datetime) -> None: ...
    async def active(self, user_id: str, limit: int) -> list[Fact]: ...
    async def search(
        self, user_id: str, query: str, limit: int, min_similarity: float | None = None
    ) -> list[ScoredFact]: ...
    async def saved_in(self, session_id: str) -> list[Fact]: ...
    async def count_about(self, person_id: str) -> int: ...
    async def forget(self, fact_id: str, at: datetime) -> list[str]: ...
    async def forget_all(self, user_id: str, at: datetime) -> None: ...
```
(See `ports/memory.py` for the exact signatures.)

- `Fact(id, user_id, text, category, importance 1–5, kind, about_person_id, quote, certainty, sensitivity, source_session_id, superseded_by, retired_at, deleted_at)`; facts are stored normalized in English for consistent retrieval across Hinglish/Hindi/English.
- **Hybrid search**: vector KNN (cosine, above a `min_similarity` cut-off measured per model and language, `config.py:MEASURED_CUT_OFFS`) + FTS5 keyword match (kept only with a little shared meaning: `ranking.py:KEYWORD_FLOOR` 0.1), each taking `4 × limit` candidates, merged with reciprocal rank fusion (k=60); ties broken by importance then recency.
- **Search terms** (`baatein/text.py`): Unicode-aware tokenizer that keeps Devanagari vowel signs, strips possessives, and drops English/Hinglish/Hindi stopwords and fillers (`FILLERS`: a turn of only "haan", "ठीक है"… isn't searched). Used by both fact and transcript search.
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
- **Default since 2026-10-30: Pinecone `llama-text-embed-v2` at 2048 dimensions** (`PineconeEmbedder`, official `pinecone` SDK, `PINECONE_API_KEY`). Chosen from `docs/RETRIEVAL_BENCHMARK.md` for English, Hindi and Hinglish (the languages in focus); Odia and Kannada are hidden from signup until their retrieval is better. Through the real hybrid search on Ram's account, the right memory came back for 30/31 English, 29/31 Hindi (Devanagari, as Sarvam returns it) and 26/31 typed-Hinglish questions.
- **Latency:** a hosted search takes about 450 ms from India (p50 453 ms, p90 665 ms over 15 searches). Starting the search on each final transcript gained only about 25 ms in live calls (the turn ends right after it), so every search missed the old 0.3 s wait and the reply went out with nothing; that early start was removed, and the search now starts when the turn ends (`CallMemory.start_recall`). Since 2026-10-30:
  - The reply doesn't wait for it (`recall_timeout_seconds`, 0 by default: Pinecone would miss any short wait; set 0.1 s for a local embedder, which finishes in 5–35 ms). A search still running carries on, and what it finds is added to the call history for the next turn (`Assistant._arrived_late`), once per memory.
  - A memory needed right away comes from Claude's `recall` tool. If that search takes longer than `thinking_after_seconds` (0.3 s), Baatein says the language profile's `thinking` line ("Hmm…", about 0.7 s of audio) meanwhile. It is kept out of the chat history, transcript and summary.
  - Logs: "the search took N ms", "the reply waited N ms", "still searching; replying without it".
  - Why this and not the alternatives: `docs/DECISIONS.md` (D14).
- **Cut-offs are per model and language** (`EmbeddingSettings.cut_off`, `config.py:MEASURED_CUT_OFFS`): llama's answers score 0.14 to 0.30, MiniLM's 0.3 and up, and each language separates from small talk at its own value. A call uses its user's language's cut-off for their words and the default one for the `recall` tool's English keywords. Override with `BAATEIN_EMBEDDING__MIN_SIMILARITY`.
- The CLI loads `.env` into the environment (like the agent worker), so `reembed`, `seed`, `finalize` and `serve` see provider keys.
- Alternatives evaluated: Ollama `bge-m3` (shared local server, better quality), Gemini / Voyage / OpenAI (hosted), `multilingual-e5-large` (too heavy per process).

## 4. Architecture

```
 Browser (login + LiveKit client)
   │  1. POST /auth/login  → JWT
   │  2. POST /livekit/token (JWT) → LiveKit token, identity = user_id
   ▼
 FastAPI (api/) ──── SQLite (users, sessions, turns, facts + vectors, people, threads, reminders)
   │
 LiveKit server (self-hosted) ── dispatches job ──▶ Agent worker (agent/)
                                                    │  STT Sarvam → Claude → TTS
                                                    │  MemoryService
                                                    │   ├─ MemoryStore (facts, hybrid search)
                                                    │   └─ repos (sessions, turns, people, threads, reminders)
                                                    ▼
                     Finalizer, in the API process (Claude: summary, cut-off, notes)
```

### Data ownership (all SQLite)

| Data | Why |
|---|---|
| Facts + vectors | Long-term semantic memory, hybrid search |
| Users, credentials, language preference | Relational, auth |
| Sessions + full transcripts (turns) | Source of truth, audit, replay into the finalizer on failure |
| People, storylines (threads), boundaries, reminders | The check-in, reminders and "don't bring this up" at call start |
| Session summaries | Cheap "last time we talked about…" context |

### Conversation lifecycle

```
CALL START  identity → user_id → CallMemory (MemoryService.open_call)
            load in parallel: profile, recent calls (SessionRepo.recent_conversations,
                              which skips silent calls): the last call's summary (or the
                              one before it if the last was trivial) and other calls from
                              earlier today, people, storylines + one check-in,
                              reminders, boundaries, MemoryStore.active(top importance)
            context_builder → instructions (docs/DECISIONS.md D1–D27)
            generate_reply(context_builder.opening: a same-day call picks up from the
                           last one, others fit the time of day; reminders due now)

EACH TURN   on_user_turn_completed:
              search the turn's words, unless they're only fillers (ठीक है, haan…);
              used if already done
              → inject as hidden context; a slower search joins the next turn
            conversation_item_added → append turn to SQLite
            tools: recall(query) ("Hmm…" if slow), remember (won't save a near copy),
                   threads, reminders, … (agent/tools.py)

CALL END    session closes (hang-up) → job shuts down at once
            shutdown callback → flush turns, close session, status=pending
            Finalizer (baatein serve, every 10 s; one Claude call, structured output):
              input: transcript, items saved in this call, active boundaries
              output: summary (saved items only), ended_abruptly + topic, ≤2 notes
              never creates memories (decision 1); trivial calls get no summary, no LLM
            status=done | failed (retried with backoff from the stored transcript)
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
- **Per call**: no child injector. `MemoryService.open_call` returns a plain `CallMemory` holding the session and the repos (Phase 4 notes).
- **Tests**: `build_root(OverrideModule(), settings=...)` rebinds any port to a fake.

## 6. Project layout

```
src/baatein/
  config.py                 pydantic-settings (BAATEIN_<SECTION>__<FIELD>), measured cut-offs
  container.py              injector modules, build_root()
  registry.py               ProviderSpec, ProviderRegistry
  text.py                   search tokenizer, stopwords and fillers
  metrics.py                CallMetrics and the `baatein metrics` report
  cli.py                    baatein migrate | reembed | seed | finalize | forget-user |
                            metrics | demo-date | serve
  domain/                   models.py, errors.py, ids.py
  ports/                    analyzer.py, auth.py, clock.py (incl. DemoClock), repos.py,
                            embedder.py, memory.py
  storage/sqlite/
    db.py, vec.py, migrate.py, repos.py, memory_store.py
    migrations/             0001_init … 0008_call_metrics
  memory/
    ranking.py, ranker.py   reciprocal rank fusion, keyword floor; ranking for the context
    embeddings/             registry.py, pinecone.py (default), fastembed.py
    service.py              MemoryService, CallMemory
    context_builder.py      memory → agent instructions, greeting (opening), per-turn notes
    analyzer.py             end-of-call summary (Claude)
    finalizer.py            runs the analyzer for finished calls, with retry
    recurrence.py           reminder occurrences and when they are due
    redact.py               codes and long numbers out of transcripts
    seed.py                 test accounts from seed files
  voice/                    languages.py, personas.py, providers.py
  agent/                    assistant.py, entrypoint.py, tools.py, resilience.py
  auth/                     passwords.py, tokens.py, livekit.py, service.py
  api/                      app.py (FastAPI), static/index.html (web client)
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
followups(...)   -- dropped in 0004 for threads
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

Later migrations (Phase 5–6): `0003_memory_consent` (people; memory kind, quote, person, certainty, sensitivity and `deleted_at` tombstones on `facts`), `0004_threads_boundaries` (storylines and their events, boundaries, declined suggestions, the user's timezone, a call's cut-off topic; drops `followups`), `0005_persona_preferences`, `0006_reminders` (reminders and their occurrences), `0007_call_summaries` (notes, saving paused, retry time), `0008_call_metrics` (`sessions.metrics`).

- `facts.pk` is an `INTEGER PRIMARY KEY` so the FTS and vector rowids stay stable across `VACUUM`.
- IDs are ULID text. Timestamps are UTC ISO-8601 with microseconds; naive datetimes are rejected.
- `remove_diacritics 0` keeps Devanagari vowel signs intact so Hindi search works.
- Migrations: `NNNN_name.sql`, contiguous, each in its own `BEGIN IMMEDIATE` transaction (statements split with `sqlite3.complete_statement`, because `executescript` commits implicitly), safe for concurrent worker processes.
- Runtime: one `aiosqlite` connection per process behind an `asyncio.Lock`; writes use `BEGIN IMMEDIATE`, reads skip the write lock.

## 8. Languages

`LanguageProfile(code, stt, tts, reply_style, pace_option, cannot_hear, honorific, formal_you, thinking)`; `users.language` selects it per call. All use Sarvam `saaras:v4` STT (`codemix` for the Indian languages, which returns native script with English words in Latin).

| Code | TTS | Claude writes | Elder persona's respect |
|---|---|---|---|
| `hinglish` | Cartesia `sonic-3.6`, `hi`, Siya | Roman-script Hinglish | "Mohan ji", "aap" |
| `english` | Sarvam `bulbul:v4-flash`, `en-IN`, `sanchita_en_companion` | Indian English | "Mohan ji" |
| `odia` (2026-10-30) | Sarvam `bulbul:v3`, `od-IN`, speaker kavya (v4-flash has no Odia voice; Cartesia rejects `or`) | Odia script | "ଆପଣ"; no name suffix, since Odia's (babu, apa) depend on gender |
| `kannada` (2026-10-30) | Sarvam `bulbul:v4-flash`, `kn-IN`, `chaitra_kn_conversation` | Kannada script | "Mohan avare", "ನೀವು" |

Respect lives in the language profile, not the persona: the elder persona only says "be respectful", and each language says how. Odia and Kannada were checked end to end on 2026-10-30: synthesised speech transcribed back exactly by Sarvam STT, and Claude replies in the right script with the formal "you" (`test_replies_in_the_users_language_and_script`). The memory instructions' example phrases are Hindi; Claude is told to say them in the user's language. Memories stay in English for every language.

## 9. Testing (coverage-first)

Tests don't have to be written before the code, but every change ships with tests and coverage stays as close to 100% as practical. Bug fixes come with a regression test.

| Layer | Speed / deps | What it proves |
|---|---|---|
| Unit | ms, no I/O | registries, text/ranking, adapters with stubs, container wiring, CLI, entrypoint |
| Contract | fast | every port's real adapter and fake behave the same: repos, `MemoryStore` |
| Integration | fast (SQLite) | migrations, transactions, cascades, Devanagari search, vector index lifecycle |
| Model (`-m model`) | downloads 0.22 GB once to `data/models` | the local FastEmbed alternative: Hinglish query closer to matching English fact |
| Agent (`-m llm`, 39 tests) | real Claude | conversational behaviour via `session.run` + `.judge`; multi-call scenarios against a real database (`tests/agent/test_scenarios.py`); the end-of-call analyzer on fixed transcripts |

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
| 4 ✅ | MemoryService unit tests, lifecycle tests, entrypoint wiring, memory tool behaviour against real Claude | Session lifecycle, transcript persistence, call-start context, per-turn retrieval, memory tools. Verified live across two calls |
| 5 ✅ | slice tests per §13, scenario evals against real Claude | Consent-based memory layer (§13): memories with consent and tombstones, people, threads, boundaries, persona and preferences, conversation-only reminders, ranked call-start context, end-of-call summary with retry |
| 6 ✅ | forget-me contracts (SQLite + fakes), API and CLI, metrics, demo clock | "Forget me", per-call metrics and `baatein metrics`, web client redesign, demo clock, voice override, multi-day demo |

## 11. Risks

- **Latency**: the reply doesn't wait for per-turn retrieval (a hosted search's memories join the next turn); "Hmm…" covers a slow `recall` tool; Haiku for conversation. Moving the agent closer to Pinecone's region is a deploy question.
- **Embedding quality across languages**: measured on 2026-10-30 in `docs/RETRIEVAL_BENCHMARK.md` (6 embedding models, 2 rerankers, Hindi, Hinglish, Odia and Kannada). Pinecone `llama-text-embed-v2` was chosen (D11), with cut-offs per language; Odia and Kannada retrieval is still weak and on the list. Re-run `uv run python -m bench.retrieval.run` before changing the embedder.
- **Memory per process**: the hosted embedder loads no model; with a local FastEmbed model each job process holds it (~0.22 GB for MiniLM).
- **Summary quality**: the analyzer must keep unsaved details out of the summary; covered by `-m llm` tests on fixed transcripts.

## 12. Open items (discuss later)

- ~~Pinecone `llama-text-embed-v2`~~: done 2026-10-30 (`memory/embeddings/pinecone.py`), now the default; see §3 Embeddings.

## 13. Phase 5 plan: the consent-based memory layer

Design and the full scenario review are in `docs/MEMORY_DESIGN.md`. Decisions: strict consent; transcripts are an internal log and only summaries reach the agent (the last call's; since D20/D27 also the one before a trivial call, and others from the same day); reminders are delivered in conversation only; persona is a per-user setting; forgetting writes a tombstone.

Each slice below says **what it is for, with a scenario from the library**, then what changes and how it is tested. Scenario numbers refer to the library (#1–45). Every slice is committed separately, keeps coverage at 100%, and is checked with a live probe call before the next one starts.

### Slice 1: memory structure, consent, correct and forget ✅

**As built:** the `facts` table and `Fact` model were extended rather than renamed. There is no consent column: with strict consent a memory can only be created through `remember` after a "yes", and `source_session_id` is the call where the user agreed. `forget` tombstones the memory and every earlier version it replaced, redacts the whole transcript of each call those versions came from (the transcript is in Devanagari while memories are in English, so the exact lines can't be matched reliably), clears those calls' summaries, and deletes the person if no current memory is about them. Explicit "yaad rakhna" requests are saved right away; otherwise Baatein offers first. `forget` is two-step and enforced in code: the first call records the request and tells Claude to ask "Pakka, main yeh poori tarah mita doon?"; a second call on the user's next message (counted from the session history, so it works in voice and text mode) deletes. `rename_person` was added for name corrections (#12); `recall_memory` became `recall` over saved memories only. Turns are redacted for codes and long numbers as they are written.

**Why, in the scenarios**
- #1 Mohan: *"Kya main yaad rakhun ki aap Ramesh ko miss karte hain?" "Haan, yaad rakhna."* Only after this "haan" may anything be saved.
- #14: *"Aaj gardening par baat kar sakte hain, save mat karna."* Baatein talks about gardening but saves nothing for the rest of the call.
- #12: *"Suman meri behen?" "Nahi, padosan… naam Sunita hai."* The old memory is replaced, not kept beside the new one.
- #13: *"Family wali baat bhool jao… poori tarah."* Next call: *"Kya tumhe woh family wali baat yaad hai?"* → Baatein doesn't use it. Baatein says "deleted" only once the database write succeeded.
- #25: *"Koi phone par bank OTP maang raha tha."* Nothing like an OTP, PIN or account number is ever stored.
- #33: *"Saheli Neena ke ghar mushkil waqt."* The memory is about Neena, not the user, and stays minimal.

**Changes**
- Migration `0003`: extend `facts` into memories with `kind` (fact, story, preference, moment), `about_person_id`, `quote` (the user's own words), `certainty` (sure, uncertain), `sensitivity` (normal, personal, health, grief, crisis), `consent_session_id`, `deleted_at`. Add a `people` table (name, relation, aliases).
- Domain: `Memory` replaces `Fact`; `Person`. `MemoryStore` gains `forget` (tombstone: clear text and quote, drop vectors and FTS rows, redact related transcript turns, keep `id` and `deleted_at`) and filters deleted and non-consented rows everywhere.
- `memory/redact.py`: removes OTP-, PIN-, card- and account-like numbers before anything is written.
- Tools: `remember(kind, text, quote, about, sensitivity)`, `correct_memory(id, text)`, `forget(id)`, `pause_saving()` for the rest of the call, `recall(query)` over consented memories only. `recall_memory`'s transcript search is removed (decision 2).
- Instructions: ask before saving, never invent, say "I don't have that saved" (#15, #20).

**Tests**: tombstone wipes text, vectors, FTS and turns; deleted memories never come back from `recall`, `active` or search; `remember` is refused while saving is paused; redaction cases. Real-Claude: Claude asks before calling `remember`; after `forget` it doesn't use the detail.

### Slice 2: threads, boundaries and the call-start context ✅

**Why, in the scenarios**
- #1 Ramesh, over three calls: wants to call → *"Main phone karunga"* → *"Maine unko phone kar diya!"* → *"Abhi sirf baat hui. Dekhenge."* One thread moves through states, with a dated event at each step, so the next call can say *"pichhli baar aap Ramesh ke baare mein…"* accurately.
- #5 Kamala: *"Sunday ko lambi call karne ka socha hai."* The thread's next check-in is after Sunday, not every call.
- #8 Lata: *"Ek din na ho paaye toh?" "Toh koi baat nahi."* A check-in, never a task to enforce.
- #16: *"Main ab yoga class nahi jaana chahta."* Thread cancelled; never suggested again. #29: *"Tumne kal walk ki baat ki thi, main naraz tha."* The rejected suggestion is recorded and not repeated.
- #11: *"Aaj painting yaad nahi dilana"* → later *"Ab painting ke baare mein poochh sakte ho."* A boundary with a scope, lifted by the user, without deleting the memory.
- #19: *"Aaj meri patni ki barsi hai."* Grief and health topics are never raised by Baatein first. #32: *"Kamre mein mehmaan hain."* No personal details spoken for the rest of the call.
- #15: *"Pehli baar mil rahe hain."* A first call says so instead of faking familiarity. #31: the last call cut off mid-story → *"Baat yahin ruk gayi thi. Aap wahin se shuru karna chahte hain?"*, offered once.
- The rule after every scenario: *"never mechanically at every opening."* At most one check-in per call.

**Changes**
- Migration `0004`: `threads` (kind, title, status: open, in_progress, done, not_done, cancelled, snoozed; next_step, check_in_after, last_offered_at, times_offered, sensitivity, consent_session_id), `thread_events`, `boundaries` (topic or thread, scope: this_call, until_reopened, until_date), `declined_suggestions`; `users.timezone` (default `Asia/Kolkata`); `sessions.ended_abruptly`, `sessions.unfinished_topic`. Existing open follow-ups become threads.
- Tools: `track_thread` (after consent), `update_thread(status, event, next_step, check_in_after)`, `set_boundary`, `lift_boundary`, `note_declined_suggestion`, `privacy_mode()`.
- Context builder rewrite: local date and time, first call or the last call's date and summary and whether it ended abruptly, boundaries (always), people, **one** check-in chosen by a ranker (due, not snoozed or bounded, not sensitive, not offered in the last few days, not offered too often without an answer), followed by the rules.
- Per-turn recall covers memories and threads, filtered by consent, boundaries and sensitivity (sensitive items only when the user's words match them).

**Tests**: ranker picks the right single check-in for fixed states (#1, #5, #16, #17); boundaries always present and never violated; first-call and abrupt-end wording. Real-Claude: with Ramesh's thread in context it asks once and accepts *"nahi"*; with a painting boundary it doesn't raise painting.

### Slice 3: persona and preferences ✅

**Why, in the scenarios**
- The library is written for users aged 68–78: *"Namaste, Mohan ji!"*, Hindi-first, patient, few questions. Baatein's current prompt is casual urban Hinglish.
- #21: *"Arre, itne sawaal mat poochho."* → fewer questions from now on. #23: *"Dheere bolo, sunai nahi diya."* → slower voice and shorter sentences. #22: *"Hindi aur English mix… is preference ko yaad rakhna."* #30: *"Chai mein elaichi nahi"* → later *"ab kabhi-kabhi elaichi bhi"*, so preferences can change.
- #28: *"Tum hi kaafi ho."* → Baatein is warm but never claims to replace people.

**Changes**
- Migration `0005`: `users.persona` (elder, casual), `address_as`, `pace`, `verbosity`, `fewer_questions`.
- `voice/personas.py`: a persona registry next to `LanguageProfile` (tone, form of address, sentence length, question frequency, greeting). `elder` follows the library; `casual` is today's prompt.
- Preferences feed the instructions and the TTS (Sarvam `pace`). Tool `set_preference`.
- Web client signup: choose persona; `/me` returns it.

**Tests**: prompt and TTS options per persona and preference. Real-Claude: elder persona uses "ji" and short replies; after "itne sawaal mat poochho" it stops asking several questions per reply.

### Slice 4: reminders, delivered in conversation ✅

**Why, in the scenarios**
- #34 Asha: *"Mere bete Ravi ka birthday 18 november ko hai… har saal 11 november subah 9 baje, aur 18 november subah 9 baje."* One reminder, two alert times, every year.
- #36: *"Shayad June ke aakhri hafte mein."* No guessed date: the reminder waits for the date (`needs_info`).
- #37, #43: *"Dukaan band thi; somvaar ko phir yaad dilao." "Purani ko hi badal do, do alag-alag mat banana."* Rescheduling updates the same reminder.
- #44: *"Har mahine ke pehle ravivaar"*, then *"sirf is baar skip kar do."* Skipping one occurrence keeps the series.
- #42: *"Bill bhar diya; baaki reminder hata do."* Only this occurrence's remaining alerts stop.
- Decision 3: Baatein says it will bring the reminder up **when they next talk**, never that it will notify or call at 9 am.

**Changes**
- Migration `0006`: `reminders` (title, person, event date, local time, timezone, recurrence: once, weekly, monthly by date, monthly nth weekday, yearly; alert offsets, note, status: needs_info, active, done, cancelled, sensitivity) and `reminder_occurrences`.
- `memory/recurrence.py` for the rules; relative dates ("agle shanivaar", "parson") resolved in the user's timezone.
- Tools: `create_reminder` (after reading it back and hearing "haan"), `update_reminder`, `complete_occurrence`, `skip_occurrence`, `cancel_reminder`.
- Context: reminders whose alert time has passed since the last call or falls in the next 7 days, phrased honestly.

**Tests**: recurrence and next-occurrence calculation (first Sunday, yearly birthday, leap years); update never duplicates; complete or skip one occurrence keeps the series. Real-Claude: reads back date, time and repeat before creating; for #36 asks for the date instead of guessing.

### Slice 5: end-of-call summary and retry ✅ (built; notes in `docs/PHASE5_SLICES.md`)

**Why, in the scenarios**
- #10: *"Bas theek. Subah chai peeke balcony mein baitha tha."* Most calls produce a short summary and nothing else.
- #14, #15: things said without consent must not leak into the summary that opens the next call.
- #31: *"Kal main apne bachpan ka ek kissa bata raha tha… [call ends]"* The summary records that the call ended mid-story and what the story was about.

**Changes**
- `memory/analyzer.py`: after a call, one structured Claude call over the transcript and the call's consented items → summary (consented items only), `ended_abruptly`, `unfinished_topic`, and "maybe ask next time" notes (not memories). Redaction runs on all output.
- `memory/finalizer.py` (run by `baatein serve`): runs `pending` sessions after the call and retries `failed` ones with backoff (`finalize_attempts`); `baatein finalize` CLI to run it by hand.

**Tests**: finalizer state machine (pending → done, failed → retried, max attempts); analyzer output validation. Evals on fixed transcripts: #10 gives a summary and no notes; a #14-style transcript keeps the unsaved detail out of the summary.

### Slice 6: scenario evals ✅ (built; mapping and findings in `docs/PHASE5_SLICES.md`)

Real-Claude tests (`-m llm`) built from the library, each starting from a fixed memory state:

| Scenario | Checks |
|---|---|
| #1 | Asks permission before saving Ramesh; next call checks in once, gently |
| #11 | Doesn't raise painting under a boundary; does after the user lifts it |
| #12 | Accepts the correction without arguing and replaces the memory |
| #13 | After forget, doesn't use the detail when asked about it |
| #14 | Saves nothing after "save mat karna" |
| #15 | First call: admits it has nothing saved, doesn't invent |
| #16, #17 | Doesn't suggest a cancelled plan; no guilt for "not done" |
| #21, #23 | Fewer questions and shorter replies after the request |
| #25 | Gives safety advice and stores no number |
| #31 | Offers once to continue the cut-off story, accepts "nahi" |
| #34, #36 | Reads back the reminder; asks for a missing date; never claims to notify |

Phase 5 is done when all slices are merged, these evals pass, and a live probe sequence reproduces #1 (three calls) and #34 (create, reschedule, complete) from the database.

# Baatein AI — Voice Agent with Long-Term Memory

Hackathon plan. Python, LiveKit Agents (self-hosted), Claude, Sarvam/Cartesia, SQLite + `sqlite-vec`, FastEmbed, `injector`. No Docker. Coverage-first testing.

## Status (2026-10-29)

**Done:** Phases 0–4. You can sign up in the web client, start a call and have a live Hinglish voice conversation (Sarvam STT → Claude Haiku 5.5 → Sarvam TTS). Each call now has a `sessions` row, every message is saved as a turn while the call runs, the agent starts with memory context (recent summaries, open follow-ups, top facts), searches facts on every user turn, and has `recall_memory`, `add_followup` and `resolve_followup` tools. Verified end to end on 2026-10-29: in one call the user mentioned an interview and the agent saved a dated follow-up; in the next call it remembered the interview and resolved the follow-up.

**Phase 5 progress:** slice 1 is done (2026-10-29): memories are saved only after the user agrees, can be corrected, and can be forgotten (tombstone). Verified live: Baatein asked "Kya main yeh yaad rakhun?", saved only after "Haan, yaad rakhna", and on "bhool jao" deleted the memory, redacted the call it came from and removed the person.

Slice 2 is built (2026-10-29): follow-ups became storylines (threads) with events, the user can set and lift boundaries, decline suggestions and switch on privacy mode, and the call-start context offers at most one check-in. Details in `docs/PHASE5_SLICES.md`.

**Not yet:** nothing turns a finished call into a summary (so the last call's summary is empty), persona and preferences, reminders, and scenario evals (slices 3–6).

**Next: Phase 5, the consent-based memory layer** (full plan in §13, design in `docs/MEMORY_DESIGN.md`). It replaces the original "silent analyzer" Phase 5 after reviewing the 45 Voice Buddy scenarios. Built in six slices on the `memory-layer` branch:
1. Memory structure, consent-only saving, correct and forget ✅
2. Threads, boundaries and the call-start context ✅
3. Persona and preferences
4. Reminders (delivered in conversation only)
5. End-of-call summary and retry
6. Scenario evals

**Phase 4 notes**
- Per-call state is a `CallMemory` object created by the process-wide `MemoryService` (`memory/service.py`), not a child injector: it only needs the session and the repos, and a plain object is easier to test.
- Turns are numbered and queued synchronously from `conversation_item_added`, written in order in the background, and flushed before the session ends.
- Per-turn recall runs `MemoryStore.search` under `BAATEIN_MEMORY__RECALL_TIMEOUT_SECONDS` (0.3 s) and is skipped silently on timeout or error. The embedder is warmed up at call start, in parallel with the greeting.
- One shutdown callback ends the session and then closes the database; LiveKit runs shutdown callbacks in parallel, and an open `aiosqlite` thread kept job processes from exiting.

**How to run** (see README): `cp .env.example .env` and fill keys, then `livekit-server --dev`, `uv run baatein-agent dev`, `uv run baatein serve`, open http://localhost:8000.

**Gotchas found so far**
- Anthropic rejects a tool parameter that has an enum and a nullable type (`Enum value 'fact' does not match declared type ['string', 'null']`). The LiveKit plugin makes every defaulted parameter nullable in strict mode, so optional parameters with fixed choices are plain `str | None` and mapped with `agent/tools.py:choice`. `tests/unit/test_tools.py` checks the generated schemas.
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
    service.py              MemoryService, CallMemory
    context_builder.py      memory → agent instructions / per-turn context / tool output
    analyzer.py             (Phase 5) session analyzer
  voice/                    languages.py, providers.py
  agent/                    assistant.py, entrypoint.py, tools.py
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
| 4 ✅ | MemoryService unit tests, lifecycle tests, entrypoint wiring, memory tool behaviour against real Claude | Session lifecycle, transcript persistence, call-start context, per-turn retrieval, memory tools. Verified live across two calls |
| 5 | slice tests per §13, scenario evals against real Claude | Consent-based memory layer (§13): memories with consent and tombstones, people, threads, boundaries, persona and preferences, conversation-only reminders, ranked call-start context, end-of-call summary with retry |
| 6 | evals, deletion tests | "Forget me", logging/metrics, demo polish |

## 11. Risks

- **Latency**: per-turn retrieval under a hard timeout (~300ms); embedder prewarmed per process; Haiku for conversation.
- **Embedding quality on Romanized Hinglish**: MiniLM is the weakest option; mitigations are English-normalized facts, keyword fusion, and an easy switch to `bge-m3`, Gemini or Pinecone. Fallback: normalize the query to English before search.
- **Memory per process**: each LiveKit job process loads the embedder (~0.22 GB); keep idle process count low.
- **Reconciliation quality**: the analyzer prompt decides add/supersede/retire; covered by evals on fixed transcripts.

## 12. Open items (discuss later)

- **Pinecone `llama-text-embed-v2`** — confirm `pc.inference.embed` parameters (`input_type`, `dimension`, `truncate`) and latency from India, then add a `PineconeEmbedder` adapter. With the `Embedder` port and per-model vector tables, switching is config + `baatein reembed`.

## 13. Phase 5 plan: the consent-based memory layer

Design and the full scenario review are in `docs/MEMORY_DESIGN.md`. Decisions: strict consent; transcripts are an internal log and only the last call's summary reaches the agent; reminders are delivered in conversation only; persona is a per-user setting; forgetting writes a tombstone.

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

### Slice 2: threads, boundaries and the call-start context

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

### Slice 3: persona and preferences

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

### Slice 4: reminders, delivered in conversation

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

### Slice 5: end-of-call summary and retry

**Why, in the scenarios**
- #10: *"Bas theek. Subah chai peeke balcony mein baitha tha."* Most calls produce a short summary and nothing else.
- #14, #15: things said without consent must not leak into the summary that opens the next call.
- #31: *"Kal main apne bachpan ka ek kissa bata raha tha… [call ends]"* The summary records that the call ended mid-story and what the story was about.

**Changes**
- `memory/analyzer.py`: after a call, one structured Claude call over the transcript and the call's consented items → summary (consented items only), `ended_abruptly`, `unfinished_topic`, and "maybe ask next time" notes (not memories). Redaction runs on all output.
- `workers/finalizer.py`: runs `pending` sessions after the call and retries `failed` ones with backoff (`finalize_attempts`); `baatein finalize` CLI to run it by hand.

**Tests**: finalizer state machine (pending → done, failed → retried, max attempts); analyzer output validation. Evals on fixed transcripts: #10 gives a summary and no notes; a #14-style transcript keeps the unsaved detail out of the summary.

### Slice 6: scenario evals

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

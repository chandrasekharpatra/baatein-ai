# Decision log

The decisions taken so far, the options considered, and why. Details live in the linked documents; this page is the index of *why*. Newest decisions are at the end.

**Working rule (applies to every entry):** fix a problem at the layer that causes it (data, model, code structure) and measure the options, rather than adding prompt instructions to cover it up. Prompt text is for behaviour that really is the model's choice.

## Foundations

### D1. Our own memory store on SQLite, not Mem0
- **Options:** Mem0 (open source or platform), Zep, our own store.
- **Chosen:** our own `MemoryStore` on SQLite: a `facts` table, `sqlite-vec` and FTS5, fused with reciprocal rank fusion.
- **Why:** Mem0 has no SQLite vector store (extra infrastructure, no Docker here), costs two extra LLM calls per add, needs a hack for custom embedders, and doesn't cover storylines, reminders, summaries or transcripts. The port stays provider-neutral, so Mem0 or Zep can still be plugged in. See `PLAN.md` §3.

### D2. Claude Haiku for conversation and call summaries
- **Chosen:** `claude-haiku-5-5` for both; Sonnet via env if needed.
- **Why:** first-word latency matters most in voice. Claude 5.5 models reject `temperature`, so it is unset.

## Memory behaviour (`MEMORY_DESIGN.md` §5–7)

### D3. Strict consent
- **Options:** save silently from the transcript, or save only with agreement.
- **Chosen:** saved only with the user's agreement in the call. An explicit "yaad rakhna" is the agreement and saves at once; otherwise Baatein asks first ("Kya main yeh yaad rakhun?").
- **Why:** the scenario library (#1, #10, #34) and trust. The end-of-call analyzer never creates memories.

### D4. Transcripts are an internal log
- **Chosen:** Claude never searches raw transcripts; only the last call's summary reaches the next call.
- **Why:** transcripts include things the user never agreed to keep.

### D5. Reminders are delivered in conversation only, for now
- **Chosen:** reminders are dated objects, brought up in the next call. Baatein never promises a day or time it can't keep (`honest_promise`).
- **Why:** there is no scheduler or outbound call yet (a later phase).

### D6. Persona is a per-user setting
- **Options:** keep the casual persona, switch everyone to the elder persona, or make it per user.
- **Chosen:** per user. Respect words come from the language profile (`honorific`, `formal_you`), not the persona.
- **Why:** each language marks respect differently: "ji" in Hindi, "avare" in Kannada, and in Odia only the formal "you", because its suffixes depend on gender.

### D7. Deletion writes a tombstone; destructive actions take two steps
- **Options:** hard delete, soft delete (hide), or tombstone.
- **Chosen:** tombstone. Wipe the text, quote, vectors and FTS entries; keep a row with no content and `deleted_at`. `forget`, `forget_thread` and `cancel_reminder` act only after the user confirms on their next message.
- **Why:** the deletion is provable (#13, #14), and one misheard sentence can't erase a memory.

### D8. Call summaries name only what was saved
- **Chosen:**
  - One structured Claude call per finished call (`memory/finalizer.py`). The summary names only items saved in that call, skips sensitive ones, keeps active boundaries out, and has at most two notes.
  - "Cut off" counts only if the user spoke last.
  - Paused calls get a fixed summary with no LLM call.
  - Failed summaries are retried from the stored transcript with backoff (1, 2, 4… minutes, up to an hour, 5 attempts).
- **Why:** early versions leaked unsaved details ("son's new job in Pune") and flagged too many calls as cut off.

## Fixes made in structure, not prompts (scenario evals, slice 6)

### D9. Recalled memories stay in the call history
- **Problem:** memories added only to the per-turn context were gone by the next turn. Claude saw its own answer without the memory behind it, and took back correct facts.
- **Chosen:** each recalled memory is also written into the history, once per call (`Assistant._add_note`).

### D10. Gaps the scenarios found, fixed in code
- **The reminder wasn't in context:** `later_reminders` added to the call-start context.
- **The check-in was asked twice:** a code-side "already asked" note.
- **Reminder promises like "9 baje":** `honest_promise` in the tool reply.
- **Why:** each was a missing fact or state, not a wording problem.

## Retrieval (`RETRIEVAL_BENCHMARK.md`)

### D11. Embedding model: Pinecone `llama-text-embed-v2`, 2048 dimensions
- **Options measured:**
  - Local: MiniLM (the old default), `multilingual-e5-large`, `embeddinggemma`, mpnet, Qwen3-0.6B, potion.
  - Local rerankers: bge-v2-m3, jina-v2.
  - Hosted on Pinecone: llama at 384, 1024 and 2048 dimensions, e5-large, bge-reranker, pinecone-rerank-v0. Cohere returned 403.
- **Chosen:** llama-2048.
- **Why:** it's the strongest for English, Hindi and Hinglish, the three languages in focus. On Ram's account through the real search: English 30/31, Hindi 29/31, Hinglish 26/31 found. It also loads no model into the worker, which removes the 1.1 GB per-process warning.
- **Cost:** about 450 ms per search over the network (see D14), and a little Pinecone usage per turn.
- **Rejected:**
  - Local rerankers: near-perfect top result, but 260–330 ms.
  - e5-large: best for Odia and Kannada, but 1.7 GB per process.

### D12. Similarity cut-offs per model and language
- **Chosen:** `config.py:MEASURED_CUT_OFFS`:
  - llama: English 0.20, Hinglish users 0.14 (Sarvam writes their speech in Devanagari), default and `recall` keywords 0.12.
  - MiniLM: 0.3.
- **Why:** llama's right answers score 0.14–0.30, so one shared cut-off of 0.3 dropped most of them.

### D13. English, Hindi and Hinglish first; Odia and Kannada later
- **Chosen:** Odia and Kannada stay in the code but are hidden from signup.
  - Odia speaks through Sarvam `bulbul:v3`, because Cartesia rejects Odia on every model.
  - Kannada speaks through Cartesia `sonic-3`.
- **Why:** with llama their retrieval is weaker (Odia 15/24, Kannada 17/25 first). Revisit with a model that suits them, and have native speakers check the benchmark sentences.

## Latency and call flow (2026-10-30)

### D14. Don't hold the reply for memory search; say "Hmm…" only during a real lookup
**Problem:**
- In the first live call on Pinecone, every per-turn search timed out ("memory recall timed out after 0.30s"), so replies went out without searched memories.
- Starting the search on Sarvam's final transcript gained only about 25 ms, because the turn commits right after it.
- Pinecone took p50 453 ms and p90 665 ms (15 searches).

**Options considered:**

| Option | Verdict |
|---|---|
| Wait longer (about 0.7 s) | Adds about half a second of silence to every reply |
| A filler on every slow turn ("hmm, yaad kar rahi hoon…") | The search is slow on nearly every turn, so nearly every reply would start with a filler, even after "theek hai". "Haan, yaad aaya" before the result is back can be untrue if nothing is found. |
| A filler written for the turn by Claude | Writing it needs an LLM call, which takes about as long as the search |
| Claude says its own lead-in before calling `recall` | Depends on prompt wording and is harder to test; the plain "Hmm…" was preferred |
| A local model only for per-turn search | A second index, and the per-process memory comes back |
| Keyword (FTS) matches straight away | Facts are stored in English but Hinglish speech arrives in Devanagari, so keywords rarely match |
| Get closer to Pinecone's region | Worth doing, but it's a deploy decision |

**Chosen:**
1. **The reply waits at most 0.1 s** (`recall_timeout_seconds`). That's enough for a local embedder. A slower search keeps running, and its memories are added to the call history for the next turn, once each (`Assistant._arrived_late`). Nothing is added in privacy mode.
2. **Memories needed right away come from Claude's `recall` tool.** If that search takes over 0.3 s (`thinking_after_seconds`), Baatein says the language profile's `thinking` line ("Hmm…", about 0.7 s of audio) through `session.say(..., add_to_chat_ctx=False)`. It stays out of the history, transcript and summary, and the caller can talk over it.

**Why:**
- No added delay on ordinary turns.
- A natural pause instead of silence, and only while a lookup is really happening.
- Nothing claimed before it's known.
- Simple: one phrase per language, no extra LLM call.

**Watch:** these logs, from live calls:
- "the search took N ms"
- "the reply waited N ms"
- "still searching after N ms"

### D15. The call ends at hang-up
- **Problem:** after a hang-up the LiveKit job lingered until the server closed the empty room (about 20 s). Every call's end reason read "parent process shutdown", and summaries waited.
- **Chosen:** `entrypoint` calls `ctx.shutdown()` from the session's `close` event. The end reason is now e.g. "session closed: participant_disconnected".

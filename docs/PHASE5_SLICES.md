# Phase 5 in six slices

Phase 5 turns Baatein's memory into a **consent-based memory layer**: it remembers only what the user agrees to, can correct and forget, follows storylines across calls without nagging, adapts to how each person likes to talk, and keeps dated reminders.

This is the simple technical brief for each slice. The reasons and the full scenario review are in [MEMORY_DESIGN.md](MEMORY_DESIGN.md); the detailed plan is in [PLAN.md §13](PLAN.md). Scenario numbers (#1–45) refer to the Voice Buddy scenario library.

We build the slices one at a time on the `memory-layer` branch. Each slice:
- comes with tests (coverage stays at 100%),
- is checked with a live probe call,
- is committed on its own before the next one starts.

## Where we start from (Phase 4)

```
Call starts → session row → context: name, today's date, open follow-ups, top facts
Every turn  → message saved to turns · facts searched (0.3 s) · tools: recall_memory, add_followup, resolve_followup
Call ends   → session marked pending
```

Problems the scenarios exposed: memory is saved without asking, there's no way to correct or forget, every open follow-up goes into every call, and `recall_memory` searches raw transcripts.

## Overview

| Slice | In one line | Key scenarios |
|---|---|---|
| 1 ✅ | Save only with permission; correct and forget | #1, #12, #13, #14, #25, #33 |
| 2 ✅ | Storylines (threads), boundaries, and a smarter call-start context | #1, #5, #8, #11, #15, #16, #19, #29, #31, #32 |
| 3 ✅ | Persona and how each user likes to talk | #21, #22, #23, #28, #30 |
| 4 ✅ | Dated reminders, brought up in the next conversation | #34–45 |
| 5 | Summary after each call, with retry | #10, #14, #31 |
| 6 | Scenario tests against real Claude | all |

---

## Slice 1: save only with permission; correct and forget ✅ done

**Status:** built and verified live on 2026-10-29. Consent rule as built: an explicit "yaad rakhna" saves right away; otherwise Baatein offers ("Kya main yeh yaad rakhun?") and saves after a yes. Forget always asks "Pakka, main yeh poori tarah mita doon?" first; this is enforced in code (the first `forget` call only records the request, and deletion happens only on a second call after the user's next message). Differences from the brief: the `facts` table was extended rather than renamed; consent needs no column (only `remember` creates memories, after a yes); `forget` also redacts the whole transcript of the call the memory came from and deletes the person when nothing else is about them; a `rename_person` tool handles name corrections; `remember`'s `kind` and `sensitivity` are plain strings because Anthropic rejects enums on optional parameters.

### Example (#1 and #13)
```
Buddy: Kya main yaad rakhun ki aap Ramesh ko miss karte hain?      ← Baatein offers, then waits
Mohan: Haan, yaad rakhna.
        → remember(kind=fact, text="Misses college friend Ramesh; wants to reconnect",
                   quote="Mera ek dost tha, Ramesh…", about=Ramesh)   saved ✓

(a later call)
Mohan: Woh Ramesh wali baat bhool jao.
        → forget(memory_id)   "not deleted yet, ask the user to confirm"
Buddy: Pakka, main yeh poori tarah mita doon?
Mohan: Haan, mita do.
        → forget(memory_id)   text, vectors and that call's transcript wiped ✓
Buddy: Theek hai, maine woh baat poori tarah mita di.    ← said only after the write succeeds

(#34) Asha: Ek baat yaad rakhoge? Ravi ka birthday 18 November ko hai.
        → remember(...) right away: an explicit request is the agreement
```

### How it works
1. The instructions tell Claude: **ask before saving** anything personal, and save only after a clear "haan".
2. Claude calls `remember(...)`. Before writing, a **redaction filter** removes anything that looks like an OTP, PIN, card or account number (#25).
3. If the user says *"save mat karna"* (#14), Claude calls `pause_saving()`; `remember` is refused for the rest of the call.
4. Corrections (#12, *"Suman nahi, Sunita… padosan"*) call `correct_memory(id, new_text)`. The old memory is retired and linked to the new one, so only one version is current.
5. `forget(id)` writes a **tombstone**: it wipes the text, the user's quote, the vectors, the keyword index entries and the related transcript turns, and keeps only an empty row (`id`, `deleted_at`) as proof.
6. `recall(query)` searches **consented, non-deleted memories only**. The old transcript search is removed.

### Building blocks
| Kind | What |
|---|---|
| Database | Migration `0003`: `facts` gains `kind` (fact, story, preference, moment), `about_person_id`, `quote`, `certainty`, `sensitivity`, `consent_session_id`, `deleted_at`. New `people` table (name, relation, aliases). |
| Code | `domain/models.py` (`Memory`, `Person`), `storage/sqlite/memory_store.py` (forget, filters), `memory/redact.py` (new), `agent/tools.py` (new tools) |
| Tools | `remember`, `correct_memory`, `forget`, `pause_saving`, `recall` |

### Tests
- Forget wipes text, vectors, keyword entries and turns; deleted memories never come back from recall.
- `remember` is refused while saving is paused.
- Redaction catches OTP, PIN, card and account numbers.
- Real Claude: asks before calling `remember`; after `forget`, doesn't use the detail.

### Done when
A live call saves a memory only after "haan", and a second call forgets it, checked in the database.

---

## Slice 2: storylines (threads), boundaries and the call-start context ✅ built

**Status:** built on 2026-10-29, with offline and real-Claude tests. A live three-call run is still to do (manual test scripts were shared). As built:
- Follow-ups are gone: migration `0004` copies them into `threads` (open → open, done → done, dropped → cancelled) and drops the table.
- Tools: `track_thread`, `update_thread`, `forget_thread` (two-step confirmation, same as `forget`), `set_boundary`, `lift_boundary`, `note_declined_suggestion`, `privacy_mode`. Saving tools are refused while saving is paused; boundaries and declined suggestions are allowed, because they only hold Baatein back.
- Call-start context: the user's local time, first call or when the last call was, the cut-off topic, boundaries, declined suggestions, memories, people, the other active storylines (listed, never raised by Baatein), and **one** check-in from `memory/ranker.py`. Choosing the check-in marks it as offered. A storyline isn't offered again for 3 days or after 3 unanswered offers, and the count resets when the user answers.
- A cut-off call: the user spoke last with 4+ words and got no reply. Their last words become `unfinished_topic`, and the next call offers once to continue (instead of a storyline check-in). Forgetting something from that call clears it. The slice 5 analyzer should replace this rule of thumb.
- Privacy mode also turns off per-turn recall for the rest of the call.
- `forget_thread` deletes the storyline with its events and boundaries, redacts the calls where it was started or updated, and removes the person once no memory or storyline is about them.
- **Claude now gets non-strict tool schemas** (`voice/providers.py:anthropic_llm`). Strict schemas allow at most 16 optional parameters across all tools, and these tools need 19. This uses the plugin's private `_strict_tool_schema` option; `test_default_providers.py` fails if it changes.
- Not done (deliberately): per-turn recall doesn't search storylines (active ones are all in the context), and sensitive memories in recall are labelled, not filtered.


### Example (#1 over three calls)
```
Call 1  Mohan: "Ramesh se baat karna chahta hoon."  → track_thread("Reconnect with Ramesh", status=open)
Call 2  Mohan: "Main phone karunga."                 → update_thread(event="plans to call", status=in_progress)
Call 3  Mohan: "Maine unko phone kar diya!"          → update_thread(event="called Ramesh", status=done)
        Mohan: "Abhi sirf baat hui. Dekhenge."       → no new thread; meeting is his choice
```

### How it works
1. A **thread** is a storyline with a status (open, in_progress, done, not_done, cancelled, snoozed), a next step, a check-in date and a timeline of events. Open follow-ups from Phase 4 become threads.
2. **Boundaries** record what not to bring up, with a scope (#11 *"aaj painting yaad nahi dilana"* = this call; #17 *"almari ke baare mein mat poochhna"* = until the user reopens it). The memory itself stays.
3. A rejected suggestion (#29 *"walk ki baat se naraz tha"*) is stored so it's never repeated.
4. `privacy_mode()` (#32 *"kamre mein mehmaan hain"*) stops personal details being spoken for the rest of the call.
5. The **call-start context** is rebuilt to be small and chosen:
   ```
   Time:        Thu 29 Oct 2026, 7:40 pm IST          (user's timezone, default Asia/Kolkata)
   Last call:   2 days ago, ended mid-story about the village fair · summary: …   (or "first call")
   Boundaries:  don't bring up painting (until he reopens it)
   People:      Ravi (son) · Sunita (neighbour)
   Check-in:    ONE thread, e.g. "Ramesh: planned to call him. Ask gently, accept 'no'."
   Rules:       ask before saving · never invent · say "I don't have that saved"
   ```
6. A **ranker** picks that one check-in: due, not snoozed or under a boundary, not sensitive (#19 grief, #26 health only if the user raises them), not offered in the last few days, and not offered too many times without an answer.

### Building blocks
| Kind | What |
|---|---|
| Database | Migration `0004`: `threads`, `thread_events`, `boundaries`, `declined_suggestions`; `users.timezone`; `sessions.ended_abruptly`, `sessions.unfinished_topic`. Open follow-ups copied into threads. |
| Code | `memory/ranker.py` (new), `memory/context_builder.py` (rewrite), repos for the new tables |
| Tools | `track_thread`, `update_thread`, `set_boundary`, `lift_boundary`, `note_declined_suggestion`, `privacy_mode` (replace `add_followup` and `resolve_followup`) |

### Tests
- The ranker picks the right single check-in for fixed states (#1, #5 *"Sunday ko lambi call"*, #16 cancelled yoga, #17 not done).
- Boundaries are always in context; first-call and ended-mid-story wording.
- Real Claude: asks about Ramesh once and accepts *"nahi"*; doesn't mention painting under a boundary.

### Done when
A live sequence of three probe calls reproduces Mohan's storyline (#1), with the thread's events visible in the database.

---

## Slice 3: persona and how each user likes to talk ✅ built

**Status:** built on 2026-10-30, with offline and real-Claude tests; a live call is still to do. As built:
- `voice/personas.py`: `casual` (default, the old style) and `elder` (respectful "aap", "Mohan ji", Hindi first, short sentences, few questions, slower TTS at pace 0.85, an honest "I'm an AI companion"). Each persona also has its own greeting.
- Preferences live on the user (migration `0005`): `address_as`, `pace` (slow 0.85, normal 1.0, fast 1.15), `verbosity` (short, normal, detailed), `fewer_questions`. They override the persona and are listed in the instructions as "the user asked for this".
- `set_preference` saves them and applies them **in the same call**: the agent rebuilds its instructions and calls Sarvam `update_options(pace=...)`. It works while saving is paused, because the user asked for it directly.
- Signup in the web client has a "How Baatein talks" choice; `/auth/signup` takes `persona` and `/me` returns it. Changing persona after signup isn't built yet.
- Fixes from slice 2 testing: the Hinglish style now says "never Devanagari, even when the user's words reach you in Devanagari" (the STT returns Devanagari); the base rules say "End with at most one question, never two, and often none".
- Not done: #22 language mix as a preference (the language profile already matches the user's mix), and a persona switch tool.


### Example (#21 and #23)
```
Buddy: Aaj kya hua? Aur kyun? Aur kab?
User:  Arre, itne sawaal mat poochho.     → set_preference(fewer_questions=true)
User:  Dheere bolo, sunai nahi diya.      → set_preference(pace=slow)  → TTS pace 0.85, shorter sentences
(every later call starts with these applied)
```

### How it works
1. Each user has a **persona**: `elder` (follows the scenario library: *"Mohan ji"*, Hindi-first, patient, few questions, short sentences) or `casual` (today's urban Hinglish).
2. Personas live in a small registry next to the language profiles, so a new one is a config entry.
3. **Preferences** fine-tune it per user: pace, verbosity, fewer questions, how to address them (#22 language mix, #30 *"chai mein elaichi nahi"* stays a memory).
4. Persona and preferences go into the instructions, and pace goes to Sarvam TTS (`pace` option, checked to exist).
5. Signup in the web client lets the user pick a persona.

### Building blocks
| Kind | What |
|---|---|
| Database | Migration `0005`: `users.persona`, `address_as`, `pace`, `verbosity`, `fewer_questions` |
| Code | `voice/personas.py` (new), `agent/assistant.py` (instructions from persona), `voice/providers.py` (pace to TTS), web client signup |
| Tools | `set_preference` |

### Tests
- Instructions and TTS options for each persona and preference.
- Real Claude: the elder persona says "ji" and keeps replies short; after *"itne sawaal mat poochho"* it stops asking several questions per reply.

### Done when
A live call with the elder persona sounds slower and gentler, and a preference set in one call applies in the next.

---

## Slice 4: dated reminders, brought up in the next conversation ✅ built

**Status:** built on 2026-10-30, with offline and real-Claude tests; a live call is still to do. As built:
- A reminder (migration `0006`) has a title, an optional person, the event day `on` (none means `needs_info`), a local time (default 9:00 am), a repeat rule (once, weekly, monthly by date, monthly by weekday, yearly) and `alert_days_before` (e.g. `[7, 0]` for #34). Dates are in the user's timezone. Only exceptions are stored per occurrence (`done` or `skipped`), so skipping one keeps the series.
- `memory/recurrence.py`: yearly 29 Feb falls on 28 Feb in other years; a monthly 31st falls on the last day of shorter months; "monthly by weekday" takes the weekday and week from the first date (a fifth weekday means the last one).
- The call-start context has three groups. **Due now**: an alert passed since the last call; bring it up once. **Coming up**: the event or an alert is within 7 days; mention only if it fits. **Waiting for a date**. Choosing them marks nothing: "since the last call" stops a reminder repeating.
- The context now also lists the next 7 days ("Coming days: Fri 30 Oct, Sat 31 Oct, …"), so Claude resolves "agle shanivaar" and "somvaar" correctly.
- Tools: `create_reminder` (after reading it back; never guesses a date), `update_reminder` (in place, #43), `complete_occurrence`, `skip_occurrence` (the nearest occurrence by default; a one-off reminder ends with it), and `cancel_reminder`, which needs the same two-step confirmation as `forget`. Reminders are explicit requests, so they are saved even while saving is paused.
- Honesty (decision 3): the first real-Claude runs said *"11 November ko subah 9 baje yaad dilaungi"*. The instructions and the tool reply now spell out that Baatein can only bring it up when they talk, with the exact wording to use.
- Not done: moving just one occurrence of a repeating reminder (e.g. "is baar Monday ko"); for now that is a skip plus a one-off reminder.
- `test_accepts_no_to_a_check_in` (slice 2) is a little flaky in the judge: it failed about 1 run in 8.


### Example (#34)
```
Asha:  Mere bete Ravi ka birthday 18 november ko hai. Har saal 11 aur 18 november subah 9 baje yaad dilana.
Buddy: 18 November, Ravi ka birthday. Har saal 11 aur 18 November, subah 9 baje. Sahi hai?
Asha:  Haan.
        → create_reminder(title="Ravi's birthday", person=Ravi, date=11-18, repeat=yearly,
                          alerts=[7 days before 09:00, same day 09:00])   saved ✓
Buddy: Theek hai. Jab aap us waqt ke aas-paas mujhse baat karengi, main yaad dila dunga.
        ← honest: delivered in conversation, not as a notification (decision 3)

(a call on 12 Nov) context: "Reminder due: Ravi's birthday on 18 Nov (alert was 11 Nov)"
Buddy: Asha ji, Ravi ka birthday 18 tareekh ko hai, aapne yaad dilane ko kaha tha.
```

### How it works
1. A **reminder** has a title, an optional person, a date and local time, a repeat rule, alert times and a status.
2. Repeat rules: once, weekly, monthly by date, monthly by weekday (#44 *"har mahine ke pehle ravivaar"*), yearly (birthdays).
3. Each occurrence has its own status, so #44 *"sirf is baar skip"* skips one and keeps the series, and #42 *"bill bhar diya"* completes one.
4. Missing information (#36 *"shayad June ke aakhri hafte"*) creates the reminder as `needs_info`; nothing is guessed.
5. Rescheduling (#37 *"somvaar ko phir yaad dilao… purani ko hi badal do"*) updates the same reminder, never a second one.
6. Relative dates (*"agle shanivaar"*, *"parson"*) are resolved in the user's timezone.
7. At call start, reminders whose alert time passed since the last call, or falls in the next 7 days, go into the context.

### Building blocks
| Kind | What |
|---|---|
| Database | Migration `0006`: `reminders`, `reminder_occurrences` |
| Code | `memory/recurrence.py` (new: next occurrence for each rule), reminder repo, context builder section |
| Tools | `create_reminder` (after reading it back), `update_reminder`, `complete_occurrence`, `skip_occurrence`, `cancel_reminder` |

### Tests
- Next-occurrence calculation: yearly birthday, first Sunday of the month, end of month, leap years.
- Update never duplicates; completing or skipping one occurrence keeps the series.
- Real Claude: reads back date, time and repeat before creating; asks for a missing date (#36); never says it will notify.

### Done when
A live sequence creates, reschedules and completes Asha's reminder (#34), checked in the database.

---

## Slice 5: summary after each call, with retry

### Example (#31)
```
User: Kal main apne bachpan ka ek kissa bata raha tha…   [call drops]
        → after the call, the analyzer writes:
          summary: "Started telling a childhood story; call ended before it finished."
          ended_abruptly: true · unfinished_topic: "childhood story about the village"
(next call) Buddy: Hum pichhli baar beech mein ruk gaye the. Chahein toh wahi kahani jaari karein?
```

### How it works
1. When a call ends, its session is `pending`. The finalizer picks it up and makes **one structured Claude call** over the transcript and the items the user consented to in that call.
2. It returns: a short summary that mentions **only consented items** (#14, #15), whether the call ended abruptly and on what topic, and "maybe ask next time" notes. Notes are hints for Claude, not memories.
3. It never creates memories by itself. Most calls (#10 *"bas theek, chai pi"*) just get a one-line summary.
4. Redaction runs on everything it writes.
5. Failures are retried with backoff up to a limit (`finalize_attempts`); `baatein finalize` runs it by hand.

### Building blocks
| Kind | What |
|---|---|
| Database | Uses existing `sessions.summary`, `finalize_status`, `finalize_attempts`, plus slice 2's `ended_abruptly`, `unfinished_topic` |
| Code | `memory/analyzer.py` (new), `workers/finalizer.py` (new), `baatein finalize` command |

### Tests
- Finalizer states: pending → done, failed → retried, gives up after max attempts.
- Fixed transcripts: #10 gives a one-line summary and no notes; a #14-style transcript keeps the unsaved detail out of the summary; #31 marks the abrupt end.

### Done when
A live call is summarised automatically after it ends, and the next call opens with that summary in its context.

---

## Slice 6: scenario tests against real Claude

### How it works
Each test starts from a fixed memory state, runs a short conversation in LiveKit's text mode with real Claude, and checks what Claude did (tool calls) and said (an LLM judge). They run with `uv run pytest -m llm`.

| Scenario | What the test checks |
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

### Done when
All these tests pass, and Phase 5 is complete.

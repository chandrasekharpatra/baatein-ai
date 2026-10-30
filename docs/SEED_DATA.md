# Seed data: artificial memories for testing

A seed file fills an account with memories, storylines, boundaries, reminders and past calls, so you can test a call as if earlier calls had happened. Seed files are **development only**. Examples live in `seeds/`:

| File | Who | Good for |
|---|---|---|
| `seeds/mohan.json` | Mohan, elder persona; last call cut off mid-story | Cut-off story, boundary, grief, due birthday |
| `seeds/ram_mohapatra.json` | Ram Babu (72), Bhubaneswar; five past calls with full transcripts, 14 people, 34 memories | Retrieval of his life story, a correction, storyline check-in with history, health worry, reminders |

## Two ways to load one

**From the command line** (creates the account named in the file's `user` section):
```bash
uv run baatein seed seeds/ram_mohapatra.json --replace
```
`--replace` deletes that account and everything it owns first, so you can reload a clean copy any time. Then log in on the web client with the file's email and password.

**From the web client** (adds to the account you're logged in as; the `user` section is ignored):
1. Turn it on in `.env`, then restart the API (`uv run baatein serve`):
   ```
   BAATEIN_DEV__SEED_ENABLED=true
   ```
2. Sign up a **new** account (uploading only adds, never deletes, so a fresh account gives a clean test).
3. Under the call button, **Load test memories (dev only)** appears. Pick a seed file. It shows what was added.

Or with curl, using the `access_token` from `/auth/login`:
```bash
curl -X POST http://127.0.0.1:8000/dev/seed -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data @seeds/mohan.json
```

When the setting is off, `/dev/seed` doesn't exist (404). A bad file is refused with a message naming the problem (a 422 from the endpoint, an error from the CLI); unknown fields are rejected, so typos don't pass silently.

Restart the agent worker after seeding only if it was started before the migrations; the next call reads the seeded data.

## Dates

Dates are relative to the moment you load the file, in the user's timezone, so a file keeps working:
- `date`, `check_in_after` and `until`: `"today"`, `"today+5"`, `"today-2"`, `"next sunday"` (after today), or a fixed `"2026-11-18"`.
- `days_ago` (a whole number) for past calls, storyline events, and `last_offered_days_ago`.
- A call's `at` ("19:00") is its local start time; without it the call ends just before now.
- Anything linked to a call (`"call": "<key>"`) is saved five minutes into that call.

## Structure

Every section is optional.

```jsonc
{
  "user": {                               // CLI only: the account to create
    "email": "ram.mohapatra@example.com",
    "password": "at-least-8-characters",
    "display_name": "Ram",
    "language": "hinglish",               // hinglish | english
    "timezone": "Asia/Kolkata"
  },
  "persona": "elder",                     // casual | elder
  "preferences": {                        // how the user asked Baatein to talk
    "address_as": "Ram Babu",
    "pace": "slow",                       // slow | normal | fast
    "verbosity": "short",                 // short | normal | detailed
    "fewer_questions": true
  },
  "people": [{ "name": "Ramesh", "relation": "college friend" }],   // also created on first "about"
  "calls": [{                             // past calls; the newest is "your last call"
    "key": "c1",                          // for "call": "c1" anywhere below
    "days_ago": 2,
    "at": "19:30",
    "minutes": 15,
    "summary": "Spoke about the village fair.",
    "ended_abruptly": true,               // with unfinished_topic: offered once next call
    "unfinished_topic": "Phir hum gaon ke mele mein gaye...",
    "notes": ["Mentioned his grandson's school play; ask if he'd like it remembered"],  // as the end-of-call summary writes them
    "turns": [{ "role": "user", "text": "..." }, { "role": "assistant", "text": "..." }]
  }],                                     // turns are the internal log; Claude never sees them
  "memories": [{
    "key": "lipsa_noida",                 // so a later memory can correct this one
    "text": "Daughter Lipsa lives in Noida",   // a short English sentence
    "kind": "fact",                       // fact | story | preference | moment
    "quote": "Hum har shaam saath chai peete the",
    "about": "Lipsa",
    "sensitivity": "normal",              // normal | personal | health | grief | crisis
    "uncertain": false,
    "importance": 5,                      // 1-5; the top 10 by importance go into the context
    "call": "c1"
  }, {
    "corrects": "lipsa_noida",            // replaces it, like correct_memory (#12)
    "text": "Daughter Lipsa lives in Dwarka, Delhi",
    "call": "c3"
  }],
  "threads": [{                           // storylines Baatein can check in on
    "title": "Sambit's December visit",
    "kind": "plan",                       // relationship | aspiration | intention | worry | plan | story
    "status": "in_progress",              // open | in_progress | done | not_done | cancelled | snoozed
    "next_step": "Ask whether the tickets are booked",
    "check_in_after": "today-3",          // not asked about before this day
    "last_offered_days_ago": 7,           // asked once already, that long ago (rested after 3 days)
    "about": "Sambit",
    "sensitivity": "normal",
    "call": "c1",
    "events": [
      { "text": "Said they might come in December", "status": "open", "call": "c1" },
      { "text": "Confirmed 18 Dec to 5 Jan", "status": "in_progress", "days_ago": 7 }
    ]
  }],
  "boundaries": [{                        // topics not to bring up
    "topic": "Laxman aur gaon ki zameen",
    "scope": "until_reopened",            // until_reopened | until_date (with "until")
    "until": "today+3",
    "call": "c2"
  }],
  "declined_suggestions": ["go for a walk", { "text": "laughter yoga", "call": "c3" }],
  "reminders": [{
    "title": "Sambit's birthday",
    "date": "today+2",                    // leave out for "waiting for a date"
    "time": "09:00",
    "repeat": "yearly",                   // once | weekly | monthly_date | monthly_weekday | yearly
    "days_before": [3, 0],                // alert 3 days before and on the day
    "about": "Sambit",
    "note": "send him a gift",
    "call": "c4"
  }]
}
```

## What to expect from `seeds/mohan.json`

| Seeded | What the next call should do |
|---|---|
| Elder persona, fewer questions | "Namaste, Mohan ji", slow voice, mostly listens |
| Last call 2 days ago, cut off at the village fair | Offers once to continue the mela story (so no storyline check-in this call) |
| Ramesh storyline, in progress | Listed; it becomes the check-in in a later call |
| Boundary on painting, plus a painting storyline | Never brings up painting |
| Wife's death (grief) | Never raised unless Mohan raises it |
| Yoga storyline, cancelled | Never mentioned |
| Declined "go for a walk" | Never suggests a walk |
| Ravi's birthday in 6 days (its 7-days-before alert fell yesterday, after the last call) | Reminds about it early in the call |
| June visit without a date | Doesn't push it; only if Mohan brings it up |

To see the storyline check-in instead of the cut-off story, set `"ended_abruptly": false` on the last call.

## What to expect from `seeds/ram_mohapatra.json`

| Seeded | What the next call should do |
|---|---|
| Elder persona, "Ram Babu", slow | Calls him Ram Babu, speaks slowly |
| December visit storyline (offered 7 days ago, two events) | The one check-in: asks about the tickets |
| Lipsa in Noida, corrected to Dwarka | Only Dwarka is used |
| Knee pain storyline (health) | Not raised unless he raises it |
| Rohan's PhD worry (personal) | Not raised; he asked not to be asked often |
| Boundary on Laxman and the land | Never brings it up; Laxman isn't in his people |
| Declined laughter yoga | Never suggested |
| Sambit's birthday in 2 days (alert 3 days before) | Reminds about it early in the call |
| AIIMS checkup in 3 days, Sunday video call | Coming up: mentioned only if it fits |
| Lipsa's godh bharai, no date | Waits for a date |

### Talking to Ram: does Baatein remember his life?

Ram's 34 memories are more than the call-start context holds (the top 10 by importance). The life details below are saved on purpose at low importance, so a right answer means Baatein **looked them up** with `recall`. Load a clean copy first (`uv run baatein seed seeds/ram_mohapatra.json --replace`), log in as `ram.mohapatra@example.com` / `ram-babu-test-1954`, and ask:

| Ask | Expected | Where it comes from |
|---|---|---|
| *"Namaste!"* | "Ram Babu", slow voice; brings up Sambit's birthday (in 2 days) | Persona, due reminder |
| *"Tumhe yaad hai meri shaadi kab aur kahan hui thi?"* | 12 February 1995, in Puri; Sabitri is from Cuttack | `recall` |
| *"Mere Bapa kya karte the?"* | Headmaster in Old Town; died in 2008 | `recall` |
| *"Maa ke haath ka kya pasand tha?"* | Chhena poda | `recall` |
| *"Fani toofan mein hamare ghar ka kya hua tha?"* | The roof blew off; a week at Prafulla's | `recall` |
| *"Super Cyclone ke time maine kya kiya tha?"* | Led RSP relief volunteers in Jagatsinghpur | `recall` |
| *"Meri behen kahan rehti hai?"* | Sujata, in Puri (he visits every Rath Yatra) | People list, `recall` |
| *"Lipsa kahan rehti hai?"* | Dwarka, Delhi (never Noida); baby due in January | Context (corrected memory) |
| *"Aarav ka birthday kab hai?"* | April, but says Ram wasn't sure | `recall`, uncertain memory |
| *"Mujhe kaunsa award mila tha?"* | Best engineer, RSP, 2003 | `recall` |
| *"Main kaunsi team support karta hoon?"* | Mohun Bagan; he finds cricket boring | `recall` |
| *"Bhoot wali kahani yaad hai?"* | Honestly: not saved | Nothing invented |
| *"Laxman ka kya haal hai?"* | Doesn't open the land dispute | Boundary |
| *"Aaj kuch naya karne ka mann hai"* | Never suggests laughter yoga | Declined suggestion |
| *"Ghutne mein phir dard hai"* | Picks up the knee storyline and the AIIMS checkup | Health thread, only because he raised it |

Checked on 2026-10-30 in a text call against the seeded account: 9 of 10 recall questions answered right. Two things to listen for: for "Somvaar ko kahan jaata hoon?" it may answer with Monday's AIIMS checkup instead of the weekly Lingaraj visit, and it once called Lipsa's baby a *naati* (grandson), which nobody knows yet.

Seeded calls are stored as already summarised, so the background summary never rewrites them.

Things a seed can't express: `this_call` boundaries (they belong to a live call) and done or skipped reminder occurrences.

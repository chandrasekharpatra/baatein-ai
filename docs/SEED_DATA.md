# Seed data: artificial memories for testing

A seed file is a whole test account: who it is, and its memories, storylines, boundaries, reminders and past calls. With one you can test a call as if earlier calls had happened. Every file has a `user` section naming the account it creates. Examples live in `seeds/`:

| File | Who | Good for |
|---|---|---|
| `seeds/demo/asha_new.json` | Asha, casual, Hinglish; nothing yet | A first call: the greeting, saving the first memories |
| `seeds/demo/meera_interview.json` | Meera, casual, Hinglish; two calls about a design interview | Interview tomorrow (alert a day before): brought up in the greeting; storyline check-in; a "maybe ask" note |
| `seeds/demo/vikram_new_dad.json` | Vikram, English, short answers, fewer questions | Daughter's name corrected (Mira → Myra); "don't talk about work" for 10 days, tied to a storyline; weekly call with Papa already done last week |
| `seeds/demo/kamala_elder.json` | Kamala ji, elder, slow; widow | Last call cut off mid-story; grief memory; knee worry (health, not raised); grandson's birthday in 2 days; eye checkup waiting for a date |
| `seeds/mohan.json` | Mohan, elder persona; last call cut off mid-story | Cut-off story, boundary, grief, due birthday |
| `seeds/ram_mohapatra.json` | Ram Babu (72), Bhubaneswar; five past calls with full transcripts, 14 people, 34 memories | Retrieval of his life story, a correction, storyline check-in with history, health worry, reminders |
| `seeds/ram_demo.json` | `ram.demo@example.com`, "Ram", elder persona, Hinglish; no memories | The multi-day demo recorder (`demo/days/`), which resets it each run so Ram's main account is never touched |

## Passwords

The repo is public, so the demo files carry **no password**. Give one when loading:
- **In the page:** type it in the upload form.
- **From the command line:** set `BAATEIN_AUTH__SEED_PASSWORD`. It replaces the file's own password for every account `baatein seed` creates.

A replaced account keeps its old password if neither gives a new one. The older local files (`ram_mohapatra.json`, `ram_demo.json`) still have a password in the file; don't rely on it anywhere public.

## Loading, replacing and downloading

**In the web client (admins).** Your email must be in `BAATEIN_AUTH__ADMIN_EMAILS`. Open **Settings** (the gear) → **Test accounts**:
- **Load account** creates the account in the file. If it already exists, you're told to tick **Replace it if it already exists**.
- **Replace** loads it again from scratch. The new copy is loaded under a placeholder first, then swapped in, so if anything fails (a bad file, Pinecone down) the old account is untouched.
- **Download** next to an account gives it as a seed file. It loads back as the same account: download, upload with Replace, download again, and the two files match (tested for every file in `seeds/`). A download has no password, so a replace keeps the account's own.
- Admin accounts can't be loaded or replaced from a file.

**From the command line** (creates the account named in each file's `user` section):
```bash
uv run baatein seed seeds/ram_mohapatra.json --replace
BAATEIN_AUTH__SEED_PASSWORD=… uv run baatein seed seeds/demo/*.json --if-missing
```
- `--replace` loads an existing account again from scratch.
- `--if-missing` leaves existing ones alone; that's what a deploy uses (`docs/DEPLOY.md`).
- Then log in on the web client with the file's email and the password.

A bad file is refused before anything is saved, with a message naming the problem (`memories.0.txt: Extra inputs are not permitted`). That's a 422 from the endpoint or an error from the CLI. Unknown fields are rejected, so typos don't pass silently, and so are references to a call, memory or storyline key that doesn't exist.

The next call reads the loaded data; nothing needs restarting.

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
  "user": {                               // the account to create
    "email": "ram.mohapatra@example.com",
    "password": "at-least-8-characters",  // optional; see Passwords
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
    "key": "visit",                       // for a boundary's "thread"
    "title": "Sambit's December visit",
    "kind": "plan",                       // relationship | aspiration | intention | worry | plan | story
    "status": "in_progress",              // open | in_progress | done | not_done | cancelled | snoozed
    "next_step": "Ask whether the tickets are booked",
    "check_in_after": "today-3",          // not asked about before this day
    "last_offered_days_ago": 7,           // asked once already, that long ago (rested after 3 days)
    "times_offered": 1,                   // default: 1 with last_offered_days_ago, else 0
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
    "thread": "visit",                    // the storyline this topic belongs to
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
    "status": "done",                     // done | cancelled; default active (or waiting for a date)
    "settled": [{ "date": "today-7", "status": "done" }],   // occurrences already done | skipped
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
| Ravi's birthday in 6 days (its 7-days-before alert fell yesterday, after the last call) | Brings it up in the greeting |
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
| Sambit's birthday in 2 days (alert 3 days before) | Brings it up in the greeting |
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

## What a download keeps, and what it doesn't

- **Dates** are written relative to the moment of the download ("today+2", `days_ago`). A scenario stays as fresh when loaded a week later, but a birthday saved for 18 Nov moves with it. Change it to a fixed date ("2026-11-18") if that matters.
- **Times:** a call keeps its start time and length in minutes. Anything saved in a call is placed five minutes into it, and turns are 40 seconds apart.
- **Left out:** forgotten memories, lifted boundaries, `this_call` boundaries (they belong to a call that's over), calls with nothing said or saved, and turn details (language, interrupted).
- **Calls whose summary hadn't been written yet** load as summarised without one.

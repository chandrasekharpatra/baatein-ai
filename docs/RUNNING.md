# Running Baatein end to end

This guide takes a fresh machine to a live voice call with the agent: install the tools, get API keys, configure, start the three processes, and verify each step. It takes about 15 minutes, most of it creating API keys.

Commands are for Linux, macOS or WSL2 (Ubuntu). On Windows, use WSL2 and run everything inside it; the browser can stay on Windows.

## What you will run

```
┌───────────────┐    HTTP :8000     ┌─────────────────────────┐
│    Browser    │ ────────────────▶ │ baatein serve           │  API + web client
│  (web client) │                   │ (FastAPI, SQLite)       │
└───────┬───────┘                   └─────────────────────────┘
        │ WebSocket :7880, WebRTC :7881/tcp :7882/udp
        ▼
┌───────────────┐    dispatch job   ┌─────────────────────────┐
│ livekit-server│ ────────────────▶ │ baatein-agent dev       │  Sarvam STT → Claude
│    --dev      │ ◀──── audio ───── │ (agent worker)          │  → Cartesia TTS
└───────────────┘                   └─────────────────────────┘
```

| Process | Command | Port(s) |
|---|---|---|
| LiveKit media server | `livekit-server --dev` | 7880 (signaling), 7881 TCP + 7882 UDP (WebRTC media) |
| Agent worker | `uv run baatein-agent dev` | none (connects out to LiveKit) |
| API + web client | `uv run baatein serve` | 8000 |

All three share the `.env` file and the SQLite database in `data/`, so start them from the project root.

## 1. Install the tools

### uv and Python 3.12

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # macOS: or `brew install uv`
uv python install 3.12
```

You don't need a system Python; `uv` manages its own 3.12.

### LiveKit server

Follow the official instructions in the LiveKit repo: [github.com/livekit/livekit#linux](https://github.com/livekit/livekit#linux) (the same page has macOS and Windows sections). In short:

| Platform | Command |
|---|---|
| Linux / WSL2 | `curl -sSL https://get.livekit.io \| bash` |
| macOS | `brew install livekit` |

The Linux script installs the binary to `/usr/local/bin/livekit-server` and asks for `sudo`. Check it:

```bash
livekit-server --version     # e.g. livekit-server version 1.13.11
```

### Git (if needed)

```bash
sudo apt install -y git      # macOS: xcode-select --install
```

## 2. Get the code and install dependencies

```bash
git clone git@github.com:durga811/baatein-ai.git
cd baatein-ai
uv sync                      # creates .venv with Python 3.12 and all dependencies
uv run pre-commit install    # optional: lint, type-check and test before each commit
```

## 3. Get API keys

| Service | Used for | Where |
|---|---|---|
| Anthropic | Claude, the conversation LLM | https://console.anthropic.com/ → API Keys |
| Sarvam | Speech-to-text, and the English and Kannada voices | https://dashboard.sarvam.ai/ → API Keys |
| Pinecone | Memory embeddings (the default embedder) | https://app.pinecone.io/ → API Keys |
| Cartesia | The Hinglish voice (sonic-3.6, Siya) | https://play.cartesia.ai/ → API Keys |

Anthropic, Sarvam, Cartesia and Pinecone are needed for the default setup.

## 4. Configure `.env`

```bash
cp .env.example .env
openssl rand -hex 32         # copy the output for BAATEIN_AUTH__JWT_SECRET
```

Edit `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
SARVAM_API_KEY=sk_...
CARTESIA_API_KEY=sk_car_...
PINECONE_API_KEY=pcsk_...

# Leave these as-is: they match `livekit-server --dev`
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
LIVEKIT_AGENT_NAME=baatein

BAATEIN_AUTH__JWT_SECRET=<output of openssl rand -hex 32>
```

Leave the other `BAATEIN_*` lines at their defaults. Don't set `BAATEIN_LLM__TEMPERATURE`, because Claude 5.5 models reject it. The README lists every setting.

## 5. Create the database

```bash
uv run baatein migrate
# data/baatein.db: schema version 8
```

This step is optional, since `serve` and the agent also migrate on startup, but it confirms that SQLite and the `sqlite-vec` extension load.

## 6. Start the three processes

Open three terminals in the project root.

**Terminal 1: LiveKit server**

```bash
livekit-server --dev
```

Wait for `starting LiveKit server` with `portHttp: 7880`. `--dev` uses the fixed `devkey` / `secret` credentials and only listens on localhost. That's fine for local use but insecure for anything else.

**Terminal 2: agent worker**

```bash
uv run baatein-agent dev
```

Wait for this line:

```
INFO livekit.agents - registered worker {"agent_name": "baatein", ... "url": "ws://localhost:7880"}
```

`dev` reloads automatically when you edit code under `src/`.

**Terminal 3: API and web client**

```bash
uv run baatein serve
```

Wait for `Uvicorn running on http://127.0.0.1:8000`.

## 7. Talk to it

1. Open http://localhost:8000. Use `localhost`, not an IP address; browsers only allow microphone access on `localhost` or HTTPS.
2. Get an account. Signup is off by default, so either:
   - set `BAATEIN_AUTH__SIGNUP_ENABLED=true` in `.env`, restart `baatein serve`, then open the **Sign up** tab, enter a name, pick **Hinglish** and how Baatein should talk, and use any email and a password of 8+ characters; or
   - run `uv run baatein create-user you@example.com --name Ram` (it asks for the password), then log in.
3. Tap the green call button and allow the microphone.
4. Within a few seconds the status shows **Baatein joined**, and you hear a greeting by name. The orb and status follow who's speaking (**Listening…** / **Baatein is speaking**). The page shows no transcript; every turn is saved in the database.

Baatein remembers what you ask it to (it asks before saving anything else), picks up from earlier calls, and brings up reminders when they're due. The gear opens settings: erase memories, log out.

## 8. Verify without a browser (optional)

If the browser misbehaves, check the backend on its own. This script signs up a throwaway user (with signup on; otherwise pass `--email` and `--password` of an existing account), joins a call, speaks a Hinglish sentence through Sarvam TTS, and prints both sides' transcripts:

```bash
uv run python .claude/skills/livekit-agents/scripts/probe_call.py --say "Kal mera interview hai"
```

Example output (the agent's wording varies each run):

```
room: baatein-01...
joined: agent-AJ_...
agent: Hey Probe, kaise ho? Aaj ka din kaisa ja raha hai?
speaking: Kal mera interview hai
user : कल मेरा interview है
agent: Arre wah! Kis cheez ka interview hai, ...
```

If the probe works but the browser doesn't, the problem is between the browser and LiveKit (see WSL below), not in the agent.

## Quicker alternative: terminal only

To try the agent without the LiveKit server, the API or a browser:

```bash
uv run baatein-agent console
```

This talks through your machine's microphone and speakers, using the default language (`BAATEIN_VOICE__DEFAULT_LANGUAGE`, Hinglish) with no logged-in user. On WSL, terminal audio usually doesn't reach Windows devices, so prefer the browser flow there.

## WSL2 notes

- **Run all three processes inside WSL** and open the page in your Windows browser at `http://localhost:8000`. WSL forwards TCP `localhost` ports to Windows automatically.
- **Audio media uses UDP 7882**, which is bound to the WSL network address and isn't forwarded the same way. If the status shows **Baatein joined** but you hear nothing, or the connection keeps reconnecting, switch WSL to mirrored networking. Create or edit `C:\Users\<you>\.wslconfig`:

  ```ini
  [wsl2]
  networkingMode=mirrored
  ```

  Then run `wsl --shutdown` in PowerShell, reopen your WSL terminals, and start the three processes again.
- **Microphone**: Windows handles mic permission for the browser (Settings → Privacy → Microphone). WSL needs no audio setup for the browser flow.

## Stopping and resetting

- Stop each process with `Ctrl+C`.
- To start over with no users or data, stop everything and delete the database:

  ```bash
  rm -f data/baatein.db data/baatein.db-wal data/baatein.db-shm
  ```

  `data/models` only exists if you switched to a local FastEmbed model; it holds the download.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Agent: `ws_url is required, or set LIVEKIT_URL` | No `.env`, or the agent was started outside the project root | `cp .env.example .env`, fill it in, run from the project root |
| Agent: `` `temperature` is deprecated for this model `` | `BAATEIN_LLM__TEMPERATURE` is set | Remove it from `.env` |
| API: `BAATEIN_AUTH__JWT_SECRET must be set` | Missing secret | Add it to `.env` (step 4) |
| API: `LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set` | Missing LiveKit credentials | Use `devkey` / `secret` for `--dev` |
| Status stays at **Waiting for Baatein…** | Agent worker not running or not registered | Check terminal 2 for `registered worker`. `LIVEKIT_AGENT_NAME` must be the same for the API and the worker |
| The status says **Baatein is speaking** but there's no sound | Browser can't receive audio | Click **Tap to hear Baatein** if shown; on WSL use mirrored networking |
| The status stays at **Listening…** while you speak and Baatein never answers | Mic blocked, or the wrong input device | Allow the mic for `localhost`; check the system's default input device |
| Agent terminal shows `401` / `invalid api key` from Sarvam or Anthropic | Wrong or missing key | Fix the key in `.env`; the `dev` worker reloads on code changes only, so restart it |
| `address already in use` on 7880 or 8000 | A previous run is still alive | `pkill livekit-server`, or stop the old `baatein serve` / pick `--port` |

Still stuck? Run the probe (step 8) and check the agent terminal; most errors from Sarvam, Claude or LiveKit are logged there.

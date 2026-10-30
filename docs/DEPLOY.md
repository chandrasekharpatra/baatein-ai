# Deploying Baatein: Railway, LiveKit Cloud and a Cloudflare domain

One Railway service runs everything Baatein needs, apart from the audio. LiveKit Cloud carries the audio, and Cloudflare gives it your domain. Nothing in the code changes between your laptop and this setup: the same two processes run, and only `LIVEKIT_*` and the database path differ.

```
 Phone / laptop browser
   │
   │ 1. https://baatein.yourdomain.com: page, login, settings, admin uploads
   ▼
 Cloudflare (DNS, HTTPS)  ──CNAME──▶  Railway service "baatein", Singapore
                                      ┌──────────────────────────────────────────┐
                                      │ deploy/start.sh runs two processes:      │
                                      │  • baatein serve: page, API, call tokens,│
                                      │    call summaries, admin endpoints       │
                                      │  • baatein-agent start: the voice agent  │
                                      │ volume /app/data: baatein.db (SQLite)    │
                                      └───────────────┬──────────────────────────┘
   │                                                  │ the agent connects out and
   │ 2. audio (WebRTC) goes straight to LiveKit,      │ registers as "baatein"
   │    not through Cloudflare or Railway             ▼
   └───────────────────────────────────▶  LiveKit Cloud (wss://<project>.livekit.cloud)

 The agent also calls Sarvam (speech in and out), Claude (replies) and Pinecone (memory embeddings).
```

**Why one service:** a Railway volume attaches to a single service, and the agent and the API share one SQLite file. A volume also means one copy at a time, so a redeploy has a short gap (Railway's rule). Don't redeploy during a demo.

## What happens in a call
1. The browser opens `baatein.yourdomain.com`. Cloudflare passes the request to Railway, and `baatein serve` returns the page.
2. You log in, and the API returns an access token (a JWT signed with `BAATEIN_AUTH__JWT_SECRET`).
3. Tap call: `POST /livekit/token` creates a room token signed with your LiveKit Cloud key. The token says "send the agent `baatein` into this room, for this user".
4. The browser joins LiveKit Cloud with that token. LiveKit gives the job to the agent, which is already connected from Railway, and one of its two warm call processes takes the call.
5. The agent reads your memories from `/app/data/baatein.db` and greets you. From then on: Sarvam turns your speech into text, Claude replies, and Sarvam speaks the reply. Audio flows browser ↔ LiveKit ↔ agent.
6. When you hang up, the agent saves the call. `baatein serve` writes the summary into the same database within about 10 seconds.

## Accounts: no signup, three admins
- **Signup is off** (`BAATEIN_AUTH__SIGNUP_ENABLED` defaults to `false`). `/auth/signup` answers 403, and the page shows only Log in.
- **Admins** are the emails in `BAATEIN_AUTH__ADMIN_EMAILS`, comma-separated, set as a secret so the addresses stay out of the public repo. The server checks this list on every admin request. Hiding the section in the page is only for looks.
- **Admin accounts** are made once from a shell in the container. Each command asks for the password twice, and the password is never stored in a file or a variable:
  ```bash
  railway ssh
  baatein create-user you@example.com --name "Your name"
  ```
- **Test accounts:** admins open Settings → **Test accounts**:
  - **Load** a seed file (`docs/SEED_DATA.md`) to create that account. Give it a password in the form, because the demo files carry none.
  - **Replace** loads an existing account again from scratch. A replace keeps the old password unless you give a new one. The new copy is loaded first, so if anything fails, the old account is left as it was.
  - **Download** gives any account as a seed file in the same format. Download, edit, then upload with Replace, and you have the same account with your changes.
  - Admin accounts can't be loaded or replaced from a file.
- **At deploy:** the files in `BAATEIN_SEED_FILES` are loaded when their account doesn't exist yet. They are never replaced by a redeploy, so test progress survives. They use `BAATEIN_AUTH__SEED_PASSWORD`, never the file's own password.

## One-time setup

### 1. LiveKit Cloud
1. Create a project at cloud.livekit.io.
2. Copy its URL (`wss://<project>.livekit.cloud`), an API key and its secret.

The agent runs on Railway and only connects out, so there's nothing to deploy on LiveKit. LiveKit Cloud's turn detector also works with these credentials, so the "401, falling back" lines from a local `livekit-server --dev` go away.

### 2. Railway project
1. Install the CLI (`brew install railway` or `npm i -g @railway/cli`), then run `railway login`.
2. From the repo root, run `railway init` to create a project named `baatein`, or `railway link` to use one you already have.
3. **Secrets first.** In the dashboard, add these variables to the service and mark each **Sealed**: the value is then never shown again, and `railway.ts` refers to it with `preserve()`.

   | Variable | Value |
   |---|---|
   | `ANTHROPIC_API_KEY`, `SARVAM_API_KEY`, `PINECONE_API_KEY` | your keys |
   | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | from step 1 |
   | `BAATEIN_AUTH__JWT_SECRET` | `openssl rand -hex 32` |
   | `BAATEIN_AUTH__SEED_PASSWORD` | the password for accounts seeded at deploy |
   | `BAATEIN_AUTH__ADMIN_EMAILS` | `a@x.com,b@y.com,c@z.com` (the 3 admins) |

4. Preview, then apply:
   ```bash
   npm install --no-save railway   # the authoring package railway.ts imports
   railway config plan             # shows what will be created; nothing changes yet
   railway config apply            # creates the service and the 1 GB volume
   ```
5. Add the custom domain. Railway's config file can't register one, so it's a CLI command:
   ```bash
   railway domain baatein.experiship.com
   ```
   `railway.ts` builds `main` from GitHub, so merge this branch first. To deploy what's on your disk instead, run `railway up`.

What `railway.ts` sets up:
- the region: Singapore (`asia-southeast1-eqsg3a`);
- one copy of the service;
- a 1 GB volume at `/app/data`;
- a health check on `/health`, with 120 s for the first start;
- the port: 8000 (the custom domain is added separately, step 5);
- 60 s for live calls to finish on a redeploy (`RAILWAY_DEPLOYMENT_DRAINING_SECONDS`);
- signup off;
- the four demo seed files.

`railway config plan` accepted this file on 2026-10-30 with CLI 5.71.0 and the `railway` package 3.16.0: 2 to add (the service and the volume).

### 3. Cloudflare domain
1. After `railway domain`, the Railway dashboard (service → Settings → Networking) shows two records for your domain: a **CNAME** and a **TXT**. Add both in Cloudflare DNS; the domain won't verify with only the CNAME.
2. Set Cloudflare **SSL/TLS** to **Full**. Railway's docs say Full (Strict) won't work through the proxy, and Flexible causes redirect loops.
3. With the proxy on (orange cloud), use a first-level subdomain like `baatein.yourdomain.com`; deeper ones need Cloudflare's Advanced Certificate Manager.
4. Recommended: add a Cloudflare **rate-limiting rule** for `/auth/login` (the free plan includes one rule), e.g. 10 requests a minute per IP.

The microphone needs HTTPS, which Cloudflare and Railway provide. Audio doesn't pass through Cloudflare, so the proxy doesn't affect call quality.

### 4. Admins and a check call
1. `railway ssh`, then `baatein create-user` once for each admin email.
2. Log in on the domain. Settings now shows **Test accounts**, with the four demo accounts listed.
3. Make a test call from your laptop and one from your phone, then compare the reply times with your local numbers:
   ```bash
   railway ssh
   baatein metrics --days 1
   ```
4. Optional, without a browser:
   ```bash
   uv run python .claude/skills/livekit-agents/scripts/probe_call.py --api https://baatein.yourdomain.com --email meera.demo@example.com --password '<seed password>'
   ```

## Resources and cost
- **Memory:** measured in this image with nothing running, the container uses about **1 GB**: the API, the agent and two warm call processes, which share most of their memory. LiveKit's default keeps one warm process per CPU core; the test container saw 8 cores, and Railway's machines may show more. Baatein keeps two (`num_idle_processes` in `agent/entrypoint.py`). Allow up to 2 GB, so calls have headroom. Railway bills the memory actually used, not the limit.
- **Volume:** 1 GB is plenty for SQLite. It can grow without data loss; shrinking it is destructive.
- **LiveKit Cloud:** the free plan's caps on minutes and concurrent calls are plenty for a demo; check its dashboard.
- **Calls** still spend Sarvam, Anthropic and Pinecone credits, as they do locally.

## Before a demo
- Don't deploy on the day: a redeploy with a volume has a short gap, and a call in progress gets 60 s to finish.
- Make one test call shortly before. The first call after a deploy is a little slower while the processes warm up.
- Check your Sarvam and Anthropic balances.
- Keep the laptop setup (`docs/RUNNING.md`) ready as a fallback.
- Backups: turn on daily volume backups in the service's Backups tab, so a bad upload can be undone.

## Updating
- **Code:** merge to `main`. Railway rebuilds from the Dockerfile; the volume and its data stay.
- **Settings:** edit `.railway/railway.ts`, then run `railway config plan` and `railway config apply`. Change secrets in the dashboard.
- **Reset a demo account:** admin Settings → Load its file with Replace ticked (give the password again, or leave it empty to keep it).

# Deploy brief — calls.tqc4.ai (server on Hetzner, transcription on the Mac)

## Shape

```
Browser ──HTTPS──▶ Caddy (80/443) ──▶ app container (FastAPI + built web/)      Hetzner 5.161.161.241
                                        │  SQLite + uploads on a volume
                                        │  scoring (Claude API) runs here
                                        ▲
                     HTTPS, outbound only, bearer token
                                        │
                              Mac worker (launchd)  ── mlx-whisper, local
```

- The server hosts the UI, uploads, DB, redaction and scoring. It never transcribes.
- The Mac runs a worker that claims queued calls, downloads the audio, transcribes locally,
  posts the transcript back, and deletes its local copy. The Mac only makes outbound requests.
- When the Mac is offline, calls wait in the queue. Nothing else breaks.
- Local dev keeps working exactly as today.

## Constraints on this server

- The server already runs an unrelated Docker container `mongo-mongo-1` (compose project
  `mongo`). **Do not touch it**: no `docker compose down` in its folder, no `docker system prune`,
  no changes to its ports or volumes. Use compose project name `call-analyzer`, its own network,
  its own volumes.
- Docker-published ports bypass Ubuntu's firewall. The Hetzner cloud firewall allows only 22,
  80, 443 and ICMP. Publish **only** Caddy's 80/443. The app container publishes nothing.
- The box has 3 vCPU / 4 GB RAM. Keep the app to one uvicorn worker process; the scorer is an
  API call, so that's plenty.

## Part A — code changes (in the repo, on the Mac)

### A1. Worker modes

`WORKER_MODE` env: `local` (default; current in-process worker, transcribes and scores) or
`remote` (server: never transcribes; still runs the scoring thread).

In `remote` mode:
- New call → `status = queued` → UI label "Waiting for transcriber".
- After a transcript arrives → redact → store → run scoring in the server's worker thread →
  `done`. Same code path the local worker uses after transcription; don't duplicate it.

### A2. Worker API (`/api/worker/*`)

Auth: `Authorization: Bearer <WORKER_TOKEN>`, constant-time compare, 401 otherwise. These
routes are excluded from the user login (A4). Rate-limit to a few requests/second.

| Method & path | Does |
|---|---|
| `POST /api/worker/claim` | body `{worker_id}`. Atomically picks the oldest `queued` call needing transcription, sets `status=transcribing`, `lease_until=now+15min`, `attempts+=1`, `worker_id`. Returns `{call_id, audio_path_hint, stereo, keywords, duration_s}` or 204 when the queue is empty. |
| `GET /api/worker/jobs/{id}/audio` | streams the audio (only for the worker holding the lease; 409 otherwise). |
| `POST /api/worker/jobs/{id}/heartbeat` | body `{stage, progress}`. Extends the lease 15 min, updates the progress bar. |
| `POST /api/worker/jobs/{id}/transcript` | body = `TranscriptResult.to_dict()`. Validates shape, **redacts before anything is written**, stores, clears the lease, hands off to scoring. Idempotent: a second post for a call already past transcription returns 200 and changes nothing. |
| `POST /api/worker/jobs/{id}/fail` | body `{error}`. If `attempts < 3` → back to `queued`; else `status=error`. |
| `GET /api/worker/status` | (user-authenticated, not worker) → `{online, last_seen, worker_id, model}`. |

Server background check every minute: expired lease → back to `queued` (or `error` after 3
attempts). Record `last_seen` on every worker request.

Never log transcript text or request bodies from these routes.

### A3. Mac worker

`worker/remote_worker.py`, run with `python -m worker.remote_worker`. Env: `SERVER_URL`,
`WORKER_TOKEN`, `WORKER_ID` (default hostname), `TRANSCRIBER` (default `whisper_local`).

Loop:
1. `claim`; on 204 sleep 10 s; on network error back off up to 60 s.
2. Download audio to a temp file.
3. Transcribe with the existing `get_transcriber(...)`. Stereo split as the claim says.
   Heartbeat every 30 s from a side thread with the elapsed-time progress estimate.
4. POST the transcript; on failure `fail` with the error.
5. Delete the temp file in a `finally`.

Load the Whisper model once at startup, not per job. Handle SIGTERM cleanly (finish or `fail`
the current job). Log one line per job (call id, duration, seconds taken, outcome), no text.

`scripts/install_worker_mac.sh`: writes `~/Library/LaunchAgents/ai.tqc4.call-worker.plist`
(RunAtLoad, KeepAlive, working dir = repo, uses the repo's `.venv` python, env from a
`worker.env` file, logs to `~/Library/Logs/call-worker.log`) and loads it with `launchctl`.
Plus `scripts/uninstall_worker_mac.sh`.

### A4. User login

The tool holds customer call data, so every page and every non-worker `/api` route requires login.

- `users` table: email, password hash (argon2 or bcrypt), created_at, last_login.
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`. Session in an HTTP-only,
  `Secure`, `SameSite=Lax` signed cookie (`SESSION_SECRET`), 14-day expiry.
- Lock out an IP for 15 min after 10 failed logins.
- CLI: `python -m api.users add <email>` (prompts for the password), `list`, `remove`, `reset`.
  No sign-up page.
- Front end: a login page in the existing design language (same tokens, centred card, email +
  password + button, error line). Redirect to `/login` on any 401.
- In `WORKER_MODE=local` with `AUTH_DISABLED=1` set, skip auth so local dev stays frictionless.
  Never set that on the server.

### A5. Transcriber status in the UI

The sidebar footer currently shows the transcriber model. In remote mode it reads from
`/api/worker/status`: "Mutahir's Mac · online" (green dot) or "offline · last seen 2 h ago"
(grey dot). The processing strip shows "Waiting for transcriber" for queued calls when the
worker is offline.

### A6. Uploads

Stream uploads to disk in chunks, never in memory. Max 500 MB per file (an hour-long WAV is
~600 MB at 16-bit stereo 44.1 kHz; if that's a problem, say so and propose a limit).

### A7. Packaging

- `Dockerfile`: multi-stage. Stage 1 Node builds `web/dist`. Stage 2 `python:3.12-slim` with
  `ffmpeg`, installs requirements **without** `mlx-whisper` (split requirements:
  `requirements-server.txt` / `requirements-mac.txt`, keep `requirements.txt` as the union for
  local dev). Runs uvicorn on 0.0.0.0:8000 inside the container as a non-root user.
- `deploy/docker-compose.yml` (project name `call-analyzer`): `app` (no published ports,
  volume `/srv/call-analyzer/data` → `/app/data`, env file `/srv/call-analyzer/.env`,
  `restart: unless-stopped`, healthcheck on `/api/health`) and `caddy` (`caddy:2`, ports
  80/443, volumes for Caddyfile, `caddy_data`, `caddy_config`, `restart: unless-stopped`).
- `deploy/Caddyfile`: `calls.tqc4.ai { reverse_proxy app:8000; request_body { max_size 500MB }
  encode gzip; header { Strict-Transport-Security "max-age=31536000"; X-Content-Type-Options
  nosniff; Referrer-Policy same-origin; X-Frame-Options DENY } }`.
- `GET /api/health` → `{ok: true}` without auth.
- `deploy/.env.example`: `ANTHROPIC_API_KEY`, `SCORER_MODEL`, `WORKER_MODE=remote`,
  `WORKER_TOKEN`, `SESSION_SECRET`, `PUBLIC_URL=https://calls.tqc4.ai`.
- `deploy/deploy.sh`: on the server, `git pull` then
  `docker compose -p call-analyzer -f deploy/docker-compose.yml up -d --build`, then waits for
  the healthcheck and prints status.
- `deploy/backup.sh`: SQLite online backup (`sqlite3 … ".backup"`) into
  `/srv/call-analyzer/backups/calls-YYYYMMDD.db`, keep 14; installed as a daily cron at 03:30 UTC.
  Audio is not in this backup (see Part B, step 9).

### A8. Tests

Worker API: claim atomicity (two claims, one job → one 204), lease expiry requeue, attempts
cap, idempotent transcript post, redaction applied on post (assert no SSN-shaped digits stored),
token required. Auth: login, lockout, protected route 401, worker routes reject session cookies
and accept only the token. Keep all existing tests passing.

## Part B — server runbook

Run these from the Mac terminal; each command block says where it runs. Claude Code may run them
over SSH **only after showing me each block and getting a yes**.

1. **Updates and reboot** (server shows "System restart required"):
   `apt update && apt upgrade -y`, then check `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' mongo-mongo-1`
   and tell me the result before rebooting (if it's `no`, that container won't come back after
   the reboot; I'll decide). Then `reboot`.
2. **Deploy user**: create `deploy`, add to the `docker` group, copy root's
   `authorized_keys`. Confirm `ssh deploy@5.161.161.241` works **before** step 3.
3. **SSH hardening**: `PasswordAuthentication no`, `PermitRootLogin prohibit-password`,
   reload sshd. Test a new SSH session before closing the current one.
4. **Unattended security upgrades**: install and enable `unattended-upgrades`.
5. **Code**: read-only GitHub deploy key for `MutahirAbbas/Transcriber` → clone into
   `/srv/call-analyzer/app`, checkout `release`.
6. **Secrets**: create `/srv/call-analyzer/.env` (mode 600) from `deploy/.env.example`.
   Generate `WORKER_TOKEN` and `SESSION_SECRET` with `openssl rand -hex 32`. Show me where to
   paste `ANTHROPIC_API_KEY`; don't echo secrets in the terminal.
7. **Start**: `deploy/deploy.sh`. Caddy fetches the certificate on first request.
   Check `curl -sI https://calls.tqc4.ai/api/health`.
8. **First user**: `docker compose -p call-analyzer exec app python -m api.users add <my email>`.
9. **Backups**: install the cron from A7. Tell me how much disk audio is using after a week; I
   may enable Hetzner server backups or add a Storage Box for audio.
10. **Mac worker**: on the Mac, create `worker.env` (`SERVER_URL=https://calls.tqc4.ai`,
    `WORKER_TOKEN=<same>`), run `scripts/install_worker_mac.sh`, check the log shows it polling.
    Also tell me how to stop the Mac sleeping on power (System Settings path), but don't change
    it yourself.

## Done when

1. `https://calls.tqc4.ai` shows the login page with a valid certificate; `/api/calls` without a
   session returns 401.
2. After logging in, uploading a call shows "Waiting for transcriber" if the worker is stopped,
   and starts transcribing within ~10 s of starting it.
3. The call completes end to end: Mac transcribes, server redacts and scores, detail page works.
4. Killing the Mac worker mid-job: within 15 min the call is back in the queue and completes
   when the worker restarts.
5. Rebooting the Mac: the worker comes back by itself.
6. Rebooting the server: app and Caddy come back by themselves; `mongo-mongo-1` is in whatever
   state I chose in B1.
7. `nc -vz 5.161.161.241 8000` and `… 27017` from the Mac both time out.
8. Local dev (`WORKER_MODE=local`, `AUTH_DISABLED=1`) still works as before, and all tests pass.
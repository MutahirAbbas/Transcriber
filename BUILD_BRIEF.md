# Build brief — Call Analyzer v1

Build the web interface for `call-bench`: upload call recordings, get a transcript and an
AI scorecard back, review them. Local tool for now (runs on one Mac). Integrations
(Vici watcher, Mongo, public deployment) come later — do not build them.

The design is approved. Match it. The two reference files are the source of truth for
layout, colours, spacing and copy:

- `design/calls-page.html` — Calls page (upload, processing queue, stats, table)
- `design/call-detail-page.html` — Call detail (score ring, audio, transcript with coachable moments, analysis column)

They are design-tool exports: the markup inside `<x-dc>` is the design; every value is an
inline style, so read exact hexes, sizes and gaps from there. Ignore `support.js`,
`<helmet>` and the `text/x-dc` script block. Sample agents and call content in them are
placeholders, not seed data.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11, FastAPI, uvicorn | the pipeline is Python; wraps `pipeline.process_call` |
| DB | SQLite via SQLModel | one file, zero setup; Mongo comes with the TQC integration later |
| Worker | one background thread + `queue.Queue` | Whisper uses the GPU; run one job at a time |
| Front end | Vite + React 18 + TypeScript + React Router + Tailwind | matches the author's TS stack |
| Data fetching | TanStack Query, polling | `refetchInterval: 2000` while any call is not `done`/`error` |
| Fonts | IBM Plex Sans + IBM Plex Mono via Google Fonts | as in the design |

Repo layout (add to the existing one, don't move what's there):

```
call-bench/
  api/            FastAPI app: main.py, db.py, models.py, worker.py, routes/
  web/            Vite app
  transcribers/ eval/ scoring/ pipeline.py   (existing — do not rewrite; small additive changes OK)
  data/calls/     uploaded audio lands here, named <call_id>.<ext>
```

Dev: `uvicorn api.main:app --reload --port 8000` and `cd web && npm run dev` (Vite proxies `/api` → 8000).

## Data model

`calls` table:

| column | type | notes |
|---|---|---|
| `id` | str PK | `<original stem>-<6 char random>`; stem sanitized to `[A-Za-z0-9_-]` |
| `filename` | str | original upload name |
| `agent` | str nullable | see Agent assignment |
| `uploaded_at` | datetime UTC | |
| `duration_s` | float nullable | from ffprobe at upload |
| `stereo` | bool nullable | from ffprobe at upload |
| `status` | enum | `queued` · `transcribing` · `scoring` · `done` · `error` |
| `progress` | float 0–1 | see Progress |
| `error` | str nullable | |
| `provider` / `model` | str | from the TranscriptResult |
| `transcript_json` | text nullable | `TranscriptResult.to_dict()` |
| `score_json` | text nullable | scorer output (shape in `scoring/rate_call.py`) |
| `scored_at` | datetime nullable | |

Derived on read, never stored: `overall_score`, `outcome`, `flag_count`, `weakest_category`.

`agents` table: `id`, `name`, `slug`. Created on first sight.

## API

All under `/api`. JSON unless noted. Errors: `{"detail": "..."}` with proper status codes.

| Method & path | Does |
|---|---|
| `POST /calls` | multipart: `files[]` (1–20 audio files), optional `agent` (agent id, or empty = detect from filename). Saves each to `data/calls/<id>.<ext>`, runs ffprobe for duration + channels, inserts row as `queued`, enqueues. Returns the created rows. Reject files > 2 h or non-audio with 400. |
| `GET /calls` | query: `filter` = `all` · `sales` · `flagged` · `below60`, `q` (search), `page`, `page_size` (default 25). Returns `{items, total}`. Each item = row summary + derived fields. Sorted `uploaded_at` desc. |
| `GET /calls/in-progress` | rows with status not in (`done`,`error`), for the processing strip. |
| `GET /calls/{id}` | full record incl. parsed transcript + score, plus `speaker_map` (see below). |
| `GET /calls/{id}/audio` | streams the file with correct content-type and `Accept-Ranges` so `<audio>` can seek. |
| `GET /calls/{id}/transcript.txt` | `[mm:ss] Agent: …` lines, speakers mapped. `Content-Disposition: attachment`. |
| `POST /calls/{id}/rescore` | re-runs the scorer only (keeps transcript). Sets status `scoring`, enqueues. 409 if no transcript yet. |
| `DELETE /calls/{id}` | removes row + audio file. |
| `GET /calls.csv` | table columns as CSV, same filters as `GET /calls`, no paging. |
| `GET /stats` | this week (Mon–Sun, server local time): `calls_scored`, `avg_score`, `compliance_flags`, `weakest_category`. |
| `GET /agents` | list. |
| `GET /settings` | `{provider, model}` of the active transcriber (from env `TRANSCRIBER`, default `whisper_local`; model name from the transcriber's `model`). |

**Search** (`q`): case-insensitive match on agent name, outcome, and transcript text
(SQL `LIKE` on the stored text is fine for v1).

**Filters**: `sales` → outcome = `sale`; `flagged` → `flag_count > 0`; `below60` → `overall_score < 60`.

## Worker

`api/worker.py`: a daemon thread started on app startup, draining a `queue.Queue` of
`(call_id, mode)` where mode is `full` or `rescore`. One job at a time. For `full` it calls
`pipeline.process_call(audio, provider=settings.provider, stereo=row.stereo, call_id=row.id,
on_status=cb, scorer=shared_scorer, out_dir=None)` and writes transcript + score to the row.
For `rescore` it rebuilds a `TranscriptResult` from `transcript_json` and calls
`shared_scorer.score(res)`.

Create one `CallScorer()` at startup and reuse it. Re-queue rows found in a non-terminal
status at startup (crash recovery). Any exception → `status = error`, `error = str(exc)`,
log the traceback, continue with the next job.

**Progress** (`progress` column, drives the bar): Whisper gives no progress callback, so
estimate. On `transcribing`: `min(0.9, elapsed / (duration_s * RTF))` with `RTF = 0.15`
for local Whisper (update it to the measured value after the first few calls: store the
last 10 ratios and use their mean). On `scoring`: 0.95. On `done`: 1.0. Update the row
every 2 s from the worker thread.

## Agent assignment

Upload form `agent` set → that agent. Empty → parse the filename: the last `_` separated
token before the extension (`20261021-141502_jblake.wav` → `jblake`), slug it, find or
create the agent with `name = slug.title()`. Nothing parseable → `agent = null`, table
shows "—".

## Mapping scorer JSON → UI

The scorer returns (see `scoring/rate_call.py`): `agent_speaker`, `call_outcome`, `scores{cat: {score, evidence}}`,
`overall_score`, `strengths[]`, `improvements[]`, `coachable_moments[{timestamp, what_happened, better_approach}]`,
`compliance{required_phrases_present, missing[], red_flags[{timestamp, flag}]}`, `summary`.

- **Speakers**: `speaker_map = {score.agent_speaker: "Agent", <every other label>: "Customer"}`. Before scoring, show raw labels.
- **Outcome badge**: `sale` → green badge "Sale"; `callback` → "Callback"; `no_sale` → "No sale"; `not_interested` → "Not interested"; `wrong_number` → "Wrong number"; `unclear` → "Unclear". All non-sale use the grey badge from the design.
- **Score colours** (bars and ring): per-category /10: ≥ 7 green `#1B7F5A`, 5–6 amber `#B45309`, ≤ 4 red `#B42318`. Overall /100: ≥ 75 green, 60–74 amber, < 60 red.
- **Ring**: SVG circle r = 36, circumference ≈ 226; `stroke-dashoffset = 226 × (1 − score/100)`.
- **Weakest**: category with the lowest score; ties → first in scorecard order.
- **Flags**: `len(compliance.red_flags) + len(compliance.missing)`. Show "— " when 0, else the red "N flags" chip.
- **Coachable moments inline**: parse `timestamp` `mm:ss` → seconds; attach the moment to the last segment whose `start ≤ t`; render that turn with the highlighted card from the design (segment text, divider, "Coachable moment · <category>", `better_approach`). Category: match `what_happened` against scorecard category names if present, else omit the suffix. Moments that match no segment go in a small "Other moments" list under the transcript.
- **Compliance panel**: one line per entry in the scorecard's `required_phrases` — tick if present (find the first segment containing it, show its timestamp), red cross + "missing" otherwise. Then one red line per `red_flags` entry with its timestamp. The two green generic lines in the design ("Carrier named correctly", "No pressure or fear language") are placeholders — only show lines backed by data.
- **Category evidence**: the small grey line under each bar is `scores[cat].evidence`.
- **Strengths / Work on next**: `strengths[]`, `improvements[]`.
- **Header summary**: `summary`. Footer line: "Scored by <scorer_model> · transcribed with <provider>/<model> · <rtf>× real time" from `score._meta` and the transcript's `latency_s / audio_duration_s`.

## Front end

Routes: `/` Calls page, `/calls/:id` Call detail. Sidebar is shared (Calls active; Agents,
Scorecard, Settings are non-functional stubs — render them disabled with no route). Sidebar
footer shows `GET /settings` as in the design.

**Calls page**
- Upload zone: drag-and-drop + click to open file picker (`accept="audio/*"`, multiple). Agent select populated from `/agents` with "Detect from filename" first. On drop/select, POST immediately, show the new rows in the processing strip.
- Processing strip: `GET /calls/in-progress` polled every 2 s; hidden when empty. Card = filename (mono, ellipsis), duration, bar (`progress`), stage label: `Queued` / `Transcribing · 70%` / `Scoring with AI…` / `Error: …` (red).
- Stats row from `/stats`. "Compliance flags" number red only when > 0.
- Table: filter pills (`All`, `Sales`, `Flagged`, `Below 60`), search box (debounced 300 ms), columns exactly as designed, "Open" → `/calls/:id`, "Showing X of Y", Prev/Next. A row whose status isn't `done` shows a grey "Processing" text in the Score column. "Export CSV" button downloads `/calls.csv` with current filters.

**Call detail page**
- "All calls" back link. Header: ring, agent, outcome badge, mono `id · date · duration`, summary paragraph, `<audio controls src="/api/calls/:id/audio">`, "Download transcript" (link to the .txt), "Re-score" (POST, then poll until `done`, button shows "Scoring…" meanwhile).
- Transcript: every turn (no truncation / "show more" in v1). Clicking a timestamp seeks the audio element to that time and plays. Legend as designed.
- Analysis column: Scorecard, Compliance, Strengths, Work on next, footer line.
- While status isn't `done`: show the header with "Transcribing…"/"Scoring…" in place of the ring and a skeleton in both columns, poll.

**Responsive**: the design is a page; at phone width the sidebar stacks above content, the
stats grid goes 2-up, the table scrolls horizontally, the two detail columns stack. No
horizontal page scroll. Buttons ≥ 44 px tall.

**Accessibility**: real `<button>`/`<a>`/`<input>`+`<label>`; icon-only buttons get `aria-label`;
decorative SVGs `aria-hidden`.

## Tokens (from the design)

```
ground   #F3F4F6     surface #FFFFFF     border #E3E5EA  / subtle #EEF0F4
ink      #15171A     text-2  #374151     muted  #6B7280  / #4B5563 nav
accent   #2F4BC9 (links, agent label, progress bar)   hover #1F37A3
green    #1B7F5A     amber   #B45309     red    #B42318
sale badge  bg #E3F3EC text #166A48       grey badge  bg #EEF0F4 text #374151
moment card bg #FDF3E7 border #F0C9A0 title #92400E body #78350F
radius   12 cards · 10 small cards · 8 buttons/inputs · 999 pills
type     body 14/1.5 · h1 26 · page h1 on detail 20 · section h2 15/600 · table head 12 uppercase 0.05em
```

Put these in `tailwind.config` as named colours; don't scatter hexes.

## Not in v1

Auth, multi-user, Vici/Mongo integration, public deployment, agent/scorecard/settings
pages, editing the scorecard from the UI, provider switching from the UI, real-time
progress from Whisper, transcript editing, diarization for mono audio with local Whisper
(the pipeline already handles stereo; mono + whisper_local produces unlabelled turns — show
them as "Speaker 1/2" and let scoring decide who the agent is).

## Done when

1. `uvicorn` + `npm run dev` up; dropping two WAVs on the Calls page shows both in the processing strip, bars move, and both land in the table with scores within a few minutes on an M4 Pro.
2. Opening a call shows the transcript with speaker labels mapped to Agent/Customer, coachable moments highlighted at the right turns, audio seeks on timestamp click.
3. Filters, search, paging, CSV export, re-score and delete all work against the DB.
4. Killing the server mid-job and restarting resumes the queued/in-progress calls.
5. A deliberately broken file (e.g. a renamed .txt) ends in `error` with a message in the strip, and the next file still processes.
6. Page is usable at 390 px wide.
7. `pipeline.py`, `transcribers/`, `scoring/`, `eval/` still work standalone (`python -m eval.run_benchmark` unchanged).

# Brief — Call Analyzer v2, phase 1: Calls and Call detail

Rebuild the two existing pages to the approved v2 design, and add what the design needs
underneath. Agents, Agent detail, Scorecard, Settings, dialogs and reports are **phase 2**:
do not build them now.

**Design (source of truth):** `design/v2/calls.png` and `design/v2/call-detail.png`
(2× screenshots of a 1440 px layout). Match layout, spacing, type and colour. The data in
them is sample data, not seed data.

## 0. Before you start

- `deploy` is what runs on the server. Merge `deploy` into `release` (it has passed its
  checks), then branch `v2` from `release`. All work happens on `v2`.
- **Do not deploy `v2` to the server** until I say. Test locally (`WORKER_MODE=local`,
  `AUTH_DISABLED=1`).
- Same rules as before: wrap `pipeline.process_call`, don't fork it; nothing outside
  `transcribers/` imports a provider SDK; never log transcript text; commit per section below.

## A. Speaker separation (Mac worker)

Vici `-all` recordings are mono, so today's transcripts have no speakers. The v2 transcript
shows names ("Josh", "Gary"), so this comes first.

- New `transcribers/diarize.py`:
  - `diarize(audio_path) -> list[(start, end, speaker)]` with the current `pyannote.audio`
    speaker-diarization pipeline, `num_speakers=2`, MPS when available, else CPU.
    Token from env `HF_TOKEN`. Load the pipeline once per worker process, not per call.
  - `assign_speakers(segments, turns) -> segments`: each word takes the speaker whose turn
    covers its midpoint (nearest turn if none); rebuild segments as runs of same-speaker
    words, so a segment that spans two speakers is split.
- `whisper_local` requests word timestamps and keeps them on `Segment.words`
  (`[{start, end, text}]`, default empty).
- Pipeline order: transcribe → (no speakers and `DIARIZER=pyannote`) diarize + assign →
  redact → score. Stereo files keep the existing channel split. Deepgram/AssemblyAI already
  return speakers and skip this.
- Missing `HF_TOKEN` or model load failure: one clear warning line listing the Hugging Face
  model pages whose terms must be accepted, then continue unlabelled. Never fail a call over
  diarization.
- New stage `separating` between `transcribing` and `scoring`; measure its real-time factor
  like transcription and include it in the time-left estimate.
- Store `speaker_source` on the call: `stereo` | `diarized` | `provider` | `none`.

## B. Waveform data (Mac worker)

The player draws the call's waveform. The worker computes peaks with ffmpeg (mono, 8 kHz,
then max-abs per bin) into **400 bins**, normalised 0–1, and posts them with the transcript.
Store as `waveform` (JSON array). The UI renders them as the thin vertical bars in the design.

## C. Scorecard v2 file

`scoring/scorecard.yaml` gains `version: 2` and three sections. The editor UI for these is
phase 2; for now they live in the file.

```yaml
target_score: 60
categories:            # as now: key, label, weight, description. Weights sum to 100.
flag_types:            # the ONLY flags the scorer may raise
  - {id: no_recording_disclosure,   label: "No recording disclosure",              severity: critical}
  - {id: no_licensed_statement,     label: "No licensed-agent statement",          severity: critical}
  - {id: approval_before_uw,        label: "Approval promised before underwriting", severity: critical}
  - {id: agent_handled_esign,       label: "Agent handled the customer's e-signature or code", severity: critical}
  - {id: ignored_end_request,       label: "Continued after a request to end the call", severity: critical}
  - {id: carrier_after_payment,     label: "Carrier named after payment data",     severity: major}
  - {id: health_answer_prejudged,   label: "Health answer pre-judged or misrecorded", severity: major}
  - {id: pressure_tactics,          label: "Fear, shame or pressure tactics",      severity: major}
required_phrases:      # each maps to a flag type raised when it is missing
  - {phrase: "recorded line",  flag: no_recording_disclosure, before_personal_data: true}
  - {phrase: "licensed agent", flag: no_licensed_statement,   before_personal_data: true}
phases:                # order of the phase bar; each maps to the category its label shows
  - {id: opening,     label: "Opening",     category: opening}
  - {id: discovery,   label: "Discovery",   category: discovery}
  - {id: value,       label: "Value",       category: value_presentation}
  - {id: application, label: "Application", category: compliance}
  - {id: objections,  label: "Objections",  category: objection_handling}
  - {id: close,       label: "Close",       category: close}
compliance_cap: {if_any: critical, max_score: 3}
```

## D. Scorer v2 output

`CallScorer` returns this shape (validate it; a call whose output fails validation goes to
`error` with the reason, and Re-score retries). `temperature=0`. `_meta.scorer_version` =
`"<scorecard version>.<first 8 hex of sha256(system prompt)>"`.

```json
{
  "agent_speaker": "SPEAKER_0 | null",
  "agent_name": "Josh | null",
  "customer": {"first_name": "Gary", "last_name": "Jones", "age": 75, "state": "Maryland"},
  "call_outcome": "sale | callback | no_sale | not_interested | wrong_number | unclear",
  "sale": {"coverage_usd": 11000, "premium_monthly_usd": 113, "carrier": "Aflac", "plan_type": "level | graded | guaranteed_issue | unknown"},
  "summary": "2–3 sentences, the paragraph under the header",
  "scores": {"<category>": {"score": 1, "evidence": "…"}},
  "phases": [{"phase": "opening", "start": "00:00"}],
  "findings": [
    {"kind": "flag",  "flag_type": "no_recording_disclosure", "category": "compliance", "timestamp": "00:00", "text": "…"},
    {"kind": "coach", "category": "discovery", "timestamp": "00:35", "text": "what happened", "better": "what to do instead"},
    {"kind": "good",  "category": "empathy",   "timestamp": "02:35", "text": "…"}
  ],
  "required_phrases": [{"phrase": "recorded line", "present": false, "timestamp": null}],
  "work_on_next": {"intro": "one sentence", "items": ["max 6, one sentence each"]},
  "_meta": {}
}
```

Rules, enforced in code after the model answers:
- `flag_type` must be one of `flag_types`; drop anything else and log the count.
- Missing required phrase ⇒ its flag exists (add it if the model forgot).
- `overall_score` is **computed in code** from category scores and weights, never taken from
  the model. Then apply `compliance_cap`: any critical flag caps the Compliance score at 3
  before weighting.
- `customer`, `sale` fields may be null; never invent. `sale` is null unless outcome is `sale`.
- Phases must be in time order; a phase can appear more than once (objections often do).
- Length limits as in the current prompt (≤ 2-sentence evidence, ≤ 8 coach findings,
  whole response < 1,800 words). `max_tokens` 16000, read the first `text` block only.

## E. Data and API changes

Calls table, new columns: `speaker_source`, `waveform` (JSON), `findings` (JSON),
`phases` (JSON), `customer` (JSON), `sale` (JSON), `scorer_version`, `score_history`
(JSON list; a re-score appends the previous score before overwriting), `cancelled_at`.
`overall_score` stays a column (computed). Agents table gains `aliases` (JSON list) and is
the source for the assign dropdown.

API:
- `GET /api/calls` items gain `stage` (`queued`, `waiting`, `transcribing`, `separating`,
  `scoring`, `error`, `done`), `progress`, `eta_s`, `flag_count`, `critical_count`,
  `weakest_category`, `agent` (id, name) or `suggested_agent` (scorer's `agent_name` when
  unassigned). Filters: `all | sales | flagged | below_target | unassigned`.
- `GET /api/stats`: add `in_progress` count and `target_score`.
- `GET /api/calls/{id}`: everything above plus `speakers` (label → display name: agent's
  name, customer's first name, else "Agent"/"Customer"/"Speaker 1"), `prev_id`, `next_id`
  (by `uploaded_at` desc, all calls), `agent_average` (that agent's average score, last 30 days).
- `POST /api/calls/{id}/cancel`: queued → removed from the queue and deleted; in progress →
  marked cancelled; the worker sees it on its next heartbeat (heartbeat response gains
  `{cancel: true}`), stops, deletes its temp file. Done calls can't be cancelled (409).
- `PATCH /api/calls/{id}` `{agent_id}`: assign; 404 for unknown agent.
- Worker transcript post accepts `waveform`, `speaker_source`, and words on segments.

## F. Calls page (`design/v2/calls.png`)

- Shell: new sidebar (logo, Calls, Agents, Scorecard, Settings; Agents/Scorecard/Settings
  render but are disabled with "Coming soon" tooltip in phase 1). Footer: Transcriber
  (status dot, worker name, model), **Scoring: "Claude · <model>"** (no Change link in
  phase 1), user email, Sign out.
- Header text and buttons as designed. "Export CSV" uses current filter.
- Upload zone: "Drop one recording or a whole folder". Accept drops of files and folders
  (walk directory entries), and the button opens a picker that allows folders
  (`webkitdirectory`) plus a normal multi-file picker. Keep only .wav/.mp3/.m4a; upload one
  file per request, sequentially; show a count ("12 of 40 uploaded") while it runs.
  "Assign to agent" select: "Detect from filename" + agents.
- Stats row: four tiles as designed. "Below the 60 target" in rust when the average is under
  target; "1 still transcribing" uses `in_progress`.
- Table: filter pills incl. **Unassigned**, search box, columns as designed.
  - In-progress calls are **rows in the table** (no separate strip): blue progress bar and
    "Transcribing · 42% · about 30 s left" / "Separating speakers…" / "Scoring with AI…" /
    "Waiting for transcriber" (worker offline) / "Error: …" in rust; right side **Cancel**.
  - Unassigned rows show a dotted-underline **Assign agent** that opens an inline dropdown
    (agents list; scorer's suggested name first, labelled "heard in call"). The full dialog
    is phase 2.
  - Flags column: warning icon + count in rust; "—" when zero.
  - Score bar: rust when under the target, ink otherwise, as in the screenshot.
- Poll every 2 s while anything is in progress; otherwise every 30 s.

## G. Call detail page (`design/v2/call-detail.png`)

Top bar: "All calls" back link; right side: call id (mono), date, length, **"stereo" or
"mono" as the file actually is**, Previous / Next.

Left column:
- Large overall score, rust when under target. Under it, when any critical flag:
  "⚠ Review for compliance"; line: "N flags · N coachable moments · above/below <agent>'s
  <avg> average".
- Buttons, changed from the screenshot per my decisions: primary **"Copy feedback for
  <agent>"** (copies agent name, date, score, summary and Work-on-next as plain text, **no
  customer name/age/state, no sale amounts**; toast "Copied"), secondary **Re-score**. The
  "Report" button is hidden until phase 2.

Right of the score:
- "<Agent> / <Customer first last>, <age>, <state>" (omit any null part) and the outcome
  pill: "Sale · $11,000 · $113/mo" when sale, else the outcome badge.
- `summary` paragraph. Six category tiles (score/10, small bar; the Compliance tile gets the
  light rust background when it's the weakest).

Player:
- Play/pause, current / total time, waveform bars from `waveform`, **markers**: rust for
  flags, blue for coach findings, at their timestamps. Under it the **phase bar**: one
  segment per phase occurrence, width ∝ duration, label "<Phase> <category score>", fill
  from light to deeper rust as the score drops (≥ 7 neutral grey). Very short segments show
  the score only (like "6" in the design). Legend line as designed. Click on waveform,
  marker or phase seeks. Speed select 0.75× / 1× / 1.25× / 1.5× / 2×.

Findings panel: header "Findings N" with pills **All / Flags N / Coach N / Good N**; each
item: timestamp (mono), kind tag (FLAG rust / COACH blue / GOOD grey), category label,
text, and for coach "**Better:** …". Click seeks audio and scrolls the transcript. Scrolls
inside its card; same height as the transcript card.

Transcript panel: "Find a phrase" (highlights matches, Enter jumps to next), **Follow
audio** toggle (highlights the turn playing and keeps it in view), **Download** (.txt with
display names). Phase dividers ("OPENING ———— 00:00 – 01:30"). Turns: timestamp, speaker
display name, text; customer turns indented as in the design. Turns with a coach finding get
the light blue background and "· coachable"; with a flag, a rust "· flag". Clicking a
timestamp seeks and plays.

"Work on next" band (dark): `work_on_next.intro` left, numbered items in a 3-column grid.
Button: **Re-score** only (the "Send to Josh" there becomes the same Copy feedback action).

Footer line: "Scored by Claude <model> · scorecard v<n> · transcribed with <model> on
<worker> · speakers <speaker_source> · <rtf>× real time · Delete call".

Loading/processing: while not `done`, show the top bar and header with the stage in place of
the score, skeletons for the rest, poll.

Responsive: at phone width the left column stacks above, tiles go 3-up then 2-up, findings
come before the transcript, the phase bar scrolls horizontally. No horizontal page scroll.

## H. Tokens

Match the screenshots. Starting point (adjust to the screenshot if they disagree):
ground `#F2F1EE`, card `#FFFFFF`, border `#E5E3DE`, divider `#ECEAE5`, ink `#18181B`,
text-2 `#3F3F46`, muted `#6B6B70`, link/blue `#2448C9`, rust `#A84A0A`, rust tint
`#FBEFE6`, blue tint `#E9EDFB`, grey pill `#EDECE8`, dark band `#18181B`. Sans: match the
screenshot's grotesque (Inter is acceptable); mono for ids, timestamps and numbers in tables.
Replace the v1 token set; no raw hexes in components.

## I. Re-scoring existing calls

`scripts/upgrade_calls_v2.py`:
- Calls with audio and no speakers (`speaker_source` none/null): requeue for re-transcription
  (the worker downloads the audio as usual), which also produces the waveform.
- Every call: re-score with scorecard v2 after its transcript is current.
- `--dry-run` prints counts and an estimated number of Claude calls; I approve before the
  real run. Keeps the old score in `score_history`.

## J. Tests

- `assign_speakers` (straddling word, gap with no turn); diarization-off path.
- Scorer output validation: bad flag type dropped, missing phrase adds its flag, overall
  computed in code, compliance cap applied, null customer/sale.
- Cancel: queued and in-progress (heartbeat returns cancel), done → 409.
- Assign: PATCH, unknown agent 404, unassigned filter.
- Folder upload: non-audio files skipped (front-end unit test).
- All existing tests still pass.

## Done when

1. Uploading the hour-long mono call gives labelled turns ("Josh" / customer's first name),
   `speaker_source = diarized`, a waveform, phases, and findings of all three kinds.
2. The Call detail page matches `call-detail.png`: markers and phase segments seek the audio,
   Follow audio tracks playback, findings filters work, Find a phrase works, Download works,
   Previous/Next work, "mono" shows for mono files.
3. Copy feedback copies text with no customer name, age, state or sale amount.
4. The Calls page matches `calls.png`: folder drop uploads only audio, in-progress rows show
   stage and time left, Cancel works in both states, Unassigned filter and inline Assign work.
5. `upgrade_calls_v2.py --dry-run` reports correctly; after the real run old calls show v2
   detail pages.
6. Phone width (390 px): both pages usable, no sideways scroll.
7. All tests pass; nothing deployed to the server.

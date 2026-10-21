# call-bench

Call transcription + AI scoring tool, with a benchmark harness for transcription providers.

- **What to build next:** `BUILD_BRIEF.md` — the v1 web interface. Read it fully before touching anything.
- **Design reference:** `design/*.html` — approved; match it, don't redesign.
- **Pipeline:** `pipeline.process_call()` is the single entry point for audio → transcript → score. The web backend wraps it; it does not duplicate it.
- **Provider-blind:** nothing outside `transcribers/` may import a provider SDK. The scorer, metrics and UI only see `TranscriptResult`.

## Conventions

- Python 3.11, type hints, `from __future__ import annotations`. No new global state; pass things in.
- Front end: TypeScript strict, Tailwind with named colours from the brief's token table — no raw hexes in components.
- Keep `eval/` and `scoring/` runnable from the CLI without the web app.
- Don't commit anything under `data/calls`, `data/references`, `results/`, `.env`, or the SQLite file.
- Secrets come from env (`.env.example` lists them). Never log transcript text at INFO level — it's customer audio.
- Small, reviewable commits: backend skeleton → worker → Calls page → Call detail → polish.

## Running

```bash
brew install ffmpeg
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --reload --port 8000     # once api/ exists
cd web && npm install && npm run dev            # once web/ exists
```

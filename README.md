# call-bench

Transcribe sales-call recordings, score them with Claude against a scorecard, and
benchmark transcription providers (Deepgram, AssemblyAI, local Whisper) on real calls.

Provider-blind by design: every transcriber returns the same `TranscriptResult`,
the scorer and metrics never know who produced it. Swap providers by name.

```
transcribers/   adapters → TranscriptResult          (base, deepgram, assemblyai, whisper_local)
eval/           metrics (WER, keyword recall) + benchmark runner
scoring/        Claude scorer + scorecard.yaml
pipeline.py     one call in → transcript + score out  (what TQC / the public tool calls)
data/calls/     put audio here                        (gitignored)
data/references/ <call_id>.txt human-corrected transcripts
data/keywords.txt domain terms (boosting + keyword-recall metric)
results/        transcripts + benchmark CSVs
```

## Setup (M4 Pro)

```bash
brew install ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill keys; whisper_local needs none
export $(grep -v '^#' .env | xargs)
```

First run of `whisper_local` downloads `whisper-large-v3-turbo` (~1.6 GB) from Hugging Face.

## Step 1 — check if Vici records stereo

```bash
ffprobe -v error -show_entries stream=channels -of csv=p=0 data/calls/<any>.wav
```

`2` → agent and customer are on separate channels. Pass `--stereo` everywhere and
diarization is free and exact. `1` → mono, rely on provider diarization (Whisper
local won't split speakers until pyannote is added).

## Step 2 — smoke test on one call

```bash
python pipeline.py data/calls/12345.wav --provider whisper_local --stereo
```

Prints the score JSON. Check it's sane before doing anything else.

## Step 3 — build references

Pick 20–30 representative calls (good, bad, short, long, different agents).
Run whisper_local over them, copy each transcript's text to
`data/references/<call_id>.txt`, and have someone who knows the product fix
every error. This is the ground truth. Without it, WER is meaningless.

## Step 4 — benchmark

```bash
python -m eval.run_benchmark --providers whisper_local deepgram assemblyai --stereo
python -m eval.run_benchmark --providers whisper_local deepgram assemblyai --stereo --score   # + Claude scores
```

Output: `results/benchmark_<ts>.csv` and a summary table:

| column | meaning |
|---|---|
| `wer` | word error rate vs reference (lower is better) |
| `keyword_recall` | fraction of domain terms in the reference that the provider got |
| `keyword_misses` | which ones it dropped, and how often |
| `rtf` | real-time factor — latency ÷ audio length |
| `cost_usd` | estimated from `price_per_hour_usd` in each adapter (fill these in) |
| `overall_score` | Claude's score — compare across providers for the same call: if it moves a lot, transcription quality matters downstream |
| `speaker_turns` | detected speaker changes — sanity check diarization |

## Before trusting the numbers

- Fill `price_per_hour_usd` in each adapter from the current pricing pages.
- Compare each provider's **best** config, not defaults (model name, keyword boosting on).
- Replace `data/keywords.txt` and `scoring/scorecard.yaml` with TQC's real terms and Leo's real scorecard.
- Run the benchmark twice; Claude scores have some variance. Set `temperature=0` in the scorer if you want them deterministic.

## Web interface (v1)

Design approved; build spec in `BUILD_BRIEF.md`, reference screens in `design/`.
FastAPI backend wrapping `pipeline.process_call`, Vite + React front end.

## Roadmap

- [x] Design the upload → transcript → analysis interface
- [ ] Build it (`BUILD_BRIEF.md`)
- [ ] pyannote diarization for mono audio in `whisper_local`
- [ ] real DER via `pyannote.metrics` against time-aligned references
- [ ] Vici folder watcher → `pipeline.process_call` → Mongo
- [ ] Command Center page: call list, scores, transcript viewer with coachable moments
- [ ] Public tool: upload → transcript free, rating gated/limited

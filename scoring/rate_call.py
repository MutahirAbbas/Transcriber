"""Claude call scorer. Takes a TranscriptResult, returns structured JSON scores.

Env: ANTHROPIC_API_KEY, SCORER_MODEL (default claude-sonnet-5-5)
The scorer is provider-blind — it only sees speaker-labelled text with timestamps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic
import yaml

from transcribers.base import TranscriptResult

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get("SCORER_MODEL", "claude-sonnet-5-5")
MAX_OUTPUT_TOKENS = 16000


def _fmt_ts(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m:02d}:{sec:02d}"


def transcript_to_text(res: TranscriptResult) -> str:
    lines = []
    for seg in res.segments:
        spk = seg.speaker or "UNKNOWN"
        lines.append(f"[{_fmt_ts(seg.start)}] {spk}: {seg.text}")
    return "\n".join(lines)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    # tolerate any preamble/postamble around the object
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1] if start != -1 and end > start else raw


class CallScorer:
    def __init__(self, scorecard_path: Path = HERE / "scorecard.yaml", model: str = DEFAULT_MODEL):
        self.card = yaml.safe_load(scorecard_path.read_text())
        self.model = model
        self.client = anthropic.Anthropic()
        self.system = self._build_system()

    def _build_system(self) -> str:
        cats = "\n".join(
            f"- {name} (weight {c['weight']}): {c['description'].strip()}"
            for name, c in self.card["categories"].items()
        )
        flags = "\n".join(f"- {f}" for f in self.card.get("red_flags", []))
        req = ", ".join(self.card.get("required_phrases", []))
        return f"""You are a QA coach for a final-expense life insurance call center. You score
recorded sales calls against a fixed scorecard and produce coaching notes a team lead
can act on. Be specific, cite timestamps, and don't inflate scores.

Speakers may be labelled SPEAKER_0 / SPEAKER_1, or UNKNOWN when the audio was not
diarized. Work out from context who the agent is.

SCORECARD (score each category 1–10):
{cats}

REQUIRED PHRASES (flag if absent): {req}

RED FLAGS (flag any occurrence with timestamp):
{flags}

LENGTH LIMITS — these apply no matter how long the call is:
- each `evidence`: at most 2 sentences, citing 1–3 timestamps
- `strengths`: at most 5 items; `improvements`: at most 6 items; one sentence each
- `coachable_moments`: the 6 most consequential at most; `what_happened` and
  `better_approach` one or two sentences each
- `summary`: 2–3 sentences
- the whole response must stay under 1,500 words

Respond with ONLY a JSON object of this shape, no prose before or after:
{{
  "agent_speaker": "<a speaker label exactly as it appears in the transcript, or null if speakers are unlabelled>",
  "agent_name": "<the agent's name if they say it, else null>",
  "call_outcome": "sale" | "callback" | "no_sale" | "not_interested" | "wrong_number" | "unclear",
  "scores": {{ "<category>": {{ "score": 1-10, "evidence": "..." }} }},
  "overall_score": 0-100,
  "strengths": ["..."],
  "improvements": ["..."],
  "coachable_moments": [ {{ "timestamp": "mm:ss", "what_happened": "...", "better_approach": "..." }} ],
  "compliance": {{ "required_phrases_present": true|false, "missing": [], "red_flags": [ {{ "timestamp": "mm:ss", "flag": "..." }} ] }},
  "summary": "..."
}}
overall_score = sum over categories of (score/10 * weight)."""

    def score(self, res: TranscriptResult) -> dict:
        text = transcript_to_text(res)
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=self.system,
            messages=[{"role": "user", "content": f"Call ID: {res.call_id}\nDuration: {_fmt_ts(res.audio_duration_s)}\n\nTRANSCRIPT:\n{text}"}],
        )
        raw = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
        meta = {
            "scorer_model": self.model,
            "transcriber": f"{res.provider}/{res.model}",
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "stop_reason": msg.stop_reason,
        }
        if msg.stop_reason == "max_tokens":
            return {"parse_error": True, "reason": "output truncated at max_tokens", "raw": raw, "_meta": meta}
        try:
            data = json.loads(_extract_json(raw))
        except json.JSONDecodeError as e:
            data = {"parse_error": True, "reason": f"invalid JSON: {e}", "raw": raw}
        data["_meta"] = meta
        return data


if __name__ == "__main__":
    import sys
    from transcribers.base import Segment
    p = Path(sys.argv[1])
    d = json.loads(p.read_text())
    res = TranscriptResult(
        provider=d["provider"], model=d["model"], call_id=d["call_id"],
        segments=[Segment(**s) for s in d["segments"]],
        latency_s=d["latency_s"], audio_duration_s=d["audio_duration_s"],
    )
    print(json.dumps(CallScorer().score(res), indent=2))
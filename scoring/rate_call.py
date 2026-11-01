"""Claude call scorer. Takes a TranscriptResult, returns structured JSON scores.

Env: ANTHROPIC_API_KEY, SCORER_MODEL (default claude-sonnet-5-5)
The scorer is provider-blind — it only sees speaker-labelled text with timestamps.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import anthropic

from scoring.output import MAX_COACH, MAX_WORK_ITEMS, ScoreInvalid, normalize_score
from scoring.scorecard import load_card
from transcribers.base import TranscriptResult

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get("SCORER_MODEL", "claude-sonnet-5-5")
MAX_OUTPUT_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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


def _served_text(content: list) -> str:
    """The first text block of the model that finished the turn. After a mid-answer fallback the
    declining model's partial text sits before a `fallback` block and must not be read."""
    last_fallback = max((i for i, b in enumerate(content) if getattr(b, "type", "") == "fallback"), default=-1)
    return next((b.text for b in content[last_fallback + 1:] if getattr(b, "type", "") == "text"), "")


class CallScorer:
    def __init__(self, scorecard_path: Path = HERE / "scorecard.yaml", model: str = DEFAULT_MODEL):
        self.card = load_card(scorecard_path)
        self.model = model
        self.client = anthropic.Anthropic()
        self.system = self._build_system()
        digest = hashlib.sha256(self.system.encode()).hexdigest()[:8]
        self.version = f"{self.card['version']}.{digest}"

    def _build_system(self) -> str:
        card = self.card
        cats = "\n".join(
            f"- {key} (weight {c['weight']}): {c['description'].strip()}"
            for key, c in card["categories"].items()
        )
        flags = "\n".join(f"- {f['id']} ({f['severity']}): {f['label']}" for f in card["flag_types"])
        phrases = "\n".join(
            f'- "{r["phrase"]}" (missing raises {r["flag"]})'
            + ("; only counts if said before any personal data is collected" if r.get("before_personal_data") else "")
            for r in card["required_phrases"]
        )
        phases = "\n".join(f"- {p['id']}: {p['label']}" for p in card["phases"])
        return f"""You are a QA coach for a final-expense life insurance call center. You score
recorded sales calls against a fixed scorecard and produce coaching notes a team lead
can act on. Be specific, cite timestamps, and don't inflate scores.

Speakers may be labelled SPEAKER_0 / SPEAKER_1, or UNKNOWN when the audio was not
diarized. Work out from context who the agent is. Tokens like [SSN] or [PHONE] are our own
redaction of the transcript, not something said on the call.

SCORECARD (score each category 1-10, whole numbers):
{cats}

FLAGS. Raise a `flag` finding for each occurrence, using only these ids:
{flags}

REQUIRED PHRASES. Report each one exactly as written, with `present` and the timestamp it was
first said (null if absent). Wording close in meaning counts as present:
{phrases}

PHASES. Mark where each phase of the call starts, in time order. A phase can appear more than
once (objections often do). Only mark phases that actually happened:
{phases}

FINDINGS. Three kinds, each with the `category` it belongs to:
- "flag": a compliance problem from the list above
- "coach": a moment to coach; `text` is what happened, `better` is what to do instead
- "good": something worth repeating

NEVER INVENT. `customer` and `sale` fields are null unless they were said on the call. `sale` is
null unless the outcome is "sale". `agent_name` is null unless the agent says it.

LENGTH LIMITS. These apply no matter how long the call is:
- each `evidence`: at most 2 sentences, citing 1-3 timestamps
- "coach" findings: the {MAX_COACH} most consequential at most; "good" findings: at most 5
- each finding's `text` and `better`: one or two sentences
- `summary`: 2-3 sentences
- `work_on_next`: `intro` is one sentence; at most {MAX_WORK_ITEMS} `items`, one sentence each
- the whole response must stay under 1,800 words

Timestamps are "mm:ss", with minutes going past 59 on long calls. Respond with ONLY a JSON
object of this shape, no prose before or after:
{{
  "agent_speaker": "<a speaker label exactly as it appears in the transcript, or null if speakers are unlabelled>",
  "agent_name": "<first name> or null",
  "customer": {{ "first_name": "...", "last_name": "...", "age": 75, "state": "..." }},
  "call_outcome": "sale" | "callback" | "no_sale" | "not_interested" | "wrong_number" | "unclear",
  "sale": {{ "coverage_usd": 11000, "premium_monthly_usd": 113, "carrier": "...", "plan_type": "level" | "graded" | "guaranteed_issue" | "unknown" }},
  "summary": "...",
  "scores": {{ "<category>": {{ "score": 1-10, "evidence": "..." }} }},
  "phases": [ {{ "phase": "<phase id>", "start": "mm:ss" }} ],
  "findings": [
    {{ "kind": "flag", "flag_type": "<flag id>", "category": "compliance", "timestamp": "mm:ss", "text": "..." }},
    {{ "kind": "coach", "category": "<category>", "timestamp": "mm:ss", "text": "...", "better": "..." }},
    {{ "kind": "good", "category": "<category>", "timestamp": "mm:ss", "text": "..." }}
  ],
  "required_phrases": [ {{ "phrase": "...", "present": true, "timestamp": "mm:ss" }} ],
  "work_on_next": {{ "intro": "...", "items": ["..."] }}
}}"""

    def _send(self, text: str, call_id: str, duration_s: float):
        request = dict(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=self.system,
            messages=[{"role": "user", "content": f"Call ID: {call_id}\nDuration: {_fmt_ts(duration_s)}\n\nTRANSCRIPT:\n{text}"}],
        )
        # a classifier decline is re-run on another model inside this same call
        return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)

    def score(self, res: TranscriptResult) -> dict:
        text = transcript_to_text(res)
        msg = self._send(text, res.call_id, res.audio_duration_s)
        raw = _served_text(msg.content)
        iterations = getattr(msg.usage, "iterations", None) or []
        meta = {
            "scorer_model": msg.model,                # who answered: a fallback model, if one ran
            "requested_model": self.model,
            "fallback": any(getattr(i, "type", "") == "fallback_message" for i in iterations),
            "scorer_version": self.version,
            "transcriber": f"{res.provider}/{res.model}",
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "stop_reason": msg.stop_reason,
        }
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            category = getattr(details, "category", None)
            reason = f"Claude declined to score this call ({category})" if category else "Claude declined to score this call"
            return {"parse_error": True, "reason": reason, "_meta": meta}
        if msg.stop_reason == "max_tokens":
            return {"parse_error": True, "reason": "output truncated at max_tokens", "raw": raw, "_meta": meta}
        try:
            data = normalize_score(json.loads(_extract_json(raw)), self.card,
                                   speakers={s.speaker for s in res.segments if s.speaker},
                                   transcript_text=res.text)
        except json.JSONDecodeError as e:
            return {"parse_error": True, "reason": f"invalid JSON: {e}", "raw": raw, "_meta": meta}
        except ScoreInvalid as e:
            return {"parse_error": True, "reason": f"scorer output failed validation: {e}", "raw": raw, "_meta": meta}
        data["_meta"] = {**data["_meta"], **meta}
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
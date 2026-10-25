"""Background worker: one daemon thread draining `app.state.jobs`, one call at a time
(local Whisper uses the GPU).

Jobs are `(call_id, mode)` with mode "full" (transcribe + score through
`pipeline.process_call`) or "rescore" (score the stored transcript — used by
Re-score, and in remote mode for every transcript a Mac worker posts back).

In remote mode this worker never transcribes: queued calls wait for a Mac to
claim them over /api/worker, and only scoring runs here.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from api import derive, repo
from api.media import find_audio
from api.models import TERMINAL, Call, CallStatus, utcnow
from api.redaction import redact_result, redact_score
from transcribers.base import Segment, TranscriptResult

log = logging.getLogger(__name__)

DEFAULT_RTF = 0.15        # local Whisper, until there are measured calls
RTF_SAMPLES = 10
TICK_S = 2.0
MAX_TRANSCRIBE_PROGRESS = 0.9
SCORING_PROGRESS = 0.95

Job = tuple[str, str]


class Scorer(Protocol):
    def score(self, res: TranscriptResult) -> dict: ...


class Worker:
    def __init__(
        self,
        engine: Engine,
        jobs: queue.Queue[Job | None],
        uploads_dir: Path,
        provider: str,
        process_call: Callable[..., dict] | None,   # None in remote mode
        make_scorer: Callable[[], Scorer],
        tick_s: float = TICK_S,
        remote: bool = False,
    ) -> None:
        self.engine = engine
        self.jobs = jobs
        self.uploads_dir = uploads_dir
        self.provider = provider
        self.process_call = process_call
        self.tick_s = tick_s
        self.remote = remote
        # one scorer for the app's lifetime; if it can't be built (no API key), every job says why
        self._scorer: Scorer | None = None
        self._scorer_error: Exception | None = None
        try:
            self._scorer = make_scorer()
        except Exception as e:
            log.error("scorer unavailable: %s", e)
            self._scorer_error = e
        self._thread = threading.Thread(target=self._run, name="call-worker", daemon=True)

    # ---- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self.jobs.put(None)
        self._thread.join(timeout)

    def recover(self) -> int:
        """Re-queue rows a previous run left unfinished. Returns how many jobs it queued.

        Remote mode only resumes scoring: queued and leased calls belong to the Mac
        worker (expired leases are handled by api.leases)."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(Call).where(col(Call.status).not_in(TERMINAL)).order_by(col(Call.uploaded_at))
            ).all()
            jobs: list[Job] = []
            for call in rows:
                if call.status == CallStatus.scoring and call.transcript_json:
                    jobs.append((call.id, "rescore"))
                elif self.remote:
                    if call.status == CallStatus.scoring:   # scoring with no transcript: needs one first
                        call.status, call.progress = CallStatus.queued, 0.0
                        s.add(call)
                else:
                    call.status, call.progress = CallStatus.queued, 0.0
                    s.add(call)
                    jobs.append((call.id, "full"))
            s.commit()
        for job in jobs:
            self.jobs.put(job)
        if jobs:
            log.info("re-queued %d unfinished call(s)", len(jobs))
        return len(jobs)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
                call_id, mode = job
                try:
                    if mode == "rescore":
                        self._rescore(call_id)
                    else:
                        self._full(call_id)
                except Exception as exc:
                    log.exception("call %s failed (%s)", call_id, mode)
                    self._update(call_id, status=CallStatus.error, error=str(exc) or type(exc).__name__)
            finally:
                self.jobs.task_done()

    # ---- jobs ------------------------------------------------------------------------

    @property
    def scorer(self) -> Scorer:
        if self._scorer is None:
            raise RuntimeError(f"Scorer unavailable: {self._scorer_error}")
        return self._scorer

    def _full(self, call_id: str) -> None:
        if self.process_call is None:
            raise RuntimeError("This server doesn't transcribe (WORKER_MODE=remote)")
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:       # deleted while queued
                return
            duration_s, stereo = call.duration_s, call.stereo
        audio = find_audio(self.uploads_dir, call_id)
        if audio is None:
            raise FileNotFoundError("Audio file is missing")
        scorer = self.scorer
        rtf = self.current_rtf(stereo)
        ticker = _ProgressTicker(self, call_id, duration_s, rtf)

        def on_status(stage: str) -> None:
            if stage == "transcribing":
                self._update(call_id, status=CallStatus.transcribing, progress=0.0, error=None)
                ticker.start()
            elif stage == "scoring":
                ticker.stop()
                self._update(call_id, status=CallStatus.scoring, progress=SCORING_PROGRESS)

        try:
            record = self.process_call(
                audio, provider=self.provider, stereo=stereo, call_id=call_id,
                on_status=on_status, scorer=scorer, out_dir=None, redact=redact_result,
            )
        finally:
            ticker.stop()
        self._save(call_id, record["transcript"], record["score"])

    def _rescore(self, call_id: str) -> None:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:
                return
            transcript = derive.parse_json(call.transcript_json)
        if transcript is None:
            raise ValueError("No transcript to score")
        self._update(call_id, status=CallStatus.scoring, progress=SCORING_PROGRESS, error=None)
        res = TranscriptResult(
            provider=transcript["provider"], model=transcript["model"], call_id=transcript.get("call_id", call_id),
            segments=[Segment(**seg) for seg in transcript["segments"]],
            latency_s=transcript["latency_s"], audio_duration_s=transcript["audio_duration_s"],
            cost_usd=transcript.get("cost_usd", 0.0),
        )
        self._save(call_id, transcript, self.scorer.score(res))

    def _save(self, call_id: str, transcript: dict, score: dict) -> None:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:       # deleted mid-job
                return
            call.transcript_json = json.dumps(transcript, ensure_ascii=False)
            call.provider, call.model = transcript.get("provider"), transcript.get("model")
            if score.get("parse_error"):
                # keep the transcript (and any earlier score) so Re-score can retry
                call.status, call.error = CallStatus.error, f"Scoring failed: {score.get('reason', 'unknown')}"
            else:
                score = redact_score(score)   # the scorer only saw redacted text; this is belt and braces
                call.score_json = json.dumps(score, ensure_ascii=False)
                call.scored_at = utcnow()
                call.status, call.progress, call.error = CallStatus.done, 1.0, None
                if call.agent_id is None and isinstance(score.get("agent_name"), str):
                    agent = repo.agent_for_name(s, score["agent_name"])
                    call.agent_id = agent.id if agent else None
            s.add(call)
            s.commit()

    # ---- progress --------------------------------------------------------------------

    def current_rtf(self, stereo: bool | None) -> float:
        """Mean latency/duration of the last few finished calls from this provider —
        same channel layout if there are any (stereo split runs Whisper twice)."""
        ratio = (func.json_extract(Call.transcript_json, "$.latency_s")
                 / func.json_extract(Call.transcript_json, "$.audio_duration_s"))
        with Session(self.engine) as s:
            for same_layout in (True, False):
                stmt = select(ratio).where(
                    Call.status == CallStatus.done, Call.provider == self.provider,
                    col(Call.transcript_json).is_not(None),
                )
                if same_layout:
                    stmt = stmt.where(Call.stereo == stereo)
                ratios = [r for r in s.exec(stmt.order_by(col(Call.scored_at).desc()).limit(RTF_SAMPLES)).all()
                          if r and r > 0]
                if ratios:
                    return sum(ratios) / len(ratios)
        return DEFAULT_RTF

    def _update(self, call_id: str, **fields: Any) -> None:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:
                return
            for k, v in fields.items():
                setattr(call, k, v)
            s.add(call)
            s.commit()


class _ProgressTicker:
    """Whisper has no progress callback: estimate from elapsed time and the RTF,
    capped at 0.9, written to the row every tick while transcribing."""

    def __init__(self, worker: Worker, call_id: str, duration_s: float | None, rtf: float) -> None:
        self.worker, self.call_id = worker, call_id
        self.expected_s = (duration_s or 0) * rtf
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.expected_s <= 0 or self._thread:
            return
        t0 = time.monotonic()

        def tick() -> None:
            while not self._stop.wait(self.worker.tick_s):
                progress = min(MAX_TRANSCRIBE_PROGRESS, (time.monotonic() - t0) / self.expected_s)
                with Session(self.worker.engine) as s:
                    call = s.get(Call, self.call_id)
                    if call is None or call.status != CallStatus.transcribing:
                        return
                    call.progress = round(progress, 3)
                    s.add(call)
                    s.commit()

        self._thread = threading.Thread(target=tick, name=f"progress-{self.call_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()

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

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from api import derive, repo
from api.media import find_audio
from api.models import ON_WORKER, TERMINAL, Call, CallStatus
from api.redaction import redact_result, redact_score
from transcribers import diarize
from transcribers.base import Segment, TranscriptResult

log = logging.getLogger(__name__)

DEFAULT_RTF = repo.DEFAULT_RTF
TICK_S = 2.0
MAX_TRANSCRIBE_PROGRESS = 0.9
SCORING_PROGRESS = 0.95

Job = tuple[str, str]


class CallCancelled(Exception):
    """Someone cancelled this call while it was running."""


class Scorer(Protocol):
    def score(self, res: TranscriptResult) -> dict: ...


def result_from_dict(transcript: dict, call_id: str) -> TranscriptResult:
    """A stored transcript, ready to score again."""
    return TranscriptResult(
        provider=transcript["provider"], model=transcript["model"], call_id=transcript.get("call_id", call_id),
        segments=[Segment(**seg) for seg in transcript["segments"]],
        latency_s=transcript["latency_s"], audio_duration_s=transcript["audio_duration_s"],
        cost_usd=transcript.get("cost_usd", 0.0),
    )


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
                if call.cancelled_at is not None:
                    if call.status not in ON_WORKER or not self.remote:   # else its lease expiry removes it
                        repo.purge_call(s, call, self.uploads_dir)
                elif call.status == CallStatus.scoring and call.transcript_json:
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
                except CallCancelled:
                    self._remove_cancelled(call_id)
                except Exception as exc:
                    log.exception("call %s failed (%s)", call_id, mode)
                    if not self._remove_cancelled(call_id):
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
        ticker = _ProgressTicker(self, call_id, duration_s, self.expected_rtf(stereo))

        def on_status(stage: str) -> None:
            if self._cancelled(call_id):    # the model can't be stopped, but the next stage can
                raise CallCancelled
            if stage == "transcribing":
                self._update(call_id, status=CallStatus.transcribing, progress=0.0, error=None)
                ticker.start()
            elif stage == "separating":
                self._update(call_id, status=CallStatus.separating)
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
        self._save(call_id, record["transcript"], record["score"], waveform=record.get("waveform"))

    def _rescore(self, call_id: str) -> None:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:
                return
            transcript = derive.parse_json(call.transcript_json)
        if transcript is None:
            raise ValueError("No transcript to score")
        if self._cancelled(call_id):
            raise CallCancelled
        self._update(call_id, status=CallStatus.scoring, progress=SCORING_PROGRESS, error=None)
        self._save(call_id, transcript, self.scorer.score(result_from_dict(transcript, call_id)))

    def _save(self, call_id: str, transcript: dict, score: dict, waveform: list[float] | None = None) -> None:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None:       # deleted mid-job
                return
            if call.cancelled_at is not None:
                repo.purge_call(s, call, self.uploads_dir)
                s.commit()
                return
            call.transcript_json = json.dumps(transcript, ensure_ascii=False)
            call.provider, call.model = transcript.get("provider"), transcript.get("model")
            call.speaker_source = transcript.get("speaker_source") or call.speaker_source
            if waveform:
                call.waveform = json.dumps(waveform)
            if score.get("parse_error"):
                # keep the transcript (and any earlier score) so Re-score can retry
                call.status, call.error = CallStatus.error, f"Scoring failed: {score.get('reason', 'unknown')}"
            else:
                # the scorer only saw redacted text; this is belt and braces
                repo.apply_score(s, call, redact_score(score))
                call.status, call.progress, call.error = CallStatus.done, 1.0, None
            s.add(call)
            s.commit()

    def _cancelled(self, call_id: str) -> bool:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            return call is not None and call.cancelled_at is not None

    def _remove_cancelled(self, call_id: str) -> bool:
        with Session(self.engine) as s:
            call = s.get(Call, call_id)
            if call is None or call.cancelled_at is None:
                return False
            repo.purge_call(s, call, self.uploads_dir)
            s.commit()
            return True

    # ---- progress --------------------------------------------------------------------

    def current_rtf(self, stereo: bool | None) -> float:
        with Session(self.engine) as s:
            return repo.recent_rtf(s, self.provider, stereo)

    def expected_rtf(self, stereo: bool | None) -> float:
        """Transcription plus, for a mono file if it's on, speaker separation."""
        separation = 0.0
        if diarize.enabled() and not stereo:
            with Session(self.engine) as s:
                separation = repo.recent_separation_rtf(s)
        return self.current_rtf(stereo) + separation

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
                    if call is None or call.status not in ON_WORKER:
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

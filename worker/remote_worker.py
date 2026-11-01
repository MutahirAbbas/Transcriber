"""Mac transcription worker for a server running WORKER_MODE=remote.

    python -m worker.remote_worker

Claims queued calls from the server, downloads the audio, transcribes it here
with the existing transcribers (mlx-whisper by default), posts the transcript
back and deletes its local copy. Outbound HTTPS only.

Settings come from the environment or, failing that, worker.env in the repo root:
    SERVER_URL     https://calls.tqc4.ai
    WORKER_TOKEN   same value as the server's WORKER_TOKEN
    WORKER_ID      name shown in the UI (default: this Mac's name)
    TRANSCRIBER    default whisper_local
    DIARIZER       pyannote to separate speakers on mono recordings (needs HF_TOKEN)
    WORKER_MODE    remote-production on the Mac serving the live site. Anything else (or unset)
                   and the worker only talks to this machine: see assert_server_allowed.

Logs one line per job — call id, audio length, time taken, outcome — never text.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from dotenv import load_dotenv

from transcribers import Transcriber, compute_waveform, diarize, get_transcriber, separate_speakers

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("call-worker")

IDLE_SLEEP_S = 10.0
FIRST_BACKOFF_S = 5.0
MAX_BACKOFF_S = 60.0
HEARTBEAT_S = 30.0
MAX_PROGRESS = 0.9
DEFAULT_RTF = 0.15
DEFAULT_SEPARATION_RTF = 0.05
TIMEOUT = httpx.Timeout(30.0, read=300.0)   # audio downloads can be large
PRODUCTION_MODE = "remote-production"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


class Stop(BaseException):
    """SIGTERM / SIGINT arrived while a job was running. Not an Exception, so nothing it
    interrupts (a model, the never-fail wrapper round diarization) can swallow it."""


class Cancelled(BaseException):
    """Someone cancelled the running call. Same reasoning as Stop."""


class LeaseLost(Exception):
    """The server gave this call to someone else (our lease expired)."""


@dataclass(frozen=True)
class Config:
    server_url: str
    token: str
    worker_id: str
    transcriber: str


def assert_server_allowed(server_url: str, worker_mode: str) -> None:
    """A dev checkout must never claim real jobs: outside WORKER_MODE=remote-production,
    refuse to start unless the server is on this machine."""
    if worker_mode != PRODUCTION_MODE and urlsplit(server_url).hostname not in LOOPBACK_HOSTS:
        raise SystemExit(f"SERVER_URL must be on this machine unless WORKER_MODE={PRODUCTION_MODE} "
                         f"(got WORKER_MODE={worker_mode or 'unset'}, SERVER_URL={server_url})")


def load_config(env_file: Path = ROOT / "worker.env") -> Config:
    load_dotenv(env_file)   # variables already in the environment win
    url = os.environ.get("SERVER_URL", "").strip().rstrip("/")
    token = os.environ.get("WORKER_TOKEN", "").strip()
    if not url or not token:
        raise SystemExit("SERVER_URL and WORKER_TOKEN must be set (environment or worker.env)")
    assert_server_allowed(url, os.environ.get("WORKER_MODE", "").strip())
    worker_id = os.environ.get("WORKER_ID", "").strip() or socket.gethostname().removesuffix(".local")
    return Config(url, token, worker_id, os.environ.get("TRANSCRIBER", "whisper_local").strip())


def make_transcriber(provider: str, stereo: bool) -> Transcriber:
    # the same provider → options mapping as pipeline.process_call
    kw: dict[str, Any] = {}
    if provider == "whisper_local":
        kw["stereo_split"] = stereo
    if provider == "deepgram":
        kw["multichannel"] = stereo
    return get_transcriber(provider, **kw)


_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,5}")


def _local_name(hint: str) -> str:
    """A safe local filename that keeps the extension (ffmpeg sniffs content anyway)."""
    suffix = Path(hint).suffix
    return "audio" + (suffix if _SUFFIX.fullmatch(suffix) else ".audio")


class RemoteWorker:
    def __init__(self, cfg: Config, client: httpx.Client, stop: threading.Event) -> None:
        self.cfg, self.client, self.stop = cfg, client, stop
        self.model = make_transcriber(cfg.transcriber, False).model.split("/")[-1]
        self.busy = False   # a job is running: SIGTERM interrupts it (see main)
        self.cancelled = threading.Event()   # checked between stages
        self.interrupt: Callable[[], None] = lambda: None   # stops the model mid-run (see main)

    # ---- loop --------------------------------------------------------------------------

    def run(self) -> None:
        backoff = 0.0
        connected = False
        while not self.stop.is_set():
            try:
                job = self.claim()
            except httpx.HTTPStatusError as e:
                log.warning("claim refused: HTTP %s", e.response.status_code)
            except httpx.HTTPError as e:
                log.warning("server unreachable: %s", type(e).__name__)
            else:
                if not connected:
                    log.info("connected to %s", self.cfg.server_url)
                    connected = True
                backoff = 0.0
                if job is None:
                    self.stop.wait(IDLE_SLEEP_S)
                else:
                    self.process(job)
                continue
            connected = False
            backoff = min(MAX_BACKOFF_S, backoff * 2 or FIRST_BACKOFF_S)
            self.stop.wait(backoff)

    def claim(self) -> dict[str, Any] | None:
        r = self.client.post("/api/worker/claim", json={"worker_id": self.cfg.worker_id, "model": self.model})
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    # ---- one job -----------------------------------------------------------------------

    def process(self, job: dict[str, Any]) -> str:
        call_id = job["call_id"]
        duration = float(job.get("duration_s") or 0)
        stereo = bool(job.get("stereo"))
        rtf = float(job.get("rtf") or DEFAULT_RTF)
        if diarize.enabled() and not stereo:
            rtf += float(job.get("separation_rtf") or DEFAULT_SEPARATION_RTF)
        t0 = time.monotonic()
        heartbeat = _Heartbeat(self, call_id, expected_s=duration * rtf)
        outcome = "ok"
        self.cancelled.clear()
        self.busy = True
        try:
            with tempfile.TemporaryDirectory(prefix="call-worker-") as td:   # removed on the way out
                heartbeat.start()
                audio = Path(td) / _local_name(job.get("audio_path_hint") or "")
                self.download(call_id, audio)
                res = make_transcriber(self.cfg.transcriber, stereo).transcribe(
                    audio, call_id, keywords=job.get("keywords") or None)
                self._check_cancelled()
                res = separate_speakers(res, audio, stereo, on_start=heartbeat.separating)
                waveform = compute_waveform(audio)
                heartbeat.stop()
                self._check_cancelled()
                self.post_transcript(call_id, {**_without_words(res.to_dict()), "waveform": waveform})
        except Cancelled:
            outcome = "cancelled"
        except Stop:
            outcome = "stopped"
            self.fail(call_id, "Transcriber was stopped mid-job")
        except LeaseLost:
            outcome = "lease lost"
        except Exception as e:
            outcome = f"failed ({type(e).__name__})"
            self.fail(call_id, f"{type(e).__name__}: {e}")
        finally:
            self.busy = False
            heartbeat.stop()
            log.info("call=%s audio=%ds took=%ds outcome=%s", call_id, round(duration), round(time.monotonic() - t0), outcome)
        return outcome

    def _check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled

    def cancel_running(self) -> None:
        self.cancelled.set()
        self.interrupt()

    def _job(self, call_id: str, what: str) -> str:
        return f"/api/worker/jobs/{quote(call_id, safe='')}/{what}"

    def download(self, call_id: str, dest: Path) -> None:
        with self.client.stream("GET", self._job(call_id, "audio")) as r:
            if r.status_code == 409:
                raise LeaseLost
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)

    def post_transcript(self, call_id: str, transcript: dict[str, Any]) -> None:
        r = self.client.post(self._job(call_id, "transcript"), json=transcript)
        if r.status_code == 409:
            raise LeaseLost
        if r.status_code == 404:    # deleted while we worked
            raise Cancelled
        r.raise_for_status()

    def heartbeat(self, call_id: str, progress: float, stage: str = "transcribing") -> bool:
        """Renew the lease. True means stop: the call was cancelled or deleted."""
        r = self.client.post(self._job(call_id, "heartbeat"), json={"stage": stage, "progress": progress})
        if r.status_code == 409:
            raise LeaseLost
        if r.status_code == 404:
            return True
        r.raise_for_status()
        return bool(r.json().get("cancel"))

    def fail(self, call_id: str, error: str) -> None:
        try:
            self.client.post(self._job(call_id, "fail"), json={"error": error[:500]})
        except httpx.HTTPError as e:   # the lease will expire and requeue it anyway
            log.warning("couldn't report failure for %s: %s", call_id, type(e).__name__)


class _Heartbeat:
    """Every HEARTBEAT_S while a job runs: renew the lease and send the elapsed-time
    progress estimate (Whisper has no progress callback)."""

    def __init__(self, worker: RemoteWorker, call_id: str, expected_s: float) -> None:
        self.worker, self.call_id, self.expected_s = worker, call_id, expected_s
        self.stage = "transcribing"
        self._t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _progress(self) -> float:
        elapsed = time.monotonic() - self._t0
        return round(min(MAX_PROGRESS, elapsed / self.expected_s), 3) if self.expected_s > 0 else 0.0

    def separating(self) -> None:
        """Tell the server now rather than at the next beat, so the UI shows the new stage."""
        self.stage = "separating"
        try:
            if self.worker.heartbeat(self.call_id, self._progress(), self.stage):
                raise Cancelled
        except (LeaseLost, httpx.HTTPError) as e:   # the regular beat will try again
            log.warning("stage update for %s failed: %s", self.call_id, type(e).__name__)

    def start(self) -> None:
        self._t0 = time.monotonic()

        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_S):
                try:
                    if self.worker.heartbeat(self.call_id, self._progress(), self.stage):
                        log.info("call=%s cancelled; stopping", self.call_id)
                        self.worker.cancel_running()
                        return
                except LeaseLost:
                    log.warning("call=%s lease lost; finishing anyway", self.call_id)
                    return
                except httpx.HTTPError as e:
                    log.warning("heartbeat for %s failed: %s", self.call_id, type(e).__name__)

        self._thread = threading.Thread(target=beat, name=f"heartbeat-{self.call_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()


def _without_words(transcript: dict[str, Any]) -> dict[str, Any]:
    """Word timings stay on this Mac: they were only needed to assign speakers."""
    return {**transcript, "segments": [{k: v for k, v in s.items() if k != "words"} for s in transcript["segments"]]}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub"):   # one line per job, not one per poll
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config()
    stop = threading.Event()

    log.info("loading %s model…", cfg.transcriber)
    make_transcriber(cfg.transcriber, False).load()   # once, not per job
    diarize.load()

    client = httpx.Client(
        base_url=cfg.server_url, timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {cfg.token}", "X-Worker-Id": quote(cfg.worker_id, safe="")},
    )
    worker = RemoteWorker(cfg, client, stop)
    worker.interrupt = lambda: os.kill(os.getpid(), signal.SIGUSR1)

    def on_cancel(_signum: int, _frame: FrameType | None) -> None:
        if worker.busy:
            raise Cancelled   # same route as SIGTERM: interrupts the model in the main thread

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("received %s, stopping", signal.Signals(signum).name)
        stop.set()
        if worker.busy:
            raise Stop   # interrupts the job in the main thread; process() reports it as failed

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGUSR1, on_cancel)

    log.info("polling %s every %ds as %r", cfg.server_url, IDLE_SLEEP_S, cfg.worker_id)
    try:
        worker.run()
    finally:
        client.close()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

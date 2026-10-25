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
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from transcribers import Transcriber, get_transcriber

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("call-worker")

IDLE_SLEEP_S = 10.0
FIRST_BACKOFF_S = 5.0
MAX_BACKOFF_S = 60.0
HEARTBEAT_S = 30.0
MAX_PROGRESS = 0.9
DEFAULT_RTF = 0.15
TIMEOUT = httpx.Timeout(30.0, read=300.0)   # audio downloads can be large


class Stop(Exception):
    """SIGTERM / SIGINT arrived while a job was running."""


class LeaseLost(Exception):
    """The server gave this call to someone else (our lease expired)."""


@dataclass(frozen=True)
class Config:
    server_url: str
    token: str
    worker_id: str
    transcriber: str


def load_config(env_file: Path = ROOT / "worker.env") -> Config:
    load_dotenv(env_file)   # variables already in the environment win
    url = os.environ.get("SERVER_URL", "").strip().rstrip("/")
    token = os.environ.get("WORKER_TOKEN", "").strip()
    if not url or not token:
        raise SystemExit("SERVER_URL and WORKER_TOKEN must be set (environment or worker.env)")
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
        rtf = float(job.get("rtf") or DEFAULT_RTF)
        t0 = time.monotonic()
        heartbeat = _Heartbeat(self, call_id, expected_s=duration * rtf)
        outcome = "ok"
        self.busy = True
        try:
            with tempfile.TemporaryDirectory(prefix="call-worker-") as td:   # removed on the way out
                heartbeat.start()
                audio = Path(td) / _local_name(job.get("audio_path_hint") or "")
                self.download(call_id, audio)
                res = make_transcriber(self.cfg.transcriber, bool(job.get("stereo"))).transcribe(
                    audio, call_id, keywords=job.get("keywords") or None)
                heartbeat.stop()
                self.post_transcript(call_id, res.to_dict())
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
        r.raise_for_status()

    def heartbeat(self, call_id: str, progress: float) -> None:
        r = self.client.post(self._job(call_id, "heartbeat"), json={"stage": "transcribing", "progress": progress})
        if r.status_code == 409:
            raise LeaseLost
        r.raise_for_status()

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
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        t0 = time.monotonic()

        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_S):
                elapsed = time.monotonic() - t0
                progress = min(MAX_PROGRESS, elapsed / self.expected_s) if self.expected_s > 0 else 0.0
                try:
                    self.worker.heartbeat(self.call_id, round(progress, 3))
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub"):   # one line per job, not one per poll
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config()
    stop = threading.Event()

    log.info("loading %s model…", cfg.transcriber)
    make_transcriber(cfg.transcriber, False).load()   # once, not per job

    client = httpx.Client(
        base_url=cfg.server_url, timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {cfg.token}", "X-Worker-Id": quote(cfg.worker_id, safe="")},
    )
    worker = RemoteWorker(cfg, client, stop)

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("received %s, stopping", signal.Signals(signum).name)
        stop.set()
        if worker.busy:
            raise Stop   # interrupts the job in the main thread; process() reports it as failed

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("polling %s every %ds as %r", cfg.server_url, IDLE_SLEEP_S, cfg.worker_id)
    try:
        worker.run()
    finally:
        client.close()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

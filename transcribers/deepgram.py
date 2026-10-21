"""Deepgram adapter (REST, prerecorded).

Env: DEEPGRAM_API_KEY
Docs: https://developers.deepgram.com/reference/speech-to-text-api/listen
Check the model name and pricing on the dashboard before running the benchmark.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from .base import Segment, Transcriber, TranscriptResult, audio_duration


class DeepgramTranscriber(Transcriber):
    name = "deepgram"

    def __init__(self, model: str = "nova-3", diarize: bool = True, multichannel: bool = False):
        self.model = model
        self.diarize = diarize
        self.multichannel = multichannel          # set True if Vici gives stereo agent/customer
        self.price_per_hour_usd = 0.0             # TODO: fill from deepgram.com/pricing
        self.api_key = os.environ["DEEPGRAM_API_KEY"]

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        params: dict = {
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
            "diarize": str(self.diarize).lower(),
            "multichannel": str(self.multichannel).lower(),
        }
        # nova-3 uses `keyterm`; older models use `keywords`
        if keywords:
            params["keyterm"] = keywords

        t0 = time.perf_counter()
        with httpx.Client(timeout=600) as client:
            resp = client.post(
                "https://api.deepgram.com/v1/listen",
                params=params,
                headers={"Authorization": f"Token {self.api_key}", "Content-Type": "audio/*"},
                content=audio_path.read_bytes(),
            )
        latency = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        segments: list[Segment] = []
        for utt in data.get("results", {}).get("utterances", []):
            spk = utt.get("speaker")
            if self.multichannel:
                spk = utt.get("channel")
            segments.append(Segment(
                start=utt["start"], end=utt["end"], text=utt["transcript"],
                speaker=f"SPEAKER_{spk}" if spk is not None else None,
                confidence=utt.get("confidence"),
            ))

        dur = data.get("metadata", {}).get("duration") or audio_duration(audio_path)
        return TranscriptResult(
            provider=self.name, model=self.model, call_id=call_id,
            segments=segments, latency_s=latency, audio_duration_s=dur,
            cost_usd=self.estimate_cost(dur), raw=data,
        )

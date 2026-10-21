"""AssemblyAI adapter (official SDK).

Env: ASSEMBLYAI_API_KEY
Docs: https://www.assemblyai.com/docs
`pip install assemblyai`. Check speech model options and pricing before benchmarking.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .base import Segment, Transcriber, TranscriptResult, audio_duration


class AssemblyAITranscriber(Transcriber):
    name = "assemblyai"

    def __init__(self, model: str = "best", diarize: bool = True):
        import assemblyai as aai
        aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
        self._aai = aai
        self.model = model
        self.diarize = diarize
        self.price_per_hour_usd = 0.0     # TODO: fill from assemblyai.com/pricing

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        aai = self._aai
        cfg_kwargs: dict = {
            "speaker_labels": self.diarize,
            "punctuate": True,
            "format_text": True,
        }
        if keywords:
            cfg_kwargs["word_boost"] = keywords
            cfg_kwargs["boost_param"] = "high"
        if self.model == "nano":
            cfg_kwargs["speech_model"] = aai.SpeechModel.nano
        config = aai.TranscriptionConfig(**cfg_kwargs)

        t0 = time.perf_counter()
        transcript = aai.Transcriber().transcribe(str(audio_path), config=config)
        latency = time.perf_counter() - t0
        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(transcript.error)

        segments: list[Segment] = []
        if self.diarize and transcript.utterances:
            for u in transcript.utterances:
                segments.append(Segment(
                    start=u.start / 1000, end=u.end / 1000, text=u.text,
                    speaker=f"SPEAKER_{u.speaker}", confidence=u.confidence,
                ))
        else:
            for s in transcript.get_sentences():
                segments.append(Segment(start=s.start / 1000, end=s.end / 1000, text=s.text))

        dur = (transcript.audio_duration or 0) or audio_duration(audio_path)
        return TranscriptResult(
            provider=self.name, model=self.model, call_id=call_id,
            segments=segments, latency_s=latency, audio_duration_s=dur,
            cost_usd=self.estimate_cost(dur), raw=transcript.json_response or {},
        )

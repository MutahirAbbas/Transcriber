from .base import Segment, Transcriber, TranscriptResult, audio_channels, audio_duration
from .diarize import separate_speakers
from .waveform import compute_waveform


def get_transcriber(name: str, **kwargs) -> Transcriber:
    """Factory so the benchmark and the pipeline pick providers by string."""
    if name == "deepgram":
        from .deepgram import DeepgramTranscriber
        return DeepgramTranscriber(**kwargs)
    if name == "assemblyai":
        from .assemblyai import AssemblyAITranscriber
        return AssemblyAITranscriber(**kwargs)
    if name == "whisper_local":
        from .whisper_local import WhisperLocalTranscriber
        return WhisperLocalTranscriber(**kwargs)
    raise ValueError(f"unknown transcriber: {name}")


__all__ = ["Segment", "Transcriber", "TranscriptResult", "audio_channels", "audio_duration", "get_transcriber",
           "compute_waveform", "separate_speakers"]

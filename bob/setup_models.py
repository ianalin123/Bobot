"""Explicit one-time model download; normal server operation only opens cached files."""

import os

from faster_whisper import WhisperModel

from .providers import ROOT

if __name__ == "__main__":
    name = os.getenv("BOB_WHISPER_MODEL", "base.en")
    WhisperModel(name, device="cpu", compute_type="int8", download_root=str(ROOT / "models/whisper"))
    print(f"Whisper {name} downloaded. Conversation setup: ollama pull qwen3:4b-instruct")

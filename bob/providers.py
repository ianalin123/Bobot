"""Local inference adapters. No cloud fallback and no microphone activation here."""

import asyncio
import io
import json
import os
import platform
import shutil
import tempfile
import threading
import wave
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np

from . import voicefx

ROOT = Path(__file__).resolve().parent.parent
MAX_AUDIO_BYTES = 1_000_000


def decode_wav(data: bytes) -> np.ndarray:
    if not 44 <= len(data) <= MAX_AUDIO_BYTES:
        raise ValueError("Audio must be a WAV recording of at most 30 seconds.")
    with wave.open(io.BytesIO(data), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono, 16-bit PCM WAV at 16000 Hz.")
        if source.getnframes() > 480000:
            raise ValueError("Maximum turn length is 30 seconds.")
        raw = source.readframes(source.getnframes())
        if len(raw) != source.getnframes() * 2:
            raise ValueError("Truncated WAV recording.")
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


class LocalSTT:
    def __init__(self):
        self.model_name = os.getenv("BOB_WHISPER_MODEL", "base.en")
        self.model = None
        self.lock = threading.Lock()
        self.error = None

    def load(self):
        from faster_whisper import WhisperModel

        with self.lock:
            if self.model is None:
                # Downloads happen via `python -m bob.setup_models`, never silently during a chat.
                self.model = WhisperModel(
                    self.model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    download_root=str(ROOT / "models/whisper"),
                    local_files_only=True,
                )

    def transcribe(self, audio: bytes):
        samples = decode_wav(audio)
        if len(samples) < 3200 or np.sqrt(np.mean(samples * samples)) < 0.002:
            return ""
        self.load()
        # Native transcription cannot be forcibly cancelled. Serialize it and discard
        # cancelled turns in VoiceSession; no stale result can trigger speech/actions.
        with self.lock:
            segments, _ = self.model.transcribe(
                samples,
                language="en",
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.65).strip()


class LocalLLM:
    def __init__(self):
        self.url = os.getenv("BOB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        parsed = urlparse(self.url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("This prototype requires a loopback Ollama server.")
        self.model = os.getenv("BOB_MODEL", "qwen3:4b-instruct")
        if "cloud" in self.model.lower():
            raise ValueError("Cloud models are disabled.")

    async def check(self):
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.post(f"{self.url}/api/show", json={"model": self.model})
            response.raise_for_status()
            info = response.json()
            if info.get("remote_host") or info.get("remote_model"):
                raise ValueError("A locally downloaded model is required.")
            return {"ready": True, "model": self.model}

    async def stream(self, messages, tools):
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(60, connect=5)) as client:
            async with client.stream(
                "POST",
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "stream": True,
                    "think": False,
                    "keep_alive": "15m",
                    "options": {"num_ctx": 4096, "num_predict": 180, "temperature": 0.65},
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise RuntimeError(chunk["error"])
                        yield chunk.get("message", {})


class LocalTTS:
    def __init__(self):
        choice = os.getenv("BOB_TTS", "auto")
        self.kind = ("macos" if platform.system() == "Darwin" else "piper") if choice == "auto" else choice
        if self.kind not in {"macos", "piper"}:
            raise ValueError("BOB_TTS must be auto, macos, or piper.")
        self.voice = os.getenv("BOB_MAC_VOICE", "Samantha")
        self.piper_model = os.getenv("BOB_PIPER_MODEL", "")
        self.piper = os.getenv("BOB_PIPER_EXECUTABLE", "piper")
        # Same Minion effects as the cloud voice, so the Mac workbench sounds like Bob too.
        self.pitch_semitones = float(os.getenv("BOB_PITCH_SEMITONES", voicefx.DEFAULT_PITCH_SEMITONES))
        self.speed = float(os.getenv("BOB_SPEED", voicefx.DEFAULT_SPEED))

    def postprocess(self, wav_bytes):
        """Engine output (any rate) -> 16 kHz mono WAV, pitched up and sped up."""
        return voicefx.minionize_wav(wav_bytes, self.pitch_semitones, self.speed)

    def ready(self):
        return (
            bool(shutil.which("say"))
            if self.kind == "macos"
            else bool(shutil.which(self.piper) and self.piper_model and Path(self.piper_model).is_file())
        )

    async def synthesize(self, text):
        if not self.ready():
            raise RuntimeError("Local TTS unavailable. Configure an installed macOS voice or Piper voice.")
        with tempfile.TemporaryDirectory(prefix="bob-speech-") as folder:
            output = Path(folder) / "speech.wav"
            if self.kind == "macos":
                args = [
                    "say",
                    "-v",
                    self.voice,
                    "-r",
                    "195",
                    "--file-format=WAVE",
                    "--data-format=LEI16@22050",
                    "-o",
                    str(output),
                    "--",
                    text,
                ]
                stdin = None
            else:
                args = [self.piper, "--model", self.piper_model, "--output_file", str(output)]
                stdin = text.encode()
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, error = await asyncio.wait_for(process.communicate(stdin), timeout=20)
                if process.returncode:
                    raise RuntimeError(f"Local TTS failed: {error.decode(errors='replace')[:160]}")
                return await asyncio.to_thread(self.postprocess, output.read_bytes())
            except BaseException:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise

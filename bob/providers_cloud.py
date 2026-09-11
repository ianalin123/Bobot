"""Cloud inference adapters (OpenAI, ElevenLabs) with optional local fallback.

Same duck-typed interface as `bob.providers`: `stt.transcribe(wav) -> str` (sync, run in a thread),
`llm.stream(messages, tools)` (async generator of Ollama-style message dicts) and
`tts.synthesize(text) -> bytes` (16 kHz mono 16-bit WAV). SDKs are imported lazily so this module
imports without the `robot` dependency group. API keys only come from `Settings`, never from code.
"""

import asyncio
import io
import json
import logging
import uuid

import numpy as np

from . import voicefx
from .providers import decode_wav

log = logging.getLogger(__name__)

TARGET_SR = voicefx.TARGET_SR
OPENAI_PCM_SR = 24000  # OpenAI `response_format="pcm"` is 24 kHz s16le mono.
MIN_TURN_SECONDS = 0.4
MIN_TURN_RMS = 0.002

TTS_INSTRUCTIONS = (
    "Voice: a Minion from Despicable Me. Tiny, high-pitched and squeaky, giddy and giggly, fast "
    "and bouncy, sing-song with big swoops up on happy words, and a playful cartoon accent with "
    "Italian and Spanish flavoured vowels. The text is mostly Minionese gibberish: pronounce it "
    "exactly as written, syllable by syllable, with total confidence, as if it were a real "
    "language. Sound delighted, like you just found a banana."
)

# Audio helpers live in bob.voicefx; re-exported here for the phrase renderer and tests.
_to_int16, _to_float = voicefx.to_int16, voicefx.to_float
wav_from_pcm16, resample, pitch_shift_wav, voice_fx = (
    voicefx.wav_from_pcm16,
    voicefx.resample,
    voicefx.pitch_shift_wav,
    voicefx.voice_fx,
)


def pcm16_from_bytes(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2")


# --- OpenAI -----------------------------------------------------------------


class OpenAISTT:
    """Speech to text via `client.audio.transcriptions.create` (sync `openai.OpenAI`)."""

    def __init__(self, client, model: str = "gpt-4o-mini-transcribe"):
        self.client = client
        self.model = model

    def transcribe(self, audio: bytes) -> str:
        samples = decode_wav(audio)
        if (
            len(samples) < int(MIN_TURN_SECONDS * TARGET_SR)
            or np.sqrt(np.mean(samples * samples)) < MIN_TURN_RMS
        ):
            return ""
        result = self.client.audio.transcriptions.create(
            model=self.model,
            file=("turn.wav", io.BytesIO(audio), "audio/wav"),
        )
        text = result.get("text") if isinstance(result, dict) else getattr(result, "text", None)
        return (text if isinstance(text, str) else str(result or "")).strip()


def openai_tools(tools) -> list[dict]:
    """Ollama tool specs are already `{"type": "function", "function": {...}}`; wrap bare ones."""
    converted = []
    for tool in tools or []:
        if "function" in tool:
            converted.append(tool if tool.get("type") == "function" else {**tool, "type": "function"})
        else:
            converted.append({"type": "function", "function": tool})
    return converted


def _arguments_json(arguments) -> str:
    if isinstance(arguments, str):
        return arguments or "{}"
    return json.dumps(arguments if arguments is not None else {})


def openai_messages(messages) -> list[dict]:
    """Convert Ollama-style history (tool replies keyed by `tool_name`) to Chat Completions format.

    Assistant tool calls carry ids (assigned by `OpenAILLM.stream`); tool replies are matched to
    them by name and order. Calls voice.py did not execute get a synthetic reply so the request
    stays valid (OpenAI requires a tool message for every tool_call id).
    """
    converted: list[dict] = []
    pending: list[dict] = []  # unanswered {"id", "name"} of the most recent assistant tool_calls

    def flush():
        for call in pending:
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps({"accepted": False, "reason": "Not executed."}),
                }
            )
        pending.clear()

    for message in messages:
        role = message.get("role")
        if role == "tool":
            name = message.get("tool_name")
            match = next((c for c in pending if c["name"] == name), None) or (pending[0] if pending else None)
            if match:
                pending.remove(match)
            call_id = match["id"] if match else f"call_{uuid.uuid4().hex[:24]}"
            converted.append({"role": "tool", "tool_call_id": call_id, "content": message.get("content", "")})
            continue
        flush()
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function", {})
                call_id = call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": function.get("name", ""),
                            "arguments": _arguments_json(function.get("arguments")),
                        },
                    }
                )
                pending.append({"id": call_id, "name": function.get("name", "")})
            converted.append(
                {"role": "assistant", "content": message.get("content") or None, "tool_calls": calls}
            )
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    flush()
    return converted


def _parse_arguments(text: str):
    text = (text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return text  # voice.py rejects non-dict arguments; do not guess.
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class OpenAILLM:
    """Chat Completions streaming via `openai.AsyncOpenAI`, yielding Ollama-style message dicts."""

    def __init__(self, client, model: str = "gpt-4.1-mini", temperature: float = 0.65, max_tokens: int = 180):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def stream(self, messages, tools):
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages(messages),
            tools=openai_tools(tools) or None,
            stream=True,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        partial: dict[int, dict] = {}
        async for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = choices[0].delta
            if delta is None:
                continue
            if delta.content:
                yield {"content": delta.content}
            for call in delta.tool_calls or []:
                entry = partial.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                if getattr(call, "id", None):
                    entry["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        entry["name"] = function.name
                    if getattr(function, "arguments", None):
                        entry["arguments"] += function.arguments
        calls = [
            {
                "id": partial[index]["id"] or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": partial[index]["name"],
                    "arguments": _parse_arguments(partial[index]["arguments"]),
                },
            }
            for index in sorted(partial)
        ]
        if calls:
            yield {"tool_calls": calls}


class OpenAITTS:
    """Text to speech via `openai.AsyncOpenAI` streaming PCM, resampled to 16 kHz and pitch shifted."""

    def __init__(
        self,
        client,
        model: str = "gpt-4o-mini-tts",
        voice: str = "ash",
        instructions: str = TTS_INSTRUCTIONS,
        pitch_semitones: float = voicefx.DEFAULT_PITCH_SEMITONES,
        speed: float = voicefx.DEFAULT_SPEED,
    ):
        self.client = client
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.pitch_semitones = pitch_semitones
        self.speed = speed

    async def synthesize(self, text: str) -> bytes:
        request = {"model": self.model, "voice": self.voice, "input": text, "response_format": "pcm"}
        if self.instructions:
            request["instructions"] = self.instructions
        chunks = []
        async with self.client.audio.speech.with_streaming_response.create(**request) as response:
            async for chunk in response.iter_bytes():
                chunks.append(chunk)
        return await asyncio.to_thread(self._postprocess, b"".join(chunks))

    def _postprocess(self, raw: bytes) -> bytes:
        samples = resample(pcm16_from_bytes(raw), OPENAI_PCM_SR, TARGET_SR)
        samples = voice_fx(samples, TARGET_SR, self.pitch_semitones, self.speed)
        return wav_from_pcm16(samples, TARGET_SR)


# --- ElevenLabs -------------------------------------------------------------


class ElevenLabsTTS:
    """Text to speech via `client.text_to_speech.convert(output_format="pcm_16000")`."""

    def __init__(self, client, voice_id: str, model_id: str = "eleven_flash_v2_5"):
        self.client = client
        self.voice_id = voice_id
        self.model_id = model_id

    async def synthesize(self, text: str) -> bytes:
        request = {
            "voice_id": self.voice_id,
            "text": text,
            "model_id": self.model_id,
            "output_format": "pcm_16000",
        }

        def convert_sync():
            result = self.client.text_to_speech.convert(**request)
            return bytes(result) if isinstance(result, (bytes, bytearray)) else b"".join(result)

        convert = self.client.text_to_speech.convert
        if asyncio.iscoroutinefunction(convert) or hasattr(convert, "__aiter__"):
            result = convert(**request)
            if hasattr(result, "__await__"):
                result = await result
            raw = (
                b"".join([chunk async for chunk in result]) if hasattr(result, "__aiter__") else bytes(result)
            )
        else:
            raw = await asyncio.to_thread(convert_sync)
        return wav_from_pcm16(pcm16_from_bytes(raw), TARGET_SR)


# --- Fallback ---------------------------------------------------------------


class _Fallback:
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.last_error = None


class FallbackSTT(_Fallback):
    def transcribe(self, audio: bytes) -> str:
        try:
            result = self.primary.transcribe(audio)
            self.last_error = None
            return result
        except Exception as exc:
            self.last_error = exc
            log.warning(
                "Primary STT failed (%s: %s); using local fallback.", type(exc).__name__, str(exc)[:160]
            )
            return self.secondary.transcribe(audio)


class FallbackTTS(_Fallback):
    async def synthesize(self, text: str) -> bytes:
        try:
            result = await self.primary.synthesize(text)
            self.last_error = None
            return result
        except Exception as exc:
            self.last_error = exc
            log.warning(
                "Primary TTS failed (%s: %s); using local fallback.", type(exc).__name__, str(exc)[:160]
            )
            return await self.secondary.synthesize(text)


# --- wiring -----------------------------------------------------------------


def build_providers(settings):
    """Return `(stt, llm, tts)` for `settings.mode` in {"cloud", "local"}."""
    from .providers import LocalLLM, LocalSTT, LocalTTS

    if settings.mode == "local":
        return LocalSTT(), LocalLLM(), LocalTTS()
    if settings.mode != "cloud":
        raise ValueError(f"build_providers supports mode cloud or local, got {settings.mode!r}")
    if not settings.openai_api_key:
        raise ValueError("BOB_MODE=cloud requires OPENAI_API_KEY")
    import openai

    sync_client = openai.OpenAI(api_key=settings.openai_api_key)
    async_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    stt = OpenAISTT(sync_client, settings.stt_model)
    llm = OpenAILLM(async_client, settings.llm_model)
    tts = OpenAITTS(
        async_client,
        settings.tts_model,
        settings.tts_voice,
        TTS_INSTRUCTIONS,
        settings.pitch_semitones,
        settings.speed,
    )
    if settings.local_fallback:
        stt = FallbackSTT(stt, LocalSTT())
        tts = FallbackTTS(tts, LocalTTS())
    return stt, llm, tts

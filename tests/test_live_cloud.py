"""Live smoke test against real OpenAI endpoints. Skipped unless BOB_LIVE=1 and OPENAI_API_KEY is set.

Run once: `set -a; . ./.env; set +a; BOB_LIVE=1 uv run pytest -q tests/test_live_cloud.py`
Keeps the calls tiny: one 1 s STT, one short LLM turn, one "Bello!" TTS saved to artifacts/bello.wav.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from bob import voice
from bob.providers import decode_wav

pytestmark = pytest.mark.skipif(
    os.environ.get("BOB_LIVE") != "1" or not os.environ.get("OPENAI_API_KEY"),
    reason="Set BOB_LIVE=1 and OPENAI_API_KEY to run the live cloud smoke test.",
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def providers():
    from bob.config import Settings
    from bob.providers_cloud import build_providers

    return build_providers(Settings.from_env({**os.environ, "BOB_MODE": "cloud", "BOB_LOCAL_FALLBACK": "0"}))


def test_live_stt_returns_string(providers):
    from bob.providers_cloud import wav_from_pcm16

    stt, _, _ = providers
    t = np.arange(16000) / 16000
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2")
    text = stt.transcribe(wav_from_pcm16(tone, 16000))
    assert isinstance(text, str)
    print(f"\nLIVE STT transcript: {text!r}")


async def test_live_llm_short_turn(providers):
    _, llm, _ = providers
    messages = [
        {
            "role": "system",
            "content": "You are Bob, a tiny minion robot. Reply with at most one short sentence.",
        },
        {"role": "user", "content": "say bello"},
    ]
    reply, calls = "", []
    async for message in llm.stream(messages, voice.TOOLS):
        reply += message.get("content", "") or ""
        calls.extend(message.get("tool_calls", []))
    assert reply.strip() or calls
    print(f"\nLIVE LLM reply: {reply.strip()!r} tool_calls={[c['function']['name'] for c in calls]}")


async def test_live_tts_bello(providers):
    _, _, tts = providers
    wav_bytes = await tts.synthesize("Bello!")
    samples = decode_wav(wav_bytes)
    assert len(samples) > 1600 and np.abs(samples).max() > 0.01
    out = ROOT / "artifacts" / "bello.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav_bytes)
    print(f"\nLIVE TTS: {len(wav_bytes)} bytes, {len(samples) / 16000:.2f} s -> {out}")

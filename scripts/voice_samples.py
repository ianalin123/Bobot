"""Render A/B clips of Bob's Minion voice so a human can pick the voice, pitch and speed.

Writes `artifacts/voice-samples/openai/<name>.wav` plus `index.txt`: one clip per OpenAI voice at
the default effects, then a pitch x speed grid for the default voice. Costs a few cents.

    set -a; . ./.env; set +a
    uv run python scripts/voice_samples.py                  # everything
    uv run python scripts/voice_samples.py --voices ash coral --pitches 6 8 --speeds 1.15 1.3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bob import voicefx  # noqa: E402

VOICES = ["ash", "coral", "fable", "sage", "shimmer", "ballad"]
PITCHES = [4.0, 6.0, 8.0]
SPEEDS = [1.0, 1.15, 1.3]
DEFAULT_OUT = "artifacts/voice-samples/openai"
LINE = "Bello, mi amigo! Me want ba-na-naaa, para tu, hehehe! Tulaliloo ti amo, Tim! Poopaye, bee-do bee-do!"


@dataclass(frozen=True)
class Sample:
    name: str
    voice: str
    pitch: float
    speed: float
    text: str = LINE


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def build_plan(
    voices=VOICES,
    pitches=PITCHES,
    speeds=SPEEDS,
    default: str = "ash",
    pitch: float = voicefx.DEFAULT_PITCH_SEMITONES,
    speed: float = voicefx.DEFAULT_SPEED,
) -> list[Sample]:
    plan = [Sample(f"voice-{voice}", voice, pitch, speed) for voice in voices]
    for p in pitches:
        for s in speeds:
            plan.append(Sample(f"{default}-pitch{_num(p)}-speed{s:g}", default, float(p), float(s)))
    return plan


async def render(plan: list[Sample], out_dir: Path, client, model: str) -> None:
    from bob.providers_cloud import TTS_INSTRUCTIONS, OpenAITTS

    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, item in enumerate(plan, 1):
        tts = OpenAITTS(client, model, item.voice, TTS_INSTRUCTIONS, item.pitch, item.speed)
        target = out_dir / f"{item.name}.wav"
        target.write_bytes(await tts.synthesize(item.text))
        lines.append(f"{item.name:<24} voice={item.voice:<8} pitch=+{_num(item.pitch)} speed={item.speed}")
        print(f"[{index}/{len(plan)}] wrote {target}")
    (out_dir / "index.txt").write_text(f"text: {plan[0].text}\n" + "\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--voices", nargs="+", default=VOICES)
    parser.add_argument("--pitches", nargs="+", type=float, default=PITCHES)
    parser.add_argument("--speeds", nargs="+", type=float, default=SPEEDS)
    parser.add_argument("--default-voice", default=os.environ.get("BOB_TTS_VOICE", "ash"))
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--model", default=os.environ.get("BOB_TTS_MODEL", "gpt-4o-mini-tts"))
    args = parser.parse_args(argv)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("error: OPENAI_API_KEY is not set", file=sys.stderr)
        return 2
    import openai

    plan = build_plan(args.voices, args.pitches, args.speeds, args.default_voice)
    asyncio.run(render(plan, Path(args.out), openai.AsyncOpenAI(api_key=api_key), args.model))
    return 0


if __name__ == "__main__":
    sys.exit(main())

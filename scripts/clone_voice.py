"""Clone a voice on ElevenLabs from one or more recordings, then render a Minionese test line with it.

Needs ELEVENLABS_API_KEY and a plan with instant voice cloning (the free tier refuses with
"paid_plan_required"). Background noise removal is on, but a clean, speech-only sample still clones
best: isolate voices from music first (ElevenLabs audio isolation, or any vocal remover) and trim
to the one character you want. Writes `artifacts/voice-samples/elevenlabs/<slug>.wav` and prints
the `.env` lines that make Bob speak with the new voice.

    set -a; . ./.env; set +a
    uv run python scripts/clone_voice.py --name "Bob Minion" artifacts/voice-clone/minion_isolated.mp3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_OUT = "artifacts/voice-samples/elevenlabs"
TEST_LINE = "Bello, mi amigo! Me want ba-na-naaa, para tu, hehehe! Tulaliloo ti amo, Tim! Poopaye!"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "voice"


def create_voice(client, name: str, paths: list[Path], description: str) -> str:
    files = [
        (path.name, path.read_bytes(), "audio/mpeg" if path.suffix.lower() == ".mp3" else None)
        for path in paths
    ]
    # labels={} is required: omitting it makes the SDK send a bad empty field ("invalid_labels").
    response = client.voices.ivc.create(
        name=name, files=files, remove_background_noise=True, description=description or None, labels={}
    )
    return response.voice_id


async def render_sample(client, voice_id: str, out_dir: Path, name: str, pitch: float, speed: float) -> Path:
    from bob.providers_cloud import ElevenLabsTTS

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{slug(name)}.wav"
    tts = ElevenLabsTTS(client, voice_id, pitch_semitones=pitch, speed=speed)
    target.write_bytes(await tts.synthesize(TEST_LINE))
    return target


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "recordings", nargs="+", help="mp3/wav/m4a files of the voice (a clean minute is plenty)"
    )
    parser.add_argument("--name", required=True, help='voice name on ElevenLabs, e.g. "Bob Minion"')
    parser.add_argument("--description", default="Voice for Bob the Minion robot")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--pitch",
        type=float,
        default=0.0,
        help="semitones for the test clip (0: the clone is already a Minion)",
    )
    parser.add_argument("--speed", type=float, default=1.0, help="speed factor for the test clip")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.recordings]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print(f"error: recording not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    if client is None:
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            print("error: ELEVENLABS_API_KEY is not set", file=sys.stderr)
            return 2
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)
    try:
        voice_id = create_voice(client, args.name, paths, args.description)
    except Exception as exc:
        print(f"error: cloning failed: {type(exc).__name__}: {str(exc)[-300:]}", file=sys.stderr)
        return 1
    print(f"created voice {voice_id!r} ({args.name})")
    sample = asyncio.run(render_sample(client, voice_id, Path(args.out), args.name, args.pitch, args.speed))
    print(f"test clip: {sample}")
    print("add to .env to make Bob speak with it (pitch 0 / speed 1 if the clone is already a Minion):")
    print(f"ELEVENLABS_VOICE_ID={voice_id}")
    print("BOB_TTS_PROVIDER=elevenlabs")
    print(f"BOB_PITCH_SEMITONES={args.pitch:g}")
    print(f"BOB_SPEED={args.speed:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

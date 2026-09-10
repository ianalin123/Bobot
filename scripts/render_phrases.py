"""Pre-render Bob's stock phrases and greetings to `assets/phrases/<key>.wav` with a cloud TTS.

Cached clips: every `STOCK_PHRASES` entry (`phrase_<key>`), every greeting without a `{name}`
placeholder, and a generic variant of each named greeting (`{name}` -> "friend"), in list order,
for as many as fit under `--budget` characters (`greet_<index:03d>`). Existing files are skipped
unless `--force`; `manifest.json` is merged and rewritten at the end.

    set -a; . ./.env; set +a
    uv run python scripts/render_phrases.py --dry-run
    uv run python scripts/render_phrases.py --provider elevenlabs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bob.persona import GREETINGS, STOCK_PHRASES  # noqa: E402
from bob.providers import decode_wav  # noqa: E402
from bob.providers_cloud import TARGET_SR, pitch_shift_wav, wav_from_pcm16  # noqa: E402

GENERIC_NAME = "friend"
DEFAULT_OUT = "assets/phrases"
DEFAULT_BUDGET = 6000
DEFAULT_VOICE_HINT = "Josh"
FALLBACK_VOICE_ID = (
    "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs premade "George", used when no voice list is available.
)
ELEVENLABS_MODEL = "eleven_flash_v2_5"
OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "ash"
PITCH_BY_PROVIDER = {"elevenlabs": 4.0, "openai": 0.0}  # OpenAITTS already pitches itself.


@dataclass(frozen=True)
class Item:
    key: str
    text: str
    generic: bool = False

    @property
    def chars(self) -> int:
        return len(self.text)


def build_plan(budget: int = DEFAULT_BUDGET) -> list[Item]:
    """Stock phrases, nameless greetings, then generic greetings while they fit under `budget`."""
    plan = [Item(f"phrase_{key}", text) for key, text in STOCK_PHRASES.items()]
    plan += [Item(f"greet_{i:03d}", g) for i, g in enumerate(GREETINGS) if "{name}" not in g]
    total = sum(item.chars for item in plan)
    for index, template in enumerate(GREETINGS):
        if "{name}" not in template:
            continue
        text = template.format(name=GENERIC_NAME)
        if total + len(text) > budget:
            break
        plan.append(Item(f"greet_{index:03d}", text, generic=True))
        total += len(text)
    return plan


def default_pitch(provider: str) -> float:
    return PITCH_BY_PROVIDER[provider]


def pick_voice(voices, hint: str) -> str:
    """First voice whose name contains `hint` (case-insensitive), else the first voice."""
    entries = [
        (v.get("name", ""), v.get("voice_id", "")) if isinstance(v, dict) else (v.name, v.voice_id)
        for v in voices
    ]
    entries = [(name or "", vid) for name, vid in entries if vid]
    if not entries:
        raise RuntimeError("ElevenLabs returned no voices")
    for name, voice_id in entries:
        if hint and hint.lower() in name.lower():
            return voice_id
    return entries[0][1]


def _elevenlabs_voice_id(client, hint: str) -> str:
    configured = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if configured:
        return configured
    try:
        response = client.voices.get_all()
        voices = getattr(response, "voices", response)
        return pick_voice(list(voices), hint)
    except Exception as exc:  # voice listing is a nicety; the premade id is a safe default
        print(
            f"warning: could not list voices ({type(exc).__name__}); using {FALLBACK_VOICE_ID}",
            file=sys.stderr,
        )
        return FALLBACK_VOICE_ID


def build_synthesizer(provider: str, voice_hint: str):
    """Return `(async text -> wav bytes, model_name)` for the chosen provider. Keys come from the environment."""
    if provider == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        from elevenlabs import ElevenLabs

        from bob.providers_cloud import ElevenLabsTTS

        client = ElevenLabs(api_key=api_key)
        voice_id = _elevenlabs_voice_id(client, voice_hint)
        print(f"elevenlabs voice {voice_id} model {ELEVENLABS_MODEL}")
        return ElevenLabsTTS(client, voice_id, ELEVENLABS_MODEL).synthesize, f"{ELEVENLABS_MODEL}/{voice_id}"
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        import openai

        from bob.providers_cloud import OpenAITTS

        tts = OpenAITTS(openai.AsyncOpenAI(api_key=api_key), OPENAI_MODEL, OPENAI_VOICE)
        return tts.synthesize, f"{OPENAI_MODEL}/{OPENAI_VOICE}"
    raise ValueError(f"unknown provider {provider!r}")


def apply_pitch(wav: bytes, semitones: float) -> bytes:
    if not semitones:
        return wav
    samples = decode_wav(wav)
    return wav_from_pcm16(pitch_shift_wav(samples, TARGET_SR, semitones), TARGET_SR)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def render(plan, out_dir: Path, synthesizer, *, force: bool, pitch: float, provider: str, model: str):
    """Synthesize each item (skipping existing files unless `force`). Returns `(manifest_entries, failures)`."""
    entries: dict[str, dict] = {}
    failures: list[str] = []
    for index, item in enumerate(plan, 1):
        target = out_dir / f"{item.key}.wav"
        entry = {
            "file": target.name,
            "text": item.text,
            "chars": item.chars,
            "provider": provider,
            "model": model,
        }
        if target.exists() and not force:
            print(f"[{index}/{len(plan)}] skip {item.key} (exists)")
            entries[item.key] = entry
            continue
        try:
            wav = apply_pitch(await synthesizer(item.text), pitch)
            decode_wav(wav)  # validate the container before writing
        except Exception as exc:
            print(
                f"[{index}/{len(plan)}] FAIL {item.key}: {type(exc).__name__}: {str(exc)[:160]}",
                file=sys.stderr,
            )
            failures.append(item.key)
            continue
        target.write_bytes(wav)
        entries[item.key] = entry
        print(f"[{index}/{len(plan)}] wrote {target.name} ({item.chars} chars)")
    return entries, failures


def main(argv: list[str] | None = None, synthesizer=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=sorted(PITCH_BY_PROVIDER), default="elevenlabs")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory (default assets/phrases)")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="max characters to render")
    parser.add_argument("--only", action="append", default=[], metavar="KEY", help="render only these keys")
    parser.add_argument(
        "--pitch", type=float, default=None, help="semitones (default +4 elevenlabs, 0 openai)"
    )
    parser.add_argument("--voice-hint", default=DEFAULT_VOICE_HINT, help="ElevenLabs voice name substring")
    parser.add_argument("--force", action="store_true", help="re-render existing files")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args(argv)

    plan = build_plan(args.budget)
    if args.only:
        wanted = set(args.only)
        plan = [item for item in plan if item.key in wanted]
        missing = wanted - {item.key for item in plan}
        if missing:
            print(f"error: unknown or over-budget keys: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    total = sum(item.chars for item in plan)
    generic = sum(item.generic for item in plan)
    print(f"plan: {len(plan)} clips, {total} chars, budget {args.budget} ({generic} generic greetings)")
    if args.dry_run:
        for item in plan:
            print(f"  {item.key:<14} {item.chars:>3}  {item.text}")
        return 0

    pitch = default_pitch(args.provider) if args.pitch is None else args.pitch
    model = "fake"
    if synthesizer is None:
        try:
            synthesizer, model = build_synthesizer(args.provider, args.voice_hint)
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries, failures = asyncio.run(
        render(plan, out_dir, synthesizer, force=args.force, pitch=pitch, provider=args.provider, model=model)
    )
    manifest_path = out_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    manifest.update(entries)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    rendered = sum(1 for key in entries if key not in failures)
    print(f"manifest: {manifest_path} ({len(manifest)} entries, {rendered} in this plan)")
    if failures:
        print(f"error: {len(failures)} clips failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

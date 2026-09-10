"""Convert any audio file into Bob's song slot: assets/songs/<name>.wav (16 kHz mono PCM).

Usage: uv run python scripts/add_song.py path/to/track.mp3 [--name banana_song]
Needs ffmpeg. Bob plays the first file (alphabetically) in assets/songs/ when asked to sing.
Only use audio you have the rights to play.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--name", default=None)
    parser.add_argument("--out", default="assets/songs")
    parser.add_argument("--start", type=float, default=0.0, help="seconds into the track to start")
    parser.add_argument("--max-seconds", type=float, default=10.0, help="clip length (Bob sings this much)")
    args = parser.parse_args(argv)
    src = Path(args.source)
    if not src.is_file():
        print(f"not a file: {src}")
        return 2
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found (brew install ffmpeg / apt install ffmpeg)")
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.name or src.stem}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-t",
        str(args.max_seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        "-af",
        "loudnorm",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB). Bob plays the first *.wav in {out_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

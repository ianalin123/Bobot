"""Download the OpenCV Zoo face models into models/vision/ and verify their sizes.

Uses the GitHub media endpoint (raw.githubusercontent.com only serves LFS pointers)
with the Hugging Face mirror as a fallback. Safe to re-run: existing files with the
right size are skipped.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

PRIMARY = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
MIRROR = "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/"

# name -> (relative path under models/, expected byte size, required)
MODELS: dict[str, tuple[str, int, bool]] = {
    "face_detection_yunet_2023mar.onnx": (
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        232_589,
        True,
    ),
    "face_recognition_sface_2021dec.onnx": (
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        38_696_353,
        True,
    ),
    "facial_expression_recognition_mobilefacenet_2022july.onnx": (
        "facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx",
        4_791_892,
        False,
    ),
}


def _download(url: str, dest: Path) -> int:
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "bob-fetch-models/1"})
    with urllib.request.urlopen(request, timeout=60) as response, tmp.open("wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    size = tmp.stat().st_size
    tmp.replace(dest)
    return size


def fetch(name: str, out_dir: Path) -> Path:
    rel, expected, _ = MODELS[name]
    dest = out_dir / name
    if dest.exists() and dest.stat().st_size == expected:
        print(f"ok       {name} ({expected:,} bytes, cached)")
        return dest
    errors: list[str] = []
    for base in (PRIMARY, MIRROR):
        url = base + rel
        try:
            print(f"fetching {url}", flush=True)
            size = _download(url, dest)
        except Exception as exc:  # network errors, HTTP errors
            errors.append(f"{url}: {exc}")
            continue
        if size == expected:
            print(f"ok       {name} ({size:,} bytes)")
            return dest
        errors.append(f"{url}: got {size:,} bytes, expected {expected:,}")
        dest.unlink(missing_ok=True)
    raise RuntimeError(f"could not fetch {name}:\n  " + "\n  ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="models/vision", help="destination directory")
    parser.add_argument("--no-emotion", action="store_true", help="skip the optional expression model")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, (_, _, required) in MODELS.items():
        if not required and args.no_emotion:
            continue
        try:
            fetch(name, out_dir)
        except RuntimeError as exc:
            print(("error    " if required else "warning  ") + str(exc), file=sys.stderr)
            failed = failed or required
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

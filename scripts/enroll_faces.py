"""Enroll people from `people/<Name>/*.jpg|png` into `people/embeddings.npz` (embeddings only, no images).

uv run python scripts/enroll_faces.py --people people --models models/vision --out people/embeddings.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bob.vision.faces import FaceEngine  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--people", default="people", help="directory with one sub-folder per person")
    parser.add_argument("--models", default="models/vision", help="directory holding the ONNX models")
    parser.add_argument("--out", default="people/embeddings.npz", help="where to write the embeddings")
    parser.add_argument("--threshold", type=float, default=0.363, help="cosine threshold (stored for info)")
    args = parser.parse_args(argv)

    people_dir = Path(args.people)
    if not people_dir.is_dir():
        print(f"error: {people_dir} is not a directory (expected people/<Name>/*.jpg)", file=sys.stderr)
        return 2
    try:
        engine = FaceEngine(args.models, threshold=args.threshold).load()
    except (FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    people = engine.enroll_dir(people_dir, out_path=args.out, log=lambda msg: print(msg, flush=True))
    if not people:
        print("error: nobody enrolled (no faces found)", file=sys.stderr)
        return 1
    print(f"wrote {args.out}: {len(people)} people ({', '.join(people)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

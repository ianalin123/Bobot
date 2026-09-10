"""Download aarch64 / CPython 3.12 wheels (or sdists) for a pinned requirements file, using PyPI's JSON API.

pip cannot download for a foreign platform once any pinned package lacks a wheel, so this picks
per package: a manylinux aarch64 cp312/abi3 wheel, else a pure-Python wheel, else the sdist.
Usage: python scripts/download_wheels.py requirements.txt dest_dir
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

PY = "cp312"
PLATFORM_RE = re.compile(r"manylinux[_0-9a-z]*aarch64|linux_aarch64")


def choose(files):
    wheels = [f for f in files if f["filename"].endswith(".whl") and not f.get("yanked")]

    def tags(f):
        parts = f["filename"][:-4].split("-")
        return parts[-3], parts[-2], parts[-1]  # python, abi, platform

    def score(f):
        py, abi, plat = tags(f)
        if PLATFORM_RE.search(plat) and (PY in py or "abi3" in abi):
            return 3
        if plat == "any" and ("py3" in py or PY in py):
            return 2
        return 0

    best = max(wheels, key=score, default=None)
    if best and score(best) > 0:
        return best
    sdists = [f for f in files if f["packagetype"] == "sdist"]
    return sdists[0] if sdists else None


def main(argv):
    req, dest = Path(argv[0]), Path(argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    missing = []
    for line in req.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split(";")[0].strip().split("==")
        name = name.split("[")[0].strip()
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
                info = json.load(r)
        except Exception as exc:
            missing.append(f"{name}=={version} ({exc})")
            continue
        chosen = choose(info["urls"])
        if not chosen:
            missing.append(f"{name}=={version} (no files)")
            continue
        out = dest / chosen["filename"]
        if out.exists() and out.stat().st_size == chosen["size"]:
            continue
        urllib.request.urlretrieve(chosen["url"], out)
        print(f"  {chosen['filename']}")
    if missing:
        print("!! not downloaded (Jetson needs network for these):\n   " + "\n   ".join(missing))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

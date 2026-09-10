"""Print the ReSpeaker XVF3800 direction of arrival at 5 Hz so you can map angles to the robot's
front. Usage: uv run --group robot python scripts/doa_calibrate.py [--seconds 30]"""

import argparse
import sys
import time
from pathlib import Path

RATE_HZ = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30.0, help="how long to print (default 30)")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bob.hardware import respeaker

    try:
        dev = respeaker.find()
    except ImportError:
        print("pyusb is not installed: run `uv sync --group robot`.", file=sys.stderr)
        return 2
    if dev is None:
        print(
            "ReSpeaker XVF3800 not found (USB 2886:001a). Check `lsusb`, and on Linux install the udev rule:\n"
            "  sudo cp scripts/udev/99-bob.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules"
            " && sudo udevadm trigger\n"
            "then replug the array.",
            file=sys.stderr,
        )
        return 1

    reader = respeaker.DoAReader(dev)
    period = 1.0 / RATE_HZ
    deadline = time.monotonic() + args.seconds
    last_error = None
    while time.monotonic() < deadline:
        started = time.monotonic()
        angle, speech = reader.read()
        print(f"angle={angle:03d} speech={speech}", flush=True)
        if reader.error != last_error:
            last_error = reader.error
            if reader.error:
                print(f"read error (showing last good value): {reader.error}", file=sys.stderr, flush=True)
        time.sleep(max(0.0, period - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Probe and preview Bob's USB camera on Linux/Jetson.

This deliberately has no Python package dependencies. It relies on the system
tools already used during bring-up: v4l2-ctl and gst-launch-1.0.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def require(command: str) -> str:
    path = shutil.which(command)
    if path is None:
        raise SystemExit(f"Missing {command!r}. Install the corresponding Linux package first.")
    return path


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(command), flush=True)
    return subprocess.run(command, check=check, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe or preview Bob's USB camera")
    parser.add_argument(
        "--device",
        default=os.environ.get("BOB_CAMERA_DEVICE", "/dev/video0"),
        help="V4L2 device (default: BOB_CAMERA_DEVICE or /dev/video0)",
    )
    parser.add_argument("--preview", action="store_true", help="Open a GStreamer live preview")
    args = parser.parse_args()

    v4l2ctl = require("v4l2-ctl")
    if not os.path.exists(args.device):
        raise SystemExit(f"Camera device does not exist: {args.device}")

    print(f"Camera device: {args.device}")
    run([v4l2ctl, "--device", args.device, "--all"])
    run([v4l2ctl, "--device", args.device, "--list-formats-ext"])

    if args.preview:
        gst = require("gst-launch-1.0")
        try:
            run(
                [
                    gst,
                    "v4l2src",
                    f"device={args.device}",
                    "!",
                    "videoconvert",
                    "!",
                    "autovideosink",
                ]
            )
        except KeyboardInterrupt:
            print("Preview stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

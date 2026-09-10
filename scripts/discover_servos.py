"""Scan the Feetech bus, print what answers and save calibration data.

Usage: uv run --group robot python scripts/discover_servos.py [--port /dev/ttyACM0] [--baud 1000000]
       [--ids 1-20] [--out config/calibration.json]

Scans at 1 Mbaud and falls back to 115200 when nothing answers (unless --baud is given). Prints a
table of id, model, position and voltage, then reads homing offset and position limits from EEPROM
into the calibration file. Never writes to a servo and never switches torque on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bob.hardware.feetech import (  # noqa: E402
    HOMING_OFFSET,
    MAX_POSITION_LIMIT,
    MIN_POSITION_LIMIT,
    BusError,
    FeetechBus,
)

DEFAULT_OUT = REPO / "config" / "calibration.json"
BAUD_CANDIDATES = (1_000_000, 115_200)


def parse_ids(spec: str) -> list[int]:
    """``"1-6,9"`` -> ``[1, 2, 3, 4, 5, 6, 9]``."""
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            ids.extend(range(lo, hi + 1))
        else:
            ids.append(int(part))
    return ids


def describe(bus, found: dict[int, dict]) -> dict[str, dict]:
    """Extend each scanned entry with the EEPROM values the arm and base rely on."""
    servos: dict[str, dict] = {}
    for motor in sorted(found):
        info = found[motor]
        servos[str(motor)] = {
            "model": int(info["model"]),
            "position": int(info["position"]),
            "voltage": float(info["voltage"]),
            "homing_offset": int(bus.read(motor, HOMING_OFFSET)),
            "min_position_limit": int(bus.read(motor, MIN_POSITION_LIMIT)),
            "max_position_limit": int(bus.read(motor, MAX_POSITION_LIMIT)),
        }
    return servos


def format_table(servos: dict[str, dict]) -> list[str]:
    header = (
        f"{'id':>3}  {'model':>6}  {'position':>8}  {'voltage':>7}  {'homing':>6}  {'min':>5}  {'max':>5}"
    )
    lines = [header, "-" * len(header)]
    for motor, s in servos.items():
        lines.append(
            f"{motor:>3}  {s['model']:>6}  {s['position']:>8}  {s['voltage']:>7.1f}  "
            f"{s['homing_offset']:>6}  {s['min_position_limit']:>5}  {s['max_position_limit']:>5}"
        )
    return lines


def _scan_real(port: str, bauds: tuple[int, ...], ids: list[int], print_fn: Callable[[str], None]):
    """Try each baudrate in turn; return ``(bus, found)`` for the first one with answers (bus left open).

    ``(None, {})`` when the port cannot be opened; ``(bus, {})`` (closed) when nothing answers.
    """
    bus = None
    for baud in bauds:
        bus = FeetechBus(port, baudrate=baud)
        try:
            bus.open()
        except BusError as exc:
            print_fn(str(exc))
            return None, {}
        found = bus.scan(ids)
        if found:
            return bus, found
        print_fn(f"No servos answered on {port} at {baud} baud.")
        bus.close()
    return bus, {}


def main(
    argv: list[str] | None = None,
    bus=None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default=os.environ.get("BOB_SERVO_PORT", "/dev/ttyACM0"))
    parser.add_argument(
        "--baud", type=int, default=None, help="serial baudrate (default: try 1000000 then 115200)"
    )
    parser.add_argument("--ids", default="1-20", help="ids to ping, e.g. 1-9 or 1,2,6 (default 1-20)")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"calibration file (default {DEFAULT_OUT})"
    )
    args = parser.parse_args(argv)
    ids = parse_ids(args.ids)

    owns_bus = bus is None
    if owns_bus:
        bauds = (args.baud,) if args.baud else BAUD_CANDIDATES
        bus, found = _scan_real(args.port, bauds, ids, print_fn)
        if bus is None:
            print_fn(
                "Check the USB cable, the 12 V supply and permissions (udev rule scripts/udev/99-bob.rules "
                "or membership of the dialout group)."
            )
            return 2
    else:
        if not bus.is_open:
            bus.open()
        found = bus.scan(ids)

    try:
        if not found:
            print_fn(f"No servos found on {bus.port} (ids {args.ids}). Nothing written.")
            return 1
        servos = describe(bus, found)
    finally:
        if owns_bus:
            bus.close()

    for line in format_table(servos):
        print_fn(line)
    arm = [m for m in servos if 1 <= int(m) <= 6]
    wheels = [m for m in servos if 7 <= int(m) <= 9]
    print_fn(f"Found {len(servos)} servo(s): arm {arm or 'none'}, wheels {wheels or 'none'}.")

    doc = {"port": bus.port, "baudrate": bus.baudrate, "servos": servos}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print_fn(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

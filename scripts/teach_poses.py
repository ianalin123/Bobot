"""Teach the SO-101 arm its banana poses by hand and save them to config/poses.json.

Usage: uv run --group robot python scripts/teach_poses.py [--port /dev/ttyACM0] [--baud 1000000]
       [--out config/poses.json] [--hold] [--squeeze 60]
       uv run --group robot python scripts/teach_poses.py --replay [--out config/poses.json]

Teaching switches torque OFF on IDs 1-6 so you can move the arm by hand. For each pose
(stow, above_cradle, grasp, lift, present, release) place the arm and press Enter. Then the gripper:
open it by hand (Enter), close it by hand around the banana in its cradle (Enter). The script then
energizes *only the gripper*, torque-capped, and squeezes a little further than your hand did while
sampling Present_Load three times 100 ms apart; the "holding" threshold is 60% of the mean.
Torque is left off at the end unless --hold is given.

--replay loads the saved file, switches torque on at a slow speed and moves through the poses one
Enter at a time (closing the gripper after `grasp`, opening it after `release`), then releases torque.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bob.hardware.arm import (  # noqa: E402
    ARM_IDS,
    GRIPPER_ID,
    POSE_NAMES,
    ArmHardware,
    Poses,
    cap_gripper_torque,
)
from bob.hardware.feetech import (  # noqa: E402
    GOAL_POSITION,
    GOAL_VELOCITY,
    PRESENT_LOAD,
    PRESENT_POSITION,
    BusError,
    FeetechBus,
)

DEFAULT_OUT = REPO / "config" / "poses.json"
REPLAY_SPEED = 200  # raw Goal_Velocity for --replay, well below the 600 used live
LOAD_SAMPLES = 3
LOAD_INTERVAL_S = 0.1
SETTLE_BEFORE_LOAD_S = 0.5
THRESHOLD_FRACTION = 0.6

Print = Callable[[str], None]
Input = Callable[[str], str]
SleepFn = Callable[[float], None]


def read_positions(bus, ids: list[int]) -> dict[int, int]:
    present = bus.sync_read(PRESENT_POSITION, ids)
    missing = [i for i in ids if i not in present]
    if missing:
        raise BusError(f"motor(s) {missing} did not answer; check power and cabling")
    return {i: int(present[i]) for i in ids}


def fmt(positions: dict[int, int]) -> str:
    return "  ".join(f"{i}:{v}" for i, v in positions.items())


def sample_load(bus, sleep_fn: SleepFn) -> list[int]:
    samples: list[int] = []
    for k in range(LOAD_SAMPLES):
        if k:
            sleep_fn(LOAD_INTERVAL_S)
        samples.append(abs(int(bus.read(GRIPPER_ID, PRESENT_LOAD))))
    return samples


def teach(bus, args, input_fn: Input, print_fn: Print, sleep_fn: SleepFn) -> int:
    ids = list(ARM_IDS)
    bus.torque(ids, False)
    print_fn("Torque is OFF on IDs 1-6: the arm will sag, support it. Move it by hand to each pose.")

    named: dict[str, dict[int, int]] = {}
    for name in POSE_NAMES:
        input_fn(f"[{name}] place the arm, then press Enter: ")
        named[name] = read_positions(bus, ids)
        print_fn(f"  {name}: {fmt(named[name])}")

    input_fn("[gripper open] open the gripper fully by hand, then press Enter: ")
    gripper_open = read_positions(bus, [GRIPPER_ID])[GRIPPER_ID]
    print_fn(f"  gripper_open: {gripper_open}")

    input_fn("[gripper closed] close the gripper by hand around the banana in its cradle, then press Enter: ")
    closed_by_hand = read_positions(bus, [GRIPPER_ID])[GRIPPER_ID]
    print_fn(f"  gripper closed by hand: {closed_by_hand}")

    direction = (closed_by_hand > gripper_open) - (closed_by_hand < gripper_open)
    gripper_closed = closed_by_hand + direction * args.squeeze
    gripper_closed = max(0, min(4095, gripper_closed))
    if direction == 0:
        print_fn("  warning: open and closed positions are equal; no squeeze applied.")

    input_fn(
        "[load] keep hands clear: the gripper alone will be energized (torque capped) and squeeze the "
        "banana for about a second. Press Enter: "
    )
    cap_gripper_torque(bus)
    bus.write(GRIPPER_ID, GOAL_POSITION, closed_by_hand)  # no jump when torque comes on
    bus.torque([GRIPPER_ID], True)
    try:
        bus.write(GRIPPER_ID, GOAL_VELOCITY, REPLAY_SPEED)
        bus.write(GRIPPER_ID, GOAL_POSITION, gripper_closed)
        sleep_fn(SETTLE_BEFORE_LOAD_S)
        samples = sample_load(bus, sleep_fn)
    finally:
        bus.torque([GRIPPER_ID], False)
    mean = sum(samples) / len(samples)
    threshold = max(1, round(mean * THRESHOLD_FRACTION))
    print_fn(f"  load samples {samples} -> mean {mean:.0f}, hold threshold {threshold}")
    if mean == 0:
        print_fn(
            "  WARNING: no load measured while squeezing; grasp verification will always fail. "
            "Check the banana is in the jaws and re-teach, or raise --squeeze."
        )

    poses = Poses(
        named=named,
        gripper_open=gripper_open,
        gripper_closed=gripper_closed,
        hold_load_threshold=threshold,
        ids=ids,
    )
    poses.save(args.out)
    print_fn(f"Wrote {args.out}")

    if args.hold:
        bus.sync_write(GOAL_POSITION, read_positions(bus, ids))  # hold where it is, no jump
        bus.torque(ids, True)
        print_fn("Torque is ON (--hold): the arm holds its current position.")
    else:
        print_fn("Torque is OFF. Support the arm or rest it in the stow pose.")
    return 0


def replay(bus, args, input_fn: Input, print_fn: Print, sleep_fn: SleepFn) -> int:
    try:
        poses = Poses.load(args.out)
    except (OSError, ValueError) as exc:
        print_fn(f"Cannot load {args.out}: {exc}")
        return 1

    async def sleep(seconds: float) -> None:
        sleep_fn(seconds)

    arm = ArmHardware(bus, poses, speed=REPLAY_SPEED, sleep=sleep)
    print_fn(f"Replaying {args.out} slowly (Goal_Velocity {REPLAY_SPEED}). Keep clear of the arm.")
    input_fn("Press Enter to energize the arm (it will hold its current position): ")
    arm.enable()
    try:
        for name in POSE_NAMES:
            input_fn(f"Press Enter to move to '{name}': ")
            asyncio.run(arm.goto(name))
            print_fn(f"  at {name}: {fmt(read_positions(bus, arm.joint_ids))}")
            if name == "grasp":
                asyncio.run(arm.close_gripper())
                print_fn(f"  gripper closed, load {abs(bus.read(GRIPPER_ID, PRESENT_LOAD))}")
            elif name == "release":
                asyncio.run(arm.open_gripper())
                print_fn("  gripper open")
    except (TimeoutError, RuntimeError) as exc:
        print_fn(f"Replay stopped: {exc}")
        asyncio.run(arm.stop())
        return 1
    finally:
        if args.hold:
            print_fn("Torque stays ON (--hold).")
        else:
            arm.disable()
            print_fn("Torque is OFF.")
    return 0


def main(
    argv: list[str] | None = None,
    bus=None,
    input_fn: Input = input,
    print_fn: Print = print,
    sleep_fn: SleepFn = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default=os.environ.get("BOB_SERVO_PORT", "/dev/ttyACM0"))
    parser.add_argument("--baud", type=int, default=1_000_000)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"poses file (default {DEFAULT_OUT})")
    parser.add_argument("--hold", action="store_true", help="leave torque ON at the end")
    parser.add_argument("--replay", action="store_true", help="move slowly through the saved poses")
    parser.add_argument(
        "--squeeze", type=int, default=60, help="raw counts to close beyond the hand-taught grip (default 60)"
    )
    args = parser.parse_args(argv)

    owns_bus = bus is None
    if owns_bus:
        bus = FeetechBus(args.port, baudrate=args.baud)
    try:
        if not bus.is_open:
            bus.open()
    except BusError as exc:
        print_fn(str(exc))
        return 2
    try:
        if args.replay:
            return replay(bus, args, input_fn, print_fn, sleep_fn)
        return teach(bus, args, input_fn, print_fn, sleep_fn)
    except BusError as exc:
        print_fn(f"Bus error: {exc}")
        return 2
    finally:
        if owns_bus:
            bus.close()


if __name__ == "__main__":
    sys.exit(main())

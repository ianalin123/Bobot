"""SO-101 arm adapter: taught poses, interpolated moves and grasp verification.

The arm is six STS3215 servos (IDs 1-5 joints, 6 gripper) on the bus shared with the base.
Nothing here energizes a motor implicitly: ``ArmHardware.enable()`` is the only place torque is
switched on, and ``perform`` refuses to move until it has been called (and again after ``stop``).

Motion is position mode with a soft interpolation loop: every tick (~20 ms) the goal of each joint is
moved at most ``max_step`` raw counts from where the joint *actually is* toward the target, and the
move completes when every joint reads within ``settle_tolerance`` of the target (or ``timeout`` ->
``TimeoutError``). Named poses drive the five joints only; the gripper is driven exclusively by the
open/close steps because once a banana is in the jaws the taught gripper value is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bob.hardware.feetech import (
    ACCELERATION,
    GOAL_POSITION,
    GOAL_VELOCITY,
    LOCK,
    MAX_TORQUE_LIMIT,
    MAXIMUM_ACCELERATION,
    PRESENT_LOAD,
    PRESENT_POSITION,
)

ARM_IDS: list[int] = [1, 2, 3, 4, 5, 6]
JOINT_IDS: list[int] = [1, 2, 3, 4, 5]
GRIPPER_ID = 6
POSE_NAMES: tuple[str, ...] = ("stow", "above_cradle", "grasp", "lift", "present", "release")
GRIPPER_TORQUE_CAP = 500  # of 1000: the gripper must never crush a banana (or a finger)
POSITION_MIN, POSITION_MAX = 0, 4095

ACTIONS: tuple[str, ...] = (
    "open_compartment",
    "grasp_banana",
    "present_banana",
    "release_banana",
    "stow_arm",
    "close_compartment",
)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class GraspFailed(RuntimeError):
    """The gripper closed but the load never showed a banana being held."""


# --- poses --------------------------------------------------------------------


def _position(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value:
        raise ValueError(f"{where}: position must be an integer, got {value!r}")
    value = int(value)
    if not POSITION_MIN <= value <= POSITION_MAX:
        raise ValueError(f"{where}: position {value} outside {POSITION_MIN}..{POSITION_MAX}")
    return value


@dataclass(frozen=True)
class Poses:
    """Taught positions: ``named[pose][motor_id] -> raw`` plus gripper values and the hold threshold."""

    named: dict[str, dict[int, int]]
    gripper_open: int
    gripper_closed: int
    hold_load_threshold: int  # sign-stripped |Present_Load| that means "holding the banana"
    ids: list[int] = field(default_factory=lambda: list(ARM_IDS))

    @classmethod
    def from_dict(cls, doc: Mapping) -> Poses:
        if not isinstance(doc, Mapping):
            raise ValueError("poses document must be a JSON object")
        ids = [int(i) for i in doc.get("ids", ARM_IDS)]
        raw_poses = doc.get("poses")
        if not isinstance(raw_poses, Mapping):
            raise ValueError("poses document is missing the 'poses' object")
        named: dict[str, dict[int, int]] = {}
        for name in POSE_NAMES:
            pose = raw_poses.get(name)
            if not isinstance(pose, Mapping):
                raise ValueError(f"pose '{name}' is missing")
            values = {int(k): v for k, v in pose.items()}
            missing = [i for i in ids if i not in values]
            if missing:
                raise ValueError(f"pose '{name}' is missing motor(s) {missing}")
            named[name] = {i: _position(values[i], f"pose '{name}' motor {i}") for i in ids}
        for key in ("gripper_open", "gripper_closed", "hold_load_threshold"):
            if key not in doc:
                raise ValueError(f"poses document is missing '{key}'")
        threshold = doc["hold_load_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            raise ValueError("hold_load_threshold must be a number")
        return cls(
            named=named,
            gripper_open=_position(doc["gripper_open"], "gripper_open"),
            gripper_closed=_position(doc["gripper_closed"], "gripper_closed"),
            hold_load_threshold=abs(int(threshold)),
            ids=ids,
        )

    @classmethod
    def load(cls, path: str | Path) -> Poses:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def to_dict(self) -> dict:
        return {
            "ids": list(self.ids),
            "poses": {name: {str(i): v for i, v in pose.items()} for name, pose in self.named.items()},
            "gripper_open": self.gripper_open,
            "gripper_closed": self.gripper_closed,
            "hold_load_threshold": self.hold_load_threshold,
        }

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


# --- bus helpers --------------------------------------------------------------


def cap_gripper_torque(bus, motor: int = GRIPPER_ID, cap: int = GRIPPER_TORQUE_CAP) -> None:
    """Lower the gripper's EEPROM ``Max_Torque_Limit`` to ``cap`` if it is higher.

    Must run while torque is off: ``bus.torque(on=True)`` sets LOCK=1 which blocks EEPROM writes.
    Only writes when needed so the EEPROM is not worn on every start.
    """
    if bus.read(motor, MAX_TORQUE_LIMIT) > cap:
        bus.write(motor, LOCK, 0)
        bus.write(motor, MAX_TORQUE_LIMIT, cap)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


# --- adapter ------------------------------------------------------------------


class ArmHardware:
    """``Hardware`` adapter for the SO-101 arm; see the module docstring for the motion model."""

    name = "arm"

    def __init__(
        self,
        bus,
        poses: Poses,
        speed: int = 600,
        acceleration: int = 30,
        max_step: int = 150,
        settle_tolerance: int = 25,
        timeout: float = 6.0,
        *,
        tick_s: float = 0.02,
        load_samples: int = 3,
        load_interval: float = 0.1,
        torque_off_on_stop: bool = False,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ):
        self.bus = bus
        self.poses = poses
        self.speed = int(speed)
        self.acceleration = int(acceleration)
        self.max_step = int(max_step)
        self.settle_tolerance = int(settle_tolerance)
        self.timeout = float(timeout)
        self.tick_s = float(tick_s)
        self.load_samples = int(load_samples)
        self.load_interval = float(load_interval)
        self.torque_off_on_stop = torque_off_on_stop
        self.sleep = sleep
        self.clock = clock
        self.ids = list(poses.ids)
        self.joint_ids = [i for i in self.ids if i != GRIPPER_ID]
        self.enabled = False
        self.estopped = False

    # -- lifecycle -----------------------------------------------------------

    def enable(self) -> None:
        """Open the bus if needed, cap gripper torque, switch torque on and set motion parameters.

        The only place motors get energized. Also clears an e-stop.
        """
        if not self.bus.is_open:
            self.bus.open()
        cap_gripper_torque(self.bus)
        self.bus.torque(self.ids, True)
        params = {i: self.acceleration for i in self.ids}
        self.bus.sync_write(ACCELERATION, params)
        self.bus.sync_write(MAXIMUM_ACCELERATION, params)
        self.bus.sync_write(GOAL_VELOCITY, {i: self.speed for i in self.ids})
        self.enabled = True
        self.estopped = False

    def disable(self) -> None:
        """Torque off (the arm may sag under gravity)."""
        self.bus.torque(self.ids, False)
        self.enabled = False

    # -- Hardware protocol ---------------------------------------------------

    async def perform(self, action: str) -> None:
        if self.estopped:
            raise RuntimeError("arm is e-stopped; call enable() to recover")
        if not self.enabled:
            raise RuntimeError("arm is not enabled; call enable() first")
        if action == "grasp_banana":
            await self.goto("above_cradle")
            await self.goto("grasp")
            await self.close_gripper()
            await self.verify_hold()
            await self.goto("lift")
        elif action == "present_banana":
            await self.goto("present")
        elif action == "release_banana":
            await self.open_gripper()
        elif action == "stow_arm":
            await self.goto("lift")
            await self.goto("stow")
        elif action in ("open_compartment", "close_compartment"):
            return  # no compartment actuator on this build
        else:
            raise ValueError(f"unknown arm action {action!r}")

    async def stop(self) -> None:
        """Freeze: write the present positions as goals and refuse further moves until ``enable()``."""
        self.estopped = True
        if not self.bus.is_open:
            return
        present = self.bus.sync_read(PRESENT_POSITION, self.ids)
        if present:
            self.bus.sync_write(GOAL_POSITION, present)
        if self.torque_off_on_stop:
            self.bus.torque(self.ids, False)
            self.enabled = False

    # -- moves ---------------------------------------------------------------

    async def goto(self, pose: str) -> None:
        """Interpolate the five joints to a named pose (the gripper is left alone)."""
        target = {i: self.poses.named[pose][i] for i in self.joint_ids}
        await self.move(target)

    async def open_gripper(self) -> None:
        await self.move({GRIPPER_ID: self.poses.gripper_open})

    async def close_gripper(self) -> None:
        """Close onto the banana: done when the jaws reach ``gripper_closed`` or the load says "holding"."""
        await self.move({GRIPPER_ID: self.poses.gripper_closed}, settle_load=self.poses.hold_load_threshold)

    async def verify_hold(self) -> None:
        """Require |Present_Load| >= threshold on every one of ``load_samples`` reads 100 ms apart."""
        samples: list[int] = []
        for k in range(self.load_samples):
            if k:
                await self.sleep(self.load_interval)
            samples.append(abs(int(self.bus.read(GRIPPER_ID, PRESENT_LOAD))))
        if min(samples) < self.poses.hold_load_threshold:
            raise GraspFailed(f"gripper load {samples} below hold threshold {self.poses.hold_load_threshold}")

    async def move(self, target: Mapping[int, int], settle_load: int | None = None) -> None:
        """Step every motor in ``target`` toward its goal, at most ``max_step`` per tick from its
        present position, until all are within ``settle_tolerance`` (or the gripper load reaches
        ``settle_load``), else ``TimeoutError``."""
        ids = list(target)
        deadline = self.clock() + self.timeout
        commanded = False  # the load shortcut only counts once a close goal has actually been sent
        while True:
            if self.estopped:
                raise RuntimeError("arm move aborted: e-stopped")
            present = self.bus.sync_read(PRESENT_POSITION, ids)
            missing = [i for i in ids if i not in present]
            if missing:
                raise RuntimeError(f"motor {missing[0]} did not answer during a move")
            if self._settled(present, target, settle_load if commanded else None):
                return
            goals = {
                i: _clamp(present[i] + _clamp(target[i] - present[i], -self.max_step, self.max_step), 0, 4095)
                for i in ids
            }
            self.bus.sync_write(GOAL_POSITION, goals)
            commanded = True
            await self.sleep(self.tick_s)
            if self.clock() > deadline:
                raise TimeoutError(
                    f"arm move to {dict(target)} did not settle within {self.timeout}s: {present}"
                )

    def _settled(
        self, present: Mapping[int, int], target: Mapping[int, int], settle_load: int | None
    ) -> bool:
        if all(abs(present[i] - target[i]) <= self.settle_tolerance for i in target):
            return True
        if settle_load is not None and GRIPPER_ID in target:
            return abs(int(self.bus.read(GRIPPER_ID, PRESENT_LOAD))) >= settle_load
        return False

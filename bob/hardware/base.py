"""LeKiwi three-wheel omni base: kinematics, velocity-mode driver and watchdog.

The base shares the Feetech bus with the SO-101 arm; wheels are STS3215 servos
with IDs 7 (left), 8 (back), 9 (right) run in velocity mode (``OPERATING_MODE``
1). Kinematics are the LeRobot LeKiwi formulas verbatim so calibration numbers
from LeRobot carry over. ``GOAL_VELOCITY`` is sign-magnitude on the wire; the
bus encodes it, so this module only deals in signed raw counts.

Frame: ``vx`` forward (m/s), ``vy`` left (m/s), ``omega_deg`` counter-clockwise
(deg/s). A positive bearing is to the left, so ``omega_for_bearing`` returns a
positive (CCW) rate to turn toward it.

Safety: ``Base`` never energizes motors until ``enable()``; ``drive`` clamps to
``max_v``/``max_omega``; the watchdog zeroes the wheels within ``watchdog_s`` of
the last command while moving; ``close()`` stops and drops torque and can be
called any number of times, even if the bus was never opened.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable

from bob.hardware.feetech import GOAL_VELOCITY, LOCK, OPERATING_MODE, BusError

log = logging.getLogger(__name__)

WHEEL_IDS: dict[str, int] = {"left": 7, "back": 8, "right": 9}
WHEEL_RADIUS = 0.05  # m
BASE_RADIUS = 0.125  # m, wheel contact point to base centre
MAX_RAW = 3000  # largest |GOAL_VELOCITY| we ever command (raw counts, 4096 per turn per second)
_RAW_LIMIT = 32767  # sign-magnitude 15-bit magnitude
_VELOCITY_MODE = 1

_WHEEL_ORDER = ("left", "back", "right")
# LeRobot LeKiwi: wheel mounting angles [240, 0, 120] deg (left, back, right), minus 90 so the
# row gives the wheel's tangential direction.
_WHEEL_ANGLES_RAD = tuple(math.radians(a - 90.0) for a in (240.0, 0.0, 120.0))
_KINEMATICS = tuple((math.cos(a), math.sin(a), BASE_RADIUS) for a in _WHEEL_ANGLES_RAD)


def wheel_raw_from_body(vx: float, vy: float, omega_deg: float) -> dict[int, int]:
    """Body velocity (m/s, m/s, deg/s) -> raw ``GOAL_VELOCITY`` per wheel id.

    LeRobot's formula: wheel_linear = m @ [vx, vy, omega_rad]; wheel_angular =
    wheel_linear / wheel_radius; raw = round(deg/s * 4096 / 360). If any |raw| exceeds
    ``MAX_RAW`` all three are scaled down proportionally (direction is preserved);
    the result is finally clamped to the 15-bit magnitude the register can hold.
    """
    body = (float(vx), float(vy), math.radians(float(omega_deg)))
    degps = []
    for row in _KINEMATICS:
        wheel_linear = sum(coef * v for coef, v in zip(row, body, strict=True))
        wheel_angular = wheel_linear / WHEEL_RADIUS
        degps.append(math.degrees(wheel_angular))
    raws = [degps_to_raw(d) for d in degps]
    peak = max(abs(r) for r in raws)
    if peak > MAX_RAW:
        scale = MAX_RAW / peak
        raws = [degps_to_raw(d * scale) for d in degps]
    return {
        WHEEL_IDS[name]: max(-_RAW_LIMIT, min(_RAW_LIMIT, raw))
        for name, raw in zip(_WHEEL_ORDER, raws, strict=True)
    }


def degps_to_raw(degps: float) -> int:
    """Degrees per second -> raw velocity counts (4096 counts per revolution)."""
    return int(round(degps * 4096.0 / 360.0))


def omega_for_bearing(
    bearing_deg: float, deadband_deg: float = 20.0, max_omega: float = 40.0, gain: float = 0.02
) -> float:
    """P controller for turning toward a bearing (deg, positive = left/CCW).

    Returns 0 inside ``±deadband_deg`` (inclusive), otherwise ``gain * bearing * max_omega``
    clamped to ``±max_omega``. With the defaults a 25° bearing gives 20 °/s and anything
    beyond 50° saturates at ``max_omega``.
    """
    bearing = float(bearing_deg)
    if abs(bearing) <= deadband_deg:
        return 0.0
    omega = gain * bearing * max_omega
    return max(-max_omega, min(max_omega, omega))


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


class Base:
    """Velocity-mode driver for the three LeKiwi wheels on a shared Feetech bus."""

    def __init__(
        self,
        bus,
        max_v: float = 0.15,
        max_omega: float = 40.0,
        watchdog_s: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.bus = bus
        self.max_v = float(max_v)
        self.max_omega = float(max_omega)
        self.watchdog_s = float(watchdog_s)
        self.clock = clock
        self.ids: tuple[int, ...] = tuple(WHEEL_IDS[name] for name in _WHEEL_ORDER)
        self.enabled = False
        self.closed = False
        self.moving = False
        self.last_cmd_t: float = clock()
        self.last_command: tuple[float, float, float] = (0.0, 0.0, 0.0)

    # -- lifecycle -----------------------------------------------------------

    def enable(self) -> None:
        """Put IDs 7-9 in velocity mode, then enable torque. This is the only call that energizes."""
        self.closed = False
        for motor in self.ids:
            # OPERATING_MODE is EEPROM; LOCK=1 (set by torque on) would silently block the write,
            # so unlock first and switch mode before torque comes on.
            self.bus.write(motor, LOCK, 0)
            self.bus.write(motor, OPERATING_MODE, _VELOCITY_MODE)
        self.bus.torque(self.ids, True)
        self.enabled = True
        self.last_cmd_t = self.clock()

    def close(self) -> None:
        """Stop and drop torque. Idempotent; safe when the bus was never opened."""
        if self.closed:
            return
        self.closed = True
        self.moving = False
        self.enabled = False
        if not getattr(self.bus, "is_open", True):
            return
        try:
            self.bus.sync_write(GOAL_VELOCITY, dict.fromkeys(self.ids, 0))
            self.bus.torque(self.ids, False)
        except BusError as exc:
            log.warning("base close: bus error ignored: %s", exc)

    # -- motion --------------------------------------------------------------

    def drive(self, vx: float, vy: float, omega_deg: float) -> dict[int, int]:
        """Command a body velocity (clamped to the limits) and refresh the watchdog."""
        if self.closed:
            return {}
        command = (_clamp(vx, self.max_v), _clamp(vy, self.max_v), _clamp(omega_deg, self.max_omega))
        raws = wheel_raw_from_body(*command)
        self.bus.sync_write(GOAL_VELOCITY, raws)
        self.last_command = command
        self.last_cmd_t = self.clock()
        self.moving = any(raws.values())
        return raws

    def stop(self) -> None:
        """Zero all three wheels."""
        if self.closed:
            return
        self.bus.sync_write(GOAL_VELOCITY, dict.fromkeys(self.ids, 0))
        self.last_command = (0.0, 0.0, 0.0)
        self.moving = False

    def turn_to(self, angle_deg: float, gain: float = 0.02, deadband_deg: float = 20.0) -> float:
        """Rotate in place toward a bearing with a P controller; returns the omega commanded (deg/s)."""
        omega = omega_for_bearing(angle_deg, deadband_deg=deadband_deg, max_omega=self.max_omega, gain=gain)
        if omega == 0.0:
            self.stop()
        else:
            self.drive(0.0, 0.0, omega)
        return omega

    # -- watchdog ------------------------------------------------------------

    def watchdog_step(self, now: float | None = None) -> bool:
        """One watchdog check: stop if moving and silent for longer than ``watchdog_s``.

        Returns True when this call stopped the base.
        """
        if not self.moving or self.closed:
            return False
        now = self.clock() if now is None else now
        if now - self.last_cmd_t > self.watchdog_s:
            log.warning("base watchdog: no command for %.2fs, stopping", now - self.last_cmd_t)
            self.stop()
            return True
        return False

    async def watchdog(self, period_s: float = 0.05) -> None:
        """Run ``watchdog_step`` every ``period_s`` until cancelled."""
        while True:
            self.watchdog_step(self.clock())
            await asyncio.sleep(period_s)

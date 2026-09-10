"""Tests for the LeKiwi base driver: kinematics, limits, watchdog and lifecycle."""

import asyncio
import math

import pytest

from bob.hardware.base import (
    BASE_RADIUS,
    MAX_RAW,
    WHEEL_IDS,
    WHEEL_RADIUS,
    Base,
    omega_for_bearing,
    wheel_raw_from_body,
)
from bob.hardware.feetech import FakeBus

LEFT, BACK, RIGHT = WHEEL_IDS["left"], WHEEL_IDS["back"], WHEEL_IDS["right"]


class Clock:
    """Manual monotonic clock for the watchdog tests."""

    def __init__(self, t: float = 100.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_base(**kw) -> tuple[Base, FakeBus, Clock]:
    bus = FakeBus(ids=range(1, 10))
    bus.open()
    clock = Clock()
    base = Base(bus, clock=clock, **kw)
    return base, bus, clock


def goal_velocity_writes(bus: FakeBus) -> list[tuple[int, str, int]]:
    return [w for w in bus.writes if w[1] == "GOAL_VELOCITY"]


# --- constants --------------------------------------------------------------


def test_constants_match_lekiwi():
    assert WHEEL_IDS == {"left": 7, "back": 8, "right": 9}
    assert WHEEL_RADIUS == 0.05
    assert BASE_RADIUS == 0.125
    assert MAX_RAW == 3000


# --- kinematics -------------------------------------------------------------


def test_zero_body_velocity_gives_zero_raws():
    assert wheel_raw_from_body(0.0, 0.0, 0.0) == {LEFT: 0, BACK: 0, RIGHT: 0}


def test_pure_rotation_gives_equal_magnitude_raws():
    raws = wheel_raw_from_body(0.0, 0.0, 30.0)
    assert set(raws) == {LEFT, BACK, RIGHT}
    values = list(raws.values())
    assert values[0] != 0
    assert values[0] == values[1] == values[2]
    # Hand-computed: 30 deg/s -> 0.5236 rad/s * 0.125 m / 0.05 m -> 1.309 rad/s -> 75 deg/s -> 853 raw
    expected = round(math.radians(30.0) * BASE_RADIUS / WHEEL_RADIUS * 180 / math.pi * 4096 / 360)
    assert values[0] == expected == 853


def test_negative_rotation_flips_sign():
    pos = wheel_raw_from_body(0.0, 0.0, 30.0)
    neg = wheel_raw_from_body(0.0, 0.0, -30.0)
    assert all(neg[i] == -pos[i] for i in pos)


def test_forward_drive_back_wheel_zero_and_sides_opposite():
    raws = wheel_raw_from_body(0.1, 0.0, 0.0)
    assert raws[BACK] == 0
    assert raws[LEFT] != 0 and raws[RIGHT] != 0
    assert raws[LEFT] == -raws[RIGHT]
    # cos(30 deg) * 0.1 m/s / 0.05 m -> 1.732 rad/s -> 99.24 deg/s -> 1129 raw
    assert abs(raws[RIGHT]) == 1129


def test_sideways_drive_uses_all_wheels():
    raws = wheel_raw_from_body(0.0, 0.1, 0.0)
    assert raws[BACK] != 0
    # sin(150) = sin(30) = 0.5, sin(-90) = -1: sides equal, back opposite and twice as large
    assert raws[LEFT] == raws[RIGHT]
    assert raws[BACK] == -2 * raws[LEFT]


def test_scaling_when_over_max_raw():
    raws = wheel_raw_from_body(1.0, 0.0, 0.0)  # unscaled ~11289 on the side wheels
    assert max(abs(v) for v in raws.values()) == MAX_RAW
    assert raws[BACK] == 0
    assert raws[LEFT] == -raws[RIGHT]


def test_scaling_preserves_wheel_ratios():
    small = wheel_raw_from_body(0.0, 0.05, 10.0)
    big = wheel_raw_from_body(0.0, 5.0, 1000.0)  # same direction, 100x faster
    assert max(abs(v) for v in big.values()) == MAX_RAW
    scale = max(abs(v) for v in big.values()) / max(abs(v) for v in small.values())
    for i in small:
        assert big[i] == pytest.approx(small[i] * scale, abs=2)


def test_raws_are_ints_within_int16():
    raws = wheel_raw_from_body(100.0, -100.0, 5000.0)
    for v in raws.values():
        assert isinstance(v, int)
        assert -32767 <= v <= 32767


# --- Base.enable ------------------------------------------------------------


def test_enable_sets_velocity_mode_before_torque():
    base, bus, _ = make_base()
    base.enable()
    for wheel in (LEFT, BACK, RIGHT):
        assert bus.registers[wheel]["OPERATING_MODE"] == 1
        assert bus.registers[wheel]["TORQUE_ENABLE"] == 1
        assert bus.registers[wheel]["LOCK"] == 1
        mode_idx = bus.writes.index((wheel, "OPERATING_MODE", 1))
        torque_idx = bus.writes.index((wheel, "TORQUE_ENABLE", 1))
        assert mode_idx < torque_idx
    # Arm motors are untouched.
    for arm in range(1, 7):
        assert bus.registers[arm]["TORQUE_ENABLE"] == 0
        assert bus.registers[arm]["OPERATING_MODE"] == 0
    assert base.enabled


# --- Base.drive / stop ------------------------------------------------------


def test_drive_writes_goal_velocity_and_marks_moving():
    base, bus, clock = make_base()
    base.enable()
    bus.writes.clear()
    base.drive(0.1, 0.0, 0.0)
    expected = wheel_raw_from_body(0.1, 0.0, 0.0)
    assert {w[0]: w[2] for w in goal_velocity_writes(bus)} == expected
    assert base.moving
    assert base.last_cmd_t == clock()


def test_drive_clamps_to_limits():
    base, bus, _ = make_base(max_v=0.15, max_omega=40.0)
    base.drive(5.0, -5.0, 999.0)
    clamped = wheel_raw_from_body(0.15, -0.15, 40.0)
    assert {w[0]: w[2] for w in goal_velocity_writes(bus)} == clamped
    assert base.last_command == (0.15, -0.15, 40.0)


def test_drive_zero_is_not_moving():
    base, _, _ = make_base()
    base.drive(0.0, 0.0, 0.0)
    assert not base.moving


def test_stop_zeroes_all_wheels():
    base, bus, _ = make_base()
    base.drive(0.1, 0.0, 10.0)
    bus.writes.clear()
    base.stop()
    writes = goal_velocity_writes(bus)
    assert sorted(writes) == [
        (LEFT, "GOAL_VELOCITY", 0),
        (BACK, "GOAL_VELOCITY", 0),
        (RIGHT, "GOAL_VELOCITY", 0),
    ]
    assert not base.moving
    for wheel in (LEFT, BACK, RIGHT):
        assert bus.registers[wheel]["GOAL_VELOCITY"] == 0


# --- watchdog ---------------------------------------------------------------


def test_watchdog_step_stops_after_silence():
    base, bus, clock = make_base(watchdog_s=0.3)
    base.drive(0.1, 0.0, 0.0)
    bus.writes.clear()
    clock.advance(0.2)
    assert base.watchdog_step(clock()) is False
    assert base.moving
    assert goal_velocity_writes(bus) == []
    clock.advance(0.2)  # 0.4 s since the last command
    assert base.watchdog_step(clock()) is True
    assert not base.moving
    assert {w[2] for w in goal_velocity_writes(bus)} == {0}


def test_watchdog_step_is_refreshed_by_drive():
    base, bus, clock = make_base(watchdog_s=0.3)
    for _ in range(10):
        base.drive(0.1, 0.0, 0.0)
        clock.advance(0.2)
        assert base.watchdog_step(clock()) is False
    assert base.moving


def test_watchdog_step_does_nothing_when_not_moving():
    base, bus, clock = make_base(watchdog_s=0.3)
    clock.advance(10.0)
    assert base.watchdog_step(clock()) is False
    assert goal_velocity_writes(bus) == []


async def test_watchdog_loop_stops_and_is_cancellable():
    base, bus, clock = make_base(watchdog_s=0.3)
    base.drive(0.1, 0.0, 0.0)
    task = asyncio.create_task(base.watchdog(period_s=0.001))
    await asyncio.sleep(0.02)
    assert base.moving  # clock has not advanced
    clock.advance(1.0)
    await asyncio.sleep(0.02)
    assert not base.moving
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


# --- close ------------------------------------------------------------------


def test_close_stops_and_disables_torque_idempotently():
    base, bus, _ = make_base()
    base.enable()
    base.drive(0.1, 0.0, 0.0)
    bus.writes.clear()
    base.close()
    assert not base.moving
    assert not base.enabled
    for wheel in (LEFT, BACK, RIGHT):
        assert bus.registers[wheel]["GOAL_VELOCITY"] == 0
        assert bus.registers[wheel]["TORQUE_ENABLE"] == 0
        assert bus.registers[wheel]["LOCK"] == 0
    n = len(bus.writes)
    base.close()
    base.close()
    assert len(bus.writes) == n


def test_close_is_safe_when_bus_not_open():
    bus = FakeBus(ids=range(1, 10))  # never opened
    base = Base(bus)
    base.close()
    base.close()
    assert bus.writes == []


def test_drive_after_close_is_ignored():
    base, bus, _ = make_base()
    base.close()
    bus.writes.clear()
    base.drive(0.1, 0.0, 0.0)
    assert bus.writes == []
    assert not base.moving


# --- omega_for_bearing / turn_to --------------------------------------------


@pytest.mark.parametrize("bearing", [0.0, 5.0, -5.0, 19.9, -19.9, 20.0, -20.0])
def test_omega_for_bearing_deadband(bearing):
    assert omega_for_bearing(bearing) == 0.0


def test_omega_for_bearing_sign_and_proportional():
    assert omega_for_bearing(30.0) > 0
    assert omega_for_bearing(-30.0) < 0
    assert omega_for_bearing(-30.0) == -omega_for_bearing(30.0)
    assert omega_for_bearing(25.0) == pytest.approx(0.02 * 25.0 * 40.0)  # 20 deg/s
    assert 0 < omega_for_bearing(25.0) < omega_for_bearing(40.0)


def test_omega_for_bearing_clamps_to_max_omega():
    assert omega_for_bearing(180.0) == 40.0
    assert omega_for_bearing(-180.0) == -40.0
    assert omega_for_bearing(180.0, max_omega=10.0) == 10.0
    assert omega_for_bearing(30.0, deadband_deg=45.0) == 0.0


def test_turn_to_drives_rotation_only():
    base, bus, _ = make_base(max_omega=40.0)
    omega = base.turn_to(90.0)
    assert omega == 40.0
    assert base.last_command == (0.0, 0.0, 40.0)
    raws = {w[0]: w[2] for w in goal_velocity_writes(bus)}
    assert raws == wheel_raw_from_body(0.0, 0.0, 40.0)
    assert base.moving
    bus.writes.clear()
    assert base.turn_to(5.0) == 0.0
    assert not base.moving
    assert {w[2] for w in goal_velocity_writes(bus)} == {0}

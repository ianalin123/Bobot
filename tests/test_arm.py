"""Tests for the SO-101 arm adapter, pose files and the discovery/teach scripts (all on FakeBus)."""

import json
import sys
from pathlib import Path

import pytest

from bob.hardware.arm import (
    ARM_IDS,
    GRIPPER_ID,
    GRIPPER_TORQUE_CAP,
    JOINT_IDS,
    POSE_NAMES,
    ArmHardware,
    GraspFailed,
    Poses,
)
from bob.hardware.feetech import (
    ACCELERATION,
    GOAL_POSITION,
    GOAL_VELOCITY,
    HOMING_OFFSET,
    LOCK,
    MAX_TORQUE_LIMIT,
    MAXIMUM_ACCELERATION,
    MIN_POSITION_LIMIT,
    PRESENT_LOAD,
    PRESENT_POSITION,
    TORQUE_ENABLE,
    FakeBus,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import discover_servos  # noqa: E402
import teach_poses  # noqa: E402

# Distinct values per pose so the order of goal writes is unambiguous.
POSE_TABLE = {
    "stow": {1: 2048, 2: 1000, 3: 3000, 4: 2048, 5: 2048, 6: 2600},
    "above_cradle": {1: 2300, 2: 1400, 3: 2700, 4: 2100, 5: 2000, 6: 2600},
    "grasp": {1: 2300, 2: 1600, 3: 2500, 4: 2200, 5: 2000, 6: 2600},
    "lift": {1: 2300, 2: 1300, 3: 2800, 4: 2100, 5: 2000, 6: 2100},
    "present": {1: 1800, 2: 1500, 3: 2600, 4: 2000, 5: 2048, 6: 2100},
    "release": {1: 1800, 2: 1500, 3: 2600, 4: 2000, 5: 2048, 6: 2600},
}


def pose_doc(**overrides) -> dict:
    doc = {
        "ids": list(ARM_IDS),
        "poses": {name: {str(i): v for i, v in pose.items()} for name, pose in POSE_TABLE.items()},
        "gripper_open": 2600,
        "gripper_closed": 2100,
        "hold_load_threshold": 120,
    }
    doc.update(overrides)
    return doc


def make_poses() -> Poses:
    return Poses.from_dict(pose_doc())


class Sim:
    """Injectable clock + sleep: each sleep advances the clock and (optionally) ticks the fake bus."""

    def __init__(self, bus: FakeBus, tick: bool = True):
        self.bus = bus
        self.tick = tick
        self.now = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.tick:
            self.bus.tick()

    def clock(self) -> float:
        return self.now


def make_arm(bus: FakeBus | None = None, tick: bool = True, **kwargs) -> tuple[ArmHardware, FakeBus, Sim]:
    bus = bus or FakeBus(ids=range(1, 10))
    sim = Sim(bus, tick=tick)
    arm = ArmHardware(bus, make_poses(), sleep=sim.sleep, clock=sim.clock, **kwargs)
    return arm, bus, sim


def goal_writes(bus: FakeBus, motor: int) -> list[int]:
    return [value for m, name, value in bus.writes if m == motor and name == "GOAL_POSITION"]


def reached_in_order(writes: list[int], values: list[int]) -> bool:
    """True when ``values`` appear in ``writes`` in that order (a repeated value may share an index,
    since a pose the joint already sits at produces no new write)."""
    at = 0
    for value in values:
        while at < len(writes) and writes[at] != value:
            at += 1
        if at == len(writes):
            return False
    return True


# --- Poses ------------------------------------------------------------------


def test_poses_load_roundtrip(tmp_path):
    path = tmp_path / "poses.json"
    path.write_text(json.dumps(pose_doc(hold_load_threshold=-120)))
    poses = Poses.load(path)
    assert poses.ids == [1, 2, 3, 4, 5, 6]
    assert set(poses.named) == set(POSE_NAMES)
    assert poses.named["grasp"] == POSE_TABLE["grasp"]
    assert poses.gripper_open == 2600 and poses.gripper_closed == 2100
    assert poses.hold_load_threshold == 120  # sign stripped
    assert Poses.from_dict(poses.to_dict()) == poses


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["poses"].pop("lift"), "lift"),
        (lambda d: d["poses"]["grasp"].pop("3"), "grasp"),
        (lambda d: d.pop("gripper_open"), "gripper_open"),
        (lambda d: d.pop("hold_load_threshold"), "hold_load_threshold"),
        (lambda d: d["poses"]["stow"].update({"1": 5000}), "stow"),
        (lambda d: d["poses"]["stow"].update({"1": "far"}), "stow"),
    ],
)
def test_poses_validate_required_keys(mutate, message):
    doc = pose_doc()
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        Poses.from_dict(doc)


def test_example_poses_file_loads():
    poses = Poses.load(Path(__file__).resolve().parents[1] / "config" / "poses.example.json")
    assert set(poses.named) == set(POSE_NAMES)
    assert poses.hold_load_threshold > 0


# --- enable / disable / safety ---------------------------------------------


def test_construction_never_touches_the_bus():
    arm, bus, _ = make_arm()
    assert arm.name == "arm"
    assert bus.writes == [] and not bus.is_open and not arm.enabled and not arm.estopped


def test_enable_sets_torque_motion_params_and_caps_gripper_torque():
    arm, bus, _ = make_arm()
    arm.enable()
    assert bus.is_open and arm.enabled and not arm.estopped
    for motor in ARM_IDS:
        assert bus.read(motor, TORQUE_ENABLE) == 1
        assert bus.read(motor, ACCELERATION) == 30
        assert bus.read(motor, MAXIMUM_ACCELERATION) == 30
        assert bus.read(motor, GOAL_VELOCITY) == 600
    assert bus.read(GRIPPER_ID, MAX_TORQUE_LIMIT) == GRIPPER_TORQUE_CAP == 500
    assert bus.read(1, MAX_TORQUE_LIMIT) == 1000  # only the gripper is capped
    # EEPROM cap happens before torque on (LOCK=0 while writing, LOCK=1 afterwards).
    names = [(m, n, v) for m, n, v in bus.writes if m == GRIPPER_ID and n in ("MAX_TORQUE_LIMIT", "LOCK")]
    assert names.index((GRIPPER_ID, "LOCK", 0)) < names.index((GRIPPER_ID, "MAX_TORQUE_LIMIT", 500))
    assert names.index((GRIPPER_ID, "MAX_TORQUE_LIMIT", 500)) < names.index((GRIPPER_ID, "LOCK", 1))
    assert bus.read(GRIPPER_ID, LOCK) == 1
    # Wheels (7-9) are never touched by the arm.
    assert all(m in ARM_IDS for m, _, _ in bus.writes)


def test_enable_does_not_rewrite_an_already_low_torque_cap():
    arm, bus, _ = make_arm()
    bus.set(GRIPPER_ID, MAX_TORQUE_LIMIT, 300)
    arm.enable()
    assert bus.read(GRIPPER_ID, MAX_TORQUE_LIMIT) == 300
    assert not any(n == "MAX_TORQUE_LIMIT" for _, n, _ in bus.writes)


def test_disable_turns_torque_off():
    arm, bus, _ = make_arm()
    arm.enable()
    arm.disable()
    assert not arm.enabled
    assert all(bus.read(m, TORQUE_ENABLE) == 0 for m in ARM_IDS)


async def test_perform_refuses_when_not_enabled():
    arm, bus, _ = make_arm()
    with pytest.raises(RuntimeError, match="enable"):
        await arm.perform("present_banana")
    assert goal_writes(bus, 1) == []


async def test_perform_rejects_unknown_action():
    arm, _, _ = make_arm()
    arm.enable()
    with pytest.raises(ValueError, match="unknown"):
        await arm.perform("dance")


# --- motion -------------------------------------------------------------------


async def test_present_moves_all_joints_to_pose_with_clamped_steps():
    arm, bus, sim = make_arm()
    arm.enable()
    await arm.perform("present_banana")
    present = bus.sync_read(PRESENT_POSITION, ARM_IDS)
    assert present == {**POSE_TABLE["present"], GRIPPER_ID: 2048}  # gripper only moves on open/close
    assert goal_writes(bus, GRIPPER_ID) == []
    for motor in JOINT_IDS:
        writes = goal_writes(bus, motor)
        assert writes[-1] == POSE_TABLE["present"][motor]
        previous = 2048
        for goal in writes:
            assert abs(goal - previous) <= arm.max_step
            previous = goal
    # tick period is ~20 ms and the move needed several ticks
    assert sim.sleeps and all(s == pytest.approx(0.02) for s in sim.sleeps)
    assert len(sim.sleeps) >= 2


async def test_noop_actions_write_nothing():
    arm, bus, _ = make_arm()
    arm.enable()
    before = len(bus.writes)
    await arm.perform("open_compartment")
    await arm.perform("close_compartment")
    assert len(bus.writes) == before


async def test_grasp_sequence_writes_goals_in_order_and_verifies_load():
    arm, bus, sim = make_arm()
    bus.set(GRIPPER_ID, PRESENT_LOAD, -300)  # holding: magnitude 300 >= threshold 120
    arm.enable()
    await arm.perform("grasp_banana")
    joint = goal_writes(bus, 2)
    assert reached_in_order(joint, [POSE_TABLE[n][2] for n in ("above_cradle", "grasp", "lift")])
    gripper = goal_writes(bus, GRIPPER_ID)
    assert gripper[-1] == 2100 and gripper == sorted(gripper, reverse=True)  # closes once, monotonically
    # the gripper closed after the grasp pose was reached and before the lift started
    close_at = bus.writes.index((GRIPPER_ID, "GOAL_POSITION", gripper[0]))
    joint_writes = [i for i, (m, n, _) in enumerate(bus.writes) if m == 2 and n == "GOAL_POSITION"]
    grasp_at = joint_writes[joint.index(POSE_TABLE["grasp"][2])]
    lift_at = joint_writes[joint.index(POSE_TABLE["lift"][2])]
    assert grasp_at < close_at < lift_at
    assert bus.sync_read(PRESENT_POSITION, ARM_IDS) == {**POSE_TABLE["lift"], GRIPPER_ID: 2100}
    # three load samples 100 ms apart
    assert sim.sleeps.count(0.1) == 2


async def test_grasp_fails_when_load_stays_zero():
    arm, bus, _ = make_arm()
    arm.enable()
    with pytest.raises(GraspFailed):
        await arm.perform("grasp_banana")
    # never lifted: joint 2 stayed at the grasp pose, gripper was commanded closed
    assert bus.read(2, PRESENT_POSITION) == POSE_TABLE["grasp"][2]
    assert goal_writes(bus, GRIPPER_ID)[-1] == 2100


async def test_grasp_fails_when_one_of_three_reads_is_low():
    arm, bus, sim = make_arm()
    arm.enable()
    loads = iter([300, 40, 300])

    async def sleep(seconds):
        await Sim.sleep(sim, seconds)
        if seconds == 0.1:
            bus.set(GRIPPER_ID, PRESENT_LOAD, next(loads))

    arm.sleep = sleep
    bus.set(GRIPPER_ID, PRESENT_LOAD, 300)
    with pytest.raises(GraspFailed):
        await arm.perform("grasp_banana")


async def test_gripper_close_settles_on_load_when_banana_blocks_the_jaws():
    """The banana stops the jaws short of ``gripper_closed``; a load above threshold counts as closed."""
    arm, bus, sim = make_arm(tick=False)
    arm.enable()

    async def sleep(seconds):
        await Sim.sleep(sim, seconds)
        for motor in ARM_IDS:
            goal = bus.read(motor, GOAL_POSITION)
            # gripper stalls 90 raw short of the closed position, other joints follow their goals
            bus.set(motor, PRESENT_POSITION, max(goal, 2190) if motor == GRIPPER_ID else goal)
        if bus.read(GRIPPER_ID, GOAL_POSITION) <= 2200:
            bus.set(GRIPPER_ID, PRESENT_LOAD, 250)

    arm.sleep = sleep
    await arm.perform("grasp_banana")
    assert bus.read(2, PRESENT_POSITION) == POSE_TABLE["lift"][2]


async def test_release_opens_gripper_and_stow_goes_via_lift():
    arm, bus, _ = make_arm()
    arm.enable()
    await arm.perform("release_banana")
    assert goal_writes(bus, GRIPPER_ID)[-1] == 2600
    assert goal_writes(bus, 1) == []  # only the gripper moved
    await arm.perform("stow_arm")
    joint = goal_writes(bus, 2)
    assert reached_in_order(joint, [POSE_TABLE["lift"][2], POSE_TABLE["stow"][2]])
    assert bus.sync_read(PRESENT_POSITION, ARM_IDS) == {**POSE_TABLE["stow"], GRIPPER_ID: 2600}


async def test_move_times_out_when_joints_never_settle():
    arm, bus, sim = make_arm(tick=False, timeout=0.5)
    arm.enable()
    with pytest.raises(TimeoutError):
        await arm.perform("present_banana")
    assert sim.now >= 0.5
    # goals were still clamped relative to the (stuck) present position
    assert all(abs(g - 2048) <= arm.max_step for g in goal_writes(bus, 1))


async def test_move_fails_when_a_motor_stops_answering():
    arm, bus, _ = make_arm()
    arm.enable()
    del bus.registers[5]  # joint 5 drops off the bus
    with pytest.raises(RuntimeError, match="5"):
        await arm.perform("present_banana")


# --- stop -------------------------------------------------------------------


async def test_stop_freezes_at_present_positions_and_blocks_until_enable():
    arm, bus, _ = make_arm()
    arm.enable()
    for motor in ARM_IDS:
        bus.set(motor, PRESENT_POSITION, 1500 + motor)
        bus.set(motor, GOAL_POSITION, 3000)
    await arm.stop()
    assert arm.estopped
    assert all(bus.read(m, GOAL_POSITION) == 1500 + m for m in ARM_IDS)
    assert all(bus.read(m, TORQUE_ENABLE) == 1 for m in ARM_IDS)  # holds, no gravity collapse
    with pytest.raises(RuntimeError, match="stop"):
        await arm.perform("present_banana")
    arm.enable()
    assert not arm.estopped
    await arm.perform("present_banana")
    assert bus.sync_read(PRESENT_POSITION, ARM_IDS) == {**POSE_TABLE["present"], GRIPPER_ID: 1506}


async def test_stop_interrupts_a_move_in_progress():
    arm, bus, sim = make_arm()
    arm.enable()

    async def sleep(seconds):
        await Sim.sleep(sim, seconds)
        await arm.stop()

    arm.sleep = sleep
    with pytest.raises(RuntimeError, match="stop"):
        await arm.perform("present_banana")
    writes = goal_writes(bus, 1)
    assert len(writes) == 2  # one interpolation step, then the freeze written by stop()
    assert writes[-1] == bus.read(1, PRESENT_POSITION)


async def test_stop_can_release_torque_when_configured():
    arm, bus, _ = make_arm(torque_off_on_stop=True)
    arm.enable()
    await arm.stop()
    assert all(bus.read(m, TORQUE_ENABLE) == 0 for m in ARM_IDS)


async def test_stop_before_enable_is_safe():
    arm, bus, _ = make_arm()
    await arm.stop()
    assert arm.estopped and bus.writes == []


# --- discover_servos.py -------------------------------------------------------


def test_discover_writes_calibration(tmp_path):
    bus = FakeBus(ids=[1, 2, 6, 7])
    bus.set(1, HOMING_OFFSET, -12)
    bus.set(6, MIN_POSITION_LIMIT, 1800)
    out = tmp_path / "calibration.json"
    lines: list[str] = []
    rc = discover_servos.main(["--out", str(out)], bus=bus, print_fn=lines.append)
    assert rc == 0
    doc = json.loads(out.read_text())
    assert sorted(int(k) for k in doc["servos"]) == [1, 2, 6, 7]
    assert doc["servos"]["1"]["homing_offset"] == -12
    assert doc["servos"]["6"]["min_position_limit"] == 1800
    assert doc["servos"]["6"]["max_position_limit"] == 4095
    assert doc["servos"]["1"] | {} == {
        "model": 777,
        "position": 2048,
        "voltage": 12.0,
        "homing_offset": -12,
        "min_position_limit": 0,
        "max_position_limit": 4095,
    }
    assert doc["baudrate"] == bus.baudrate and doc["port"] == "fake"
    text = "\n".join(lines)
    assert "777" in text and "12.0" in text and "6" in text
    assert bus.writes == []  # discovery never energizes or writes


def test_discover_reports_nothing_found(tmp_path):
    bus = FakeBus(ids=[])
    lines: list[str] = []
    rc = discover_servos.main(["--out", str(tmp_path / "c.json")], bus=bus, print_fn=lines.append)
    assert rc == 1
    assert not (tmp_path / "c.json").exists()
    assert any("No servos" in line for line in lines)


# --- teach_poses.py -----------------------------------------------------------


def scripted_positions(bus: FakeBus):
    """Simulate the operator moving the arm by hand: each Enter sets new present positions."""
    steps = iter(list(POSE_TABLE) + ["open", "closed", "load"])

    def input_fn(prompt: str) -> str:
        step = next(steps)
        assert step.split("_")[0] in prompt or step in prompt  # prompts are in teach order
        if step == "load":
            pass  # the script energizes the gripper and squeezes; nothing to place
        elif step == "open":
            bus.set(GRIPPER_ID, PRESENT_POSITION, 2600)
        elif step == "closed":
            bus.set(GRIPPER_ID, PRESENT_POSITION, 2100)
            bus.set(GRIPPER_ID, PRESENT_LOAD, -200)
        else:
            for motor, value in POSE_TABLE[step].items():
                bus.set(motor, PRESENT_POSITION, value)
        return ""

    return input_fn


def test_teach_records_poses_and_threshold(tmp_path):
    bus = FakeBus(ids=range(1, 10))
    out = tmp_path / "poses.json"
    lines: list[str] = []
    rc = teach_poses.main(
        ["--out", str(out)],
        bus=bus,
        input_fn=scripted_positions(bus),
        print_fn=lines.append,
        sleep_fn=lambda s: None,
    )
    assert rc == 0
    poses = Poses.load(out)
    assert poses.named == POSE_TABLE
    assert poses.gripper_open == 2600
    assert poses.hold_load_threshold == 120  # 60% of |−200| over 3 samples
    assert poses.gripper_closed <= 2100  # closes onto the banana, never opens past the hand-taught point
    # torque was switched off first and left off at the end
    first_torque = next((m, n, v) for m, n, v in bus.writes if n == "TORQUE_ENABLE")
    assert first_torque[2] == 0
    assert all(bus.read(m, TORQUE_ENABLE) == 0 for m in ARM_IDS)
    assert bus.read(GRIPPER_ID, MAX_TORQUE_LIMIT) <= GRIPPER_TORQUE_CAP
    assert all(m in ARM_IDS for m, _, _ in bus.writes)


def test_teach_hold_leaves_torque_on(tmp_path):
    bus = FakeBus(ids=range(1, 10))
    out = tmp_path / "poses.json"
    rc = teach_poses.main(
        ["--out", str(out), "--hold"],
        bus=bus,
        input_fn=scripted_positions(bus),
        print_fn=lambda *_: None,
        sleep_fn=lambda s: None,
    )
    assert rc == 0
    assert all(bus.read(m, TORQUE_ENABLE) == 1 for m in ARM_IDS)


def test_teach_warns_when_no_load_measured(tmp_path):
    bus = FakeBus(ids=range(1, 10))
    out = tmp_path / "poses.json"
    lines: list[str] = []

    def input_fn(prompt):
        return ""

    rc = teach_poses.main(
        ["--out", str(out)], bus=bus, input_fn=input_fn, print_fn=lines.append, sleep_fn=lambda s: None
    )
    assert rc == 0
    assert Poses.load(out).hold_load_threshold >= 1
    assert any("load" in line.lower() and "warn" in line.lower() for line in lines)


def test_teach_replay_moves_through_poses(tmp_path):
    bus = FakeBus(ids=range(1, 10))
    out = tmp_path / "poses.json"
    out.write_text(json.dumps(pose_doc()))
    prompts: list[str] = []

    def input_fn(prompt):
        prompts.append(prompt)
        return ""

    rc = teach_poses.main(
        ["--out", str(out), "--replay"],
        bus=bus,
        input_fn=input_fn,
        print_fn=lambda *_: None,
        sleep_fn=lambda s: bus.tick(),
    )
    assert rc == 0
    assert "energize" in prompts[0]  # explicit confirmation before torque comes on
    assert len(prompts) == len(POSE_NAMES) + 1
    assert all(name in prompt for name, prompt in zip(POSE_NAMES, prompts[1:], strict=True))
    joint = goal_writes(bus, 2)
    assert reached_in_order(joint, [POSE_TABLE[n][2] for n in POSE_NAMES])
    gripper = goal_writes(bus, GRIPPER_ID)
    assert reached_in_order(gripper, [2100, 2600])  # closed after grasp, opened after release
    assert all(bus.read(m, GOAL_VELOCITY) < 600 for m in ARM_IDS)  # slow replay
    assert all(bus.read(m, TORQUE_ENABLE) == 0 for m in ARM_IDS)  # torque released at the end


def test_teach_replay_without_poses_file(tmp_path):
    lines: list[str] = []
    rc = teach_poses.main(
        ["--out", str(tmp_path / "missing.json"), "--replay"], bus=FakeBus(), print_fn=lines.append
    )
    assert rc == 1
    assert any("missing.json" in line for line in lines)

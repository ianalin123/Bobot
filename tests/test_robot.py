import asyncio

import pytest

from bob.robot import Robot, SimHardware


async def test_delivery_requires_acknowledgements_and_recipient():
    robot = Robot(SimHardware(0.001))
    assert (await robot.command("offer_banana"))["accepted"]
    assert not (await robot.command("offer_banana"))["accepted"]
    assert robot.state.banana == "compartment"
    assert not (await robot.command("take_banana"))["accepted"]
    await robot.task
    assert (robot.state.phase, robot.state.banana, robot.state.deliveries) == ("waiting", "hand", 0)
    assert (await robot.command("take_banana"))["accepted"]
    assert robot.state.banana == "hand"
    await robot.task
    assert (robot.state.phase, robot.state.banana, robot.state.deliveries) == ("idle", "delivered", 1)
    assert not (await robot.command("offer_banana"))["accepted"]
    assert (await robot.command("reload"))["accepted"]


@pytest.mark.parametrize("phase", ["opening", "grasping", "presenting", "releasing", "returning", "closing"])
async def test_stop_cancels_each_phase_without_fake_completion(phase):
    class Paused(SimHardware):
        async def perform(self, action):
            if robot.state.phase == phase:
                reached.set()
                await asyncio.Event().wait()

    reached = asyncio.Event()
    robot = Robot(Paused())
    await robot.offer()
    if phase in {"releasing", "returning", "closing"}:
        await robot.task
        await robot.take()
    await asyncio.wait_for(reached.wait(), 1)
    banana, deliveries = robot.state.banana, robot.state.deliveries
    await robot.stop()
    assert robot.state.stopped
    assert robot.task.cancelled()
    assert (robot.state.banana, robot.state.deliveries) == (banana, deliveries)
    assert not (await robot.offer())["accepted"]
    assert (await robot.command("reset_simulation"))["accepted"]
    assert robot.state.phase == "idle" and not robot.state.stopped


async def test_timeout_stops_and_does_not_claim_grasp():
    robot = Robot(SimHardware(1), timeout=0.005)
    await robot.offer()
    await robot.task
    assert robot.state.stopped and "TimeoutError" in robot.state.error
    assert robot.state.banana == "compartment"


async def test_hardware_cannot_be_reset_by_simulation_control():
    class Hardware(SimHardware):
        name = "test-hardware"

    robot = Robot(Hardware())
    assert not (await robot.command("reset_simulation"))["accepted"]


async def test_stop_failure_is_visible():
    class Broken(SimHardware):
        async def stop(self):
            raise OSError("not connected")

    robot = Robot(Broken())
    assert not (await robot.stop())["stop_confirmed"]
    assert "unconfirmed" in robot.state.error

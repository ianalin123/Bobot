"""Authoritative behavior. Only an adapter can acknowledge physical actions."""

import asyncio
from dataclasses import asdict, dataclass
from typing import Protocol


class Hardware(Protocol):
    name: str

    async def perform(self, action: str) -> None: ...
    async def stop(self) -> None: ...


class SimHardware:
    name = "sim"

    def __init__(self, delay: float = 0.75):
        self.delay = delay

    async def perform(self, action: str) -> None:
        await asyncio.sleep(self.delay)

    async def stop(self) -> None:
        pass


@dataclass
class State:
    phase: str = "idle"
    banana: str = "compartment"
    stopped: bool = False
    deliveries: int = 0
    expression: str = "curious"
    error: str | None = None


class Robot:
    def __init__(self, hardware: Hardware, timeout: float = 8):
        self.hardware = hardware
        self.timeout = timeout
        self.state = State()
        self.task: asyncio.Task | None = None
        self.events = ["Runtime ready. Simulated hardware." if hardware.name == "sim" else "Runtime ready."]

    def snapshot(self):
        return {**asdict(self.state), "hardware": self.hardware.name, "events": self.events[:16]}

    def log(self, text):
        self.events.insert(0, text)
        self.events = self.events[:40]

    async def step(self, action, phase):
        self.state.phase = phase
        self.log(phase.replace("_", " ").capitalize())
        await asyncio.wait_for(self.hardware.perform(action), self.timeout)

    async def guard(self, sequence):
        try:
            await sequence()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.stopped = True
            self.state.error = f"Action failed: {type(exc).__name__}"
            self.log(self.state.error)
            try:
                await asyncio.wait_for(self.hardware.stop(), self.timeout)
            except Exception:
                self.log("Hardware stop was not acknowledged; manual intervention required.")

    async def offer(self):
        if self.state.stopped or self.state.phase != "idle" or self.state.banana != "compartment":
            return {"accepted": False, "reason": "Robot stopped, busy, or banana not loaded."}
        # Set the busy state synchronously before scheduling: no duplicate offers.
        self.state.phase = "opening"

        async def sequence():
            await self.step("open_compartment", "opening")
            await self.step("grasp_banana", "grasping")
            self.state.banana = "hand"
            await self.step("present_banana", "presenting")
            self.state.phase = "waiting"
            self.log("Waiting for recipient confirmation.")

        self.task = asyncio.create_task(self.guard(sequence))
        return {"accepted": True, "status": "started", "hardware": self.hardware.name}

    async def take(self):
        if self.state.stopped or self.state.phase != "waiting":
            return {"accepted": False, "reason": "No banana ready for handoff."}
        self.state.phase = "releasing"

        async def sequence():
            await self.step("release_banana", "releasing")
            self.state.banana = "delivered"
            self.state.deliveries += 1
            await self.step("stow_arm", "returning")
            await self.step("close_compartment", "closing")
            self.state.phase = "idle"

        self.task = asyncio.create_task(self.guard(sequence))
        return {"accepted": True}

    async def stop(self):
        self.state.stopped = True
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(self.hardware.stop(), self.timeout)
        except Exception:
            self.state.error = "Hardware stop unconfirmed. Manual intervention required."
        self.log("Motion stopped. Explicit recovery required.")
        return {"accepted": True, "stop_confirmed": self.state.error is None}

    async def command(self, action):
        if action == "offer_banana":
            return await self.offer()
        if action == "take_banana":
            return await self.take()
        if action == "stop_motion":
            return await self.stop()
        if action == "reset_simulation" and self.hardware.name == "sim":
            await self.stop()
            self.state = State(deliveries=self.state.deliveries)
            self.log("Manual simulator recovery: arm stowed, banana loaded.")
            return {"accepted": True}
        if action == "reload" and not self.state.stopped and self.state.phase == "idle":
            self.state.banana = "compartment"
            self.log("Manual reload confirmed.")
            return {"accepted": True}
        return {"accepted": False, "reason": "Unknown command or invalid state."}

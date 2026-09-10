"""Bob's runtime server: one FastAPI process that wires the Robot state machine, the voice
pipeline and the Director to either simulated devices (``BOB_MODE=sim``, the Mac workbench) or
the real hardware (``BOB_MODE=cloud|local`` with ``BOB_HARDWARE=real``, the Jetson).

Startup never moves a motor: the Feetech bus opens lazily and ``arm.enable()`` only runs on an
explicit console ``enable_arm`` message or ``POST /api/arm/enable``. Any device that fails to open
is logged and swapped for its fake so the server still comes up; ``/api/health`` reports each one.

WebSocket clients come in two roles: at most one *workbench* (the default, ``/`` and ``/eyes``)
and any number of *consoles* (``?role=console``, the phone e-stop/teleop page at ``/console``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio import FakeAudioIO, RobotClient
from .config import Settings
from .director import Console, Director, PhrasePlayer
from .hardware.eyes import EyeState, FakeEyes
from .hardware.respeaker import FakeDoA
from .persona import STOCK_PHRASES
from .providers import MAX_AUDIO_BYTES, LocalLLM, LocalSTT, LocalTTS
from .robot import Robot, SimHardware, State
from .vision.camera import FakeCamera
from .vision.faces import Person, Tracker
from .voice import VoiceSession

log = logging.getLogger("bob.server")

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]", "testserver"]
WEB_ASSETS = {"app.mjs", "audio.mjs", "vad.mjs", "mic-worklet.js", "styles.css"}
EYES_ASSETS = {"eyes.mjs", "expressions.mjs"}
NO_STORE = {"Cache-Control": "no-store"}

DIRECTOR_HZ = 10
VISION_HZ = 10
WS_TICK_S = 0.05
ARM_TIMEOUT_S = 0.6  # dead-man: the console must refresh ``armed`` within this window
EYES_OVERRIDE_S = 5.0  # a client-pushed eye state is mirrored this long before the Director resumes
TELEOP_MAX_V = 0.15  # m/s
TELEOP_MAX_OMEGA = 40.0  # deg/s
MAX_MESSAGE_BYTES = 1_400_000
ROLES = ("workbench", "console", "viewer")


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _is_missing(exc: Exception) -> bool:
    """Absent device (unplugged, no file) rather than a broken one."""
    if isinstance(exc, FileNotFoundError):
        return True
    return (
        re.search(r"not found|\bno\b.*\bfound\b|no such|does not exist|missing", str(exc).lower()) is not None
    )


def _flag(data: dict, key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


# --- device factories (module attributes so tests can monkeypatch them) --------------------


def make_bus(settings: Settings):
    """The shared Feetech bus. Constructing it does not open the port; ``enable`` does."""
    from .hardware.feetech import FeetechBus

    return FeetechBus(settings.servo_port)


def make_arm(settings: Settings, bus):
    from .hardware.arm import ArmHardware, Poses

    return ArmHardware(bus, Poses.load(_path(settings.poses_path)))


def make_base(settings: Settings, bus):
    from .hardware.base import Base

    return Base(bus)


def make_doa(settings: Settings):
    from .hardware.respeaker import DoAReader

    reader = DoAReader()
    if not reader.find():
        raise RuntimeError(reader.error or "ReSpeaker XVF3800 not found")
    return reader


def make_eyes(settings: Settings):
    from .hardware.eyes import Eyes

    eyes = Eyes(settings.eye_ports)
    eyes.open()
    if not eyes.connected:
        raise RuntimeError(f"no eye boards found on {', '.join(settings.eye_ports)}")
    return eyes


def make_camera(settings: Settings):
    from .vision.camera import Camera

    return Camera(settings.camera_index).open()


def make_faces(settings: Settings):
    from .vision.faces import FaceEngine

    engine = FaceEngine(_path(settings.models_dir), settings.face_threshold, settings.emotion_enabled)
    engine.load()
    embeddings = _path(settings.people_dir) / "embeddings.npz"
    if embeddings.exists():
        engine.load_embeddings(embeddings)
    return engine


def make_audio(settings: Settings):
    from .audio import AudioIO

    return AudioIO(settings)


def make_providers(settings: Settings):
    if settings.mode == "sim":
        return LocalSTT(), LocalLLM(), LocalTTS()
    from .providers_cloud import build_providers

    return build_providers(settings)


# --- helpers ----------------------------------------------------------------------------------


class CachedPhrasePlayer(PhrasePlayer):
    """PhrasePlayer that also finds the ``phrase_<key>.wav`` names written by scripts/render_phrases.py."""

    async def say(self, text_or_key: str) -> str:
        if text_or_key in STOCK_PHRASES and self._play is not None:
            for name in (f"{text_or_key}.wav", f"phrase_{text_or_key}.wav"):
                path = self.phrases_dir / name
                if path.exists():
                    result = self._play(path)
                    if asyncio.iscoroutine(result):
                        await result
                    return path.name
        return await super().say(text_or_key)


class _BaseGate:
    """What the Director drives: wheels energize on the first drive while armed, never before."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def drive(self, vx: float, vy: float, omega: float) -> None:
        self.runtime.drive(vx, vy, omega)

    def stop(self) -> None:
        self.runtime.stop_base()


def _opened(device):
    device.open()
    return device


# --- runtime ----------------------------------------------------------------------------------


class Runtime:
    """Everything the server owns: robot, providers, director, devices and console state."""

    def __init__(self, settings: Settings, robot: Robot | None = None):
        self.settings = settings
        self.real = settings.real_hardware
        self.clock: Callable[[], float] = time.monotonic
        self.devices: dict[str, str] = {}
        self.console = Console()
        self.arm_enabled = False
        self.arm_timeout_s = ARM_TIMEOUT_S
        self._armed_until = 0.0
        self.persons: list[Person] = []
        self.doa_value: tuple[int, bool] = (0, False)
        self.last_transcript = ""
        self.last_reply = ""
        self._eye_override: tuple[dict, float] | None = None
        self.connected = False  # the single workbench controller
        self.consoles = 0
        self.tasks: list[asyncio.Task] = []
        self._phrase_n = 0
        self._base_ready = False
        self._vision_stop = threading.Event()
        self._vision_thread: threading.Thread | None = None
        self._vision_errors = 0

        self.stt, self.llm, self.tts = make_providers(settings)
        self.readiness = self._initial_readiness()

        self.bus = self.arm = self.base = self.audio = self.client = self.camera = self.faces = None
        self.tracker = Tracker()
        hardware: Any = SimHardware()
        if self.real:
            self.bus = self._device("servo", lambda: make_bus(settings), lambda: None)
            if self.bus is not None:
                self.devices["servo"] = (
                    "closed (opens on arm enable)" if Path(settings.servo_port).exists() else "missing"
                )
            arm = self._device("arm", lambda: make_arm(settings, self.bus), lambda: None)
            if arm is not None:
                self.arm, hardware = arm, arm
                self.devices["arm"] = "disabled (enable from the console)"
            self.base = self._device("base", lambda: make_base(settings, self.bus), lambda: None)
            if self.base is not None:
                self.devices["base"] = "disabled (energizes on first armed drive)"
            self.eyes = self._device("eyes", lambda: make_eyes(settings), lambda: _opened(FakeEyes()))
            if settings.doa_enabled:
                self.doa = self._device("doa", lambda: make_doa(settings), FakeDoA)
            else:
                self.doa, self.devices["doa"] = FakeDoA(), "disabled"
            self.camera = self._device("camera", lambda: make_camera(settings), FakeCamera)
            self.faces = self._device("faces", lambda: make_faces(settings), lambda: None)
            self.audio = self._device("audio", lambda: make_audio(settings), FakeAudioIO)
            if self.devices["audio"] == "ok":
                self.devices["audio"] = "pending"
        else:
            self.eyes = _opened(FakeEyes())
            self.doa = FakeDoA()
            self.camera = FakeCamera()
            self.devices.update(
                servo="sim", arm="sim", base="sim", eyes="fake", doa="fake", camera="fake", faces="off"
            )
            self.devices["audio"] = "browser"

        self.robot = robot if robot is not None else Robot(hardware)
        self.phrases = CachedPhrasePlayer(
            self._say_phrase,
            play=self._play_phrase if self.audio is not None else None,
            phrases_dir=_path(settings.phrases_dir),
        )
        self.director = Director(
            self.robot,
            self.eyes,
            self.phrases,
            settings,
            base=_BaseGate(self) if self.base is not None else None,
            console=self.console,
            clock=self.clock,
        )
        if self.audio is not None:
            self.client = RobotClient(self._make_session, self.audio)
            self.client.on_event = self.observe

    # -- construction helpers -------------------------------------------------------------

    def _initial_readiness(self) -> dict:
        settings = self.settings
        if settings.mode == "cloud":
            engine = "openai" + (" + local fallback" if settings.local_fallback else "")
            return {
                "llm": {"ready": True, "model": settings.llm_model, "provider": "openai"},
                "stt": {"ready": True, "model": settings.stt_model, "provider": engine},
                "tts": {"ready": True, "engine": f"{engine}:{settings.tts_voice}"},
            }
        return {
            "llm": {"ready": False},
            "stt": {"ready": False},
            "tts": {"ready": self.tts.ready(), "engine": self.tts.kind},
        }

    def _device(self, name: str, factory: Callable[[], Any], fallback: Callable[[], Any]):
        try:
            device = factory()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:120]}"
            self.devices[name] = ("missing: " if _is_missing(exc) else "error: ") + reason
            log.warning("%s unavailable, using the fake: %s", name, reason)
            return fallback()
        self.devices[name] = "ok"
        return device

    def _make_session(self, send):
        return VoiceSession(send, self.stt, self.llm, self.tts, self.robot, on_tool=self.director.on_tool)

    def session_for(self, send) -> VoiceSession:
        """A browser-driven VoiceSession whose events also feed the Director and the status panel."""

        async def observed(message):
            self.observe(message)
            await send(message)

        return self._make_session(observed)

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        if self.settings.mode in ("sim", "local"):
            self.tasks.append(asyncio.create_task(self._warm()))
        if self.audio is not None:
            try:
                self.audio.start()
                if self.devices.get("audio") == "pending":
                    self.devices["audio"] = "ok"
            except Exception as exc:
                self.devices["audio"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
                log.warning("audio unavailable: %s", exc)
        if self.faces is not None:
            self._vision_stop.clear()
            self._vision_thread = threading.Thread(target=self._vision_loop, name="vision", daemon=True)
            self._vision_thread.start()
        if self.base is not None and hasattr(self.base, "watchdog"):
            self.tasks.append(asyncio.create_task(self._guarded(self.base.watchdog(), "base watchdog")))
        self.tasks.append(asyncio.create_task(self._director_loop()))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks.clear()
        self._vision_stop.set()
        if self._vision_thread is not None:
            self._vision_thread.join(timeout=2)
            self._vision_thread = None
        for device in (self.audio, self.camera, self.eyes):
            if device is not None:
                with contextlib.suppress(Exception):
                    device.close()
        self.stop_base()
        if self.base is not None and hasattr(self.base, "close"):
            with contextlib.suppress(Exception):
                self.base.close()
        await self.robot.stop()
        if self.bus is not None:
            with contextlib.suppress(Exception):
                self.bus.close()

    async def _warm(self) -> None:
        try:
            self.readiness["llm"] = await self.llm.check()
        except Exception as exc:
            self.readiness["llm"] = {"ready": False, "model": self.llm.model, "error": str(exc)[:150]}
        try:
            await asyncio.to_thread(self.stt.load)
            self.readiness["stt"] = {"ready": True, "model": self.stt.model_name}
        except Exception:
            self.readiness["stt"] = {
                "ready": False,
                "error": "Download models first: uv run python -m bob.setup_models",
            }

    async def _guarded(self, coro, what: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s stopped", what)

    # -- director loop --------------------------------------------------------------------

    async def _director_loop(self) -> None:
        period = 1.0 / DIRECTOR_HZ
        while True:
            started = self.clock()
            try:
                await self._tick(started)
            except Exception:
                log.exception("director tick failed")
            await asyncio.sleep(max(0.0, period - (self.clock() - started)))

    async def _tick(self, now: float) -> None:
        if self.console.armed and now > self._armed_until:
            log.info("console dead-man expired; disarming")
            self.set_armed(False)
        if self.doa_threaded:
            self.doa_value = await asyncio.to_thread(self.doa.read)
        else:
            self.doa_value = self.doa.read()
        await self.director.tick(list(self.persons), self.doa_value, now)
        flush = getattr(self.eyes, "flush", None)
        if flush is not None:
            flush()

    @property
    def doa_threaded(self) -> bool:
        return not isinstance(self.doa, FakeDoA)

    def _vision_loop(self) -> None:
        period = 1.0 / VISION_HZ
        while not self._vision_stop.is_set():
            started = time.monotonic()
            frame = self.camera.latest()
            persons: list[Person] = []
            if frame is not None:
                try:
                    persons = self.tracker.update(self.faces.detect(frame))
                except Exception as exc:
                    self._vision_errors += 1
                    if self._vision_errors <= 3:
                        log.warning("face detection failed: %s", exc)
            self.persons = persons
            self._vision_stop.wait(max(0.0, period - (time.monotonic() - started)))

    # -- conversation events --------------------------------------------------------------

    def observe(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "transcript":
            self.last_transcript = str(event.get("text", ""))[:300]
        elif kind == "turn_started":
            self.last_reply = ""
        elif kind == "assistant_delta":
            self.last_reply = (self.last_reply + str(event.get("text", "")))[-400:]
        self.director.handle_event(event)

    async def _say_phrase(self, text: str) -> None:
        self.robot.log(f"Bob says: {text}")
        if self.audio is None:
            return
        wav = await self.tts.synthesize(text)
        self._phrase_n += 1
        self.audio.play(f"phrase:{self._phrase_n}", wav)

    def _play_phrase(self, path: Path) -> None:
        self.robot.log(f"Bob plays {path.name}")
        self._phrase_n += 1
        self.audio.play(f"phrase:{self._phrase_n}", path.read_bytes())

    # -- console: e-stop, dead-man, arm ---------------------------------------------------

    async def handle_console(self, data: dict) -> dict | None:
        handled, result = False, None
        if "estop" in data:
            await self.set_estop(_flag(data, "estop"))
            handled = True
        if "armed" in data:
            self.set_armed(_flag(data, "armed"))
            handled = True
        if "enable_arm" in data:
            result = await (self.enable_arm() if _flag(data, "enable_arm") else self.disable_arm())
            handled = True
        if not handled:
            raise ValueError("console message needs estop, armed or enable_arm")
        return result

    async def set_estop(self, on: bool) -> None:
        self.console.estop = on
        if on:
            self.console.armed = False
            self.stop_base()
            await self.robot.stop()

    def set_armed(self, on: bool) -> None:
        if on and self.console.estop:
            return
        if on:
            self._armed_until = self.clock() + self.arm_timeout_s
        elif self.console.armed:
            self.stop_base()
        self.console.armed = on

    async def enable_arm(self) -> dict:
        if not self.real or self.arm is None:
            result = await self.robot.command("reset_simulation")
            self.arm_enabled = bool(result.get("accepted"))
            return {**result, "hardware": self.robot.hardware.name}
        try:
            await asyncio.to_thread(self._enable_arm_sync)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:120]}"
            self.devices["arm"] = f"error: {reason}"
            self.arm_enabled = False
            log.warning("arm enable failed: %s", reason)
            return {"accepted": False, "reason": reason}
        self.arm_enabled = True
        self.devices["arm"] = "ok"
        if self.robot.state.stopped:
            self.robot.state = State(
                banana=self.robot.state.banana,
                deliveries=self.robot.state.deliveries,
                expression=self.robot.state.expression,
            )
        self.robot.log("Arm enabled. Operator confirmed recovery.")
        return {"accepted": True, "hardware": self.robot.hardware.name}

    def _enable_arm_sync(self) -> None:
        self._open_bus()
        self.arm.enable()

    async def disable_arm(self) -> dict:
        self.arm_enabled = False
        if self.real and self.arm is not None and hasattr(self.arm, "disable"):
            try:
                await asyncio.to_thread(self.arm.disable)
            except Exception as exc:
                return {"accepted": False, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
            self.devices["arm"] = "disabled (enable from the console)"
        return {"accepted": True, "hardware": self.robot.hardware.name}

    def _open_bus(self) -> None:
        if self.bus is None:
            return
        try:
            if not self.bus.is_open:
                self.bus.open()
        except Exception as exc:
            self.devices["servo"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
            raise
        self.devices["servo"] = "ok"

    # -- base ---------------------------------------------------------------------------

    def drive(self, vx: float, vy: float, omega: float) -> bool:
        """Drive the base only while the console dead-man is held and no e-stop is latched."""
        if self.base is None or self.console.estop or not self.console.armed:
            return False
        if not self._ensure_base():
            return False
        try:
            self.base.drive(vx, vy, omega)
        except Exception as exc:
            self.devices["base"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
            log.warning("base drive failed: %s", exc)
            return False
        return True

    def teleop(self, vx: Any, vy: Any, omega: Any) -> bool:
        command = (
            _clamp(_number(vx, "vx"), TELEOP_MAX_V),
            _clamp(_number(vy, "vy"), TELEOP_MAX_V),
            _clamp(_number(omega, "omega"), TELEOP_MAX_OMEGA),
        )
        return self.drive(*command)

    def stop_base(self) -> None:
        if self.base is None or not self._base_ready:
            return
        try:
            self.base.stop()
        except Exception as exc:
            log.warning("base stop failed: %s", exc)

    def _ensure_base(self) -> bool:
        if self._base_ready:
            return True
        try:
            self._open_bus()
            enable = getattr(self.base, "enable", None)
            if enable is not None:
                enable()
        except Exception as exc:
            self.devices["base"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
            log.warning("base enable failed: %s", exc)
            return False
        self._base_ready = True
        self.devices["base"] = "ok"
        return True

    # -- wire snapshots -------------------------------------------------------------------

    def override_eyes(self, data: dict) -> None:
        base = EyeState.from_dict(self.eye_wire())
        state = EyeState.from_dict(data, base=base).to_dict()
        self._eye_override = (state, self.clock() + EYES_OVERRIDE_S)

    def eye_wire(self) -> dict:
        if self._eye_override is not None:
            state, until = self._eye_override
            if self.clock() < until:
                return dict(state)
            self._eye_override = None
        return self.director.eye_state.to_dict()

    def person_wire(self) -> dict | None:
        person = self.director.engaged
        if person is None:
            return None
        return {
            "name": person.name,
            "cx": round(person.cx, 3),
            "cy": round(person.cy, 3),
            "size": round(person.size, 3),
            "emotion": person.emotion,
        }

    def console_wire(self) -> dict:
        return {"armed": self.console.armed, "estop": self.console.estop, "arm_enabled": self.arm_enabled}

    def status_wire(self) -> dict:
        return {
            "mode": self.settings.mode,
            "hardware": self.robot.hardware.name,
            "director": self.director.mode,
            "speaking": self.director.speaking,
            "transcript": self.last_transcript,
            "reply": self.last_reply,
        }

    def wire_events(self) -> list[tuple[str, dict]]:
        angle, speech = self.doa_value
        return [
            ("robot_state", {"state": self.robot.snapshot()}),
            ("eyes", {"state": self.eye_wire()}),
            ("person", {"person": self.person_wire()}),
            ("doa", {"angle": int(angle), "speech": bool(speech)}),
            ("console", self.console_wire()),
            ("status", self.status_wire()),
        ]

    def health(self) -> dict:
        return {
            "mode": self.settings.mode,
            "hardware": self.robot.hardware.name,
            **self.readiness,
            "devices": dict(self.devices),
            "console": self.console_wire(),
            "director": self.director.mode,
        }

    def origin_ok(self, origin: str | None, host: str | None) -> bool:
        if not origin:
            return True
        parsed = urlparse(origin)
        schemes = ("http", "https") if self.settings.console_token else ("http",)
        return parsed.scheme in schemes and parsed.netloc == host


# --- app --------------------------------------------------------------------------------------


def build_app(settings: Settings | None = None, *, robot: Robot | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    runtime = Runtime(settings, robot=robot)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.runtime = runtime
    hosts = ["*"] if settings.console_token else list(LOOPBACK_HOSTS)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    def check_token(token: str) -> None:
        if settings.console_token and token != settings.console_token:
            raise HTTPException(403, "Console token missing or wrong.")

    @app.get("/api/health")
    async def health():
        return runtime.health()

    @app.post("/api/arm/enable")
    async def arm_enable(token: str = ""):
        check_token(token)
        return await runtime.enable_arm()

    @app.post("/api/arm/disable")
    async def arm_disable(token: str = ""):
        check_token(token)
        return await runtime.disable_arm()

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "index.html", headers=NO_STORE)

    @app.get("/console")
    async def console_page(token: str = ""):
        check_token(token)
        return FileResponse(ROOT / "web" / "console.html", headers=NO_STORE)

    @app.get("/assets/{filename}")
    async def asset(filename: str):
        # An allowlist prevents serving .env, models, .git or arbitrary project files.
        if filename not in WEB_ASSETS:
            raise HTTPException(404)
        return FileResponse(ROOT / "web" / filename, headers=NO_STORE)

    @app.get("/eyes")
    async def eyes_page():
        return FileResponse(ROOT / "web" / "eyes-sim" / "index.html", headers=NO_STORE)

    @app.get("/assets/eyes-sim/{filename}")
    async def eyes_asset(filename: str):
        if filename not in EYES_ASSETS:
            raise HTTPException(404)
        return FileResponse(ROOT / "web" / "eyes-sim" / filename, headers=NO_STORE)

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await _serve_websocket(runtime, ws)

    return app


async def _serve_websocket(rt: Runtime, ws: WebSocket) -> None:
    settings = rt.settings
    if settings.console_token and ws.query_params.get("token") != settings.console_token:
        await ws.close(code=1008, reason="Console token missing or wrong.")
        return
    if not rt.origin_ok(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=1008)
        return
    role = ws.query_params.get("role", "workbench")
    if role not in ROLES:
        role = "workbench"
    controller = role == "workbench"
    if controller:
        if rt.connected:
            await ws.close(code=1013, reason="One controller at a time. Close the other Bob tab.")
            return
        rt.connected = True
    else:
        rt.consoles += 1
    try:
        await ws.accept()
    except Exception:
        if controller:
            rt.connected = False
        else:
            rt.consoles -= 1
        raise
    send_lock = asyncio.Lock()

    async def send(message):
        async with send_lock:
            await ws.send_json(message)

    session = rt.session_for(send) if controller else None

    async def updates():
        last: dict[str, dict] = {}
        while True:
            for kind, payload in rt.wire_events():
                if payload != last.get(kind):
                    last[kind] = payload
                    await send({"type": kind, **payload})
            await asyncio.sleep(WS_TICK_S)

    ticker = asyncio.create_task(updates())
    try:
        await send(
            {"type": "hello", "protocol": 1, "providers": rt.readiness, "role": role, "mode": settings.mode}
        )
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_MESSAGE_BYTES:
                await send({"type": "error", "message": "Message too large."})
                continue
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Message must be an object")
                await _handle_message(rt, session, send, data)
            except (ValueError, TypeError, AttributeError) as exc:
                await send({"type": "error", "message": str(exc)[:160]})
    except WebSocketDisconnect:
        pass
    finally:
        # ASGI disconnect can cancel its entire task scope. Cleanup must still reach
        # the driver stop, even if a voice/websocket operation fails along the way.
        with anyio.CancelScope(shield=True):
            try:
                if controller:
                    await rt.robot.stop()
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeError, WebSocketDisconnect):
                    await ticker
                if session is not None:
                    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                        await session.interrupt()
            finally:
                if controller:
                    rt.connected = False
                else:
                    rt.consoles -= 1
                    if rt.consoles <= 0:
                        rt.consoles = 0
                        rt.set_armed(False)


async def _handle_message(rt: Runtime, session: VoiceSession | None, send, data: dict) -> None:
    kind = data.get("type")
    request_id = data.get("request_id", 0)
    if type(request_id) is not int or request_id < 0:
        raise ValueError("Invalid request ID")
    if kind in {"interrupt", "user_text", "audio_turn", "playback_started", "playback_finished"}:
        if session is None:
            raise ValueError("Only the workbench can talk to Bob")
        if kind == "interrupt":
            await session.interrupt(request_id)
        elif kind == "user_text":
            text = data.get("text", "")
            if not isinstance(text, str) or len(text) > 2000:
                raise ValueError("Text must be at most 2000 characters")
            await session.start(request_id, text=text)
        elif kind == "audio_turn":
            if not rt.readiness["stt"]["ready"]:
                raise ValueError("Speech recognition is not ready")
            audio = base64.b64decode(data.get("wav", ""), validate=True)
            if len(audio) > MAX_AUDIO_BYTES:
                raise ValueError("Recording too large")
            await session.start(request_id, audio=audio)
        else:
            session.acknowledge(request_id, data.get("segment_id"), kind == "playback_finished")
    elif kind == "action":
        action = data.get("action")
        if action == "stop_motion":
            rt.stop_base()
        result = await rt.robot.command(action)
        await send({"type": "action_result", "result": result})
    elif kind == "expression":
        if data.get("expression") in {"curious", "happy", "sleepy"}:
            rt.robot.state.expression = data["expression"]
    elif kind == "eyes":
        # Sim/demo hook: the eyes page pushes a state and the ticker mirrors it for a few seconds.
        rt.override_eyes(data)
    elif kind == "console":
        result = await rt.handle_console(data)
        if result is not None:
            action = "enable_arm" if data.get("enable_arm") else "disable_arm"
            await send({"type": "action_result", "action": action, "result": result})
    elif kind == "drive":
        rt.teleop(data.get("vx", 0.0), data.get("vy", 0.0), data.get("omega", 0.0))
    else:
        raise ValueError("Unknown message type")


app = build_app()
runtime: Runtime = app.state.runtime

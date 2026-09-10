"""Server modes: sim vs real wiring with fakes, the phone console protocol and the doctor script."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bob import server
from bob.audio import FakeAudioIO
from bob.config import Settings
from bob.hardware.eyes import FakeEyes
from bob.hardware.feetech import FakeBus
from bob.hardware.respeaker import FakeDoA
from bob.robot import Robot, SimHardware
from bob.vision.camera import FakeCamera

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402

TOKEN = "s3cret"


class FakeArm:
    name = "fake-arm"

    def __init__(self):
        self.enabled = False
        self.enable_calls = 0

    def enable(self):
        self.enabled = True
        self.enable_calls += 1

    def disable(self):
        self.enabled = False

    async def perform(self, action):
        pass

    async def stop(self):
        pass


class FakeBase:
    def __init__(self):
        self.enabled = False
        self.drives: list[tuple[float, float, float]] = []
        self.stops = 0

    def enable(self):
        self.enabled = True

    def drive(self, vx, vy, omega):
        assert self.enabled, "drive before enable"
        self.drives.append((vx, vy, omega))

    def stop(self):
        self.stops += 1

    def close(self):
        pass


class FakeFaces:
    def detect(self, frame):
        return []


def opened_eyes():
    eyes = FakeEyes()
    eyes.open()
    return eyes


def make_app(monkeypatch, settings, **kwargs):
    app = server.build_app(settings, **kwargs)
    runtime = app.state.runtime

    async def ready():
        return {"ready": True, "model": "fake-local"}

    monkeypatch.setattr(runtime.llm, "check", ready)
    monkeypatch.setattr(runtime.stt, "load", lambda: None)
    return app


@pytest.fixture
def fakes(monkeypatch):
    """Every device factory returns a fake; the dict lets tests reach the instances."""
    made = {}

    def factory(name, build):
        def make(*args):
            made[name] = build()
            return made[name]

        return make

    monkeypatch.setattr(server, "make_bus", factory("bus", FakeBus))
    monkeypatch.setattr(server, "make_arm", factory("arm", FakeArm))
    monkeypatch.setattr(server, "make_base", factory("base", FakeBase))
    monkeypatch.setattr(server, "make_doa", factory("doa", FakeDoA))
    monkeypatch.setattr(server, "make_eyes", factory("eyes", opened_eyes))
    monkeypatch.setattr(server, "make_camera", factory("camera", FakeCamera))
    monkeypatch.setattr(server, "make_faces", factory("faces", FakeFaces))
    monkeypatch.setattr(server, "make_audio", factory("audio", FakeAudioIO))
    return made


def real_settings(**overrides):
    return Settings(mode="local", hardware="real", **overrides)


def receive_until(ws, predicate, limit=200):
    for _ in range(limit):
        message = ws.receive_json()
        if predicate(message):
            return message
    raise AssertionError("expected message never arrived")


def wait_for_console(ws, armed):
    return receive_until(ws, lambda m: m["type"] == "console" and m["armed"] is armed)


def sync(ws):
    """Round-trip a harmless action so every earlier message on this socket has been handled."""
    ws.send_json({"type": "action", "action": "no_such_action"})
    return receive_until(ws, lambda m: m["type"] == "action_result")


# --- sim mode --------------------------------------------------------------------------------


def test_sim_health_reports_fake_devices_and_console(monkeypatch):
    with TestClient(make_app(monkeypatch, Settings())) as client:
        data = client.get("/api/health").json()
    assert data["mode"] == "sim" and data["hardware"] == "sim"
    assert data["devices"]["eyes"] == "fake" and data["devices"]["doa"] == "fake"
    assert {"camera", "servo", "arm", "base", "audio"} <= set(data["devices"])
    assert data["console"] == {"armed": False, "estop": False, "arm_enabled": False}
    assert data["director"] == "idle"


def test_sim_console_page_open_without_token_and_streams_new_events(monkeypatch):
    with TestClient(make_app(monkeypatch, Settings())) as client:
        assert client.get("/console").status_code == 200
        with client.websocket_connect("/ws?role=console") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["role"] == "console" and hello["mode"] == "sim"
            seen = {}
            while len(seen) < 6:
                message = ws.receive_json()
                seen[message["type"]] = message
            assert set(seen) == {"robot_state", "eyes", "person", "doa", "console", "status"}
            assert seen["person"]["person"] is None
            assert seen["doa"] == {"type": "doa", "angle": 0, "speech": False}
            assert list(seen["eyes"]["state"]) == ["e", "gx", "gy", "blink", "p"]
            ws.send_json({"type": "user_text", "request_id": 1, "text": "hi"})
            assert "workbench" in receive_until(ws, lambda m: m["type"] == "error")["message"]


def test_console_does_not_count_as_the_single_workbench_controller(monkeypatch):
    app = make_app(monkeypatch, Settings(), robot=Robot(SimHardware(0.001)))
    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws") as workbench,
            client.websocket_connect("/ws?role=console") as console,
        ):
            assert workbench.receive_json()["role"] == "workbench"
            assert console.receive_json()["role"] == "console"
            with client.websocket_connect("/ws?role=console") as second:
                assert second.receive_json()["role"] == "console"
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws"):
                    pass
        assert not app.state.runtime.connected and app.state.runtime.consoles == 0


# --- console token -----------------------------------------------------------------------------


def test_console_token_rejects_and_accepts(monkeypatch):
    with TestClient(make_app(monkeypatch, Settings(console_token=TOKEN))) as client:
        assert client.get("/console").status_code == 403
        assert client.get("/console?token=wrong").status_code == 403
        assert client.get(f"/console?token={TOKEN}").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?role=console"):
                pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?role=console&token=wrong"):
                pass
        with client.websocket_connect(f"/ws?role=console&token={TOKEN}") as ws:
            assert ws.receive_json()["type"] == "hello"
        # With a token the LAN may connect: any host header and https origins on the same host.
        with client.websocket_connect(
            f"/ws?token={TOKEN}", headers={"origin": "https://testserver", "host": "testserver"}
        ) as ws:
            assert ws.receive_json()["role"] == "workbench"
        assert client.get("/api/health", headers={"host": "192.168.1.20:8766"}).status_code == 200
        assert client.post("/api/arm/enable").status_code == 403


def test_without_token_hosts_and_origins_stay_loopback_only(monkeypatch):
    with TestClient(make_app(monkeypatch, Settings())) as client:
        assert client.get("/api/health", headers={"host": "192.168.1.20:8766"}).status_code == 400
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"origin": "https://testserver"}):
                pass


# --- dead-man, e-stop, teleop, arm ---------------------------------------------------------------


def test_console_armed_times_out_and_disarms(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    runtime.arm_timeout_s = 0.05
    with TestClient(app) as client:
        with client.websocket_connect("/ws?role=console") as ws:
            wait_for_console(ws, False)
            ws.send_json({"type": "console", "armed": True})
            wait_for_console(ws, True)
            wait_for_console(ws, False)  # nobody refreshed it: the server let go
            assert not runtime.console.armed
    assert fakes["base"].drives == []


def test_drive_is_ignored_unless_armed(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    base = fakes["base"]
    with TestClient(app) as client:
        with client.websocket_connect("/ws?role=console") as ws:
            wait_for_console(ws, False)
            ws.send_json({"type": "drive", "vx": 0.1, "vy": 0.0, "omega": 0.0})
            sync(ws)
            assert base.drives == [] and not base.enabled and not fakes["bus"].is_open
            ws.send_json({"type": "console", "armed": True})
            ws.send_json({"type": "drive", "vx": 0.1, "vy": 0.0, "omega": 5.0})
            ws.send_json({"type": "drive", "vx": 9.0, "vy": -9.0, "omega": 900})  # clamped
            sync(ws)
            assert base.enabled and fakes["bus"].is_open
            assert base.drives == [(0.1, 0.0, 5.0), (0.15, -0.15, 40.0)]
            ws.send_json({"type": "drive", "vx": "fast"})
            assert "vx" in receive_until(ws, lambda m: m["type"] == "error")["message"]
            ws.send_json({"type": "console", "estop": True})
            wait_for_console(ws, False)
            assert runtime.console.estop and base.stops >= 1
            ws.send_json({"type": "console", "armed": True})
            ws.send_json({"type": "drive", "vx": 0.1, "vy": 0.0, "omega": 0.0})
            sync(ws)
            assert len(base.drives) == 2 and not runtime.console.armed  # e-stop wins
        assert not runtime.console.armed  # console gone -> disarmed


def test_enable_arm_calls_the_arm_and_recovers_the_robot(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    arm = fakes["arm"]
    with TestClient(app) as client:
        assert runtime.robot.hardware is arm and not arm.enabled and not fakes["bus"].is_open
        with client.websocket_connect("/ws?role=console") as ws:
            ws.send_json({"type": "action", "action": "stop_motion"})
            receive_until(ws, lambda m: m["type"] == "action_result")
            assert runtime.robot.state.stopped
            ws.send_json({"type": "console", "enable_arm": True})
            result = receive_until(ws, lambda m: m["type"] == "action_result" and m.get("action"))
            assert result["action"] == "enable_arm" and result["result"]["accepted"]
            assert arm.enable_calls == 1 and fakes["bus"].is_open
            assert not runtime.robot.state.stopped and runtime.robot.state.phase == "idle"
            assert receive_until(ws, lambda m: m["type"] == "console" and m["arm_enabled"])["armed"] is False
        response = client.post("/api/arm/enable")
        assert response.status_code == 200 and response.json()["accepted"] and arm.enable_calls == 2
        assert client.get("/api/health").json()["devices"]["arm"] == "ok"
        assert client.post("/api/arm/disable").json()["accepted"] and not arm.enabled
        assert not runtime.arm_enabled


def test_enable_arm_failure_is_reported_not_raised(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    arm = fakes["arm"]

    def boom():
        raise OSError("bus dead")

    arm.enable = boom
    with TestClient(app) as client:
        result = client.post("/api/arm/enable").json()
        assert not result["accepted"] and "bus dead" in result["reason"]
        assert client.get("/api/health").json()["devices"]["arm"].startswith("error:")


# --- real-mode construction ----------------------------------------------------------------------


def test_real_mode_with_fakes_serves_health(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    with TestClient(app) as client:
        data = client.get("/api/health").json()
        assert runtime.client is not None and fakes["audio"].started
        assert runtime._vision_thread is not None and runtime._vision_thread.is_alive()
    assert data["mode"] == "local" and data["hardware"] == "fake-arm"
    devices = data["devices"]
    assert devices["eyes"] == "ok" and devices["doa"] == "ok" and devices["camera"] == "ok"
    assert devices["faces"] == "ok" and devices["audio"] == "ok"
    assert devices["arm"].startswith("disabled") and devices["base"].startswith("disabled")
    assert devices["servo"] in {"missing", "closed (opens on arm enable)"}
    assert not fakes["arm"].enabled and not fakes["base"].enabled and not fakes["bus"].is_open
    assert not fakes["audio"].started  # closed on shutdown


def test_real_mode_falls_back_to_fakes_when_devices_fail(monkeypatch, fakes):
    def fail(*args):
        raise RuntimeError("no camera found (tried [])")

    monkeypatch.setattr(server, "make_camera", fail)
    monkeypatch.setattr(server, "make_eyes", fail)
    monkeypatch.setattr(
        server, "make_arm", lambda settings, bus: (_ for _ in ()).throw(FileNotFoundError("poses"))
    )
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    with TestClient(app) as client:
        devices = client.get("/api/health").json()["devices"]
    assert devices["camera"].startswith("missing:") and devices["eyes"].startswith("missing:")
    assert devices["arm"].startswith("missing:")
    assert isinstance(runtime.camera, FakeCamera) and isinstance(runtime.eyes, FakeEyes)
    assert runtime.robot.hardware.name == "sim"


def test_headless_transcript_feeds_the_console_status(monkeypatch, fakes):
    app = make_app(monkeypatch, real_settings())
    runtime = app.state.runtime
    with TestClient(app):
        runtime.observe({"type": "transcript", "text": "banana please"})
        runtime.observe({"type": "turn_started"})
        runtime.observe({"type": "assistant_delta", "text": "Bello! "})
        runtime.observe({"type": "assistant_delta", "text": "Banana for you."})
        status = runtime.status_wire()
    assert status["transcript"] == "banana please" and status["reply"] == "Bello! Banana for you."
    assert runtime.director.eye_state.expression in {"love", "neutral", "curious", "happy"}


# --- doctor ----------------------------------------------------------------------------------------


def doctor_checks(*specs):
    return [doctor.Check(name, priority, (lambda r=result: r)) for name, priority, result in specs]


def test_doctor_exit_code_follows_p0_only(capsys):
    checks = doctor_checks(
        ("a", "P0", (True, "fine")), ("b", "P1", (False, "meh")), ("c", "P0", (None, "skip"))
    )
    assert doctor.main(["--no-color"], settings=Settings(), checks=checks) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "FAIL" in out and "SKIP" in out and "All P0 checks passed" in out
    checks = doctor_checks(("a", "P0", (False, "broken")))
    assert doctor.main(["--no-color"], settings=Settings(), checks=checks) == 1
    assert "1 P0 check(s) failed: a" in capsys.readouterr().out


def test_doctor_survives_a_crashing_check(capsys):
    def crash():
        raise OSError("device on fire")

    checks = [doctor.Check("x", "P1", crash)]
    assert doctor.main(["--no-color"], settings=Settings(), checks=checks) == 0
    assert "OSError: device on fire" in capsys.readouterr().out


def test_doctor_default_checks_skip_cloud_in_sim_mode():
    names = [c.name for c in doctor.default_checks(Settings())]
    assert names[:3] == ["openai", "elevenlabs", "audio device"] and "camera" in names
    assert doctor.check_openai(Settings())[0] is None
    assert doctor.check_elevenlabs(Settings())[0] is None
    assert doctor.check_xvf3800_usb(Settings(doa_enabled=False))[0] is None
    ok, detail = doctor.check_models(Settings(models_dir="/nonexistent"))
    assert ok is False and "yunet" in detail.lower()

import json

import pytest
from fastapi.testclient import TestClient

from bob import server
from bob.config import Settings
from bob.hardware.eyes import EXPRESSIONS, Eyes, EyeState, FakeEyes
from bob.robot import Robot, SimHardware


class FakeSerial:
    def __init__(self, reply=b'{"ok":1,"side":"L","fps":58}\n'):
        self.lines: list[bytes] = []
        self.reply = reply
        self.closed = False

    def write(self, data):
        self.lines.append(data)

    def readline(self):
        return self.reply

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_eyes(ports=("/dev/left", "/dev/right"), missing=()):
    links = {}

    def factory(port, baud):
        if port in missing:
            raise OSError(f"could not open port {port}")
        links[port] = FakeSerial()
        return links[port]

    clock = Clock()
    eyes = Eyes(ports, clock=clock, serial_factory=factory)
    eyes.open()
    return eyes, links, clock


def test_expressions_match_spec():
    assert EXPRESSIONS == (
        "neutral",
        "curious",
        "happy",
        "love",
        "sleepy",
        "surprised",
        "sad",
        "angry_playful",
    )


def test_eye_state_to_json_exact():
    state = EyeState(expression="happy", gx=0.3, gy=-0.1, blink=False, pupil=1.0)
    assert state.to_json() == '{"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}'
    assert EyeState(gx=0.123456, gy=1 / 3, pupil=0.999).to_json() == (
        '{"e":"neutral","gx":0.12,"gy":0.33,"blink":false,"p":1.0}'
    )
    assert list(json.loads(EyeState().to_json())) == ["e", "gx", "gy", "blink", "p"]


def test_eye_state_from_dict_validates_and_clamps():
    state = EyeState.from_dict({"e": "love", "gx": 4, "gy": -2.5, "blink": True, "p": 9})
    assert state == EyeState("love", 1.0, -1.0, True, 2.0)
    assert EyeState.from_dict({"gx": 0.5}, base=state).expression == "love"
    for bad in [{"e": "furious"}, {"gx": "left"}, {"blink": 1}, {"p": None}]:
        with pytest.raises(ValueError):
            EyeState.from_dict(bad)


def test_set_writes_one_line_per_port_and_rate_limits():
    eyes, links, clock = make_eyes()
    assert eyes.connected
    assert eyes.set(EyeState("happy", 0.3, -0.1))
    for link in links.values():
        assert link.lines == [b'{"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}\n']
    clock.now = 0.01
    assert not eyes.set(EyeState("sad"))
    clock.now = 0.02
    assert not eyes.set(EyeState("curious"))
    assert all(len(link.lines) == 1 for link in links.values())
    clock.now = 0.04
    assert not eyes.flush()
    clock.now = 0.05
    assert eyes.flush()  # newest coalesced state wins
    assert links["/dev/left"].lines[-1] == b'{"e":"curious","gx":0.0,"gy":0.0,"blink":false,"p":1.0}\n'
    assert eyes.last.expression == "curious"
    clock.now = 0.2
    assert eyes.set(EyeState("sleepy"))
    assert len(links["/dev/right"].lines) == 3


def test_missing_port_does_not_raise():
    eyes, links, _ = make_eyes(missing=("/dev/right",))
    assert eyes.connected and set(links) == {"/dev/left"}
    eyes.set(EyeState())
    assert eyes.ping() == {
        "/dev/left": {"ok": 1, "side": "L", "fps": 58},
        "/dev/right": {"ok": 0, "error": "not connected"},
    }
    blind, _, _ = make_eyes(missing=("/dev/left", "/dev/right"))
    assert not blind.connected
    assert blind.set(EyeState("happy"))  # accepted, nowhere to write
    blind.close()


def test_write_failure_drops_port_and_close_releases_links():
    eyes, links, _ = make_eyes()

    def boom(_):
        raise OSError("unplugged")

    links["/dev/right"].write = boom
    eyes.set(EyeState())
    assert eyes.connected and "/dev/right" not in eyes._links
    eyes.close()
    assert not eyes.connected and links["/dev/left"].closed


def test_pyserial_import_is_lazy_and_default_factory_tolerates_missing_device():
    eyes = Eyes(("/dev/definitely-not-a-port",))
    eyes.open()
    assert not eyes.connected


def test_fake_eyes_records_states():
    fake = FakeEyes()
    fake.open()
    assert fake.last is None and fake.connected
    fake.set(EyeState("happy"))
    fake.set(EyeState("love", blink=True))
    assert [s.expression for s in fake.states] == ["happy", "love"]
    assert fake.last.blink
    fake.close()
    assert not fake.connected


@pytest.fixture
def client(monkeypatch):
    async def ready():
        return {"ready": True, "model": "fake-local"}

    app = server.build_app(Settings(), robot=Robot(SimHardware(0.001)))
    runtime = app.state.runtime
    monkeypatch.setattr(runtime.llm, "check", ready)
    monkeypatch.setattr(runtime.stt, "load", lambda: None)
    with TestClient(app) as test_client:
        yield test_client


def test_eyes_sim_pages_are_served_from_allowlist(client):
    assert client.get("/eyes").status_code == 200
    assert client.get("/assets/eyes-sim/eyes.mjs").status_code == 200
    assert client.get("/assets/eyes-sim/expressions.mjs").status_code == 200
    for path in [
        "/assets/eyes-sim/index.html",
        "/assets/eyes-sim/styles.css",
        "/assets/eyes-sim/expressions.test.mjs",
    ]:
        assert client.get(path).status_code == 404


def test_websocket_broadcasts_initial_eye_state_and_echoes_updates(client):
    with client.websocket_connect("/ws") as ws:
        while (message := ws.receive_json())["type"] != "eyes":
            pass
        # The Director owns the eyes now (idle gaze wanders), so the first state is whatever it has.
        assert list(message["state"]) == ["e", "gx", "gy", "blink", "p"]
        assert message["state"]["e"] in EXPRESSIONS
        ws.send_json({"type": "eyes", "e": "love", "gx": 0.25, "gy": -0.5, "blink": True, "p": 1.3})
        expected = {"e": "love", "gx": 0.25, "gy": -0.5, "blink": True, "p": 1.3}
        for _ in range(50):  # skip Director states already in flight before the override landed
            message = ws.receive_json()
            if message["type"] == "eyes" and message["state"] == expected:
                break
        assert message["state"] == expected
        ws.send_json({"type": "eyes", "e": "furious"})
        while (message := ws.receive_json())["type"] != "error":
            pass
        assert "furious" in message["message"]

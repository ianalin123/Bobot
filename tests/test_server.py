import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bob import server
from bob.config import Settings
from bob.robot import Robot, SimHardware


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


def test_static_files_are_allowlisted(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.mjs").status_code == 200
    for path in ["/.git/config", "/.env", "/bob/server.py", "/assets/server.py"]:
        assert client.get(path).status_code == 404


def test_cross_origin_websocket_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass


def test_single_controller_and_disconnect_stops_motion(client):
    with client.websocket_connect("/ws") as ws:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass
        ws.send_json({"type": "action", "action": "offer_banana"})
        while (message := ws.receive_json())["type"] != "action_result":
            pass
        assert message["result"]["accepted"]
    runtime = client.app.state.runtime
    assert runtime.robot.state.stopped
    assert not runtime.connected

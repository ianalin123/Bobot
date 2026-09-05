import asyncio
import base64
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .providers import MAX_AUDIO_BYTES, LocalLLM, LocalSTT, LocalTTS
from .robot import Robot, SimHardware
from .voice import VoiceSession

ROOT = Path(__file__).resolve().parent.parent
if os.getenv("BOB_HARDWARE", "sim") != "sim":
    raise RuntimeError(
        "Only the simulated adapter is implemented. Real hardware must be explicitly integrated."
    )
robot = Robot(SimHardware())
stt, llm, tts = LocalSTT(), LocalLLM(), LocalTTS()
readiness = {
    "llm": {"ready": False},
    "stt": {"ready": False},
    "tts": {"ready": tts.ready(), "engine": tts.kind},
}
connected = False


@asynccontextmanager
async def lifespan(app):
    async def warm():
        try:
            readiness["llm"] = await llm.check()
        except Exception as exc:
            readiness["llm"] = {"ready": False, "model": llm.model, "error": str(exc)[:150]}
        try:
            await asyncio.to_thread(stt.load)
            readiness["stt"] = {"ready": True, "model": stt.model_name}
        except Exception:
            readiness["stt"] = {
                "ready": False,
                "error": "Download models first: uv run python -m bob.setup_models",
            }

    task = asyncio.create_task(warm())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await robot.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])


@app.get("/api/health")
async def health():
    return {"mode": "local", "hardware": robot.hardware.name, **readiness}


@app.get("/")
async def index():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/assets/{filename}")
async def asset(filename: str):
    # An allowlist prevents serving .env, models, .git or arbitrary project files.
    if filename not in {"app.mjs", "audio.mjs", "vad.mjs", "mic-worklet.js", "styles.css"}:
        from fastapi import HTTPException

        raise HTTPException(404)
    return FileResponse(ROOT / "web" / filename, headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    global connected
    origin = ws.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme != "http" or parsed.netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return
    if connected:
        await ws.close(code=1013, reason="One controller at a time. Close the other Bob tab.")
        return
    connected = True
    try:
        await ws.accept()
    except Exception:
        connected = False
        raise
    send_lock = asyncio.Lock()

    async def send(message):
        async with send_lock:
            await ws.send_json(message)

    session = VoiceSession(send, stt, llm, tts, robot)

    async def updates():
        last = None
        while True:
            state = robot.snapshot()
            if state != last:
                await send({"type": "robot_state", "state": state})
                last = state
            await asyncio.sleep(0.05)

    ticker = asyncio.create_task(updates())
    try:
        await send({"type": "hello", "protocol": 1, "providers": readiness})
        while True:
            raw = await ws.receive_text()
            if len(raw) > 1_400_000:
                await send({"type": "error", "message": "Message too large."})
                continue
            import json

            try:
                data = json.loads(raw)
                kind = data.get("type")
                request_id = data.get("request_id", 0)
                if type(request_id) is not int or request_id < 0:
                    raise ValueError("Invalid request ID")
                if kind == "interrupt":
                    await session.interrupt(request_id)
                elif kind in {"user_text", "audio_turn"}:
                    if not readiness["llm"]["ready"]:
                        await send(
                            {
                                "type": "error",
                                "request_id": request_id,
                                "message": "Local model not ready. Check the setup panel.",
                            }
                        )
                        continue
                    if kind == "user_text":
                        text = data.get("text", "")
                        if not isinstance(text, str) or len(text) > 2000:
                            raise ValueError("Text must be at most 2000 characters")
                        await session.start(request_id, text=text)
                    else:
                        if not readiness["stt"]["ready"]:
                            raise ValueError("Local speech recognition is not ready")
                        audio = base64.b64decode(data.get("wav", ""), validate=True)
                        if len(audio) > MAX_AUDIO_BYTES:
                            raise ValueError("Recording too large")
                        await session.start(request_id, audio=audio)
                elif kind in {"playback_started", "playback_finished"}:
                    session.acknowledge(request_id, data.get("segment_id"), kind == "playback_finished")
                elif kind == "action":
                    result = await robot.command(data.get("action"))
                    await send({"type": "action_result", "result": result})
                elif kind == "expression":
                    if data.get("expression") in {"curious", "happy", "sleepy"}:
                        robot.state.expression = data["expression"]
                else:
                    raise ValueError("Unknown message type")
            except (ValueError, TypeError, AttributeError) as exc:
                await send({"type": "error", "message": str(exc)[:160]})
    except WebSocketDisconnect:
        pass
    finally:
        # ASGI disconnect can cancel its entire task scope. Cleanup must still reach
        # the driver stop, even if a voice/websocket operation fails along the way.
        with anyio.CancelScope(shield=True):
            try:
                await robot.stop()
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeError, WebSocketDisconnect):
                    await ticker
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await session.interrupt()
            finally:
                connected = False

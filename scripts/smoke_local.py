"""Opt-in real-model check. Synthetic speech only; never opens a microphone."""

import asyncio
import base64
import io
import json
import wave

import httpx
import websockets

from bob.providers import LocalTTS


async def receive_until(ws, predicate, timeout=90):
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == "error":
                raise RuntimeError(message)
            if predicate(message):
                return message


async def main():
    async with httpx.AsyncClient(trust_env=False) as client:
        health = (await client.get("http://127.0.0.1:8766/api/health")).json()
        assert all(health[k]["ready"] for k in ["llm", "stt", "tts"]), health
        print("Ready:", json.dumps(health), flush=True)
        for path in ["/", "/assets/app.mjs", "/assets/audio.mjs", "/assets/vad.mjs", "/assets/styles.css"]:
            assert (await client.get(f"http://127.0.0.1:8766{path}")).status_code == 200
        for path in ["/.git/config", "/.env", "/assets/server.py"]:
            assert (await client.get(f"http://127.0.0.1:8766{path}")).status_code == 404

    source = await LocalTTS().synthesize(
        "Hello Bob. My name is Alex. Please say hello in one short sentence."
    )
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    pcm, _ = await process.communicate(source)
    assert process.returncode == 0
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)

    async with websockets.connect("ws://127.0.0.1:8766/ws", origin="http://127.0.0.1:8766") as ws:
        await receive_until(ws, lambda m: m["type"] == "hello")
        await ws.send(json.dumps({"type": "action", "action": "reset_simulation"}))
        await ws.send(
            json.dumps(
                {"type": "audio_turn", "request_id": 1, "wav": base64.b64encode(buffer.getvalue()).decode()}
            )
        )
        transcript = await receive_until(ws, lambda m: m["type"] == "transcript")
        assert "alex" in transcript["text"].lower(), transcript
        print("Recognized synthetic speech:", transcript["text"], flush=True)
        metric = await receive_until(ws, lambda m: m["type"] == "metric" and m["name"] == "first_audio_ms")
        audio = await receive_until(ws, lambda m: m["type"] == "audio")
        with wave.open(io.BytesIO(base64.b64decode(audio["wav"]))) as output:
            assert output.getnframes() > 0
        print("First local reply:", audio["text"], "end-to-end ms:", metric["value"], flush=True)
        await ws.send(json.dumps({"type": "playback_started", "request_id": 1, "segment_id": 0}))
        await ws.send(json.dumps({"type": "interrupt", "request_id": 2}))
        await receive_until(ws, lambda m: m["type"] == "interrupted" and m["request_id"] == 2)
        await ws.send(json.dumps({"type": "user_text", "request_id": 3, "text": "Please give me a banana."}))
        tool = await receive_until(ws, lambda m: m["type"] == "tool_result")
        assert tool["name"] == "offer_banana" and tool["result"]["accepted"], tool
        assert tool["source"] == "exact_command"
        print("Exact voice/text command invoked shared banana behavior:", tool["result"], flush=True)
        await receive_until(ws, lambda m: m["type"] == "robot_state" and m["state"]["phase"] == "waiting")
        await ws.send(json.dumps({"type": "action", "action": "take_banana"}))
        done = await receive_until(
            ws,
            lambda m: (
                m["type"] == "robot_state" and m["state"]["phase"] == "idle" and m["state"]["deliveries"] == 1
            ),
        )
        print("Simulated handoff confirmed:", done["state"]["banana"], flush=True)
        await ws.send(json.dumps({"type": "interrupt", "request_id": 4}))
        await receive_until(ws, lambda m: m["type"] == "interrupted" and m["request_id"] == 4)
        try:
            async with asyncio.timeout(0.4):
                while True:
                    event = json.loads(await ws.recv())
                    assert event.get("request_id", 4) >= 4, event
        except TimeoutError:
            pass
    print(
        "PASS: local STT → local LLM → local WAV; interrupt; tool; simulated handoff. No live mic/playback test.",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())

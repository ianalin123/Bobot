# Bob · little robot, local brain

A local-first companion-robot workbench: animated Bob, interruptible voice, and an authoritative banana-delivery controller that runs against a simulated hardware adapter.

**Current target:** Apple Silicon Mac. Speech recognition, language inference, and synthesized voice run locally; no API keys, paid inference, or cloud fallback. This means local on the development computer, **not yet onboard a physical robot**. Initial dependencies/models require internet downloads.

## Start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), [Ollama](https://ollama.com/download), and an installed macOS voice (`say -v '?'`). Node 22+ is only needed for frontend tests. On macOS, `brew install uv ollama` installs the first two tools if you use Homebrew.

```sh
git clone git@github.com:ianalin123/Bobot.git
cd Bobot
uv sync --locked --python 3.12
```

Start Ollama in another terminal if it is not already running:

```sh
OLLAMA_NO_CLOUD=1 ollama serve
```

Download models once, then start Bob:

```sh
ollama pull qwen3:4b-instruct
uv run python -m bob.setup_models
uv run uvicorn bob.server:app --host 127.0.0.1 --port 8766
```

Open **http://127.0.0.1:8766** in Chrome on this computer. Wait for all three setup checks to show ready. Type a message or explicitly enable the microphone. Models are ignored by Git. No microphone audio or transcripts are written to project files; conversation history is in memory for the current tab session. TTS creates short-lived local WAV files and removes them after synthesis.

Optional settings are in `.env.example`. To use a local `.env`, launch with `--env-file .env`. Never commit credentials. Keep the server on loopback; it is not a hosted multi-user service. Model changes require restarting the runtime.

## Try it

1. Say/type “Hello Bob,” then “Please give me a banana.” The local model can request the same behavior as the Offer button.
2. In hands-free mode, start speaking while Bob talks. Playback stops locally before transcription finishes; old inference and queued speech are cancelled. Short noise spikes are filtered.
3. If speaker echo or room chatter causes interruptions, use headphones or switch to **Push to talk**. Hold the button with pointer, Space, or Enter. Microphone permission is opt-in.
4. Click **Confirm handoff** after presentation; this is a manual simulated recipient event. Reload before another delivery.
5. **Interrupt speech** cancels conversation output, not motion. **Stop motion** and Escape stop both; **Reset simulation** explicitly recovers the simulator. A disconnected controller also triggers a software motion stop.
6. F / Face view enlarges Bob. Escape returns to the workbench.

## What is real / simulated

| Real on this computer | Simulated or not implemented |
| --- | --- |
| faster-whisper `base.en`, CPU int8 STT | Physical mic/speaker performance in your room still needs testing |
| Ollama `qwen3:4b-instruct`, streamed responses and tool calls | Camera, identification, LinkedIn lookup, browser actions |
| macOS local TTS, sentence-by-sentence WAV playback | Character-quality voice; Linux/Piper adapter has not been device-tested |
| Cancellation, playback acknowledgements, bounded history | Wheel navigation, motor firmware, real arm, grasp and handoff sensing |
| Backend behavior state machine with action timeouts | Adapter completion is a timer; this is **not physics, CAD, torque, or stability validation** |

The robot illustration is a UI preview, not a manufacturing render. Bob/Minions character rights are not granted by this repository. No software or character license is implied.

## Verify

```sh
uv run pytest -q
node --test web/*.test.mjs
uv run ruff check bob tests scripts
uv run ruff format --check bob tests scripts
```

These tests use fake providers and do not download models or open a mic. For a real local-model smoke test, with the server running and all Bob tabs closed:

```sh
# Requires ffmpeg and installed models; generates synthetic speech, not a live recording.
uv run python -m scripts.smoke_local
```

This checks HTTP asset isolation, local STT → LLM → valid WAV, interruption, a model tool call, and simulated handoff. It does not prove actual browser playback, acoustic echo cancellation, or physical safety. The smoke test changes simulator state and closes its controller; reset the simulator when opening the UI afterward.

## Build toward hardware

- [Architecture and boundaries](docs/ARCHITECTURE.md): reusable runtime, adapter contracts, cancellation, and privacy.
- [One-week plan and longer-term roadmap](docs/ROADMAP.md): acceptance gates, onboard compute, consent-based introductions.
- [Hardware compatibility checklist](docs/HARDWARE.md): what is portable and what must be measured.
- [WebSocket protocol](docs/PROTOCOL.md): interface for a future robot-side face/audio client.
- [Ordering candidates](ORDERING.md): parts to evaluate, not verified delivery promises.

Keep behavior and safety in `bob/robot.py`; implement hardware acknowledgements behind its `Hardware` protocol. Do not put motor logic in the browser or trust LLM prose as physical feedback. The current server intentionally refuses `BOB_HARDWARE` values other than `sim` until a reviewed adapter exists.

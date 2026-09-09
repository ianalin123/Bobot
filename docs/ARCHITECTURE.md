# Architecture

## September 6 deployment decision (planned, not implemented)

Target: borrowed **12V SO-101 on a LeKiwi base**, Pi for robot-local I/O, existing computer for AI over Wi-Fi. The table below describes today's local runtime and eventual onboard options, not an already working distributed system.

Add authenticated robot transport, command IDs/expiry, reconnect handling, hardware acknowledgements and local motion watchdogs before remote operation. Do not expose the current unauthenticated localhost server to the LAN. Keep localhost-only defaults. Playback cancellation must execute on the robot audio client without waiting for a Wi-Fi round trip. Pi audio/eye clients and the physical adapter remain unimplemented.

Preserve the borrowed controller for initial bench tests. A separate arm/base-controller topology needs an explicit driver; stock LeKiwi's shared bus cannot be assumed to control it unchanged. See [hardware baseline](HARDWARE.md).

## One reusable behavior layer

`web/` captures audio and renders the latest backend state. `bob/voice.py` orchestrates local providers and exposes only `offer_banana` and `stop_motion` to the LLM. `bob/robot.py` validates requests, owns action sequencing, and consumes hardware acknowledgements. The current `SimHardware` supplies delayed acknowledgements. The future physical adapter must supply measured completion/failure.

| Boundary | Current implementation | Physical robot path |
| --- | --- | --- |
| Face/audio client | Browser, AudioWorklet, PCM WAV, AudioContext | Onboard kiosk browser first; native ALSA/SPI client later using the same protocol |
| STT | faster-whisper base.en CPU int8 | Benchmark on chosen compute; swap provider without changing robot behavior |
| Conversation | Loopback Ollama qwen3:4b-instruct, thinking disabled | Locally hosted model on onboard compute; no cloud fallback |
| TTS | Installed macOS voice via `say` | Piper CLI adapter exists but Linux installation, model license and latency need verification |
| Behavior | Python Robot state machine | Same state machine, backed by physical acknowledgements |
| Actuation | Timer-based SimHardware | MCU/servo-controller protocol with limits, watchdog, measured state and stop acknowledgement |

No motor coordinates, voltages or joint targets are exposed to the language model. It can request a banana or a stop, not arbitrary Python/shell/browser operations. State checks reject duplicate, busy, empty and stopped requests. A small LLM can misunderstand intent; do not treat prompting as a physical safety mechanism.

Exact phrases such as “Please give me a banana” and “Stop moving” bypass LLM interpretation and return a bounded state-based acknowledgement. This was added after real testing found that the small model sometimes narrated a handoff without calling its tool. The matcher uses a narrow full-string vocabulary, not a banana keyword search; negated requests and longer prose do not match. Less exact phrasings still use the model's allowlisted tools and can be misunderstood. Keep the UI controls and physical stop available.

## Physical adapter contract

`perform(action)` returns only after the requested operation completes, or raises on failure. Actions are `open_compartment`, `grasp_banana`, `present_banana`, `release_banana`, `stow_arm`, `close_compartment`. The behavior transitions banana ownership only after the corresponding acknowledgement. `stop()` must stop actuation and acknowledge the result. Runtime calls have bounded deadlines.

This interface is an initial seam, not a finished motor protocol. Before a real adapter is accepted it needs command IDs, duplicate protection, cancellation semantics, heartbeat timeout, joint/current/speed limits, startup homing or state discovery, sensed grip/load state, and deliberate post-fault recovery. Cancellation of a coroutine alone cannot stop a motor. Implement the driver cancellation path and an independent controller watchdog; fit a physical stop that does not depend on Python, Wi-Fi or the LLM. Do not energize or home motors automatically on browser connection.

The browser's `take_banana` and `reload` are manual confirmations. Replace or corroborate them with appropriate sensors on hardware. `reset_simulation` must never recover a physical robot.

## Interruptions

1. Sustained mic energy (~140 ms), typing a new message, or the Interrupt button increments the client request ID.
2. Client immediately stops playback, clears queued WAV segments, and invalidates in-flight asynchronous decoding with an epoch counter.
3. Server cancels the prior turn's Ollama stream and TTS subprocess, then accepts the next utterance.
4. CPU transcription already in native code may finish; its cancelled coroutine cannot publish a transcript, invoke a tool or speak. A lock prevents simultaneous native transcriptions. Repeated interruptions can still create a compute backlog; quantify this before an always-on deployment.
5. Each audio segment has started/finished acknowledgements. Conversation history retains fully played sentences, not everything generated. Partial sentences are not treated as fully heard.

Speech cancellation deliberately does not cancel an already accepted robot action. Stop motion / Escape are the explicit combined stop controls. Exact spoken stop requests bypass the LLM but still depend on successful STT; they are a convenience, never an emergency-stop circuit.

Hands-free VAD is simple fixed-threshold energy detection with a pre-roll and ~650 ms trailing silence, not speaker recognition. It has a 15-second utterance limit. Browser acoustic echo cancellation is requested, not guaranteed; use PTT/headphones in difficult acoustic conditions. TTS uses sentence chunks, not a full-duplex speech-to-speech model. First-audio metric is measured from server turn receipt, excluding client end-of-speech delay, network transit and device playback startup.

## Local operation and privacy

Ollama must be loopback HTTP; cloud model names and models reported as remote are rejected. Use `OLLAMA_NO_CLOUD=1` on the Ollama service for defense in depth. STT normal runtime opens cached models only. Dependencies/models need initial internet access. Verify actual offline operation before claiming air-gap readiness. No external browser/font/CDN assets are loaded by the UI.

Mic access is explicit and can be disabled. Conversation history is session-local and bounded; no recording or transcript persistence is implemented. TTS uses temporary WAV files that are removed after synthesis. Models, logs, artifacts, recordings and `.env` are Git-ignored. Server exposes an asset allowlist rather than the repository directory, validates browser WebSocket origin, and permits one controller. No authentication or remote deployment support is implemented; bind only to localhost. Other trusted local processes can connect without an Origin header.

Camera presence detection and consent-based profile lookup are a later module; they are not current capabilities. No camera identity database, profile scraping, messages, or private-account access is part of this runtime.

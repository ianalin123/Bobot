# Bob Event Robot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bobot repo deployable on the Jetson-based Bob with real voice, face recognition, LCD eyes, SO-101 arm and LeKiwi base, while keeping the Mac simulator working.

**Architecture:** Keep `Robot` (state machine) and `VoiceSession` (turn/interrupt logic) as they are; add hardware adapters in `bob/hardware/`, cloud providers in `bob/providers_cloud.py`, perception in `bob/vision/`, and a `Director` that turns perception + conversation events into gaze, expressions, greetings, and motion. Everything has a fake so it runs and tests on the Mac.

**Tech Stack:** Python 3.12 (uv), FastAPI, asyncio, sounddevice, openai, elevenlabs, opencv-python (<5), numpy, pyusb, feetech-servo-sdk, pyserial; PlatformIO (pioarduino) + Arduino_GFX for the ESP32-S3 eyes; plain HTML/JS for the eye sim and console.

**Spec:** `docs/superpowers/specs/2026-09-10-bob-event-robot-design.md`

## Global Constraints

- Python `>=3.11,<3.14`; the Jetson venv is created by uv with Python 3.12.
- `opencv-python>=4.10,<5` (2023mar YuNet is broken on 5.x).
- Never energize servos at import or server start; motion needs an explicit enable.
- Wheels get `Goal_Velocity=0` in every exit path (`finally`).
- No API keys in code or docs; read from env (`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`). `.env` is gitignored.
- Existing tests (`uv run pytest -q`, `node --test web/*.test.mjs`) and `ruff check`/`ruff format --check` must stay green after every task.
- Heavy optional deps (`opencv-python`, `sounddevice`, `pyusb`, `feetech-servo-sdk`, `pyserial`, `openai`, `elevenlabs`, `librosa`) live in a `robot` dependency group; core tests must pass without them installed (import inside functions or guard with `pytest.importorskip`).
- Commit after each task with a conventional message.

## File map

| Path | Responsibility |
| --- | --- |
| `bob/config.py` | `Settings` dataclass from env; single source of truth for mode, device paths, model IDs, thresholds |
| `bob/providers_cloud.py` | `OpenAISTT`, `OpenAILLM`, `OpenAITTS`, `ElevenLabsTTS`, `FallbackSTT`, `FallbackTTS`, `pitch_shift_wav` |
| `bob/persona.py` | `SYSTEM_PROMPT`, `MINIONESE`, `GREETINGS` (100), `STOCK_PHRASES`, `pick_greeting(name)` |
| `bob/audio.py` | `AudioIO` (capture + VAD turns, playback queue with cancel + acks), `FakeAudioIO` |
| `bob/hardware/feetech.py` | `FeetechBus`, `FakeBus`, register constants, sign-magnitude helpers |
| `bob/hardware/arm.py` | `ArmHardware(Hardware)`, `Poses` loading, grasp verification |
| `bob/hardware/base.py` | `Base`, `wheel_raw_from_body(vx, vy, omega_deg)`, watchdog |
| `bob/hardware/respeaker.py` | `DoAReader` (pyusb), `FakeDoA`, `set_led(...)` |
| `bob/hardware/eyes.py` | `Eyes` (serial JSON), `FakeEyes`, `EXPRESSIONS` |
| `bob/vision/camera.py` | `Camera` (V4L2 auto-detect), `FakeCamera` |
| `bob/vision/faces.py` | `FaceEngine` (YuNet+SFace), `enroll_dir(...)`, `Person` dataclass, `Tracker` |
| `bob/director.py` | `Director` state machine and behavior rules |
| `bob/server.py` | wire mode `sim|cloud|local`, add `/console`, new events `person`, `doa`, `eyes` |
| `web/eyes-sim/index.html` + `eyes.mjs` | browser rendering of the eye protocol |
| `web/console.html` | phone e-stop, dead-man wander, teleop |
| `firmware/eye/` | PlatformIO project, `src/main.cpp`, `src/eye.h`, `platformio.ini`, `dist/` |
| `scripts/*.py`, `scripts/*.sh` | discovery, teach, enroll, flash, bundle, doctor, render phrases, jetson setup |
| `docs/DEPLOY.md` | tomorrow's runbook |

---

### Task 1: Settings and dependency groups

**Files:** Create `bob/config.py`, `tests/test_config.py`; Modify `pyproject.toml`, `.env.example`.

**Produces:** `Settings` (frozen dataclass) with fields: `mode: str` (`sim|cloud|local`), `hardware: str` (`sim|real`), `openai_api_key`, `elevenlabs_api_key`, `llm_model="gpt-4.1-mini"`, `stt_model="gpt-4o-mini-transcribe"`, `tts_model="gpt-4o-mini-tts"`, `tts_voice="ash"`, `pitch_semitones=5.0`, `servo_port="/dev/ttyACM0"`, `eye_ports=("/dev/ttyACM1","/dev/ttyACM2")` (comma env), `camera_index=-1` (auto), `face_threshold=0.363`, `console_token=""`, `local_fallback=False`, `doa_enabled=True`. `Settings.from_env(environ=os.environ) -> Settings`. Validation: `mode=cloud` without `openai_api_key` raises `ValueError`.

- [x] Add `[dependency-groups] robot = ["openai>=1.50,<3", "elevenlabs>=1.50,<3", "opencv-python>=4.10,<5", "sounddevice>=0.5,<1", "pyusb>=1.2,<2", "feetech-servo-sdk>=1.0,<2", "pyserial>=3.5,<4", "librosa>=0.10,<1", "soundfile>=0.12,<1"]` and keep `dev`. Run `uv lock` (do not `uv sync` the robot group on Mac unless needed; `uv sync --group robot` is what the Jetson does).
- [x] Tests: from_env defaults, comma-split eye ports, cloud mode requires key, `BOB_HARDWARE=real` maps to `hardware="real"`.
- [x] Update `.env.example` with all new variables and comments (no values).
- [x] Commit `feat: settings from env and robot dependency group`.

### Task 2: Persona and greetings

**Files:** Create `bob/persona.py`, `tests/test_persona.py`.

**Produces:** `SYSTEM_PROMPT: str` (Bob persona: sweet, childlike, loves teddy Tim and bananas; ~60% English, Minionese sprinkled; one or two short sentences; never claims a completed handoff; tool guidance for `offer_banana`, `stop_motion`, `look_at_speaker`, `set_expression`); `MINIONESE: dict[str,str]` (bello=hello, poopaye=goodbye, tank yu=thank you, bee do=fire alarm, papoy=toy, tulaliloo ti amo=we love you, bapple=apple, gelato=ice cream, hana dul sae=1 2 3, me want banana, muak=kiss, para tu=for you, stopa=stop, tatata bala tu=I hate you (never use), pwede na=can we start, underwear=I swear, bananaaa!); `GREETINGS: list[str]` exactly 100 unique templates containing `{name}` at most once, mixing Minionese and English, each ≤ 90 characters; `STOCK_PHRASES: dict[str,str]` keys `bello, banana, poopaye, laugh, yawn, tank_yu, bee_do, para_tu, whoa, uh_oh`; `pick_greeting(name: str, rng=random) -> str`.

- [x] Tests: 100 greetings, all unique, all ≤90 chars, each has `{name}` ≤ 1 time, `pick_greeting("Sissi")` contains "Sissi" when the template has the placeholder, total characters of `GREETINGS` (with an 8-char name) + `STOCK_PHRASES` < 6000 (ElevenLabs budget).
- [x] Commit `feat: Bob persona, Minionese lexicon and 100 greetings`.

### Task 3: Cloud providers with local fallback

**Files:** Create `bob/providers_cloud.py`, `tests/test_providers_cloud.py`; Modify `bob/voice.py` (TOOLS list gains `look_at_speaker` and `set_expression(expression)`; tool dispatch routes them to `robot.command` / a `director` hook via an optional `on_tool` callback; keep behavior identical when absent).

**Produces:**
- `class OpenAISTT: __init__(client, model); transcribe(wav_bytes) -> str` (uses `client.audio.transcriptions.create(model=..., file=("turn.wav", BytesIO, "audio/wav"))`, returns `""` for audio under 0.4 s or RMS < 0.002 using `providers.decode_wav`).
- `class OpenAILLM: __init__(client, model); async stream(messages, tools) -> AsyncIterator[dict]` yielding the same message-dict shape `voice.py` consumes today: `{"content": str}` deltas and `{"tool_calls": [{"function": {"name", "arguments": dict}}]}`; uses Chat Completions streaming and converts Ollama-style tools to OpenAI `{"type":"function","function":{...}}`; accumulates tool-call argument fragments by index and emits them once at the end of the stream.
- `class OpenAITTS: __init__(client, model, voice, instructions, pitch_semitones); async synthesize(text) -> bytes` (WAV, 16 kHz mono 16-bit). Requests `response_format="pcm"` (24 kHz s16le), resamples to 16 kHz, applies `pitch_shift_wav`.
- `pitch_shift_wav(pcm16: np.ndarray, sr: int, semitones: float) -> np.ndarray`: use `librosa.effects.pitch_shift` if importable, else a resample-based chipmunk shift (`np.interp` with ratio `2**(semitones/12)`; changes duration, acceptable fallback).
- `class ElevenLabsTTS: __init__(client, voice_id, model_id="eleven_flash_v2_5"); async synthesize(text) -> bytes` (request `output_format="pcm_16000"`, wrap into WAV).
- `class FallbackTTS(primary, secondary)`, `class FallbackSTT(primary, secondary)`: try primary, on any exception log and use secondary; expose `.last_error`.
- `wav_from_pcm16(samples: np.ndarray, sr: int) -> bytes` helper.
- `build_providers(settings) -> tuple[stt, llm, tts]` for mode `cloud` (OpenAI + optional local fallback when `settings.local_fallback`), `local` (existing `LocalSTT/LocalLLM/LocalTTS`).

- [x] Tests use a fake OpenAI client object (duck-typed: `audio.transcriptions.create`, `chat.completions.create` returning an async iterator of chunk objects with `.choices[0].delta.content` / `.tool_calls`, `audio.speech.with_streaming_response.create` context manager yielding PCM bytes). Assert: STT skips silence, LLM stream yields deltas then one tool_calls message with parsed JSON args, TTS output is a valid 16 kHz WAV (`providers.decode_wav` accepts it), pitch shift with 0 semitones is identity, Fallback uses secondary on exception.
- [x] Live smoke test `tests/test_live_cloud.py` skipped unless `BOB_LIVE=1`: one STT on a generated 1 s sine WAV (expects a string), one LLM turn ("say bello"), one TTS ("Bello!") saved to `artifacts/bello.wav`.
- [x] Run the live smoke once on the Mac (`BOB_LIVE=1 uv run --group robot pytest tests/test_live_cloud.py -q`) and record results in the commit message.
- [x] Commit `feat: OpenAI/ElevenLabs providers with local fallback`.

### Task 4: Stock phrase cache

**Files:** Create `scripts/render_phrases.py`, `tests/test_render_phrases.py`; Modify `.gitignore` (add `assets/phrases/`).

**Produces:** `assets/phrases/<key>.wav` for every `STOCK_PHRASES` key and `greet_NNN.wav` for each greeting rendered with a placeholder-free variant (greetings with `{name}` are rendered as two halves is over budget; instead render greetings **without** names: `{name}` replaced by "friend" only for the 30 greetings whose template ends with the name; the remaining 70 are TTS'd live). `manifest.json` maps key → file, chars, model. Script flags: `--provider elevenlabs|openai`, `--dry-run` prints total characters and exits, `--only KEY`. Stop if projected characters exceed `--budget` (default 6000). Never re-render existing files unless `--force`.

- [x] Tests: dry-run count equals sum of lengths; with a fake synthesizer, files are written and manifest is valid; existing files are skipped.
- [x] Run for real once (`uv run --group robot python scripts/render_phrases.py --provider elevenlabs`) and check `assets/phrases/` plus the ElevenLabs remaining quota via `/v1/user/subscription`. Report the characters used.
- [x] Commit `feat: pre-rendered Minion phrase cache`.

### Task 5: Audio I/O on the Jetson (and fake)

**Files:** Create `bob/audio.py`, `tests/test_audio.py`.

**Produces:**
- `class Vad` — port of `web/vad.mjs`: `feed(frame: np.ndarray) -> "silence"|"speech_start"|"speech"|"speech_end"`, threshold RMS 0.015 default, pre-roll 300 ms ring buffer, 650 ms trailing silence, 15 s cap; `take_turn() -> bytes` returns WAV of the last utterance.
- `class AudioIO` — `__init__(settings, on_turn: Callable[[bytes], Awaitable], on_barge_in: Callable[[], Awaitable])`; `start()` opens `sounddevice.InputStream` (16 kHz, channels=2 on the XVF3800, keep channel 0; device chosen by name substring "XVF3800" or `BOB_AUDIO_DEVICE`) and `OutputStream`; `play(segment_id, wav_bytes)` enqueues; `cancel_playback()` clears queue and current; playback emits `on_playback(segment_id, started: bool)`; `speaking` flag; barge-in when VAD reports `speech_start` sustained 140 ms while `speaking`.
- `class FakeAudioIO` — same API, `feed_wav(bytes)` to inject turns, records played segments and cancels.
- `class RobotClient` in `bob/audio.py` (or `bob/headless.py`): drives a `VoiceSession` without a browser: `send()` handler that routes `audio` events to `AudioIO.play`, `playback_*` acks back to `session.acknowledge`, and exposes `events` for the Director.

- [x] Tests: VAD on synthetic signal (silence→tone→silence) yields start and end with the pre-roll included; 15 s cap cuts; FakeAudioIO play/cancel ordering; RobotClient + `VoiceSession` with fake providers produce playback acks and commit history (reuse `tests/test_voice.py` fakes).
- [x] Commit `feat: headless audio I/O with VAD, barge-in and fake`.

### Task 6: Feetech bus and fake

**Files:** Create `bob/hardware/__init__.py`, `bob/hardware/feetech.py`, `tests/test_feetech.py`.

**Produces:**
```python
# registers (addr, size)
TORQUE_ENABLE=(40,1); ACCELERATION=(41,1); GOAL_POSITION=(42,2); GOAL_VELOCITY=(46,2)
LOCK=(55,1); PRESENT_POSITION=(56,2); PRESENT_VELOCITY=(58,2); PRESENT_LOAD=(60,2)
PRESENT_VOLTAGE=(62,1); PRESENT_TEMPERATURE=(63,1); MOVING=(66,1); PRESENT_CURRENT=(69,2)
OPERATING_MODE=(33,1); HOMING_OFFSET=(31,2); MIN_POSITION_LIMIT=(9,2); MAX_POSITION_LIMIT=(11,2)
MAX_TORQUE_LIMIT=(16,2); PROTECTION_CURRENT=(28,2); OVERLOAD_TORQUE=(36,1); MAXIMUM_ACCELERATION=(85,1)
def encode_sign_magnitude(value:int, sign_bit:int)->int  # e.g. -5 with bit 15 -> 0x8005
def decode_sign_magnitude(raw:int, sign_bit:int)->int
SIGN_BITS = {"HOMING_OFFSET":11, "GOAL_POSITION":15, "GOAL_VELOCITY":15, "PRESENT_POSITION":15, "PRESENT_VELOCITY":15, "PRESENT_LOAD":10}
class FeetechBus:
    def __init__(self, port:str, baudrate:int=1_000_000, protocol:int=0): ...  # lazy import scservo_sdk
    def open(self)/close(self); def scan(self, ids=range(1,21)) -> dict[int, dict]  # {id:{"model":int,"position":int,"voltage":float}}
    def read(self, motor:int, reg:tuple)->int; def write(self, motor:int, reg:tuple, value:int)->None
    def sync_write(self, reg:tuple, values:dict[int,int])->None; def sync_read(self, reg:tuple, ids:list[int])->dict[int,int]
    def torque(self, ids, on:bool)  # writes TORQUE_ENABLE and LOCK (on→1/1, off→0/0)
class FakeBus:  # same API; dict-of-dicts registers; goal position becomes present position on `tick()`; scan returns configured ids
```
Signed values are decoded/encoded automatically for the registers listed in `SIGN_BITS`. Bus access is guarded by a `threading.Lock`.

- [x] Tests: sign-magnitude round trips (incl. -0 handling), FakeBus read/write/sync_write/tick, `torque(off)` writes 0 then Lock 0, scan returns configured ids only. Real-hardware code path is exercised with a fake `scservo_sdk` module injected via `sys.modules` for `open()` (PortHandler.openPort/setBaudRate called).
- [x] Commit `feat: minimal Feetech STS3215 bus with fake`.

### Task 7: Arm adapter, discovery and teach scripts

**Files:** Create `bob/hardware/arm.py`, `scripts/discover_servos.py`, `scripts/teach_poses.py`, `config/poses.example.json`, `tests/test_arm.py`.

**Produces:**
- `Poses.load(path) -> Poses` with `.named: dict[str, dict[int,int]]` for `stow, above_cradle, grasp, lift, present, release` (each maps motor id 1–6 → raw position) and `.gripper_open`, `.gripper_closed`, `.hold_load_threshold` (int, sign-stripped Present_Load magnitude), `.ids=[1..6]`.
- `class ArmHardware(Hardware)`: `name="arm"`, `__init__(bus, poses, speed=600, acceleration=30, max_step=150, settle_tolerance=25, timeout=6.0)`; `enable()` (torque on, write ACCELERATION and MAXIMUM_ACCELERATION on IDs 1–6, cap `MAX_TORQUE_LIMIT` on gripper to 500); `disable()`; `async perform(action)` mapping: `open_compartment`→noop, `grasp_banana`→`above_cradle`,`grasp`,gripper close,verify hold (load ≥ threshold over 3 reads 100 ms apart, else raise `GraspFailed`),`lift`; `present_banana`→`present`; `release_banana`→gripper open; `stow_arm`→`lift`,`stow`; `close_compartment`→noop. Moves interpolate toward the target with per-tick clamp `max_step` and wait until all joints within `settle_tolerance` or timeout → `TimeoutError`. `async stop()` writes present positions as goals (freeze) and sets an `estopped` flag so `perform` refuses until `enable()`.
- `scripts/discover_servos.py [--port] [--baud]`: scans, prints a table (id, model, position, voltage), reads homing offset and limits, writes `config/calibration.json`.
- `scripts/teach_poses.py [--port] [--out config/poses.json]`: torque off all, for each pose name prompt Enter and record positions; then prompts for gripper open, gripper closed on banana (records position and 3 load samples, sets threshold = 60% of mean), writes JSON, re-enables torque at end only if `--hold`.

- [x] Tests with `FakeBus`: perform sequence writes expected goals in order; grasp verification raises when load stays 0; `stop()` freezes and blocks; per-tick clamp never exceeds `max_step`; `Poses.load` validates required keys. Scripts are tested by calling their `main(argv, bus=FakeBus, input=fake_input)` functions.
- [x] Commit `feat: SO-101 arm adapter with teach and discovery scripts`.

### Task 8: Base driver and kinematics

**Files:** Create `bob/hardware/base.py`, `tests/test_base.py`.

**Produces:**
```python
WHEEL_IDS = {"left":7, "back":8, "right":9}; WHEEL_RADIUS=0.05; BASE_RADIUS=0.125; MAX_RAW=3000
def wheel_raw_from_body(vx:float, vy:float, omega_deg:float)->dict[int,int]  # LeRobot formula, scaled if any |raw|>MAX_RAW, sign-magnitude encoded by the bus
class Base:
    def __init__(self, bus, max_v=0.15, max_omega=40.0, watchdog_s=0.3)
    def enable(self)  # OPERATING_MODE=1 (velocity) on 7-9, torque on
    def drive(self, vx, vy, omega_deg)  # clamps to limits, sync_write GOAL_VELOCITY, refreshes watchdog timestamp
    def stop(self)  # writes 0 to all three
    async def watchdog(self)  # loop: if now-last_cmd>watchdog_s and moving: stop()
    def close(self)  # stop + torque off, always safe to call twice
```
- [x] Tests: pure rotation gives equal-magnitude raws; forward `vx` gives back wheel ~0 and left/right opposite signs; scaling applies when over MAX_RAW; drive clamps; watchdog stops after silence (use a controllable clock); `close()` idempotent.
- [x] Commit `feat: LeKiwi base driver with kinematics and watchdog`.

### Task 9: ReSpeaker DoA and LEDs

**Files:** Create `bob/hardware/respeaker.py`, `tests/test_respeaker.py`, `scripts/udev/99-bob.rules`, `scripts/doa_calibrate.py`.

**Produces:** `class DoAReader: __init__(dev=None)`; `find()` uses `usb.core.find(idVendor=0x2886, idProduct=0x001A)`; `read() -> tuple[int, bool]` performs `dev.ctrl_transfer(0xC0, 0, 0x80|18, 20, 5, 100000)` and parses with `parse_doa(resp: bytes) -> (angle, speech)` that handles both layouts: if `len(resp)>=5` try `struct.unpack('<BHH', resp[:5])` giving `(status, angle, flag)`; if angle > 359 fall back to `(resp[1], bool(resp[3]))`. `class FakeDoA` with settable `(angle, speech)`. `set_led_effect(dev, n)` and `set_led_color(dev, rgb)` via the same vendor control path (leave command ids as constants with a comment to verify against `xvf_host --list-commands`). udev rule: `SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="001a", MODE="0666"` plus `KERNEL=="ttyACM*", MODE="0666"`. `doa_calibrate.py` prints angle/speech at 5 Hz for 30 s.

- [x] Tests: `parse_doa` on both byte layouts; `DoAReader.read` with a fake device object; `FakeDoA`.
- [x] Commit `feat: ReSpeaker XVF3800 direction-of-arrival reader`.

### Task 10: Eyes link and expression table

**Files:** Create `bob/hardware/eyes.py`, `web/eyes-sim/index.html`, `web/eyes-sim/eyes.mjs`, `web/eyes-sim/expressions.mjs`, `web/eyes-sim/expressions.test.mjs`, `tests/test_eyes.py`; Modify `bob/server.py` (serve `/eyes` page and `/assets/eyes-sim/*` from an allowlist; add `eyes` server event so the browser sim mirrors hardware).

**Produces:**
- `EXPRESSIONS = ("neutral","curious","happy","love","sleepy","surprised","sad","angry_playful")`.
- `@dataclass EyeState: expression="neutral", gx=0.0, gy=0.0, blink=False, pupil=1.0` with `to_json() -> str` producing exactly `{"e":..,"gx":..,"gy":..,"blink":..,"p":..}` (rounded to 2 decimals).
- `class Eyes: __init__(ports: tuple[str,...], baud=115200)`; `open()` (pyserial, non-blocking, tolerant of missing ports → logs and marks `connected=False`); `set(state: EyeState)` writes one line to every port at ≤20 Hz (coalesce); `ping() -> dict[port, dict]`; `close()`. `class FakeEyes` records every `EyeState`.
- Browser sim: page with two round canvases (left green iris, right brown iris), reads `EyeState` JSON from the `/ws` `eyes` event **or** a local demo mode with a dropdown of expressions and mouse-driven gaze. `expressions.mjs` exports the geometry table (lid openness, pupil shape, iris scale per expression) so firmware and sim stay in sync (the firmware copies these numbers; test enforces they exist for all 8 expressions).

- [x] Tests: `EyeState.to_json` exact string; `Eyes.set` rate-limits and writes to a fake serial; missing port doesn't raise; Node test: every expression has all geometry keys and values in range.
- [x] Commit `feat: eye link protocol and browser eye simulator`.

### Task 11: Eye firmware (ESP32-S3-Touch-LCD-1.85)

**Files:** Create `firmware/eye/platformio.ini`, `firmware/eye/src/main.cpp`, `firmware/eye/src/eye.h`, `firmware/eye/src/eye.cpp`, `firmware/eye/src/protocol.h`, `firmware/eye/README.md`, `scripts/flash_eyes.sh`, `scripts/build_eyes.sh`.

**platformio.ini** (from research; mark untested on hardware):
```ini
[env:eye]
platform = https://github.com/pioarduino/platform-espressif32/releases/download/stable/platform-espressif32.zip
board = esp32-s3-devkitc-1
framework = arduino
board_build.arduino.memory_type = qio_opi
board_build.flash_mode = qio
board_upload.flash_size = 16MB
board_build.partitions = default_16MB.csv
build_flags = -DBOARD_HAS_PSRAM -DARDUINO_USB_MODE=1 -DARDUINO_USB_CDC_ON_BOOT=1
lib_deps = moononournation/GFX Library for Arduino@^1.6.7, bblanchon/ArduinoJson@^7
monitor_speed = 115200
upload_speed = 921600
```
**Display init:** `Arduino_ESP32QSPI(21, 40, 46, 45, 42, 41, false)`; `Arduino_ST77916(bus, -1, 0, true, 360, 360)`; before `gfx->begin(80000000)`, pulse LCD reset through TCA9554 at I2C 0x20 on `Wire.begin(11, 10)`: configure EXIO2 as output (config reg 0x03 clear bit 2), write output reg 0x01 bit2 low 10 ms then high 50 ms. Backlight `ledcAttach(5, 20000, 10)` and `ledcWrite(5, 800)`. Canvas: `Arduino_Canvas(360, 360, gfx)` → draw → `flush()`.
**Eye rendering (eye.cpp):** procedural; parameters per expression copied from `web/eyes-sim/expressions.mjs`: `upperLid`, `lowerLid` (0–1 coverage), `irisScale`, `pupilScale`, `pupilShape` (`round|heart`), `tilt`. Draw order: black background → white sclera circle r=150 → iris (filled circle, colour from `side`: green `0x3F8F3A`, brown `0x6B3E1E`, with a darker ring) → pupil (circle or heart made of two circles + a triangle) → highlight → eyelids as black fills from top/bottom with slight tilt → goggle-frame ring is physical, none drawn. Gaze offsets iris/pupil by `gx*60, gy*60` px. Smooth: each frame `cur += (target-cur)*0.25`. Auto-blink every 3000–7000 ms (120 ms close, 100 ms open). Idle saccades ±6 px every 1–3 s.
**Protocol (protocol.h):** read lines from `Serial` (USB CDC); JSON with `e/gx/gy/blink/p` updates targets; `{"cmd":"side","value":"L"}` stores side in `Preferences` namespace `eye`; `{"cmd":"ping"}` answers `{"ok":1,"side":"L","fps":N,"fw":"0.1.0"}`; `{"cmd":"bl","value":0..1023}` sets backlight. Unknown → `{"err":"unknown"}`. If no message for 2 s after having received some, keep last state (do not reset).
**Scripts:** `build_eyes.sh` runs `pio run -d firmware/eye` then `esptool --chip esp32s3 merge-bin -o firmware/eye/dist/eye-merged.bin --flash-mode dio --flash-size 16MB 0x0 bootloader.bin 0x8000 partitions.bin 0xe000 boot_app0.bin 0x10000 firmware.bin` (paths from `.pio/build/eye/` and the framework package's `tools/partitions/boot_app0.bin`). `flash_eyes.sh <port> <L|R>` writes the merged bin at 0x0 with `--before default-reset --after hard-reset`, waits 3 s, sends `{"cmd":"side","value":"<L|R>"}` and `{"cmd":"ping"}` via `python -m serial.tools.miniterm`-free approach (a tiny Python snippet using pyserial), prints the reply.

- [x] `pio run -d firmware/eye` builds on this Mac (install pioarduino platform; first build downloads toolchains). Commit `dist/eye-merged.bin` (size ~1–2 MB; acceptable for tomorrow) or keep in the USB bundle if >5 MB.
- [x] README lists: which USB port appears (`/dev/cu.usbmodem*`), BOOT-button recovery, how to tell L from R, and the "wrong colours → alternate init table" note.
- [x] Commit `feat: ESP32-S3 round LCD eye firmware`.

### Task 12: Camera and face recognition

**Files:** Create `bob/vision/__init__.py`, `bob/vision/camera.py`, `bob/vision/faces.py`, `scripts/enroll_faces.py`, `scripts/fetch_models.py`, `tests/test_faces.py`, `tests/fixtures/faces/` (generate 2–3 synthetic face-like images is not enough for SFace; instead download two public-domain portrait photos of *different* people from Wikimedia Commons at test time only if `BOB_NET_TESTS=1`, else skip; also include a tiny unit test that runs YuNet on a blank image and expects no faces).

**Produces:**
- `fetch_models.py` downloads to `models/vision/`: `face_detection_yunet_2023mar.onnx` and `face_recognition_sface_2021dec.onnx` from `https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/...` (fallback HF mirror), optional `facial_expression_recognition_mobilefacenet_2022july.onnx`; verifies sizes (232,589 / 38,696,353 / 4,791,892 bytes).
- `class Camera: __init__(index=-1, width=640, height=480)`; `open()` auto-detects by trying `/dev/video0..9` with `CAP_V4L2` (Mac: `CAP_AVFOUNDATION` index 0) and MJPG; `read() -> np.ndarray|None`; runs in a thread with `latest()` returning the newest frame; `FakeCamera(frames: list)` cycles frames.
- `@dataclass Person: name: str|None, score: float, bbox: tuple[int,int,int,int], cx: float, cy: float, size: float, emotion: str|None` where `cx, cy` are normalized -1..1 (positive = right/down) and `size` is bbox height / frame height.
- `class FaceEngine: __init__(models_dir, threshold=0.363)`; `load()`; `enroll_dir(people_dir) -> dict[str, np.ndarray]` (mean-normalized embedding per person from 1–5 images; writes `people/embeddings.npz`); `load_embeddings(path)`; `detect(frame) -> list[Person]` (names by best cosine match ≥ threshold, else `None`).
- `class Tracker: update(persons) -> list[Person]` with 5-frame majority vote per face slot (match by IoU > 0.3) and `seen_recently(name, window_s=600)` / `mark_seen(name)`.

- [x] Tests: `Person` normalization math; `Tracker` majority vote and cooldown with a fake clock; `FaceEngine.detect` on a blank image → empty (requires models; `pytest.importorskip("cv2")` and skip if models missing); network-gated test enrolls person A from photo 1 and recognizes photo 2 of A but not B.
- [x] Commit `feat: camera and YuNet/SFace face recognition with enrollment`.

### Task 13: Director

**Files:** Create `bob/director.py`, `tests/test_director.py`.

**Produces:** `class Director: __init__(robot, eyes, base=None, doa=None, phrases: PhrasePlayer, settings, clock=time.monotonic, rng=random.Random(0))`, with `async def tick(persons: list[Person], doa: tuple[int,bool]|None, now)` called at 10 Hz, and event hooks `on_speech_start()`, `on_transcript(text)`, `on_assistant_text(text)`, `on_speaking(bool)`, `on_tool(name, args)`. Outputs go through `eyes.set(EyeState)`, `phrases.say(key_or_text)` (queue a cached WAV or a TTS line into the audio queue with priority below conversation), `robot.command("offer_banana")`, `base.drive/stop`. Rules exactly as spec §8 with constants at the top (`GREET_STABLE_S=1.0`, `SEEN_COOLDOWN_S=600`, `LOVE_S=3.0`, `IDLE_SLEEPY_S=60`, `GIFT_MIN_S=240`, `GIFT_MAX_S=480`, `TURN_DEADBAND_DEG=20`, `CLOSE_SIZE=0.35`). `PhrasePlayer` is a small class in `bob/director.py` with `say(text_or_key)` and a `FakePhrasePlayer`. Base motion only when `settings.hardware=="real"` and `console.armed` (a simple object with `.armed: bool` and `.estop: bool`).

- [x] Tests (pure, fake clock/rng): recognized person greeted once after 1 s stability and not again within cooldown; "banana" in transcript → love for 3 s then back; idle → sleepy after 60 s and wakes on speech; DoA outside deadband with real hardware and armed console → `base.drive` called with omega sign matching angle, inside deadband → stop; estop → no drive calls ever; random gift fires within window and only when `robot.state.banana=="compartment"`.
- [x] Commit `feat: Director behavior layer`.

### Task 14: Server wiring, console and headless mode

**Files:** Modify `bob/server.py`, `bob/voice.py` (tool hooks), `index.html`/`web/app.mjs` (link to eyes sim, show person/doa in the workbench); Create `web/console.html`, `tests/test_server_modes.py`.

**Produces:** `bob.server` builds everything from `Settings.from_env()`:
- `mode=sim` (default): behaves exactly as today plus `Director` with `FakeEyes` mirrored to the browser via the `eyes` event, `FakeDoA`, `FakeCamera` (no frames) — all existing tests pass unchanged.
- `mode=cloud|local` with `hardware=real`: `FeetechBus` opened lazily on first `enable`; `ArmHardware` replaces `SimHardware`; `Base`; `DoAReader`; `Eyes`; `Camera`+`FaceEngine` thread; `AudioIO` + `RobotClient` so Bob talks without a browser. Startup never moves motors. `/api/health` reports each device's status.
- `/console?token=...` serves `web/console.html`: STOP (sends `action: stop_motion` and `console: {"estop": true}`), ARM/DISARM wander (dead-man: page sends `console: {"armed": true}` every 250 ms while held; server disarms if not refreshed within 600 ms), teleop arrows (`drive: {vx, vy, omega}` at ≤10 Hz, only while armed), status panel (state, person, doa, last transcript). Token check: reject if `settings.console_token` set and query token mismatches; when the token is set, the WebSocket origin check allows the LAN host too.
- New server events: `person` `{name, cx, cy, size, emotion}`, `doa` `{angle, speech}`, `eyes` (EyeState JSON), `console` `{armed, estop}`.
- `scripts/doctor.py`: prints a table: OpenAI reachable, ElevenLabs quota, audio device found (lists `sounddevice.query_devices()`), XVF3800 USB control, servo port + scan result, eye ports ping, camera frame, models present, phrase cache count; exit code non-zero if any P0 item fails.

- [x] Tests: sim mode health includes `eyes`/`doa` keys; console token rejection; `console` armed timeout disarms; `drive` ignored when not armed; existing `tests/test_server.py` still passes.
- [x] Commit `feat: real-hardware server wiring, phone console and doctor`.

### Task 15: Jetson setup, bundle, deploy runbook

**Files:** Create `scripts/jetson_setup.sh`, `scripts/make_bundle.sh`, `scripts/bob.service`, `docs/DEPLOY.md`; Modify `README.md` (short "Event robot" section linking DEPLOY.md).

**jetson_setup.sh** (idempotent, `set -euo pipefail`): apt `libportaudio2 libusb-1.0-0 python3-venv v4l-utils alsa-utils git curl`; install uv if missing; `uv python install 3.12`; `uv sync --group robot --python 3.12` (offline: `UV_OFFLINE=1 UV_FIND_LINKS=/media/$USER/*/bundle/wheels`); `python scripts/fetch_models.py` (or copy from bundle); install udev rules and reload; add user to `dialout`; write `~/.asoundrc` making the XVF3800 default (`pcm.!default { type plug slave.pcm "hw:Array" }` with the card name discovered from `arecord -l`); install `bob.service` (systemd user unit: `EnvironmentFile=%h/bobot/.env`, `ExecStart=%h/bobot/.venv/bin/uvicorn bob.server:app --host 0.0.0.0 --port 8766`, `Restart=on-failure`), enabled but **not** started; print next steps.

**make_bundle.sh** (run on the Mac): creates `bundle/` with the repo archive (`git archive HEAD`), `wheels/` via `uv pip download --python-version 3.12 --python-platform aarch64-manylinux_2_28 -r <(uv export --group robot --no-hashes)`, `models/vision/*.onnx`, `assets/phrases/`, `firmware/eye/dist/eye-merged.bin`, a copy of `.env` (warn: contains keys; the stick must stay with you), and `README-USB.txt`. Copies to a mounted volume if `--to /Volumes/NAME` given.

**docs/DEPLOY.md** sections, each with exact commands and a "you know it worked when": 0) Bring list (from spec §2); 1) First boot (USB-C→DP, create user `bob`, check `cat /etc/nv_tegra_release`, `nvpmodel -m 0`, `jetson_clocks`); 2) Network (`nmcli device wifi connect`, or Ethernet, or USB tethering; find IP `hostname -I`); 3) Get the code (git clone or `tar xf` from USB) and run `scripts/jetson_setup.sh`; 4) `.env` (copy from USB or paste keys), `BOB_MODE=cloud BOB_HARDWARE=real`; 5) `uv run python scripts/doctor.py`; 6) Audio check (`arecord`/`aplay` loopback, speaker on the JST, set volume with `alsamixer`); 7) Eyes: flash from Mac, plug into hub, `doctor` shows ping OK; 8) Faces: put portraits in `people/<Name>/`, run `enroll_faces.py`; 9) Arm: `discover_servos.py`, then `teach_poses.py` with the banana; 10) Base: `doa_calibrate.py`, then console teleop test with wheels off the ground first; 11) Start `systemctl --user start bob`, open `http://<ip>:8766/console?token=...` on the phone; 12) Show flow and recovery (restart service, disarm, stop). Include a troubleshooting table (no audio device → check `lsusb`; `ttyACM` permission → udev/dialout; camera not found → `v4l2-ctl --list-devices`; eyes black → backlight/init table; STT empty → mic channel/gain).

- [ ] `bash -n` both scripts; run `make_bundle.sh` on the Mac to `bundle/` (wheel download will take minutes; verify a few aarch64 wheels landed, e.g. `numpy`, `opencv_python`); do not commit `bundle/` (gitignore it).
- [ ] Commit `docs: Jetson setup, USB bundle and deploy runbook`.

### Task 16: Final verification and handoff

- [ ] `uv run pytest -q`, `node --test web/**/*.test.mjs web/*.test.mjs`, `uv run ruff check bob tests scripts`, `uv run ruff format --check bob tests scripts`, `pio run -d firmware/eye` all green; record outputs.
- [ ] Start the server in sim mode, open `/` and `/eyes` in Chrome via the browser tools, screenshot both, and confirm the eye sim reacts to typing "banana" (love expression).
- [ ] Update `docs/ROADMAP.md` and `docs/ARCHITECTURE.md` with a short "September 10 event build" section pointing at the spec and DEPLOY.md.
- [ ] Push branch `jetson-event` to origin and open a PR (`gh pr create`) with the summary and the shopping list.

## Self-review notes

Spec coverage: §4 → Tasks 3–5; §5 → Tasks 9, 12; §6 → Tasks 10–11; §7 → Tasks 6–8; §8 → Task 13; §9 → Task 15; §10 → each task's tests + Task 16. Names used across tasks: `Settings`, `EyeState`, `Eyes/FakeEyes`, `Person`, `Tracker`, `FeetechBus/FakeBus`, `ArmHardware`, `Base`, `DoAReader/FakeDoA`, `AudioIO/FakeAudioIO`, `RobotClient`, `Director`, `PhrasePlayer` — consistent.

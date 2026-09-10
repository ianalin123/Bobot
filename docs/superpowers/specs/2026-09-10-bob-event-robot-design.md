# Bob the Minion: event robot on Jetson — design

Date: 2026-09-10. Event and hardware day: 2026-09-11 (same day). Owner: Sissi. Repo: `ianalin123/bobot`.

## 1. Goal

Turn the existing Mac-only simulator into a deployable event robot on the real hardware. Bob (a 10-inch Minion plush) must, in priority order:

- **P0 Voice**: hear people through the mic array, answer in a Minion persona (mix of Minionese and short English), speak through the speaker, and stop talking when interrupted.
- **P0 Faces**: recognize up to 15 pre-enrolled people from portrait photos and greet them by name with one of 100 randomized Minionese greetings.
- **P0 Eyes**: two round LCD eyes that look like Bob's (left eye green, right eye brown, silver goggles are physical), blink, follow the person, show heart pupils when "banana" is mentioned, and reflect moods.
- **P1 Arm**: the SO-101 holds a real banana in a cradle, presents it to a person, releases on confirmation, stows.
- **P1 Turn-to-voice**: rotate toward whoever is talking using the mic array's direction-of-arrival.
- **P2 Base**: creep toward a recognized/talking person, stop at conversational distance; supervised "wander" mode with a phone e-stop.

Everything must be testable on this Mac today without hardware (fakes and simulators), and deployable tomorrow with one setup script plus an offline USB bundle.

## 2. Hardware and what it means

| Item | Facts that matter | Consequence |
| --- | --- | --- |
| Seeed reComputer Mini J3011 (Jetson Orin Nano 8GB), fresh | JetPack 6.0 preinstalled on NVMe. **No HDMI** (display only via USB-C DisplayPort). **No Wi-Fi module** (M.2 Key E empty). **No CSI camera connector.** 2× USB-A 3.2, 1× USB-C. Python 3.10 system. | First boot needs a USB-C→DP/HDMI adapter + monitor + keyboard. Network needs Ethernet (extension board RJ45, if present) or a USB Wi-Fi dongle or phone USB tethering. Camera must be USB. A powered USB hub is required. `uv` installs Python 3.12 for the venv. |
| ReSpeaker XVF3800 USB 4-mic array | USB Audio Class 2, 16 kHz, 2 capture channels: ch0 = beamformed+AEC mono. DoA angle + speech flag via vendor USB control transfer (pyusb, VID 0x2886 PID 0x001A). Has onboard amp + JST speaker connector and 3.5 mm jack; playback through it is the AEC reference. 12 WS2812 LEDs. | Bob's speaker plugs into the array's JST (verify pitch/polarity with a meter). Set the array as ALSA default sink and source. Needs a udev rule for USB control. |
| CQRobot 5 W 8 Ω speaker | Passive, JST-PH2.0 lead. | Amp comes from the XVF3800. Backup: PAM8403 board off the 3.5 mm jack. |
| SO-101 follower (borrowed, state unknown), 12 V STS3215 | Feetech bus, 1 Mbaud, Waveshare Bus Servo Adapter → `/dev/ttyACM0`. IDs 1–6 by LeRobot convention. Calibration lives in servo EEPROM (homing offset, limits) and can be read back. Gripper opening undocumented, measure. Payload ~400 g at extension; banana ~120–150 g is fine. | Pure-Python `feetech-servo-sdk`. Discovery script scans IDs. Teach mode = torque off, read positions. |
| LeKiwi-style base, 3 omni wheels, STS3215 | Same bus/adapter as arm, IDs 7 (left), 8 (back), 9 (right), velocity mode. Known kinematics (wheel r=0.05 m, base r=0.125 m). | One serial driver for both. Wheels always get `Goal_Velocity=0` on any exit. |
| 2× Waveshare ESP32-S3-Touch-LCD-1.85 (SKU 28514) | ST77916 360×360 over QSPI (CS 21, SCK 40, D0–3 46/45/42/41), backlight PWM GPIO5, LCD reset on TCA9554 EXIO2 (I2C 11/10). Native USB (`/dev/cu.usbmodem*`), 16 MB QIO flash, 8 MB OPI PSRAM. Arduino_GFX supports it; PlatformIO via pioarduino. | Firmware built here with PlatformIO, flashed with one script. Serial JSON protocol from the Jetson; each eye is told which side it is. |
| Tiny camera (connector unknown) | Jetson has no CSI, so if it's a ribbon-cable module it cannot be used. | Plan for a USB webcam; the code auto-detects `/dev/video*`. Bring any USB webcam as backup. |
| Power | Unknown battery. Servos need 12 V 5 A+; Jetson needs 12–54 V on XT30. | Bench supplies for the day; not this spec's problem beyond documenting. |

Tomorrow's shopping/borrow list (blocking items marked ★): ★ USB-C→HDMI/DP adapter or USB-C monitor, ★ USB keyboard+mouse, ★ network path (Ethernet cable+extension board, or USB Wi-Fi dongle, or Android/iPhone USB tethering), ★ powered USB hub (≥5 ports), ★ USB webcam if the tiny camera is CSI, ★ 12 V 5 A supply for servos, USB drive with the offline bundle, multimeter, banana.

## 3. Architecture

One Python process on the Jetson (`bob.server`, FastAPI + asyncio) is the brain; it keeps the existing authoritative `Robot` state machine and WebSocket protocol, and adds hardware adapters and cloud providers behind the same interfaces. The browser workbench keeps working on the Mac in `sim` mode.

```
                    ┌──────────────── bob.server (asyncio) ────────────────┐
 XVF3800 ─ALSA──▶   │ audio.capture ─▶ VAD/turn ─▶ STT ─▶ LLM ─▶ TTS ─▶ audio.play ─▶ XVF3800 → speaker
 XVF3800 ─USB───▶   │ doa.reader ───────────────────────┐
 USB cam ────────▶  │ vision.faces ──▶ people tracker ──┼──▶ Director (behaviors) ──▶ Robot (state machine)
                    │ eyes.link ◀──────────────────────┘        │                        │
 2× ESP32 ◀─serial──│                                            ▼                        ▼
                    │                                      base driver ◀──── FeetechBus ────▶ arm driver
 phone/laptop ◀─ws──│ /ws (existing protocol + new events), /console (e-stop + teleop page)
                    └───────────────────────────────────────────────────────────────────────┘
```

Packages (new or changed):

- `bob/config.py`: one settings object from env (`BOB_MODE=sim|cloud|local`, device paths, model IDs, thresholds).
- `bob/providers.py` (existing local) + `bob/providers_cloud.py`: `OpenAISTT`, `OpenAILLM`, `OpenAITTS`, `ElevenLabsTTS`. All providers share the existing duck-typed interface (`transcribe(bytes)->str`, `stream(messages, tools)`, `synthesize(text)->wav bytes`). A `FallbackTTS` and `FallbackSTT` wrap cloud with local.
- `bob/persona.py`: Bob system prompt, Minionese lexicon, 100 greetings, phrase cache manifest.
- `bob/audio.py`: ALSA capture/playback via `sounddevice`, energy VAD with pre-roll (port of `web/vad.mjs`), playback queue with instant cancel. Emits the same `playback_started/finished` acks the browser does, so `VoiceSession` is reused unchanged.
- `bob/hardware/feetech.py`: minimal STS3215 bus (read/write registers, sync write, sign-magnitude encode, torque on/off, scan).
- `bob/hardware/arm.py`: `ArmHardware` implementing the existing `Hardware` protocol (`perform(action)`, `stop()`) using named poses from `config/poses.json`; grasp confirmation via `Present_Load`/`Present_Current`; speed and acceleration caps; per-step relative-target clamp.
- `bob/hardware/base.py`: `Base` with `drive(vx, vy, omega)`, `stop()`, kinematics, watchdog (stops if no command for 300 ms).
- `bob/hardware/respeaker.py`: DoA + speech flag reader, LED control.
- `bob/hardware/eyes.py`: serial link to both eyes, JSON lines; `set(expression, gaze_x, gaze_y, blink)`; a `FakeEyes` records calls.
- `bob/vision/camera.py`, `bob/vision/faces.py`: capture thread; YuNet detect + SFace recognize against enrolled embeddings; optional expression model. Publishes `Person(name|None, bbox, center_x, size, emotion)` at ~10 Hz.
- `bob/director.py`: the behavior layer. Consumes people + DoA + conversation events; decides gaze, expression, greetings, when to offer a banana, when to turn/creep. Only the Director talks to `Robot`, `Base`, and `Eyes`.
- `web/eyes-sim/`: browser page that renders the same eye protocol (dev without hardware).
- `firmware/eye/`: PlatformIO project.
- `scripts/`: `jetson_setup.sh`, `discover_servos.py`, `teach_poses.py`, `enroll_faces.py`, `flash_eyes.sh`, `make_bundle.sh`, `render_phrases.py`, `doctor.py` (checks every device and API and prints a green/red table).
- `docs/DEPLOY.md`: the tomorrow runbook.

## 4. Voice pipeline (P0)

- Capture: `sounddevice` input stream on the XVF3800, channel 0, 16 kHz mono. Turn detection: energy VAD with 300 ms pre-roll and 650 ms trailing silence, 15 s cap, plus the array's speech flag as a gate when available. Barge-in: sustained energy during playback cancels playback immediately and starts a new turn (same request-id semantics as the browser).
- STT: `gpt-4o-mini-transcribe` (fallback `whisper-1`, then local faster-whisper if `BOB_LOCAL_FALLBACK=1` and models present). Skip turns shorter than 0.4 s or below RMS floor.
- LLM: `gpt-4.1-mini` by default (`BOB_LLM_MODEL` overrides, e.g. `gpt-5.6-luna` with low reasoning). Chat Completions streaming with tools `offer_banana`, `stop_motion`, `look_at_speaker`, `set_expression`. Reply cap ~60 words. The exact-command bypass from `voice.py` stays. Persona: Bob is sweet, childlike, loves his teddy bear Tim, says "Bello", "Poopaye", "Tank yu", "Bee-do bee-do", "Banana!", "Papoy", "Me want banana", laughs "hehehe"; 60% English so people understand, Minionese sprinkled.
- TTS: dynamic replies via `gpt-4o-mini-tts` (voice `ash` or `coral`, instructions "high-pitched giggly Minion, fast, playful"), then a +4 to +6 semitone pitch shift with `librosa` or a resample trick (configurable), output 16 kHz WAV. Stock phrases (greetings, "Bello!", "Banana!", laughs) pre-rendered with ElevenLabs `eleven_flash_v2_5` into `assets/phrases/*.wav` by `render_phrases.py`, budgeted under 6,000 of the 10,000 free characters; the cache is committed-ignored but included in the USB bundle. Local fallback: Piper if installed, else the phrase cache only.
- Playback: through the XVF3800 so its AEC gets the reference. Sentence-chunked, cancellable queue, acks feed `VoiceSession`.
- Latency target: first audio under 1.5 s from end of speech on Wi-Fi. Metrics logged as now.

## 5. Perception (P0/P1)

- Camera: `cv2.VideoCapture(index, CAP_V4L2)` MJPG 640×480, auto-detect index. `opencv-python>=4.10,<5` (aarch64 wheels exist). No GStreamer.
- Faces: YuNet 2023mar + SFace 2021dec ONNX from the opencv_zoo media URLs (downloaded by setup and bundled). `enroll_faces.py` reads `people/<Name>/*.jpg` (1–5 portraits each), stores embeddings in `people/embeddings.npz`. Match = cosine ≥ 0.363 (tunable), majority over the last 5 frames before a name is committed; a name is "seen" once per 10 minutes to avoid re-greeting loops. Optional expression model (mobilefacenet FER, 7 labels) behind `BOB_EMOTION=1`.
- Direction of arrival: `respeaker.py` polls DoA at 10 Hz. Angle convention documented after a bench check tomorrow (a calibration script prints the angle while you talk from a known side).
- Consent: only enrolled people are named; everyone else is "friend". No images are stored; embeddings only.

## 6. Eyes (P0)

- Firmware: Arduino framework via pioarduino, Arduino_GFX `Arduino_ESP32QSPI` + `Arduino_ST77916`, `Arduino_Canvas` in PSRAM, 30–60 fps. Procedural eye: sclera white disk, iris (green `#3F8F3A`-ish or brown `#6B3E1E`-ish, chosen by `side`), black pupil, upper/lower eyelids for blink and mood (happy = lower lid up, sleepy = upper lid down, curious = one eye wider), pupil dilation, gaze offset, heart-shaped pupil for `love`, and micro-saccades. Auto-blink every 3–7 s. Each board stores `side` (`L`/`R`) in NVS; set once via serial command `{"cmd":"side","value":"L"}`.
- Protocol: 115200 baud JSON lines from the Jetson: `{"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}` at ≤20 Hz; firmware interpolates. Expressions: `neutral, curious, happy, love, sleepy, surprised, sad, angry_playful`. Firmware answers `{"ok":1,"side":"L","fps":58}` on `{"cmd":"ping"}`. Optional Wi-Fi UDP mode with the same payload is a stretch.
- Sim: `web/eyes-sim/` renders both eyes with Canvas 2D from the same expression table so the Director can be tested visually on the Mac. The existing workbench face gets a link to it.
- Flash: `scripts/flash_eyes.sh <port> <L|R>` uses the merged bin built into `firmware/eye/dist/`.

## 7. Arm and base (P1/P2)

- Bus: one `FeetechBus(port, baud=1_000_000)` shared by arm and base with a lock. Startup never energizes motors; `arm.enable()` is explicit.
- Discovery: `discover_servos.py` scans IDs 1–20 at 1 Mbaud (and 115200 as fallback), prints model, position, voltage, and reads homing offset and limits from EEPROM into `config/calibration.json`.
- Teach: `teach_poses.py` disables torque, prompts for each named pose (`stow`, `above_cradle`, `grasp`, `lift`, `present`, `release`), records `Present_Position` for IDs 1–6 and writes `config/poses.json`. It also records `gripper_open` and `gripper_closed_on_banana` readings and the load threshold that means "holding".
- Motion: position mode, `Acceleration` 30, `Goal_Velocity` ~600, per-tick relative clamp of 150 raw. Actions map to pose sequences: `open_compartment`→no-op or LED, `grasp_banana`→above_cradle→grasp→close gripper→verify load→lift, `present_banana`→present, `release_banana`→open gripper, `stow_arm`→lift→stow. `stop()` freezes goals at present positions then disables torque only if `BOB_STOP_TORQUE_OFF=1` (gravity collapse risk).
- Base: velocity mode on IDs 7–9, speed caps (0.15 m/s, 40 °/s). Behaviors: `turn_to(angle)` from DoA or face x-offset with a P controller; `approach()` creeps while a face bbox height is below the "close" threshold and stops when reached or lost; `wander()` = random short turn + 1 s creep, only while the console e-stop page shows a held dead-man button. Watchdog stops wheels within 300 ms of silence from the Director.
- Console: `/console` mobile page with a big STOP, a dead-man hold button for wander, arrow teleop, and live status. Same-origin restriction is relaxed to the LAN for this page behind a `BOB_CONSOLE_TOKEN` query token.

## 8. Director (behaviors)

State: `idle`, `engaged(person)`, `offering`, `wandering`. Rules:

1. Speech detected → gaze toward DoA/face, `curious`; while Bob talks, `happy`; on "banana" in transcript or reply → `love` for 3 s.
2. New recognized face → after 1 s of stable identity: play a random greeting from the 100 (name substituted), `happy`, mark seen. Unknown face after 3 s → 20% chance of a random "Bello!" style hello.
3. Banana offers: on explicit request → existing `offer_banana`. Random gift: every 4–8 minutes while engaged with a person and a banana is loaded, Bob announces "Banana for you!" and offers; recognized people get 3× the odds.
4. P1: on speech with DoA outside ±20° → `turn_to`. P2: engaged person far and console armed → `approach`.
5. Idle 60 s → `sleepy` with slow blinks and an occasional yawn phrase; any sound wakes.

## 9. Deploy plan (tomorrow)

`docs/DEPLOY.md` covers, in order: first boot on the DP monitor and user creation; network via nmcli or Ethernet or USB tethering; `git clone` (or copy from USB) and `scripts/jetson_setup.sh` (installs uv + Python 3.12, apt deps, venv, ONNX models, udev rules for XVF3800 and ttyACM, dialout group, ALSA default device, systemd unit); `scripts/doctor.py` until all green; flash eyes from the Mac (`flash_eyes.sh`); `enroll_faces.py`; `discover_servos.py`; `teach_poses.py`; `render_phrases.py` (or use the bundled cache); start `bob` service; open `/console` on a phone. USB drive answer: yes, a plain USB stick is enough; `scripts/make_bundle.sh` writes repo + wheelhouse (aarch64, built on Mac via `uv pip download --python-platform aarch64-manylinux_2_28`) + models + phrases + eye firmware to it.

## 10. Testing

- Unit tests with fakes for every provider and device (`FakeBus` simulates servo registers, `FakeEyes`, `FakeCamera` yields images from `tests/fixtures/`, `FakeDoA`). Director tested as a pure state machine with scripted events.
- Face pipeline tested on this Mac with real OpenCV models and a few public-domain portraits in fixtures (or synthetic faces if none are bundled).
- Cloud providers have live smoke tests gated by `BOB_LIVE=1` (cost-bounded: one STT, one LLM, one TTS call).
- Firmware compiles in CI-like fashion with `pio run`; eye sim page renders every expression via a Node test of the expression table.
- Existing 22 tests keep passing; `ruff` clean.

## 11. Out of scope

LinkedIn/profile lookup, autonomous unsupervised crowd navigation, obstacle avoidance, full offline operation (fallbacks are best-effort), battery design, plush tailoring, character licensing.

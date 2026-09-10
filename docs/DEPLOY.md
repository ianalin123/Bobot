# Deploying Bob on the Jetson (event day runbook)

Target: Seeed reComputer Mini J3011 (Jetson Orin Nano 8GB, JetPack 6.x on NVMe). Everything runs on the Jetson; your Mac is only for flashing the eyes and holding the USB bundle. Work through the steps in order; each has a "you know it worked when".

## 0. Bring

Blocking (★) means the day stops without it.

| ★ | Item | Why |
| --- | --- | --- |
| ★ | USB-C to HDMI/DisplayPort adapter (or USB-C monitor) + HDMI cable + monitor | The Mini has **no HDMI**; display is USB-C DP only. Needed for first boot. |
| ★ | USB keyboard + mouse | First-boot wizard |
| ★ | Network: Ethernet cable to a router/laptop **or** USB Wi-Fi dongle **or** phone USB tethering (Android: USB tethering; iPhone works via `usbmuxd`, less reliable) | The Mini ships **without** a Wi-Fi module |
| ★ | Powered USB hub, 5+ ports | Devices: ReSpeaker, servo adapter, camera, 2 eyes, keyboard. The Mini has 2× USB-A + 1× USB-C |
| ★ | USB webcam (any UVC) | The Mini has **no CSI connector**. If the tiny camera has a ribbon cable it will not work; if it has a USB plug it is fine |
| ★ | 12 V 5 A+ supply for the servo adapter; power for the Jetson (XT30, 12–54 V) | Motors + brain |
| ★ | USB stick with `bundle/` (made by `scripts/make_bundle.sh`) | Offline install path |
|  | Multimeter | Confirm JST speaker pinout/polarity before plugging into the ReSpeaker |
|  | 2× USB-C data cables for the eyes, 1 USB cable for the servo adapter, 1 for the ReSpeaker | |
|  | Bananas (2–3), foam or grip tape for the gripper | |
|  | Portrait photos of the people to recognize (1–5 each, phone photos are fine) | Put in `people/<Name>/` |
|  | This repo on your Mac with PlatformIO installed | Eye flashing |

## 1. First boot

1. Plug monitor (via USB-C), keyboard, mouse; power on. If nothing shows within a minute, try the other USB-C orientation and a different adapter; last resort is the NVIDIA SDK Manager flash from a Linux laptop (not planned for today).
2. Complete the Ubuntu wizard. Username `bob`, a password you remember, auto-login on.
3. Open a terminal:

```sh
cat /etc/nv_tegra_release        # R36.x = JetPack 6
sudo nvpmodel -m 0 && sudo jetson_clocks
```

You know it worked when: a desktop appears and the release file prints R36.

## 2. Network

```sh
nmcli device                      # find the interface
nmcli radio wifi on               # only if a Wi-Fi dongle is present
nmcli device wifi rescan && nmcli device wifi list
sudo nmcli device wifi connect "<SSID>" password "<PSK>"
hostname -I                       # note the IP for the phone console
ping -c 2 api.openai.com
```

Ethernet and USB tethering usually need nothing beyond plugging in. You know it worked when: `ping` answers.

Then make it stick forever (autoconnect, no power saving, a reconnect watchdog every minute):

```sh
bash scripts/wifi_forever.sh              # while connected to the venue Wi-Fi
```

After that the Jetson only needs power. Get its IP with `hostname -I` and SSH to it from your Mac instead of using the monitor.

## 3. Code and setup

With network:

```sh
git clone https://github.com/ianalin123/bobot ~/bobot
cd ~/bobot && git checkout jetson-event
bash scripts/jetson_setup.sh
```

From the USB stick (no network):

```sh
mkdir -p ~/bobot && tar -xzf /media/bob/<STICK>/bundle/bobot.tar.gz -C ~/bobot
cd ~/bobot && bash scripts/jetson_setup.sh --bundle /media/bob/<STICK>/bundle
```

The script is idempotent; re-run it after plugging in the ReSpeaker so it can set the ALSA default. Log out and back in once (dialout group). You know it worked when: it ends with "Next steps".

## 4. Keys and mode

`~/bobot/.env` (copied from the stick, or edit `.env.example`):

```
BOB_MODE=cloud
BOB_HARDWARE=real
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
BOB_CONSOLE_TOKEN=<pick a word>
```

Leave `BOB_SERVO_PORT` / `BOB_EYE_PORTS` at defaults for now; step 5 tells you the real ports.

## 5. Doctor

```sh
cd ~/bobot && uv run python scripts/doctor.py
```

Fix anything red, top to bottom. Typical fixes:

| Red line | Fix |
| --- | --- |
| OpenAI unreachable | network / key; `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"` |
| Audio device not found | `lsusb` shows `2886:001a`? `arecord -l`; re-run setup; or set `BOB_AUDIO_DEVICE` to the name from the doctor's device list |
| XVF3800 USB control denied | udev rule installed? `sudo udevadm trigger`; unplug/replug |
| Servo port | `ls /dev/ttyACM*`; the Waveshare adapter is usually the first ACM device; set `BOB_SERVO_PORT`; the eyes are the other two ACM devices, set `BOB_EYE_PORTS` |
| Camera | `v4l2-ctl --list-devices`; set `BOB_CAMERA_INDEX` |
| Models missing | `uv run python scripts/fetch_models.py` or copy from the stick |

You know it worked when: every P0 row is green.

## 6. Audio

1. Plug the CQRobot speaker into the ReSpeaker's JST speaker connector (check polarity with the meter; both wires are the same colour on some leads). Fallback: 3.5 mm jack into a small amp board.
2. Test:

```sh
arecord -d 3 -f S16_LE -r 16000 -c 2 /tmp/t.wav && aplay /tmp/t.wav
alsamixer                       # F6 to pick the ReSpeaker, raise PCM to ~80%
sudo alsactl store
```

You know it worked when: you hear yourself, and speech into the mic while the speaker plays does not echo back (AEC).

## 7. Eyes (from the Mac)

The eyes are Waveshare ESP32-S3-Touch-LCD-2.1 boards (round 2.1", 480x480). Slide the board's power switch to **ON**, then plug it into the Mac using the USB-C port labelled **USB** (not the one labelled UART). It shows up as `/dev/cu.usbmodem*`.

```sh
bash scripts/flash_eyes.sh /dev/cu.usbmodemXXXX L     # left eye = green iris
bash scripts/flash_eyes.sh /dev/cu.usbmodemYYYY R     # right eye = brown iris
```

If the board does not enumerate: hold BOOT, press and release RESET, release BOOT, retry. If the USB port still does not show up, flash through the other USB-C port (**UART**, a CH343 bridge, `/dev/cu.wchusbserial*`) with esptool as described in `firmware/eye/README.md`, then move the cable back to the USB port to set the side. Then plug both eyes into the Jetson hub (USB port, switch ON); the doctor's eye rows should show `ping ok side=L/R`. If the screen stays black: backlight (`{"cmd":"bl","value":900}` over serial) or the panel-init troubleshooting list in `firmware/eye/README.md`.

You know it worked when: both eyes blink on their own and the doctor pings both.

## 8. Faces

```sh
mkdir -p people/Sissi && cp /media/bob/<STICK>/photos/sissi*.jpg people/Sissi/
uv run python scripts/enroll_faces.py
```

Warns about images without a detectable face; use frontal, well-lit photos. You know it worked when: `people/embeddings.npz` exists and each name reports ≥1 image.

## 9. Arm (banana)

Power the servo adapter from the 12 V supply. Both jumpers on the adapter in the USB position.

```sh
uv run python scripts/discover_servos.py         # expect IDs 1-6 (arm) and 7-9 (wheels)
uv run python scripts/teach_poses.py             # torque off; move the arm by hand to each pose, press Enter
```

Teach order: stow, above_cradle, grasp (gripper around the banana in its cradle), lift, present, release. Keep the banana close to the base (short lever arm). The script measures the gripper load while holding the banana and sets the "holding" threshold. You know it worked when: `config/poses.json` exists and a dry run (`uv run python scripts/teach_poses.py --replay`) moves slowly through the poses without slipping.

## 10. Base

```sh
uv run python scripts/doa_calibrate.py           # talk from the left/right; note which angle is which
```

Wheels off the ground first. Start the service (step 11), open the console, hold ARM and tap the arrows. You know it worked when: the wheels spin the way the arrows say, and releasing ARM stops them within half a second.

## 11. Run

```sh
systemctl --user start bob
journalctl --user -u bob -f
```

Phone on the same network: `http://<jetson-ip>:8766/console?token=<BOB_CONSOLE_TOKEN>`. The big STOP button always works. ARM must be held for any base motion.

Show flow: say "Bello Bob" → Bob looks at you and answers. Walk in front of the camera as an enrolled person → greeting by name, heart eyes on "banana". Say "Please give me a banana" → arm presents; press "Confirm handoff" on the console (or the workbench) → release and stow.

## 12. Recovery

| Problem | Do |
| --- | --- |
| Bob stops responding | `systemctl --user restart bob` |
| Arm moved badly | STOP on the console, then `enable` again from the console once clear |
| No speech | check `journalctl` for STT errors; `BOB_LOCAL_FALLBACK=1` needs Whisper models |
| Wi-Fi died | phone hotspot + USB tethering; Bob keeps eyes/faces/phrase cache working offline, only conversation needs the cloud |
| Want the Mac workbench instead | `BOB_MODE=sim` on the Mac as before |

Keys were pasted in chat during development. Rotate both API keys after the event.

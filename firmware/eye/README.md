# Bob eye firmware (ESP32-S3-Touch-LCD-1.85)

One board per eye. Each Waveshare ESP32-S3-Touch-LCD-1.85 (ST77916, 360x360 round IPS, QSPI)
renders a procedural eye and takes JSON lines over its native USB-CDC port from the Jetson
(`bob/hardware/eyes.py`). The geometry and expression table are copied from the browser twin
in `web/eyes-sim/` (`expressions.mjs`, `eyes.mjs`), so what you see in the sim is what the LCD shows.

**Status: builds clean; not yet run on hardware.** Pin numbers and the reset sequence come from the
Waveshare wiki and the Arduino_GFX ST77916 driver, not from a board on the bench. See "Unverified
assumptions" at the bottom before blaming the code.

## Files

| file | what |
|---|---|
| `platformio.ini` | pioarduino platform, `esp32-s3-devkitc-1`, `qio_opi` (16 MB flash + OPI PSRAM), USB CDC on boot |
| `src/main.cpp` | board bring-up: TCA9554 reset pulse, backlight PWM, QSPI bus + ST77916, PSRAM canvas, NVS side, main loop |
| `src/eye.h/.cpp` | expression table + procedural renderer (sclera, iris, pupil/heart, highlights, lids, blink, saccades) |
| `src/protocol.h` | JSON-lines parser (ArduinoJson 7): state, `ping`, `side`, `bl` |
| `dist/eye-merged.bin` | output of `scripts/build_eyes.sh`, flashable at `0x0` |

## Build

```sh
scripts/build_eyes.sh
```

Runs `pio run -d firmware/eye` and merges bootloader + partitions + boot_app0 + app with esptool
into `firmware/eye/dist/eye-merged.bin` (about 1 MB). Needs PlatformIO (`pio` on PATH or
`~/.local/bin/pio`); the first build downloads the pioarduino platform and the Xtensa toolchain
(several hundred MB). esptool is taken from PlatformIO's own venv, so nothing else to install.
The script auto-detects esptool 4.x (`merge_bin`) vs 5.x (`merge-bin`).

## Flash

Plug one board in over its **USB** port (the one marked USB, not UART). On a Mac it shows up as
`/dev/cu.usbmodem*` (e.g. `/dev/cu.usbmodem101` or `/dev/cu.usbmodem14201`; `ls /dev/cu.usbmodem*`
before and after plugging in). On the Jetson it is `/dev/ttyACM0` / `/dev/ttyACM1`.

```sh
scripts/flash_eyes.sh /dev/cu.usbmodem101 L    # left eye  (green iris)
scripts/flash_eyes.sh /dev/cu.usbmodem101 R    # right eye (brown iris)
```

The script writes the merged image at 921600 baud (`--before default-reset --after hard-reset`),
waits 3 s for the board to reboot and re-enumerate, then sends `{"cmd":"side","value":"L"}` and
`{"cmd":"ping"}` over pyserial and prints the replies. Expect:

```
side -> {"ok":1,"side":"L"}
ping -> {"ok":1,"side":"L","fps":55,"fw":"0.1.0"}
```

The side is stored in NVS (`Preferences` namespace `eye`, key `side`) and survives reflashing unless
the flash is erased (`esptool --chip esp32s3 erase-flash`). A board with no stored side boots as `L`.

### BOOT-button recovery

If esptool says "Failed to connect" or "wrong boot mode", the running firmware is holding the USB
port (or a crash loop is). Hold **BOOT**, tap **RESET** (or re-plug USB while holding BOOT), release
BOOT, then run the flash script again. The port name can change after this (`ls /dev/cu.usbmodem*`).
If the board shows up as `/dev/cu.usbmodem*` but nothing answers pings, do the same and reflash.

### Telling L from R

- Left eye = green iris (`#3F8F3A`); right eye = brown iris (`#6B3E1E`). That is Bob's heterochromia;
  the colour comes from the stored side, so a board that shows green is `L`.
- `{"cmd":"ping"}` reports `"side"`. The boot banner `{"boot":1,"side":"L"}` is printed once on reset.
- The `curious` expression opens the **right** eye wider (`lidAsym`), and `tilt` is mirrored per side.
- Wrong way round? Just send `{"cmd":"side","value":"R"}` (or rerun the flash script); no reflash needed.
  Label the boards with tape after setting them.

## Protocol (115200, JSON lines, USB CDC)

| send | reply |
|---|---|
| `{"e":"happy","gx":0.3,"gy":-0.1,"blink":false,"p":1.0}` | none (state update, <= 20 Hz; missing keys keep the previous value) |
| `{"cmd":"ping"}` | `{"ok":1,"side":"L","fps":58,"fw":"0.1.0"}` |
| `{"cmd":"side","value":"L"}` | `{"ok":1,"side":"L"}` (stored in NVS) |
| `{"cmd":"bl","value":800}` | `{"ok":1,"bl":800}` (backlight 0..1023) |
| anything else | `{"err":"unknown"}`; unparsable line: `{"err":"json"}` |

Expressions: `neutral curious happy love sleepy surprised sad angry_playful` (unknown names render as
neutral). `gx`/`gy` are -1..1 (60 px of travel), `p` is pupil dilation 0.3..2, `blink:true` is an
edge (one blink when it goes false -> true). The firmware smooths every parameter (`cur += (target -
cur) * 0.25` per frame), auto-blinks every `blinkRateMs` +/-30 % (120 ms close, 100 ms open) and adds
+/-6 px saccades every 1-3 s. If the link goes quiet the last state is kept.

Quick manual test: `uv run python -m serial.tools.miniterm /dev/cu.usbmodem101 115200`, then type a
line and press Enter.

## Wrong colours or a blank panel: alternate init table

Arduino_GFX's `Arduino_ST77916` ships with a default init table (`st77916_180_init_operations`,
written for a 1.8" 360x360 panel). Waveshare's own demo for the 1.85" board uses a different
vendor init table. If the panel lights up but colours are inverted, tinted, or the image is
garbled/offset:

1. Try flipping `ips` (`true` -> `false`) in `main.cpp`; that toggles the INVON command.
2. If that is not it, paste Waveshare's init sequence (from their `ESP32-S3-Touch-LCD-1.85` demo,
   `ST77916` driver) into a `static const uint8_t waveshare_185_init[]` array in `main.cpp` and pass
   it as the last two constructor arguments:
   `new Arduino_ST77916(bus, -1, 0, true, 360, 360, 0, 0, 0, 0, waveshare_185_init, sizeof(waveshare_185_init))`.

Rebuild, reflash. Nothing else in the firmware depends on the init table.

## Unverified assumptions (need a board to check)

1. **TCA9554 reset sequence.** The LCD reset is not on a GPIO but on EXIO2 of a TCA9554 I/O expander
   at I2C `0x20` (SDA=11, SCL=10). `main.cpp` clears bit 2 of the config register (`0x03`) to make it
   an output, drives bit 2 of the output register (`0x01`) low for 10 ms then high and waits 50 ms,
   all before `gfx->begin()`. The register map is standard TCA9554, but the EXIO bit assignment and
   address come from the wiki. If the panel stays black, scope EXIO2 or read the registers back.
2. **Arduino_GFX default ST77916 init table** (see the previous section). The QSPI pins
   (cs=21 sck=40 d0=46 d1=45 d2=42 d3=41), 80 MHz clock, `ips=true`, backlight on GPIO5 (LEDC
   20 kHz, 10-bit, default 800/1023) are from the wiki and untested here.

Also worth checking once on hardware: the measured `fps` in the ping reply (target 30+; a 360x360
RGB565 flush over 80 MHz QSPI is ~7 ms, the scanline lid pass ~3-4 ms), and that the canvas really
landed in PSRAM (`PsramCanvas` asks for `MALLOC_CAP_SPIRAM`; if PSRAM is missing it falls back to
internal RAM and `canvas->begin()` most likely fails, in which case the board prints
`{"err":"display"}` but still answers pings).

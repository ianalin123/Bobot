# Bob eye firmware (ESP32-S3-Touch-LCD-2.1)

One board per eye. Each Waveshare **ESP32-S3-Touch-LCD-2.1** (ST7701, 480x480 round IPS driven over
the ESP32-S3's 16-bit RGB parallel interface; 16 MB flash, 8 MB PSRAM) renders a procedural eye and
takes JSON lines over its native USB-CDC port from the Jetson (`bob/hardware/eyes.py`). The geometry
and expression table are copied from the browser twin in `web/eyes-sim/` (`expressions.mjs`,
`eyes.mjs`, 360 px) and scaled by 480/360, so what you see in the sim is what the LCD shows:
Bob's silver goggle rim with four rivets around the eye, Minion-yellow eyelids with a darker
crease, a shadow under the goggle lip and a soft glare across the lens. The rim never changes,
so it is rendered once into a PSRAM template and copied per frame.

**Status: builds clean; not yet run on hardware.** Pin numbers, RGB timings and the ST7701 init
table come from the Waveshare wiki and the ESP32_Display_Panel board file
`BOARD_WAVESHARE_ESP32_S3_TOUCH_LCD_2_1`, not from a board on the bench. See "Unverified
assumptions" at the bottom before blaming the code.

## Board facts (what the code encodes)

| what | where |
|---|---|
| LCD | ST7701S, 480x480, RGB565 over 16-bit RGB: DE 40, VSYNC 39, HSYNC 38, PCLK 41; R0-4 = 46/3/8/18/17, G0-5 = 14/13/12/11/10/9, B0-4 = 5/45/48/47/21 (wiki calls them R1-5 / B1-5; R0 and B0 are NC) |
| RGB timing | 16 MHz pixel clock, HPW 8 / HBP 10 / HFP 50, VPW 3 / VBP 8 / VFP 8, PCLK rising edge, 4800 px bounce buffer |
| ST7701 command SPI (init only) | 3-wire 9-bit, SCL = GPIO2, SDA = GPIO1 (shared with the TF card's SCK/MOSI), CS = expander EXIO3 |
| I/O expander | TCA9554 at I2C `0x20` (SDA 15, SCL 7, shared with touch CST820 `0x15`, IMU QMI8658 `0x6B`, RTC PCF85063 `0x51`). EXIO1 = LCD_RST, EXIO2 = TP_RST, EXIO3 = LCD_CS, EXIO4 = SD_CS, EXIO5/6 = IMU INT, EXIO7 = RTC INT, EXIO8 = buzzer |
| Backlight | GPIO6, LEDC 20 kHz 10-bit, default 800/1023 |
| USB | two USB-C ports: **USB** = ESP32-S3 native USB (GPIO19/20, what the firmware talks on), **UART** = CH343P USB-serial on GPIO43/44 with auto-download circuit |
| Power | slide switch ON/OFF next to the ports; BAT = MX1.25 2-pin 3.7 V Li-ion with charger; BOOT and RESET buttons |

## Files

| file | what |
|---|---|
| `platformio.ini` | pioarduino platform, `esp32-s3-devkitc-1`, `qio_opi` (16 MB flash + OPI PSRAM), USB CDC on boot |
| `src/main.cpp` | board bring-up: TCA9554 reset/CS lines, 3-wire SPI init table, `Arduino_ESP32RGBPanel` + `Arduino_RGB_Display`, PSRAM canvas, backlight PWM, NVS side, main loop |
| `src/eye.h/.cpp` | expression table + procedural renderer (sclera, iris, pupil/heart, highlights, lids, blink, saccades), 480 px geometry |
| `src/protocol.h` | JSON-lines parser (ArduinoJson 7): state, `ping`, `side`, `bl`; reports `fw` `0.3.0` |
| `dist/eye-merged.bin` | output of `scripts/build_eyes.sh`, flashable at `0x0` |

## Build

```sh
scripts/build_eyes.sh
```

Runs `pio run -d firmware/eye` and merges bootloader + partitions + boot_app0 + app with esptool
into `firmware/eye/dist/eye-merged.bin` (about 0.5 MB). Needs PlatformIO (`pio` on PATH or
`~/.local/bin/pio`); the first build downloads the pioarduino platform and the Xtensa toolchain
(several hundred MB). esptool is taken from PlatformIO's own venv, so nothing else to install.
The script auto-detects esptool 4.x (`merge_bin`) vs 5.x (`merge-bin`).

## Flash

1. Slide the **power switch to ON** (the board is dead on USB power with the switch OFF, even with
   no battery attached).
2. Plug the board in over the USB-C port labelled **USB** (native USB, next to the one labelled
   UART). On a Mac it shows up as `/dev/cu.usbmodem*` (e.g. `/dev/cu.usbmodem101`;
   `ls /dev/cu.usbmodem*` before and after plugging in). On the Jetson it is `/dev/ttyACM0` / `/dev/ttyACM1`.

```sh
scripts/flash_eyes.sh /dev/cu.usbmodem101 L    # left eye  (green iris)
scripts/flash_eyes.sh /dev/cu.usbmodem101 R    # right eye (brown iris)
```

The script writes the merged image at 921600 baud (`--before default-reset --after hard-reset`),
waits 3 s for the board to reboot and re-enumerate, then sends `{"cmd":"side","value":"L"}` and
`{"cmd":"ping"}` over pyserial and prints the replies. Expect:

```
side -> {"ok":1,"side":"L"}
ping -> {"ok":1,"side":"L","fps":40,"fw":"0.3.0"}
```

The side is stored in NVS (`Preferences` namespace `eye`, key `side`) and survives reflashing unless
the flash is erased (`esptool --chip esp32s3 erase-flash`). A board with no stored side boots as `L`.

### If the USB port does not enumerate: flash through the UART port

The second USB-C port, labelled **UART**, is a CH343P USB-serial bridge with an auto-download
circuit, so it can always flash the chip even when nothing is running on the native USB port
(blank flash, crash loop, wrong USB mode). On a Mac it appears as `/dev/cu.wchusbserial*` or
`/dev/cu.usbserial*` (install Waveshare's CH343 driver if nothing shows up); on Linux `/dev/ttyACM*`
or `/dev/ttyUSB*`.

```sh
~/.platformio/penv/bin/esptool --chip esp32s3 --port /dev/cu.wchusbserial1140 --baud 921600 \
  --before default-reset --after hard-reset write-flash 0x0 firmware/eye/dist/eye-merged.bin
```

(esptool 4.x spells it `write_flash`.) The firmware's serial console is the **USB** port only
(`ARDUINO_USB_CDC_ON_BOOT=1`), so after flashing over UART move the cable to the USB port to set
the side and ping; `scripts/flash_eyes.sh` pointed at the UART port will flash fine but its side/ping
step will get no reply and the script exits 1 (the flash itself still succeeded).

### BOOT-button recovery

If esptool says "Failed to connect" or "wrong boot mode", the running firmware is holding the USB
port (or a crash loop is). Hold **BOOT**, press and release **RESET**, then release BOOT: the chip is
now in download mode. Run the flash script again (the port name can change after this,
`ls /dev/cu.usbmodem*`). If the board shows up but nothing answers pings, do the same and reflash.
Waveshare's other tip: press RESET for more than a second and wait for the PC to re-enumerate.

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
| `{"cmd":"ping"}` | `{"ok":1,"side":"L","fps":40,"fw":"0.3.0"}` |
| `{"cmd":"side","value":"L"}` | `{"ok":1,"side":"L"}` (stored in NVS) |
| `{"cmd":"bl","value":800}` | `{"ok":1,"bl":800}` (backlight 0..1023) |
| anything else | `{"err":"unknown"}`; unparsable line: `{"err":"json"}` |

Expressions: `neutral curious happy love sleepy surprised sad angry_playful` (unknown names render as
neutral). `gx`/`gy` are -1..1 (64 px of travel), `p` is pupil dilation 0.3..2, `blink:true` is an
edge (one blink when it goes false -> true). The firmware smooths every parameter (`cur += (target -
cur) * 0.25` per frame), auto-blinks every `blinkRateMs` +/-30 % (120 ms close, 100 ms open) and adds
+/-7 px saccades every 1-3 s. If the link goes quiet the last state is kept.

Quick manual test: `uv run python -m serial.tools.miniterm /dev/cu.usbmodem101 115200`, then type a
line and press Enter.

## How the display path works

`Arduino_RGB_Display` owns the DMA framebuffer (PSRAM) that the ESP32-S3 RGB peripheral scans out
continuously at 16 MHz. The eye is drawn into a second PSRAM buffer (`PsramCanvas`, 460 800 bytes) and
`canvas->flush()` copies it into the DMA buffer once per frame, so the panel never shows a half-drawn
eye. The ST7701 only needs its command interface during init: `ExpanderCsSWSPI` in `main.cpp` is
Arduino_GFX's bit-banged 9-bit `Arduino_SWSPI` on GPIO2/GPIO1 with the chip-select routed through the
TCA9554 (Arduino_GFX's own `Arduino_XCA9554SWSPI` assumes all four lines are on the expander, which
is not how this board is wired). The init table is Waveshare's vendor sequence, byte for byte.

## Blank panel, wrong colours, or a rolling image

1. **Black, no backlight**: `{"cmd":"bl","value":1023}`. If still dark, the board printed
   `{"err":"display"}` at boot (check with miniterm): `canvas->begin()` failed, which means the RGB
   peripheral could not allocate its PSRAM framebuffer; make sure the build is `qio_opi`.
2. **Backlight on, panel white/garbage**: the init sequence did not reach the ST7701. Check the
   TCA9554 answers at `0x20` (I2C scan on SDA 15 / SCL 7) and that `EXP_LCD_CS`/`EXP_LCD_RST` bit
   numbers match (EXIO3 -> bit 2, EXIO1 -> bit 0). Try `Wire.begin(..., 100000)`.
3. **Image but colours wrong / tinted**: try `0x3A, 0x60` instead of `0x66` in the init table, or
   swap the constructor to `useBigEndian = true`. Inverted colours: change `0x20` (INVOFF) to `0x21`.
4. **Image tears or rolls**: lower `RGB_PCLK_HZ` to 12000000, or flip `hsync_polarity`/`vsync_polarity`
   (1 -> 0) in the `Arduino_ESP32RGBPanel` constructor. Arduino_GFX's `st7701_type5_init_operations`
   (its generic 2.1" round table) is a drop-in alternative to `st7701_waveshare_21_init`.

Rebuild, reflash. Nothing else in the firmware depends on the init table or timings.

## Unverified assumptions (need a board to check)

1. **Chip select through the expander.** `ExpanderCsSWSPI` pulls EXIO3 low around each batch of
   init commands (one I2C write each way) and holds SD_CS (EXIO4) high so the TF card stays off the
   shared GPIO1/GPIO2 lines. The ST7701 should not care that CS stays low for the whole table.
2. **Sync polarities.** ESP32_Display_Panel leaves `hsync_idle_low`/`vsync_idle_low` at 0 for this
   board; Arduino_GFX expresses the same as `hsync_polarity = vsync_polarity = 1`, which is what every
   other ST7701 board in its examples uses. Not measured here.
3. **Software reset before the vendor table.** Because the hardware reset line is on the expander,
   `Arduino_RGB_Display` (with `rst = GFX_NOT_DEFINED`) sends `0x01` + 120 ms before the table, on
   top of the 10 ms low / 100 ms high pulse on EXIO1. Harmless in theory, untested.
4. **Backlight on GPIO6 with LEDC**, 20 kHz, 10-bit, default 800/1023. Wiki and board file agree.

Also worth checking once on hardware: the measured `fps` in the ping reply (target 30+; the per-frame
cost is the 460 KB PSRAM-to-PSRAM copy in `flush()` plus the scanline lid pass), and that the canvas
really landed in PSRAM (`PsramCanvas` asks for `MALLOC_CAP_SPIRAM`; if PSRAM is missing the RGB panel
itself fails first, in which case the board prints `{"err":"display"}` but still answers pings).

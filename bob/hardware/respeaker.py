"""ReSpeaker XVF3800 USB 4-Mic Array: direction-of-arrival reader and LED control.

The array exposes XMOS device-control commands over USB vendor control transfers
(``bRequest=0``, ``wValue=command id`` with 0x80 set for reads, ``wIndex=resource id``).
Read responses start with a status byte (0 = success, 64 = retry) followed by the payload.
``usb.core`` is imported lazily so this module is importable without pyusb or hardware.

Command ids below match Seeed's ``python_control/xvf_host.py``
(github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY). If a firmware update renumbers
them, confirm with ``xvf_host --list-commands`` on the device.
"""

from __future__ import annotations

import struct
from typing import Any

USB_VID = 0x2886
USB_PID = 0x001A

# bmRequestType: CTRL_IN|CTRL_TYPE_VENDOR|CTRL_RECIPIENT_DEVICE and the OUT equivalent.
CTRL_IN_VENDOR = 0xC0
CTRL_OUT_VENDOR = 0x40
READ_BIT = 0x80  # OR'd into the command id (wValue) for reads

# Resource id shared by the LED and DoA commands. VERIFY with xvf_host --list-commands.
RES_LED_DOA = 20
CMD_LED_EFFECT = 12  # uint8: see LED_EFFECT_* below
CMD_LED_BRIGHTNESS = 13  # uint8 0..255 (breath and rainbow modes)
CMD_LED_COLOR = 16  # uint32 0xRRGGBB (breath and single-colour modes)
CMD_LED_DOA_COLOR = 17  # uint32 x2
CMD_DOA_VALUE = 18  # uint16 x2: angle 0..359, speech flag
CMD_LED_RING_COLOR = 19  # uint32 x12

LED_EFFECT_OFF = 0
LED_EFFECT_BREATH = 1
LED_EFFECT_RAINBOW = 2
LED_EFFECT_SINGLE = 3
LED_EFFECT_DOA = 4
LED_EFFECT_MAX = LED_EFFECT_DOA

STATUS_OK = 0
STATUS_RETRY = 64
DOA_RESPONSE_LENGTH = 5  # status byte + two uint16
DOA_MAX_ANGLE = 359
USB_TIMEOUT_MS = 100000  # as in Seeed's example; control transfers normally complete in <10 ms
READ_RETRIES = 3


def find():
    """Return the first XVF3800 array as a pyusb device, or None. Imports pyusb lazily."""
    import usb.core

    return usb.core.find(idVendor=USB_VID, idProduct=USB_PID)


def parse_doa(resp) -> tuple[int, bool]:
    """Parse a DOA_VALUE response into ``(angle_degrees, speech_detected)``.

    Handles both known layouts: ``<BHH`` (status, angle, flag) per the firmware changelog,
    and the byte layout used by Seeed's example (``resp[1]`` angle, ``resp[3]`` flag).
    """
    data = bytes(resp)
    if len(data) < 4:
        raise ValueError(f"DoA response too short: {data!r}")
    if len(data) >= DOA_RESPONSE_LENGTH:
        _status, angle, flag = struct.unpack("<BHH", data[:DOA_RESPONSE_LENGTH])
        if angle <= DOA_MAX_ANGLE:
            return angle, bool(flag)
    return data[1], bool(data[3])


def read_control(dev, cmd_id: int, res_id: int, length: int, timeout_ms: int = USB_TIMEOUT_MS) -> bytes:
    """Read ``length`` bytes (status byte included) for a device-control command."""
    return bytes(dev.ctrl_transfer(CTRL_IN_VENDOR, 0, READ_BIT | cmd_id, res_id, length, timeout_ms))


def write_control(dev, cmd_id: int, res_id: int, payload: bytes, timeout_ms: int = USB_TIMEOUT_MS) -> int:
    """Write ``payload`` for a device-control command; returns the number of bytes sent."""
    return dev.ctrl_transfer(CTRL_OUT_VENDOR, 0, cmd_id, res_id, bytes(payload), timeout_ms)


def _check_range(name: str, value: int, maximum: int) -> int:
    value = int(value)
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be 0..{maximum}, got {value}")
    return value


def set_led_effect(dev, effect: int) -> int:
    """LED_EFFECT: 0 off, 1 breath, 2 rainbow, 3 single colour, 4 follow DoA."""
    return write_control(
        dev, CMD_LED_EFFECT, RES_LED_DOA, bytes([_check_range("effect", effect, LED_EFFECT_MAX)])
    )


def set_led_color(dev, rgb: int) -> int:
    """LED_COLOR: 0xRRGGBB used by the breath and single-colour effects."""
    return write_control(
        dev, CMD_LED_COLOR, RES_LED_DOA, struct.pack("<I", _check_range("rgb", rgb, 0xFFFFFFFF))
    )


def set_led_brightness(dev, value: int) -> int:
    """LED_BRIGHTNESS: 0..255 for the breath and rainbow effects."""
    return write_control(
        dev, CMD_LED_BRIGHTNESS, RES_LED_DOA, bytes([_check_range("brightness", value, 255)])
    )


class DoAReader:
    """Polls the array's direction of arrival. ``read()`` never raises: on any USB error it
    keeps the last good value and records the reason in ``error``."""

    def __init__(self, dev: Any = None, timeout_ms: int = USB_TIMEOUT_MS):
        self.dev = dev
        self.timeout_ms = timeout_ms
        self.angle = 0
        self.speech = False
        self.error: str | None = None

    def find(self) -> bool:
        """Locate the array if no device was given. Returns True when a device is available."""
        if self.dev is None:
            try:
                self.dev = find()
            except Exception as exc:  # pyusb missing, libusb missing, permission problems
                self.error = f"usb lookup failed: {exc}"
                return False
        if self.dev is None:
            self.error = "ReSpeaker XVF3800 not found (usb 2886:001a)"
        return self.dev is not None

    def read(self) -> tuple[int, bool]:
        if not self.find():
            return self.angle, self.speech
        try:
            for _ in range(READ_RETRIES):
                resp = read_control(
                    self.dev, CMD_DOA_VALUE, RES_LED_DOA, DOA_RESPONSE_LENGTH, self.timeout_ms
                )
                if not resp or resp[0] != STATUS_RETRY:
                    break
            self.angle, self.speech = parse_doa(resp)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        else:
            self.error = None
        return self.angle, self.speech


class FakeDoA:
    """Test double with the same ``read()`` shape; set ``angle``/``speech`` directly."""

    def __init__(self, angle: int = 0, speech: bool = False):
        self.angle = angle
        self.speech = speech
        self.error: str | None = None

    def read(self) -> tuple[int, bool]:
        return self.angle, self.speech

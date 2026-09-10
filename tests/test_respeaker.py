import struct

import pytest

from bob.hardware import respeaker
from bob.hardware.respeaker import (
    CMD_DOA_VALUE,
    CMD_LED_BRIGHTNESS,
    CMD_LED_COLOR,
    CMD_LED_EFFECT,
    CTRL_IN_VENDOR,
    CTRL_OUT_VENDOR,
    DOA_RESPONSE_LENGTH,
    READ_BIT,
    RES_LED_DOA,
    STATUS_OK,
    STATUS_RETRY,
    DoAReader,
    FakeDoA,
    parse_doa,
    set_led_brightness,
    set_led_color,
    set_led_effect,
    write_control,
)


class FakeDevice:
    """Stands in for a pyusb device: records control transfers and replays scripted responses."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def ctrl_transfer(self, bm_request_type, b_request, w_value, w_index, data_or_length, timeout=None):
        self.calls.append((bm_request_type, b_request, w_value, w_index, data_or_length, timeout))
        if bm_request_type & 0x80 == 0:
            return len(data_or_length)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def doa_bytes(angle, speech, status=STATUS_OK):
    return struct.pack("<BHH", status, angle, int(speech))


def test_parse_doa_uint16_layout():
    assert parse_doa(doa_bytes(300, True)) == (300, True)
    assert parse_doa(doa_bytes(0, False)) == (0, False)
    assert parse_doa(doa_bytes(359, 1)) == (359, True)


def test_parse_doa_byte_layout_when_uint16_angle_is_out_of_range():
    # High byte of the "angle" is garbage -> fall back to resp[1] angle / resp[3] speech.
    assert parse_doa(bytes([0, 200, 0xFF, 1, 0])) == (200, True)
    assert parse_doa(bytes([0, 45, 0xFF, 0, 0])) == (45, False)
    # Short (4-byte) responses only have the byte layout.
    assert parse_doa(bytes([0, 90, 0, 1])) == (90, True)


def test_parse_doa_accepts_array_like_and_rejects_short_input():
    assert parse_doa(bytearray(doa_bytes(180, False))) == (180, False)
    assert parse_doa(list(doa_bytes(10, True))) == (10, True)
    with pytest.raises(ValueError):
        parse_doa(b"\x00\x01")


def test_reader_reads_via_vendor_control_transfer():
    dev = FakeDevice([doa_bytes(123, True)])
    reader = DoAReader(dev)
    assert reader.read() == (123, True)
    assert reader.error is None
    assert (reader.angle, reader.speech) == (123, True)
    assert dev.calls == [
        (CTRL_IN_VENDOR, 0, READ_BIT | CMD_DOA_VALUE, RES_LED_DOA, DOA_RESPONSE_LENGTH, reader.timeout_ms)
    ]
    assert CTRL_IN_VENDOR == 0xC0 and (READ_BIT | CMD_DOA_VALUE) == 0x92 and RES_LED_DOA == 20


def test_reader_retries_when_device_asks_for_retry():
    dev = FakeDevice([doa_bytes(0, False, status=STATUS_RETRY), doa_bytes(77, False)])
    assert DoAReader(dev).read() == (77, False)
    assert len(dev.calls) == 2


def test_reader_keeps_last_value_and_sets_error_on_usb_failure():
    dev = FakeDevice([doa_bytes(250, True), OSError("pipe error"), b"\x00", doa_bytes(10, False)])
    reader = DoAReader(dev)
    assert reader.read() == (250, True)
    assert reader.read() == (250, True)
    assert "pipe error" in reader.error
    assert reader.read() == (250, True)  # too-short response is also non-fatal
    assert reader.error
    assert reader.read() == (10, False)
    assert reader.error is None


def test_reader_without_device_reports_not_found(monkeypatch):
    monkeypatch.setattr(respeaker, "find", lambda: None)
    reader = DoAReader()
    assert reader.read() == (0, False)
    assert "not found" in reader.error
    assert reader.dev is None


def test_reader_finds_device_lazily(monkeypatch):
    dev = FakeDevice([doa_bytes(30, True)])
    monkeypatch.setattr(respeaker, "find", lambda: dev)
    reader = DoAReader()
    assert reader.read() == (30, True)
    assert reader.dev is dev


def test_fake_doa_is_settable():
    fake = FakeDoA()
    assert fake.read() == (0, False)
    fake.angle, fake.speech = 270, True
    assert fake.read() == (270, True)
    assert FakeDoA(angle=15, speech=True).read() == (15, True)
    assert FakeDoA().error is None


def test_led_helpers_write_vendor_control_transfers():
    dev = FakeDevice()
    set_led_effect(dev, 4)
    set_led_color(dev, 0x00FF8800)
    set_led_brightness(dev, 128)
    assert [call[:5] for call in dev.calls] == [
        (CTRL_OUT_VENDOR, 0, CMD_LED_EFFECT, RES_LED_DOA, b"\x04"),
        (CTRL_OUT_VENDOR, 0, CMD_LED_COLOR, RES_LED_DOA, struct.pack("<I", 0x00FF8800)),
        (CTRL_OUT_VENDOR, 0, CMD_LED_BRIGHTNESS, RES_LED_DOA, b"\x80"),
    ]
    assert CTRL_OUT_VENDOR == 0x40


def test_led_helpers_validate_ranges():
    dev = FakeDevice()
    with pytest.raises(ValueError):
        set_led_effect(dev, 9)
    with pytest.raises(ValueError):
        set_led_color(dev, 0x1_0000_0000)
    with pytest.raises(ValueError):
        set_led_brightness(dev, 256)
    assert dev.calls == []


def test_write_control_returns_bytes_written():
    dev = FakeDevice()
    assert write_control(dev, 12, 20, b"\x01") == 1

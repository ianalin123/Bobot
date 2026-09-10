import asyncio
import io
import sys
import types
import wave

import numpy as np
import pytest

from bob.audio import (
    BLOCK,
    RATE,
    AudioIO,
    FakeAudioIO,
    RobotClient,
    Vad,
    decode_pcm16k,
    encode_wav,
    find_device,
    rms,
)
from bob.config import Settings
from bob.providers import decode_wav
from bob.robot import Robot, SimHardware
from bob.voice import VoiceSession
from tests.test_voice import LLM, TTS


def tone(seconds, amplitude=0.3, frequency=440.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * RATE), np.float32)


def frames(samples, size=BLOCK):
    return [samples[i : i + size] for i in range(0, len(samples) - len(samples) % size, size)]


def run_vad(vad, samples):
    return [vad.feed(frame) for frame in frames(samples)]


# Vad -------------------------------------------------------------------------------------------


def test_vad_detects_turn_with_pre_roll():
    vad = Vad()
    states = run_vad(vad, np.concatenate([silence(1.0), tone(1.0), silence(1.0)]))
    assert states.count("speech_start") == 1 and states.count("speech_end") == 1
    assert states.index("speech_start") < states.index("speech_end")
    # Start is reported once speech has been sustained for 140 ms (7 blocks), never earlier.
    assert states.index("speech_start") == 50 + 6
    turn = decode_wav(vad.take_turn())
    # 300 ms pre-roll (160 ms of it silence) + 860 ms remaining tone + 650 ms trailing silence.
    assert abs(len(turn) / RATE - 1.81) < 0.05
    assert rms(turn[:2400]) < 0.001 and rms(turn[3200:16000]) > 0.15
    with pytest.raises(RuntimeError):
        vad.take_turn()


def test_vad_caps_at_fifteen_seconds_and_keeps_listening():
    vad = Vad()
    states = run_vad(vad, tone(17.0))
    assert states.count("speech_end") == 1
    ended = states.index("speech_end")
    assert 14.9 <= (ended + 1) * BLOCK / RATE <= 15.2
    assert 14.9 <= len(decode_wav(vad.take_turn())) / RATE <= 15.4
    # After the cap, the still-loud signal starts a fresh utterance.
    assert states.count("speech_start") == 2 and states.index("speech_start", ended) > ended


def test_vad_ignores_short_noise_and_accepts_int16_frames():
    vad = Vad()
    states = run_vad(vad, np.concatenate([silence(0.5), tone(0.1), silence(0.5)]))
    assert set(states) == {"silence"}
    pcm = (np.concatenate([silence(0.2), tone(0.5), silence(0.8)]) * 32767).astype(np.int16)
    assert "speech_end" in run_vad(vad, pcm)


def test_wav_helpers_round_trip_and_resample():
    samples = tone(0.5)
    assert np.allclose(decode_wav(encode_wav(samples)), samples, atol=1e-3)
    # A 2-channel 48 kHz file (0.5 s) is averaged to mono and resampled to 16 kHz.
    stereo = np.repeat((0.3 * np.sin(np.arange(24000) / 48000 * 2 * np.pi * 440) * 32767).astype("<i2"), 2)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(stereo.tobytes())
    decoded = decode_pcm16k(buffer.getvalue())
    assert decoded.dtype == np.int16 and abs(len(decoded) - 0.5 * RATE) <= 1
    with pytest.raises(Exception):
        decode_pcm16k(b"fake-wav")


# Fake sounddevice ------------------------------------------------------------------------------


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def close(self):
        pass


def fake_sounddevice(monkeypatch, devices):
    module = types.ModuleType("sounddevice")
    module.query_devices = lambda: devices
    module.InputStream = FakeStream
    module.OutputStream = FakeStream
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


DEVICES = [
    {"name": "HDA NVidia: HDMI", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "ReSpeaker XVF3800 USB: Audio (hw:1,0)", "max_input_channels": 2, "max_output_channels": 2},
]


def test_find_device_by_substring_and_kind():
    assert find_device(DEVICES, "xvf3800", "input") == 1
    assert find_device(DEVICES, "HDMI", "input") is None
    assert find_device(DEVICES, "HDMI", "output") == 0
    assert find_device(DEVICES, "nothing", "output") is None


def capture(audio, samples):
    """Push float samples through the input callback as 2-channel int16 blocks (channel 1 is noise)."""
    for frame in frames(samples):
        block = np.zeros((len(frame), 2), np.int16)
        block[:, 0] = (frame * 32767).astype(np.int16)
        block[:, 1] = 20000  # Loud garbage on the second channel must be ignored.
        audio._input_callback(block, len(frame), None, None)


async def settle(times=5):
    for _ in range(times):
        await asyncio.sleep(0)


async def test_audio_io_captures_channel_zero_and_selects_device(monkeypatch):
    fake_sounddevice(monkeypatch, DEVICES)
    turns, barges = [], []

    async def on_turn(wav):
        turns.append(wav)

    async def on_barge_in():
        barges.append(True)

    audio = AudioIO(Settings(), on_turn, on_barge_in)
    audio.start()
    try:
        assert audio.device_info == {"input": 1, "output": 1, "capture_channels": 2}
        assert audio.input.kwargs["channels"] == 2 and audio.input.kwargs["dtype"] == "int16"
        assert audio.output.kwargs["channels"] == 1 and audio.input.kwargs["blocksize"] == BLOCK
        capture(audio, np.concatenate([silence(0.5), tone(0.8), silence(0.8)]))
        await settle()
        assert len(turns) == 1 and not barges
        assert abs(len(decode_wav(turns[0])) / RATE - (0.3 + 0.66 + 0.65)) < 0.05
    finally:
        audio.close()


async def test_audio_io_falls_back_to_default_device_and_respects_override(monkeypatch):
    fake_sounddevice(monkeypatch, DEVICES)
    audio = AudioIO(Settings(audio_device="Nonexistent"))
    audio.start()
    assert audio.device_info == {"input": None, "output": None, "capture_channels": 1}
    assert audio.input.kwargs["device"] is None and audio.input.kwargs["channels"] == 1
    audio.close()
    audio = AudioIO(Settings(audio_device="hdmi"))
    audio.start()
    assert audio.device_info["output"] == 0 and audio.device_info["input"] is None
    audio.close()


def play_block(audio):
    out = np.zeros((BLOCK, 1), np.int16)
    audio._output_callback(out, BLOCK, None, None)
    return out[:, 0]


async def test_audio_io_playback_acks_and_cancel_within_one_block(monkeypatch):
    fake_sounddevice(monkeypatch, DEVICES)
    acks = []
    audio = AudioIO(Settings(), on_playback=lambda sid, started: acks.append((sid, started)))
    audio.start()
    try:
        audio.play(0, encode_wav(tone(0.05)))  # 800 samples = 2.5 blocks
        audio.play(1, encode_wav(tone(0.05)))
        await settle()
        assert audio.speaking and acks == [(0, True)]
        assert np.abs(play_block(audio)).max() > 1000
        play_block(audio)
        assert play_block(audio)[200:].tolist() == [0] * 120  # tail of the segment is padded
        await settle()
        assert acks == [(0, True), (0, False), (1, True)]
        assert np.abs(play_block(audio)).max() > 1000
        audio.cancel_playback()
        assert not audio.speaking
        assert play_block(audio).tolist() == [0] * BLOCK
        await settle()
        assert acks == [(0, True), (0, False), (1, True)]  # No finished ack for the cancelled one.
        audio.play(2, encode_wav(tone(0.02)))
        await settle()
        play_block(audio)
        await settle()
        assert acks[-2:] == [(2, True), (2, False)] and not audio.speaking
        audio.play(3, b"fake-wav")  # Undecodable audio is dropped, not fatal.
        await settle()
        assert acks[-1] == (2, False)
    finally:
        audio.close()


async def test_audio_io_barge_in_only_while_speaking(monkeypatch):
    fake_sounddevice(monkeypatch, DEVICES)
    barges, turns = [], []

    async def on_barge_in():
        barges.append(audio.speaking)

    async def on_turn(wav):
        turns.append(wav)

    audio = AudioIO(Settings(), on_turn, on_barge_in)
    audio.start()
    try:
        audio.play(0, encode_wav(tone(2.0)))
        await settle()
        assert audio.speaking
        capture(audio, tone(0.12))
        await settle()
        assert not barges  # 120 ms is not sustained enough.
        capture(audio, tone(0.04))
        await settle()
        assert barges == [True]
        capture(audio, np.concatenate([tone(0.3), silence(0.7)]))
        await settle()
        assert len(barges) == 1 and len(turns) == 1  # The interrupting speech becomes the next turn.
        audio.cancel_playback()
        capture(audio, np.concatenate([tone(0.5), silence(0.7)]))
        await settle()
        assert len(barges) == 1 and len(turns) == 2
    finally:
        audio.close()


# FakeAudioIO -----------------------------------------------------------------------------------


async def test_fake_audio_io_records_play_and_cancel_order():
    acks = []
    fake = FakeAudioIO(on_playback=lambda sid, started: acks.append((sid, started)))
    fake.start()
    fake.play(0, b"a")
    fake.play(1, b"b")
    assert fake.speaking and fake.pending == [0, 1]
    await fake.drain()
    assert acks == [(0, True), (0, False), (1, True), (1, False)] and not fake.speaking
    fake.play(2, b"c")
    fake.cancel_playback()
    assert fake.log[-2:] == [("play", 2), ("cancel",)] and fake.cancels == 1 and not fake.pending
    fake.play(3, b"d")
    task = asyncio.create_task(fake.drain())
    await asyncio.sleep(0)
    fake.cancel_playback()
    await task
    assert acks[-1] == (3, True)  # Cancelled mid-segment: started but never finished.


async def test_fake_audio_io_feed_wav_barges_in_when_speaking():
    calls = []

    async def on_turn(wav):
        calls.append(("turn", wav))

    async def on_barge_in():
        calls.append(("barge_in",))

    fake = FakeAudioIO(on_turn, on_barge_in)
    await fake.feed_wav(b"one")
    fake.play(0, b"reply")
    await fake.feed_wav(b"two")
    assert calls == [("turn", b"one"), ("barge_in",), ("turn", b"two")]


# RobotClient -----------------------------------------------------------------------------------


class STT:
    def __init__(self, text="Hello"):
        self.text = text

    def transcribe(self, data):
        return self.text


def client(stt=None):
    fake = FakeAudioIO()
    factory = lambda send: VoiceSession(send, stt or STT(), LLM(), TTS(), Robot(SimHardware(0.001)))  # noqa: E731
    return RobotClient(factory, fake), fake


async def test_robot_client_routes_audio_and_acks_commit_history():
    robot, fake = client()
    seen = []
    robot.on_event = lambda event: seen.append(event["type"])
    await fake.feed_wav(b"turn-1")
    await robot.session.task
    assert robot.request_id == 1
    assert fake.played == [(0, b"fake-wav"), (1, b"fake-wav")]
    assert not robot.session.history[-1:] or robot.session.history[-1]["role"] == "user"
    await fake.drain()
    assert robot.session.history[-1] == {"role": "assistant", "content": "Bello! A banana for you."}
    assert not robot.session.segments and not robot.session.heard
    types_ = [e["type"] for e in robot.events]
    assert types_[:2] == ["interrupted", "turn_started"]
    assert types_.count("playback_started") == 2 and types_.count("playback_finished") == 2
    assert "transcript" in types_ and "wav" not in str(robot.events)
    assert seen == types_


async def test_robot_client_barge_in_cancels_and_interrupts():
    robot, fake = client()
    await fake.feed_wav(b"turn-1")
    await robot.session.task
    # Only the first sentence was heard when the person starts talking again.
    fake.on_playback(0, True)
    fake.on_playback(0, False)
    fake.on_playback(1, True)
    await fake.feed_wav(b"turn-2")
    await robot.session.task
    assert fake.cancels >= 2 and fake.log.index(("cancel",), 1) > fake.log.index(("play", 1))
    assert robot.request_id == 3  # barge-in bumped to 2, the new turn to 3
    assert [e for e in robot.events if e["type"] == "barge_in"][0]["request_id"] == 2
    assert robot.session.history[1] == {
        "role": "assistant",
        "content": "Bello! [The next sentence was interrupted.]",
    }
    assert robot.session.history[2]["role"] == "user"
    assert fake.played[-2:] == [(0, b"fake-wav"), (1, b"fake-wav")]
    assert robot.session.request_id == 3


async def test_robot_client_ignores_stale_events_and_bounds_history():
    robot, fake = client()
    await robot.session.send({"type": "audio", "request_id": 99, "segment_id": 0, "wav": ""})
    assert not fake.played and not robot.events
    for i in range(80):
        await robot.session.send({"type": "metric", "name": "x", "value": i})
    assert len(robot.events) == 50 and robot.events[-1]["value"] == 79
    robot.session.acknowledge(7, 0, True)  # Old request id: ignored by the session.
    await robot.text_turn("Hello")
    await robot.session.task
    assert robot.request_id == 1 and len(fake.played) == 2
    await robot.session.interrupt(robot.request_id)

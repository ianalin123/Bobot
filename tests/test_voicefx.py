"""Minion voice effects: pitch up and speed up, shared by the cloud and local TTS paths."""

import builtins

import numpy as np
import pytest

from bob import voicefx
from bob.providers import LocalTTS, decode_wav
from bob.providers_cloud import wav_from_pcm16


def tone(seconds=1.0, rate=16000, hz=300.0):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * hz * t) * 0.5 * 32767).astype(np.int16)


def peak_hz(samples, rate):
    spectrum = np.abs(np.fft.rfft(np.asarray(samples, dtype=np.float32)))
    return np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum)]


def test_defaults_sound_like_a_minion_not_a_chipmunk():
    assert 4.0 <= voicefx.DEFAULT_PITCH_SEMITONES <= 8.0
    assert 1.05 <= voicefx.DEFAULT_SPEED <= 1.35


def test_neutral_fx_is_identity_and_keeps_int16():
    pcm = tone(0.25)
    out = voicefx.voice_fx(pcm, 16000, semitones=0.0, speed=1.0)
    assert out.dtype == np.int16 and np.array_equal(out, pcm)


def test_speed_shortens_duration_but_keeps_pitch():
    pytest.importorskip("librosa")
    pcm = tone(1.0)
    out = voicefx.voice_fx(pcm, 16000, semitones=0.0, speed=1.5)
    assert abs(len(out) - len(pcm) / 1.5) <= len(pcm) * 0.05
    assert 280 < peak_hz(out, 16000) < 320


def test_pitch_raises_frequency_and_keeps_duration():
    pytest.importorskip("librosa")
    pcm = tone(1.0)
    out = voicefx.voice_fx(pcm, 16000, semitones=12.0, speed=1.0)
    assert abs(len(out) - len(pcm)) <= len(pcm) * 0.02
    assert 570 < peak_hz(out, 16000) < 630


def test_numpy_fallback_folds_speed_into_a_single_resample(monkeypatch):
    real_import = builtins.__import__

    def no_librosa(name, *args, **kwargs):
        if name.startswith("librosa"):
            raise ImportError("librosa disabled for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_librosa)
    pcm = tone(1.0)
    out = voicefx.voice_fx(pcm, 16000, semitones=12.0, speed=1.5)
    # The film trick: one resample raises pitch and shortens the clip together.
    assert out.dtype == np.int16
    assert abs(len(out) - len(pcm) / 2) <= 4
    assert 570 < peak_hz(out, 16000) < 630


def test_local_tts_postprocess_resamples_to_16k_and_applies_fx(monkeypatch):
    pytest.importorskip("librosa")
    monkeypatch.setenv("BOB_PITCH_SEMITONES", "0")
    monkeypatch.setenv("BOB_SPEED", "2")
    tts = LocalTTS()
    assert tts.pitch_semitones == 0.0 and tts.speed == 2.0
    out = tts.postprocess(wav_from_pcm16(tone(1.0, rate=22050), 22050))
    samples = decode_wav(out)  # asserts 16 kHz mono 16-bit
    assert abs(len(samples) - 8000) <= 400
    assert 280 < peak_hz(samples, 16000) < 320


def test_local_tts_defaults_come_from_voicefx(monkeypatch):
    monkeypatch.delenv("BOB_PITCH_SEMITONES", raising=False)
    monkeypatch.delenv("BOB_SPEED", raising=False)
    tts = LocalTTS()
    assert tts.pitch_semitones == voicefx.DEFAULT_PITCH_SEMITONES
    assert tts.speed == voicefx.DEFAULT_SPEED

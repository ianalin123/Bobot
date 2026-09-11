"""Minion voice effects shared by every TTS path: pitch up and speed up.

The film Minion voice is an ordinary recording pitched up and played faster. ``voice_fx`` does
both. With ``librosa`` importable the two are independent (``semitones`` keeps duration,
``speed`` keeps pitch); without it, a single resample raises pitch and shortens the clip at
once, so the effect is "at least this fast and at least this high". numpy only, no SDKs.
"""

import io
import wave

import numpy as np

DEFAULT_PITCH_SEMITONES = 6.0
DEFAULT_SPEED = 1.15
TARGET_SR = 16000
PEAK = 0.97  # normalize every clip to this peak: as loud as possible without clipping


def to_int16(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples)
    if samples.dtype == np.int16:
        return samples
    if samples.dtype.kind == "f":
        return np.clip(np.asarray(samples, dtype=np.float64) * 32768.0, -32768, 32767).astype(np.int16)
    return samples.astype(np.int16)


def to_float(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples)
    if samples.dtype.kind == "f":
        return samples.astype(np.float32)
    return samples.astype(np.float32) / 32768.0


def _interp(samples: np.ndarray, ratio: float) -> np.ndarray:
    """Plain resample by ``ratio`` (>1 shortens the clip and raises pitch)."""
    count = max(1, int(round(len(samples) / ratio)))
    positions = np.arange(count) * ratio
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def resample(samples: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Resample float samples; librosa when available, linear interpolation otherwise."""
    samples = to_float(samples)
    if sr_in == sr_out or len(samples) == 0:
        return samples
    try:
        import librosa

        return librosa.resample(samples, orig_sr=sr_in, target_sr=sr_out).astype(np.float32)
    except ImportError:
        return _interp(samples, sr_in / sr_out)


def voice_fx(samples: np.ndarray, sr: int, semitones: float, speed: float) -> np.ndarray:
    """Pitch by ``semitones`` and speed up by ``speed`` (1.0 = unchanged). int16 in, int16 out."""
    samples = np.asarray(samples)
    if (not semitones and speed == 1.0) or len(samples) == 0:
        return samples
    as_int = samples.dtype.kind != "f"
    floats = to_float(samples)
    try:
        import librosa

        if speed != 1.0:
            floats = librosa.effects.time_stretch(floats, rate=float(speed))
        if semitones:
            floats = librosa.effects.pitch_shift(floats, sr=sr, n_steps=float(semitones))
        out = floats.astype(np.float32)
    except ImportError:
        out = _interp(floats, max(2 ** (semitones / 12), float(speed)))
    return to_int16(out) if as_int else out


def normalize(samples: np.ndarray, peak: float = PEAK) -> np.ndarray:
    """Scale so the loudest sample sits at ``peak`` of full scale. Silence stays silence."""
    samples = np.asarray(samples)
    if len(samples) == 0:
        return samples
    as_int = samples.dtype.kind != "f"
    floats = to_float(samples)
    current = float(np.abs(floats).max())
    if current <= 0.0:
        return samples
    out = (floats * (peak / current)).astype(np.float32)
    return to_int16(out) if as_int else out


def normalize_wav(data: bytes) -> bytes:
    """Any 16-bit PCM WAV -> the same rate, mono, peak-normalized."""
    samples, rate = read_wav(data)
    return wav_from_pcm16(normalize(samples), rate)


def pitch_shift_wav(pcm16: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Pitch only (kept for the phrase renderer and older callers)."""
    return voice_fx(pcm16, sr, semitones, 1.0)


def wav_from_pcm16(samples: np.ndarray, sr: int) -> bytes:
    """Wrap int16 (or float -1..1) mono samples into a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sr)
        output.writeframes(to_int16(samples).astype("<i2").tobytes())
    return buffer.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Any 16-bit PCM WAV (mono or stereo, any rate) -> (float mono samples, rate)."""
    with wave.open(io.BytesIO(data), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        raw = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError("Expected 16-bit PCM WAV.")
    samples = np.frombuffer(raw[: len(raw) - len(raw) % (2 * channels)], dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return to_float(samples), rate


def minionize_wav(data: bytes, semitones: float, speed: float) -> bytes:
    """Any WAV -> 16 kHz mono WAV with the Minion effects applied and full-scale loudness."""
    samples, rate = read_wav(data)
    samples = resample(samples, rate, TARGET_SR)
    return wav_from_pcm16(normalize(voice_fx(samples, TARGET_SR, semitones, speed)), TARGET_SR)

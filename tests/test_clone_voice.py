"""scripts/clone_voice.py: make an ElevenLabs instant voice clone from a recording and hear it."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bob.providers import decode_wav

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clone_voice  # noqa: E402


class FakeIVC:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(voice_id="v123", requires_verification=False)


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append(kwargs)
        t = np.arange(8000) / 16000
        return iter([(np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2").tobytes()])


def fake_client():
    return SimpleNamespace(voices=SimpleNamespace(ivc=FakeIVC()), text_to_speech=FakeSpeech())


def test_slug_is_filesystem_safe():
    assert clone_voice.slug("Bob Minion!") == "bob-minion"


def test_clone_creates_voice_renders_a_sample_and_prints_env_lines(tmp_path, capsys):
    audio = tmp_path / "minion.mp3"
    audio.write_bytes(b"ID3fake")
    client = fake_client()
    code = clone_voice.main(
        ["--name", "Bob Minion", "--out", str(tmp_path / "samples"), str(audio)], client=client
    )
    assert code == 0
    call = client.voices.ivc.calls[0]
    assert call["name"] == "Bob Minion" and call["remove_background_noise"] is True
    assert [name for name, *_ in call["files"]] == ["minion.mp3"]
    sample = tmp_path / "samples" / "bob-minion.wav"
    assert len(decode_wav(sample.read_bytes())) > 0
    assert client.text_to_speech.calls[0]["voice_id"] == "v123"
    out = capsys.readouterr().out
    assert "ELEVENLABS_VOICE_ID=v123" in out and "BOB_TTS_PROVIDER=elevenlabs" in out


def test_missing_recording_is_an_error(tmp_path, capsys):
    assert clone_voice.main(["--name", "x", str(tmp_path / "nope.mp3")], client=fake_client()) == 2
    assert "nope.mp3" in capsys.readouterr().err

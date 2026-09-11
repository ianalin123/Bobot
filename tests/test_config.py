import pytest

from bob.config import Settings


def test_defaults_are_sim():
    s = Settings.from_env({})
    assert s.mode == "sim"
    assert s.hardware == "sim"
    assert not s.real_hardware
    assert s.llm_model == "gpt-4.1-mini"
    assert s.eye_ports == ("/dev/ttyACM1", "/dev/ttyACM2")
    assert s.camera_index == -1
    assert s.doa_enabled is True


def test_eye_ports_split_on_comma():
    s = Settings.from_env({"BOB_EYE_PORTS": "/dev/ttyUSB3, /dev/ttyUSB4"})
    assert s.eye_ports == ("/dev/ttyUSB3", "/dev/ttyUSB4")


def test_cloud_requires_openai_key():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings.from_env({"BOB_MODE": "cloud"})
    s = Settings.from_env({"BOB_MODE": "cloud", "OPENAI_API_KEY": "sk-test"})
    assert s.cloud and s.openai_api_key == "sk-test"


def test_real_hardware_flag_and_bools():
    s = Settings.from_env({"BOB_HARDWARE": "real", "BOB_LOCAL_FALLBACK": "yes", "BOB_DOA": "0"})
    assert s.real_hardware
    assert s.local_fallback is True
    assert s.doa_enabled is False


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        Settings.from_env({"BOB_MODE": "banana"})


def test_voice_fx_settings():
    from bob import voicefx

    s = Settings.from_env({})
    assert s.pitch_semitones == voicefx.DEFAULT_PITCH_SEMITONES
    assert s.speed == voicefx.DEFAULT_SPEED
    s = Settings.from_env({"BOB_PITCH_SEMITONES": "2.5", "BOB_SPEED": "1.3"})
    assert s.pitch_semitones == 2.5 and s.speed == 1.3

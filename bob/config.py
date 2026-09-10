"""Runtime settings from environment variables. One source of truth for modes, devices and models."""

import os
from dataclasses import dataclass, field
from typing import Mapping

MODES = ("sim", "cloud", "local")
HARDWARE = ("sim", "real")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ports(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(p.strip() for p in value.split(",") if p.strip())


@dataclass(frozen=True)
class Settings:
    mode: str = "sim"
    hardware: str = "sim"
    openai_api_key: str = ""
    elevenlabs_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "ash"
    pitch_semitones: float = 5.0
    elevenlabs_voice_id: str = ""
    servo_port: str = "/dev/ttyACM0"
    eye_ports: tuple[str, ...] = ("/dev/ttyACM1", "/dev/ttyACM2")
    camera_index: int = -1
    face_threshold: float = 0.363
    console_token: str = ""
    local_fallback: bool = False
    doa_enabled: bool = True
    emotion_enabled: bool = False
    audio_device: str = ""
    poses_path: str = "config/poses.json"
    people_dir: str = "people"
    models_dir: str = "models/vision"
    phrases_dir: str = "assets/phrases"
    extra: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        mode = env.get("BOB_MODE", "sim").strip().lower() or "sim"
        hardware = env.get("BOB_HARDWARE", "sim").strip().lower() or "sim"
        if mode not in MODES:
            raise ValueError(f"BOB_MODE must be one of {MODES}, got {mode!r}")
        if hardware not in HARDWARE:
            raise ValueError(f"BOB_HARDWARE must be one of {HARDWARE}, got {hardware!r}")
        openai_key = env.get("OPENAI_API_KEY", "").strip()
        if mode == "cloud" and not openai_key:
            raise ValueError("BOB_MODE=cloud requires OPENAI_API_KEY")
        return cls(
            mode=mode,
            hardware=hardware,
            openai_api_key=openai_key,
            elevenlabs_api_key=env.get("ELEVENLABS_API_KEY", "").strip(),
            llm_model=env.get("BOB_LLM_MODEL", cls.llm_model),
            stt_model=env.get("BOB_STT_MODEL", cls.stt_model),
            tts_model=env.get("BOB_TTS_MODEL", cls.tts_model),
            tts_voice=env.get("BOB_TTS_VOICE", cls.tts_voice),
            pitch_semitones=float(env.get("BOB_PITCH_SEMITONES", cls.pitch_semitones)),
            elevenlabs_voice_id=env.get("ELEVENLABS_VOICE_ID", ""),
            servo_port=env.get("BOB_SERVO_PORT", cls.servo_port),
            eye_ports=_ports(env.get("BOB_EYE_PORTS"), cls.eye_ports),
            camera_index=int(env.get("BOB_CAMERA_INDEX", cls.camera_index)),
            face_threshold=float(env.get("BOB_FACE_THRESHOLD", cls.face_threshold)),
            console_token=env.get("BOB_CONSOLE_TOKEN", ""),
            local_fallback=_bool(env.get("BOB_LOCAL_FALLBACK"), False),
            doa_enabled=_bool(env.get("BOB_DOA"), True),
            emotion_enabled=_bool(env.get("BOB_EMOTION"), False),
            audio_device=env.get("BOB_AUDIO_DEVICE", ""),
            poses_path=env.get("BOB_POSES", cls.poses_path),
            people_dir=env.get("BOB_PEOPLE_DIR", cls.people_dir),
            models_dir=env.get("BOB_MODELS_DIR", cls.models_dir),
            phrases_dir=env.get("BOB_PHRASES_DIR", cls.phrases_dir),
        )

    @property
    def real_hardware(self) -> bool:
        return self.hardware == "real"

    @property
    def cloud(self) -> bool:
        return self.mode == "cloud"

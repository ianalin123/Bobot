"""Preflight for the event: one green/red table of every device and API Bob needs.

    uv run python scripts/doctor.py                      # BOB_* from the environment
    set -a; . ./.env; set +a; uv run python scripts/doctor.py

Read-only: it never energizes a servo (the bus scan only pings). Exit code 1 when a P0 check
fails: OpenAI (cloud mode only), the audio device, the eyes, the camera and the vision models.
Everything else (ElevenLabs, DoA, servos, phrases, poses, embeddings) is P1 and only reported.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bob.config import Settings  # noqa: E402

Result = tuple[bool | None, str]  # (ok; None = skipped, detail)
P0, P1 = "P0", "P1"
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
HTTP_TIMEOUT = 10.0


@dataclass(frozen=True)
class Check:
    name: str
    priority: str
    run: Callable[[], Result]


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO / path


# --- individual checks --------------------------------------------------------------------


def check_openai(settings: Settings) -> Result:
    if not settings.cloud:
        return None, f"skipped (BOB_MODE={settings.mode})"
    import httpx

    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:80]}"
    models = response.json().get("data", [])
    ids = {m.get("id") for m in models}
    wanted = [m for m in (settings.llm_model, settings.stt_model, settings.tts_model) if m not in ids]
    detail = f"{len(models)} models"
    if wanted:
        detail += f"; not listed: {', '.join(wanted)}"
    return True, detail


def check_elevenlabs(settings: Settings) -> Result:
    if not settings.elevenlabs_api_key:
        return None, "skipped (no ELEVENLABS_API_KEY)"
    import httpx

    response = httpx.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:80]}"
    info = response.json()
    used, limit = info.get("character_count", 0), info.get("character_limit", 0)
    return used < limit, f"{used:,}/{limit:,} characters used ({info.get('tier', '?')})"


def check_audio(settings: Settings) -> Result:
    import sounddevice as sd

    from bob.audio import DEFAULT_DEVICE, find_device

    devices = [dict(d) for d in sd.query_devices()]
    name = settings.audio_device or DEFAULT_DEVICE
    inp, out = find_device(devices, name, "input"), find_device(devices, name, "output")
    names = ", ".join(d["name"] for d in devices) or "none"
    if inp is None or out is None:
        return False, f"no '{name}' with input+output among {len(devices)} devices: {names[:160]}"
    return True, f"'{devices[inp]['name']}' input #{inp}, output #{out} ({len(devices)} devices)"


def check_xvf3800_usb(settings: Settings) -> Result:
    if not settings.doa_enabled:
        return None, "skipped (BOB_DOA=0)"
    from bob.hardware.respeaker import DoAReader

    reader = DoAReader(timeout_ms=2000)
    if not reader.find():
        return False, reader.error or "not found"
    angle, speech = reader.read()
    if reader.error:
        return False, f"control read failed: {reader.error}"
    return True, f"DoA {angle} deg, speech={speech}"


def check_servos(settings: Settings) -> Result:
    if not Path(settings.servo_port).exists():
        return False, f"{settings.servo_port} does not exist"
    from bob.hardware.feetech import FeetechBus

    bus = FeetechBus(settings.servo_port)
    bus.open()
    try:
        found = bus.scan(range(1, 10))
    finally:
        bus.close()
    if not found:
        return False, f"{settings.servo_port} open, no servo answered ids 1-9"
    ids = ", ".join(str(i) for i in sorted(found))
    missing = sorted(set(range(1, 10)) - set(found))
    detail = f"ids {ids}" + (f"; missing {missing}" if missing else "") + " (read-only)"
    return not missing, detail


def check_eyes(settings: Settings) -> Result:
    from bob.hardware.eyes import Eyes

    eyes = Eyes(settings.eye_ports)
    eyes.open()
    try:
        replies = eyes.ping()
    finally:
        eyes.close()
    good = [port for port, reply in replies.items() if reply.get("ok")]
    detail = "; ".join(
        f"{port}: {'ok side=' + str(r.get('side')) + ' fps=' + str(r.get('fps')) if r.get('ok') else r.get('error')}"
        for port, r in replies.items()
    )
    return len(good) == len(settings.eye_ports), detail


def check_camera(settings: Settings) -> Result:
    from bob.vision.camera import Camera

    camera = Camera(settings.camera_index)
    camera.open()
    try:
        frame = camera.read()
    finally:
        camera.close()
    if frame is None:
        return False, f"{camera.device}: opened but no frame"
    height, width = frame.shape[:2]
    return True, f"{camera.device}: {width}x{height} frame"


def check_models(settings: Settings) -> Result:
    from bob.vision.faces import FER, SFACE, YUNET

    folder = _resolve(settings.models_dir)
    missing = [name for name in (YUNET, SFACE) if not (folder / name).exists()]
    if missing:
        return False, f"missing in {folder}: {', '.join(missing)} (run scripts/fetch_models.py)"
    detail = f"YuNet + SFace in {folder}"
    detail += " + FER" if (folder / FER).exists() else " (FER optional, absent)"
    return True, detail


def check_phrases(settings: Settings) -> Result:
    folder = _resolve(settings.phrases_dir)
    count = len(list(folder.glob("*.wav"))) if folder.is_dir() else 0
    if count == 0:
        return False, f"no WAVs in {folder} (run scripts/render_phrases.py)"
    return True, f"{count} cached WAVs in {folder}"


def check_poses(settings: Settings) -> Result:
    path = _resolve(settings.poses_path)
    if not path.exists():
        return False, f"{path} missing (run scripts/teach_poses.py)"
    from bob.hardware.arm import Poses

    poses = Poses.load(path)
    return True, f"{len(poses.named)} poses, ids {poses.ids}, hold threshold {poses.hold_load_threshold}"


def check_embeddings(settings: Settings) -> Result:
    path = _resolve(settings.people_dir) / "embeddings.npz"
    if not path.exists():
        return False, f"{path} missing (run scripts/enroll_faces.py)"
    import numpy as np

    names = [str(n) for n in np.load(path, allow_pickle=False)["names"]]
    return True, f"{len(names)} people: {', '.join(names)[:120]}"


def default_checks(settings: Settings) -> list[Check]:
    return [
        Check("openai", P0, lambda: check_openai(settings)),
        Check("elevenlabs", P1, lambda: check_elevenlabs(settings)),
        Check("audio device", P0, lambda: check_audio(settings)),
        Check("xvf3800 usb/doa", P1, lambda: check_xvf3800_usb(settings)),
        Check("servos", P1, lambda: check_servos(settings)),
        Check("eyes", P0, lambda: check_eyes(settings)),
        Check("camera", P0, lambda: check_camera(settings)),
        Check("vision models", P0, lambda: check_models(settings)),
        Check("phrase cache", P1, lambda: check_phrases(settings)),
        Check("poses file", P1, lambda: check_poses(settings)),
        Check("face embeddings", P1, lambda: check_embeddings(settings)),
    ]


# --- runner --------------------------------------------------------------------------------


def run_check(check: Check) -> Result:
    try:
        return check.run()
    except Exception as exc:  # a failing device must not stop the table
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def main(argv: list[str] | None = None, settings: Settings | None = None, checks=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-color", action="store_true", help="plain text output")
    args = parser.parse_args(argv)
    settings = settings if settings is not None else Settings.from_env()
    checks = list(checks) if checks is not None else default_checks(settings)
    color = not args.no_color and sys.stdout.isatty()

    def paint(text: str, tone: str) -> str:
        return f"{tone}{text}{RESET}" if color else text

    print(f"Bob doctor  mode={settings.mode} hardware={settings.hardware}")
    width = max(len(c.name) for c in checks) if checks else 8
    failures: list[Check] = []
    for check in checks:
        ok, detail = run_check(check)
        if ok is None:
            label, tone = "SKIP", YELLOW
        elif ok:
            label, tone = "OK  ", GREEN
        else:
            label, tone = "FAIL", RED
            if check.priority == P0:
                failures.append(check)
        print(f"  {paint(check.priority, DIM)}  {check.name.ljust(width)}  {paint(label, tone)}  {detail}")
    if failures:
        print(paint(f"{len(failures)} P0 check(s) failed: {', '.join(c.name for c in failures)}", RED))
        return 1
    print(paint("All P0 checks passed.", GREEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())

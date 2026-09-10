#!/usr/bin/env bash
# One-shot, idempotent setup for Bob on a Jetson Orin Nano (JetPack 6, Ubuntu 22.04, aarch64).
# Usage:  bash scripts/jetson_setup.sh [--bundle /media/$USER/BOBUSB/bundle]
# Re-run freely; every step checks before it changes anything.
set -euo pipefail

BUNDLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle) BUNDLE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
log() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m   ok: %s\033[0m\n' "$*"; }

log "System packages"
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq || true
  sudo apt-get install -y -qq libportaudio2 libusb-1.0-0 python3-venv v4l-utils alsa-utils git curl \
    libsndfile1 ffmpeg usbutils >/dev/null
  ok "apt packages present"
else
  echo "   (no apt-get; skipping system packages)"
fi

log "uv + Python 3.12"
if ! command -v uv >/dev/null; then
  if [[ -n "$BUNDLE" && -x "$BUNDLE/uv/uv" ]]; then
    mkdir -p "$HOME/.local/bin" && cp "$BUNDLE/uv/uv" "$HOME/.local/bin/uv"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12 >/dev/null 2>&1 || true
ok "uv $(uv --version | awk '{print $2}')"

log "Python environment (robot group)"
if [[ -n "$BUNDLE" && -d "$BUNDLE/wheels" ]]; then
  UV_OFFLINE=1 UV_FIND_LINKS="$BUNDLE/wheels" uv sync --group robot --python 3.12 \
    || uv sync --group robot --python 3.12
else
  uv sync --group robot --python 3.12
fi
ok "venv ready: $(uv run python -c 'import sys; print(sys.version.split()[0])')"

log "Vision models"
mkdir -p models/vision
if [[ -n "$BUNDLE" && -d "$BUNDLE/models/vision" ]]; then
  cp -n "$BUNDLE"/models/vision/*.onnx models/vision/ 2>/dev/null || true
fi
uv run python scripts/fetch_models.py || echo "   (model download failed; copy models/vision/*.onnx from the USB bundle)"

log "Phrase cache and eye firmware from bundle"
if [[ -n "$BUNDLE" ]]; then
  [[ -d "$BUNDLE/assets/phrases" ]] && mkdir -p assets/phrases && cp -n "$BUNDLE"/assets/phrases/* assets/phrases/ 2>/dev/null || true
  [[ -f "$BUNDLE/firmware/eye-merged.bin" ]] && mkdir -p firmware/eye/dist && cp -n "$BUNDLE/firmware/eye-merged.bin" firmware/eye/dist/ || true
  [[ -f "$BUNDLE/.env" && ! -f .env ]] && cp "$BUNDLE/.env" .env && chmod 600 .env && ok "copied .env from bundle"
fi

log "udev rules (ReSpeaker USB control, servo/eye serial ports)"
if [[ -d /etc/udev/rules.d ]]; then
  sudo install -m 644 scripts/udev/99-bob.rules /etc/udev/rules.d/99-bob.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ok "udev rules installed"
fi
if ! id -nG "$USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$USER"
  echo "   added $USER to dialout: log out and back in (or reboot) before using serial ports"
fi

log "ALSA default device -> ReSpeaker XVF3800"
CARD="$(arecord -l 2>/dev/null | grep -i -m1 'XVF3800\|reSpeaker\|Array' | sed -E 's/^card ([0-9]+).*/\1/' || true)"
if [[ -n "$CARD" ]]; then
  cat > "$HOME/.asoundrc" <<EOF
# Written by scripts/jetson_setup.sh: route default capture/playback through the ReSpeaker XVF3800.
pcm.!default { type plug; slave.pcm "hw:${CARD},0" }
ctl.!default { type hw; card ${CARD} }
EOF
  ok "default ALSA device is card ${CARD}"
else
  echo "   ReSpeaker not detected yet (plug it in and re-run, or set BOB_AUDIO_DEVICE in .env)"
fi

log "Performance mode"
if command -v nvpmodel >/dev/null; then
  sudo nvpmodel -m 0 >/dev/null 2>&1 || true
  sudo jetson_clocks >/dev/null 2>&1 || true
  ok "nvpmodel MAXN + jetson_clocks"
fi

log "systemd user service (installed, enabled, NOT started)"
mkdir -p "$HOME/.config/systemd/user"
sed "s#%REPO%#$REPO#g" scripts/bob.service > "$HOME/.config/systemd/user/bob.service"
systemctl --user daemon-reload
systemctl --user enable bob.service >/dev/null 2>&1 || true
loginctl enable-linger "$USER" >/dev/null 2>&1 || true
ok "start later with: systemctl --user start bob   (logs: journalctl --user -u bob -f)"

log "Config"
[[ -f .env ]] || { cp .env.example .env; echo "   created .env from .env.example: fill in keys and set BOB_MODE=cloud BOB_HARDWARE=real"; }
mkdir -p people config assets/phrases
[[ -f config/poses.json ]] || echo "   config/poses.json missing: run scripts/teach_poses.py after discover_servos.py"

cat <<EOF

Next steps:
  1. uv run python scripts/doctor.py          # every device/API check must be green for P0
  2. uv run python scripts/enroll_faces.py    # after putting portraits in people/<Name>/
  3. uv run python scripts/discover_servos.py && uv run python scripts/teach_poses.py
  4. systemctl --user start bob && journalctl --user -u bob -f
  5. Phone: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8766/console?token=<BOB_CONSOLE_TOKEN>
EOF

#!/usr/bin/env bash
# Build the ESP32-S3 eye firmware and merge it into one image flashable at 0x0:
#   firmware/eye/dist/eye-merged.bin
# Usage: scripts/build_eyes.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW="$ROOT/firmware/eye"
BUILD="$FW/.pio/build/eye"
DIST="$FW/dist"

PIO="${PIO:-$(command -v pio || true)}"
[[ -n "$PIO" ]] || PIO="$HOME/.local/bin/pio"
[[ -x "$PIO" ]] || { echo "pio not found; install PlatformIO (pipx install platformio)" >&2; exit 1; }

# esptool: prefer the copy PlatformIO installs, then PATH, then uv/python modules.
find_esptool() {
  local c
  for c in "$HOME/.platformio/penv/bin/esptool" "$HOME/.platformio/penv/bin/esptool.py"; do
    [[ -x "$c" ]] && { echo "$c"; return; }
  done
  for c in esptool esptool.py; do
    command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return; }
  done
  if command -v uv >/dev/null 2>&1 && uv run --project "$ROOT" python -c "import esptool" >/dev/null 2>&1; then
    echo "uv run --project $ROOT python -m esptool"; return
  fi
  if python3 -c "import esptool" >/dev/null 2>&1; then
    echo "python3 -m esptool"; return
  fi
  echo "esptool not found (looked in ~/.platformio/penv/bin, PATH, uv env, python3)" >&2
  exit 1
}

ESPTOOL="$(find_esptool)"
# esptool >= 5 renamed sub-commands to hyphens (merge-bin); 4.x uses underscores (merge_bin).
ESPTOOL_VER="$($ESPTOOL version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
if [[ "${ESPTOOL_VER%%.*}" =~ ^[0-9]+$ ]] && (( ${ESPTOOL_VER%%.*} >= 5 )); then
  MERGE_CMD="merge-bin"; FLASH_MODE_OPT="--flash-mode"; FLASH_SIZE_OPT="--flash-size"
else
  MERGE_CMD="merge_bin"; FLASH_MODE_OPT="--flash_mode"; FLASH_SIZE_OPT="--flash_size"
fi
echo "using esptool ${ESPTOOL_VER:-unknown} ($ESPTOOL)"

"$PIO" run -d "$FW"

BOOT_APP0=""
for c in "$HOME"/.platformio/packages/framework-arduinoespressif32*/tools/partitions/boot_app0.bin; do
  [[ -f "$c" ]] && { BOOT_APP0="$c"; break; }
done
[[ -n "$BOOT_APP0" ]] || { echo "boot_app0.bin not found under ~/.platformio/packages" >&2; exit 1; }
for f in "$BUILD/bootloader.bin" "$BUILD/partitions.bin" "$BUILD/firmware.bin"; do
  [[ -f "$f" ]] || { echo "missing build artefact $f" >&2; exit 1; }
done

mkdir -p "$DIST"
$ESPTOOL --chip esp32s3 "$MERGE_CMD" -o "$DIST/eye-merged.bin" \
  "$FLASH_MODE_OPT" dio "$FLASH_SIZE_OPT" 16MB \
  0x0 "$BUILD/bootloader.bin" \
  0x8000 "$BUILD/partitions.bin" \
  0xe000 "$BOOT_APP0" \
  0x10000 "$BUILD/firmware.bin"

echo "merged image: $DIST/eye-merged.bin ($(wc -c < "$DIST/eye-merged.bin" | tr -d " ") bytes)"

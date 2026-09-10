#!/usr/bin/env bash
# Flash one eye board with the merged image, then tell it which eye it is and ping it.
# Usage: scripts/flash_eyes.sh /dev/cu.usbmodemXXXX L|R
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <port> <L|R>   e.g. $0 /dev/cu.usbmodem101 L" >&2
  exit 2
fi
PORT="$1"
SIDE="$2"
[[ "$SIDE" == "L" || "$SIDE" == "R" ]] || { echo "side must be L or R" >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/firmware/eye/dist/eye-merged.bin"
[[ -f "$BIN" ]] || { echo "$BIN missing; run scripts/build_eyes.sh first" >&2; exit 1; }

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
ESPTOOL_VER="$($ESPTOOL version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
if [[ "${ESPTOOL_VER%%.*}" =~ ^[0-9]+$ ]] && (( ${ESPTOOL_VER%%.*} >= 5 )); then
  WRITE_CMD="write-flash"
else
  WRITE_CMD="write_flash"
fi
echo "using esptool ${ESPTOOL_VER:-unknown} ($ESPTOOL)"

$ESPTOOL --chip esp32s3 --port "$PORT" --baud 921600 \
  --before default-reset --after hard-reset \
  "$WRITE_CMD" 0x0 "$BIN"

echo "waiting 3 s for the board to reboot and re-enumerate..."
sleep 3

if command -v uv >/dev/null 2>&1; then
  PY=(uv run --project "$ROOT" python)
else
  PY=(python3)
fi

"${PY[@]}" - "$PORT" "$SIDE" <<'PYEOF'
import json, sys, time
import serial

port, side = sys.argv[1], sys.argv[2]

# The CDC port disappears during the reset and comes back a moment later; retry the open.
deadline = time.monotonic() + 10
while True:
    try:
        ser = serial.Serial(port, 115200, timeout=0.5, write_timeout=1)
        break
    except Exception as exc:
        if time.monotonic() > deadline:
            print(f"could not open {port}: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)

def ask(payload, wait=1.0):
    ser.reset_input_buffer()
    ser.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
    end = time.monotonic() + wait
    lines = []
    while time.monotonic() < end:
        raw = ser.readline()
        if raw:
            lines.append(raw.decode(errors="replace").strip())
            if any('"ok"' in l or '"err"' in l for l in lines):
                break
    return lines

with ser:
    time.sleep(0.3)
    ser.reset_input_buffer()  # drop the {"boot":1,...} banner
    for line in ask({"cmd": "side", "value": side}):
        print("side ->", line)
    reply = ask({"cmd": "ping"})
    for line in reply:
        print("ping ->", line)
    ok = any(l.startswith("{") and json.loads(l).get("ok") == 1 and json.loads(l).get("side") == side for l in reply if l.startswith("{"))
    if not ok:
        print(f"WARNING: no {{\"ok\":1,\"side\":\"{side}\"}} reply; check the port / press BOOT and retry", file=sys.stderr)
        sys.exit(1)
    print(f"eye {side} flashed and answering on {port}")
PYEOF

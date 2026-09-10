#!/usr/bin/env bash
# Build an offline USB bundle for the Jetson on this Mac.
# Usage: bash scripts/make_bundle.sh [--to /Volumes/BOBUSB] [--skip-wheels]
# Output: bundle/ (gitignored). Contains the repo archive, aarch64 wheels, vision models,
# phrase cache, eye firmware, uv installer, and a copy of .env (KEEP THE STICK WITH YOU).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
DEST=""; SKIP_WHEELS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to) DEST="$2"; shift 2 ;;
    --skip-wheels) SKIP_WHEELS=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
OUT="$REPO/bundle"
rm -rf "$OUT" && mkdir -p "$OUT"/{wheels,models/vision,assets/phrases,firmware,uv}
log() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }

log "Repo archive (HEAD, tracked files only)"
git archive --format=tar.gz -o "$OUT/bobot.tar.gz" HEAD
git rev-parse HEAD > "$OUT/COMMIT"

if [[ $SKIP_WHEELS -eq 0 ]]; then
  log "aarch64 wheels for Python 3.12 (this downloads a few hundred MB)"
  uv export --group robot --no-hashes --no-emit-project --no-dev -o "$OUT/requirements.txt" >/dev/null
  uv run python scripts/download_wheels.py "$OUT/requirements.txt" "$OUT/wheels" || true
  ls "$OUT/wheels" | wc -l | xargs printf '   %s wheel/sdist files\n'
fi

log "uv installer binary for aarch64"
UV_VER="$(uv --version | awk '{print $2}')"
curl -LsSf "https://github.com/astral-sh/uv/releases/download/${UV_VER}/uv-aarch64-unknown-linux-gnu.tar.gz" \
  | tar -xz -C "$OUT/uv" --strip-components=1 2>/dev/null || echo "   (uv binary download failed; installer will use the network)"

log "Models, phrases, firmware, env"
cp models/vision/*.onnx "$OUT/models/vision/" 2>/dev/null || echo "   no models in models/vision (run scripts/fetch_models.py)"
cp assets/phrases/* "$OUT/assets/phrases/" 2>/dev/null || echo "   no phrase cache (run scripts/render_phrases.py)"
cp firmware/eye/dist/eye-merged.bin "$OUT/firmware/" 2>/dev/null || echo "   no eye firmware (run scripts/build_eyes.sh)"
[[ -f .env ]] && cp .env "$OUT/.env" && chmod 600 "$OUT/.env" && echo "   .env copied: contains API keys, keep the stick with you"

cat > "$OUT/README-USB.txt" <<'EOF'
Bob USB bundle. On the Jetson:
  mkdir -p ~/bobot && tar -xzf bobot.tar.gz -C ~/bobot
  cd ~/bobot && bash scripts/jetson_setup.sh --bundle "$(dirname "$(readlink -f "$0")")"
  (or:  bash scripts/jetson_setup.sh --bundle /media/$USER/<STICK>/bundle)
Then follow docs/DEPLOY.md from step 4.
EOF

du -sh "$OUT"
if [[ -n "$DEST" ]]; then
  log "Copying to $DEST"
  mkdir -p "$DEST/bundle" && rsync -a --delete "$OUT/" "$DEST/bundle/"
  sync && echo "   done; eject the stick safely"
fi

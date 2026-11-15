#!/usr/bin/env bash
# İlk kurulum: Python venv + pip + npm
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;36m[alaz]\033[0m %s\n' "$*"; }

# VS Code Flatpak ise host'ta kur
if [[ -f /.flatpak-info || -n "${FLATPAK_ID:-}" ]] && command -v flatpak-spawn >/dev/null 2>&1; then
  exec flatpak-spawn --host -- bash "$ROOT/scripts/setup.sh" "$@"
fi

log "Python venv ..."
if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/backend/.venv"
fi
"$ROOT/backend/.venv/bin/python" -m pip install -U pip -q
"$ROOT/backend/.venv/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
"$ROOT/backend/.venv/bin/python" -c "import numpy, fastapi" || {
  echo "Python paket doğrulaması başarısız"
  exit 1
}

if [[ ! -f "$ROOT/backend/.env" && -f "$ROOT/backend/.env.example" ]]; then
  cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"
  log "backend/.env oluşturuldu — FIRMS_MAP_KEY (ve isteğe bağlı DEEPSEEK_API_KEY) ekle"
fi

# Cursor terminalinde npm PATH'te olmayabilir
NPM_BIN=""
for p in "$HOME/nodejs/bin/npm" /usr/bin/npm /usr/local/bin/npm; do
  if [[ -x "$p" ]]; then NPM_BIN="$p"; break; fi
done
if [[ -z "$NPM_BIN" ]] && command -v npm >/dev/null 2>&1; then
  NPM_BIN="$(command -v npm)"
fi
[[ -n "$NPM_BIN" ]] || { echo "npm bulunamadı"; exit 1; }

log "npm install ($NPM_BIN) ..."
"$NPM_BIN" install

log "Tamam. Başlatmak için:"
log "  bash scripts/start.sh"
log "  bash scripts/start.sh --with-slides   # sunum dahil"

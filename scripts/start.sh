#!/usr/bin/env bash
# Alaz — tek komutla API + UI (+ sunum) ayağa kaldırır.
# Kullanım:
#   bash scripts/start.sh
#   bash scripts/start.sh --with-slides
#   bash scripts/start.sh --setup
#   Ctrl+Shift+B  (VS Code / Cursor task)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# VS Code Flatpak: host'taki node/python/venv'e erişemez → host'ta yeniden çalıştır
if [[ -f /.flatpak-info || -n "${FLATPAK_ID:-}" ]] && command -v flatpak-spawn >/dev/null 2>&1; then
  exec flatpak-spawn --host -- bash "$ROOT/scripts/start.sh" "$@"
fi

HOST="${ALAZ_HOST:-127.0.0.1}"
API_PORT="${ALAZ_API_PORT:-8000}"
UI_PORT="${ALAZ_UI_PORT:-5173}"
SLIDES_PORT="${ALAZ_SLIDES_PORT:-5500}"

WITH_SLIDES=0
DO_SETUP=0
for arg in "$@"; do
  case "$arg" in
    --with-slides|--demo) WITH_SLIDES=1 ;;
    --setup) DO_SETUP=1 ;;
    -h|--help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
  esac
done

if [[ "${npm_lifecycle_event:-}" == "start:demo" ]]; then
  WITH_SLIDES=1
fi

log() { printf '\033[1;36m[alaz]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[alaz]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[alaz]\033[0m %s\n' "$*" >&2; exit 1; }

resolve_node() {
  # Vite 8 / rolldown → Node ≥ 20.19 (tercihen 22). Eski ~/nodejs (v20.11) yetmez.
  local p ver major minor
  local -a candidates=(
    "${ALAZ_NODE:-}"
    /usr/bin/node-22
    /usr/bin/node
    /usr/sbin/node
    /usr/local/bin/node
    "$HOME/nodejs/bin/node"
  )
  for p in "${candidates[@]}"; do
    [[ -n "$p" && -x "$p" ]] || continue
    ver="$("$p" -v 2>/dev/null | tr -d v)" || continue
    major="${ver%%.*}"
    minor="${ver#*.}"; minor="${minor%%.*}"
    # 22+ veya 20.19+
    if [[ "$major" -gt 20 ]] || { [[ "$major" -eq 20 ]] && [[ "$minor" -ge 19 ]]; }; then
      echo "$p"
      return 0
    fi
  done
  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi
  return 1
}

resolve_npm() {
  local p node_dir
  NODE_BIN="${NODE_BIN:-$(resolve_node || true)}"
  if [[ -n "${NODE_BIN:-}" ]]; then
    node_dir="$(dirname "$NODE_BIN")"
    for p in "$node_dir/npm" "$HOME/nodejs/bin/npm" /usr/bin/npm /usr/local/bin/npm; do
      [[ -x "$p" ]] && { echo "$p"; return 0; }
    done
  fi
  command -v npm 2>/dev/null || return 1
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    # bağlanabiliyorsa dolu say
    (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
  fi
}

pids_on_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser "${port}/tcp" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp "( sport = :$port )" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true
  fi
}

free_port() {
  local port="$1"
  local pids
  if ! port_in_use "$port"; then
    return 0
  fi
  warn "port $port dolu — önceki süreç kapatılıyor"
  pids="$(pids_on_port "$port")"
  if [[ -n "${pids:-}" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.4
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  fi
  sleep 0.5
  if port_in_use "$port"; then
    warn "port $port hâlâ dolu — elle kapatman gerekebilir"
  fi
}

PY="$ROOT/backend/.venv/bin/python"
VITE_JS="$ROOT/node_modules/vite/bin/vite.js"
NODE_BIN=""

ensure_backend() {
  if [[ ! -x "$PY" ]]; then
    if [[ "$DO_SETUP" -eq 1 ]]; then
      log "Python venv oluşturuluyor ..."
      python3 -m venv "$ROOT/backend/.venv"
      "$PY" -m pip install -U pip -q
      "$PY" -m pip install -r "$ROOT/backend/requirements.txt"
    else
      die "backend/.venv yok. VS Code: Run Task → Alaz: Setup"
    fi
  fi

  if ! "$PY" -c "import numpy, fastapi, uvicorn" 2>/dev/null; then
    warn "Python paketleri eksik — kuruluyor ..."
    "$PY" -m pip install -U pip -q
    "$PY" -m pip install -r "$ROOT/backend/requirements.txt"
    "$PY" -c "import numpy, fastapi, uvicorn" \
      || die "numpy/fastapi kurulamadı. Bir kez: bash scripts/setup.sh"
  fi

  if [[ ! -f "$ROOT/backend/.env" && -f "$ROOT/backend/.env.example" ]]; then
    warn "backend/.env yok — .env.example kopyalanıyor (FIRMS_MAP_KEY ekle)"
    cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"
  fi
}

ensure_frontend() {
  NODE_BIN="$(resolve_node)" || die "node bulunamadı. Node.js kurulu mu? (örn. ~/nodejs/bin/node)"
  log "node → $NODE_BIN"

  if [[ ! -f "$VITE_JS" ]]; then
    if [[ "$DO_SETUP" -eq 1 ]]; then
      local npm_bin
      npm_bin="$(resolve_npm)" || die "npm bulunamadı"
      log "npm install ..."
      "$npm_bin" install
    else
      die "node_modules yok. VS Code: Run Task → Alaz: Setup"
    fi
  fi
  [[ -f "$VITE_JS" ]] || die "vite yok. bash scripts/setup.sh"
}

PIDS=()
CLEANED=0
cleanup() {
  [[ "$CLEANED" -eq 1 ]] && return 0
  CLEANED=1
  trap - EXIT INT TERM
  log "kapatılıyor ..."
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 0.3
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  free_port "$API_PORT" || true
  free_port "$UI_PORT" || true
  if [[ "$WITH_SLIDES" -eq 1 ]]; then
    free_port "$SLIDES_PORT" || true
  fi
  exit 0
}
trap cleanup EXIT INT TERM

ensure_backend
ensure_frontend

free_port "$API_PORT"
free_port "$UI_PORT"
if [[ "$WITH_SLIDES" -eq 1 ]]; then
  free_port "$SLIDES_PORT"
fi

wait_for_api() {
  local url="http://${HOST}:${API_PORT}/api/health"
  local max_s="${ALAZ_API_WAIT:-90}"
  local t=0
  log "API hazırlanıyor (model kontrolü ~10–20 sn) ..."
  while [[ "$t" -lt "$max_s" ]]; do
    if ! kill -0 "${PIDS[0]}" 2>/dev/null; then
      die "API süreci çıktı — yukarıdaki Python hatasına bak"
    fi
    if curl -sf --max-time 1 "$url" >/dev/null 2>&1; then
      log "API hazır → $url"
      return 0
    fi
    # curl yoksa port dinlemeyi dene
    if ! command -v curl >/dev/null 2>&1 && port_in_use "$API_PORT"; then
      sleep 1
      if port_in_use "$API_PORT"; then
        log "API portu açık → http://${HOST}:${API_PORT}"
        return 0
      fi
    fi
    sleep 1
    t=$((t + 1))
    if (( t % 5 == 0 )); then
      log "hâlâ bekleniyor… ${t}s / ${max_s}s"
    fi
  done
  die "API ${max_s}s içinde hazır olmadı"
}

log "API  → http://${HOST}:${API_PORT}"
(
  cd "$ROOT/backend"
  exec "$PY" run.py --host "$HOST" --port "$API_PORT"
) &
PIDS+=($!)

wait_for_api

log "UI   → http://${HOST}:${UI_PORT}"
(
  cd "$ROOT"
  # shebang'e (/usr/bin/env node) güvenme — explicit node
  exec "$NODE_BIN" "$VITE_JS" --host "$HOST" --port "$UI_PORT"
) &
PIDS+=($!)

if [[ "$WITH_SLIDES" -eq 1 ]]; then
  log "Slides → http://${HOST}:${SLIDES_PORT}/"
  (
    cd "$ROOT"
    exec "$PY" -m http.server "$SLIDES_PORT" --bind "$HOST" --directory presentation
  ) &
  PIDS+=($!)
fi

echo
log "Hazır. Ctrl+C ile hepsini durdur."
log "UI  http://${HOST}:${UI_PORT}"
if [[ "$WITH_SLIDES" -eq 1 ]]; then
  log "Sunum  http://${HOST}:${SLIDES_PORT}/"
fi
echo

sleep 1
for pid in "${PIDS[@]}"; do
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "süreç $pid hemen çıktı — log yukarıda"
    cleanup
  fi
done

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      warn "süreç $pid çıktı — diğerleri kapatılıyor"
      cleanup
    fi
  done
  sleep 1
done

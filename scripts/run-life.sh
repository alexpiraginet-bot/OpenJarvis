#!/usr/bin/env bash
# Run Jarvis — Life OS on this machine and print the URL to open on a phone.
#
# The fastest way to hold the app in your hand: no host, no domain, no
# certificate. The phone just has to be on the same Wi-Fi.
#
#   ./scripts/run-life.sh
#
# Caveat worth knowing before you try it: over plain HTTP on a LAN address,
# Chrome and Safari refuse the microphone and will not offer "add to home
# screen". Everything else — the springboard, the apps, paying a bill, the
# assistant's typed answers — works. Voice and installability need HTTPS,
# which means a real host; see deploy/fly/fly.toml.

set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${OPENJARVIS_LIFE_PORT:-8100}"
export OPENJARVIS_LIFE_PORT="$PORT"
# Bind every interface so the phone can reach it. Safe on a home network,
# never on an untrusted one.
export OPENJARVIS_LIFE_HOST="${OPENJARVIS_LIFE_HOST:-0.0.0.0}"

echo "==> Building the PWA"
( cd frontend && npm install --silent && npm run build )

# The LAN address the phone must dial. Both branches are needed: `hostname -I`
# is Linux-only and `ipconfig getifaddr` is macOS-only.
lan_ip() {
  if command -v ipconfig >/dev/null 2>&1; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
  elif command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{print $1}'
  fi
}

IP="$(lan_ip)"
echo
echo "==> Jarvis está subindo"
echo "    neste computador:  http://localhost:${PORT}/vida"
if [ -n "${IP:-}" ]; then
  echo "    no seu celular:    http://${IP}:${PORT}/vida"
else
  echo "    no seu celular:    descubra o IP da máquina nesta rede e use :${PORT}/vida"
fi
echo
echo "    A primeira conta criada é a sua — depois dela o cadastro fecha sozinho."
echo

exec uv run python -m openjarvis.life.server

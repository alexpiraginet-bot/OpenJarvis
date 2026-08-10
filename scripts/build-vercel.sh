#!/usr/bin/env bash
# Assemble a self-contained Vercel deployment directory.
#
#   ./scripts/build-vercel.sh
#   cd deploy/vercel/.build && vercel deploy --prod
#
# Vercel's Python runtime puts the project root on sys.path, so the openjarvis
# source is vendored there rather than installed from PyPI — this repo is not
# published, and giving Vercel git credentials to install it would be a worse
# trade than copying a few megabytes of pure Python.
#
# Layout produced:
#
#   .build/public/       the PWA, served by Vercel's CDN (not by the function)
#   .build/api/index.py  the ASGI entrypoint
#   .build/openjarvis/   the vendored package
#   .build/vercel.json   routing, caching and security headers
#   .build/requirements.txt

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
OUT="$ROOT/deploy/vercel/.build"

echo "==> Building the PWA"
( cd frontend && npm install --silent && npm run build )

echo "==> Assembling $OUT"
rm -rf "$OUT"
mkdir -p "$OUT/api" "$OUT/public"

# The PWA. Vite writes it into the Python package's static/ directory; on
# Vercel it belongs in public/, served by the CDN.
cp -R "$ROOT/src/openjarvis/server/static/." "$OUT/public/"

cp "$ROOT/deploy/vercel/api/index.py" "$OUT/api/index.py"
cp "$ROOT/deploy/vercel/vercel.json" "$OUT/vercel.json"
cp "$ROOT/deploy/vercel/requirements.txt" "$OUT/requirements.txt"

# Vendor the package, minus anything the function never imports. The static
# bundle is already in public/, and the node bridges are for channels the Life
# API does not use — copying them would blow past the function size limit for
# no benefit. Plain cp + prune rather than rsync, which is not present on every
# build image.
cp -R "$ROOT/src/openjarvis" "$OUT/openjarvis"
rm -rf \
  "$OUT/openjarvis/server/static" \
  "$OUT/openjarvis/agents/claude_code_runner" \
  "$OUT/openjarvis/channels/whatsapp_baileys_bridge"
find "$OUT/openjarvis" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$OUT/openjarvis" -name '*.pyc' -delete 2>/dev/null || true

echo "==> Verifying the vendored tree imports on its own"
# Catches a module pruned by mistake *before* a deploy does, by importing with
# only the build directory on the path. Prefer the project venv — the system
# python3 has no fastapi, and a check that silently skips is worthless.
if command -v uv >/dev/null 2>&1; then
  PY=(uv run --project "$ROOT" python)
else
  PY=(python3)
fi
( cd "$OUT" && PYTHONPATH="$OUT" OPENJARVIS_LIFE_DB="postgres://u:p@h/d" \
    "${PY[@]}" -c "
import sys
# Drop the repo's own src/ so the vendored copy is what actually gets loaded.
sys.path = [p for p in sys.path if 'OpenJarvis/src' not in p]
import openjarvis.life.server
import openjarvis.server.life_routes
assert openjarvis.__file__.startswith('$OUT'), (
    f'imported the wrong copy: {openjarvis.__file__}'
)
print('  vendored openjarvis imports cleanly:', openjarvis.__file__)
" )

SIZE="$(du -sh "$OUT" | cut -f1)"
echo
echo "==> Pronto: $OUT ($SIZE)"
echo "    Antes de publicar, defina no projeto Vercel:"
echo "      OPENJARVIS_LIFE_DB = postgres://...   (DSN do Supabase)"
echo "    Opcional, só para abrir cadastro:"
echo "      OPENJARVIS_LIFE_OPEN_SIGNUP = 1"

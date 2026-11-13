#!/usr/bin/env bash
# Run the transcription server. Works from a shell and from systemd.
set -euo pipefail
cd "$(dirname "$0")"

# Settings come from .env when it exists. Under systemd the unit passes the same
# file through EnvironmentFile, so reading it twice is harmless.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    echo "No virtualenv found at $PYTHON. Create one first (see README)." >&2
    exit 1
fi

# CTranslate2 loads the CUDA libraries dynamically and does not look for them in
# the site-packages of the nvidia-* wheels, so those directories are added here.
# The list is empty on a CPU-only install, which is fine.
NV_LIBS=$("$PYTHON" - <<'PY'
import os
import site

paths = []
for base in site.getsitepackages():
    nvidia = os.path.join(base, "nvidia")
    if not os.path.isdir(nvidia):
        continue
    for name in sorted(os.listdir(nvidia)):
        lib = os.path.join(nvidia, name, "lib")
        if os.path.isdir(lib):
            paths.append(lib)
print(":".join(paths))
PY
)
if [ -n "$NV_LIBS" ]; then
    export LD_LIBRARY_PATH="$NV_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# A PYTHONPATH from the caller could inject packages built for another Python
# version into the virtualenv, so start with a clean environment.
unset PYTHONPATH

exec "$PYTHON" -m uvicorn server:app \
    --host "${HOST:-127.0.0.1}" \
    --port "${PORT:-8766}" \
    --no-access-log \
    --log-level "${LOG_LEVEL:-info}"

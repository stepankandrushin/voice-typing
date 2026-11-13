#!/usr/bin/env bash
# Check a running server with the same request the voice-typing client makes.
# Usage: ./smoke_test.sh <recording.wav> [base_url] [token]
set -euo pipefail

WAV="${1:?usage: smoke_test.sh <recording.wav> [base_url] [token]}"
BASE="${2:-http://127.0.0.1:8766}"
TOKEN="${3:-}"

echo "health: $(curl -fsS "$BASE/health")"

URL="$BASE/transcribe?languages=ru,en&app=smoke-test"
if [ -n "$TOKEN" ]; then
    URL="$URL&token=$TOKEN"
fi

RESPONSE=$(curl -fsS -X POST "$URL" -F "file=@$WAV")
printf '%s\n' "$RESPONSE"
printf '%s' "$RESPONSE" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
assert data.get("text"), "the server returned an empty transcription"
print(
    "OK language=%s duration=%.2fs elapsed=%.2fs"
    % (data["language"], data["duration_s"], data["elapsed_s"])
)
'

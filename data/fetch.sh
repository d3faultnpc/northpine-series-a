#!/usr/bin/env bash
# Downloads the Northpine data room (21 files, fictional) into data/raw.
set -euo pipefail
cd "$(dirname "$0")/.."
URL="https://knwlg.get-ryze.ai/raw/s/VV5oOiMySMzb/northpine_data.zip"
TMP="$(mktemp -d)"
curl -sSL -o "$TMP/northpine_data.zip" "$URL"
unzip -q -o "$TMP/northpine_data.zip" -d "$TMP/x"
mkdir -p data/raw
cp "$TMP"/x/northpine_data/* data/raw/
chmod a-w data/raw/*
echo "files in data/raw: $(ls data/raw | grep -v '^\.' | wc -l | tr -d ' ') (expected 21)"

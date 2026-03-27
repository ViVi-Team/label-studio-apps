#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any existing server on port 8081
lsof -ti:8081 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true

# Start image server from results/previews/
cd "$SCRIPT_DIR/results/previews"
python3 "$SCRIPT_DIR/server.py" 8081 &
sleep 2

# Start Label Studio
label-studio start checking_perturb_bboxes --init --label-config="$SCRIPT_DIR/config.xml"
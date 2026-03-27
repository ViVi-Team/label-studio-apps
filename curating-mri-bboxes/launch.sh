#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export LOCAL_FILES_SERVING_ENABLED=true
export LOCAL_FILES_DOCUMENT_ROOT="$SCRIPT_DIR"

lsof -ti:8081 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true

INPUT_DIR="$SCRIPT_DIR/images"
INPUT_DIR_ESCAPED=$(printf '%s\n' "$INPUT_DIR" | sed -e 's/[\/&]/\\&/g')
find "$INPUT_DIR" -type f -name "*.jpg" | sed "s|${INPUT_DIR_ESCAPED}|http://localhost:8081/|" > "$SCRIPT_DIR/files.txt"

cd "$SCRIPT_DIR/images"
python3 "$SCRIPT_DIR/server.py" 8081 &
sleep 2

label-studio start checking_perturb_bboxes --init --label-config="$SCRIPT_DIR/config.xml"
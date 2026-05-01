#!/bin/bash
# Schlage App — Start Script
# Usage: ./start.sh

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/venv"
LOG_FILE="$APP_DIR/logs/app.log"

# Check venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment not found at $VENV_DIR"
    echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Ensure log dir exists
mkdir -p "$(dirname "$LOG_FILE")"

# Activate venv and start uvicorn
cd "$APP_DIR"
echo "[$(date)] Starting Schlage app..." >> "$LOG_FILE"
exec "$VENV_DIR/bin/python" -c "
import os, sys
os.environ[PYTHONPATH] = 
os.environ[APP_DIR] = 
import uvicorn
uvicorn.run(backend.main:app, host=0.0.0.0, port=8000, log_level=info)
" >> "$LOG_FILE" 2>&1

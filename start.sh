#!/bin/bash
# Schlage App — Start Script
# Usage: ./start.sh

set -e

APP_DIR="/root/schlage-app"
VENV_DIR="/root/schlage-app/venv"
LOG_FILE="${APP_DIR}/logs/app.log"

# Load env vars
if [ -f "${APP_DIR}/.env" ]; then
    set -a
    source "${APP_DIR}/.env"
    set +a
fi

# Check venv exists
if [ ! -d "${VENV_DIR}" ]; then
    echo "ERROR: Virtual environment not found at ${VENV_DIR}"
    echo "Run: python3 -m venv ${VENV_DIR} && source ${VENV_DIR}/bin/activate && pip install -r ${APP_DIR}/requirements.txt"
    exit 1
fi

# Ensure log dir exists
mkdir -p "$(dirname "$LOG_FILE")"

# Activate venv and start uvicorn
# Use exec to replace shell so signals pass through; capture logs via script
cd "${APP_DIR}"
echo "[$(date)] Starting Schlage app..." >> "$LOG_FILE"
exec ${VENV_DIR}/bin/python -c "
import os, sys
os.environ['PYTHONPATH'] = '/root/schlage-app'
os.environ['APP_DIR'] = '/root/schlage-app'
import uvicorn
uvicorn.run('backend.main:app', host='0.0.0.0', port=8000, log_level='info')
" >> "$LOG_FILE" 2>&1
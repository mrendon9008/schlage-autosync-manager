#!/usr/bin/env python3
import os
import sys

os.environ['PYTHONPATH'] = '/root/schlage-app'
os.environ['APP_DIR'] = '/root/schlage-app'

import logging

logger = logging.getLogger(__name__)

# ── uvicorn entrypoint ────────────────────────────────────────────────────────
import uvicorn
uvicorn.run('backend.main:app', host='0.0.0.0', port=8000, log_level='info')

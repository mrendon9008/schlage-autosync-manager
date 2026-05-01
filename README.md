# Schlage Auto-Sync Manager

Self-hosted Schlage lock manager for short-term rental hosts. Manage access codes across all your locks, sync parent-to-child automatically, and cut out expensive property management software that charges per lock.

Built on Schlage's unofficial API.

## Features

- **Group-based access codes** — Create codes once, apply to every lock in a group
- **Auto sync** — Parent/child lock sync with configurable check times (up to 144 per day)
- **Bulk code management** — Add, edit, or delete codes across all locks at once
- **Force sync** — Manually trigger sync whenever you need it
- **Encrypted credentials** — Your Schlage password stored with AES-256-GCM

## Requirements

- Python 3.11+
- A Schlage account (Schlage Home app or schlage.com)
- A VPS or server to run it on

## Setup

```bash
# Clone the repo
git clone https://github.com/mrendon9008/schlage-autosync-manager.git
cd schlage-autosync-manager

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # (or venv\\Scripts\\activate on Windows)

# Install dependencies
pip install -r requirements.txt

# Run the app
python start_server.py
```

Then open `http://localhost:8000` in your browser.

## First Login

1. Enter your Schlage account email and password
2. Create your first lock group
3. Assign your Schlage locks to the group
4. Start creating access codes

## Auto Sync

The app can automatically sync codes on a schedule. Go to the **Sync** tab, select a check interval (0-144 times per day), and the app will keep your child locks in sync with the parent automatically.

## Security

- Your Schlage credentials are encrypted with AES-256-GCM before storage
- The encryption key is generated locally and never transmitted
- All API traffic is HTTPS

**Important:** Only run this on a server you control. Do not expose the app to the public internet without adding your own authentication layer.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLite
- **Frontend:** Vanilla HTML + CSS + JavaScript (no framework)
- **API:** PySchlage (reverse-engineered Schlage API)
- **Encryption:** AES-256-GCM via Python cryptography library

## Disclaimer

This app uses Schlage's unofficial/internal API. Schlage may change their API at any time, which could temporarily break this app. There is no guarantee of uptime or compatibility.

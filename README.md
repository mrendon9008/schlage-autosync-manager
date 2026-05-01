# Schlage Auto-Sync Manager

Self-hosted Schlage lock manager — save money every month on property management smart lock fees.

Most rental platforms and property management systems charge per lock for access code management. If you have multiple locks on one property, you're paying for each one. This application allows you to only pay for one lock (parent lock) per rental. Any other locks (child locks) at the rental automatically inherit access code creations, edits, and deletions. Making it unnecssary to pay for more than one lock per rental.

Built for tech-minded short-term rental hosts who want to cut software costs without giving up automation.

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
4. Assign a parent lock in each group
5. Set auto sync times to each group, or use the tool to assign 144 syncs per day to a group in bulk at once (every 10 minutes)
6. Start creating, editing, and deleting access codes on the parent lock of each group, and watch all children locks inherit the changes

## Auto Sync

The app can automatically sync codes on a schedule. Go to the **Sync** tab, select a check interval (or bulk set 144 checks), and the app will keep your child locks in sync with the parent automatically at check times.

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

# Schlage Lock Management App — Phase 1 Specification

**Version:** 1.0
**Date:** 2026-04-20
**Author:** Tabitha (Product Manager)
**Status:** Ready for Development

---

## 1. Overview & Goals

**App Name:** Schlage Lock Manager
**Type:** Personal web application (single-user)
**Summary:** A self-hosted web interface for managing Schlage smart locks — login, view lock status, organize locks into groups, and manage access codes.
**Phase 1 Scope:** Full Phase 1 implementation. No Phase 2 planned.
**Target User:** Mike R — sole user, personal use case.

### Goals

- Authenticate with Schlage Cloud API using user credentials
- Display all locks with battery level and online/offline status
- Organize locks into named groups
- Create time-bounded or always-valid access codes scoped to a group
- View all access codes across all locks in one place
- Bulk-delete access codes
- Overwrite same-named access codes across multiple locks (delete + recreate loop)

### Out of Scope (Phase 1)

- Physical lock/unlock commands
- User management / multi-user support
- Access code editing (edit = delete + recreate)
- Notifications or alerts
- Mobile-specific UI
- Access audit logs

---

## 2. Architecture

### Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI |
| Frontend | Vanilla HTML + CSS + JavaScript (no framework) |
| Local Storage | SQLite |
| Schlage API | PySchlage library (Cognito SRP, password-based auth) |
| Deployment | Hostinger VPS, single host |
| Reverse Proxy | Caddy or nginx (SSL termination) |

### Architecture Pattern

**Single-page application (SPA) feel with multi-page backend.**

- Backend serves static files (HTML/CSS/JS) from a `static/` directory
- All client-server communication is via JSON REST API
- No build step required for frontend
- FastAPI runs on port 8000; reverse proxy handles HTTPS on port 443

### Directory Structure

```
schlage-app/
├── SPEC.md
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── auth.py             # Schlage auth helpers
│   ├── database.py         # SQLite setup and helpers
│   ├── models.py           # Pydantic request/response models
│   └── routers/
│       ├── auth.py         # POST /login, POST /logout, GET /me
│       ├── locks.py         # GET /locks
│       ├── groups.py       # GET /groups, POST /groups, DELETE /groups/{id}, POST /groups/{id}/locks, DELETE /groups/{id}/locks/{lock_id}
│       └── codes.py        # GET /codes, POST /codes, DELETE /codes, PUT /codes/{id}
├── static/
│   ├── index.html          # Single HTML shell
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js          # All frontend logic
├── data/
│   └── app.db              # SQLite database file
└── requirements.txt
```

---

## 3. Data Model

### SQLite Schema

```sql
-- Stores encrypted Schlage credentials
CREATE TABLE credentials (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    encrypted_password BLOB NOT NULL,   -- AES-256-GCM encrypted
    nonce BLOB NOT NULL,                -- Encryption nonce
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User-created lock groups
CREATE TABLE IF NOT EXISTS `groups` (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Many-to-many: which Schlage locks belong to which groups
-- Schlage lock ID (device_id) stored as TEXT
CREATE TABLE group_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    lock_id TEXT NOT NULL,             -- Schlage device_id
    lock_name TEXT,                    -- Cached lock name for display
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, lock_id)
);

-- Access codes created by the user
CREATE TABLE access_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                -- User-defined code name (e.g., "Dog Walker")
    code_value TEXT NOT NULL,          -- The actual code (4-8 digits)
    group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    is_always_valid BOOLEAN NOT NULL DEFAULT 0,
    start_datetime TEXT,              -- ISO 8601 datetime (UTC), NULL if always_valid
    end_datetime TEXT,                -- ISO 8601 datetime (UTC), NULL if always_valid
    schlage_lock_id TEXT NOT NULL,    -- Which lock this code applies to
    schlage_code_id TEXT,             -- Schlage API's internal code ID (for delete/overwrite)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, schlage_lock_id)     -- Same name on same lock = unique constraint
);
```

### Notes on the Schema

- `group_locks.lock_id` is the Schlage device ID — a string.
- `access_codes.schlage_lock_id` is the Schlage device ID for the lock this code was created on.
- `access_codes.schlage_code_id` is the ID returned by the Schlage API when the code is created. Used for subsequent delete/overwrite operations.
- `access_codes.name` is the user-facing name, not the code value. Same name on different locks represents the "same person/code type" for overwrite purposes.
- When overwriting, all codes with the same `name` across all locks are deleted, then recreated on each lock with the new code value.

---

## 4. API Endpoints

### Authentication

#### `POST /api/login`
Authenticate with Schlage credentials. On success, credentials are encrypted and stored in SQLite.

**Request:**
```json
{
  "username": "user@example.com",
  "password": "schlage_password"
}
```

**Response (200):**
```json
{
  "message": "Login successful",
  "username": "user@example.com"
}
```

**Response (401):**
```json
{ "detail": "Invalid credentials" }
```

**Behavior:**
1. Attempt Cognito SRP authentication via PySchlage
2. On success, encrypt the password with AES-256-GCM and store in `credentials` table
3. Return success response (no token — session-based)

---

#### `POST /api/logout`
Clear stored credentials.

**Response (200):**
```json
{ "message": "Logged out" }
```

---

#### `GET /api/me`
Check if currently authenticated.

**Response (200):**
```json
{ "authenticated": true, "username": "user@example.com" }
```

**Response (401):**
```json
{ "authenticated": false }
```

---

### Locks

#### `GET /api/locks`
Fetch all locks from Schlage Cloud API and return with current status.

**Response (200):**
```json
{
  "locks": [
    {
      "device_id": "DRTDXXXXXXX",
      "name": "Front Door",
      "battery_level": 85,
      "is_online": true,
      "model": "Schlage Encode Plus",
      "last_activity": "2026-04-20T10:30:00Z"
    }
  ]
}
```

**Behavior:**
1. Load encrypted credentials from SQLite
2. Decrypt password
3. Call `pyschlage` API to fetch lock list and status
4. Return list; do not cache lock data (always fresh)

---

### Groups

#### `GET /api/groups`
List all groups with their assigned locks.

**Response (200):**
```json
{
  "groups": [
    {
      "id": 1,
      "name": "Family",
      "locks": [
        { "lock_id": "DRTDXXXXXXX", "lock_name": "Front Door" },
        { "lock_id": "DRTDYYYYYYY", "lock_name": "Back Door" }
      ]
    }
  ]
}
```

---

#### `POST /api/groups`
Create a new group.

**Request:**
```json
{ "name": "Family" }
```

**Response (201):**
```json
{ "id": 1, "name": "Family", "locks": [] }
```

---

#### `DELETE /api/groups/{id}`
Delete a group. Locks are unlinked but not deleted.

**Response (200):**
```json
{ "message": "Group deleted" }
```

---

#### `POST /api/groups/{id}/locks`
Add a lock to a group.

**Request:**
```json
{ "lock_id": "DRTDXXXXXXX", "lock_name": "Front Door" }
```

**Response (200):**
```json
{ "message": "Lock added to group" }
```

---

#### `DELETE /api/groups/{id}/locks/{lock_id}`
Remove a lock from a group.

**Response (200):**
```json
{ "message": "Lock removed from group" }
```

---

### Access Codes

#### `GET /api/codes`
List all access codes from the local database.

**Response (200):**
```json
{
  "codes": [
    {
      "id": 1,
      "name": "Dog Walker",
      "code_value": "1234",
      "group_id": 1,
      "group_name": "Family",
      "is_always_valid": true,
      "start_datetime": null,
      "end_datetime": null,
      "schlage_lock_id": "DRTDXXXXXXX",
      "lock_name": "Front Door",
      "created_at": "2026-04-18T09:00:00Z"
    }
  ]
}
```

---

#### `POST /api/codes`
Create a new access code. Creates on all locks in the selected group.

**Request:**
```json
{
  "name": "Dog Walker",
  "code_value": "123456",
  "group_id": 1,
  "is_always_valid": false,
  "start_datetime": "2026-04-20T08:00:00Z",
  "end_datetime": "2026-12-31T23:59:59Z"
}
```

**Response (201):**
```json
{
  "message": "Codes created",
  "codes": [
    { "local_id": 1, "schlage_lock_id": "DRTDXXXXXXX", "schlage_code_id": "CODE-ABC123" }
  ]
}
```

**Behavior:**
1. Iterate over all locks in the specified group
2. For each lock, call Schlage API to create the access code
3. Store each code in `access_codes` table with the Schlage code ID
4. Return list of created codes

---

#### `PUT /api/codes/{id}`
Overwrite an access code. Updates the code value, timing, and scope.

**Request:**
```json
{
  "name": "Dog Walker",
  "code_value": "999999",
  "group_id": 1,
  "is_always_valid": false,
  "start_datetime": "2026-04-20T08:00:00Z",
  "end_datetime": "2026-12-31T23:59:59Z"
}
```

**Response (200):**
```json
{
  "message": "Codes overwritten",
  "codes": [
    { "local_id": 5, "schlage_lock_id": "DRTDXXXXXXX", "schlage_code_id": "CODE-DEF456" }
  ]
}
```

**Behavior (Overwrite Flow):**
1. Find all local access codes matching the given `name` across all locks (ignores group_id — the name is the overwrite key)
2. For each matching local code record, call Schlage API to **delete** that code using stored `schlage_code_id`
3. For each lock in the **new** target group (which may be same or different), call Schlage API to **create** the new code
4. Update local `access_codes` table: delete old records, insert new ones
5. Return list of new code records

> Note: Overwrite is name-based, not group-based. If you overwrite "Dog Walker" (currently on Front Door + Back Door) and target a new group that only has Front Door, the Back Door code is deleted and Front Door gets the new code.

---

#### `DELETE /api/codes`
Bulk delete access codes by ID.

**Request:**
```json
{
  "ids": [1, 3, 5]
}
```

**Response (200):**
```json
{
  "message": "Codes deleted",
  "deleted": 3
}
```

**Behavior:**
1. Look up each code in the local database
2. Call Schlage API to delete each code using stored `schlage_code_id`
3. Delete records from local database
4. Return count of deleted codes

---

## 5. Auth Flow

```
User submits credentials (username + password)
         │
         ▼
POST /api/login
         │
         ▼
backend: PySchlage authenticates via Cognito SRP
         │         (actual password auth against AWS Cognito)
         │  ✓ success → encrypt password with AES-256-GCM
         │            → store in credentials table
         │            → return 200
         │
         ✗ failure → return 401
```

**Session Handling:**
- No JWT or session token is issued to the client
- The FastAPI app stores encrypted credentials in SQLite
- All subsequent API calls assume an authenticated session (the server has the credentials)
- The frontend simply shows the authenticated view once login succeeds
- Logout clears the credentials from the database

**Future consideration (out of scope):** Server-side session with a session cookie for additional protection.

---

## 6. Access Code Overwrite Flow (Detailed)

This is the most complex operation in the app. Detailed sequence:

```
PUT /api/codes/{id} with new code_value, new group_id, new timing
         │
         ▼
Find all local access_code records with the SAME name as the code being overwritten
(across all locks — name is the overwrite key, not group)
         │
         ▼
For each old code record:
    Schlage API → DELETE code using schlage_code_id
    Delete local record
         │
         ▼
Iterate over all locks in the NEW target group
For each lock in new group:
    Schlage API → CREATE code with new code_value + timing
    Insert new local record with new schlage_code_id
         │
         ▼
Return summary of new code records
```

**Example:**
- Current: "Dog Walker" code (value=123456) exists on Front Door and Back Door
- User wants to change "Dog Walker" to code 999999, but only targets the "Family" group which has Front Door only
- Result: Front Door gets 999999, Back Door code is deleted and NOT recreated

---

## 7. Security Notes

### Credential Storage
- Schlage password is encrypted at rest using **AES-256-GCM**
- Encryption key is stored in a file (`encryption_key`) outside the web root, readable only by the app process
- Key stored as a 32-byte random value in a file with permissions `600`

### Transport
- App is served behind a reverse proxy (Caddy recommended)
- **HTTPS enforced at the proxy level** (Caddy auto-redirects HTTP to HTTPS)
- PySchlage communicates with Schlage Cloud over HTTPS (TLS)

### Session Security
- No client-side token — credentials stay on the server
- Future enhancement: HTTP-only secure session cookie if multi-user support is added
- Session expiry: on logout, credentials are cleared; no automatic expiry in Phase 1

### Schlage API
- PySchlage uses Cognito SRP (Secure Remote Password) protocol for authentication
- Password never transmitted in plaintext
- API calls made server-side only

### Deployment (Hostinger VPS)
- App runs as a dedicated system user (not root)
- SQLite database in `data/` directory with appropriate file permissions
- Firewall: only ports 443 (HTTPS) and optionally 22 (SSH) exposed

---

## 8. Frontend Page Structure

### Single HTML Shell (`index.html`)

All views are rendered within one HTML file by swapping visible sections.

```
┌─────────────────────────────────────────────┐
│  HEADER: App name + Logout button          │
├─────────────────────────────────────────────┤
│  LOGIN VIEW (shown when not logged in)      │
│    Username field                           │
│    Password field                           │
│    Login button                             │
├─────────────────────────────────────────────┤
│  MAIN VIEW (shown when logged in)           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Locks    │ │ Groups   │ │ Codes    │    │  ← Tab navigation
│  └──────────┘ └──────────┘ └──────────┘    │
│                                             │
│  [Tab content area — one shown at a time]   │
│                                             │
└─────────────────────────────────────────────┘
```

### Tab: Locks

- "Refresh" button to reload lock list
- Table or card grid showing:
  - Lock name
  - Battery level (percentage + color indicator: green >50%, yellow 20-50%, red <20%)
  - Online status (green dot = online, gray dot = offline)
- No lock/unlock controls

### Tab: Groups

- "Create Group" form: text input for group name + submit button
- List of groups, each showing:
  - Group name
  - List of locks assigned (with remove button per lock)
  - "Add Lock" button → shows dropdown of locks not yet in this group
  - Delete group button (with confirmation)
- Inline editing of group name (click to edit, blur or Enter to save)

### Tab: Codes

- "Create Code" form:
  - Group dropdown (populated from API)
  - Code name text input
  - Code value (4–8 digit input, numeric)
  - "Always valid" toggle/checkbox
  - Start datetime picker (hidden when always valid)
  - End datetime picker (hidden when always valid)
  - Submit button
- Table of all codes showing:
  - Checkbox (for bulk select)
  - Code name
  - Code value (masked: `••••••` — revealed on hover/click)
  - Lock name
  - Group name
  - Validity period (always valid or date range)
  - Edit button (pencil icon — opens create form pre-filled)
  - Delete button (trash icon)
- Bulk actions bar (visible when ≥1 checkbox selected):
  - "Delete Selected" button
  - Count of selected items

### Key UI Components

| Component | Description |
|-----------|-------------|
| `LoginForm` | Username + password fields, error message display |
| `NavTabs` | Locks / Groups / Codes tab switcher |
| `LocksView` | Lock list with battery indicators and status dots |
| `GroupsView` | Group list with inline lock management |
| `CodesView` | Code table with bulk select and actions |
| `CodeFormModal` | Modal for create/edit code (reuses same form) |
| `ConfirmDialog` | Generic confirmation dialog for destructive actions |
| `Toast` | Temporary success/error notifications |

---

## 9. Implementation Notes

### PySchlage Usage

```python
from pyschlage import Auth, Lock

# Authenticate
auth = Auth(username=username, password=password)
auth.authenticate()  # Cognito SRP flow

# List locks
locks = Lock.list(auth=auth)
for lock in locks:
    print(lock.device_id, lock.name, lock.battery_level, lock.is_online)
```

### Access Code Creation

```python
from pyschlage import Lock

lock = Lock(device_id='DRTDXXXXXXX', auth=auth)
lock.access_codes.add(
    name='Dog Walker',
    code='123456',
    is_always_valid=False,
    start_datetime=datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc),
    end_datetime=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
)
```

### Access Code Deletion

```python
lock.access_codes.delete(code_id='CODE-ABC123')
```

### Encryption Helper

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

KEY = open('encryption_key', 'rb').read()  # 32 bytes
NONCE_SIZE = 12

def encrypt(plaintext: str) -> tuple[bytes, bytes]:
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(KEY)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return ciphertext, nonce

def decrypt(ciphertext: bytes, nonce: bytes) -> str:
    aesgcm = AESGCM(KEY)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
```

### Datetime Handling

- All datetimes stored in ISO 8601 format (UTC) in SQLite as TEXT
- Frontend sends datetime as ISO 8601 UTC strings
- Timezone: all times treated as UTC; frontend displays in local time with UTC indicator

### Error Handling

- Schlage API errors: display user-friendly message, log raw error
- Network errors: display "Unable to reach Schlage servers" message
- Validation errors: inline field-level error messages in forms
- All API errors return JSON `{ "detail": "message" }` with appropriate HTTP status code

---

## 10. Acceptance Criteria

| # | Criterion |
|---|-----------|
| 1 | User can log in with Schlage username and password; sees error on bad credentials |
| 2 | Lock list loads within 5 seconds and shows battery % and online status |
| 3 | User can create a named group and it persists across page reloads |
| 4 | User can add any lock to any group (many-to-many; same lock can be in multiple groups) |
| 5 | User can remove a lock from a group |
| 6 | User can delete a group without deleting the locks |
| 7 | User can create an access code for a group with all fields (name, code, always-valid, start, end) |
| 8 | Access code is created on Schlage Cloud for every lock in the target group |
| 9 | Codes view shows all codes from local DB with lock name and group name |
| 10 | User can bulk-delete codes (checkbox select + delete button) |
| 11 | User can overwrite a code: same-name codes on all locks are deleted and recreated with new code value |
| 12 | Overwrite targets the new group (only locks in new group receive the new code) |
| 13 | App is served over HTTPS |
| 14 | Credentials are encrypted at rest in SQLite |
| 15 | No Phase 2 scope creeping — scope is frozen for this spec |

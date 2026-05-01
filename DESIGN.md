# Schlage Lock Manager — Design Spec
**Zac was here. Then he wasn't. Henri wrote this.**

---

## 1. Design Philosophy

**Feel:** Premium smart home security. Not a startup dashboard, not a generic admin panel. Think high-end alarm system meets modern app. Dark, precise, controlled. Every element earns its place.

The aesthetic is: you've got money and taste, your locks areSchlage, your house is smart. This app should feel like it belongs in that world.

---

## 2. Color Palette

```css
/* Backgrounds */
--bg-base: #0a0a0f;           /* Near-black base */
--bg-surface: #14141f;        /* Card/panel surfaces */
--bg-elevated: #1c1c2a;       /* Modals, dropdowns, elevated elements */
--bg-hover: #22223a;          /* Hover states */

/* Borders & Dividers */
--border-subtle: #2a2a3e;     /* Subtle dividers */
--border-default: #3a3a52;    /* Default borders */
--border-focus: #6366f1;       /* Focus ring */

/* Accent — Indigo/Slate, not blue */
--accent-primary: #6366f1;    /* Primary actions, links */
--accent-primary-hover: #818cf8;
--accent-secondary: #4f46e5;

/* Status Colors */
--status-online: #22c55e;     /* Green — lock is online */
--status-offline: #64748b;    /* Slate — lock is offline */
--status-error: #ef4444;      /* Red — something wrong */

/* Battery Colors */
--battery-full: #22c55e;      /* Green — 60-100% */
--battery-medium: #eab308;   /* Yellow — 20-59% */
--battery-low: #ef4444;       /* Red — <20% */

/* Text */
--text-primary: #f8fafc;       /* Headings, primary text */
--text-secondary: #94a3b8;     /* Secondary, labels */
--text-muted: #64748b;        /* Placeholders, disabled */
--text-inverse: #0a0a0f;       /* Text on light backgrounds */

/* Semantic */
--destructive: #dc2626;
--destructive-hover: #ef4444;
--success: #22c55e;
--warning: #eab308;
```

---

## 3. Typography

**Font:** `Inter` (body) + `JetBrains Mono` (code/numbers — access codes, battery %)

```css
/* Type Scale */
--text-xs: 0.75rem;     /* 12px — captions */
--text-sm: 0.875rem;    /* 14px — secondary text */
--text-base: 1rem;      /* 16px — body */
--text-lg: 1.125rem;    /* 18px — labels */
--text-xl: 1.25rem;     /* 20px — section headers */
--text-2xl: 1.5rem;     /* 24px — page titles */
--text-3xl: 2rem;       /* 32px — hero elements */

/* Font Weights */
--font-normal: 400;
--font-medium: 500;
--font-semibold: 600;
--font-bold: 700;
```

---

## 4. Spacing System

```css
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
--space-10: 2.5rem;   /* 40px */
--space-12: 3rem;     /* 48px */
--space-16: 4rem;     /* 64px */
```

---

## 5. Border Radius & Shadows

```css
/* Radius */
--radius-sm: 6px;
--radius-md: 10px;
--radius-lg: 16px;
--radius-xl: 24px;
--radius-full: 9999px;

/* Shadows — layered for depth */
--shadow-card: 0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.6);
--shadow-elevated: 0 4px 6px rgba(0,0,0,0.3), 0 2px 4px rgba(0,0,0,0.4);
--shadow-modal: 0 25px 50px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05);
--shadow-glow: 0 0 20px rgba(99, 102, 241, 0.2); /* Accent glow on focus/active */
```

---

## 6. Page Structure

### Login Page

Full viewport dark background (#0a0a0f). Centered card (max-width 400px), no decorations except a small Schlage-style lock icon at top (simple, geometric, not a logo). Card has subtle border (#2a2a3e) and shadow.

**Elements:**
- Lock icon (Lucide `lock` or custom, 48px, accent color)
- "Schlage Manager" heading — `JetBrains Mono`, bold
- Username field + password field, stacked
- "Sign In" button — full width, accent primary, bold
- Error state: red border on field + error message below

**Interaction:**
- On submit: button shows spinner + "Signing in..." text
- Failed: shake animation on card + error message
- Success: brief fade out, redirect to dashboard

---

### Dashboard Layout

**Top navigation bar** (fixed, 64px tall):
- Left: "Schlage" in JetBrains Mono + small lock icon
- Right: User avatar circle (initials), dropdown on click (Sign Out)

**Sidebar-free design** — tabs across the top below the nav bar:
- Tab 1: "Locks" — lock list
- Tab 2: "Groups" — group management
- Tab 3: "Access Codes" — the main workflow

Active tab: accent underline (3px, accent primary), text-primary. Inactive: text-muted, hover text-secondary.

**Content area:** scrollable, centered max-width 1100px, padding 32px sides.

---

### Lock List Page (Locks Tab)

Page title: "My Locks" (text-2xl, text-primary, font-semibold). Below: subtitle "X locks connected" in text-secondary.

**Lock cards** — grid layout, 2 columns on desktop, 1 on mobile.

Each lock card:
- Dark surface (#14141f), border-subtle, radius-lg, shadow-card
- Left: large lock icon (Lucide `lock-keyhole` or similar, 32px, text-muted)
- Right side:
  - Lock name (text-lg, font-medium, text-primary)
  - Location/zone (text-sm, text-secondary) — if available from Schlage API
  - Status pill: "Online" (green dot + green text) or "Offline" (slate dot + text-muted)
  - Battery meter: horizontal bar, color-coded (green/yellow/red), percentage number in JetBrains Mono
  - Last activity: "Active 2h ago" (text-xs, text-muted)

Card hover: border shifts to border-default, shadow-elevated, slight scale (1.01).

**Empty state:** If no locks found — centered message, lock icon (muted), "No locks found. Check your Schlage account credentials." in text-secondary. Muted icon size 64px.

---

### Groups Page (Groups Tab)

**Two-column layout:**
- Left (40%): Group list
- Right (60%): Group detail / editor

**Left — Group list:**
- "Groups" heading + "New Group" button (accent primary)
- List of group cards: group name, lock count badge, created date
- Click card to select → right panel shows detail

**New Group modal:**
- Centered modal, backdrop blur
- "Create Group" heading
- Text input for group name
- Multi-select dropdown for locks (show all available locks, checkbox each)
- "Create" button (accent primary) + "Cancel" (ghost/secondary)

**Right — Group detail (when a group is selected):**
- Group name as heading (editable on click — pencil icon)
- Lock count
- "Locks in this group" — list with lock name + remove button (X icon, appears on hover)
- "Add Locks" button — opens a dropdown/modal with available locks (not already in group), checkbox select, "Add Selected" button
- "Delete Group" button — destructive, bottom of panel, requires confirmation modal

**Empty state (no group selected):** Right panel shows "Select a group or create a new one" with a subtle icon.

---

### Access Codes Page (Codes Tab) — Main Workflow

**This is the primary page. Most used. It needs to be excellent.**

**Top controls row:**
- Group selector dropdown — label "Group:", accent dropdown style, full width on mobile
- "Create Code" button — accent primary, right-aligned

**Create Code modal** (slide-in from right, not center popup):
- Width: 480px, full height on mobile (slide from bottom)
- Backdrop: semi-transparent dark (#0a0a0f at 80% opacity)
- Header: "Create Access Code" + close X button
- Form fields (all dark input styling):

  1. **Group** — dropdown, required
  2. **Code Name** — text input, required (e.g., "Cleaning Service", "Dog Walker")
  3. **Access Code** — number input, 4-8 digits, required
     - Toggle: "Show code" eye icon to reveal/hide
     - Input styled to look like a PIN pad entry
  4. **Always Valid** — toggle switch, off by default
  5. **If Always Valid OFF** — two datetime-local inputs appear:
     - Start Date/Time
     - End Date/Time
     - Both show date picker + time picker native or custom
  6. **Notification** — toggle (optional, default off)

- Footer buttons: "Create Code" (accent primary, full width) + "Cancel" (ghost, full width)

**Below the create form:**
- Table of existing codes for the selected group (or all codes if no group filter)
- Table columns:
  - Checkbox (header = select all)
  - Name (text-primary)
  - Code (masked as `••••••`, eye toggle to reveal — JetBrains Mono)
  - Schedule (e.g., "Always" or "Dec 25 2024 8am - Dec 26 2024 6pm")
  - Actions (three-dot menu with: Edit, Overwrite, Delete)

- **Bulk action bar** — appears when 1+ checkboxes selected
  - Shows count: "X selected"
  - "Delete Selected" button (destructive)
  - "Clear Selection" button (secondary)

**Overwrite flow:**
- Three-dot menu → "Overwrite" → same modal as Create but pre-filled with existing code name + new code field
- On confirm: delete all codes with that name across all locks, then recreate on selected group's locks

**Delete flow:**
- Click delete → confirmation modal: "Delete this code? This will remove it from X lock(s)." [Cancel] [Delete]

**Table interactions:**
- Row hover: bg-hover
- Checkbox checked: row gets subtle accent tint background
- Select all: header checkbox toggles all in view
- Sticky table header

**Empty state:**
- No codes yet: centered, icon (key icon, muted), "No access codes yet. Create one to get started."
- No group selected: "Select a group to manage access codes" with group icon

---

## 7. Component Specifications

### Button — Primary
- Background: accent-primary
- Text: text-primary, font-medium
- Padding: 12px 24px
- Border-radius: radius-md
- Hover: accent-primary-hover, slight shadow-glow
- Active: scale(0.98)
- Disabled: opacity 50%, cursor not-allowed
- Loading: spinner icon + "Loading..." text

### Button — Secondary
- Background: transparent
- Border: border-default
- Text: text-secondary
- Hover: border-focus, text-primary

### Button — Destructive
- Background: destructive
- Text: white
- Same size/shape as primary

### Button — Ghost
- Background: transparent
- Text: text-muted
- Hover: text-secondary, no background

### Input Field
- Background: bg-elevated
- Border: border-subtle
- Text: text-primary
- Placeholder: text-muted
- Focus: border-focus + shadow-glow (subtle)
- Error: border-destructive + red error text below
- Padding: 12px 16px
- Border-radius: radius-md

### Toggle Switch
- Track: 44px wide, 24px tall, rounded-full
- Off: bg-surface, border-subtle
- On: bg-accent-primary
- Knob: 20px circle, white
- Transition: 200ms ease

### Dropdown / Select
- Same styling as input
- Chevron icon right-aligned
- Dropdown panel: bg-elevated, border-subtle, shadow-elevated, radius-md
- Options: padding 10px 16px, hover bg-hover

### Status Pill
- Inline-flex, align-center, gap 6px
- Padding: 4px 10px
- Border-radius: radius-full
- Font: text-xs, font-medium
- Online: green background (20% opacity), green text, green dot
- Offline: slate background, slate text, slate dot

### Battery Meter
- Horizontal bar, 80px wide, 8px tall, radius-full
- Track: bg-surface
- Fill: color-coded based on level
- Percentage text: JetBrains Mono, text-xs, right of bar

### Modal
- Centered or slide-in from right
- Backdrop: bg-base at 80% opacity, backdrop-filter blur(4px)
- Panel: bg-elevated, border-subtle, shadow-modal, radius-xl
- Max-width: 480px (centered) or 100% width 480px max (slide-in)
- Padding: space-8

### Toast Notification
- Fixed bottom-right
- bg-elevated, border-subtle, shadow-elevated
- Icon left (success = check circle green, error = x circle red)
- Auto-dismiss: 4s
- Slide in from right

### Table
- Full width
- Header: text-xs, text-muted, uppercase, letter-spacing 0.05em, border-bottom border-subtle
- Rows: border-bottom border-subtle, padding 12px 16px
- Row hover: bg-hover
- Checkbox column: 40px, centered
- Actions column: right-aligned, three-dot menu

### Card
- bg-surface, border-subtle, radius-lg, shadow-card
- Padding: space-6
- Hover (if clickable): border-default, shadow-elevated

---

## 8. Icons

**Library:** Lucide Icons (via CDN in HTML, or inline SVGs)

Key icons needed:
- `lock-keyhole` — lock icon (primary lock representation)
- `key` — access codes
- `group` — groups (or `layers`)
- `battery-full`, `battery-50`, `battery-25` — battery indicator (or use custom SVG bar)
- `wifi`, `wifi-off` — online/offline status
- `plus` — add/create actions
- `x` — close, delete
- `chevron-down` — dropdown arrow
- `eye`, `eye-off` — show/hide code toggle
- `edit-2` — edit
- `trash-2` — delete
- `more-vertical` — three-dot menu
- `check` — success states
- `alert-circle` — error states
- `log-out` — sign out

---

## 9. Animations & Transitions

```css
--transition-fast: 150ms ease;
--transition-base: 200ms ease;
--transition-slow: 300ms ease;
```

**Button press:** `transform: scale(0.98)` on active, 100ms
**Card hover:** `transform: scale(1.01)`, 200ms
**Modal appear:** fade in backdrop (200ms) + slide/fade panel (300ms ease-out)
**Toast:** slide in from right, 300ms ease-out
**Tab switch:** underline slides to active tab, 200ms ease
**Toggle:** knob slides, 200ms ease
**Modal close:** reverse of appear, 200ms

---

## 10. Responsive Breakpoints

```css
--breakpoint-sm: 640px;   /* Mobile landscape */
--breakpoint-md: 768px;   /* Tablet */
--breakpoint-lg: 1024px;  /* Desktop */
--breakpoint-xl: 1280px;  /* Large desktop */
```

- Lock cards: 1 column below md, 2 columns above
- Groups page: stack columns below lg
- Access codes table: horizontal scroll on mobile, freeze name column
- Modals: full-screen on mobile (slide from bottom)

---

## 11. Empty States

Each section needs a considered empty state:

- **Locks:** Lock icon (64px, text-muted) + "No locks found. Make sure your Schlage credentials are correct." + "Update Credentials" button if logged in
- **Groups:** Layers icon + "No groups yet. Create one to start organizing your locks."
- **Access codes:** Key icon + "No access codes for this group. Create your first code above."
- **Group detail (no group selected):** Subtle group icon + "Select a group to view and manage its locks."

---

## 12. Loading States

- **Page load:** Skeleton cards with subtle shimmer animation (not spinners)
- **Button loading:** Spinner replaces icon, text changes to "Loading..."
- **Table load:** Skeleton rows (3-5 rows of gray shimmer)
- **Modal open:** Backdrop fades in, panel slides/fades

---

## 13. Error States

- **Form validation:** Inline, red border on field + red text below in text-sm
- **API errors:** Toast notification bottom-right, auto-dismiss 5s, red theme for errors
- **Network error:** "Connection lost. Retrying..." banner at top of content area
- **Lock offline:** Lock card has muted overlay, status shows "Offline" with tooltip "Lock is not responding"

---

## 14. Implementation Notes

**CSS approach:** Plain CSS with CSS custom properties (variables above). No Tailwind. No CSS-in-JS. One main `styles.css` file.

**Font loading:** Google Fonts (Inter + JetBrains Mono) via `<link>` in HTML head.

**Icons:** Lucide via `https://unpkg.com/lucide@latest` or inline SVG. Prefer inline SVG for critical icons (lock, key, battery) to avoid FOIC.

**No framework:** Vanilla HTML/CSS/JS. Keep it simple, keep it fast.

**Accessibility:** All interactive elements keyboard-navigable. Focus states visible. Color not the only indicator (icons + text supplement status colors).

---

*Design spec by Zac — override by Henri. April 20, 2026.*
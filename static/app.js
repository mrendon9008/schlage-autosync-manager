// Schlage Lock Manager — app.js

// SPA frontend for Schlage smart lock management


'use strict';


// ─── STATE ────────────────────────────────────────────────────────────────────

const state = {

  loggedIn: false,

  user: null,

  locks: [],

  groups: [],

  codes: [],

  selectedGroup: null,

  selectedCodes: new Set(),

  activeTab: 'locks',

  syncSchedules: [],

  syncHistory: []

};


// ─── BULK BAR UPDATE (no re-render) ──────────────────────────────────────────
function updateBulkBar() {
  const bar = document.getElementById('bulkBar');
  if (!bar) return;
  const count = state.selectedCodes.size;
  bar.classList.toggle('visible', count > 0);
  const span = bar.querySelector('span');
  if (span) span.textContent = count + ' selected';
}

// ─── API HELPERS ──────────────────────────────────────────────────────────────

function getSessionToken() {
  const match = document.cookie.match(/schlage_session=([^;]+)/);
  return match ? match[1] : null;
}

async function api(path, options = {}) {

  const headers = { 'Content-Type': 'application/json' };

  // Read session token from cookie (not HttpOnly, so JS can access it)
  const token = getSessionToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;


  try {

    const res = await fetch(path, { ...options, headers: { ...headers, ...options.headers } });

    if (!res.ok) {

      const err = await res.json().catch(() => ({ detail: 'Request failed' }));

      throw new Error(err.detail || `HTTP ${res.status}`);

    }

    return await res.json();

  } catch (e) {

    if (options.throw !== false) toast(e.message, 'error');

    throw e;

  }

}


// ─── TOAST ────────────────────────────────────────────────────────────────────

let toastTimer = null;

function toast(message, type = 'info') {

  const existing = document.getElementById('toast');

  if (existing) existing.remove();

  if (toastTimer) clearTimeout(toastTimer);


  const icons = { success: 'check-circle', error: 'alert-circle', info: 'info' };

  const el = document.createElement('div');

  el.id = 'toast';

  el.className = `toast toast-${type}`;

  el.innerHTML = `<i data-lucide="${icons[type] || 'info'}"></i><span>${message}</span>`;

  document.body.appendChild(el);

  lucide.createIcons();

  toastTimer = setTimeout(() => el.remove(), 4000);

}


// ─── INIT ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {

  lucide.createIcons();

  await checkSession();

});


async function checkSession() {

  try {

    const data = await api('/auth/me', { throw: false });

    if (data.authenticated) {

      state.loggedIn = true;

      state.user = data;

      await loadAppData();

      renderDashboard();

    } else {

      renderLogin();

    }

  } catch {

    renderLogin();

  }

}


// ─── LOGIN ────────────────────────────────────────────────────────────────────

function renderLogin() {

  const app = document.getElementById('app');

  app.innerHTML = `

    <div class="login-wrapper">

      <div class="login-card" id="loginCard">

        <i data-lucide="lock-keyhole" class="login-icon" style="width:48px;height:48px;"></i>

        <h1 class="login-title">Schlage Manager</h1>

        <p class="login-subtitle">Sign in with your Schlage account</p>

        <form class="login-form" id="loginForm">

          <div class="form-error" id="loginError"></div>

          <div class="form-group">

            <input type="text" id="username" class="form-input" placeholder="Email" autocomplete="username" required />

          </div>

          <div class="form-group">

            <input type="password" id="password" class="form-input" placeholder="Password" autocomplete="current-password" required />

          </div>

          <button type="submit" class="btn btn-primary btn-full" id="loginBtn">Sign In</button>

        </form>

      </div>

    </div>

  `;

  lucide.createIcons();


  document.getElementById('loginForm').addEventListener('submit', async (e) => {

    e.preventDefault();

    const username = document.getElementById('username').value.trim();

    const password = document.getElementById('password').value;

    const btn = document.getElementById('loginBtn');

    const card = document.getElementById('loginCard');

    const error = document.getElementById('loginError');


    if (!username || !password) { error.textContent = 'Email and password required.'; error.style.display = 'block'; return; }


    btn.disabled = true;

    btn.innerHTML = '<span class="spinner">⏳</span> Signing in...';


    try {

      await api('/auth/login', {

        method: 'POST',

        body: JSON.stringify({ username, password })

      });

      // Sync cookie token to localStorage for API auth
      const token = getSessionToken();
      if (token) localStorage.setItem('schlage_session', token);

      state.loggedIn = true;

      state.user = { username };

      await loadAppData();

      renderDashboard();

    } catch (e) {

      card.classList.add('shake');

      setTimeout(() => card.classList.remove('shake'), 500);

      error.textContent = e.message || 'Invalid credentials';

      error.style.display = 'block';

      btn.disabled = false;

      btn.textContent = 'Sign In';

    }

  });

}


// ─── LOAD APP DATA ────────────────────────────────────────────────────────────

async function loadAppData() {

  try {

    const [locks, groups, codes] = await Promise.all([

      api('/locks').catch(() => ({ locks: [] })),

      api('/groups').catch(() => ({ groups: [] })),

      api('/codes').catch(() => ({ codes: [] }))

    ]);

    state.locks = locks.locks || [];

    state.groups = groups.groups || [];

    state.codes = codes.codes || [];

  } catch (e) {

    // non-fatal

  }

}


// ─── DASHBOARD ────────────────────────────────────────────────────────────────

function renderDashboard() {

  const initials = (state.user?.username || 'U').slice(0, 2).toUpperCase();

  const app = document.getElementById('app');

  app.innerHTML = `

    <nav class="navbar">

      <div class="navbar-brand"><i data-lucide="lock-keyhole" style="width:20px;height:20px;"></i> Schlage</div>

      <button class="hamburger-btn" id="hamburgerBtn" aria-label="Open menu">
        <i data-lucide="menu" style="width:22px;height:22px;"></i>
      </button>

      <div class="navbar-overlay" id="navbarOverlay"></div>

      <div class="navbar-drawer" id="navbarDrawer">
        <div class="drawer-header">
          <span class="drawer-title">Menu</span>
          <button class="drawer-close" id="drawerClose" aria-label="Close menu">
            <i data-lucide="x" style="width:22px;height:22px;"></i>
          </button>
        </div>
        <div class="drawer-tabs">
          <div class="tab ${state.activeTab === 'locks' ? 'active' : ''}" data-tab="locks" data-drawer-close>
            <i data-lucide="lock-keyhole" style="width:16px;height:16px;"></i> Locks
          </div>
          <div class="tab ${state.activeTab === 'groups' ? 'active' : ''}" data-tab="groups" data-drawer-close>
            <i data-lucide="layers" style="width:16px;height:16px;"></i> Groups
          </div>
          <div class="tab ${state.activeTab === 'codes' ? 'active' : ''}" data-tab="codes" data-drawer-close>
            <i data-lucide="key" style="width:16px;height:16px;"></i> Access Codes
          </div>
          <div class="tab ${state.activeTab === 'sync' ? 'active' : ''}" data-tab="sync" data-drawer-close>
            <i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Sync
          </div>
        </div>
        <div class="drawer-footer">
          <div class="navbar-avatar" id="userAvatarDrawer">${initials}</div>
          <span class="drawer-username">${state.user?.username || 'User'}</span>
          <button class="user-dropdown-item danger" id="logoutBtnDrawer">
            <i data-lucide="log-out" style="width:16px;height:16px;"></i> Sign Out
          </button>
        </div>
      </div>

      <div class="navbar-user">

        <div class="navbar-avatar" id="userAvatar">${initials}</div>

        <div class="user-dropdown" id="userDropdown">

          <button class="user-dropdown-item danger" id="logoutBtn">

            <i data-lucide="log-out" style="width:16px;height:16px;"></i> Sign Out

          </button>

        </div>

      </div>

    </nav>

    <div class="main-content main-content-desktop">

      <div class="tabs">

        <div class="tab ${state.activeTab === 'locks' ? 'active' : ''}" data-tab="locks">

          <i data-lucide="lock-keyhole" style="width:16px;height:16px;"></i> Locks

        </div>

        <div class="tab ${state.activeTab === 'groups' ? 'active' : ''}" data-tab="groups">

          <i data-lucide="layers" style="width:16px;height:16px;"></i> Groups

        </div>

        <div class="tab ${state.activeTab === 'codes' ? 'active' : ''}" data-tab="codes">

          <i data-lucide="key" style="width:16px;height:16px;"></i> Access Codes

        </div>

        <div class="tab ${state.activeTab === 'sync' ? 'active' : ''}" data-tab="sync">

          <i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Sync

        </div>

      </div>

      <div class="page-container" id="tabContent"></div>

    </div>

  `;

  lucide.createIcons();


  document.getElementById('userAvatar').addEventListener('click', () => {

    document.getElementById('userDropdown').classList.toggle('open');

  });

  document.addEventListener('click', (e) => {

    if (!e.target.closest('.navbar-user')) document.getElementById('userDropdown')?.classList.remove('open');

  });

  // ── Hamburger menu / drawer ──────────────────────────────────────────

  const drawer = document.getElementById('navbarDrawer');
  const overlay = document.getElementById('navbarOverlay');
  const hamburgerBtn = document.getElementById('hamburgerBtn');
  const drawerClose = document.getElementById('drawerClose');

  function openDrawer() {
    drawer?.classList.add('open');
    overlay?.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeDrawer() {
    drawer?.classList.remove('open');
    overlay?.classList.remove('open');
    document.body.style.overflow = '';
  }

  hamburgerBtn?.addEventListener('click', openDrawer);
  overlay?.addEventListener('click', closeDrawer);
  drawerClose?.addEventListener('click', closeDrawer);

  // Close drawer on tab click
  document.querySelectorAll('[data-drawer-close]').forEach(tab => {
    tab.addEventListener('click', () => {
      state.activeTab = tab.dataset.tab;
      state.selectedCodes.clear();
      closeDrawer();
      renderDashboard();
    });
  });

  // Sign out from drawer
  document.getElementById('logoutBtnDrawer')?.addEventListener('click', logout);

  // ── Desktop tab click ────────────────────────────────────────────────

  document.querySelectorAll('.tab').forEach(tab => {

    tab.addEventListener('click', () => {

      state.activeTab = tab.dataset.tab;

      state.selectedCodes.clear();

      renderDashboard();

    });

  });

  renderTabContent();

}


// ─── TAB CONTENT ──────────────────────────────────────────────────────────────

function renderTabContent() {

  const container = document.getElementById('tabContent');

  if (state.activeTab === 'locks') renderLocksPage(container);

  else if (state.activeTab === 'groups') renderGroupsPage(container);

  else if (state.activeTab === 'codes') renderCodesPage(container);

  else if (state.activeTab === 'sync') renderSyncPage(container);

}


// ─── LOCKS PAGE ───────────────────────────────────────────────────────────────

function renderLocksPage(container) {

  const lockCards = state.locks.length ? state.locks.map(lock => {

    const battery = lock.battery_level || 0;

    const battClass = battery > 60 ? 'full' : battery > 20 ? 'medium' : 'low';

    const online = lock.is_online;

    return `

      <div class="lock-card ${online ? '' : 'offline'}">

        <i data-lucide="lock-keyhole" class="lock-icon" style="width:32px;height:32px;"></i>

        <div class="lock-info">

          <div class="lock-name">${escHtml(lock.name || 'Unknown Lock')}</div>

          ${lock.model ? `<div class="lock-meta">${escHtml(lock.model)}</div>` : ''}

          <div class="lock-footer">

            <span class="status-pill ${online ? 'online' : 'offline'}">

              <span class="status-dot"></span>${online ? 'Online' : 'Offline'}

            </span>

            <div class="battery-meter">

              <div class="battery-track">

                <div class="battery-fill ${battClass}" style="width:${battery}%"></div>

              </div>

              <span class="battery-pct">${battery}%</span>

            </div>

          </div>

        </div>

      </div>`;

  }).join('') : `<div class="empty-state">

    <i data-lucide="lock-keyhole" style="width:64px;height:64px;"></i>

    <p>No locks found. Check your Schlage credentials.</p>

  </div>`;


  container.innerHTML = `

    <div class="page-header">

      <div>

        <h1 class="page-title">My Locks</h1>

        <p class="page-subtitle">${state.locks.length} lock${state.locks.length !== 1 ? 's' : ''} connected</p>

      </div>

      <button class="btn btn-secondary" id="refreshLocks">

        <i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Refresh

      </button>

    </div>

    <div class="lock-grid">${lockCards}</div>

  `;

  lucide.createIcons();

  document.getElementById('refreshLocks')?.addEventListener('click', async () => {

    const btn = document.getElementById('refreshLocks');

    btn.disabled = true;

    try {

      const data = await api('/locks');

      state.locks = data.locks || [];

      renderTabContent();

      toast('Locks refreshed', 'success');

    } catch { /* toast already */ }

    btn.disabled = false;

  });

}


// ─── GROUPS PAGE ──────────────────────────────────────────────────────────────

function renderGroupsPage(container) {

  const groupsHtml = state.groups.map(g => `

    <div class="group-card ${state.selectedGroup?.id === g.id ? 'selected' : ''}" data-id="${g.id}">

      <div class="group-card-info">

        <div class="group-card-name">${escHtml(g.name)}</div>

        <div class="group-card-meta">${g.locks?.length || 0} lock${(g.locks?.length || 0) !== 1 ? 's' : ''}</div>

      </div>

      <button class="btn btn-ghost btn-icon group-delete-btn" data-id="${g.id}" title="Delete group">

        <i data-lucide="trash-2" style="width:16px;height:16px;"></i>

      </button>

    </div>`).join('') || `<p class="text-muted" style="padding:16px;">No groups yet.</p>`;


  const selected = state.selectedGroup;

  const detailHtml = selected ? `

    <div class="group-detail-header">

      <h2 class="page-title">${escHtml(selected.name)}</h2>

    </div>

    <div class="group-locks-list">

      <div class="group-locks-label">Locks in this group</div>

      ${(selected.locks || []).map(l => `
        <div class="group-lock-item">
          <i data-lucide="lock-keyhole" style="width:16px;height:16px;color:var(--text-muted);"></i>
          <span>${escHtml(l.lock_name || l.lock_id)}${l.is_master ? ' <span class="parent-badge">Parent</span>' : ''}</span>
          ${!l.is_master ? `<button class="btn btn-ghost set-parent-btn" data-lock="${l.lock_id}" title="Set as Parent" style="font-size:11px;padding:2px 8px;">Set as Parent</button>` : ''}
          <button class="btn btn-ghost btn-icon remove-lock-btn" data-lock="${l.lock_id}" title="Remove">
            <i data-lucide="x" style="width:14px;height:14px;"></i>
          </button>
        </div>`).join('') || '<p class="text-muted">No locks in this group.</p>'}

      <button class="btn btn-secondary" id="addLocksBtn" style="margin-top:12px;">

        <i data-lucide="plus" style="width:16px;height:16px;"></i> Add Locks

      </button>

    </div>` : `

    <div style="padding:32px;text-align:center;color:var(--text-muted);">

      <i data-lucide="layers" style="width:48px;height:48px;margin-bottom:12px;"></i>

      <p>Select a group or create a new one</p>

    </div>`;


  container.innerHTML = `

    <div class="page-header">

      <h1 class="page-title">Groups</h1>

      <button class="btn btn-primary" id="newGroupBtn">

        <i data-lucide="plus" style="width:16px;height:16px;"></i> New Group

      </button>

    </div>

    <div class="groups-layout">

      <div class="groups-list">${groupsHtml}</div>

      <div class="group-detail">${detailHtml}</div>

    </div>`;

  lucide.createIcons();


  document.getElementById('newGroupBtn')?.addEventListener('click', () => showNewGroupModal());

  document.querySelectorAll('.group-card').forEach(card => {

    card.addEventListener('click', (e) => {

      if (e.target.closest('.group-delete-btn')) return;

      const id = parseInt(card.dataset.id);

      const group = state.groups.find(g => g.id === id);

      state.selectedGroup = group;

      renderTabContent();

    });

  });

  document.querySelectorAll('.group-delete-btn').forEach(btn => {

    btn.addEventListener('click', async (e) => {

      e.stopPropagation();

      if (!confirm('Delete this group?')) return;

      try {

        await api(`/groups/${btn.dataset.id}`, { method: 'DELETE' });

        const idx = state.groups.findIndex(g => g.id === parseInt(btn.dataset.id));

        if (idx > -1) state.groups.splice(idx, 1);

        if (state.selectedGroup?.id === parseInt(btn.dataset.id)) state.selectedGroup = null;

        toast('Group deleted', 'success');

        renderTabContent();

      } catch { /* */ }

    });

  });

  document.querySelectorAll('.remove-lock-btn').forEach(btn => {

    btn.addEventListener('click', async () => {

      if (!state.selectedGroup) return;

      try {

        await api(`/groups/${state.selectedGroup.id}/locks/${btn.dataset.lock}`, { method: 'DELETE' });

        state.selectedGroup.locks = (state.selectedGroup.locks || []).filter(l => l.lock_id !== btn.dataset.lock);

        toast('Lock removed', 'success');

        renderTabContent();

      } catch { /* */ }

    });

  });


  document.querySelectorAll('.set-parent-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!state.selectedGroup) return;
      const lockId = btn.dataset.lock;
      try {
        await api(`/groups/${state.selectedGroup.id}/master-lock`, {
          method: 'PUT',
          body: JSON.stringify({ lock_id: lockId }),
        });
        // Update is_master flag in local state
        state.selectedGroup.locks = (state.selectedGroup.locks || []).map(l => ({
          ...l,
          is_master: l.lock_id === lockId ? 1 : 0,
        }));
        toast('Parent updated', 'success');
        renderTabContent();
      } catch { /* */ }
    });
  });

  document.getElementById('addLocksBtn')?.addEventListener('click', () => showAddLocksModal());

}


function showNewGroupModal() {

  const modal = document.createElement('div');

  modal.className = 'modal-backdrop';

  modal.innerHTML = `

    <div class="modal-panel">

      <div class="modal-header">

        <h3>Create Group</h3>

        <button class="btn btn-ghost btn-icon modal-close"><i data-lucide="x" style="width:18px;height:18px;"></i></button>

      </div>

      <div class="modal-body">

        <div class="form-group">

          <label class="form-label">Group Name</label>

          <input type="text" class="form-input" id="newGroupName" placeholder="e.g. Family, Airbnb, Cleaning" />

        </div>

      </div>

      <div class="modal-footer">

        <button class="btn btn-primary btn-full" id="createGroupBtn">Create Group</button>

      </div>

    </div>`;

  document.body.appendChild(modal);

  lucide.createIcons();

  modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());

  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

  document.getElementById('createGroupBtn').addEventListener('click', async () => {

    const name = document.getElementById('newGroupName').value.trim();

    if (!name) { toast('Group name required', 'error'); return; }

    try {

      const data = await api('/groups', { method: 'POST', body: JSON.stringify({ name }) });

      state.groups.push({ ...data, locks: [] });

      toast('Group created', 'success');

      modal.remove();

      renderTabContent();

    } catch { /* */ }

  });

}


async function showAddLocksModal() {

  if (!state.selectedGroup) return;

  // Re-fetch locks from Schlage to get the real-time list (filters out deleted locks)
  let currentLocks = state.locks;
  try {
    const locks = await api('/locks');
    currentLocks = locks.locks || [];
    state.locks = currentLocks;
  } catch(e) {
    // Use cached if refresh fails
  }

  const inGroup = new Set((state.selectedGroup.locks || []).map(l => l.lock_id));

  const available = currentLocks.filter(l => !inGroup.has(l.device_id));

  if (!available.length) { toast('All locks already in this group', 'info'); return; }


  const modal = document.createElement('div');

  modal.className = 'modal-backdrop';

  modal.innerHTML = `

    <div class="modal-panel">

      <div class="modal-header">

        <h3>Add Locks to ${escHtml(state.selectedGroup.name)}</h3>

        <button class="btn btn-ghost btn-icon modal-close"><i data-lucide="x" style="width:18px;height:18px;"></i></button>

      </div>

      <div class="modal-body">

        ${available.map(l => `

          <label class="lock-checkbox-item">

            <input type="checkbox" class="add-lock-cb" value="${l.device_id}" data-name="${escHtml(l.name)}" />

            <span>${escHtml(l.name)}</span>

          </label>`).join('')}

      </div>

      <div class="modal-footer">

        <button class="btn btn-primary btn-full" id="addSelectedLocksBtn">Add Selected</button>

      </div>

    </div>`;

  document.body.appendChild(modal);

  lucide.createIcons();

  modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());

  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

  document.getElementById('addSelectedLocksBtn').addEventListener('click', async () => {

    const cbs = modal.querySelectorAll('.add-lock-cb:checked');

    if (!cbs.length) { toast('Select at least one lock', 'error'); return; }

    const lockIds = Array.from(cbs).map(cb => cb.value);

    const lockNames = Array.from(cbs).map(cb => cb.dataset.name);

    try {

      await api(`/groups/${state.selectedGroup.id}/locks`, {

        method: 'POST',

        body: JSON.stringify({ lock_ids: lockIds, lock_names: lockNames })

      });

      toast('Locks added', 'success');

      const [selGroup, allGroups] = await Promise.all([

        api(`/groups/${state.selectedGroup.id}`),

        api('/groups')

      ]);

      state.selectedGroup = selGroup;

      state.groups = allGroups.groups || [];

      modal.remove();

      renderTabContent();

    } catch { /* */ }

  });

}


// ─── CODES PAGE ───────────────────────────────────────────────────────────────

function renderCodesPage(container) {
  const codes = state.codes;
  const groups = state.groups;

  // Group codes by lock
  const lockMap = {};
  codes.forEach(c => {
    const lockId = c.schlage_lock_id;
    if (!lockMap[lockId]) lockMap[lockId] = [];
    lockMap[lockId].push(c);
  });

  // Ordered lock list: state.locks first (if they have codes), then others
  const orderedLockIds = [];
  const seen = new Set();
  state.locks.forEach(l => {
    if (lockMap[l.device_id]) {
      orderedLockIds.push({ lockId: l.device_id, lockName: l.name });
      seen.add(l.device_id);
    }
  });
  Object.keys(lockMap).forEach(lockId => {
    if (!seen.has(lockId)) {
      const firstCode = lockMap[lockId][0];
      orderedLockIds.push({ lockId, lockName: firstCode.lock_name || lockId });
    }
  });

  function codeRow(c) {
    const schedule = c.is_always_valid ? 'Always' : `${fmtDate(c.start_datetime)} – ${fmtDate(c.end_datetime)}`;
    return `<tr class="code-row" data-id="${c.id}">
      <td><input type="checkbox" class="code-cb" value="${c.id > 0 ? c.id : ''}" ${c.id > 0 && state.selectedCodes.has(c.id) ? 'checked' : ''} ${c.id <= 0 ? 'disabled' : ''} /></td>
      <td><span class="code-name">${escHtml(c.name)}</span></td>
      <td>
        <span class="code-value" data-code="${escHtml(c.code_value)}">••••••</span>
        <button class="btn btn-ghost btn-icon toggle-code-btn" data-code="${escHtml(c.code_value)}">
          <i data-lucide="eye" style="width:14px;height:14px;"></i>
        </button>
      </td>
      <td><span class="code-schedule">${schedule}</span></td>
      <td>
        <div class="code-actions">
          <button class="btn btn-ghost btn-icon code-menu-btn" data-id="${c.id}">
            <i data-lucide="more-vertical" style="width:16px;height:16px;"></i>
          </button>
          <div class="code-menu-dropdown" id="menu-${c.id}" style="display:none;">
            <button class="code-menu-item overwrite-btn" data-id="${c.id}" data-name="${escHtml(c.name)}">
              <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> Overwrite
            </button>
            <button class="code-menu-item delete-code-btn danger" data-id="${c.id}">
              <i data-lucide="trash-2" style="width:14px;height:14px;"></i> Delete
            </button>
          </div>
        </div>
      </td>
    </tr>`;
  }

  if (codes.length === 0) {
    container.innerHTML = `
      <div class="page-header">
        <div>
          <h1 class="page-title">Access Codes</h1>
          <p class="page-subtitle">0 codes</p>
        </div>
        <div class="page-header-right">
          <button class="btn btn-secondary" id="refreshCodesBtn"><i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Refresh</button>
          <button class="btn btn-primary" id="createCodeBtn">
            <i data-lucide="plus" style="width:16px;height:16px;"></i> Create Code
          </button>
        </div>
      </div>
      <div class="codes-empty-state">
        <i data-lucide="key" style="width:32px;height:32px;"></i>
        <p>No access codes yet. Create one to get started.</p>
      </div>`;
    lucide.createIcons();
    document.getElementById('createCodeBtn').addEventListener('click', () => showCodeModal());
  document.getElementById('refreshCodesBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('refreshCodesBtn');
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader" style="width:16px;height:16px;animation:spin;"></i> Syncing...';
    lucide.createIcons();
    try {
      const [codesRes, locksRes] = await Promise.all([api('/codes'), api('/locks')]);
      state.codes = codesRes.codes || [];
      state.locks = locksRes.locks || [];
      state.selectedCodes.clear();
      renderCodesPage(container, state.codes, state.groups);
    } catch {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Refresh';
      lucide.createIcons();
    }
  });
    return;
  }

  const lockGroupsHtml = orderedLockIds.map(({ lockId, lockName }) => {
    const lockCodes = lockMap[lockId] || [];
    const lockSelected = lockCodes.filter(c => state.selectedCodes.has(c.id)).length;
    const lockAllSelected = lockCodes.length > 0 && lockSelected === lockCodes.length;
    return `
      <div class="lock-group" data-lock-id="${lockId}">
        <div class="lock-group-header">
          <div class="lock-group-header-left">
            <span class="lock-group-name">${escHtml(lockName)}</span>
          </div>
          <span class="lock-group-count">${lockCodes.length} code${lockCodes.length !== 1 ? 's' : ''}</span>
        </div>
        <div class="lock-group-body">
          <table class="codes-table codes-table-grouped">
            <tbody>
              ${lockCodes.map(c => codeRow(c)).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  }).join('');

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">Access Codes</h1>
        <p class="page-subtitle">${codes.length} code${codes.length !== 1 ? 's' : ''}</p>
      </div>
      <div class="page-header-right">
        <button class="btn btn-secondary" id="refreshCodesBtn"><i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Refresh</button>
        <button class="btn btn-primary" id="createCodeBtn">
          <i data-lucide="plus" style="width:16px;height:16px;"></i> Create Code
        </button>
      </div>
    </div>

    <div class="bulk-action-bar ${state.selectedCodes.size ? 'visible' : ''}" id="bulkBar">
      <span>${state.selectedCodes.size} selected</span>
      <button class="btn btn-destructive btn-sm" id="bulkDeleteBtn">Delete Selected</button>
      <button class="btn btn-ghost btn-sm" id="clearSelectionBtn">Clear</button>
    </div>

    <div class="codes-table-wrapper">
      ${lockGroupsHtml}
    </div>`;
  lucide.createIcons();
  container.querySelectorAll('.code-cb').forEach(cb => { cb.checked = state.selectedCodes.has(parseInt(cb.value)); });

  container.addEventListener('change', (e) => {    if (!e.target.classList.contains('code-cb')) return;    if (e.target.checked) state.selectedCodes.add(parseInt(e.target.value));    else state.selectedCodes.delete(parseInt(e.target.value));    updateBulkBar();  });

  // Toggle code reveal
  document.querySelectorAll('.toggle-code-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const span = btn.previousElementSibling;
      span.textContent = span.textContent === '••••••' ? btn.dataset.code : '••••••';
    });
  });

  // Code menu toggle
  document.querySelectorAll('.code-menu-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.code-menu-dropdown').forEach(m => m.style.display = 'none');
      const menu = document.getElementById(`menu-${btn.dataset.id}`);
      menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    });
  });

  // Overwrite code
  document.querySelectorAll('.overwrite-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const codeId = parseInt(btn.dataset.id);
      const code = state.codes.find(c => c.id === codeId);
      if (code) showCodeModal(code, code.name);
    });
  });

  // Delete single
  document.querySelectorAll('.delete-code-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this access code?')) return;
      try {
        const codeId = parseInt(btn.dataset.id);
        await api(`/codes/${codeId}`, { method: 'DELETE' });
        state.codes = state.codes.filter(c => c.id !== codeId);
        state.selectedCodes.delete(codeId);
        toast('Code deleted', 'success');
        renderTabContent();
      } catch (e) { toast(e.message || 'Delete failed', 'error'); }
    });
  });

  // Bulk delete
  document.getElementById('bulkDeleteBtn')?.addEventListener('click', async () => {
    const ids = Array.from(state.selectedCodes);
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} code(s)?`)) return;
    try {
      await api('/codes/delete-batch', { method: 'POST', body: JSON.stringify({ ids }) });
      state.codes = state.codes.filter(c => !state.selectedCodes.has(c.id));
      state.selectedCodes.clear();
      toast('Codes deleted', 'success');
      renderTabContent();
    } catch { /* */ }
  });

  document.getElementById('clearSelectionBtn')?.addEventListener('click', () => {
    state.selectedCodes.clear();
    renderTabContent();
  });

  document.getElementById('createCodeBtn').addEventListener('click', () => showCodeModal());
  document.getElementById('refreshCodesBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('refreshCodesBtn');
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader" style="width:16px;height:16px;animation:spin;"></i> Syncing...';
    lucide.createIcons();
    try {
      const [codesRes, locksRes] = await Promise.all([api('/codes'), api('/locks')]);
      state.codes = codesRes.codes || [];
      state.locks = locksRes.locks || [];
      state.selectedCodes.clear();
      renderCodesPage(container, state.codes, state.groups);
    } catch {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Refresh';
      lucide.createIcons();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.code-actions')) {
      document.querySelectorAll('.code-menu-dropdown').forEach(m => m.style.display = 'none');
    }
  });
}


// ─── CODE MODAL (slide-in from right) ────────────────────────────────────────

function showCodeModal(existingCode = null) {

  const groups = state.groups;

  const groupOptions = groups.map(g => `<option value="${g.id}">${escHtml(g.name)}</option>`).join('');


  const modal = document.createElement('div');

  modal.className = 'slide-modal-backdrop';

  modal.innerHTML = `

    <div class="slide-modal-panel">

      <div class="slide-modal-header">

        <h3>${existingCode ? 'Overwrite Access Code' : 'Create Access Code'}</h3>

        <button class="btn btn-ghost btn-icon slide-modal-close"><i data-lucide="x" style="width:20px;height:20px;"></i></button>

      </div>

      <div class="slide-modal-body">

        <div class="form-group">

          <label class="form-label">Group</label>

          <select class="form-select" id="codeGroup">${groupOptions}</select>

        </div>

        <div class="form-group">

          <label class="form-label">Code Name</label>

          <input type="text" class="form-input" id="codeName" placeholder="e.g. Dog Walker, Cleaning Service" value="${existingCode ? escHtml(existingCode.name) : ''}" />

        </div>

        <div class="form-group">

          <label class="form-label">Access Code (4–8 digits)</label>

          <div class="code-input-wrapper">

            <input type="password" class="form-input" id="codeValue" placeholder="000000" maxlength="8" pattern="[0-9]*" inputmode="numeric" value="" />

            <button class="btn btn-ghost btn-icon toggle-code-val" type="button">

              <i data-lucide="eye" style="width:16px;height:16px;"></i>

            </button>

          </div>

        </div>

        <div class="form-group">

          <label class="toggle-wrapper" id="alwaysValidToggle">

            <div class="toggle" id="alwaysValidToggleTrack"></div>

            <span class="toggle-label">Always Valid</span>

          </label>

        </div>

        <div class="datetime-fields" id="datetimeFields" style="display:none;">

          <div class="form-group">

            <label class="form-label">Start Date/Time</label>

            <input type="datetime-local" class="form-input" id="codeStart" />

          </div>

          <div class="form-group">

            <label class="form-label">End Date/Time</label>

            <input type="datetime-local" class="form-input" id="codeEnd" />

          </div>

        </div>

      </div>

      <div class="slide-modal-footer">

        <button class="btn btn-primary btn-full" id="submitCodeBtn">

          ${existingCode ? 'Overwrite Code' : 'Create Code'}

        </button>

        <button class="btn btn-ghost btn-full" id="cancelCodeBtn">Cancel</button>

      </div>

    </div>`;

  document.body.appendChild(modal);

  lucide.createIcons();


  let alwaysValid = existingCode ? existingCode.is_always_valid : true;

  updateToggleState(modal, alwaysValid);


  modal.querySelector('#alwaysValidToggle').addEventListener('click', () => {

    alwaysValid = !alwaysValid;

    updateToggleState(modal, alwaysValid);

  });


  modal.querySelector('.toggle-code-val').addEventListener('click', () => {

    const input = modal.querySelector('#codeValue');

    input.type = input.type === 'password' ? 'text' : 'password';

  });


  modal.querySelector('.slide-modal-close').addEventListener('click', () => modal.remove());

  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });


  document.getElementById('cancelCodeBtn').addEventListener('click', () => modal.remove());


  document.getElementById('submitCodeBtn').addEventListener('click', async () => {

    const group_id = parseInt(document.getElementById('codeGroup').value);

    const name = document.getElementById('codeName').value.trim();

    const code_value = document.getElementById('codeValue').value.trim();

    const start_time = document.getElementById('codeStart').value;

    const end_time = document.getElementById('codeEnd').value;


    if (!group_id || !name || !code_value) { toast('Group, name, and code are required', 'error'); return; }

    if (!/^\d{4,8}$/.test(code_value)) { toast('Code must be 4–8 digits', 'error'); return; }

    if (!alwaysValid && (!start_time || !end_time)) { toast('Start and end time required', 'error'); return; }


    try {

      const payload = { group_id, name, code_value, always_valid: alwaysValid };

      if (!alwaysValid) {

        payload.start_time = new Date(start_time).toISOString();

        payload.end_time = new Date(end_time).toISOString();

      }


      if (existingCode) {

        await api(`/codes/${existingCode.id}`, {

          method: 'PUT',

          body: JSON.stringify(payload)

        });

      } else {

        await api('/codes', { method: 'POST', body: JSON.stringify(payload) });

      }


      toast(existingCode ? 'Code overwritten' : 'Code created', 'success');

      modal.remove();

      await loadAppData();

      renderTabContent();

    } catch { /* */ }

  });

}


function updateToggleState(modal, on) {

  const track = modal.querySelector('#alwaysValidToggleTrack');

  const fields = modal.querySelector('#datetimeFields');

  if (on) track.classList.add('on');

  else track.classList.remove('on');

  fields.style.display = on ? 'none' : 'flex';

  fields.style.flexDirection = 'column';

  fields.style.gap = '12px';

}


// ─── LOGOUT ───────────────────────────────────────────────────────────────────

async function logout() {

  try { await api('/auth/logout', { method: 'POST', throw: false }); } catch { /* */ }

  localStorage.removeItem('schlage_session');

  state.loggedIn = false;

  state.user = null;

  state.locks = [];

  state.groups = [];

  state.codes = [];

  state.selectedGroup = null;

  state.selectedCodes.clear();

  renderLogin();

}


// ─── HELPERS ──────────────────────────────────────────────────────────────────

function escHtml(str) {

  if (!str) return '';

  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

}


function fmtDate(iso) {

  if (!iso) return '';

  try {

    const d = new Date(iso);

    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });

  } catch { return iso; }

}

function fmtTime12(t) {
  try {
    const [h, m] = t.split(':').map(Number);
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 || 12;
    return `${h12}:${m.toString().padStart(2, '0')} ${ampm}`;
  } catch { return t; }
}


// ─── CSS INJECTED FOR SLIDE MODAL ────────────────────────────────────────────

// ─── SYNC PAGE ───────────────────────────────────────────────────────────────

async function renderSyncPage(container) {

  container.innerHTML = '<div class="page-loading"><p>Loading sync...</p></div>';

  let groups = state.groups;

  let schedules = [];

  let allPending = {};

  let recentSyncs = {};


  try {

    if (!groups || !groups.length) {

      const gData = await Promise.race([

        api('/groups'),

        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000))

      ]);

      groups = gData.groups || [];

      state.groups = groups;

    }

    if (!groups || !groups.length) {

      container.innerHTML = '<div class="empty-state"><p>No groups found. Create a group first.</p></div>';

      return;

    }

    const sData = await api('/sync/schedules');

    schedules = sData.schedules || [];

    state.syncSchedules = schedules;


    const promises = groups.map(async (g) => {

      try {

        const [jobsData, historyData] = await Promise.all([

          api(`/sync/jobs/${g.id}`),

          api(`/sync/history/${g.id}?limit=3`),

        ]);

        allPending[g.id] = jobsData.jobs || [];

        recentSyncs[g.id] = historyData.runs || [];

      } catch (e) {

        allPending[g.id] = [];

        recentSyncs[g.id] = [];

      }

    });

    const timeoutMs = 12000;

    let timedOut = false;

    const timeoutPromise = new Promise(r => setTimeout(() => { timedOut = true; r(); }, timeoutMs));

    await Promise.race([Promise.all(promises), timeoutPromise]);

    state.syncPending = allPending;

    renderSyncContent(container, groups, schedules, allPending, recentSyncs);

    if (timedOut) {

      Promise.all(promises).then(() => {

        state.syncPending = allPending;

        renderSyncContent(container, groups, schedules, allPending, recentSyncs);

      });

    }

  } catch (e) {

    container.innerHTML = `<div class="empty-state"><p>Failed to load sync data: ${escHtml(e.message)}</p></div>`;

    return;

  }

}


function renderSyncContent(container, groups, schedules, allPending, recentSyncs) {

  const groupsHtml = (groups || []).map((g) => {

    const sched = schedules.find((s) => s.group_id === g.id);

    let times = [];

    try { times = Array.isArray(sched?.check_times) ? sched.check_times : JSON.parse(sched?.check_times || '[]'); } catch { times = []; }

    const masterLockId = sched?.master_lock_id || g.locks?.find(l => l.is_master === 1)?.lock_id || null;

    const masterLock = g.locks?.find((l) => l.lock_id === masterLockId);

    const masterName = masterLock ? masterLock.lock_name : 'Not set';

    const pending = allPending[g.id] || [];

    const toCreate = pending.filter(item => item.action === 'create' && item.state === 'pending');

    const toUpdate = pending.filter(item => item.action === 'update' && item.state === 'pending');

    const toDelete = pending.filter(item => item.action === 'delete' && item.state === 'pending');

    const totalPending = toCreate.length + toUpdate.length + toDelete.length;


    let pendingHtml = '';

    if (totalPending === 0) {

      pendingHtml = '<div class="sync-pending-empty">No pending changes — all locks are in sync</div>';

    } else {

      if (toCreate.length) {

        pendingHtml += `<div class="sync-pending-section"><div class="sync-pending-header"><i data-lucide="plus-circle" style="width:14px;height:14px;color:var(--success);"></i> To Create (${toCreate.length})</div>`;

        toCreate.forEach((item) => {

          const tName = item.target_lock_name || '?';
          const icon = '<i data-lucide="plus-circle" style="width:14px;height:14px;color:var(--success);display:inline-flex;"></i>';

          pendingHtml += `<div class="sync-pending-item"><label class="sync-pending-label"><input type="checkbox" class="sync-pending-cb" data-group="${g.id}" data-action="create" data-job-id="${item.id}" data-code="${escHtml(item.code_name)}" data-target="${item.target_lock_id}" checked /><span><strong>${escHtml(item.code_name)}</strong></span><span class="code-value">${escHtml(item.code_value || '')}</span><span class="sync-pending-target">→ ${escHtml(tName)}</span>${icon}</label></div>`;

        });

        pendingHtml += '</div>';

      }

      if (toUpdate.length) {

        pendingHtml += `<div class="sync-pending-section"><div class="sync-pending-header"><i data-lucide="edit-2" style="width:14px;height:14px;color:var(--status-warning);"></i> To Update (${toUpdate.length})</div>`;

        toUpdate.forEach((item) => {

          const tName = item.target_lock_name || '?';
          const icon = '<i data-lucide="edit-2" style="width:14px;height:14px;color:var(--status-warning);display:inline-flex;"></i>';

          pendingHtml += `<div class="sync-pending-item"><label class="sync-pending-label"><input type="checkbox" class="sync-pending-cb" data-group="${g.id}" data-action="update" data-job-id="${item.id}" data-code="${escHtml(item.code_name)}" data-target="${item.target_lock_id}" checked /><span><strong>${escHtml(item.code_name)}</strong></span><span class="code-value">${escHtml(item.code_value || '')}</span><span class="sync-pending-target">→ ${escHtml(tName)}</span>${icon}</label></div>`;

        });

        pendingHtml += '</div>';

      }

      if (toDelete.length) {

        pendingHtml += `<div class="sync-pending-section"><div class="sync-pending-header"><i data-lucide="trash-2" style="width:14px;height:14px;color:var(--status-error);"></i> To Delete (${toDelete.length})</div>`;

        toDelete.forEach((item) => {

          const tName = item.target_lock_name || '?';
          const icon = '<i data-lucide="trash-2" style="width:14px;height:14px;color:var(--status-error);display:inline-flex;"></i>';

          pendingHtml += `<div class="sync-pending-item"><label class="sync-pending-label"><input type="checkbox" class="sync-pending-cb" data-group="${g.id}" data-action="delete" data-job-id="${item.id}" data-code="${escHtml(item.code_name)}" data-target="${item.target_lock_id}" checked /><span><strong>${escHtml(item.code_name)}</strong></span><span class="sync-pending-target">from ${escHtml(tName)}</span>${icon}</label></div>`;

        });

        pendingHtml += '</div>';

      }

    }


    const recent = recentSyncs[g.id] || [];

    let recentHtml = '';

    if (!recent.length) {

      recentHtml = '<div class="sync-recent-empty">No sync runs yet</div>';

    } else {

      recent.forEach((run) => {

        const isAuto = !run.dry_run && run.schedule_id;

        const badge = isAuto ? '<span class="sync-type-badge auto">Auto</span>' : '<span class="sync-type-badge forced">Forced</span>';

        recentHtml += `<div class="sync-recent-row"><div class="sync-recent-info">${badge}<span class="sync-recent-time">${fmtDate(run.started_at)}</span></div><div class="sync-recent-stats"><span class="sync-stat create">+${run.codes_created||0}</span><span class="sync-stat update">~${run.codes_updated||0}</span><span class="sync-stat delete">-${run.codes_deleted||0}</span></div></div>`;

      });

    }


    return `<div class="sync-group-card">

      <div class="sync-group-header">

        <h3>${escHtml(g.name)}</h3>

        <span class="sync-master-badge">Parent: ${escHtml(masterName)}</span>

      </div>

      <div class="sync-section">

        <div class="sync-section-title">Pending Changes</div>

        ${pendingHtml}

      </div>

      <div class="sync-section">

        <div class="sync-section-title">Recent Syncs</div>

        ${recentHtml}

      </div>

      <div class="sync-add-time">

        <div class="sync-add-time">

          <input type="time" class="form-input new-time-input" id="new-time-${g.id}" />

          <button class="btn btn-secondary add-time-btn" data-group="${g.id}">Add</button>

        </div>

        ${times.length ? `<details class="sync-times-accordion">
          <summary class="sync-times-summary">
            <span class="sync-times-label">Check times (${times.length})</span>
            <span class="sync-times-chevron"><i data-lucide="chevron-down" style="width:14px;height:14px;"></i></span>
          </summary>
          <div class="sync-times-list">
            ${times.map(t => `<div class="sync-time-item"><span>${fmtTime12(t)}</span><button class="btn btn-ghost remove-time-btn" data-group="${g.id}" data-time="${escHtml(t)}" title="Remove time">&times;</button></div>`).join('')}
          </div>
        </details>` : ''}

        <button class="btn btn-primary force-sync-btn" data-group="${g.id}" data-group-name="${escHtml(g.name)}" ${totalPending === 0 ? 'disabled' : ''}>

          <i data-lucide="refresh-cw" style="width:16px;height:16px;"></i> Force Sync Now${totalPending > 0 ? ` (${totalPending})` : ''}

        </button>

      </div>

    </div>`;

  }).join('');


  container.innerHTML = `<div class="page-header"><h1 class="page-title">Sync</h1></div><div class="sync-layout">${groupsHtml || '<div class="empty-state"><p>No groups configured.</p></div>'}</div>`;

  try { lucide.createIcons(); } catch(e) { console.warn('lucide error:', e); }



  container.querySelectorAll('.add-time-btn').forEach((btn) => {

    btn.addEventListener('click', async () => {

      const groupId = btn.dataset.group;

      const timeVal = document.getElementById('new-time-' + groupId)?.value;

      if (!timeVal) { alert('enter a time first'); return; }

      const url = '/sync/schedules?group_id=' + groupId + '&check_time=' + encodeURIComponent(timeVal);

      alert('POST to: ' + url);

      try {

        const res = await api(url, { method: 'POST' });

        alert('saved! check_times=' + JSON.stringify(res?.schedule?.check_times));

        toast('Time added', 'success');

        renderSyncPage(container);

      } catch (e) {

        alert('Failed: ' + e.message);

        toast('Failed to add time: ' + e.message, 'error');

      }

    });

  });


  container.querySelectorAll('.remove-time-btn').forEach((btn) => {

    btn.addEventListener('click', async () => {

      const groupId = btn.dataset.group;

      const time = btn.dataset.time;

      try {

        await api(`/sync/schedules/${groupId}?time=${encodeURIComponent(time)}`, { method: 'DELETE' });

        toast('Time removed', 'success');

        renderSyncPage(container);

      } catch { toast('Failed to remove time', 'error'); }

    });

  });


  container.querySelectorAll('.force-sync-btn').forEach((btn) => {

    btn.addEventListener('click', async () => {

      const groupId = parseInt(btn.dataset.group);

      const optOuts = [];

      container.querySelectorAll('.sync-pending-cb:not(:checked)').forEach((cb) => {

        if (parseInt(cb.dataset.group) === groupId) {

          optOuts.push({ job_id: parseInt(cb.dataset.jobId) || null, action: cb.dataset.action, code_name: cb.dataset.code, target_lock_id: cb.dataset.target });

        }

      });

      try {

        await api(`/sync/run/${groupId}`, { method: 'POST', body: JSON.stringify({ opt_outs: optOuts }) });

        toast('Sync complete', 'success');

        renderSyncPage(container);

      } catch (e) { toast('Sync failed', 'error'); }

    });

  });

}

const slideStyle = document.createElement('style');

slideStyle.textContent = `

.slide-modal-backdrop{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(10,10,15,0.7);backdrop-filter:blur(4px);z-index:1000;display:flex;justify-content:flex-end;}

.slide-modal-panel{width:480px;max-width:100%;background:var(--bg-elevated);border-left:1px solid var(--border-subtle);display:flex;flex-direction:column;animation:slideIn 0.3s ease;}

.slide-modal-header{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--border-subtle);}

.slide-modal-header h3{font-size:var(--text-lg);font-weight:var(--font-semibold);color:var(--text-primary);}

.slide-modal-body{flex:1;padding:24px;overflow-y:auto;display:flex;flex-direction:column;gap:16px;}

.slide-modal-footer{padding:16px 24px;border-top:1px solid var(--border-subtle);display:flex;flex-direction:column;gap:8px;}

.code-input-wrapper{display:flex;gap:8px;align-items:center;}

.code-input-wrapper .form-input{flex:1;}

.page-header-right{display:flex;gap:12px;align-items:center;}

.bulk-action-bar{display:none;align-items:center;gap:12px;padding:12px 16px;background:var(--bg-elevated);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);margin-bottom:16px;}

.bulk-action-bar.visible{display:flex;}

.codes-table-wrapper{overflow-x:auto;}

.codes-table{width:100%;border-collapse:collapse;}

.codes-table th{text-align:left;padding:12px 16px;font-size:var(--text-xs);font-weight:var(--font-medium);color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid var(--border-subtle);}

.codes-table td{padding:12px 16px;border-bottom:1px solid var(--border-subtle);font-size:var(--text-sm);color:var(--text-primary);vertical-align:middle;}

.code-row:hover{background:var(--bg-hover);}

.empty-state-cell{text-align:center;padding:48px !important;color:var(--text-muted);}

.empty-state-cell p{margin-top:12px;}

.group-card.selected{border-color:var(--accent-primary);}

.group-card{display:flex;align-items:center;justify-content:space-between;cursor:pointer;transition:border-color 0.2s,background 0.2s;}

.group-card:hover{border-color:var(--border-default);}

.group-detail{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:24px;}

.group-detail-header{margin-bottom:20px;}

.group-locks-list{display:flex;flex-direction:column;gap:8px;}

.group-locks-label{font-size:var(--text-xs);text-transform:uppercase;color:var(--text-muted);letter-spacing:0.05em;margin-bottom:8px;}

.group-lock-item{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-elevated);border-radius:var(--radius-md);font-size:var(--text-sm);}

.group-lock-item span{flex:1;}

.lock-checkbox-item{display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;border-radius:var(--radius-md);transition:background 0.15s;}

.lock-checkbox-item:hover{background:var(--bg-hover);}

.lock-checkbox-item input{width:18px;height:18px;accent-color:var(--accent-primary);}

.text-muted{color:var(--text-muted);}

.page-header-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}

.page-header-right .form-select{width:auto;min-width:160px;}

@keyframes slideIn{from{transform:translateX(100%);}to{transform:translateX(0);}}

.toast{position:fixed;bottom:24px;right:24px;background:var(--bg-elevated);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:12px 20px;display:flex;align-items:center;gap:10px;box-shadow:var(--shadow-elevated);z-index:9999;font-size:var(--text-sm);animation:slideUp 0.3s ease;}

.toast-success{border-color:rgba(34,197,94,0.4);}

.toast-error{border-color:rgba(239,68,68,0.4);}

.toast-success i{color:var(--success);}

.toast-error i{color:var(--status-error);}

@keyframes slideUp{from{transform:translateY(20px);opacity:0;}to{transform:translateY(0);opacity:1};}

.modal-backdrop{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(10,10,15,0.7);backdrop-filter:blur(4px);z-index:900;display:flex;align-items:center;justify-content:center;}

.modal-panel{background:var(--bg-elevated);border:1px solid var(--border-subtle);border-radius:var(--radius-xl);width:480px;max-width:90vw;box-shadow:var(--shadow-modal);animation:modalIn 0.2s ease;}

.modal-header{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--border-subtle);}

.modal-header h3{font-size:var(--text-lg);font-weight:var(--font-semibold);}

.modal-body{padding:24px;display:flex;flex-direction:column;gap:16px;}

.modal-footer{padding:16px 24px;border-top:1px solid var(--border-subtle);}

@keyframes modalIn{from{transform:scale(0.95);opacity:0;}to{transform:scale(1);opacity:1};}

.codes-group-filter{width:auto;min-width:160px;}

.code-schedule{font-size:var(--text-xs);color:var(--text-secondary);}

.code-value{font-family:'JetBrains Mono',monospace;font-size:var(--text-sm);}

.code-actions{position:relative;}

.code-menu-dropdown{position:absolute;right:0;top:100%;background:var(--bg-elevated);border:1px solid var(--border-subtle);border-radius:var(--radius-md);box-shadow:var(--shadow-elevated);z-index:10;min-width:140px;overflow:hidden;}

.code-menu-item{display:flex;align-items:center;gap:8px;padding:10px 14px;font-size:var(--text-sm);color:var(--text-secondary);cursor:pointer;width:100%;transition:background 0.15s,color 0.15s;border:none;background:none;text-align:left;}

.code-menu-item:hover{background:var(--bg-hover);color:var(--text-primary);}

.code-menu-item.danger{color:var(--status-error);}

.code-menu-item.danger:hover{background:rgba(239,68,68,0.1);}

.page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-6);gap:var(--space-4);flex-wrap:wrap;}

.page-header-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}

.page-header-right .form-select{width:auto;min-width:160px;}

.datetime-fields{display:none;flex-direction:column;gap:12px;}


/* Sync styles */

.sync-layout{display:grid;grid-template-columns:1fr 1fr;gap:24px;}

.sync-groups-section{display:flex;flex-direction:column;gap:16px;}

.sync-group-card{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:20px;}

.sync-group-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}

.sync-group-header h3{font-size:var(--text-lg);font-weight:var(--font-semibold);margin:0;}

.sync-master-badge{font-size:var(--text-xs);background:var(--bg-elevated);padding:4px 8px;border-radius:var(--radius-sm);color:var(--text-muted);}

.sync-times-list{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;align-items:center;}

.sync-times-label{font-size:var(--text-xs);color:var(--text-muted);margin-right:4px;}
.sync-times-accordion{margin-bottom:12px;}
.sync-times-summary{display:flex;align-items:center;gap:6px;cursor:pointer;padding:4px 0;list-style:none;font-size:var(--text-xs);color:var(--text-muted);}
.sync-times-summary::-webkit-details-marker{display:none;}
.sync-times-summary .sync-times-chevron{transition:transform 0.2s;display:inline-flex;}
.sync-times-accordion[open] .sync-times-chevron{transform:rotate(180deg);}
.sync-times-list{padding-top:8px;gap:6px;}


.sync-time-item .btn-ghost{padding:2px 6px;font-size:var(--text-base);background:transparent;border:none;color:var(--text-muted);cursor:pointer;}

.sync-time-item .btn-ghost:hover{color:var(--status-error);}

.sync-time-item{display:flex;align-items:center;gap:6px;background:var(--bg-elevated);padding:4px 8px;border-radius:var(--radius-md);font-size:var(--text-sm);}

.sync-time-item .btn-ghost{padding:2px;}

.sync-group-actions{display:flex;flex-direction:column;gap:12px;}

.master-lock-select{width:100%;}

.sync-add-time{display:flex;gap:8px;}

.sync-add-time input{flex:1;}

.force-sync-btn{width:100%;}

.section-title{font-size:var(--text-lg);font-weight:var(--font-semibold);margin-bottom:12px;}

.sync-history-section{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:20px;}

.sync-history-table{width:100%;border-collapse:collapse;font-size:var(--text-sm);}

.sync-history-table th{text-align:left;padding:8px 12px;border-bottom:1px solid var(--border-default);color:var(--text-muted);font-weight:var(--font-normal);font-size:var(--text-xs);text-transform:uppercase;}

.sync-history-table td{padding:10px 12px;border-bottom:1px solid var(--border-subtle);}

.sync-preview-section{margin-bottom:16px;}

.sync-preview-title{display:flex;align-items:center;gap:8px;font-size:var(--text-sm);font-weight:var(--font-semibold);margin-bottom:8px;}

.sync-preview-item{background:var(--bg-elevated);border-radius:var(--radius-md);padding:8px 12px;margin-bottom:6px;}

.sync-preview-label{display:flex;align-items:center;gap:10px;cursor:pointer;}

.sync-preview-label input{width:16px;height:16px;accent-color:var(--accent-primary);}

.sync-preview-label .code-value{font-family:'JetBrains Mono',monospace;font-size:var(--text-xs);margin-left:auto;}

.sync-preview-note{font-size:var(--text-xs);color:var(--text-muted);margin-top:8px;}

.status-success{color:var(--success);}

.status-error{color:var(--status-error);}

.status-pending{color:var(--text-muted);}

.btn-warning{background:var(--status-warning);color:#000;}

.btn-warning:hover{background:#d97706;}

`;

document.head.appendChild(slideStyle);
const POLL_MS = 6000;
const TOP_LIMIT = 10;
const PROFILE_FIELD_IDS = [
  'state_preamble',
  'identity_revealed',
  'identity_endorsed',
  'positive_profile',
  'negative_profile',
];

let pollTimer = null;
let activeTab = 'top';

const send = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatScore(row) {
  const raw = row.m3_score ?? row.score;
  if (raw == null || Number.isNaN(Number(raw))) return 'pending';
  return String(Math.round(Number(raw)));
}

function showBanner(text, visible) {
  const el = document.getElementById('banner');
  if (!el) return;
  if (!visible || !text) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = 'block';
  el.textContent = text;
}

function humanStatusSummary(s) {
  if (s.m3_error) return 'Scoring hit a snag — refresh to retry';
  const m3 = String(s.m3_status ?? 'idle').toLowerCase();
  if (s.top_ready) return 'Top Picks ready';
  if (m3 === 'scoring' || m3 === 'running' || m3 === 'busy' || m3 === 'queued') {
    return 'Scoring Top Picks';
  }
  const pending = Number(s.unscored_count);
  const active = Number(s.candidate_count);
  if (!Number.isNaN(pending) && pending > 0) return 'Scoring Top Picks';
  if (!Number.isNaN(active) && active === 0) return 'Waiting for candidates';
  if (s.candidate_count == null && s.total_seen_count == null) return 'Waiting for candidates';
  return 'Scoring Top Picks';
}

function formatStatusDetails(s) {
  const phase = s.phase ?? s.epoch_status ?? '—';
  const seen = s.total_seen_count ?? '—';
  const active = s.candidate_count ?? '—';
  const expired = s.expired_count ?? '—';
  const topK = s.top_k ?? TOP_LIMIT;
  const scored = s.scored_count ?? '—';
  const pending = s.unscored_count ?? '—';
  const m3 = s.m3_status ?? 'idle';
  const queue = s.m3_queue_depth ?? '—';
  const winMax = s.active_window_max ?? '—';
  return `Phase ${phase} · seen ${seen} · active ${active} · expired ${expired} · window ${winMax} · scored ${scored} · pending ${pending} · queue ${queue} · M3 ${m3} · top ${topK}`;
}

function renderStatusHeader(stateRes, statusRes) {
  const corePill = document.getElementById('core-pill');
  const modePill = document.getElementById('mode-pill');
  const statusLine = document.getElementById('status-line');
  const progressLine = document.getElementById('progress-line');

  const state = stateRes?.state || {};
  const online = !!state.health?.ok;
  const mode = state.mode || 'native';

  if (corePill) {
    corePill.textContent = online
      ? `core online${state.health?.core_base ? ` · ${state.health.core_base}` : ''}`
      : `core offline${state.health?.error ? `: ${state.health.error}` : ''}`;
    corePill.className = `pill ${online ? 'ok' : 'bad'}`;
  }
  if (modePill) {
    modePill.textContent = `mode · ${mode}`;
    modePill.className = `pill${mode === 'm3' ? ' ok' : ''}`;
  }

  if (mode !== 'm3') {
    if (statusLine) statusLine.textContent = 'Switch to MiniMax M3 in the extension popup to review ambient Top Picks.';
    if (progressLine) progressLine.textContent = '';
    showBanner('M3 side panel is most useful in M3 mode.', true);
    return;
  }

  showBanner(!online ? 'Start local core: python scripts\\serve_core.py' : '', !online);

  const s = statusRes && statusRes.ok !== false ? statusRes : null;
  if (!s) {
    const err = statusRes?.error || (online ? 'no feed status yet' : 'cannot reach core');
    if (statusLine) statusLine.textContent = online ? 'Status unavailable' : 'Core offline';
    if (progressLine) progressLine.textContent = err;
    return;
  }

  if (statusLine) statusLine.textContent = humanStatusSummary(s);
  if (progressLine) progressLine.textContent = formatStatusDetails(s);
}

function renderList(currentRes, statusRes) {
  const list = document.getElementById('list');
  if (!list) return;

  const topK = (statusRes && statusRes.ok !== false ? statusRes.top_k : null) ?? TOP_LIMIT;

  if (!currentRes || currentRes.ok === false) {
    const err = currentRes?.error || 'could not load current feed';
    list.className = 'empty';
    list.innerHTML = `<div class="empty">${escapeHtml(err)}</div>`;
    return;
  }

  const items = (currentRes.items || []).slice(0, topK);
  if (!items.length) {
    list.className = 'empty';
    list.textContent = 'No ranked items yet — scoring may still be in progress.';
    return;
  }

  list.className = '';
  list.innerHTML = items
    .map((row) => {
      const author = row.author_handle ? `@${row.author_handle}` : '@?';
      const tier = row.tier ? ` · ${row.tier}` : '';
      const snippet = ((row.text || '') + '').trim().slice(0, 280);
      const reason = ((row.reason || '') + '').trim();
      const url = row.url && String(row.url).trim() ? String(row.url).trim() : '';
      const openBtn = url
        ? `<button type="button" class="open-item" data-url="${escapeHtml(url)}">Open on X</button>`
        : '<span class="meta">no URL</span>';
      return `<article class="item" data-item-id="${escapeHtml(row.item_id || '')}">
        <div class="item-head">
          <span class="rank">#${row.rank ?? '—'}</span>
          <span class="meta">${escapeHtml(formatScore(row))}${escapeHtml(tier)}</span>
        </div>
        <div class="meta">${escapeHtml(author)}</div>
        ${snippet ? `<div class="snippet">${escapeHtml(snippet)}</div>` : ''}
        ${reason ? `<div class="reason">${escapeHtml(reason)}</div>` : ''}
        <div class="item-actions">${openBtn}</div>
      </article>`;
    })
    .join('');

  list.querySelectorAll('.open-item').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const url = btn.getAttribute('data-url');
      if (!url) return;
      btn.disabled = true;
      const res = await send({ type: 'OPEN_ITEM_URL', url });
      if (!res?.ok) showBanner(res?.error || 'failed to open tab', true);
      btn.disabled = false;
    });
  });
}

function readProfileFieldsFromForm() {
  const out = {};
  for (const id of PROFILE_FIELD_IDS) {
    const el = document.getElementById(id);
    out[id] = el ? String(el.value) : '';
  }
  return out;
}

function applyProfileFieldsToForm(fields) {
  if (!fields || typeof fields !== 'object') return;
  for (const id of PROFILE_FIELD_IDS) {
    const el = document.getElementById(id);
    if (el && fields[id] != null) el.value = String(fields[id]);
  }
}

function setProfileMeta(text, isError) {
  const el = document.getElementById('profile-meta');
  if (!el) return;
  el.textContent = text;
  el.className = isError ? 'meta bad' : 'meta';
}

function hideProfilePreview() {
  const box = document.getElementById('profile-preview');
  if (box) box.classList.remove('visible');
}

function renderProfilePreview(data) {
  const box = document.getElementById('profile-preview');
  const fieldsEl = document.getElementById('profile-preview-fields');
  const textEl = document.getElementById('profile-preview-text');
  const hashEl = document.getElementById('profile-preview-hash');
  if (!box || !textEl) return;

  const fields = data.fields || {};
  const lines = PROFILE_FIELD_IDS.map((key) => {
    const label = key.replace(/_/g, ' ');
    const val = fields[key] != null ? String(fields[key]) : '';
    const display = val.length ? val : '(empty in profile)';
    return `${label}:\n${display}`;
  });
  if (fieldsEl) {
    fieldsEl.innerHTML = `<pre style="margin:0 0 8px;font-size:10px;color:var(--muted)">${escapeHtml(lines.join('\n\n'))}</pre>`;
  }

  const promptText = data.prompt_text ?? data.prompt ?? '';
  textEl.textContent = promptText || '(no prompt text returned)';
  if (hashEl) {
    const parts = [];
    if (data.profile_version_id) parts.push(`version ${data.profile_version_id}`);
    if (data.prompt_hash) parts.push(data.prompt_hash);
    hashEl.textContent = parts.join(' · ');
  }
  box.classList.add('visible');
}

async function loadProfile() {
  setProfileMeta('Loading profile…', false);
  hideProfilePreview();
  const res = await send({ type: 'GET_PROFILE' });
  const active = res.active || res.version || {};
  applyProfileFieldsToForm(active);

  if (res.offline || res.ok === false) {
    setProfileMeta(
      res.error || 'Core offline — showing defaults. Save when core is running.',
      true,
    );
    showBanner('Profile edits need local core: python scripts\\serve_core.py', true);
    return;
  }

  const vid = res.active_version_id || active.profile_version_id || '—';
  const src = active.source ? ` · ${active.source}` : '';
  setProfileMeta(`Active profile ${vid}${src}`, false);
  showBanner('', false);
}

async function saveProfile() {
  const btn = document.getElementById('profile-save');
  if (btn) btn.disabled = true;
  try {
    const fields = readProfileFieldsFromForm();
    const res = await send({ type: 'SAVE_PROFILE', fields });
    if (res?.ok === false) {
      setProfileMeta(res.error || 'Save failed', true);
      showBanner(res.error || 'Save failed', true);
      return;
    }
    const version = res.version || res.active || fields;
    applyProfileFieldsToForm(version);
    setProfileMeta(`Saved · ${res.active_version_id || version.profile_version_id || 'new version'}`, false);
    hideProfilePreview();
    showBanner('Profile saved — new M3 batches will use this version.', true);
    setTimeout(() => showBanner('', false), 4000);
  } catch (err) {
    setProfileMeta(String(err), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function resetProfile() {
  if (!window.confirm('Reset profile to defaults? This appends a new default version.')) return;
  const btn = document.getElementById('profile-reset');
  if (btn) btn.disabled = true;
  try {
    const res = await send({ type: 'RESET_PROFILE' });
    if (res?.ok === false) {
      setProfileMeta(res.error || 'Reset failed', true);
      return;
    }
    const version = res.version || res.active || {};
    applyProfileFieldsToForm(version);
    setProfileMeta(`Reset · ${res.active_version_id || version.profile_version_id || 'default'}`, false);
    hideProfilePreview();
  } catch (err) {
    setProfileMeta(String(err), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function previewProfile() {
  const btn = document.getElementById('profile-preview-btn');
  if (btn) btn.disabled = true;
  try {
    const fields = readProfileFieldsFromForm();
    const res = await send({ type: 'PROFILE_PROMPT_PREVIEW', fields });
    if (res?.ok === false) {
      showBanner(res.error || 'Preview failed — is core running?', true);
      return;
    }
    renderProfilePreview(res);
  } catch (err) {
    showBanner(String(err), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setActiveTab(tab) {
  activeTab = tab === 'profile' ? 'profile' : 'top';
  document.querySelectorAll('.tab').forEach((el) => {
    const isActive = el.getAttribute('data-tab') === activeTab;
    el.classList.toggle('active', isActive);
    el.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
  const panelPairs = [
    ['panel-top', 'top'],
    ['panel-profile', 'profile'],
    ['view-top', 'top'],
    ['view-profile', 'profile'],
  ];
  for (const [id, tabName] of panelPairs) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', tabName === activeTab);
  }

  if (activeTab === 'profile') {
    clearInterval(pollTimer);
    pollTimer = null;
    loadProfile();
  } else {
    refreshAll();
    startPolling();
  }
}

async function refreshAll({ requestScoring = false } = {}) {
  if (activeTab !== 'top') return;
  const refreshBtn = document.getElementById('refresh');
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    const stateRes = await send({ type: 'GET_STATE' });
    let statusRes = null;
    let currentRes = null;

    try {
      statusRes = await send({ type: 'FEED_STATUS' });
    } catch {
      statusRes = { ok: false, error: 'FEED_STATUS failed' };
    }

    const mode = stateRes?.state?.mode || 'native';
    if (mode !== 'm3') {
      renderStatusHeader(stateRes, statusRes);
      const list = document.getElementById('list');
      if (list) {
        list.className = 'empty';
        list.textContent = 'Switch to MiniMax M3 in the extension popup to show Top Picks.';
      }
      return;
    }

    if (requestScoring && mode === 'm3') {
      try {
        await send({ type: 'REQUEST_M3_FEED' });
        statusRes = await send({ type: 'FEED_STATUS' });
      } catch {
        /* keep last status */
      }
    }

    try {
      currentRes = await send({ type: 'FEED_CURRENT', limit: TOP_LIMIT });
    } catch {
      currentRes = { ok: false, error: 'FEED_CURRENT failed' };
    }

    renderStatusHeader(stateRes, statusRes);
    renderList(currentRes, statusRes);
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (document.visibilityState === 'visible' && activeTab === 'top') refreshAll();
  }, POLL_MS);
}

function bindProfileActions() {
  document.getElementById('profile-save')?.addEventListener('click', () => saveProfile());
  document.getElementById('profile-reset')?.addEventListener('click', () => resetProfile());
  document.getElementById('profile-preview-btn')?.addEventListener('click', () => previewProfile());
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab');
      if (tab) setActiveTab(tab);
    });
  });
}

async function resolveInitialTab() {
  try {
    const stored = await chrome.storage.local.get(['sidepanelInitialTab']);
    if (stored.sidepanelInitialTab === 'profile') {
      await chrome.storage.local.remove('sidepanelInitialTab');
      return 'profile';
    }
  } catch {
    /* ignore */
  }
  return 'top';
}

document.getElementById('refresh')?.addEventListener('click', () => refreshAll({ requestScoring: true }));

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && activeTab === 'top') refreshAll();
});

bindTabs();
bindProfileActions();

(async () => {
  const initial = await resolveInitialTab();
  setActiveTab(initial);
  if (initial === 'top') {
    refreshAll();
    startPolling();
  }
})();
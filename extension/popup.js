const send = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function scoreLabel(row) {
  const raw = row.m3_score ?? row.score;
  if (raw == null || Number.isNaN(Number(raw))) return 'pending';
  return String(Math.round(Number(raw)));
}

function formatM3StatusLine(online, feedStatus) {
  if (!online) return 'Core offline — start local core, then open Top Picks.';
  if (!feedStatus) return 'Waiting for feed status — browse X For You, then refresh scoring.';
  if (feedStatus.top_ready) {
    const k = feedStatus.top_k ?? 10;
    return `Top ${k} picks ready — open the side panel or Top Picks window for the full list.`;
  }
  const m3 = feedStatus.m3_status ?? 'idle';
  if (m3 === 'busy' || (feedStatus.unscored_count ?? 0) > 0) {
    return 'Scoring in progress — open Top Picks to watch results update there.';
  }
  return 'Queue warming up — ingest on X, then refresh scoring.';
}

function updateM3StatusLine(online, feedStatus) {
  const el = document.getElementById('m3-status-line');
  if (!el) return;
  el.textContent = formatM3StatusLine(online, feedStatus);
}

function renderM3TopList(container, stateEl, { online, feedStatus, items, fetchError }) {
  container.innerHTML = '';
  container.appendChild(stateEl);

  if (!online) {
    stateEl.textContent = 'Core offline — start local core to load Top Picks.';
    return;
  }
  if (fetchError) {
    stateEl.textContent = `Could not load Top Picks: ${fetchError}`;
    return;
  }
  if (!feedStatus) {
    stateEl.textContent = 'No feed status yet — ingest tweets on X, then refresh.';
    return;
  }
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {
    const topReady = feedStatus.top_ready;
    stateEl.textContent = topReady
      ? 'No ranked items in the current window yet.'
      : 'Top Picks not ready — scoring in progress or queue empty.';
    return;
  }

  stateEl.style.display = 'none';
  for (const row of list) {
    const div = document.createElement('div');
    div.className = 'm3-row';
    const author = row.author_handle || '?';
    const tier = row.tier ? ` · ${row.tier}` : '';
    const snippet = ((row.text || '') + '').trim().slice(0, 120);
    const rank = row.rank != null ? row.rank : '—';
    div.innerHTML = `
      <div class="m3-row-meta">#${escapeHtml(rank)} · @${escapeHtml(author)} · ${escapeHtml(scoreLabel(row))}${escapeHtml(tier)}</div>
      ${snippet ? `<div class="m3-row-snippet">${escapeHtml(snippet)}</div>` : ''}
      <div class="m3-row-actions"></div>`;
    const actions = div.querySelector('.m3-row-actions');
    const url = row.url;
    if (url) {
      const openBtn = document.createElement('button');
      openBtn.type = 'button';
      openBtn.textContent = 'Open on X';
      openBtn.addEventListener('click', () => openTweetUrl(url));
      actions.appendChild(openBtn);
    }
    container.appendChild(div);
  }
}

async function openTweetUrl(url) {
  if (!url) return;
  const res = await send({ type: 'OPEN_ITEM_URL', url });
  if (res?.ok) return;
  try {
    await chrome.tabs.create({ url });
  } catch {
    window.open(url, '_blank', 'noopener');
  }
}

async function loadM3TopPicks(online, feedStatus) {
  const listRoot = document.getElementById('m3-top-list');
  let stateEl = document.getElementById('m3-list-state');
  if (!stateEl) {
    stateEl = document.createElement('div');
    stateEl.id = 'm3-list-state';
    stateEl.className = 'muted';
  }
  stateEl.style.display = 'block';
  stateEl.textContent = 'Loading…';
  listRoot.innerHTML = '';
  listRoot.appendChild(stateEl);

  if (!online) {
    renderM3TopList(listRoot, stateEl, { online, feedStatus, items: [], fetchError: null });
    return;
  }

  let items = [];
  let fetchError = null;
  try {
    const cur = await send({ type: 'FEED_CURRENT', limit: 10 });
    if (cur?.ok === false) {
      fetchError = cur.error || 'request failed';
    } else {
      items = cur.items || [];
    }
  } catch (err) {
    fetchError = String(err);
  }
  renderM3TopList(listRoot, stateEl, { online, feedStatus, items, fetchError });
}

let m3FallbackListLoaded = false;

function bindM3FallbackPreviewLoader() {
  const details = document.getElementById('m3-fallback-preview');
  if (!details || details.__lensFallbackBound) return;
  details.__lensFallbackBound = true;
  details.addEventListener('toggle', async () => {
    if (!details.open) return;
    const res = await send({ type: 'GET_STATE' });
    const state = res.state || {};
    const online = !!state.health?.ok;
    await loadM3TopPicks(online, state.feedStatus);
    m3FallbackListLoaded = true;
  });
}

async function ensureM3FallbackListLoaded() {
  const res = await send({ type: 'GET_STATE' });
  const state = res.state || {};
  const online = !!state.health?.ok;
  await loadM3TopPicks(online, state.feedStatus);
  m3FallbackListLoaded = true;
}

function setM3SectionVisible(mode) {
  const section = document.getElementById('m3-section');
  const openBtn = document.getElementById('open-top-picks');
  const isM3 = mode === 'm3';
  if (section) section.style.display = isM3 ? 'block' : 'none';
  if (openBtn) openBtn.style.display = isM3 ? 'inline-block' : 'none';
  const hint = document.getElementById('m3-panel-hint');
  if (hint && !isM3) {
    hint.style.display = 'none';
    hint.textContent = '';
  }
}

function setProfileCtasVisible() {
  const globalBtn = document.getElementById('edit-profile-global');
  if (globalBtn) globalBtn.style.display = 'inline-block';
}

async function load() {
  setProfileCtasVisible();

  const res = await send({ type: 'GET_STATE' });
  const state = res.state || {};
  document.getElementById('session').textContent = state.sessionId || '';
  const online = !!state.health?.ok;
  document.getElementById('health').textContent = online
    ? `core online (${state.health?.core_base || 'local'})`
    : `core offline: ${state.health?.error || 'not reachable'}`;
  document.getElementById('health').className = online ? 'ok' : 'bad';
  document.getElementById('offline-hint').style.display = online ? 'none' : 'block';
  const radio = document.querySelector(`input[name="mode"][value="${state.mode || 'native'}"]`);
  if (radio) radio.checked = true;
  document.getElementById('reorder').checked = !!state.experimentalReorder;
  const mode = state.mode || 'native';
  setM3SectionVisible(mode);
  if (mode === 'm3') {
    updateM3StatusLine(online, state.feedStatus);

    bindM3FallbackPreviewLoader();
    const fallbackDetails = document.getElementById('m3-fallback-preview');
    if (!fallbackDetails?.open) {
      m3FallbackListLoaded = false;
    }
  } else {
    m3FallbackListLoaded = false;
  }
  document.getElementById('status').textContent = JSON.stringify(
    { queues: state.queues, minimax: state.health?.minimax_key_present, feedStatus: state.feedStatus || null },
    null,
    2,
  );
}

document.querySelectorAll('input[name="mode"]').forEach((el) => {
  el.addEventListener('change', async () => {
    await send({ type: 'SET_MODE', mode: el.value });
    await load();
  });
});

document.getElementById('reorder').addEventListener('change', async (e) => {
  await send({ type: 'SET_REORDER', experimentalReorder: e.target.checked });
  await load();
});

document.getElementById('new-session').addEventListener('click', async () => {
  await send({ type: 'NEW_SESSION' });
  await load();
});

async function tryOpenSidePanelInPopup() {
  const sidePanel = chrome.sidePanel;
  if (!sidePanel?.open) return null;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tab?.id;
  if (tabId == null) return null;
  try {
    await sidePanel.open({ tabId });
    return { ok: true, opened: true, surface: 'side_panel' };
  } catch {
    return null;
  }
}

document.getElementById('open-top-picks').addEventListener('click', async () => {
  const hint = document.getElementById('m3-panel-hint');
  let res = await tryOpenSidePanelInPopup();
  if (!res?.ok) {
    res = await send({ type: 'OPEN_M3_PANEL' });
  }
  if (res?.ok && res.opened) {
    if (hint) {
      hint.style.display = 'block';
      const surface =
        res.surface === 'popup_window'
          ? 'Top Picks window'
          : res.surface === 'side_panel'
            ? 'Side panel'
            : 'Top Picks';
      hint.textContent = `Opened ${surface} — that is the canonical Top Picks surface. Use fallback preview below only if needed.`;
      hint.className = 'ok';
    }
    return;
  }
  if (hint) {
    hint.style.display = 'block';
    const err = res?.error || 'Top Picks surface not available';
    hint.textContent = `${err}. Expand fallback preview below for an inline list.`;
    hint.className = 'muted';
  }
  const fallback = document.getElementById('m3-fallback-preview');
  if (fallback) fallback.open = true;
  await ensureM3FallbackListLoaded();
});

async function openProfileEditor() {
  const hint = document.getElementById('m3-panel-hint');
  await chrome.storage.local.set({ sidepanelInitialTab: 'profile' });
  let res = await tryOpenSidePanelInPopup();
  if (!res?.ok) res = await send({ type: 'OPEN_PROFILE_PANEL' });
  if (res?.ok && res.opened) {
    if (hint) {
      hint.style.display = 'block';
      hint.textContent = 'Side panel opened — use the Profile tab.';
      hint.className = 'ok';
    }
    return;
  }
  if (hint) {
    hint.style.display = 'block';
    hint.textContent = res?.error || 'Open the Lens side panel and choose Profile.';
    hint.className = 'muted';
  }
}


document.getElementById('edit-profile')?.addEventListener('click', () => openProfileEditor());
document.getElementById('edit-profile-global')?.addEventListener('click', () => openProfileEditor());



document.getElementById('refresh-rank').addEventListener('click', async () => {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  if (mode === 'm3') {
    const res = await send({ type: 'REQUEST_M3_FEED' });
    await load();
    const statusPre = document.getElementById('status');
    const detail = { m3_request: res };
    try {
      Object.assign(detail, JSON.parse(statusPre.textContent || '{}'));
    } catch {
      detail.previous = statusPre.textContent;
    }
    statusPre.textContent = JSON.stringify(detail, null, 2);
  } else {
    const res = await send({ type: 'RANK', mode, refresh: true });
    document.getElementById('status').textContent = JSON.stringify(
      { arm: res.arm, effective: res.effective_arm, n: res.items?.length, model_calls: res.model_calls },
      null,
      2,
    );
  }
});

document.getElementById('flush').addEventListener('click', async () => {
  await send({ type: 'FLUSH' });
  await load();
});

load();
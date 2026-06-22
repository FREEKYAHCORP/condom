const CORE_CANDIDATES = ['http://127.0.0.1:8765', 'http://localhost:8765'];
const DEFAULTS = { mode: 'native', experimentalReorder: false };
const queues = { raw: [], items: [], events: [] };
let flushing = false;
let m3RequestTimer = null;

async function getState() {
  const state = await chrome.storage.local.get(['sessionId', 'mode', 'experimentalReorder']);
  if (!state.sessionId) {
    state.sessionId = `x_for_you_${new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15)}Z`;
    await chrome.storage.local.set({ sessionId: state.sessionId });
  }
  return { ...DEFAULTS, ...state };
}

async function coreBase() {
  const cached = await chrome.storage.local.get(['coreBase']);
  const candidates = cached.coreBase
    ? [cached.coreBase, ...CORE_CANDIDATES.filter((x) => x !== cached.coreBase)]
    : CORE_CANDIDATES;
  let lastError = '';
  for (const base of candidates) {
    try {
      const res = await fetch(`${base}/health`, { cache: 'no-store' });
      if (res.ok) {
        await chrome.storage.local.set({ coreBase: base });
        return base;
      }
      lastError = `${base} HTTP ${res.status}`;
    } catch (err) {
      lastError = `${base}: ${String(err)}`;
    }
  }
  throw new Error(`Condom core is not reachable. Start it with: python scripts/serve_core.py. Last error: ${lastError}`);
}

async function getJson(path) {
  const base = await coreBase();
  const res = await fetch(`${base}${path}`, { cache: 'no-store' });
  const body = await res.json().catch(() => ({ ok: res.ok }));
  if (!res.ok) {
    const err = new Error(body?.detail || body?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

async function postJson(path, payload) {
  const base = await coreBase();
  const res = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return res.json().catch(() => ({ ok: res.ok }));
}

async function health() {
  try {
    const base = await coreBase();
    const res = await fetch(`${base}/health`, { cache: 'no-store' });
    return { ...(await res.json()), core_base: base };
  } catch (err) {
    return { ok: false, error: String(err), core_base: null };
  }
}

function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

function clearQueues() {
  queues.raw.length = 0;
  queues.items.length = 0;
  queues.events.length = 0;
}

async function broadcastSessionReset(sessionId) {
  const tabs = await chrome.tabs.query({ url: ['*://x.com/*', '*://twitter.com/*'] });
  for (const tab of tabs) {
    if (tab.id == null) continue;
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'SESSION_RESET', sessionId });
    } catch {
      /* tab may not have content script */
    }
  }
}

async function fetchFeedStatus(sessionId) {
  return getJson(`/feed/status?session_id=${encodeURIComponent(sessionId)}`);
}

async function fetchFeedCurrent(sessionId, limit) {
  let path = `/feed/m3/current?session_id=${encodeURIComponent(sessionId)}`;
  if (limit != null && Number.isFinite(limit)) {
    path += `&limit=${encodeURIComponent(String(limit))}`;
  }
  return getJson(path);
}

async function requestM3FeedScoring(sessionId, options = {}) {
  const payload = { session_id: sessionId };
  if (options.batch_size != null) payload.batch_size = options.batch_size;
  if (options.max_batches != null) payload.max_batches = options.max_batches;
  return postJson('/feed/m3/request', payload);
}

function scheduleM3FeedRequest(sessionId) {
  clearTimeout(m3RequestTimer);
  m3RequestTimer = setTimeout(() => {
    m3RequestTimer = null;
    requestM3FeedScoring(sessionId).catch(() => {});
  }, 1500);
}

async function flush() {
  if (flushing) return { hadRaw: false, hadItems: false, hadEvents: false };
  flushing = true;
  let hadRaw = false;
  let hadItems = false;
  let hadEvents = false;
  try {
    const state = await getState();
    while (queues.raw.length) {
      hadRaw = true;
      const row = queues.raw.shift();
      await postJson('/ingest/raw-response', {
        session_id: state.sessionId,
        response_id: row.response_id,
        url: row.url,
        body: row.bodyText,
        captured_at: row.capturedAt,
      });
    }
    if (queues.items.length) {
      hadItems = true;
      const items = queues.items.splice(0, queues.items.length);
      await postJson('/ingest/items', { session_id: state.sessionId, items });
    }
    if (queues.events.length) {
      hadEvents = true;
      const events = queues.events.splice(0, queues.events.length);
      await postJson('/ingest/events', { session_id: state.sessionId, events });
    }
    if ((hadRaw || hadItems) && (state.mode || DEFAULTS.mode) === 'm3') {
      scheduleM3FeedRequest(state.sessionId);
    }
    return { hadRaw, hadItems, hadEvents };
  } finally {
    flushing = false;
  }
}

async function rankViaAmbientFeed(state, refresh) {
  await flush();
  const limit = refresh ? undefined : 500;
  const current = await fetchFeedCurrent(state.sessionId, limit);
  const mode = 'm3';
  const arm = current.arm ?? 'm3_item_scoring_v0';
  const effective_arm = current.effective_arm ?? arm;
  const items = current.items || [];
  return {
    session_id: state.sessionId,
    mode,
    arm,
    effective_arm,
    ordered_item_ids: current.ordered_item_ids ?? items.map((row) => row.item_id),
    items,
    model_calls: current.model_calls ?? [],
    feed_snapshot: current.feed_snapshot ?? current.snapshot ?? null,
    ambient: true,
    refresh,
  };
}

setInterval(() => {
  flush().catch(() => {});
}, 2000);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const state = await getState();
    if (msg?.type === 'RAW_RESPONSE') {
      queues.raw.push({
        ...msg.payload,
        response_id: `${state.sessionId}:${hashString(msg.payload.url + msg.payload.capturedAt + msg.payload.bodyText.slice(0, 500))}`,
      });
      if (queues.raw.length >= 5) await flush();
      sendResponse({ ok: true, queued: queues.raw.length });
    } else if (msg?.type === 'ITEMS') {
      queues.items.push(...(msg.items || []));
      if (queues.items.length >= 20) await flush();
      sendResponse({ ok: true, queued: queues.items.length });
    } else if (msg?.type === 'EVENTS') {
      queues.events.push(...(msg.events || []));
      if (queues.events.length >= 20) await flush();
      sendResponse({ ok: true, queued: queues.events.length });
    } else if (msg?.type === 'FLUSH') {
      await flush();
      sendResponse({ ok: true });
    } else if (msg?.type === 'FEED_STATUS') {
      const data = await fetchFeedStatus(state.sessionId);
      sendResponse({ ok: true, ...data });
    } else if (msg?.type === 'FEED_CURRENT') {
      const data = await fetchFeedCurrent(state.sessionId, msg.limit);
      sendResponse({ ok: true, ...data });
    } else if (msg?.type === 'REQUEST_M3_FEED') {
      const data = await requestM3FeedScoring(state.sessionId, {
        batch_size: msg.batch_size,
        max_batches: msg.max_batches,
      });
      sendResponse({ ok: true, ...data });
    } else if (msg?.type === 'GET_STATE') {
      let feedStatus = null;
      try {
        feedStatus = await fetchFeedStatus(state.sessionId);
      } catch {
        feedStatus = null;
      }
      sendResponse({
        ok: true,
        state: {
          ...state,
          health: await health(),
          queues: Object.fromEntries(Object.entries(queues).map(([k, v]) => [k, v.length])),
          feedStatus,
        },
      });
    } else if (msg?.type === 'SET_MODE') {
      await chrome.storage.local.set({ mode: msg.mode });
      sendResponse({ ok: true });
    } else if (msg?.type === 'SET_REORDER') {
      await chrome.storage.local.set({ experimentalReorder: !!msg.experimentalReorder });
      sendResponse({ ok: true });
    } else if (msg?.type === 'NEW_SESSION') {
      clearTimeout(m3RequestTimer);
      m3RequestTimer = null;
      clearQueues();
      const sessionId = `x_for_you_${new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15)}Z`;
      await chrome.storage.local.set({ sessionId });
      await broadcastSessionReset(sessionId);
      sendResponse({ ok: true, sessionId });
    } else if (msg?.type === 'RANK') {
      const mode = msg.mode || state.mode || 'native';
      const refresh = !!msg.refresh;
      if (mode === 'm3') {
        if (refresh) {
          requestM3FeedScoring(state.sessionId).catch(() => {});
        }
        sendResponse(await rankViaAmbientFeed(state, refresh));
      } else {
        await flush();
        const base = await coreBase();
        const res = await fetch(
          `${base}/rank?session_id=${encodeURIComponent(state.sessionId)}&mode=${encodeURIComponent(mode)}&refresh=${refresh ? 'true' : 'false'}`,
        );
        sendResponse(await res.json());
      }
    } else {
      sendResponse({ ok: false, error: 'unknown message' });
    }
  })().catch((err) => sendResponse({ ok: false, error: String(err) }));
  return true;
});
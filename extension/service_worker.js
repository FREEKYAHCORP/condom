const CORE_CANDIDATES = ['http://127.0.0.1:8765', 'http://localhost:8765'];
const DEFAULTS = { mode: 'native', experimentalReorder: false };
const queues = { raw: [], items: [], events: [] };
let flushing = false;

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

async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    const state = await getState();
    while (queues.raw.length) {
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
      const items = queues.items.splice(0, queues.items.length);
      await postJson('/ingest/items', { session_id: state.sessionId, items });
    }
    if (queues.events.length) {
      const events = queues.events.splice(0, queues.events.length);
      await postJson('/ingest/events', { session_id: state.sessionId, events });
    }
  } finally {
    flushing = false;
  }
}

setInterval(flush, 2000);

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
    } else if (msg?.type === 'GET_STATE') {
      sendResponse({ ok: true, state: { ...state, health: await health(), queues: Object.fromEntries(Object.entries(queues).map(([k,v]) => [k, v.length])) } });
    } else if (msg?.type === 'SET_MODE') {
      await chrome.storage.local.set({ mode: msg.mode });
      sendResponse({ ok: true });
    } else if (msg?.type === 'SET_REORDER') {
      await chrome.storage.local.set({ experimentalReorder: !!msg.experimentalReorder });
      sendResponse({ ok: true });
    } else if (msg?.type === 'NEW_SESSION') {
      const sessionId = `x_for_you_${new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15)}Z`;
      await chrome.storage.local.set({ sessionId });
      sendResponse({ ok: true, sessionId });
    } else if (msg?.type === 'RANK') {
      await flush();
      const mode = msg.mode || state.mode || 'native';
      const refresh = !!msg.refresh;
      const base = await coreBase();
      const res = await fetch(`${base}/rank?session_id=${encodeURIComponent(state.sessionId)}&mode=${encodeURIComponent(mode)}&refresh=${refresh ? 'true' : 'false'}`);
      sendResponse(await res.json());
    } else {
      sendResponse({ ok: false, error: 'unknown message' });
    }
  })().catch((err) => sendResponse({ ok: false, error: String(err) }));
  return true;
});

(() => {
  const seenItems = new Map();
  const visible = new Map();
  let mode = 'native';
  let experimentalReorder = false;
  let rankById = new Map();
  let itemMetaById = new Map();
  let ambientFeedStatus = null;

  const send = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));

  function resetSessionState() {
    seenItems.clear();
    visible.clear();
    rankById = new Map();
    itemMetaById = new Map();
    ambientFeedStatus = null;
    for (const article of document.querySelectorAll('article')) {
      const badge = article.querySelector(':scope > .lens-m0-badge');
      if (badge) badge.remove();
      article.removeAttribute('data-lens-m0-top');
      article.removeAttribute('data-lens-m0-low');
      const cell = cellFor(article);
      if (cell) cell.style.order = '';
    }
    const statusEl = document.getElementById('lens-m0-status');
    if (statusEl) statusEl.textContent = 'Lens M0';
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type === 'SESSION_RESET') resetSessionState();
  });

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== 'lens-m0-page-hook' || data.type !== 'raw-response') return;
    send({ type: 'RAW_RESPONSE', payload: data });
  });

  function statusLink(article) {
    const links = [...article.querySelectorAll('a[href*="/status/"]')];
    return links.find((a) => /\/status\/\d+/.test(a.href));
  }

  function extractItem(article) {
    const link = statusLink(article);
    if (!link) return null;
    const match = link.href.match(/\/([^/]+)\/status\/(\d+)/);
    if (!match) return null;
    const text = (article.innerText || '').trim();
    if (!text) return null;
    return {
      item_id: match[2],
      author_handle: match[1],
      url: link.href.split('?')[0],
      text: text.slice(0, 5000),
      first_seen_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
      saved_dom: /Bookmarked/i.test(text) ? 1 : 0,
    };
  }

  function eventFor(itemId, patch = {}) {
    return {
      event_id: `${itemId}:${Date.now()}:${Math.random().toString(16).slice(2)}`,
      item_id: itemId,
      exposed: 1,
      visible_ms: 0,
      stop: 0,
      save: 0,
      look_sec: 0,
      profile_open: 0,
      thread_open: 0,
      link_click: 0,
      exposed_surface: 'x_for_you',
      ts: new Date().toISOString(),
      ...patch,
    };
  }

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const article = entry.target;
      const item = extractItem(article);
      if (!item) continue;
      if (!seenItems.has(item.item_id)) {
        seenItems.set(item.item_id, item);
        send({ type: 'ITEMS', items: [item] });
      }
      const st = visible.get(item.item_id) || { visibleMs: 0, startedAt: null };
      if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
        if (!st.startedAt) {
          st.startedAt = Date.now();
          send({ type: 'EVENTS', events: [eventFor(item.item_id)] });
        }
      } else if (st.startedAt) {
        const delta = Date.now() - st.startedAt;
        st.visibleMs += delta;
        st.startedAt = null;
        send({ type: 'EVENTS', events: [eventFor(item.item_id, {
          visible_ms: Math.round(st.visibleMs),
          look_sec: st.visibleMs / 1000,
          stop: delta >= 1500 ? 1 : 0,
        })] });
      }
      visible.set(item.item_id, st);
    }
  }, { threshold: [0, 0.5, 1] });

  function injectStyles() {
    if (document.getElementById('lens-m0-style')) return;
    const style = document.createElement('style');
    style.id = 'lens-m0-style';
    style.textContent = `
      .lens-m0-badge { position:absolute; z-index:999999; right:8px; top:4px; font:12px/1.2 system-ui; padding:2px 6px; border-radius:999px; background:#111827; color:white; opacity:.85; pointer-events:none; }
      [data-lens-m0-top="1"] { outline:2px solid rgba(14,165,233,.65) !important; outline-offset:-2px; }
      [data-lens-m0-low="1"] { opacity:.45 !important; }
      #lens-m0-status { position:fixed; z-index:999999; bottom:12px; right:12px; background:#111827; color:white; padding:6px 9px; border-radius:8px; font:12px system-ui; pointer-events:none; opacity:.82; }
    `;
    document.documentElement.appendChild(style);
  }

  function formatAmbientStatus() {
    if (mode === 'm3') {
      const s = ambientFeedStatus;
      if (!s) return 'Lens M0 · m3 · waiting for core';
      const captured = s.candidate_count ?? s.captured ?? '—';
      const scored = s.scored_count ?? s.scored ?? '—';
      const pending = s.unscored_count ?? s.pending ?? (Number.isFinite(captured) && Number.isFinite(scored) ? Math.max(0, captured - scored) : '—');
      const status = s.m3_status ?? s.status ?? 'idle';
      return `Lens M0 · m3 · ${scored}/${captured} scored · ${pending} pending · ${status}`;
    }
    return `Lens M0 · ${mode}`;
  }

  function showStatus() {
    injectStyles();
    let el = document.getElementById('lens-m0-status');
    if (!el) {
      el = document.createElement('div');
      el.id = 'lens-m0-status';
      document.documentElement.appendChild(el);
    }
    el.textContent = formatAmbientStatus();
  }

  function cellFor(article) {
    return article.closest('[data-testid="cellInnerDiv"]') || article.parentElement;
  }

  function applyRanks() {
    showStatus();
    for (const article of document.querySelectorAll('article')) {
      const item = extractItem(article);
      if (!item) continue;
      const cell = cellFor(article);
      article.style.position = 'relative';
      let badge = article.querySelector(':scope > .lens-m0-badge');
      if (mode === 'native') {
        if (badge) badge.remove();
        article.removeAttribute('data-lens-m0-top');
        article.removeAttribute('data-lens-m0-low');
        if (cell) cell.style.order = '';
        continue;
      }
      const rank = rankById.get(item.item_id);
      const meta = itemMetaById.get(item.item_id) || {};
      if (!rank) {
        if (badge) badge.remove();
        article.removeAttribute('data-lens-m0-top');
        article.removeAttribute('data-lens-m0-low');
        if (cell && !experimentalReorder) cell.style.order = '';
        continue;
      }
      if (!badge) {
        badge = document.createElement('div');
        badge.className = 'lens-m0-badge';
        article.appendChild(badge);
      }
      if (mode === 'm3') {
        const score = meta.score != null ? Math.round(Number(meta.score)) : null;
        const tier = meta.tier ? String(meta.tier) : '';
        badge.textContent = score != null && !Number.isNaN(score)
          ? `m3 #${rank}${tier ? ` · ${tier}` : ''} (${score})`
          : `m3 #${rank}${tier ? ` · ${tier}` : ''}`;
        const serve = meta.serve;
        const top = tier === 'gold' || rank <= 12;
        const low = serve === false || rank > 40;
        article.setAttribute('data-lens-m0-top', top && serve !== false ? '1' : '0');
        article.setAttribute('data-lens-m0-low', low ? '1' : '0');
      } else {
        badge.textContent = `${mode} #${rank}`;
        article.setAttribute('data-lens-m0-top', rank <= 12 ? '1' : '0');
        article.setAttribute('data-lens-m0-low', rank > 40 ? '1' : '0');
      }
      if (experimentalReorder && cell) {
        const parent = cell.parentElement;
        if (parent) {
          parent.style.display = 'flex';
          parent.style.flexDirection = 'column';
          cell.style.order = String(rank);
        }
      } else if (cell) {
        cell.style.order = '';
      }
    }
  }

  function scan() {
    const articles = [...document.querySelectorAll('article')];
    const newItems = [];
    for (const article of articles) {
      if (!article.__lensObserved) {
        article.__lensObserved = true;
        observer.observe(article);
      }
      const item = extractItem(article);
      if (item && !seenItems.has(item.item_id)) {
        seenItems.set(item.item_id, item);
        newItems.push(item);
      }
    }
    if (newItems.length) send({ type: 'ITEMS', items: newItems });
    applyRanks();
  }

  async function refreshStateAndRank(force = false) {
    const res = await send({ type: 'GET_STATE' });
    if (res && res.state) {
      mode = res.state.mode || 'native';
      experimentalReorder = !!res.state.experimentalReorder;
      ambientFeedStatus = res.state.feedStatus ?? ambientFeedStatus;
    }
    if (mode === 'm3') {
      try {
        const st = await send({ type: 'FEED_STATUS' });
        if (st?.ok !== false) ambientFeedStatus = st;
      } catch {
        /* keep last feedStatus */
      }
      const ranked = await send({ type: 'RANK', mode: 'm3', refresh: force });
      const items = ranked.items || [];
      rankById = new Map(items.map((row) => [row.item_id, row.rank]));
      itemMetaById = new Map(
        items.map((row) => [
          row.item_id,
          { score: row.score, tier: row.tier, serve: row.serve, reason: row.reason },
        ]),
      );
    } else if (mode === 'cheap') {
      itemMetaById = new Map();
      const ranked = await send({ type: 'RANK', mode: 'cheap', refresh: force });
      rankById = new Map((ranked.items || []).map((row) => [row.item_id, row.rank]));
    } else {
      rankById = new Map();
      itemMetaById = new Map();
    }
    applyRanks();
  }

  document.addEventListener('click', (event) => {
    const article = event.target.closest && event.target.closest('article');
    if (!article) return;
    const item = extractItem(article);
    if (!item) return;
    const target = event.target.closest('a,button,[role="button"]');
    const label = `${target?.getAttribute('aria-label') || ''} ${target?.getAttribute('data-testid') || ''}`;
    const href = target?.href || '';
    const patch = {};
    if (/bookmark/i.test(label)) patch.save = 1;
    else if (/\/status\//.test(href)) patch.thread_open = 1;
    else if (/^https?:\/\/(x|twitter)\.com\/[^/?#]+\/?$/.test(href)) patch.profile_open = 1;
    else if (href && !/\/status\//.test(href)) patch.link_click = 1;
    if (Object.keys(patch).length) send({ type: 'EVENTS', events: [eventFor(item.item_id, patch)] });
  }, true);

  document.addEventListener('visibilitychange', () => { if (document.hidden) send({ type: 'FLUSH' }); });
  setInterval(scan, 1000);
  setInterval(() => refreshStateAndRank(false), 8000);
  chrome.storage.onChanged.addListener(() => refreshStateAndRank(false));
  scan();
  refreshStateAndRank(false);
})();

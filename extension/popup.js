const send = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));

async function load() {
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
  const m3El = document.getElementById('m3-ambient');
  if (m3El) {
    if (mode === 'm3') {
      m3El.style.display = 'block';
      const s = state.feedStatus;
      if (!s) {
        m3El.textContent = online ? 'M3 ambient: no status yet (scoring may be pending)' : 'M3 ambient: core offline';
      } else {
        const seen = s.total_seen_count ?? '—';
        const active = s.candidate_count ?? '—';
        const expired = s.expired_count ?? '—';
        const phase = s.phase ?? s.epoch_status ?? '—';
        const topReady = s.top_ready ? 'yes' : 'no';
        const topK = s.top_k ?? 10;
        const scored = s.scored_count ?? '—';
        const pending = s.unscored_count ?? '—';
        const m3 = s.m3_status ?? 'idle';
        m3El.textContent = `M3 · phase ${phase} · seen ${seen} · active ${active} · expired ${expired} · top ${topK} ready ${topReady} · ${scored} scored · ${pending} pending · ${m3}`;
      }
    } else {
      m3El.style.display = 'none';
      m3El.textContent = '';
    }
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

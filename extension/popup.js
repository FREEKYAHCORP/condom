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
  document.getElementById('status').textContent = JSON.stringify({ queues: state.queues, minimax: state.health?.minimax_key_present }, null, 2);
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
  const res = await send({ type: 'RANK', mode, refresh: true });
  document.getElementById('status').textContent = JSON.stringify({ arm: res.arm, effective: res.effective_arm, n: res.items?.length, model_calls: res.model_calls }, null, 2);
});

document.getElementById('flush').addEventListener('click', async () => {
  await send({ type: 'FLUSH' });
  await load();
});

load();

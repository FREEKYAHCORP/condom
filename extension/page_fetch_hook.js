(() => {
  if (window.__lensM0FetchHookInstalled) return;
  window.__lensM0FetchHookInstalled = true;

  const MAX_BODY_CHARS = 4_000_000;
  const isInteresting = (url) => {
    try {
      const u = new URL(String(url), location.href);
      if (!/(^|\.)x\.com$|(^|\.)twitter\.com$/.test(u.hostname)) return false;
      return u.pathname.includes('/i/api/graphql') || u.pathname.includes('/i/api/');
    } catch {
      return false;
    }
  };

  const postBody = (url, status, bodyText) => {
    if (!bodyText || bodyText.length > MAX_BODY_CHARS) return;
    window.postMessage({
      source: 'lens-m0-page-hook',
      type: 'raw-response',
      url: String(url),
      status,
      bodyText,
      capturedAt: new Date().toISOString(),
    }, '*');
  };

  const nativeFetch = window.fetch;
  window.fetch = async function lensFetch(input, init) {
    const response = await nativeFetch.apply(this, arguments);
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (isInteresting(url)) {
      try {
        response.clone().text().then((text) => postBody(url, response.status, text)).catch(() => {});
      } catch {}
    }
    return response;
  };

  const NativeXHR = window.XMLHttpRequest;
  function LensXHR() {
    const xhr = new NativeXHR();
    let url = '';
    const open = xhr.open;
    xhr.open = function(method, requestUrl) {
      url = String(requestUrl || '');
      return open.apply(xhr, arguments);
    };
    xhr.addEventListener('load', () => {
      if (!isInteresting(url)) return;
      try {
        if (typeof xhr.responseText === 'string') postBody(url, xhr.status, xhr.responseText);
      } catch {}
    });
    return xhr;
  }
  window.XMLHttpRequest = LensXHR;
})();

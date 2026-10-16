// روز جملة: keeps the trader page openable without internet so the shortages notebook works offline.
// Pages: network first, cached copy when offline. Static files: cached copy first, refreshed in the background.
// API calls are never cached here; the page keeps its own offline data.
const CACHE = 'rose-wholesale-v1';
const SHELL = ['/wholesale/', '/static/icon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('rose-wholesale-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isStatic(url) {
  return url.origin === self.location.origin && (url.pathname.startsWith('/static/wholesale/trader/') || url.pathname === '/static/icon.svg' || url.pathname === '/api/branding/logo' || url.pathname.startsWith('/api/wholesale/images/'))
    || url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com';
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (req.mode === 'navigate' && url.pathname.startsWith('/wholesale')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put('/wholesale/', copy)); }
          return res;
        })
        .catch(() => caches.match('/wholesale/').then((r) => r || Response.error())),
    );
    return;
  }
  if (isStatic(url)) {
    event.respondWith(
      caches.open(CACHE).then((c) => c.match(req).then((hit) => {
        const fresh = fetch(req).then((res) => { if (res.ok || res.type === 'opaque') c.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || fresh;
      })),
    );
  }
});

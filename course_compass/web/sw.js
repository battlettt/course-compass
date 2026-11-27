// Minimal service worker: makes the hub installable and keeps static assets available offline.
// The page itself (it carries the per-launch API token) and every /api, /feed and /files response
// are never cached.
const CACHE = 'course-compass-v1';
const ASSETS = ['/app.css', '/app.js', '/icon.svg', '/vendor/katex/katex.min.js', '/vendor/katex/katex.min.css'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname === '/' || url.pathname === '/index.html' || /^\/(api|feed|files)\//.test(url.pathname)) return;
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request)));
});

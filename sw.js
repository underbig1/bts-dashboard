const CACHE_PREFIX = 'bts-install-shell-';
const CACHE_NAME = CACHE_PREFIX + 'v1';
const BASE = new URL('./', self.location.href);
const ASSETS = ['index.html', 'bts-icon.png'];
const ASSET_URLS = ASSETS.map(path => new URL(path, BASE).href);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSET_URLS)));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  // The live Streamlit dashboard and its data are never cached by this shell.
  if (request.method !== 'GET' || url.origin !== BASE.origin || !url.pathname.startsWith(BASE.pathname)) return;
  const isHome = request.mode === 'navigate' && (url.pathname === BASE.pathname || url.pathname === BASE.pathname + 'index.html');
  const isAsset = ASSET_URLS.includes(url.href);
  if (!isHome && !isAsset) return;
  const cacheKey = isHome ? ASSET_URLS[0] : request;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      const response = await fetch(request);
      if (response.ok) await cache.put(cacheKey, response.clone());
      return response;
    } catch (error) {
      const cached = await cache.match(cacheKey);
      if (cached) return cached;
      throw error;
    }
  })());
});

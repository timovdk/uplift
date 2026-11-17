// Offline support.
// - App shell and station list: served from the cache straight away and refreshed in the
//   background (stale-while-revalidate), so a new version shows up on the next launch.
// - data/lifts.json: network first, because an old status is worse than a slow one. Falls back
//   to the cached copy when offline; the app then shows how old it is.
// When the background refresh finds a changed file, open pages get a message and show a
// "Herladen" banner (same as Clawback).
// Bump VERSION when the list of files below changes. The deploy adds the commit id to it, so
// every release is a new service worker, which always shows the "Herladen" banner.

const VERSION = 'v7';
const SHELL = `lift-shell-${VERSION}`;
const STATUS = 'lift-status';

const SHELL_FILES = [
  './',
  'index.html',
  'style.css',
  'manifest.webmanifest',
  'icon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/apple-touch-icon.png',
  'vendor/mithril.min.js',
  'vendor/mithril.LICENSE',
  'js/app.js', 'js/config.js',
  'js/lib.js',
  'js/mascot.js',
  'js/messages.js',
  'js/storage.js',
  'data/stations.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES.map((f) => new Request(f, { cache: 'reload' })))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith('lift-shell-') && k !== SHELL).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET') return;

  // The status: data/lifts.json on this site, or lifts.json in the R2 bucket (js/config.js).
  if (url.pathname.endsWith('/lifts.json')) {
    event.respondWith(networkFirst(request));
    return;
  }
  // The station list from the R2 bucket: like the app files, but a new list just shows up on the
  // next launch (it isn't a new version of the app, so no "Herladen" banner).
  if (url.origin !== self.location.origin) {
    if (url.pathname.endsWith('/stations.json')) event.respondWith(staleWhileRevalidate(request.url, request, event, false));
    return;
  }
  const key = request.mode === 'navigate' ? 'index.html' : request;
  event.respondWith(staleWhileRevalidate(key, request, event));
});

async function networkFirst(request) {
  const cache = await caches.open(STATUS);
  try {
    const res = await fetch(request, { cache: 'no-cache' });
    if (res.ok) await cache.put(request.url, res.clone());
    return res;
  } catch {
    const hit = await cache.match(request.url);
    return hit || new Response('Offline', { status: 503, statusText: 'Offline' });
  }
}

/** Did a file really change? Cheap header check first, then compare the content. */
async function changed(cached, fresh) {
  const tag = (r) => r.headers.get('etag') || r.headers.get('last-modified') || '';
  if (tag(cached) && tag(cached) === tag(fresh)) return false;
  const [a, b] = await Promise.all([cached.clone().arrayBuffer(), fresh.clone().arrayBuffer()]);
  if (a.byteLength !== b.byteLength) return true;
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return true;
  return false;
}

/** Tell open pages that a newer version is stored; they show a "reload" banner. */
async function notifyUpdate() {
  for (const client of await self.clients.matchAll({ type: 'window' })) client.postMessage({ type: 'update-available' });
}

async function staleWhileRevalidate(key, request, event, notify = true) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(key, { ignoreSearch: request.mode === 'navigate' });
  const update = fetch(request, { cache: 'no-cache' })
    .then(async (res) => {
      if (res.ok) {
        if (notify && hit && await changed(hit, res)) notifyUpdate();
        await cache.put(key, res.clone());
      }
      return res;
    })
    .catch(() => null);
  if (hit) {
    event.waitUntil(update);
    return hit;
  }
  return (await update) || new Response('Offline', { status: 503, statusText: 'Offline' });
}

// PracticeLoop's PWA support (Phase 16). Scoped honestly: this app is
// entirely server-rendered (FastAPI + Jinja), not a bundled SPA, so a
// real "works fully offline" experience isn't architecturally realistic
// here -- every page is a genuine round trip to the server. What this
// actually does:
//   1. Cache-first for the handful of static assets under /static/ (CSS,
//      manifest, icon) -- faster repeat loads, and they survive being
//      briefly offline.
//   2. A dedicated offline fallback page for navigation requests that
//      fail outright (no connection at all), instead of the browser's
//      own generic "no internet" error screen.
//   3. Everything else (every dynamic page, every form POST, every API
//      call) passes straight through to the network, unmodified -- no
//      attempt to fake functionality that genuinely needs the server.

const CACHE_NAME = "practiceloop-shell-v1";
const SHELL_ASSETS = ["/static/style.css", "/static/manifest.json", "/static/icon.svg", "/static/offline.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/static/offline.html"))
    );
  }
  // Everything else: no interception, request goes straight to the network.
});

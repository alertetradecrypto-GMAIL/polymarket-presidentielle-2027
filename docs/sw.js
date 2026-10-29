/* Service worker : appli installable + repli hors ligne.
 * - Pages, scripts et data/*.json du site : réseau d'abord, cache en secours (toujours à jour en ligne).
 * - Lightweight Charts (version figée sur unpkg) : cache d'abord.
 * - API externes (Polymarket, RPC, change) : non interceptées. */
const VERSION = "v1";
const SHELL = `shell-${VERSION}`;
const LIBS = `libs-${VERSION}`;
const PRECACHE = [
  "./", "index.html", "trades.html", "portfolio.html", "style.css", "pwa.js",
  "app.js", "trades.js", "portfolio.js", "manifest.webmanifest", "icons/icon-192.png",
];
const LIB_HOST = "unpkg.com";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== SHELL && k !== LIBS) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (url.hostname === LIB_HOST) {
    e.respondWith(caches.open(LIBS).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok || res.type === "opaque") c.put(req, res.clone());
      return res;
    }));
    return;
  }

  if (url.origin !== self.location.origin || !url.pathname.startsWith(new URL(self.registration.scope).pathname)) return;

  e.respondWith((async () => {
    const c = await caches.open(SHELL);
    try {
      const res = await fetch(req, { cache: "no-cache" });
      if (res.ok) c.put(req, res.clone());
      return res;
    } catch {
      return (await c.match(req, { ignoreSearch: true })) ||
        (req.mode === "navigate" ? await c.match("index.html") : Response.error());
    }
  })());
});

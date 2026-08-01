/**
 * Service Worker — cache offline pour les assets statiques.
 *
 * Stratégie :
 * - Network-first pour les pages HTML (toujours fraîches si online)
 * - Network-first pour les bundles `/_next/` (voir ci-dessous)
 * - Cache-first pour les vrais assets statiques (fonts, images, manifest)
 * - Pas de cache pour /api/* (données toujours fraîches)
 *
 * Pourquoi `/_next/` n'est PAS en cache-first :
 * en développement les chunks ne sont pas hashés (`/_next/static/chunks/app/
 * bots-v2/page.js` garde le même nom à chaque édition). Le cache-first les
 * figeait définitivement : le navigateur continuait de servir l'ancien bundle
 * malgré un redémarrage du serveur et la suppression de `.next`. Le même piège
 * s'appliquait après un déploiement pour tout client ayant déjà visité le site :
 * il restait sur l'UI précédente. En production, `/_next/static/` est déjà servi
 * avec `Cache-Control: immutable` — le cache HTTP fait le travail, le Service
 * Worker n'apportait rien de plus que le risque.
 *
 * `CACHE_NAME` doit être incrémenté à chaque changement de stratégie : le
 * handler `activate` purge alors les caches aux anciens noms.
 *
 * `STATIC_ASSETS` liste des URL servies en 200 direct, pas des redirections.
 * Depuis S10, `/` et `/dashboard` renvoient un 308 vers `/portfolio-v2` (cf.
 * `redirects()` dans next.config.mjs) : `addAll` suivait la redirection et
 * stockait le HTML de `/portfolio-v2` sous deux clés qui ne sont jamais
 * redemandées, au prix d'un aller-retour supplémentaire à l'installation.
 * On précache donc la cible réelle. Le repli hors ligne du handler HTML suit
 * la même clé.
 */

const CACHE_NAME = 'crypto-bot-v3';
const STATIC_ASSETS = [
  '/portfolio-v2',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return;
  if (url.pathname.startsWith('/_next/webpack-hmr')) return;

  // Bundles applicatifs : toujours le réseau, avec repli sur le cache hors ligne.
  if (url.pathname.startsWith('/_next/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok && response.type === 'basic') {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => caches.match(request)),
    );
    return;
  }

  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match('/portfolio-v2')))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok && response.type === 'basic') {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
        }
        return response;
      });
    })
  );
});

// Nivixsa PWA Service Worker
const CACHE_NAME = 'nivixsa-v3';

// Assets to cache for offline shell
const PRECACHE_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/logo_new.png',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png'
];

// Install: cache core shell assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Fetch: network-first strategy for pages, cache-first for static assets
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET requests (socket.io, POST, etc.)
  if (event.request.method !== 'GET') return;

  // Skip socket.io and API calls
  if (url.pathname.startsWith('/socket.io')) return;

  // Static assets: cache-first (fast loads)
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return cached || fetch(event.request).then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        });
      })
    );
    return;
  }

  // HTML pages: network-first (always fresh), fall back to cache
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then((cached) => {
          return cached || new Response('<h1>Nivixsa is Offline</h1><p>Please check your internet connection.</p>', {
            headers: { 'Content-Type': 'text/html' }
          });
        });
      })
  );
});

// Push notification listener
self.addEventListener('push', (event) => {
  let data = { title: 'Nivixsa Update', body: 'System automation event triggered' };

  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data = { title: 'Nivixsa Update', body: event.data.text() };
    }
  }

  const options = {
    body: data.body || data.message,
    icon: data.icon || (self.location.origin + '/static/icons/icon-192x192.png'),
    badge: data.badge || (self.location.origin + '/static/icons/icon-72x72.png'),
    tag: data.tag || 'nivixsa-notification',
    renotify: true,
    data: data.url || '/',
    vibrate: [200, 100, 200]
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Notification click handler (opens the PWA app or focuses it)
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = event.notification.data || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // Check if there is already a window open with this app
      for (let i = 0; i < windowClients.length; i++) {
        const client = windowClients[i];
        if (client.url.includes(targetUrl) && 'focus' in client) {
          return client.focus();
        }
      }
      // If no window is open, open a new one
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});

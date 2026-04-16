/**
 * MedSimulation Service Worker
 * Provides offline support, caching, and install prompt
 */

const CACHE_NAME = 'medsimulation-v1';
const STATIC_CACHE = 'medsimulation-static-v1';
const API_CACHE = 'medsimulation-api-v1';

// Cache version for busting - increment when static assets change
const CACHE_VERSION = 'v3';

// Resources to cache immediately on install
const STATIC_ASSETS = [
  '/',
  '/static/styles.css?' + CACHE_VERSION,
  '/static/favicon.svg',
  '/static/manifest.json',
  '/offline',
];

// API endpoints to cache (with network-first strategy)
const API_ENDPOINTS = [
  '/api/simulation/cases',
  '/api/cases/recommended',
];

// ═══════════════════════════════════════════════════════════════════════════
// Install Event - Cache static assets
// ═══════════════════════════════════════════════════════════════════════════

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        console.log('[SW] Caching static assets');
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => {
        console.log('[SW] Installation complete, skipping waiting');
        return self.skipWaiting();
      })
      .catch((err) => {
        console.error('[SW] Install failed:', err);
      })
  );
});

// ═══════════════════════════════════════════════════════════════════════════
// Activate Event - Clean old caches
// ═══════════════════════════════════════════════════════════════════════════

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => {
              return name !== STATIC_CACHE &&
                     name !== API_CACHE &&
                     name !== CACHE_NAME;
            })
            .map((name) => {
              console.log('[SW] Deleting old cache:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => {
        console.log('[SW] Activation complete, claiming clients');
        return self.clients.claim();
      })
  );
});

// ═══════════════════════════════════════════════════════════════════════════
// Fetch Event - Network first with cache fallback
// ═══════════════════════════════════════════════════════════════════════════

// Normalize URL for cache matching - strip version query strings
function normalizeUrl(url) {
  const parsed = new URL(url);
  // Remove cache-busting query strings like ?v=3
  if (parsed.searchParams.has('v')) {
    parsed.searchParams.delete('v');
  }
  return parsed.toString();
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip chrome-extension and other non-http requests
  if (!url.protocol.startsWith('http')) {
    return;
  }

  // API requests - network first with cache fallback
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirstStrategy(request));
    return;
  }

  // Static assets - cache first with network fallback
  event.respondWith(cacheFirstStrategy(request, event));
});

// ═══════════════════════════════════════════════════════════════════════════
// Cache First Strategy (for static assets)
// ═══════════════════════════════════════════════════════════════════════════

async function cacheFirstStrategy(request, event) {
  // Try exact match first, then normalized URL (for versioned assets)
  let cachedResponse = await caches.match(request);

  if (!cachedResponse) {
    const normalizedUrl = normalizeUrl(request.url);
    if (normalizedUrl !== request.url) {
      cachedResponse = await caches.match(normalizedUrl);
    }
  }

  if (cachedResponse) {
    // Return cached version, but update cache in background
    event.waitUntil(updateCache(request));
    return cachedResponse;
  }

  try {
    const networkResponse = await fetch(request);

    // Cache successful responses
    if (networkResponse.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, networkResponse.clone());
    }

    return networkResponse;
  } catch (error) {
    // If offline and it's a navigation request, serve offline page
    if (request.mode === 'navigate') {
      const offlineResponse = await caches.match('/offline');
      return offlineResponse || new Response('Offline', { status: 503 });
    }

    throw error;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Network First Strategy (for API calls)
// ═══════════════════════════════════════════════════════════════════════════

async function networkFirstStrategy(request) {
  try {
    const networkResponse = await fetch(request);

    // Cache successful API responses
    if (networkResponse.ok) {
      const cache = await caches.open(API_CACHE);
      cache.put(request, networkResponse.clone());
    }

    return networkResponse;
  } catch (error) {
    // Network failed, try cache
    const cachedResponse = await caches.match(request);

    if (cachedResponse) {
      console.log('[SW] Returning cached API response:', request.url);
      return cachedResponse;
    }

    // Return error response with offline indicator
    return new Response(
      JSON.stringify({
        error: 'offline',
        message: 'You are offline. Some features may be unavailable.'
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      }
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Background cache update
// ═══════════════════════════════════════════════════════════════════════════

async function updateCache(request) {
  try {
    const networkResponse = await fetch(request);

    if (networkResponse.ok) {
      const cache = await caches.open(STATIC_CACHE);
      await cache.put(request, networkResponse.clone());
    }
  } catch (error) {
    // Silently fail - network will be tried next time
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Push Notifications (for future use)
// ═══════════════════════════════════════════════════════════════════════════

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'MedSimulation';
  const options = {
    body: data.body || 'New notification',
    icon: '/static/icon-192.svg',
    badge: '/static/icon-192.svg',
    vibrate: [200, 100, 200],
    data: data.url || '/',
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

// ═══════════════════════════════════════════════════════════════════════════
// Notification Click Handler
// ═══════════════════════════════════════════════════════════════════════════

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  event.waitUntil(
    clients.openWindow(event.notification.data)
  );
});

// ═══════════════════════════════════════════════════════════════════════════
// Message Handler (for install prompt communication)
// ═══════════════════════════════════════════════════════════════════════════

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

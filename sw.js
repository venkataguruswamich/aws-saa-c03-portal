// Retires the offline service worker from the previous version of this site.
// The new portal doesn't use a service worker. Browsers that still have the old one
// installed fetch this file, delete the old caches, unregister, and reload once.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) await caches.delete(key);
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: 'window' })) client.navigate(client.url);
  })());
});

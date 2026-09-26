/* Minimaler Service Worker: macht die Seite installierbar („Zum Startbildschirm").
   Es wird bewusst nichts gecacht – alle Daten kommen immer vom Server. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* Netzwerk-Durchreiche */ });

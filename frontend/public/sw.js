/* Minimal service worker so the app can be installed (Add to Dock).
   Deliberately thin: no offline caching, because stale SLB quotes are worse than
   no quotes, and no web push (decided 4 Oct 2026; alerts reach the desktop via
   macOS notifications and the phone via WhatsApp). Adapted from T's sw.js. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) if ("focus" in client) return client.focus();
      return self.clients.openWindow("/");
    }),
  );
});

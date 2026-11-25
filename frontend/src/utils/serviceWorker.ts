/**
 * Register the minimal service worker that makes the app installable (Phase
 * 5.5). Best effort: returns false, never throws, when unsupported or refused.
 * No web push and no offline caching (decided 4 Oct 2026).
 */
export async function registerServiceWorker(nav: Navigator = navigator): Promise<boolean> {
  if (!nav || !("serviceWorker" in nav) || !nav.serviceWorker) return false;
  try {
    await nav.serviceWorker.register("/sw.js");
    return true;
  } catch {
    return false;
  }
}

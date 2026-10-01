/*
 * Service Worker der Marschbahn-App.
 *
 * Seiten kommen immer zuerst frisch vom Server ("network first") — die Daten
 * ändern sich alle paar Minuten, eine zwischengespeicherte Übersicht wäre sonst
 * veraltet. Nur ohne Netz wird die zuletzt geladene Übersicht gezeigt, und wenn
 * es die nicht gibt, eine Offline-Seite.
 *
 * Icons und Manifest ändern sich selten und kommen aus dem Speicher.
 *
 * Neue Version: VERSION erhöhen. Der Browser prüft sw.js bei jedem Öffnen der
 * App (siehe .htaccess, no-cache) und übernimmt eine geänderte Datei sofort.
 * icons_erzeugen.py erhöht VERSION automatisch.
 *
 * Push: Nachrichten von push.py (verschlüsselt, der Browser entschlüsselt sie) werden
 * als Benachrichtigung gezeigt; Antippen öffnet bzw. holt die App nach vorn.
 */
const VERSION = 'v39';
const CACHE = 'zugradar-' + VERSION;
// Für die Karte ohne Netz: Kartenprogramm, zuletzt geholte Daten und schon einmal
// gesehene Kartenkacheln. Eigene Speicher, damit eine neue Version sie nicht wegwirft.
const KARTE_CACHE = 'zugradar-karte';
const DATEN_CACHE = 'zugradar-daten';
const KACHEL_CACHE = 'zugradar-kacheln';
const KACHELN_MAX = 400;
const LEAFLET = [
  'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css',
];
const DATEN_PFADE = ['/zuege.php', '/netz.php', '/halte.php', '/grenzen.php', '/tafel.php'];
const KACHEL_HOSTS = ['tile.openstreetmap.org', 'basemaps.cartocdn.com', 'tile.opentopomap.org',
                      'server.arcgisonline.com', 'tiles.openrailwaymap.org'];
const STATISCH = [
  './offline.html', './manifest.webmanifest', './favicon.ico', './favicon-32.png',
  './icon-192.png', './icon-512.png', './badge-96.png', './push-client.js', './icon-maskable-512.png', './apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(Promise.all([
    caches.open(CACHE).then((c) => c.addAll(STATISCH)),
    // Leaflet gehört einem fremden Server — ohne CORS käme nur eine undurchsichtige Antwort.
    caches.open(KARTE_CACHE).then((c) => Promise.all(
      LEAFLET.map((u) => fetch(u, { mode: 'cors', credentials: 'omit' })
        .then((a) => (a.ok ? c.put(u, a) : null)).catch(() => null)))),
  ]).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((namen) => Promise.all(namen
        .filter((n) => (n.startsWith('zugradar-') || n.startsWith('marschbahn-'))
                    && ![CACHE, KARTE_CACHE, DATEN_CACHE, KACHEL_CACHE].includes(n))
        .map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const anfrage = e.request;
  const url = new URL(anfrage.url);
  if (anfrage.method !== 'GET') { return; }

  // Kartenprogramm: erst aus dem Speicher — damit die Karte auch ohne Netz aufgeht.
  if (LEAFLET.includes(url.href.split('?')[0])) {
    e.respondWith(caches.match(url.href.split('?')[0]).then((treffer) => treffer || fetch(anfrage)));
    return;
  }
  // Kartenkacheln: was man schon gesehen hat, bleibt offline sichtbar (begrenzt).
  if (KACHEL_HOSTS.some((h) => url.hostname.endsWith(h))) {
    e.respondWith(
      caches.match(anfrage).then((treffer) => treffer || fetch(anfrage).then((antwort) => {
        if (antwort.ok) {
          const kopie = antwort.clone();
          caches.open(KACHEL_CACHE).then(async (c) => {
            await c.put(anfrage, kopie);
            const schluessel = await c.keys();
            // Ältestes zuerst weg, sonst wächst der Speicher ohne Ende.
            if (schluessel.length > KACHELN_MAX) {
              await Promise.all(schluessel.slice(0, schluessel.length - KACHELN_MAX).map((k) => c.delete(k)));
            }
          });
        }
        return antwort;
      }))
    );
    return;
  }
  if (url.origin !== location.origin || !url.pathname.startsWith('/zugradar/')) {
    return;
  }
  // Kartendaten: immer frisch versuchen, ohne Netz den letzten Stand ausliefern.
  if (DATEN_PFADE.some((p) => url.pathname.endsWith(p))) {
    e.respondWith(
      fetch(anfrage).then((antwort) => {
        if (antwort.ok) {
          const kopie = antwort.clone();
          caches.open(DATEN_CACHE).then((c) => c.put(anfrage.url, kopie));
        }
        return antwort;
      }).catch(() => caches.match(anfrage.url).then((alt) => alt
        || new Response('{"fehler":"offline"}', { status: 503, headers: { 'Content-Type': 'application/json' } })))
    );
    return;
  }
  // Merk-Links immer direkt an den Server — die sollen etwas bewirken.
  if (url.pathname.endsWith('/merken.php') || url.pathname.endsWith('/push.php')) {
    return;
  }

  if (anfrage.mode === 'navigate') {
    e.respondWith(
      fetch(anfrage)
        .then((antwort) => {
          // Jede Menüseite (./, ./?s=lage …) als eigene Offline-Kopie merken.
          if (antwort.ok && (url.pathname === '/zugradar/' || url.pathname.endsWith('/index.php'))) {
            const kopie = antwort.clone();
            caches.open(CACHE).then((c) => c.put(anfrage.url, kopie));
          }
          return antwort;
        })
        // Ohne Netz: erst diese Seite aus dem Speicher, sonst die Übersicht,
        // sonst die Offline-Seite. So ist die App auch offline benutzbar.
        .catch(() => caches.match(anfrage.url)
          .then((alt) => alt || caches.match(new URL('./', location.href).href))
          .then((alt) => alt || caches.match('./offline.html'))
          .then((alt) => alt || new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } })))
    );
    return;
  }

  if (STATISCH.some((p) => url.pathname.endsWith(p.slice(1)))) {
    e.respondWith(caches.match(anfrage).then((treffer) => treffer || fetch(anfrage)));
  }
});

// ---------------------------------------------------------------- Push
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { text: e.data ? e.data.text() : '' }; }
  const o = d.optionen || {};
  // Lag der Rechner aus, schiebt der Push-Dienst Liegengebliebenes beim Einschalten nach.
  // Was seine Haltbarkeit überschritten hat ("in 30 min in Elmshorn" von heute früh), wird
  // nicht mehr einzeln gemeldet: alles Alte landet still in einer einzigen Zeile.
  if (d.gueltig_bis && Date.now() / 1000 > d.gueltig_bis) {
    e.waitUntil(self.registration.showNotification('Zugradar: ältere Meldungen', {
      body: 'Während das Gerät aus war, kam: ' + (d.titel || 'eine Meldung')
        + '. Alles steht im Verlauf.',
      icon: 'icon-192.png', badge: 'badge-96.png', tag: 'veraltet', renotify: false,
      silent: true, lang: 'de', data: { url: './?s=verlauf' },
    }));
    return;
  }
  // Live-Meldungen zu beobachteten Fahrten: gleiche Markierung ersetzt die alte Nachricht,
  // "festhalten" lässt sie liegen, "still" aktualisiert ohne Ton und Vibration.
  e.waitUntil(self.registration.showNotification(d.titel || 'Zugradar', {
    body: d.text || '',
    icon: 'icon-192.png',
    badge: 'badge-96.png',            // kleines Symbol in der Android-Statusleiste
    tag: d.tag || undefined,          // gleiche Markierung ersetzt die ältere Meldung
    renotify: d.tag ? o.ersetzen !== false : false,
    requireInteraction: !!o.festhalten,
    silent: !!o.still,
    lang: 'de',
    // Knöpfe direkt in der Meldung, z. B. "Nicht mehr beobachten".
    actions: (d.aktionen || []).slice(0, 2).map((x) => ({ action: x.id, title: x.titel })),
    data: { url: d.url || './', aktionen: d.aktionen || [] },
  }));
});

self.addEventListener('notificationclick', (e) => {
  const daten = e.notification.data || {};
  const gewaehlt = (daten.aktionen || []).find((x) => x.id === e.action);
  e.notification.close();
  // Knopf in der Meldung: Auftrag abschicken, ohne die App zu öffnen.
  if (gewaehlt) {
    e.waitUntil((async () => {
      try {
        const r = await fetch(gewaehlt.url, { credentials: 'same-origin' });
        const j = await r.json();
        await self.registration.showNotification('Zugradar', {
          body: j.text || 'Erledigt.', icon: 'icon-192.png', badge: 'badge-96.png',
          tag: (e.notification.tag || 'aktion') + '-quittung', silent: true, lang: 'de',
          data: { url: daten.url || './' },
        });
      } catch (fehler) {
        await self.clients.openWindow(gewaehlt.url + '&seite=1');
      }
    })());
    return;
  }
  const ziel = new URL(e.notification.data && e.notification.data.url || './', self.registration.scope).href;
  e.waitUntil((async () => {
    const fenster = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const f of fenster) {
      if (f.url.startsWith(self.registration.scope)) {
        await f.focus();
        if (f.url !== ziel && 'navigate' in f) { await f.navigate(ziel); }
        return;
      }
    }
    await self.clients.openWindow(ziel);
  })());
});

// Der Browser tauscht Abos gelegentlich selbst aus — dann die neue Adresse melden.
self.addEventListener('pushsubscriptionchange', (e) => {
  e.waitUntil((async () => {
    const alt = e.oldSubscription;
    const neu = e.newSubscription || (alt && await self.registration.pushManager.subscribe(alt.options));
    if (!alt || !neu) { return; }
    await fetch('push.php', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aktion: 'erneuern', alt: alt.endpoint, abo: neu.toJSON() }),
    });
  })());
});

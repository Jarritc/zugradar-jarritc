/*
 * Push-Benachrichtigungen im Browser ein- und ausschalten.
 *
 * Erwartet im <head>:  <meta name="push-vapid" content="…">  und  <meta name="csrf" content="…">
 * Aufruf:  MarschbahnPush.knopf(document.getElementById('push-bereich'))
 *
 * Das Abo liegt beim Push-Dienst des Browsers; push.php merkt sich Adresse und
 * Schlüssel, /opt/docker/kukas-zug/push.py verschickt.
 */
(function () {
  const meta = (n) => (document.querySelector('meta[name="' + n + '"]') || {}).content || '';
  const ua = navigator.userAgent;
  const ios = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const alsApp = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  function schluessel(b64) {
    const s = (b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  }

  async function senden(daten) {
    const r = await fetch('push.php', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': meta('csrf') },
      body: JSON.stringify(daten),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { throw new Error(j.fehler || ('HTTP ' + r.status)); }
    return j;
  }

  /** Vorschlag für den Gerätenamen, später auf geraete.php änderbar. */
  async function geraeteName() {
    let modell = '';
    try {
      if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
        modell = (await navigator.userAgentData.getHighEntropyValues(['model'])).model || '';
      }
    } catch (_) { /* egal */ }
    const system = /android/i.test(ua) ? 'Android' : /iphone/i.test(ua) ? 'iPhone' : ios ? 'iPad'
                 : /windows/i.test(ua) ? 'Windows' : /mac os/i.test(ua) ? 'Mac' : /linux/i.test(ua) ? 'Linux' : 'Gerät';
    const browser = /SamsungBrowser/.test(ua) ? 'Samsung Internet' : /Edg\//.test(ua) ? 'Edge'
                  : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
    return [system + (modell ? ' ' + modell : ''), alsApp ? 'App' : browser].filter(Boolean).join(' · ');
  }

  async function zustand() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      return { geht: false, grund: ios && !alsApp
        ? 'Auf iPhone und iPad gehen Benachrichtigungen nur in der installierten App: '
          + 'Teilen ⬆︎ → „Zum Home-Bildschirm“, dann die App öffnen.'
        : 'Dieser Browser kann keine Push-Benachrichtigungen.' };
    }
    if (!meta('push-vapid')) { return { geht: false, grund: 'Push ist auf dem Server noch nicht eingerichtet.' }; }
    if (Notification.permission === 'denied') {
      return { geht: false, grund: 'Benachrichtigungen sind für diese Seite blockiert — in den '
        + 'Browser- bzw. App-Einstellungen unter „Benachrichtigungen“ wieder erlauben.' };
    }
    const reg = await navigator.serviceWorker.ready;
    const abo = await reg.pushManager.getSubscription();
    if (!abo) {
      const altAktiv = (await Promise.all((await alteRegistrierungen())
        .map((r) => r.pushManager.getSubscription().catch(() => null)))).some(Boolean);
      return { geht: true, aktiv: false, reg, umzug: altAktiv };
    }
    const s = await senden({ aktion: 'status', abo: abo.toJSON() }).catch(() => ({}));
    return { geht: true, aktiv: !!s.aktiv, name: s.name, abo, reg };
  }

  async function einschalten() {
    if (Notification.permission !== 'granted') {
      const erlaubt = await Notification.requestPermission();
      if (erlaubt !== 'granted') { throw new Error('Benachrichtigungen wurden nicht erlaubt.'); }
    }
    const reg = await navigator.serviceWorker.ready;
    let abo = await reg.pushManager.getSubscription();
    // Ein Abo mit fremdem Schlüssel (z. B. nach Schlüsselwechsel) lässt sich nicht nutzen.
    if (abo && abo.options && abo.options.applicationServerKey) {
      const alt = new Uint8Array(abo.options.applicationServerKey);
      const neu = schluessel(meta('push-vapid'));
      if (alt.length !== neu.length || alt.some((b, i) => b !== neu[i])) { await abo.unsubscribe(); abo = null; }
    }
    if (!abo) {
      abo = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: schluessel(meta('push-vapid')) });
    }
    const antwort = await senden({ aktion: 'anmelden', abo: abo.toJSON(), name: await geraeteName() });
    await altesAboAufraeumen();
    return antwort;
  }

  /** Service Worker der alten Adresse /marschbahn/ (vor dem Umzug am 17.09.2026). */
  async function alteRegistrierungen() {
    const alle = await navigator.serviceWorker.getRegistrations().catch(() => []);
    return alle.filter((r) => new URL(r.scope).pathname === '/marschbahn/');
  }

  /** Nach dem Einschalten hier: das Abo der alten Adresse abmelden, sonst käme jede
   *  Nachricht doppelt. Die Abmeldung auf dem Server zuerst, dann im Browser. */
  async function altesAboAufraeumen() {
    for (const reg of await alteRegistrierungen()) {
      const alt = await reg.pushManager.getSubscription().catch(() => null);
      if (alt) {
        await senden({ aktion: 'abmelden', abo: alt.toJSON() }).catch(() => {});
        await alt.unsubscribe().catch(() => {});
      }
      await reg.unregister().catch(() => {});
    }
  }

  async function ausschalten() {
    const reg = await navigator.serviceWorker.ready;
    const abo = await reg.pushManager.getSubscription();
    if (abo) {
      await senden({ aktion: 'abmelden', abo: abo.toJSON() }).catch(() => {});
      await abo.unsubscribe();
    }
  }

  /** Knopf + Statuszeile in ein Element zeichnen. */
  async function knopf(ziel, beiAenderung) {
    if (!ziel) { return; }
    ziel.hidden = false;
    ziel.innerHTML = '<button type="button" class="app-knopf push-knopf">🔔 <span>Benachrichtigungen …</span></button>'
                   + '<div class="app-hinweis push-hinweis" hidden></div>';
    const b = ziel.querySelector('button');
    const text = b.querySelector('span');
    const hinweis = ziel.querySelector('.push-hinweis');
    const zeige = (t) => { hinweis.hidden = !t; hinweis.textContent = t || ''; };

    async function neu() {
      b.disabled = true;
      let z;
      try { z = await zustand(); } catch (e) { z = { geht: false, grund: e.message }; }
      b.disabled = !z.geht;
      b.classList.toggle('an', !!z.aktiv);
      text.textContent = !z.geht ? 'Benachrichtigungen nicht möglich'
                       : z.aktiv ? 'Benachrichtigungen an ✓' : 'Benachrichtigungen einschalten';
      zeige(!z.geht ? z.grund
          : z.aktiv ? 'Dieses Gerät: „' + (z.name || '') + '“. Antippen zum Ausschalten.'
          : z.umzug ? 'Die App ist nach /zugradar umgezogen. Benachrichtigungen kommen noch über die alte '
                      + 'Adresse — bitte hier einmal einschalten, dann wird die alte abgemeldet.'
          : '');
      b.onclick = async () => {
        b.disabled = true;
        try {
          if (z.aktiv) { await ausschalten(); } else {
            await einschalten();
          }
        } catch (e) {
          zeige('Hat nicht geklappt: ' + e.message);
          b.disabled = false;
          return;
        }
        await neu();
        if (!z.aktiv) { zeige('Eingeschaltet — eine Begrüßung kommt in wenigen Sekunden.'); }
        if (beiAenderung) { beiAenderung(); }
      };
    }
    await neu();
  }

  window.ZugradarPush = { knopf, zustand, einschalten, ausschalten };
})();

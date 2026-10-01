<?php
/**
 * Gemeinsamer Rahmen aller Zugradar-Seiten: Kopf, Menü, Stil, Fuß.
 *
 * Menü: auf dem Handy als Leiste unten (wie in einer App, mit dem Daumen erreichbar),
 * ab 900 px Breite oben in der Kopfleiste. Eingebunden von index.php und geraete.php,
 * nie direkt aufrufbar (.htaccess).
 */
declare(strict_types=1);

/** Menüpunkte: Schlüssel => [Beschriftung, Adresse, SVG-Pfade (24×24, Linie)]. */
const MENUE = [
    'start' => ['Start', './',
        '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>'],
    'lage'  => ['Live', './?s=lage',
        '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>'],
    'loks'  => ['Loks', './?s=loks',
        '<rect x="5" y="3" width="14" height="13" rx="3"/><path d="M5 9.5h14M8.5 16 6 21M15.5 16 18 21M7.5 19h9"/><circle cx="9" cy="12.8" r=".6"/><circle cx="15" cy="12.8" r=".6"/>'],
    'tage'  => ['Tage', './?s=tage',
        '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>'],
    'mehr'  => ['Mehr', './?s=mehr',
        '<path d="M4 6h16M4 12h16M4 18h16"/>'],
];

function menue_icon(string $pfade): string {
    return '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" '
         . 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' . $pfade . '</svg>';
}

function menue_links(string $aktiv, string $klasse): string {
    $html = '';
    foreach (MENUE as $schluessel => [$text, $adresse, $pfade]) {
        $ist = $schluessel === $aktiv;
        $html .= '<a class="' . $klasse . ($ist ? ' aktiv' : '') . '" href="' . $adresse . '"'
               . ($ist ? ' aria-current="page"' : '') . '>' . menue_icon($pfade)
               . '<span>' . $text . '</span></a>';
    }
    return $html;
}

/**
 * Kopf bis zum Beginn des Inhalts.
 * $titel      Seitenname in Kopfleiste und Tab ("Lage" → "Lage · Zugradar"); leer auf der Startseite
 * $aktiv      Schlüssel aus MENUE, der hervorgehoben wird
 * $zusatzCss  seitenspezifische Regeln
 */
function seiten_kopf(string $titel, string $aktiv, string $zusatzCss = '', string $vapid = ''): void {
    // "Über Zugradar · Zugradar" wäre doppelt gemoppelt.
    $tab = $titel === '' || str_contains($titel, 'Zugradar') ? ($titel ?: 'Zugradar') : $titel . ' · Zugradar';
    ?><!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#8f2029">
<title><?= htmlspecialchars($tab) ?></title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Zugradar">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="csrf" content="<?= htmlspecialchars((string)($_SESSION['csrf'] ?? '')) ?>">
<meta name="erzeugt" content="<?= date('Y-m-d H:i') ?>">
<meta name="push-vapid" content="<?= htmlspecialchars($vapid) ?>">
<style>
<?= grundstil() ?>
<?= seiten_stil() ?>
<?= $zusatzCss ?>
</style>
<script>
// Startbildschirm nur beim Öffnen der App und nur einmal je Sitzung — beim Blättern
// zwischen den Seiten wäre er lästig. Ohne JavaScript bleibt er ganz weg.
(function () {
  var wurzel = document.documentElement;
  var dauer = 1600;                 // Voreinstellung in Millisekunden
  var jedesMal = false;
  try {
    var gespeichert = localStorage.getItem('zr-start-dauer');
    if (gespeichert !== null) { dauer = parseInt(gespeichert, 10) || 0; }
    jedesMal = localStorage.getItem('zr-start-immer') === '1';
    if (localStorage.getItem('zr-animationen') === 'aus') { wurzel.dataset.motion = 'aus'; }
  } catch (e) { /* privater Modus: dann die Voreinstellung */ }
  wurzel.style.setProperty('--splash-dauer', dauer + 'ms');
  try {
    var alsApp = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    var neu = jedesMal || !sessionStorage.getItem('zr-start');
    if (dauer > 0 && (alsApp || jedesMal) && neu) {
      sessionStorage.setItem('zr-start', '1');
      wurzel.dataset.splash = 'an';
    }
  } catch (e) { /* ohne Speicher eben ohne Startbild */ }
})();
</script>
</head><body>

<div class="start-schirm" aria-hidden="true">
  <img src="icon-192.png" alt="" width="96" height="96">
  <div class="name">Zugradar</div>
  <div class="gleis"></div>
</div>
<div class="fortschritt" id="fortschritt"></div>

<header class="topbar"><div class="inner">
  <a class="marke-link" href="./">
    <img class="logo" src="icon-192.png" alt="" width="36" height="36">
    <span class="kopftitel"><span class="app">Zugradar</span><?php if ($titel !== ''): ?><span class="seite"><?= htmlspecialchars($titel) ?></span><?php endif; ?></span>
  </a>
  <span class="offline-marke" id="offline-marke" hidden>
    <span class="punkt"></span><span class="text">Offline</span></span>
  <nav class="topnav" aria-label="Menü"><?= menue_links($aktiv, 'topnav-link') ?></nav>
</div></header>

<main class="wrap">
<?php
}

/**
 * Versionsnummer der App: kommt aus der VERSION in sw.js ('v18' -> "1.18"). Die wird bei
 * jeder Änderung ohnehin erhöht, damit das Handy die neue Fassung lädt — so stimmen
 * Anzeige und ausgelieferte Fassung immer überein. Stand = letzte Änderung an sw.js.
 */
function zugradar_version(): array {
    static $v = null;
    if ($v === null) {
        $datei = __DIR__ . '/sw.js';
        $nr = preg_match("/const VERSION = 'v(\d+)'/", (string)@file_get_contents($datei), $m) ? (int)$m[1] : 0;
        $v = ['nummer' => '1.' . $nr, 'stand' => date('d.m.Y', (int)@filemtime($datei) ?: time())];
    }
    return $v;
}

/** Fuß: Menüleiste unten, Service Worker, optional seitenspezifisches Skript. */
function seiten_fuss(string $aktiv, string $skript = ''): void {
    ?>
<footer class="seitenfuss">
  <div class="zeile"><span class="marke">Zugradar</span> ·
    <a href="./?s=info" title="Stand <?= zugradar_version()['stand'] ?>">Version <?= zugradar_version()['nummer'] ?></a></div>
  <div class="zeile">Daten: <a href="https://www.drehscheibe-online.de/foren/list.php?006" target="_blank"
      rel="noopener">Drehscheibe-Online</a>, Fahrplan und Echtzeit der Deutschen Bahn,
    Wagenreihung über <a href="https://dbf.finalrewind.org/" target="_blank" rel="noopener">dbf</a>,
    Karte über <a href="https://transitous.org" target="_blank" rel="noopener">transitous</a>
    (<a href="https://transitous.org/sources/" target="_blank" rel="noopener">deren Quellen</a>)
    und <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>.
    Alle Angaben ohne Gewähr.</div>
  <div class="zeile">&copy; <?= date('Y') ?> <a href="https://jarritc.de">jarritc.de</a> ·
    privat und nicht öffentlich · <a href="./?logout">Abmelden</a></div>
</footer>
</main>

<nav class="tabbar" aria-label="Menü"><?= menue_links($aktiv, 'tab') ?></nav>

<div class="wr-hinter" id="wr-hinter" role="dialog" aria-modal="true" aria-labelledby="wr-titel">
  <div class="wr-fenster">
    <div class="wr-kopf">
      <div><h3 id="wr-titel">Wagenreihung</h3><div class="wr-neben" id="wr-neben"></div></div>
      <button class="wr-zu" type="button" aria-label="Schließen">×</button>
    </div>
    <div class="wr-inhalt" id="wr-inhalt"></div>
    <div class="wr-quelle" id="wr-quelle"></div>
  </div>
</div>
<script>
/* Wagenreihung: Knopf öffnet ein Fenster, die Daten holt wagenreihung.php.
   Der Zug wird waagerecht gezeichnet, Abschnitt A links — die Prozentwerte kommen
   von der Quelle, dadurch stehen die Wagen über den richtigen Abschnitten. */
(function () {
  const hinter = document.getElementById('wr-hinter');
  if (!hinter) { return; }
  const inhalt = document.getElementById('wr-inhalt');
  const titel = document.getElementById('wr-titel');
  const neben = document.getElementById('wr-neben');
  const quelle = document.getElementById('wr-quelle');
  let letzterKnopf = null;

  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function zu() {
    hinter.removeAttribute('open');
    document.body.style.overflow = '';
    if (letzterKnopf) { letzterKnopf.focus(); }
  }
  hinter.addEventListener('click', (e) => { if (e.target === hinter) { zu(); } });
  hinter.querySelector('.wr-zu').addEventListener('click', zu);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && hinter.hasAttribute('open')) { zu(); } });

  function zeichne(d) {
    const wagen = d.wagen || [];
    const abschnitte = d.abschnitte || [];
    // dbf zeichnet senkrecht (0 % oben = letzter Abschnitt). Waagerecht steht A links.
    const links = (o) => 100 - o.bis;
    const breit = (o) => Math.max(o.bis - o.von, 1);
    const fach = (w) => {
      const mitte = (w.von + w.bis) / 2;
      const a = abschnitte.find((x) => mitte >= x.von && mitte <= x.bis);
      return a ? a.name : '';
    };
    const klasse = (w) => w.lok ? ' lok' : (w.erste_klasse ? ' erste' : '');

    const steig = ['<div class="wr-gleisband"></div>']
      .concat(abschnitte.map((a) => '<div class="wr-abschnitt" style="left:' + links(a)
        + '%;width:' + breit(a) + '%">' + esc(a.name) + '</div>'))
      .concat(wagen.map((w) => '<div class="wr-wagen' + klasse(w) + '" style="left:' + links(w)
        + '%;width:' + breit(w) + '%" title="' + esc((w.bezeichnung || w.typ)
        + (fach(w) ? ' · Abschnitt ' + fach(w) : '')) + '"></div>'));

    const pfeil = (wagen.find((w) => w.pfeil) || {}).pfeil;
    const richtung = pfeil === 'hoch' ? '→ Fahrtrichtung nach rechts'
                   : pfeil === 'runter' ? '← Fahrtrichtung nach links' : '';
    const lok = wagen.find((w) => w.lok);
    const ersterInFahrt = pfeil === 'runter' ? wagen[wagen.length - 1] : wagen[0];
    const lokVorn = lok && ersterInFahrt && lok === ersterInFahrt;

    inhalt.innerHTML =
      (d.ziel || d.gleis ? '<div class="wr-ziel">' + (d.ziel ? 'nach ' + esc(d.ziel) : '')
          + (d.gleis ? ' · <span class="wr-gleis">Gleis ' + esc(d.gleis) + '</span>' : '') + '</div>' : '')
      + (lok ? '<div class="wr-zusammen">🚂 ' + esc(lok.bezeichnung || 'Lok')
          + (fach(lok) ? ' hält in Abschnitt ' + esc(fach(lok)) : '')
          + (lokVorn ? ' — vorne' : (wagen.length > 1 ? ' — hinten' : '')) + '</div>'
        : '<div class="wr-zusammen">' + esc(d.zusammenfassung || 'Zug ohne erkennbare Lok (Triebwagen)') + '</div>')
      + '<div class="wr-steig">' + steig.join('') + '</div>'
      + (richtung ? '<div class="wr-richtung">' + esc(richtung) + ' · Abschnitte A bis '
          + esc((abschnitte[0] || {}).name || '') + '</div>' : '')
      + '<ul class="wr-liste">' + wagen.map((w) =>
          '<li class="' + (w.lok ? 'lok' : '') + '"><span class="wr-fach">' + esc(fach(w) || '·') + '</span>'
          + '<span class="wr-was"><b>' + esc(w.bezeichnung || w.typ) + '</b>'
          + (w.typ && (w.bezeichnung || w.typ) !== w.typ ? ' <span class="dim">' + esc(w.typ) + '</span>' : '')
          + (w.erste_klasse ? ' <span class="dim">· 1. Klasse</span>' : '')
          + (w.barrierefrei ? ' ♿' : '') + (w.bistro ? ' 🍴' : '')
          + (w.geschlossen ? ' <span class="dim">· gesperrt</span>' : '')
          + '</span></li>').join('') + '</ul>';
  }

  async function oeffne(knopf) {
    const daten = JSON.parse(knopf.dataset.wr);
    letzterKnopf = knopf;
    titel.textContent = daten.titel || 'Wagenreihung';
    neben.textContent = daten.neben || '';
    quelle.innerHTML = 'Quelle: <a href="' + esc(daten.url) + '" target="_blank" rel="noopener noreferrer">'
      + 'dbf.finalrewind.org</a> · Angaben ohne Gewähr.';
    inhalt.innerHTML = '<div class="wr-lade">Wagenreihung wird geholt …'
      + '<div class="balken"></div><div class="balken"></div><div class="balken"></div></div>';
    hinter.setAttribute('open', '');
    document.body.style.overflow = 'hidden';
    hinter.querySelector('.wr-zu').focus();
    try {
      const r = await fetch('wagenreihung.php?tt=' + encodeURIComponent(daten.tt)
        + '&tn=' + encodeURIComponent(daten.tn) + '&dt=' + encodeURIComponent(daten.dt),
        { credentials: 'same-origin' });
      const j = await r.json();
      if (j.status === 'fertig' && j.daten) {
        zeichne(j.daten);
        quelle.innerHTML += ' Stand ' + esc(j.stand) + ' Uhr.';
      } else {
        inhalt.innerHTML = '<div class="wr-fehler">' + esc(j.fehler
          || 'Für diese Fahrt liegt keine Wagenreihung vor.') + '</div>';
      }
    } catch (e) {
      inhalt.innerHTML = '<div class="wr-fehler">Keine Verbindung: ' + esc(e.message) + '</div>';
    }
  }

  // ---- Lok-Steckbrief im selben Fenster
  function zeichneLok(d) {
    const lokNr = d.lok || titel.textContent;
    const zeile = (z, t) => '<li><span class="lk-zeit">' + esc(z) + '</span><span>' + t + '</span></li>';
    const herkunft = (q) => q === 'shuttle' ? ' <span class="etikett quelle-shuttle">SyltShuttle</span>'
                          : (q === 'regio' ? ' <span class="etikett quelle-regio">RE6</span>' : '');
    const fahrt = (f) => esc(f.zug) + herkunft(f.quelle)
      + ' <span class="dim">' + esc(f.von_halt) + ' → ' + esc(f.nach_halt) + '</span>';
    const b = d.besonders || {};
    const teile = [];
    if (b.lackierung) { teile.push(esc(b.lackierung)); }
    if (b.bemerkung) { teile.push(esc(b.bemerkung)); }
    if (b.betreiber) { teile.push(esc(b.betreiber)); }
    if (b.zustand) { teile.push(esc(b.zustand)); }

    inhalt.innerHTML =
      '<div class="lk-kopf">'
      + (d.name ? '<span class="etikett gut">„' + esc(d.name) + '“</span>' : '')
      + (b.lackierung ? '<span class="etikett">' + esc(b.lackierung) + '</span>' : '')
      + (d.standort ? '<span class="etikett">zuletzt in ' + esc(d.standort.ort) + '</span>' : '')
      + '</div>'
      + (teile.length ? '<div class="wr-ziel">' + teile.join(' · ') + '</div>' : '')
      + '<div class="lk-zahlen">'
      + '<div class="lk-zahl"><b>' + d.zahlen.elmshorn_gesamt + '</b><span>Mal durch Elmshorn</span></div>'
      + '<div class="lk-zahl"><b>' + d.zahlen.elmshorn_30_tage + '</b><span>davon 30 Tage</span></div>'
      + '<div class="lk-zahl"><b>' + d.zahlen.einsatztage + '</b><span>Einsatztage</span></div>'
      + '</div>'
      // Von überall erreichbar: die Lok auf der Karte suchen, alle ihre Fahrten zeigen
      + '<div class="lk-aktionen">'
      + '<a class="knopf-klein haupt" href="./?s=karte&amp;lok=' + encodeURIComponent(lokNr) + '">🗺️ Auf der Karte</a>'
      + '<a class="knopf-klein" href="./?s=tage&amp;lok=' + encodeURIComponent(lokNr) + '">📅 Alle Fahrten</a>'
      + '</div>'
      + (d.heute && d.heute.length
          ? '<div class="lk-titel">Heute</div><ul class="lk-liste">'
            + d.heute.map((f) => zeile((f.von_zeit || '') + '–' + (f.nach_zeit || ''), fahrt(f))).join('') + '</ul>'
          : '<div class="lk-titel">Heute</div><div class="wr-ziel">Keine Fahrten in den Tageslisten.</div>')
      + (d.naechste && d.naechste.length
          ? '<div class="lk-titel">Nächste Durchfahrten in Elmshorn</div><ul class="lk-liste">'
            + d.naechste.map((f) => zeile(datum(f.tag) + ' ' + (f.elmshorn_zeit || ''),
                esc(f.zug) + herkunft(f.quelle)
                + (f.gleis ? ' <span class="dim">Gleis ' + esc(f.gleis) + '</span>' : '')
                + ' <span class="dim">' + esc(f.richtung || '') + '</span>')).join('') + '</ul>'
          : '')
      + (d.letzte && d.letzte.length
          ? '<div class="lk-titel">Zuletzt durch Elmshorn</div><ul class="lk-liste">'
            + d.letzte.map((f) => zeile(datum(f.tag) + ' ' + (f.elmshorn_zeit || ''),
                esc(f.zug) + herkunft(f.quelle)
                + ' <span class="dim">' + esc(f.richtung || '') + '</span>')).join('') + '</ul>'
          : '');
  }

  function datum(iso) {
    const t = new Date(iso + 'T12:00:00');
    return ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'][t.getDay()] + ' ' + t.getDate() + '.' + (t.getMonth() + 1) + '.';
  }

  async function oeffneLok(knopf) {
    const lok = knopf.dataset.lok;
    letzterKnopf = knopf;
    titel.textContent = lok;
    neben.textContent = 'Steckbrief';
    quelle.innerHTML = 'Fahrten aus den Tageslisten im Forum, Angaben zur Lok aus Wikipedia.';
    inhalt.innerHTML = '<div class="wr-lade">Wird geladen …'
      + '<div class="balken"></div><div class="balken"></div><div class="balken"></div></div>';
    hinter.setAttribute('open', '');
    document.body.style.overflow = 'hidden';
    hinter.querySelector('.wr-zu').focus();
    try {
      const r = await fetch('lok.php?lok=' + encodeURIComponent(lok), { credentials: 'same-origin' });
      const j = await r.json();
      if (j.fehler) { throw new Error(j.fehler); }
      neben.textContent = j.name ? '„' + j.name + '“' : 'Steckbrief';
      zeichneLok(j);
    } catch (e) {
      inhalt.innerHTML = '<div class="wr-fehler">' + esc(e.message) + '</div>';
    }
  }

  // ---- Abfahrtstafel eines Bahnhofs (Karte: Halt antippen)
  function zeichneTafel(d) {
    const zeilen = (d.fahrten || []).map((f) => {
      const spaet = f.ausfall ? '<span class="tf-weg">Ausfall</span>'
                  : (f.spaet > 0 ? '<span class="tf-spaet">+' + f.spaet + '</span>' : '');
      return '<div class="tf-zeile' + (f.ausfall ? ' ausfall' : '') + '">'
        + '<span class="tf-zeit">' + esc(f.soll) + spaet + '</span>'
        + '<span class="tf-zug"><b>' + esc(f.linie) + '</b> <span class="dim">' + esc(f.nummer) + '</span>'
        + (f.ziel ? '<br>' + esc(f.ziel) : '') + '</span>'
        + '<span class="tf-gleis' + (f.gleis_neu ? ' neu' : '') + '">' + esc(f.gleis || '–') + '</span></div>';
    }).join('');
    inhalt.innerHTML = (zeilen || '<div class="wr-lade">Keine Abfahrten im Zeitfenster.</div>')
      + '<a class="mehr-link" href="./?s=lage' + (d.bahnhof === 'Elmshorn' ? '' : '&bf=' + encodeURIComponent(d.bahnhof))
      + '">Alle Abfahrten mit Filtern →</a>';
  }

  window.zeigeTafel = async function (bahnhof) {
    letzterKnopf = document.activeElement;
    titel.textContent = bahnhof;
    neben.textContent = 'Abfahrten';
    quelle.textContent = 'Echtzeit der Deutschen Bahn. Angaben ohne Gewähr.';
    inhalt.innerHTML = '<div class="wr-lade">Abfahrten werden geholt …'
      + '<div class="balken"></div><div class="balken"></div><div class="balken"></div></div>';
    hinter.setAttribute('open', '');
    document.body.style.overflow = 'hidden';
    try {
      const r = await fetch('tafel.php?bf=' + encodeURIComponent(bahnhof), { credentials: 'same-origin' });
      const j = await r.json();
      if (j.status === 'fertig') {
        neben.textContent = 'Abfahrten · Stand ' + (j.stand || '–');
        zeichneTafel(j);
      } else {
        inhalt.innerHTML = '<div class="wr-fehler">' + esc(j.fehler || 'Keine Abfahrten verfügbar.') + '</div>';
      }
    } catch (e) {
      inhalt.innerHTML = '<div class="wr-fehler">Keine Verbindung: ' + esc(e.message) + '</div>';
    }
  };

  document.addEventListener('click', (e) => {
    const tafelKnopf = e.target.closest('[data-tafel]');
    if (tafelKnopf) { e.preventDefault(); window.zeigeTafel(tafelKnopf.dataset.tafel); return; }
    const knopf = e.target.closest('[data-wr]');
    if (knopf) { e.preventDefault(); oeffne(knopf); return; }
    const lokKnopf = e.target.closest('[data-lok]');
    if (lokKnopf) { e.preventDefault(); oeffneLok(lokKnopf); }
  });
})();
</script>


<script>
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('sw.js'); }

// Der Startbildschirm blendet sich nach der eingestellten Zeit selbst aus (CSS).
// Lädt die Seite länger, bleibt er einfach stehen — genau dafür ist er da.

// Offline-Anzeige: Die App zeigt dann die zuletzt geladene Fassung, deshalb steht
// neben "Offline" der Stand dieser Seite.
(function () {
  const marke = document.getElementById('offline-marke');
  if (!marke) { return; }
  const meta = document.querySelector('meta[name="erzeugt"]');
  const stand = meta ? meta.content.slice(11) : '';
  function zeige() {
    const offline = !navigator.onLine || erreichbar === false;
    marke.hidden = !offline;
    marke.querySelector('.text').textContent = offline && stand ? 'Offline · Stand ' + stand : 'Offline';
    document.documentElement.dataset.netz = offline ? 'weg' : 'da';
  }
  window.addEventListener('online', () => pruefe());
  window.addEventListener('offline', zeige);
  marke.addEventListener('click', () => pruefe());      // antippen: nochmal nachsehen

  // navigator.onLine sagt nur, ob ein Netz da ist — nicht, ob der Server antwortet.
  // Deshalb einmal kurz anklopfen (winzige Datei, am Zwischenspeicher vorbei).
  let erreichbar = navigator.onLine;
  async function klopf() {
    try {
      const r = await fetch('ping.txt?z=' + Date.now(), { cache: 'no-store' });
      return r.ok;
    } catch (e) {
      return false;
    }
  }
  async function pruefe() {
    if (!navigator.onLine) { erreichbar = false; zeige(); return; }
    // Ein einzelner Fehlschlag kann ein Ausrutscher sein — erst beim zweiten gilt es.
    erreichbar = await klopf() || await new Promise((f) => setTimeout(() => f(klopf()), 2500));
    zeige();
  }
  zeige();
  pruefe();
  // Beim Zurückkehren zur App nochmal nachsehen.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { pruefe(); } });
})();

// Ladebalken: Seiten kommen frisch vom Server, ein Wechsel dauert einen Moment.
(function () {
  const balken = document.getElementById('fortschritt');
  if (!balken) { return; }
  let laeuft = null;
  function starte() {
    if (laeuft) { return; }
    let breite = 8;
    balken.classList.add('laeuft');
    balken.style.width = breite + '%';
    laeuft = setInterval(() => {
      breite = Math.min(breite + (90 - breite) * 0.18, 90);
      balken.style.width = breite + '%';
    }, 180);
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a');
    if (!a || a.target === '_blank' || a.dataset.wr || a.dataset.lok) { return; }
    const ziel = a.getAttribute('href') || '';
    if (ziel.startsWith('#') || ziel.startsWith('mailto:')) { return; }
    if (new URL(a.href, location.href).origin === location.origin) { starte(); }
  });
  document.addEventListener('submit', starte);
  // Kommt man über den Zurück-Knopf aus dem Zwischenspeicher, läuft nichts mehr.
  window.addEventListener('pageshow', () => {
    clearInterval(laeuft);
    laeuft = null;
    balken.classList.remove('laeuft');
    balken.style.width = '0';
  });
})();
</script>
<?= $skript ?>
</body></html>
<?php
}

function seiten_stil(): string {
    return <<<'CSS'

  .wrap { max-width:1000px; margin:0 auto; padding:0 16px 56px; }

  /* Kopfleiste bleibt beim Scrollen stehen — auf dem Handy der Ankerpunkt. */
  .topbar {
    position:sticky; top:0; z-index:20;
    background:var(--bg);
    background:color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter:saturate(160%) blur(10px);
    border-bottom:1px solid var(--line);
    padding:calc(10px + env(safe-area-inset-top)) 0 10px;
    margin-bottom:20px;
  }
  .topbar .inner {
    max-width:1000px; margin:0 auto; padding:0 16px;
    display:flex; align-items:center; gap:12px;
  }
  .topbar h1 { font-size:17px; margin:0; letter-spacing:-.01em; }
  .topbar .abmelden {
    margin-left:auto; font-size:14px; text-decoration:none; color:var(--muted);
    padding:8px 10px; border-radius:9px; min-height:40px; display:flex; align-items:center;
  }
  .topbar .abmelden:active { background:var(--neutral-bg); }

  /* Aufmacher: die nächste Durchfahrt */
  .hero {
    background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    box-shadow:var(--schatten); padding:20px; margin-bottom:14px;
  }
  .hero .label { font-size:13px; color:var(--muted); margin-bottom:6px; }
  .hero .zeit { font-size:38px; font-weight:700; line-height:1.05; letter-spacing:-.02em; }
  .hero .zeit small { font-size:15px; font-weight:600; color:var(--muted); margin-left:8px; }
  .hero .lok { font-size:17px; font-weight:600; margin-top:6px; }
  .hero .weg { color:var(--muted); font-size:14px; margin-top:2px; }
  .hero .keine { font-size:20px; font-weight:600; color:var(--muted); }
  .hero .gleis {
    display:inline-block; background:var(--akzent); color:#fff; font-weight:700;
    font-size:14px; padding:3px 11px; border-radius:20px; margin-left:4px;
    vertical-align:middle;
  }

  /* Statuszeile */
  .status { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:22px; }
  .chip {
    display:inline-flex; align-items:center; gap:6px; font-size:13px;
    padding:7px 12px; border-radius:20px; background:var(--neutral-bg);
    color:var(--neutral-fg); border:1px solid transparent;
  }
  .chip.gut  { background:var(--ok-bg);   color:var(--ok-fg);   font-weight:600; }
  .chip.warn { background:var(--warn-bg); color:var(--warn-fg); font-weight:600; }

  section { margin-bottom:26px; }
  section > h2 {
    font-size:13px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:0 0 10px 2px; font-weight:600;
  }
  .box {
    background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    box-shadow:var(--schatten); overflow:hidden;
  }
  .boxkopf {
    display:flex; flex-wrap:wrap; align-items:center; gap:10px;
    padding:14px 16px; border-bottom:1px solid var(--line);
  }
  .boxkopf .titel { font-weight:600; }
  .boxkopf .neben { color:var(--muted); font-size:13px; margin-left:auto; }
  .hinweis {
    background:var(--warn-bg); color:var(--warn-fg); padding:12px 16px;
    font-size:14px; border-bottom:1px solid var(--line);
  }
  .leer { padding:16px; color:var(--muted); font-size:14px; }

  table { width:100%; border-collapse:collapse; font-size:14.5px; }
  th, td { text-align:left; padding:11px 16px; border-bottom:1px solid var(--line); }
  th { font-size:12px; color:var(--muted); font-weight:600; letter-spacing:.02em; }
  tr:last-child td { border-bottom:0; }
  .nowrap { white-space:nowrap; }
  .dim { color:var(--muted); }
  .stark { font-weight:600; }
  .zeitzelle { font-weight:700; font-size:16px; white-space:nowrap; }
  .gleiszelle { font-weight:700; color:var(--akzent); white-space:nowrap; }
  .spaet { color:var(--warn-fg); font-weight:600; }
  .marke {
    display:inline-block; font-weight:600; font-size:11px; white-space:nowrap;
    color:var(--warn); border:1px solid currentColor; border-radius:20px; padding:1px 8px;
  }
  .klein { font-size:11.5px; font-weight:400; color:var(--muted); display:block; }
  .durch td { opacity:.55; }
  .durch td.zeitzelle, .durch td.gleiszelle { text-decoration:none; }
  .durch td:not(.zeitzelle):not(.gleiszelle) { text-decoration:line-through; }

  .beschreibung { color:var(--muted); font-size:13.5px; line-height:1.5; margin-top:5px; }
  .standort { padding:12px 16px; border-bottom:1px solid var(--line); }
  .standort:last-child { border-bottom:0; }
  .ortsname { font-weight:600; font-size:14px; margin-bottom:8px; }
  .ortsname .dim { font-weight:400; font-size:12px; }
  .lokliste { display:flex; flex-wrap:wrap; gap:6px; }
  .lokmarke {
    font-size:13px; padding:4px 10px; border-radius:8px; white-space:nowrap;
    background:var(--neutral-bg); color:var(--neutral-fg);
    font-variant-numeric:tabular-nums;
  }
  .lokmarke .lokart { display:block; font-size:10.5px; font-weight:600; opacity:.9; letter-spacing:.02em; }
  .lokmarke.art-shuttle { outline:1px dashed var(--akzent); outline-offset:-1px; }
  .lokmarke .lokname { font-weight:600; font-style:italic; margin-left:5px; opacity:.85; }
  .weg-zeile { font-size:13px; color:var(--muted); margin-bottom:6px; }
  .lokmarke .lokbes { display:block; font-size:11px; font-weight:400; opacity:.8; }
  .lokmarke.besonders { border:1px dashed currentColor; }
  details.alle { border-top:1px solid var(--line); }
  .live { color:var(--ok-fg); }
  .merkform { margin:0; display:inline; }
  .knopf-klein { font:inherit; font-size:12.5px; padding:6px 11px; border-radius:20px; cursor:pointer;
                 border:1px solid var(--line); background:var(--card); color:var(--link);
                 white-space:nowrap; min-height:34px; }
  .knopf-klein.aktiv { background:var(--ok-bg); color:var(--ok-fg); border-color:transparent; font-weight:600; }
  .knopf-klein:active { filter:brightness(.93); }
  details.alle summary { padding:12px 16px; cursor:pointer; color:var(--link); font-size:14px; }
  tr.trifft td.zeitzelle { color:var(--ok-fg); }
  .lokmarke.br218 { background:var(--ok-bg); color:var(--ok-fg); font-weight:600; }
  .lokmarke.faehrt { outline:2px solid var(--akzent); outline-offset:-2px; }
  .titel-link { font-weight:600; text-decoration:none; }
  .titel-link:hover { text-decoration:underline; }

  /* Umschalter */
  .schalter { display:inline-flex; background:var(--neutral-bg); border-radius:11px; padding:3px; }
  .schalter a {
    text-decoration:none; color:var(--muted); font-size:14px; padding:8px 14px;
    border-radius:9px; min-height:40px; display:flex; align-items:center;
  }
  .schalter a.aktiv { background:var(--card); color:var(--fg); font-weight:600;
                      box-shadow:var(--schatten); }

  .tagkarte + .tagkarte { margin-top:10px; }
  .fuss { color:var(--muted); font-size:12.5px; line-height:1.65; margin-top:28px; }
  .topbar .logo { width:36px; height:36px; flex:0 0 36px; }
  .app-bereich { text-align:center; margin:30px 0 10px; }
  .app-knopf { display:inline-flex; align-items:center; gap:10px; font:inherit; font-weight:600;
               font-size:15px; padding:12px 20px; min-height:50px; border-radius:14px; cursor:pointer;
               border:1px solid var(--line); background:var(--card); color:var(--fg);
               box-shadow:var(--schatten); }
  .app-knopf img { width:28px; height:28px; }
  .app-knopf:active { filter:brightness(.95); }
  .app-hinweis { margin:12px auto 0; max-width:340px; font-size:14px; color:var(--muted);
                 background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
  .app-status { color:var(--muted); font-size:13px; }
  .push-bereich { margin-top:12px; }
  .app-knopf.an { background:var(--ok-bg); color:var(--ok-fg); border-color:transparent; }
  .app-knopf:disabled { opacity:.6; cursor:default; }
  .geraete-link { display:inline-block; margin-top:14px; font-size:14px; text-decoration:none; padding:8px; }
  .fuss strong { color:var(--fg); }

  /* ---------- Handy: Tabellen werden zu Karten ---------- */
  @media (max-width: 700px) {
    .wrap { padding:0 12px 48px; }
    .hero .zeit { font-size:34px; }
    section > h2 { margin-left:0; }

    table, thead, tbody, tr, td { display:block; width:100%; }
    thead { display:none; }
    tr { padding:12px 14px; border-bottom:1px solid var(--line); }
    tr:last-child { border-bottom:0; }
    td {
      border:0; padding:3px 0; display:flex; gap:12px; align-items:baseline;
    }
    td::before {
      content:attr(data-l); flex:0 0 88px; color:var(--muted); font-size:12px;
      text-transform:uppercase; letter-spacing:.04em;
    }
    td:empty { display:none; }
    td.voll { display:block; }
    td.voll::before { display:block; margin-bottom:3px; }
    .durch td:not(.zeitzelle):not(.gleiszelle) { text-decoration:none; }
    .durch tr { opacity:.6; }
  }
  @media (max-width: 380px) {
    td::before { flex-basis:74px; }
  }

  /* ---------- Rahmen und Menü ---------- */
  body { padding-bottom:calc(72px + env(safe-area-inset-bottom)); }
  .topbar { margin-bottom:18px; }
  .marke-link { display:flex; align-items:center; gap:10px; text-decoration:none; color:inherit; min-width:0; }
  .kopftitel { display:flex; flex-direction:column; line-height:1.15; min-width:0; }
  .kopftitel .app { font-size:17px; font-weight:700; letter-spacing:-.01em; }
  .kopftitel .seite { font-size:13px; color:var(--muted); }

  /* Oben rechts, sobald das Netz weg ist. Der Stand sagt, von wann die Anzeige ist. */
  .offline-marke {
    margin-left:auto; display:inline-flex; align-items:center; gap:6px; white-space:nowrap;
    background:var(--warn-bg); color:var(--warn-fg); font-size:12.5px; font-weight:600;
    padding:5px 11px; border-radius:20px;
  }
  .offline-marke .punkt { width:7px; height:7px; border-radius:50%; background:currentColor; }
  .offline-marke:not([hidden]) + .topnav { margin-left:12px; }

  .tabbar {
    position:fixed; left:0; right:0; bottom:0; z-index:30; display:flex;
    background:var(--card);
    background:color-mix(in srgb, var(--card) 92%, transparent);
    backdrop-filter:saturate(160%) blur(12px);
    border-top:1px solid var(--line);
    padding:0 max(6px, env(safe-area-inset-left)) env(safe-area-inset-bottom) max(6px, env(safe-area-inset-right));
  }
  .tab {
    flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px;
    min-height:58px; padding:6px 0; font-size:11.5px; color:var(--muted); text-decoration:none;
    -webkit-tap-highlight-color:transparent;
  }
  .tab svg { width:24px; height:24px; }
  .tab.aktiv { color:var(--akzent); font-weight:600; }
  .tab.aktiv svg { stroke-width:2.2; }
  .tab:active { opacity:.7; }

  .topnav { display:none; margin-left:auto; gap:4px; }
  .topnav-link {
    display:flex; align-items:center; gap:7px; padding:8px 12px; border-radius:10px;
    color:var(--muted); text-decoration:none; font-size:14px; min-height:40px;
  }
  .topnav-link svg { width:19px; height:19px; }
  .topnav-link:hover { background:var(--neutral-bg); color:var(--fg); }
  .topnav-link.aktiv { background:var(--neutral-bg); color:var(--fg); font-weight:600; }
  @media (min-width: 900px) {
    .tabbar { display:none; }
    .topnav { display:flex; }
    body { padding-bottom:env(safe-area-inset-bottom); }
    .kopftitel .seite { display:none; }
  }

  /* ---------- Startseite ---------- */
  .kacheln { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:24px; }
  .kachel {
    display:block; background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    box-shadow:var(--schatten); padding:13px 15px; text-decoration:none; color:inherit;
  }
  .kachel .k-label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .kachel .k-wert { font-size:20px; font-weight:700; margin-top:2px; }
  .kachel .k-neben { font-size:13px; color:var(--muted); }
  .kachel.warn { background:var(--warn-bg); border-color:transparent; }
  .kachel.warn .k-wert, .kachel.warn .k-label, .kachel.warn .k-neben { color:var(--warn-fg); }
  .kachel.gut .k-wert { color:var(--ok-fg); }
  .hero .bis { display:inline-block; margin-left:8px; font-size:14px; font-weight:600; color:var(--ok-fg);
               background:var(--ok-bg); padding:2px 10px; border-radius:20px; vertical-align:middle; }

  .plantag + .plantag { margin-top:12px; }
  .plantag .boxkopf .titel { font-size:15px; }
  .fahrt { display:grid; grid-template-columns:62px 1fr auto; gap:4px 14px; align-items:center;
           padding:12px 16px; border-bottom:1px solid var(--line); }
  .fahrt:last-child { border-bottom:0; }
  .fahrt .f-zeit { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.1; }
  .fahrt .f-gleis { display:block; font-size:12px; font-weight:600; color:var(--akzent); margin-top:2px; }
  .fahrt .f-lok { font-weight:600; }
  .fahrt .f-weg { font-size:13.5px; color:var(--muted); }
  .fahrt .f-zusatz { font-size:12px; color:var(--muted); }
  .fahrt.vorbei { opacity:.5; }
  .fahrt.gestrichen .f-lok, .fahrt.gestrichen .f-weg { text-decoration:line-through; }
  .fahrt.sonder .f-lok { color:var(--akzent); }
  .etikett { display:inline-block; font-size:11px; font-weight:600; padding:1px 8px; border-radius:20px;
             background:var(--neutral-bg); color:var(--neutral-fg); margin-left:6px; vertical-align:middle; }
  .etikett.warn { background:var(--warn-bg); color:var(--warn-fg); }
  /* Herkunft: Regionalverkehr oder SyltShuttle/IC */
  .etikett.quelle-regio { background:var(--neutral-bg); color:var(--neutral-fg); }
  .etikett.quelle-beide { background:color-mix(in srgb, var(--ok-fg) 15%, transparent); color:var(--ok-fg);
                          font-weight:600; }
  .etikett.quelle-shuttle { background:color-mix(in srgb, var(--akzent) 16%, transparent); color:var(--akzent);
                            font-weight:600; }
  .etikett.gut { background:var(--ok-bg); color:var(--ok-fg); }
  .fahrt.fertig { opacity:.75; }
  .fahrt.fertig .f-zeit { color:var(--ok-fg); }
  .f-spaet { display:block; font-size:12px; font-weight:700; color:var(--warn-fg); }
  .hero .bis.warn { background:var(--warn-bg); color:var(--warn-fg); }
  @media (max-width: 420px) {
    .fahrt { grid-template-columns:54px 1fr; }
    .fahrt .f-knopf { grid-column:2; justify-self:start; }
  }
  .f-knopf { display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
  .wr-knopf { text-decoration:none; display:inline-flex; align-items:center; }
  @media (max-width: 420px) { .f-knopf { flex-direction:row; flex-wrap:wrap; align-items:center; } }
  /* ---------- Fenster für die Wagenreihung ---------- */
  .wr-hinter {
    position:fixed; inset:0; z-index:60; display:none; align-items:flex-end; justify-content:center;
    background:rgba(16,24,40,.45); backdrop-filter:blur(2px);
  }
  .wr-hinter[open] { display:flex; }
  .wr-fenster {
    background:var(--card); color:var(--fg); width:min(620px, 100%);
    border-radius:var(--radius) var(--radius) 0 0; box-shadow:0 -8px 40px rgba(16,24,40,.25);
    max-height:88vh; overflow:auto; padding-bottom:max(14px, env(safe-area-inset-bottom));
    animation:wr-auf .18s ease-out;
  }
  @keyframes wr-auf { from { transform:translateY(24px); opacity:.4; } to { transform:none; opacity:1; } }
  @media (min-width: 700px) {
    .wr-hinter { align-items:center; }
    .wr-fenster { border-radius:var(--radius); }
  }
  .wr-kopf { display:flex; align-items:flex-start; gap:12px; padding:16px 16px 10px; border-bottom:1px solid var(--line);
             position:sticky; top:0; background:var(--card); }
  .wr-kopf h3 { margin:0; font-size:17px; }
  .wr-kopf .wr-neben { font-size:13px; color:var(--muted); margin-top:2px; }
  .wr-zu { margin-left:auto; border:0; background:var(--neutral-bg); color:var(--fg); font:inherit; font-size:20px;
           line-height:1; width:36px; height:36px; border-radius:50%; cursor:pointer; flex:0 0 36px; }
  .wr-inhalt { padding:14px 16px 6px; }
  .wr-lade { color:var(--muted); font-size:14px; padding:20px 0; text-align:center; }
  .wr-fehler { background:var(--warn-bg); color:var(--warn-fg); border-radius:10px; padding:12px 14px; font-size:14px; }
  .wr-zusammen { font-size:15px; font-weight:600; margin-bottom:4px; }
  .wr-ziel { font-size:13px; color:var(--muted); margin-bottom:14px; }
  .wr-gleis { display:inline-block; background:var(--akzent); color:#fff; font-weight:700; font-size:13px;
              padding:2px 10px; border-radius:20px; }

  /* Bahnsteig in voller Breite, der Zug steht als Balken darauf — die Prozentwerte
     kommen von der Quelle, deshalb stimmt, wo der Zug am Bahnsteig hält. */
  .wr-steig { position:relative; height:56px; margin:8px 0 2px; }
  .wr-gleisband { position:absolute; left:0; right:0; top:14px; height:24px;
                  background:var(--neutral-bg); border-radius:6px; }
  .wr-abschnitt { position:absolute; top:40px; height:16px; border-left:1px solid var(--line);
                  font-size:11px; color:var(--muted); text-align:center; line-height:16px; }
  .wr-abschnitt:last-of-type { border-right:1px solid var(--line); }
  .wr-wagen { position:absolute; top:14px; height:24px; border-radius:5px; background:var(--card);
              border:1px solid var(--line); }
  .wr-wagen.lok { background:var(--akzent); border-color:transparent; }
  .wr-wagen.erste { background:var(--ok-bg); border-color:transparent; }
  .wr-pfeil { position:absolute; top:0; font-size:12px; color:var(--muted); white-space:nowrap; }
  .wr-richtung { font-size:13px; color:var(--muted); margin:10px 0 2px; display:flex; align-items:center; gap:6px; }
  .wr-liste { list-style:none; margin:14px 0 0; padding:0; font-size:14px; }
  .wr-liste li { display:flex; gap:10px; align-items:center; padding:9px 0; border-bottom:1px solid var(--line); }
  .wr-liste li:last-child { border-bottom:0; }
  .wr-liste .wr-fach { flex:0 0 26px; height:26px; border-radius:7px; background:var(--neutral-bg);
                       color:var(--neutral-fg); font-size:12px; font-weight:700; display:grid; place-items:center; }
  .wr-liste li.lok .wr-fach { background:var(--akzent); color:#fff; }
  .wr-liste li.lok b { color:var(--akzent); }
  .wr-liste .wr-was { flex:1; min-width:0; }
  .wr-liste .wr-was .dim { font-size:12.5px; }
  .wr-quelle { font-size:12px; color:var(--muted); padding:12px 16px 4px; line-height:1.5; }

  /* Steht die Tagesliste im Forum schon? */
  .quellenstand { padding:8px 16px 10px; border-top:1px solid var(--line); font-size:12.5px;
                  display:flex; flex-wrap:wrap; gap:4px 16px; }
  .qs-teil { display:inline-flex; align-items:center; gap:6px; white-space:nowrap;
             font-variant-numeric:tabular-nums; }
  .qs-punkt { width:7px; height:7px; border-radius:50%; flex:0 0 7px; }
  .qs-punkt.da { background:var(--ok-fg); }
  .qs-punkt.fehlt { background:var(--muted); opacity:.5; }
  .qs-text a { text-decoration:none; font-weight:600; }
  .quellenstand .dim { color:var(--muted); }

  .mehr-link { display:inline-block; margin-top:10px; font-size:14px; text-decoration:none; padding:6px 2px; }

  /* ---------- Lok-Steckbrief ---------- */
  .lok-link { color:inherit; text-decoration:none; border-bottom:1px dotted var(--muted); cursor:pointer; }
  .lok-link:active { opacity:.7; }
  .lk-kopf { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:10px; }
  .lk-zahlen { display:grid; grid-template-columns:repeat(auto-fit, minmax(110px, 1fr)); gap:8px; margin:12px 0; }
  .lk-zahl { background:var(--neutral-bg); border-radius:10px; padding:9px 11px; }
  .lk-zahl b { display:block; font-size:19px; }
  .lk-zahl span { font-size:11.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .lk-titel { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
              margin:16px 0 6px; font-weight:600; }
  .lk-liste { list-style:none; margin:0; padding:0; font-size:14px; }
  .lk-liste li { display:flex; gap:10px; padding:7px 0; border-bottom:1px solid var(--line); }
  .lk-liste li:last-child { border-bottom:0; }
  .lk-liste .lk-zeit { flex:0 0 96px; font-weight:600; font-variant-numeric:tabular-nums; }
  .lk-liste .dim { font-size:13px; }

  /* ---------- Lage: eine Zeile je Abfahrt ---------- */
  /* Eine Zeile Filter; auf schmalen Geräten seitlich schiebbar. */
  .filter { display:flex; flex-wrap:nowrap; gap:6px; margin-bottom:10px; overflow-x:auto;
            padding-bottom:2px; scrollbar-width:none; }
  .filter::-webkit-scrollbar { display:none; }
  .f-chip {
    font:inherit; font-size:13px; padding:7px 12px; min-height:36px; border-radius:20px; cursor:pointer;
    border:1px solid var(--line); background:var(--card); color:var(--fg); white-space:nowrap;
  }
  .f-chip .dim { font-size:12px; margin-left:4px; }
  .f-chip.aktiv { background:var(--akzent); border-color:transparent; color:#fff; font-weight:600; }
  .f-chip.aktiv .dim { color:#fff; opacity:.8; }
  .f-chip.warnung:not(.aktiv) { color:var(--warn-fg); background:var(--warn-bg); border-color:transparent; }

  .lagezeile {
    display:grid; grid-template-columns:52px 1fr 26px 44px; gap:2px 10px; align-items:center;
    padding:10px 14px; border-bottom:1px solid var(--line);
  }
  .lagezeile:last-of-type { border-bottom:0; }
  .lagezeile.entfaellt .lz-zug { text-decoration:line-through; }
  .lagezeile.entfaellt { opacity:.65; }
  .lagezeile.br218 { background:color-mix(in srgb, var(--ok-bg) 45%, transparent); }
  .lz-zeit { font-size:17px; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.15; }
  .lz-spaet { display:block; font-size:12px; font-weight:700; color:var(--warn-fg); }
  .lz-zug { font-size:14.5px; line-height:1.35; }
  .lz-grund { font-size:12.5px; color:var(--muted); line-height:1.4; }
  /* Eigene Spalte: so stehen alle Gleisnummern genau untereinander. */
  .lz-gleis { font-weight:700; color:var(--akzent); font-size:16px; text-align:center;
              font-variant-numeric:tabular-nums; }
  .lz-rechts { display:flex; align-items:center; justify-content:flex-end; }
  .lz-rechts .wr-knopf { font-size:0; padding:7px 10px; min-height:34px; }
  .lz-rechts .wr-knopf::before { content:"🚃"; font-size:15px; }
  @media (max-width: 420px) {
    .lagezeile { grid-template-columns:46px 1fr 22px 40px; padding:10px 11px; gap:2px 8px; }
    .lz-zeit { font-size:16px; }
  }

  /* ---------- Benachrichtigungsverlauf ---------- */
  .verlauf-tag { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
                 margin:18px 0 8px 2px; font-weight:600; }
  .verlauf-zeile { padding:13px 15px; margin-bottom:8px; }
  .v-kopf { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:5px; }
  .v-zeit { font-weight:700; font-variant-numeric:tabular-nums; }
  .v-wege { margin-left:auto; display:flex; gap:6px; flex-wrap:wrap; }
  .v-betreff { font-weight:600; font-size:15px; line-height:1.35; }
  .v-text { color:var(--muted); font-size:13.5px; margin-top:3px; line-height:1.5; }

  /* ---------- Bewegung ----------
     Alles hier ist Zierde. Wer im System "Bewegung reduzieren" eingestellt hat,
     bekommt die Seite ohne jede Animation (siehe ganz unten). */
  @keyframes zr-auftauchen { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  @keyframes zr-weg { to { opacity:0; visibility:hidden; } }
  @keyframes zr-fahrt { from { transform:translateX(-120%); } to { transform:translateX(320%); } }
  @keyframes zr-puls { 0%,100% { transform:scale(1); } 50% { transform:scale(1.06); } }
  @keyframes zr-schimmer { from { background-position:-450px 0; } to { background-position:450px 0; } }
  @keyframes zr-dreh { to { transform:rotate(360deg); } }

  /* Startbildschirm: nur beim Öffnen der installierten App, einmal je Sitzung.
     Er verschwindet auch ohne JavaScript von selbst (Animation mit forwards).

     Farben folgen dem Handy-Design: auf einem dunklen Gerät ist auch der Startbildschirm
     dunkel. Androids eigenes Startbild davor kennt nur eine feste Farbe — die steht im
     Manifest unter background_color und ist seit 17.09.2026 dunkel (#0c1117), passend zum
     dunklen App-Hintergrund. Abschalten lässt sich Androids Bild nicht. */
  .start-schirm { display:none; }
  [data-splash="an"] .start-schirm {
    display:flex; position:fixed; inset:0; z-index:90; flex-direction:column;
    align-items:center; justify-content:center; gap:18px; background:var(--bg);
    /* Dauer kommt aus den Einstellungen (localStorage), Voreinstellung 1,6 Sekunden. */
    animation:zr-weg .45s ease-in var(--splash-dauer, 1600ms) forwards;
  }
  [data-splash="fertig"] .start-schirm { animation:zr-weg .3s ease-in forwards; }
  .start-schirm img { width:96px; height:96px; animation:zr-puls 1.6s ease-in-out infinite; }
  .start-schirm .name { font-size:20px; font-weight:700; letter-spacing:-.01em; }
  .start-schirm .gleis {
    position:relative; width:180px; height:3px; border-radius:3px; background:var(--line); overflow:hidden;
  }
  .start-schirm .gleis::after {
    content:""; position:absolute; inset:0 auto 0 0; width:38%; border-radius:3px;
    background:linear-gradient(90deg, transparent, var(--akzent), transparent);
    animation:zr-fahrt 1.1s ease-in-out infinite;
  }

  /* Ladebalken beim Seitenwechsel */
  .fortschritt {
    position:fixed; top:0; left:0; height:3px; width:0; z-index:95; background:var(--akzent);
    box-shadow:0 0 8px var(--akzent); transition:width .25s ease-out, opacity .3s ease-out;
    opacity:0; pointer-events:none;
  }
  .fortschritt.laeuft { opacity:1; }

  /* Inhalte tauchen gestaffelt auf */
  .wrap > section, .wrap > .hero, .wrap > .kacheln, .wrap > .box, .wrap > .fuss {
    animation:zr-auftauchen .38s ease-out both;
  }
  .wrap > :nth-child(2) { animation-delay:.04s; }
  .wrap > :nth-child(3) { animation-delay:.08s; }
  .wrap > :nth-child(4) { animation-delay:.12s; }
  .wrap > :nth-child(5) { animation-delay:.16s; }
  .wrap > :nth-child(n+6) { animation-delay:.2s; }

  /* Kleinigkeiten */
  .kachel, .knopf-klein, .f-chip, .app-knopf, .tab, .lagezeile, .fahrt {
    transition:background-color .18s ease, color .18s ease, transform .12s ease, opacity .18s ease;
  }
  .kachel:active, .fahrt:active { transform:scale(.985); }
  .tab.aktiv svg { animation:zr-puls .35s ease-out; }
  .wr-hinter { animation:zr-auftauchen .2s ease-out; }
  .lagezeile[hidden] { display:none; }

  /* Wartebalken im Fenster statt nackter Text */
  .wr-lade .balken {
    height:14px; border-radius:7px; margin:8px auto; max-width:320px;
    background:linear-gradient(90deg, var(--neutral-bg) 25%, var(--line) 50%, var(--neutral-bg) 75%);
    background-size:450px 100%; animation:zr-schimmer 1.2s linear infinite;
  }
  .wr-lade .balken:nth-child(2) { max-width:250px; }
  .wr-lade .balken:nth-child(3) { max-width:180px; }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration:.001ms !important; animation-iteration-count:1 !important;
      transition-duration:.001ms !important; scroll-behavior:auto !important;
    }
  }
  /* Dasselbe, wenn man die Animationen in den Einstellungen abschaltet. */
  [data-motion="aus"] *, [data-motion="aus"] *::before, [data-motion="aus"] *::after {
    animation-duration:.001ms !important; animation-iteration-count:1 !important;
    transition-duration:.001ms !important;
  }
  [data-motion="aus"] .start-schirm { display:none !important; }

  /* ---------- Einstellungen ---------- */
  .einstellung { display:flex; flex-wrap:wrap; align-items:center; gap:10px;
                 padding:13px 16px; border-bottom:1px solid var(--line); }
  .einstellung:last-child { border-bottom:0; }
  .einstellung .e-text { flex:1; min-width:150px; }
  .einstellung .e-titel { font-weight:600; }
  .einstellung .e-neben { font-size:12.5px; color:var(--muted); }
  .knopf-klein.haupt { background:var(--akzent); color:#fff; border-color:transparent; font-weight:600; }
  .einstellung select, .einstellung button {
    font:inherit; font-size:14px; padding:8px 11px; border-radius:10px; min-height:40px;
    border:1px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer;
  }

  /* ---------- Fußzeile ---------- */
  .seitenfuss {
    margin-top:34px; padding:18px 0 6px; border-top:1px solid var(--line);
    color:var(--muted); font-size:12.5px; line-height:1.7; text-align:center;
  }
  .seitenfuss a { color:var(--muted); text-decoration:none; border-bottom:1px solid var(--line); }
  .seitenfuss a:hover { color:var(--link); }
  .seitenfuss .marke { font-weight:600; color:var(--fg); }
  .seitenfuss .zeile + .zeile { margin-top:4px; }

  /* ---------- Tageslisten: je Tag eine aufklappbare Karte ---------- */
  .tagkarte { padding:0; }
  .tagkarte + .tagkarte { margin-top:8px; }
  .tagkopf {
    display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:12px 14px; cursor:pointer;
    list-style:none; min-height:48px;
  }
  .tagkopf::-webkit-details-marker { display:none; }
  .tagkopf::after { content:"›"; margin-left:auto; color:var(--muted); font-size:18px;
                    transform:rotate(90deg); transition:transform .2s ease; }
  .tagkarte[open] .tagkopf::after { transform:rotate(-90deg); }
  .tagkarte[open] .tagkopf { border-bottom:1px solid var(--line); }
  .tk-datum { font-weight:700; font-variant-numeric:tabular-nums; }
  .tk-neben { color:var(--muted); font-size:12.5px; width:100%; }

  .tzeile { display:grid; grid-template-columns:62px 1fr auto; gap:1px 10px; align-items:center;
            padding:8px 14px; border-bottom:1px solid var(--line); }
  .tzeile:last-of-type { border-bottom:0; }
  .tzeile.elmshorn { background:color-mix(in srgb, var(--ok-bg) 30%, transparent); }
  .tzeile.gestrichen .tz-lok, .tzeile.gestrichen .tz-weg { text-decoration:line-through; }
  .tzeile.gestrichen { opacity:.6; }
  .tz-zeit { font-weight:700; font-variant-numeric:tabular-nums; line-height:1.15; }
  .tz-gleis { display:block; font-size:11.5px; font-weight:600; color:var(--akzent); }
  .tz-lok { font-size:14px; }
  .tz-weg { font-size:12.5px; color:var(--muted); line-height:1.4; }
  .tz-knopf .knopf-klein { min-width:38px; text-align:center; }
  .weitere > summary { padding:10px 14px; cursor:pointer; color:var(--link); font-size:13.5px;
                       border-bottom:1px solid var(--line); }
  .tk-fuss { padding:9px 14px; font-size:12.5px; color:var(--muted); }
  .tk-fuss a { color:var(--muted); }

  /* ---------- Besondere 218er: eine Zeile je Lok ---------- */
  .lokzeile { display:grid; grid-template-columns:minmax(120px, 34%) 1fr; gap:4px 12px;
              padding:9px 14px; border-bottom:1px solid var(--line); align-items:baseline; }
  .lokzeile:last-of-type { border-bottom:0; }
  .lokzeile.aktiv { background:color-mix(in srgb, var(--ok-bg) 30%, transparent); }
  .lz-name { font-weight:600; }
  .lz-lage { font-size:13.5px; }
  @media (max-width: 480px) {
    .lokzeile { grid-template-columns:1fr; }
    .tzeile { grid-template-columns:54px 1fr auto; padding:8px 11px; }
  }

  /* ---------- Statistik: Balken ---------- */
  .balkenzeile { display:grid; grid-template-columns:minmax(96px, 30%) 1fr auto; gap:10px;
                 align-items:center; padding:9px 14px; border-bottom:1px solid var(--line); }
  .balkenzeile:last-of-type { border-bottom:0; }
  .bz-name { font-weight:600; font-size:14px; }
  .bz-balken { height:10px; border-radius:5px; background:var(--neutral-bg); overflow:hidden; }
  .bz-balken span { display:block; height:100%; border-radius:5px; background:var(--akzent);
                    transition:width .4s ease-out; }
  .bz-wert { font-size:13px; white-space:nowrap; }
  .bz-wert .dim { font-size:12px; margin-left:4px; }
  @media (max-width: 420px) { .balkenzeile { grid-template-columns:minmax(80px, 38%) 1fr auto; padding:9px 11px; } }

  /* ---------- Quellen und Logs ---------- */
  .quellzeile { display:grid; grid-template-columns:1fr auto auto; gap:6px 12px; align-items:center;
                padding:11px 14px; border-bottom:1px solid var(--line); }
  .quellzeile:last-of-type { border-bottom:0; }
  .qz-name { font-weight:600; font-size:14.5px; }
  .qz-neben { font-size:12.5px; color:var(--muted); line-height:1.4; }
  .qz-zeit { font-size:13px; text-align:right; white-space:nowrap; }
  .qz-zeit .klein { display:block; }
  .auftrag-details summary { cursor:pointer; color:var(--link); font-size:12.5px; }
  .auftrag-details pre, .logtext {
    margin:8px 0 0; padding:12px 14px; background:var(--bg); border-radius:10px;
    font-size:12px; line-height:1.5; white-space:pre-wrap; word-break:break-word;
    max-height:60vh; overflow:auto; font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .logtext { margin:0; border-radius:0; max-height:70vh; }
  @media (max-width: 480px) {
    .quellzeile { grid-template-columns:1fr auto; }
    .qz-knopf { grid-column:2; }
  }

  /* ---------- Bahnhofswahl: eine Zeile mit Vorschlägen ---------- */
  /* Schnellwahl und Suche in einer Zeile */
  .bahnhofzeile { display:flex; align-items:center; gap:6px; margin-bottom:12px; }
  .bahnhofzeile .f-chip { flex:0 0 auto; text-decoration:none; }
  .bahnhofwahl { position:relative; flex:1 1 120px; min-width:0; display:flex; align-items:center; gap:4px;
                 background:var(--card); border:1px solid var(--line); border-radius:20px;
                 padding:0 10px; min-height:36px; }
  .bahnhofwahl:focus-within { border-color:var(--akzent); }
  .bahnhofwahl.aktiv { border-color:var(--akzent); background:color-mix(in srgb, var(--akzent) 8%, var(--card)); }
  .bw-icon { font-size:13px; opacity:.7; }
  .bahnhofwahl input[type=search] {
    flex:1; min-width:0; border:0; background:transparent; color:var(--fg);
    font:inherit; font-size:16px; padding:7px 2px; outline:none;
  }
  .bahnhofwahl.aktiv input[type=search] { font-weight:600; }
  .bw-liste {
    position:absolute; right:0; top:calc(100% + 6px); z-index:40; width:min(320px, 86vw);
    background:var(--card); border:1px solid var(--line); border-radius:14px;
    box-shadow:0 10px 30px rgba(16,24,40,.18); max-height:60vh; overflow-y:auto;
  }
  .bw-kopf { padding:8px 14px 4px; font-size:11.5px; color:var(--muted); text-transform:uppercase;
             letter-spacing:.05em; }
  .bw-eintrag { padding:11px 14px; cursor:pointer; font-size:15px; border-top:1px solid var(--line); }
  .bw-kopf + .bw-eintrag, .bw-liste > .bw-eintrag:first-child { border-top:0; }
  .bw-eintrag:hover, .bw-eintrag.aktiv { background:var(--neutral-bg); }

  /* ---------- Suche ---------- */
  .suchzeile { display:flex; gap:6px; margin-bottom:10px; }
  .suchzeile input[type=search] {
    flex:1; min-width:0; font:inherit; font-size:15px; padding:9px 12px; border-radius:12px;
    border:1px solid var(--line); background:var(--card); color:var(--fg);
  }
  .suchzeile .knopf-klein { white-space:nowrap; display:inline-flex; align-items:center; text-decoration:none; }

  /* ---------- Über Zugradar ---------- */
  .ueber { text-align:center; padding:26px 20px 22px; }
  .ueber img { width:84px; height:84px; margin-bottom:10px; }
  .ueber-titel { font-size:24px; font-weight:700; letter-spacing:-.02em; }
  .ueber-sub { color:var(--muted); font-size:14px; margin-top:2px; }
  .ueber-von { margin-top:12px; font-size:15px; }
  .ueber-von a { text-decoration:none; }
  .ueber-version { margin-top:6px; font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }

  /* ---------- Abfahrtstafel im Fenster ---------- */
  .tf-zeile { display:grid; grid-template-columns:58px 1fr 34px; gap:2px 10px; align-items:center;
              padding:9px 0; border-bottom:1px solid var(--line); font-size:14px; }
  .tf-zeile:last-of-type { border-bottom:0; }
  .tf-zeile.ausfall .tf-zug { text-decoration:line-through; opacity:.6; }
  .tf-zeit { font-weight:700; font-variant-numeric:tabular-nums; line-height:1.2; }
  .tf-spaet { display:block; font-size:11.5px; color:var(--warn-fg); }
  .tf-weg { display:block; font-size:11.5px; color:var(--warn); }
  .tf-zug { line-height:1.35; }
  .tf-gleis { font-weight:700; color:var(--akzent); text-align:center; }
  .tf-gleis.neu { color:var(--warn); }

  /* ---------- Karte ---------- */
  .h2-link { float:right; text-transform:none; letter-spacing:0; font-weight:600; font-size:13px;
             text-decoration:none; }
  .kartenbox { overflow:hidden; }
  .karte { height:min(68vh, 560px); width:100%; background:var(--neutral-bg); }
  .karte-offline { padding:24px 16px; color:var(--muted); font-size:14px; text-align:center; }
  .karte-hinweis { padding:7px 12px; font-size:12.5px; font-weight:600; text-align:center;
                   background:var(--warn-bg); color:var(--warn-fg); }
  @media (prefers-color-scheme: dark) {
    /* Helle Kartenkacheln im dunklen Design abdunkeln, Beschriftung bleibt lesbar */
    .karte .kachel-invert .leaflet-tile, .karte .leaflet-tile.kachel-invert { filter:invert(1) hue-rotate(180deg) brightness(.85) contrast(.9); }
    .karte .leaflet-container { background:#0c1117; }
  }
  .leaflet-marker-icon.lok-marke { display:block; }
  .lok-marke span {
    display:inline-flex; align-items:center; justify-content:center; width:58px; height:24px;
    border-radius:12px; font:700 11.5px/1 system-ui, sans-serif; color:#fff;
    background:#5b6674; border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.35);
  }
  .lok-marke.unterwegs span { background:#8f2029; }
  .kartenlegende { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12.5px; color:var(--muted);
                   margin:10px 2px 0; }
  .kartenlegende span { display:inline-flex; align-items:center; gap:6px; }
  .kl-punkt { width:12px; height:12px; border-radius:50%; display:inline-block; border:2px solid #fff;
              box-shadow:0 0 0 1px var(--line); }
  .kl-punkt.lok { background:#8f2029; } .kl-punkt.steht { background:#5b6674; }
  .kl-punkt.abgestellt { background:#93a1b1; } .kl-punkt.umkreis { background:#f0a04b; }
  .kl-kreis { width:12px; height:12px; border-radius:50%; border:1.5px dashed #0a66d0; display:inline-block; }
  .leaflet-popup-content { font:13.5px/1.45 system-ui, sans-serif; }
  /* Bahnhöfe auf der Karte: 30 px Tippfläche, sichtbarer Punkt in der Mitte */
  /* Leaflets eigene Formatvorlage kommt nach unserer und setzt .leaflet-marker-icon auf
     display:block — dann ist der Punkt ein leeres Inline-Element, und übrig bleibt nur sein
     Rand als schmales Oval. Deshalb doppelte Klasse (höherer Vorrang) und span als Block. */
  .leaflet-marker-icon.halt-marke { display:flex; align-items:center; justify-content:center; cursor:pointer; }
  .halt-marke span { display:block; flex:0 0 auto; width:12px; height:12px; border-radius:50%; background:#fff;
                     border:3px solid #8f2029; box-shadow:0 0 0 1.5px #fff, 0 1px 3px rgba(0,0,0,.35); }
  .halt-marke.haupt span { width:18px; height:18px; border-width:4px; background:#fff;
                           box-shadow:0 0 0 2px #8f2029, 0 1px 4px rgba(0,0,0,.4); }
  .halt-marke:active span { transform:scale(1.25); }
  /* Alle Bahnhöfe: Punktgröße nach Rang (ICE, IC, RE, RB) */
  .leaflet-marker-icon.bf-marke { display:flex; align-items:center; justify-content:center; cursor:pointer; }
  .bf-marke span { display:block; border-radius:50%; background:#fff; border:2px solid #5b6674;
                   box-shadow:0 0 0 1px rgba(255,255,255,.8); width:7px; height:7px; }
  .bf-marke.r0 span { width:6px; height:6px; border-width:1.5px; opacity:.85; }
  .bf-marke.r1 span { width:8px; height:8px; border-color:#0a5ec7; }
  .bf-marke.r2 span { width:10px; height:10px; border-color:#2b3440; border-width:3px; }
  .bf-marke.r3 span { width:12px; height:12px; border-color:#c8102e; border-width:3px; }
  .leaflet-tooltip.bf-name { background:rgba(255,255,255,.85); border:0; box-shadow:none; color:#151b23;
                             font:600 11px/1.2 system-ui, sans-serif; padding:1px 4px; }
  .leaflet-tooltip.bf-name::before { display:none; }
  @media (prefers-color-scheme: dark) {
    .leaflet-tooltip.bf-name { background:rgba(12,17,23,.75); color:#e6edf3; }
  }
  .leaflet-tooltip.halt-name { background:rgba(255,255,255,.92); border:0; box-shadow:0 1px 3px rgba(0,0,0,.25);
                               color:#151b23; font:600 11.5px/1.2 system-ui, sans-serif; padding:2px 6px; }
  .leaflet-tooltip.halt-name::before { display:none; }
  .leaflet-tooltip.halt-name.haupt { font-size:13px; color:#8f2029; }
  /* Fahrende Züge: Marke ohne feste Größe, die Pille selbst mittig auf dem Punkt */
  .leaflet-marker-icon.zug-marke { width:0; height:0; overflow:visible; }
  .zug-marke span {
    position:absolute; left:0; top:0; transform:translate(-50%, -50%);
    display:inline-flex; align-items:center; gap:3px; white-space:nowrap; padding:2px 6px 2px 4px;
    border-radius:10px; font:700 10.5px/1 system-ui, sans-serif; color:#fff; background:#2b6a3f;
    border:1.5px solid #fff; box-shadow:0 1px 3px rgba(0,0,0,.35); transition:transform .2s;
  }
  .zug-marke svg { width:12px; height:12px; flex:0 0 auto; }
  /* Eigene Farbe je Gattung: RE blau, RB grün, ICE weiß mit rotem Rand, IC/EC anthrazit,
     FlixTrain grün-gelb, Nachtzüge violett */
  .zug-marke.re span  { background:#0a5ec7; }
  .zug-marke.rb span  { background:#2b6a3f; }
  .zug-marke.ice span { background:#f3f5f8; color:#151b23; border-color:#c8102e; }
  .zug-marke.ic span  { background:#2b3440; }
  .zug-marke.flx span { background:#3cb44a; }
  .zug-marke.nacht span { background:#5b3fa0; }
  .zug-marke.fern span { background:#e9edf2; color:#151b23; border-color:#5b6674; }
  .zug-marke.spaet span { box-shadow:0 0 0 2px #f0a04b, 0 1px 3px rgba(0,0,0,.35); }
  /* Steht er am Halt, sitzt die Pille darüber — der Bahnhof bleibt antippbar */
  .zug-marke.steht span { transform:translate(-50%, -135%); opacity:.9; }
  .zug-marke.z218 span { background:#c8102e; font-size:12px; padding:3px 8px 3px 5px;
                         border-width:2px; box-shadow:0 0 0 3px rgba(200,16,46,.35), 0 1px 4px rgba(0,0,0,.4); }
  .zug-marke.z218 svg { width:14px; height:14px; }
  /* Weit herausgezoomt nur das Zugsymbol — außer bei den 218ern */
  .karte.klein .zug-marke:not(.z218) b { display:none; }
  .karte.klein .zug-marke:not(.z218) span { padding:2px; }
  .leaflet-popup-content .rot { color:#c8102e; }
  .dz-kopf { font-weight:700; margin-bottom:6px; }
  .lk-aktionen { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 4px; }
  .lk-aktionen a { text-decoration:none; }
  /* Filtertafel über der Karte */
  .kartenkopf { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .filter-knopf { font:inherit; font-size:13.5px; font-weight:600; padding:8px 14px; border-radius:20px;
                  border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer;
                  white-space:nowrap; min-height:38px; }
  .filter-knopf.offen { background:var(--akzent); border-color:transparent; color:#fff; }
  .filter-stand { flex:1 1 auto; min-width:0; font:inherit; font-size:12.5px; color:var(--muted);
                  background:none; border:0; text-align:left; cursor:pointer; padding:0 2px;
                  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .filterblatt { background:var(--card); border:1px solid var(--line); border-radius:14px;
                 padding:12px 14px 14px; margin-bottom:10px; }
  .fb-gruppe + .fb-gruppe { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  .fb-gruppe h3 { margin:0 0 7px; font-size:11.5px; font-weight:700; letter-spacing:.06em;
                  text-transform:uppercase; color:var(--muted); }
  .fb-hinweis { text-transform:none; letter-spacing:0; font-weight:400; font-size:11px; }
  .fb-liste { display:flex; flex-wrap:wrap; gap:6px; }
  .fb-schalter { font:inherit; font-size:13px; padding:7px 12px; min-height:36px; border-radius:10px;
                 cursor:pointer; border:1px solid var(--line); background:var(--bg); color:var(--muted);
                 display:inline-flex; align-items:center; gap:6px; }
  .fb-schalter .fb-zeichen { font-size:13px; filter:grayscale(1); opacity:.7; }
  .fb-schalter.an { background:var(--akzent); border-color:transparent; color:#fff; font-weight:600; }
  .fb-schalter.an .fb-zeichen { filter:none; opacity:1; }
  .fb-schalter.fest { opacity:.75; cursor:default; }
  .fb-schalter.klemmt.an { box-shadow:inset 0 0 0 2px #f0a04b; }
  .fb-schalter.laedt { opacity:.6; }
  .fb-schalter.laedt::after { content:"…"; }
  .f-chip.laedt { opacity:.6; }
  .f-chip.laedt::after { content:" …"; }
  /* Tage: Lok-Suche */
  .lok-suche { margin-bottom:8px; }
  .lok-suche-feld { width:100%; box-sizing:border-box; font:inherit; font-size:16px; padding:9px 12px;
                    border:1px solid var(--line); border-radius:10px; background:var(--card); color:var(--fg); }
  .lok-suche-info { font-size:12.5px; color:var(--muted); margin:6px 2px 0; }
  /* Startseite: beobachtete Fahrten ganz oben */
  .beob-liste { display:grid; gap:8px; }
  .beob { padding:12px 14px; border-left:4px solid var(--akzent); }
  .beob-kopf { display:flex; align-items:center; gap:8px; flex-wrap:wrap; text-decoration:none; color:inherit; }
  .beob-punkt { width:10px; height:10px; border-radius:50%; background:var(--akzent); flex:0 0 auto; }
  .beob-unterwegs .beob-punkt, .beob-elmshorn .beob-punkt { animation:beob-puls 1.6s ease-in-out infinite; }
  @keyframes beob-puls { 50% { opacity:.25; } }
  .beob-lage { margin:6px 0 8px; font-size:15px; line-height:1.4; }
  .beob-lage .rot, .archiv-tag .rot { color:#c8102e; }
  .beob-fuss { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
  .beob-angekommen { border-left-color:var(--muted); opacity:.85; }
  .beob-angekommen .beob-punkt { background:var(--muted); }
  .beob-ausfall { border-left-color:#c8102e; }
  /* Archiv */
  .archiv-tag { padding:6px 14px; margin-bottom:10px; }
  .archiv-datum { font-weight:700; padding:6px 0 4px; }
  .archiv-zeile { display:grid; grid-template-columns:minmax(90px, 1fr) 3fr auto; gap:4px 10px; align-items:baseline;
                  padding:8px 0; border-top:1px solid var(--line); text-decoration:none; color:inherit; font-size:14px; }
  .archiv-ende { text-align:right; display:flex; flex-direction:column; align-items:flex-end; gap:2px; }
  @media (max-width:560px) {
    .archiv-zeile { grid-template-columns:1fr auto; }
    .archiv-fahrt { grid-column:1 / -1; grid-row:2; }
  }

  /* Startseite: In deiner Nähe */
  .naehe-start { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
  .naehe-text { font-size:14px; }
  .naehe-knopf { white-space:nowrap; }
  .naehe-status { font-size:12.5px; color:var(--muted); margin-top:8px; }
  .naehe-liste { margin-top:6px; }
  .naehe-liste li { font-size:13.5px; }
  .naehe-liste .rot { color:#c8102e; }
  .dz-standort { font-size:12.5px; color:#0a66d0; font-weight:600; margin-bottom:4px; }
  .standort-knopf a { display:flex; align-items:center; justify-content:center; color:#0a66d0; }
  .standort-knopf svg { width:18px; height:18px; }
  .standort-knopf a.sucht svg { animation:standort-puls 1s ease-in-out infinite; }
  @keyframes standort-puls { 50% { opacity:.3; } }
  .leaflet-marker-icon.ich-marke { display:flex; align-items:center; justify-content:center; }
  .ich-marke span { display:block; width:14px; height:14px; border-radius:50%; background:#0a66d0;
                    border:3px solid #fff; box-shadow:0 0 0 2px rgba(10,102,208,.35), 0 1px 4px rgba(0,0,0,.4); }
  .dz-kopf small { display:block; font-weight:400; color:var(--muted); font-size:12px; }
  .dz-laedt { color:var(--muted); }
  .dz-liste { list-style:none; margin:0; padding:0; }
  .dz-liste li { display:flex; gap:10px; padding:6px 0; border-top:1px solid var(--line); line-height:1.35; }
  .dz-liste li.z218 { background:rgba(200,16,46,.08); margin:0 -6px; padding:6px; border-radius:6px; }
  .dz-zeit { flex:0 0 52px; font-weight:700; font-variant-numeric:tabular-nums; }
  .dz-zeit small { display:block; font-weight:400; color:var(--muted); font-size:11.5px; }
  .dz-zug small { color:var(--muted); font-size:11.5px; }
  /* Fahrzeugzeile: immer eine Zeile fester Höhe — beim Nachladen springt die Liste nicht */
  .dz-fz { display:block; height:1.5em; line-height:1.5em; margin-top:2px; font-size:12px; white-space:nowrap;
           overflow:hidden; text-overflow:ellipsis; }
  .dz-fz .leise { color:var(--muted); }
  .dz-fz-knopf { font:inherit; font-size:12px; padding:0; border:0; background:none; color:var(--link);
                 cursor:pointer; line-height:1.5em; }
  .dz-zug { min-width:0; flex:1 1 auto; }
  /* Fahrzeuge: immer zwei einzeilige Zeilen, feste Höhe — nichts springt beim Nachladen */
  .zug-fz { margin:4px 0 6px; padding:5px 8px; border-radius:6px; background:rgba(143,32,41,.08);
            line-height:1.45; height:2.9em; box-sizing:content-box; }
  .zug-fz b, .zug-fz small { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .zug-fz small { color:var(--muted); font-size:12px; }
  .zug-fz .leise { color:var(--muted); font-weight:600; }
  .zug-lauf { margin:4px 0; padding:3px 0 3px 8px; border-left:3px solid #8f2029; line-height:1.5; }
  .zug-lauf span { display:inline-block; min-width:38px; font-variant-numeric:tabular-nums; color:var(--muted); }
  .kl-zug { width:18px; height:11px; border-radius:6px; display:inline-block; border:1.5px solid #fff;
            box-shadow:0 0 0 1px var(--line); background:#2b6a3f; }
  .kl-zug.z218 { background:#c8102e; } .kl-zug.fern { background:#e9edf2; box-shadow:0 0 0 1px #5b6674; }
  .kl-zug.re { background:#0a5ec7; } .kl-zug.rb { background:#2b6a3f; }
  .kl-zug.ice { background:#f3f5f8; box-shadow:0 0 0 1px #c8102e; }
  .kl-zug.ic { background:#2b3440; } .kl-zug.flx { background:#3cb44a; }
  .kl-zug.spaet { box-shadow:0 0 0 2px #f0a04b; }
  .kl-netz { width:18px; height:0; border-top:2px solid #7a8796; display:inline-block; }
  .kl-linie { width:18px; height:0; border-top:3px solid #1f7a4d; display:inline-block; }

  /* ---------- Menüseite "Mehr" ---------- */
  .liste a, .liste .zeile {
    display:flex; align-items:center; gap:14px; padding:15px 16px; border-bottom:1px solid var(--line);
    color:inherit; text-decoration:none; min-height:56px;
  }
  .liste a:last-child { border-bottom:0; }
  .liste a:active { background:var(--neutral-bg); }
  .liste .l-icon { width:36px; height:36px; border-radius:10px; display:grid; place-items:center;
                   background:var(--neutral-bg); color:var(--akzent); flex:0 0 36px; font-size:18px; }
  .liste .l-text { flex:1; min-width:0; }
  .liste .l-titel { font-weight:600; }
  .liste .l-neben { font-size:13px; color:var(--muted); }
  .liste .l-pfeil { color:var(--muted); }
  .liste a.gefahr .l-titel { color:var(--warn); }
CSS;
}

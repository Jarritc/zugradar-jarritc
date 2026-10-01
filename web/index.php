<?php
/**
 * Zugradar.
 *
 * Seiten (Parameter s): Start (nächste 218, Kurzlage, Plan für Elmshorn), lage (Echtzeit,
 * Meldungen, Sonderzüge, Umkreis), loks (Standorte, besondere 218er), tage (Tageslisten),
 * mehr (Benachrichtigungen, App, Info, Abmelden). Rahmen und Menü: _layout.php.
 * Daten liefern die Skripte in /opt/docker/kukas-zug (stündlich bzw. alle 10 min).
 * Diese Seite fragt bewusst keine API selbst ab, sie liest nur die Datenbank.
 */
declare(strict_types=1);

// Zugangsdaten, Schlüssel für die Merk-Links und Zeitzone (Europe/Berlin — ohne sie
// rechnete PHP in UTC und "veraltet" griff nie) stehen gemeinsam in _kukas.php.
require __DIR__ . '/_kukas.php';
require __DIR__ . '/_layout.php';

const SEITEN = ['start' => '', 'lage' => 'Live', 'loks' => 'Loks', 'tage' => 'Tage',
                'mehr' => 'Mehr', 'verlauf' => 'Verlauf', 'info' => 'Über Zugradar',
                'fahrt' => 'Fahrt', 'statistik' => 'Statistik',
                'quellen' => 'Quellen', 'logs' => 'Logs', 'karte' => 'Karte',
                'archiv' => 'Archiv'];
$seite = (string)($_GET['s'] ?? 'start');
if (!isset(SEITEN[$seite])) { $seite = 'start'; }
// Der Verlauf hat keinen eigenen Menüpunkt, er hängt unter "Mehr".
$menueAktiv = match ($seite) {
    'verlauf', 'info', 'statistik', 'quellen', 'logs', 'karte', 'archiv' => 'mehr',
    'fahrt' => 'start',
    default => $seite,
};

session_start();
$fehler = '';
angemeldet();                           // stellt die Sitzung aus dem Merk-Cookie wieder her

if (isset($_GET['logout'])) {
    abmelden();
    header('Location: ./');
    exit;
}

if (!empty($_POST['pw'])) {
    if (passwort_ok((string)$_POST['pw'])) {
        session_regenerate_id(true);
        login_merken();
    } else {
        sleep(1);                       // bremst stumpfes Durchprobieren
        $fehler = 'Falsches Passwort.';
    }
}

function h(?string $s): string {
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

// Schutz gegen untergeschobene Formulare: jede Aktion trägt ein Sitzungsgeheimnis.
if (empty($_SESSION['csrf'])) {
    $_SESSION['csrf'] = bin2hex(random_bytes(16));
}

// Live-Ansicht anderer Bahnhöfe. Feste Auswahl wie in bahnhof.py — kein freier Text
// aus dem Web, und die Nachbarschaft der Marschbahn deckt ab, was hier interessiert.
// Schnellauswahl; gesucht werden darf jeder Bahnhof. Der Name geht an die
// Bahn-Schnittstelle, deshalb streng geprüft (wie bahnhof.NAME_RE).
const BAHNHOEFE = ['Elmshorn', 'Pinneberg', 'Tornesch', 'Itzehoe', 'Wrist', 'Husum',
                   'Niebüll', 'Westerland(Sylt)', 'Hamburg-Altona', 'Heide(Holst)'];
$gesucht = trim((string)($_GET['bf'] ?? ''));
$bahnhofUngueltig = $gesucht !== '' && !preg_match("/^[\p{L}\d .()\/'-]{2,40}$/u", $gesucht);
$bahnhof = ($gesucht !== '' && !$bahnhofUngueltig) ? $gesucht : 'Elmshorn';

// "Jetzt prüfen": Die Webseite darf die Skripte nicht selbst starten (Zugangsdaten
// gehören dem Hintergrunddienst), sie schreibt nur einen Auftrag. auftrag.py führt ihn aus.
const AUFTRAEGE = ['marschbahn', 'stoerung', 'sichtungen', 'sonderzuege', 'beobachten',
                   'erinnerung', 'besondere_loks', 'wacht'];
if (!empty($_SESSION['kukas']) && ($_POST['aktion'] ?? '') === 'pruefen'
        && hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? ''))) {
    $skript = (string)($_POST['skript'] ?? '');
    if (in_array($skript, AUFTRAEGE, true)) {
        kukas_db()->prepare('INSERT INTO auftraege (skript, angefordert_am) VALUES (?, NOW())')
            ->execute([$skript]);
    }
    header('Location: ./?s=quellen');
    exit;
}

// Einstellungen, die den Versand betreffen, gelten für alle Geräte und stehen in meta.
// (config.json kann die Webseite nicht schreiben, sie gehört dem Hintergrunddienst.)
const EINSTELLUNGEN = [
    'merken_live_meldung' => ['0', '1'],
    'merken_live_ab_minuten' => ['15', '30', '60', '120'],
    'merken_live_festhalten' => ['0', '1'],
];
if (!empty($_SESSION['kukas']) && ($_POST['aktion'] ?? '') === 'einstellungen'
        && hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? ''))) {
    $pdo = kukas_db();
    foreach (EINSTELLUNGEN as $name => $erlaubt) {
        $wert = (string)($_POST[$name] ?? '');
        if (in_array($wert, $erlaubt, true)) {
            $pdo->prepare('INSERT INTO meta (schluessel, wert) VALUES (?, ?)
                           ON DUPLICATE KEY UPDATE wert = VALUES(wert)')
                ->execute(['einst_' . $name, $wert]);
        }
    }
    header('Location: ./?s=mehr&gespeichert=1#einstellungen');
    exit;
}

// "Diese Fahrt interessiert mich" / "nicht mehr beobachten" — nur angemeldet.
if (!empty($_SESSION['kukas']) && isset($_POST['aktion'])
        && hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? ''))) {
    $tag = (string)($_POST['tag'] ?? '');
    $lok = (string)($_POST['lok'] ?? '');
    $zug = (string)($_POST['zug'] ?? '');
    if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $tag) && $lok !== '' && $zug !== '') {
        $pdo = kukas_db();
        if ($_POST['aktion'] === 'merken' && $tag >= date('Y-m-d')) {
            $st = $pdo->prepare('SELECT von_halt, von_zeit, nach_halt, nach_zeit FROM umlaeufe
                                  WHERE tag = ? AND lok = ? AND zug = ? ORDER BY von_zeit LIMIT 1');
            $st->execute([$tag, $lok, $zug]);
            $f = $st->fetch() ?: [];
            $pdo->prepare("INSERT INTO interesse (tag, lok, zug, von_halt, von_zeit, nach_halt, nach_zeit,
                                  quelle, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,?,'web',1,NOW())
                           ON DUPLICATE KEY UPDATE aktiv = 1, archiviert_am = NULL, archiv_grund = NULL")
                ->execute([$tag, $lok, $zug, $f['von_halt'] ?? null, $f['von_zeit'] ?? null,
                           $f['nach_halt'] ?? null, $f['nach_zeit'] ?? null]);
        } elseif ($_POST['aktion'] === 'entfernen') {
            $pdo->prepare("UPDATE interesse SET aktiv = 0, archiviert_am = NOW(), archiv_grund = 'entfernt'
                            WHERE tag = ? AND lok = ? AND zug = ? AND aktiv = 1")
                ->execute([$tag, $lok, $zug]);
        }
    }
    // Nach dem Absenden neu laden (sonst schickt Aktualisieren doppelt ab) — zurück auf
    // die Seite, von der der Knopf kam.
    $zurueck = (string)($_POST['zurueck'] ?? 'start');
    $ziel = match ($zurueck) {
        'tage'  => './?s=tage' . (isset($_GET['treffer']) ? '&treffer' : ''),
        'start' => './',
        default => isset(SEITEN[$zurueck]) ? './?s=' . $zurueck : './',
    };
    header('Location: ' . $ziel . (str_starts_with((string)$_POST['aktion'], 'entfernen') ? '#beobachtet' : '#plan'));
    exit;
}

/** Kleines Formular für einen Merk- oder Entfernen-Knopf. */
function merkknopf(string $aktion, string $tag, string $lok, string $zug, string $beschriftung, string $klasse): string {
    return '<form method="post" class="merkform">'
         . '<input type="hidden" name="csrf" value="' . h($_SESSION['csrf']) . '">'
         . '<input type="hidden" name="aktion" value="' . h($aktion) . '">'
         . '<input type="hidden" name="zurueck" value="' . h((string)($GLOBALS['seite'] ?? 'start')) . '">'
         . '<input type="hidden" name="tag" value="' . h($tag) . '">'
         . '<input type="hidden" name="lok" value="' . h($lok) . '">'
         . '<input type="hidden" name="zug" value="' . h($zug) . '">'
         . '<button type="submit" class="' . h($klasse) . '">' . h($beschriftung) . '</button></form>';
}

if (empty($_SESSION['kukas'])) {
    ?><!doctype html>
    <html lang="de"><head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
    <meta name="robots" content="noindex,nofollow">
    <meta name="theme-color" content="#8f2029">
    <title>Zugradar</title>
    <link rel="manifest" href="manifest.webmanifest">
    <link rel="icon" href="favicon.ico" sizes="any">
    <link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
    <link rel="apple-touch-icon" href="apple-touch-icon.png">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="Zugradar">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <style>
    <?= grundstil() ?>
      body { min-height:100dvh; display:grid; place-items:center; padding:24px; }
      form {
        background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
        padding:28px 24px; width:min(360px,100%); box-shadow:var(--schatten);
      }
      .zeichen { display:block; width:72px; height:72px; margin-bottom:10px; }
      h1 { margin:0 0 2px; font-size:20px; letter-spacing:-.01em; }
      p.sub { margin:0 0 20px; color:var(--muted); font-size:14px; }
      label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }
      input {
        width:100%; padding:13px 14px; font-size:16px; border:1px solid var(--line);
        border-radius:10px; margin-bottom:14px; background:var(--bg); color:var(--fg);
      }
      input:focus { outline:2px solid var(--akzent); outline-offset:1px; border-color:transparent; }
      button {
        width:100%; padding:13px; font-size:16px; font-weight:600; border:0;
        border-radius:10px; background:var(--akzent); color:#fff; cursor:pointer;
        min-height:48px;
      }
      button:active { filter:brightness(.92); }
      p.mini { margin:16px 0 0; text-align:center; font-size:12px; color:var(--muted); }
      .err {
        color:var(--warn-fg); background:var(--warn-bg); font-size:14px;
        padding:10px 12px; border-radius:10px; margin:0 0 14px;
      }
    </style></head><body>
    <form method="post">
      <img class="zeichen" src="icon-192.png" alt="" width="72" height="72">
      <h1>Zugradar</h1>
      <?php if ($fehler): ?><p class="err"><?= h($fehler) ?></p><?php endif; ?>
      <label for="pw">Passwort</label>
      <input id="pw" type="password" name="pw" autofocus autocomplete="current-password"
             autocapitalize="off" autocorrect="off" spellcheck="false">
      <button type="submit">Anmelden</button>
      <p class="mini">© <?= date('Y') ?> jarritc.de · privat</p>
    </form>
    <script>if ('serviceWorker' in navigator) { navigator.serviceWorker.register('sw.js'); }</script>
    </body></html><?php
    exit;
}

// ---------------------------------------------------------------- Daten holen
$nurTreffer     = $seite === 'tage' && isset($_GET['treffer']);
$tage           = [];
$laeufe         = [];
$meldungen      = [];
$letzterLauf    = null;
$lage           = null;
$meldungenHeute = [];
$sichtungen     = [];
$standorte      = [];
$standortTag    = null;
$lokNamen       = [];
$lokQuellen     = [];
$umlaeufe       = [];
$besondere      = [];
$standortHeute  = [];
$interessen     = [];
$naechste       = null;
$sonderPlan     = [];
$verlauf        = [];
$zahlen         = [];
$einstellungen  = [];
$fahrt          = null;
$fahrtTag       = [];
$fahrtQuellen   = [];
$statistik      = [];
$bahnhofDaten   = null;
$bahnhofStand   = null;
$bahnhofEva     = null;
$bahnhofFehler  = '';
$bahnhofWartet  = false;
$zuletztBahnhoefe = [];
$karte = null;
$laeufeMeta     = [];
$quellenStand   = [];
$auftraege      = [];
$verlaufZahl    = 0;
$sichtungen24   = 0;
$dbFehler       = '';

const QUELLE_LABEL = ['regio' => 'DB Regio', 'shuttle' => 'SyltShuttle / ICE'];
// Zwei Zugarten mit 218: Regionalverkehr (RE6) und die Autozüge/IC nach Sylt.
const QUELLE_KURZ = ['regio' => 'RE6', 'shuttle' => 'SyltShuttle'];

/**
 * Etikett für die Lok selbst: fährt sie im Regionalverkehr (RE6), vor den Autozügen
 * nach Sylt (SyltShuttle) oder beides? Aus ihren Umläufen der letzten 60 Tage.
 */
function lokEinsatz(string $lok, array $lokQuellen): string {
    $q = $lokQuellen[$lok] ?? [];
    $regio = isset($q['regio']);
    $shuttle = isset($q['shuttle']);
    if ($regio && $shuttle) {
        return '<span class="etikett quelle-beide" title="fährt beides">RE6 + Shuttle</span>';
    }
    if ($regio) {
        return '<span class="etikett quelle-regio" title="Regionalverkehr, '
             . (int)$q['regio']['fahrten'] . ' Fahrten in 60 Tagen">RE6</span>';
    }
    if ($shuttle) {
        return '<span class="etikett quelle-shuttle" title="Autozüge und IC nach Sylt, '
             . (int)$q['shuttle']['fahrten'] . ' Fahrten in 60 Tagen">SyltShuttle</span>';
    }
    return '';
}

/** Adresse der Seite zu genau dieser Fahrt — auch Ziel der Benachrichtigungen. */
function fahrtUrl(string $tag, string $lok, string $zug): string {
    return './?' . http_build_query(['s' => 'fahrt', 'tag' => $tag, 'lok' => $lok, 'zug' => $zug]);
}

/** Kleines Etikett, das die Herkunft eines Umlaufs zeigt. */
function quelleEtikett(?string $quelle): string {
    if (!$quelle || !isset(QUELLE_KURZ[$quelle])) { return ''; }
    return '<span class="etikett quelle-' . h($quelle) . '">' . h(QUELLE_KURZ[$quelle]) . '</span>';
}
const BRETT_LABEL  = ['004' => 'Bild-Sichtungen', '109' => 'Betriebsstörungen'];

try {
    $pdo = kukas_db();

    // Pro Tag gibt es bis zu zwei Threads (DB Regio und SyltShuttle) — hier
    // werden sie zu einer Tageszeile zusammengefasst.
    foreach ($pdo->query('SELECT * FROM tage ORDER BY tag DESC, quelle')->fetchAll() as $t) {
        $tag = $t['tag'];
        if (!isset($tage[$tag])) {
            $tage[$tag] = ['tag' => $tag, 'treffer' => false, 'quellen' => [],
                           'geprueft' => $t['zuletzt_geprueft']];
        }
        $tage[$tag]['treffer'] = $tage[$tag]['treffer'] || (bool)$t['hat_elmshorn'];
        $tage[$tag]['quellen'][] = $t;
        if ($t['zuletzt_geprueft'] > $tage[$tag]['geprueft']) {
            $tage[$tag]['geprueft'] = $t['zuletzt_geprueft'];
        }
    }
    if ($nurTreffer) {
        $tage = array_filter($tage, static fn(array $t): bool => $t['treffer']);
    }

    foreach ($pdo->query('SELECT * FROM laeufe ORDER BY tag DESC, lok, von_zeit')->fetchAll() as $l) {
        $laeufe[$l['tag']][] = $l;
    }
    foreach ($pdo->query('SELECT * FROM tagesmeldung')->fetchAll() as $m) {
        $meldungen[$m['tag']] = $m;
    }

    $letzterLauf = $pdo->query(
        'SELECT * FROM laeufe_log ORDER BY gestartet DESC LIMIT 1')->fetch() ?: null;

    // Momentaufnahme der Echtzeitlage, vom Störungswächter geschrieben.
    $lage = $pdo->query('SELECT * FROM lage WHERE id = 1')->fetch() ?: null;
    if ($lage) {
        $lage['daten'] = json_decode((string)$lage['daten'], true) ?: [];
    }

    $meldungenHeute = $pdo->query(
        'SELECT * FROM stoerungen WHERE tag = CURDATE() AND verschickt = 1 ORDER BY gemeldet_am DESC'
    )->fetchAll();

    $sichtungen = $pdo->query(
        'SELECT * FROM sichtungen WHERE treffer = 1 ORDER BY gesehen_am DESC LIMIT 25'
    )->fetchAll();

    // Alle Fahrten der 218er, nicht nur die durch Elmshorn.
    foreach ($pdo->query(
        'SELECT * FROM umlaeufe ORDER BY tag DESC, lok, von_zeit')->fetchAll() as $u) {
        $umlaeufe[$u['tag']][] = $u;
    }

    // Besondere 218er laut Wikipedia (Name, Lackierung, Werbung) — Grundlage für
    // Namen. Die im Forum selbst gesehenen Namen gehen darüber.
    try {
        foreach ($pdo->query(
            "SELECT b.*,
                    (SELECT MAX(tag) FROM standorte s WHERE s.lok = b.lok) AS zuletzt_stand,
                    (SELECT ort FROM standorte s WHERE s.lok = b.lok ORDER BY tag DESC LIMIT 1) AS zuletzt_ort,
                    (SELECT MAX(tag) FROM umlaeufe u WHERE u.lok = b.lok) AS zuletzt_gefahren
               FROM besondere_loks b ORDER BY b.lok")->fetchAll() as $r) {
            $besondere[$r['lok']] = $r;
            if ($r['name']) { $lokNamen[$r['lok']] = $r['name']; }
        }
    } catch (Throwable $e) { /* Tabelle noch nicht angelegt */ }

    // Eigennamen der Loks ("Donna", "Konrad") — im Forum gesehen, gelten dauerhaft.
    foreach ($pdo->query('SELECT lok, name FROM lok_namen')->fetchAll() as $r) {
        $lokNamen[$r['lok']] = $r['name'];
    }

    // Wo die Loks stehen — aus dem jeweils neuesten Tag, nach Ort gebündelt.
    foreach ($pdo->query(
        'SELECT * FROM standorte WHERE tag = (SELECT MAX(tag) FROM standorte)
          ORDER BY ort, lok')->fetchAll() as $st) {
        $standorte[$st['ort']][] = $st;
        $standortTag = $st['tag'];
    }

    // Loks, die im Beitrag nur unter ihrem Umlauf stehen, nennen keinen Standort.
    // Ohne sie fehlte ausgerechnet die, die heute fährt.
    if ($standortTag) {
        $bekannt = [];
        foreach ($standorte as $g) {
            foreach ($g as $x) { $bekannt[$x['lok']] = true; }
        }
        $stmt = $pdo->prepare('SELECT loks_218 FROM tage WHERE tag = ?');
        $stmt->execute([$standortTag]);
        $unterwegs = [];
        foreach ($stmt->fetchAll() as $r) {
            foreach (array_filter(array_map('trim', explode(',', (string)$r['loks_218']))) as $lok) {
                if (!isset($bekannt[$lok])) { $unterwegs[$lok] = true; }
            }
        }
        if ($unterwegs) {
            $standorte['im Einsatz'] = array_map(
                static function (string $lok) use ($umlaeufe, $standortTag): array {
                    // Wohin sie fährt: erster Start, letztes Ziel, Zahl der Fahrten.
                    $f = array_values(array_filter($umlaeufe[$standortTag] ?? [],
                        static fn(array $u): bool => $u['lok'] === $lok));
                    // Pendelt eine Lok, wäre "Husum → Husum" nichtssagend. Deshalb
                    // alle angefahrenen Halte in der Reihenfolge des ersten Besuchs.
                    $halte = [];
                    foreach ($f as $u) {
                        $halte[$u['von_halt']] = true;
                        $halte[$u['nach_halt']] = true;
                    }
                    $weg = $f
                        ? implode(' · ', array_keys($halte)) . ' — ' . count($f) . ' Fahrten, '
                          . ($f[0]['von_zeit'] ?? '') . '–' . ($f[count($f) - 1]['nach_zeit'] ?? '')
                        : '';
                    return ['lok' => $lok, 'hat_umlauf' => 1, 'weg' => $weg];
                }, array_keys($unterwegs));
        }
    }

    // Wo eine Lok zuletzt eingesetzt war: Regionalverkehr, SyltShuttle oder beides.
    foreach ($pdo->query('SELECT lok, quelle, COUNT(*) AS n, MAX(tag) AS zuletzt
                            FROM umlaeufe WHERE tag >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                           GROUP BY lok, quelle')->fetchAll() as $z) {
        $lokQuellen[$z['lok']][$z['quelle']] = ['fahrten' => (int)$z['n'], 'zuletzt' => $z['zuletzt']];
    }

    // Vorgemerkte Fahrten ab gestern (Fahrten über Mitternacht), Schlüssel "tag|lok|zug".
    // Die Zeit durch Elmshorn kommt aus dem Umlauf.
    $st = $pdo->prepare("SELECT i.*, (SELECT u.elmshorn_zeit FROM umlaeufe u
                                        WHERE u.tag = i.tag AND u.lok = i.lok AND u.zug = i.zug AND u.elmshorn = 1
                                        ORDER BY u.von_zeit LIMIT 1) AS elmshorn_zeit
                           FROM interesse i WHERE i.aktiv = 1 AND i.tag >= ? ORDER BY i.tag, i.von_zeit");
    $st->execute([date('Y-m-d', strtotime('-1 day'))]);
    foreach ($st->fetchAll() as $r) { $interessen[$r['tag'] . '|' . $r['lok'] . '|' . $r['zug']] = $r; }

    // Für "Wo ist sie gerade": heutige Standorte je Lok.
    $standortHeute = [];
    $stmt = $pdo->prepare('SELECT lok, ort FROM standorte WHERE tag = ?');
    $stmt->execute([date('Y-m-d')]);
    foreach ($stmt->fetchAll() as $r) { $standortHeute[$r['lok']] = $r['ort']; }

    // Die nächste noch bevorstehende Durchfahrt — das ist die Zahl, für die man
    // die Seite überhaupt aufruft.
    $naechste = $pdo->query(
        "SELECT * FROM laeufe
          WHERE aktiv = 1 AND elmshorn_zeit IS NOT NULL
            AND (tag > CURDATE() OR (tag = CURDATE() AND elmshorn_zeit >= TIME_FORMAT(NOW(),'%H:%i')))
          ORDER BY tag, elmshorn_zeit LIMIT 1")->fetch() ?: null;

    // Sonderzüge durch Elmshorn ab heute — für den Plan und die Lage.
    try {
        $sonderPlan = $pdo->query('SELECT * FROM sonderzuege WHERE tag >= CURDATE() ORDER BY tag, zeit')->fetchAll();
    } catch (Throwable $e) { /* Tabelle noch nicht angelegt */ }

    // Benachrichtigungsverlauf: was wann über welchen Weg rausging.
    if ($seite === 'verlauf') {
        $gruppen = ['218' => ['treffer', 'tagesmeldung_treffer', 'tagesmeldung_leer', 'streichung',
                              'ausfall_218', 'nachtrag', 'beobachtung', 'erinnerung'],
                    'lage' => ['stoerung', 'entwarnung', 'wacht'],
                    'umkreis' => ['sichtung', 'sonderzug'],
                    'test' => ['test']];
        $wahl = (string)($_GET['art'] ?? '');
        $suche = trim((string)($_GET['q'] ?? ''));
        $bedingungen = [];
        $werte = [];
        if (isset($gruppen[$wahl])) {
            $bedingungen[] = 'art IN (' . implode(',', array_fill(0, count($gruppen[$wahl]), '?')) . ')';
            $werte = $gruppen[$wahl];
        }
        if ($suche !== '') {
            // Suche über Betreff und Kurztext, z. B. nach "Konrad" oder "11026".
            $bedingungen[] = '(betreff LIKE ? OR text LIKE ?)';
            $werte[] = '%' . $suche . '%';
            $werte[] = '%' . $suche . '%';
        }
        $wo = $bedingungen ? ' WHERE ' . implode(' AND ', $bedingungen) : '';
        $st = $pdo->prepare('SELECT l.*, w.verschickt_am, w.ergebnis FROM meldungen_log l
                              LEFT JOIN push_warteschlange w ON w.id = l.warteschlange_id'
                            . $wo . ' ORDER BY l.erstellt_am DESC, l.id DESC LIMIT 200');
        $st->execute($werte);
        $verlauf = $st->fetchAll();
        $verlaufZahl = (int)$pdo->query('SELECT COUNT(*) FROM meldungen_log')->fetchColumn();
    }

    // Gespeicherte Einstellungen für die Seite "Mehr"
    if ($seite === 'mehr') {
        foreach ($pdo->query("SELECT schluessel, wert FROM meta WHERE schluessel LIKE 'einst\_%'")
                     ->fetchAll() as $z) {
            $einstellungen[substr((string)$z['schluessel'], 6)] = (string)$z['wert'];
        }
    }

    // Eine einzelne Fahrt (Ziel der Benachrichtigungen)
    if ($seite === 'fahrt') {
        $fTag = (string)($_GET['tag'] ?? '');
        $fLok = (string)($_GET['lok'] ?? '');
        $fZug = (string)($_GET['zug'] ?? '');
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $fTag) && $fLok !== '' && $fZug !== '') {
            $st = $pdo->prepare('SELECT * FROM laeufe WHERE tag = ? AND lok = ? AND zug = ? LIMIT 1');
            $st->execute([$fTag, $fLok, $fZug]);
            $fahrt = $st->fetch() ?: null;
            if (!$fahrt) {
                $st = $pdo->prepare('SELECT *, NULL AS elmshorn_zeit, NULL AS gleis, 1 AS aktiv
                                       FROM umlaeufe WHERE tag = ? AND lok = ? AND zug = ? LIMIT 1');
                $st->execute([$fTag, $fLok, $fZug]);
                $fahrt = $st->fetch() ?: null;
            }
            $st = $pdo->prepare('SELECT * FROM umlaeufe WHERE tag = ? AND lok = ? ORDER BY von_zeit');
            $st->execute([$fTag, $fLok]);
            $fahrtTag = $st->fetchAll();
            $st = $pdo->prepare('SELECT quelle, url FROM tage WHERE tag = ?');
            $st->execute([$fTag]);
            $fahrtQuellen = $st->fetchAll();
        }
    }

    // Statistik
    if ($seite === 'statistik') {
        $statistik['loks'] = $pdo->query(
            'SELECT lok, COUNT(*) AS fahrten, COUNT(DISTINCT tag) AS tage, MAX(tag) AS zuletzt
               FROM laeufe GROUP BY lok ORDER BY fahrten DESC, lok')->fetchAll();
        $statistik['wochentage'] = $pdo->query(
            'SELECT DAYOFWEEK(tag) AS wt, COUNT(DISTINCT tag) AS tage, COUNT(*) AS fahrten
               FROM laeufe GROUP BY wt')->fetchAll();
        $statistik['stunden'] = $pdo->query(
            "SELECT LEFT(elmshorn_zeit, 2) AS stunde, COUNT(*) AS n FROM laeufe
              WHERE elmshorn_zeit IS NOT NULL GROUP BY stunde ORDER BY stunde")->fetchAll();
        $statistik['richtung'] = $pdo->query(
            'SELECT richtung, COUNT(*) AS n FROM laeufe GROUP BY richtung')->fetchAll();
        $statistik['quelle'] = $pdo->query(
            'SELECT quelle, COUNT(*) AS n FROM umlaeufe GROUP BY quelle')->fetchAll();
        $statistik['tage_gesamt'] = (int)$pdo->query('SELECT COUNT(*) FROM tage')->fetchColumn();
        $statistik['tage_treffer'] = (int)$pdo->query('SELECT COUNT(DISTINCT tag) FROM laeufe')->fetchColumn();
        $statistik['erste'] = $pdo->query('SELECT MIN(tag) FROM tage')->fetchColumn();
        $statistik['gleise'] = $pdo->query(
            'SELECT gleis, COUNT(*) AS n FROM laeufe WHERE gleis IS NOT NULL
              GROUP BY gleis ORDER BY n DESC')->fetchAll();
    }

    // Schon einmal gesuchte Bahnhöfe stehen als Knopf bereit.
    if ($seite === 'lage') {
        $zuletztBahnhoefe = array_column($pdo->query(
            "SELECT name FROM bahnhof_lage WHERE status = 'fertig' ORDER BY geholt_am DESC LIMIT 8")
            ->fetchAll(), 'name');
    }

    // Anderer Bahnhof: Abfahrtstafel aus der Ablage, sonst beim Hintergrunddienst anfragen.
    if ($seite === 'lage' && $bahnhof !== 'Elmshorn') {
        $st = $pdo->prepare('SELECT * FROM bahnhof_lage WHERE name = ?');
        $st->execute([$bahnhof]);
        $bf = $st->fetch() ?: null;
        $frisch = $bf && $bf['geholt_am'] !== null
                  && strtotime((string)$bf['geholt_am']) > time() - 300;
        if ($frisch && $bf['status'] === 'fertig') {
            $bahnhofDaten = json_decode((string)$bf['daten'], true) ?: [];
            $bahnhofStand = (string)$bf['geholt_am'];
            $bahnhofEva = (int)$bf['eva'];
        } elseif ($frisch && $bf['status'] === 'fehler') {
            $bahnhofFehler = (string)$bf['fehler'];
        } else {
            $pdo->prepare('INSERT INTO bahnhof_lage (name, status, angefragt_am)
                           VALUES (?, "offen", NOW())
                           ON DUPLICATE KEY UPDATE status = "offen", angefragt_am = NOW()')
                ->execute([$bahnhof]);
            $bahnhofWartet = true;
            // Der Hintergrunddienst antwortet in ein paar Sekunden — solange die alte
            // Tafel zeigen, falls es eine gibt.
            if ($bf && $bf['daten']) {
                $bahnhofDaten = json_decode((string)$bf['daten'], true) ?: [];
                $bahnhofStand = (string)$bf['geholt_am'];
                $bahnhofEva = (int)$bf['eva'];
            }
        }
    }

    // Karte: Strecke, 218er laut Plan, Standorte, Umkreis
    if ($seite === 'karte') {
        $koord = [];
        $strecke = [];
        foreach ($pdo->query('SELECT * FROM koordinaten ORDER BY strecke_nr IS NULL, strecke_nr')->fetchAll() as $k) {
            $koord[(string)$k['name']] = [(float)$k['lat'], (float)$k['lon']];
            if ($k['strecke_nr'] !== null) { $strecke[] = (string)$k['name']; }
        }
        $karte = ['strecke' => array_map(static fn(string $n) => ['name' => $n, 'p' => $koord[$n]], $strecke),
                  'gleise' => json_decode((string)($pdo->query("SELECT wert FROM meta WHERE schluessel = 'karte_gleise'")
                                                         ->fetchColumn() ?: '[]'), true) ?: [],
                  'loks' => [], 'standorte' => [], 'umkreis' => []];
        $gleise = $karte['gleise'];
        // Zu jedem Halt der nächstgelegene Punkt auf dem Gleis — für Positionen entlang der Strecke.
        $gleisIndex = [];
        foreach ($strecke as $name) {
            $best = null; $bestD = INF;
            foreach ($gleise as $i => $g) {
                $dd = ($g[0] - $koord[$name][0]) ** 2 + (($g[1] - $koord[$name][1]) * 0.6) ** 2;
                if ($dd < $bestD) { $bestD = $dd; $best = $i; }
            }
            if ($best !== null) { $gleisIndex[$name] = $best; }
        }
        // Die Geokodierung liefert oft die Ortsmitte ("Husum" liegt 600 m neben dem Bahnhof).
        // Bahnhöfe deshalb auf den nächsten Gleispunkt setzen — sofern der nah genug ist.
        // Lotfußpunkt auf dem nächsten Abschnitt der Gleislinie — nicht bloß der nächste
        // Linienpunkt, denn auf langen Geraden hat die vereinfachte Linie kaum Punkte.
        $naechsterGleispunkt = static function (array $p, float $maxKm) use ($gleise): ?array {
            $k = cos(deg2rad($p[0]));
            $best = null; $bestD = INF;
            for ($i = 1, $n = count($gleise); $i < $n; $i++) {
                [$ay, $ax] = [$gleise[$i - 1][0], $gleise[$i - 1][1] * $k];
                [$by, $bx] = [$gleise[$i][0], $gleise[$i][1] * $k];
                [$py, $px] = [$p[0], $p[1] * $k];
                $dx = $bx - $ax; $dy = $by - $ay;
                $l2 = $dx * $dx + $dy * $dy;
                $t = $l2 > 0 ? max(0.0, min(1.0, (($px - $ax) * $dx + ($py - $ay) * $dy) / $l2)) : 0.0;
                $qx = $ax + $t * $dx; $qy = $ay + $t * $dy;
                $dd = ($px - $qx) ** 2 + ($py - $qy) ** 2;
                if ($dd < $bestD) { $bestD = $dd; $best = [$qy, $qx / $k]; }
            }
            return ($best && sqrt($bestD) * 111.3 <= $maxKm) ? [round($best[0], 6), round($best[1], 6)] : null;
        };
        foreach ($karte['strecke'] as &$halt) {
            if ($g = $naechsterGleispunkt($halt['p'], 3.0)) { $halt['p'] = $g; }
        }
        unset($halt);

        // Position entlang der Strecke: Abstand der Punkte aufsummieren, dann anteilig.
        $abstand = static fn(array $a, array $b): float => hypot($a[0] - $b[0], ($a[1] - $b[1]) * cos(deg2rad($a[0])));
        $streckenPunkt = static function (string $von, string $nach, float $anteil) use ($koord, $strecke, $abstand, $gleise, $gleisIndex): ?array {
            // Am liebsten auf dem echten Gleis: Punkte zwischen den beiden Halten abgehen.
            if (isset($gleisIndex[$von], $gleisIndex[$nach]) && $gleisIndex[$von] !== $gleisIndex[$nach]) {
                $a = $gleisIndex[$von]; $b = $gleisIndex[$nach]; $sch = $b > $a ? 1 : -1;
                $pkt = [];
                for ($i = $a; $i !== $b + $sch; $i += $sch) { $pkt[] = $gleise[$i]; }
                $lng = [];
                for ($i = 1; $i < count($pkt); $i++) { $lng[] = $abstand($pkt[$i - 1], $pkt[$i]); }
                $rest = array_sum($lng) * max(0.0, min(1.0, $anteil));
                foreach ($lng as $i => $l) {
                    if ($rest <= $l || $i === count($lng) - 1) {
                        $t = $l > 0 ? min(1.0, $rest / $l) : 0.0;
                        return [$pkt[$i][0] + ($pkt[$i + 1][0] - $pkt[$i][0]) * $t,
                                $pkt[$i][1] + ($pkt[$i + 1][1] - $pkt[$i][1]) * $t];
                    }
                    $rest -= $l;
                }
            }
            $iv = array_search($von, $strecke, true);
            $in = array_search($nach, $strecke, true);
            if (!isset($koord[$von], $koord[$nach])) { return null; }
            if ($iv === false || $in === false || $iv === $in) {
                // nicht beide auf der Marschbahn: gerade Linie
                return [$koord[$von][0] + ($koord[$nach][0] - $koord[$von][0]) * $anteil,
                        $koord[$von][1] + ($koord[$nach][1] - $koord[$von][1]) * $anteil];
            }
            $schritt = $in > $iv ? 1 : -1;
            $punkte = [];
            for ($i = $iv; $i !== $in + $schritt; $i += $schritt) { $punkte[] = $koord[$strecke[$i]]; }
            $laengen = [];
            for ($i = 1; $i < count($punkte); $i++) { $laengen[] = $abstand($punkte[$i - 1], $punkte[$i]); }
            $ziel = array_sum($laengen) * max(0.0, min(1.0, $anteil));
            foreach ($laengen as $i => $l) {
                if ($ziel <= $l || $i === count($laengen) - 1) {
                    $t = $l > 0 ? min(1.0, $ziel / $l) : 0.0;
                    return [$punkte[$i][0] + ($punkte[$i + 1][0] - $punkte[$i][0]) * $t,
                            $punkte[$i][1] + ($punkte[$i + 1][1] - $punkte[$i][1]) * $t];
                }
                $ziel -= $l;
            }
            return $koord[$nach];
        };
        $minuten = static fn(?string $hhmm): ?int => ($hhmm && preg_match('/^(\d{1,2}):(\d{2})/', $hhmm, $m))
            ? (int)$m[1] * 60 + (int)$m[2] : null;
        $jetztMin = (int)date('G') * 60 + (int)date('i');

        // Ausgefallene Züge heute: aus der Echtzeitlage und aus den gemeldeten Ausfällen
        // (die Bahn nimmt ausgefallene Züge manchmal ganz aus der Liste).
        $ausgefallen = [];
        foreach (($lage['daten']['fahrten'] ?? []) as $f) {
            if (!empty($f['ausgefallen'])) { $ausgefallen[(string)$f['nummer']] = true; }
        }
        foreach ($pdo->query("SELECT bezug FROM stoerungen WHERE tag = CURDATE() AND art = 'ausfall'")
                     ->fetchAll() as $z) {
            $ausgefallen[(string)$z['bezug']] = true;
        }

        // 218er laut heutigem Plan
        $jeLok = [];
        foreach ($umlaeufe[date('Y-m-d')] ?? [] as $u) {
            if (str_starts_with((string)$u['lok'], '218 ')) { $jeLok[(string)$u['lok']][] = $u; }
        }
        foreach ($jeLok as $lok => $fahrten) {
            usort($fahrten, static fn($a, $b) => strcmp((string)$a['von_zeit'], (string)$b['von_zeit']));
            $pos = null; $text = ''; $ausfall = [];
            foreach ($fahrten as $i => $u) {
                $ab = $minuten($u['von_zeit']); $an = $minuten($u['nach_zeit']);
                if ($ab === null || $an === null) { continue; }
                // Ausgefallene Fahrten fährt die Lok nicht — sie zählen für die Position nicht.
                $nr = preg_replace('/\D/', '', (string)$u['zug']);
                if (isset($ausgefallen[$nr])) { $ausfall[] = (string)$u['zug']; continue; }
                if ($an < $ab) { $an += 1440; }
                if ($jetztMin < $ab) {
                    $pos = $koord[$u['von_halt']] ?? null;
                    $text = ($i === 0 ? 'in ' : 'wartet in ') . $u['von_halt'] . ', ab ' . $u['von_zeit'] . ' als ' . $u['zug'];
                    break;
                }
                if ($jetztMin <= $an) {
                    $anteil = ($jetztMin - $ab) / max(1, $an - $ab);
                    $pos = $streckenPunkt((string)$u['von_halt'], (string)$u['nach_halt'], $anteil);
                    $text = 'unterwegs ' . $u['von_halt'] . ' → ' . $u['nach_halt'] . ' (' . $u['zug'] . ', an ' . $u['nach_zeit'] . ')';
                    break;
                }
            }
            if ($pos === null && $fahrten) {
                $letzte = end($fahrten);
                $pos = $koord[$letzte['nach_halt']] ?? null;
                $text = 'in ' . $letzte['nach_halt'] . ', fertig seit ' . $letzte['nach_zeit'];
            }
            if ($pos) {
                if ($ausfall) { $text .= ' · fällt aus: ' . implode(', ', $ausfall); }
                $karte['loks'][] = ['lok' => $lok, 'name' => $lokNamen[$lok] ?? null, 'p' => $pos,
                                    'text' => $text, 'quelle' => (string)$fahrten[0]['quelle'],
                                    'unterwegs' => str_starts_with($text, 'unterwegs')];
            }
        }

        // Abgestellte Loks (neuester Stand der Fahrzeugliste)
        // "Niebüll" und "Niebüll (BW/Süd/Terminal)" liegen am selben Punkt — ein Kreis dafür.
        $proPunkt = [];
        foreach ($standorte as $ort => $gruppe) {
            if ($ort === 'im Einsatz' || !isset($koord[$ort])) { continue; }
            $schluessel = sprintf('%.3f|%.3f', $koord[$ort][0], $koord[$ort][1]);
            $proPunkt[$schluessel]['p'] = $koord[$ort];
            $proPunkt[$schluessel]['orte'][] = $ort;
            foreach ($gruppe as $x) {
                $proPunkt[$schluessel]['loks'][] = $x['lok'] . (isset($lokNamen[$x['lok']]) ? ' „' . $lokNamen[$x['lok']] . '“' : '')
                    . (count($proPunkt[$schluessel]['orte']) > 1 || $ort !== preg_replace('/\s*\(.*\)$/', '', $ort)
                       ? ' (' . $ort . ')' : '');
            }
        }
        foreach ($proPunkt as $pp) {
            $karte['standorte'][] = ['ort' => preg_replace('/\s*\(.*\)$/', '', $pp['orte'][0]),
                                     'p' => $naechsterGleispunkt($pp['p'], 3.0) ?? $pp['p'], 'loks' => $pp['loks']];
        }

        // Beiträge aus dem Umkreis, letzte sieben Tage
        foreach ($pdo->query("SELECT s.titel, s.url, s.ort, s.entfernung, s.gesehen_am, o.lat, o.lon
                                FROM sichtungen s JOIN orte o ON o.name = s.ort
                               WHERE s.treffer = 1 AND s.gesehen_am >= NOW() - INTERVAL 7 DAY
                                 AND o.lat IS NOT NULL ORDER BY s.gesehen_am DESC LIMIT 40")->fetchAll() as $x) {
            $karte['umkreis'][] = ['titel' => (string)$x['titel'], 'url' => (string)$x['url'],
                                   'ort' => (string)$x['ort'], 'p' => [(float)$x['lat'], (float)$x['lon']],
                                   'wann' => date('d.m. H:i', strtotime((string)$x['gesehen_am']))];
        }

        // Linien durch Elmshorn (ohne AKN), gesammelt von zuege.py. Hin- und Rückrichtung
        // liegen auf demselben Gleis — je Halte-Paar nur einmal zeichnen. RE6 hat schon
        // die Marschbahn-Linie.
        $karte['linien'] = [];
        $gezeichnet = [];
        foreach ($pdo->query("SELECT linie, von, nach, punkte FROM linien_abschnitte
                               WHERE linie <> 'RE6' ORDER BY linie, von, nach")->fetchAll() as $a) {
            $paar = [(string)$a['von'], (string)$a['nach']];
            sort($paar);
            $schl = $a['linie'] . '|' . implode('|', $paar);
            if (isset($gezeichnet[$schl])) { continue; }
            $gezeichnet[$schl] = true;
            $karte['linien'][(string)$a['linie']][] = json_decode((string)$a['punkte'], true);
        }

        // Welche Zugnummern heute eine 218 fährt — damit die Karte diese Züge hervorhebt.
        $karte['nr218'] = [];
        foreach ($jeLok as $lok => $fahrten) {
            foreach ($fahrten as $u) {
                $nr = preg_replace('/\D/', '', (string)$u['zug']);
                if ($nr !== '' && !isset($ausgefallen[$nr])) {
                    $karte['nr218'][$nr] = ['lok' => $lok, 'name' => $lokNamen[$lok] ?? null,
                                            'quelle' => (string)$u['quelle']];
                }
            }
        }

        // Die fahrenden Züge gleich mitgeben, damit die Karte nicht leer startet — nur
        // Schleswig-Holstein/Hamburg, alles Weitere holt die Karte selbst nach (zuege.php).
        $zz = $pdo->query('SELECT stand, daten FROM karte_zuege WHERE id = 1')->fetch();
        $alleZuege = $zz ? (json_decode((string)$zz['daten'], true) ?: []) : [];
        $karte['zuege'] = array_values(array_filter($alleZuege,
            static fn(array $a): bool => ($a['r'] ?? 'SH') === 'SH'));
        $karte['zuege_stand'] = $zz ? (int)$zz['stand'] : 0;
        // Gebiete: was es gibt (wie REGIONEN in zuege.py) und was der Dienst gerade holt
        // Wie LAENDER in zuege.py — Schleswig-Holstein und Hamburg bleiben immer an.
        $karte['gebiete'] = [
            ['k' => 'SH', 'name' => 'Schleswig-Holstein', 'kurz' => 'Schleswig-Holstein'],
            ['k' => 'HH', 'name' => 'Hamburg', 'kurz' => 'Hamburg'],
            ['k' => 'NI', 'name' => 'Niedersachsen', 'kurz' => 'Niedersachsen'],
            ['k' => 'HB', 'name' => 'Bremen', 'kurz' => 'Bremen'],
            ['k' => 'MV', 'name' => 'Mecklenburg-Vorpommern', 'kurz' => 'Meck-Pomm'],
            ['k' => 'BE', 'name' => 'Berlin', 'kurz' => 'Berlin'],
            ['k' => 'BB', 'name' => 'Brandenburg', 'kurz' => 'Brandenburg'],
            ['k' => 'ST', 'name' => 'Sachsen-Anhalt', 'kurz' => 'Sachsen-Anhalt'],
            ['k' => 'SN', 'name' => 'Sachsen', 'kurz' => 'Sachsen'],
            ['k' => 'TH', 'name' => 'Thüringen', 'kurz' => 'Thüringen'],
            ['k' => 'HE', 'name' => 'Hessen', 'kurz' => 'Hessen'],
            ['k' => 'NW', 'name' => 'Nordrhein-Westfalen', 'kurz' => 'NRW'],
            ['k' => 'RP', 'name' => 'Rheinland-Pfalz', 'kurz' => 'Rheinland-Pfalz'],
            ['k' => 'SL', 'name' => 'Saarland', 'kurz' => 'Saarland'],
            ['k' => 'BW', 'name' => 'Baden-Württemberg', 'kurz' => 'Baden-Württ.'],
            ['k' => 'BY', 'name' => 'Bayern', 'kurz' => 'Bayern'],
            ['k' => 'DK', 'name' => 'Dänemark (Grenzgebiet)', 'kurz' => 'Dänemark'],
        ];
        $karte['gebiete_status'] = json_decode((string)($pdo->query(
            'SELECT wert FROM meta WHERE schluessel = "karte_gebiete_status"')->fetchColumn() ?: '{}'), true) ?: [];
        $karte['gebiete_an'] = array_values(array_filter(explode(',',
            (string)($pdo->query('SELECT wert FROM meta WHERE schluessel = "einst_karte_regionen"')
                          ->fetchColumn() ?: 'SH'))));
    }

    // Archiv beobachteter Fahrten
    $archiv = [];
    if ($seite === 'archiv') {
        $archiv = $pdo->query("SELECT i.*, (SELECT u.elmshorn_zeit FROM umlaeufe u
                                             WHERE u.tag = i.tag AND u.lok = i.lok AND u.zug = i.zug AND u.elmshorn = 1
                                             ORDER BY u.von_zeit LIMIT 1) AS elmshorn_zeit
                                 FROM interesse i WHERE i.aktiv = 0
                                ORDER BY i.tag DESC, i.von_zeit DESC LIMIT 200")->fetchAll();
    }

    // Quellen: wann zuletzt abgefragt, was läuft gerade
    if ($seite === 'quellen') {
        foreach ($pdo->query("SELECT schluessel, wert FROM meta
                               WHERE schluessel LIKE 'lauf\_%' OR schluessel = 'letzter_erfolg'")
                     ->fetchAll() as $z) {
            $laeufeMeta[(string)$z['schluessel']] = (string)$z['wert'];
        }
        $quellenStand['lage'] = $pdo->query('SELECT stand, quelle FROM lage WHERE id = 1')->fetch() ?: null;
        $quellenStand['forum'] = $pdo->query('SELECT MAX(zuletzt_geprueft) FROM tage')->fetchColumn();
        $quellenStand['sichtung'] = $laeufeMeta['lauf_sichtungen'] ?? null;
        $quellenStand['sonderzug'] = $pdo->query('SELECT MAX(gesehen_am) FROM sonderzuege')->fetchColumn();
        $quellenStand['wagenreihung'] = $pdo->query('SELECT MAX(geholt_am) FROM wagenreihung')->fetchColumn();
        $quellenStand['besondere'] = $pdo->query('SELECT MAX(stand) FROM besondere_loks')->fetchColumn();
        $quellenStand['push'] = $pdo->query('SELECT MAX(zuletzt_ok) FROM push_geraete')->fetchColumn();
        $auftraege = $pdo->query('SELECT * FROM auftraege ORDER BY id DESC LIMIT 10')->fetchAll();
    }

    // Zahlen für die Info-Seite
    if ($seite === 'info') {
        foreach ([
            'tage' => 'SELECT COUNT(*) FROM tage',
            'treffer_tage' => 'SELECT COUNT(DISTINCT tag) FROM laeufe',
            'durchfahrten' => 'SELECT COUNT(*) FROM laeufe',
            'umlaeufe' => 'SELECT COUNT(*) FROM umlaeufe',
            'loks' => 'SELECT COUNT(DISTINCT lok) FROM umlaeufe',
            'sichtungen' => 'SELECT COUNT(*) FROM sichtungen WHERE treffer = 1',
            'meldungen' => 'SELECT COUNT(*) FROM meldungen_log',
            'geraete' => 'SELECT COUNT(*) FROM push_geraete WHERE aktiv = 1',
            'seit' => 'SELECT MIN(tag) FROM tage',
        ] as $name => $sql) {
            try { $zahlen[$name] = $pdo->query($sql)->fetchColumn(); } catch (Throwable $e) { $zahlen[$name] = null; }
        }
    }

    $sichtungen24 = (int)$pdo->query(
        'SELECT COUNT(*) FROM sichtungen WHERE treffer = 1 AND gesehen_am >= NOW() - INTERVAL 1 DAY')->fetchColumn();
} catch (Throwable $e) {
    $dbFehler = $e->getMessage();
}

/** "218 443-0" wird zu "218 443-0 Donna", wenn ein Eigenname bekannt ist. */
/** Anklickbarer Lokname — öffnet den Steckbrief im Fenster (lok.php + _layout.php). */
function lokLink(string $lok, array $namen, string $klasse = ''): string {
    $text = h(mitName($lok, $namen));
    return preg_match('/^\d{3} \d{3}-\d$/', $lok)
        ? '<a class="lok-link ' . h($klasse) . '" href="#" data-lok="' . h($lok)
          . '" title="Steckbrief der Lok">' . $text . '</a>'
        : $text;
}

function mitName(string $lok, array $namen): string {
    return isset($namen[$lok]) ? $lok . ' „' . $namen[$lok] . '“' : $lok;
}

/**
 * Wo eine Lok gerade ist und was sie heute macht — aus ihren heutigen Fahrten und
 * der Uhrzeit abgeleitet, also "laut Plan", nicht per Ortung.
 *
 * @return array{jetzt:string, heute:string, aktiv:bool}
 */
function lageHeute(string $lok, array $fahrtenHeute, ?string $standortHeute, array $b, array $wt): array {
    $minuten = static function (?string $hhmm): ?int {
        if (!$hhmm || !preg_match('/^(\d{1,2}):(\d{2})$/', $hhmm, $m)) { return null; }
        return (int)$m[1] * 60 + (int)$m[2];
    };
    $f = array_values(array_filter($fahrtenHeute, static fn(array $u): bool => $u['lok'] === $lok));
    usort($f, static fn(array $x, array $y): int => strcmp((string)$x['von_zeit'], (string)$y['von_zeit']));

    if ($f) {
        $jetzt = (int)date('G') * 60 + (int)date('i');
        $halte = [];
        foreach ($f as $u) { $halte[$u['von_halt']] = true; $halte[$u['nach_halt']] = true; }
        $heute = count($f) . ' Fahrten: ' . implode(' · ', array_keys($halte))
               . ' (' . $f[0]['von_zeit'] . '–' . $f[count($f) - 1]['nach_zeit'] . ')';

        $lage = null;
        foreach ($f as $i => $u) {
            $ab = $minuten($u['von_zeit']);
            $an = $minuten($u['nach_zeit']);
            if ($ab === null || $an === null) { continue; }
            if ($an < $ab) { $an += 1440; }                       // über Mitternacht
            if ($jetzt < $ab) {
                $lage = $i === 0
                    ? 'in ' . $u['von_halt'] . ', erste Fahrt ' . $u['von_zeit']
                    : 'wartet in ' . $u['von_halt'] . ' bis ' . $u['von_zeit'];
                break;
            }
            if ($jetzt <= $an) {
                $lage = 'unterwegs ' . $u['von_halt'] . ' → ' . $u['nach_halt']
                      . ' (' . $u['zug'] . ', an ' . $u['nach_zeit'] . ')';
                break;
            }
        }
        if ($lage === null) {
            $letzte = $f[count($f) - 1];
            $lage = 'in ' . $letzte['nach_halt'] . ', fertig seit ' . $letzte['nach_zeit'];
        }
        return ['jetzt' => $lage, 'heute' => $heute, 'aktiv' => true];
    }
    if ($standortHeute) {
        return ['jetzt' => 'steht in ' . $standortHeute, 'heute' => 'keine Fahrten', 'aktiv' => false];
    }
    if (!empty($b['zuletzt_stand']) || !empty($b['zuletzt_gefahren'])) {
        $wann = max((string)$b['zuletzt_stand'], (string)$b['zuletzt_gefahren']);
        return ['jetzt' => 'heute nicht gelistet',
                'heute' => 'zuletzt ' . datumKurz($wann, $wt)
                         . ($b['zuletzt_ort'] && $wann === (string)$b['zuletzt_stand'] ? ' in ' . $b['zuletzt_ort'] : ''),
                'aktiv' => false];
    }
    return ['jetzt' => '—', 'heute' => 'nicht auf der Marschbahn gesehen', 'aktiv' => false];
}

/** Was man an der Lok sieht: Werbung, Design oder Sonderlackierung — sonst leer. */
function besonders(string $lok, array $besondere): string {
    $b = $besondere[$lok] ?? null;
    if (!$b) { return ''; }
    $bem = (string)($b['bemerkung'] ?? '');
    if ($b['name']) { $bem = str_replace('Aufschrift „' . $b['name'] . '“', '', $bem); }
    $teile = [];
    if (str_contains($bem, 'PIKO/Märklin')) {
        $teile[] = 'Werbelok PIKO/Märklin';
    } elseif (preg_match('/IC-Design|HERING-Design/', $bem, $m)) {
        $teile[] = $m[0];
    } elseif (preg_match('/Aufschrift „([^“]+)“/u', $bem, $m)) {
        $teile[] = 'Aufschrift „' . $m[1] . '“';
    }
    $lack = (string)($b['lackierung'] ?? '');
    if ($lack !== '' && mb_strtolower($lack) !== 'verkehrsrot' && !in_array('Werbelok PIKO/Märklin', $teile, true)) {
        $teile[] = $lack;
    }
    return implode(', ', $teile);
}

$WOCHENTAGE = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
function wochentag(string $iso, array $wt): string {
    return $wt[(int)date('N', strtotime($iso)) - 1];
}
/**
 * Wo steht eine beobachtete Fahrt gerade? Für die Karte oben auf der Startseite.
 * Liefert ['phase' => vor|elmshorn|unterwegs|angekommen|ausfall, 'text' => HTML,
 * 'archiv' => bool]. archiv: eine Stunde nach der (verspäteten) Ankunft — so wie
 * beobachten.py sie ins Archiv legt (das läuft nur alle fünf Minuten).
 */
function beobachtungsLage(array $i, ?int $jetzt = null): array {
    $jetzt ??= time();
    $basis = strtotime((string)$i['tag'] . ' 00:00');
    $zp = static function (?string $hhmm, ?int $nicht_vor = null) use ($basis): ?int {
        if (!$hhmm || !preg_match('/^(\d{1,2}):(\d{2})/', $hhmm, $m)) { return null; }
        $t = $basis + (int)$m[1] * 3600 + (int)$m[2] * 60;
        return ($nicht_vor !== null && $t < $nicht_vor) ? $t + 86400 : $t;     // über Mitternacht
    };
    $ab = $zp($i['von_zeit'] ?? null);
    $elm = $zp($i['elmshorn_zeit'] ?? null, $ab);
    $an = $zp($i['nach_zeit'] ?? null, $ab);
    // "stand" ist auf 255 Zeichen gekürzt — Verspätung und Ausfall zur Not per Muster
    $stand = (string)($i['stand'] ?? '');
    $spaet = preg_match('/"verspaetung":\s*(-?\d+)/', $stand, $m) ? (int)$m[1] : 0;
    $ausfall = (bool)preg_match('/"ausfall":\s*true/', $stand);
    $uhr = static fn(int $t): string => date('H:i', $t);
    $mitSpaet = static fn(int $t) => h($uhr($t + $spaet * 60)) . ($spaet > 0 ? ' <span class="rot">+' . $spaet . '</span>' : '');
    $nach = h((string)($i['nach_halt'] ?? ''));
    $ende = ($an ?? ($ab !== null ? $ab + 4 * 3600 : $basis + 86400)) + ($ausfall ? 0 : $spaet * 60);
    $archiv = $jetzt > $ende + 3600;

    if ($ausfall) {
        return ['phase' => 'ausfall', 'archiv' => $archiv, 'text' => '<b class="rot">fällt aus</b>'
            . ($ab !== null ? ' · geplant ab ' . h($uhr($ab)) . ' ' . h((string)$i['von_halt']) : '')];
    }
    if ($ab !== null && $jetzt < $ab + $spaet * 60) {
        $tagText = date('Y-m-d', $ab) === date('Y-m-d', $jetzt) ? '' : (date('Y-m-d', $ab) === date('Y-m-d', $jetzt + 86400) ? 'morgen ' : date('d.m. ', $ab));
        return ['phase' => 'vor', 'archiv' => false, 'text' => 'Abfahrt ' . $tagText . $mitSpaet($ab) . ' in ' . h((string)$i['von_halt'])
            . ($elm !== null ? ' · durch Elmshorn ' . h($uhr($elm)) : '') . ' · nach ' . $nach];
    }
    if ($elm !== null && $jetzt < $elm + $spaet * 60) {
        return ['phase' => 'unterwegs', 'archiv' => false, 'text' => 'unterwegs · <b>durch Elmshorn um ' . $mitSpaet($elm)
            . '</b> · auf dem Weg nach ' . $nach];
    }
    if ($an === null || $jetzt < $an + $spaet * 60) {
        return ['phase' => $elm !== null ? 'elmshorn' : 'unterwegs', 'archiv' => false,
                'text' => ($elm !== null ? '<b>Elmshorn durch</b> · ' : 'unterwegs · ') . 'auf dem Weg nach ' . $nach
                    . ($an !== null ? ' · an ' . $mitSpaet($an) : '')];
    }
    return ['phase' => 'angekommen', 'archiv' => $archiv,
            'text' => 'angekommen in ' . $nach . ' um ' . $mitSpaet($an)];
}

function datumKurz(string $iso, array $wt): string {
    return wochentag($iso, $wt) . '., ' . date('d.m.', strtotime($iso));
}

$anzTreffer = 0;
foreach ($tage as $t) { $anzTreffer += $t['treffer'] ? 1 : 0; }

/**
 * Plan für Elmshorn: je Tag ab heute alle 218-Durchfahrten (auch gestrichene) und
 * Sonderzüge, nach Uhrzeit. Tage mit Tagesliste ohne Treffer stehen mit leerer Liste drin.
 *
 * @return array<string, list<array>>
 */
function planElmshorn(array $tage, array $laeufe, array $sonder): array {
    $heute = date('Y-m-d');
    // Heute und morgen stehen immer drin — fehlt die Liste noch, sagt die Karte genau das.
    $plan = [$heute => [], date('Y-m-d', strtotime('+1 day')) => []];
    foreach (array_keys($tage) as $tag) {
        if ($tag >= $heute) { $plan[$tag] = []; }
    }
    foreach ($laeufe as $tag => $liste) {
        if ($tag < $heute) { continue; }
        // Dieselbe Fahrt (Lok + Zug) nur einmal — gibt es sie aktiv, gilt die aktive.
        // Absicherung: bis 18.09.2026 konnte eine bearbeitete Forumszeile eine Fahrt
        // doppelt anlegen, einmal gestrichen und einmal aktiv.
        $eindeutig = [];
        foreach ($liste as $l) {
            if (!(int)$l['aktiv'] && empty($l['gestrichen_am'])) { continue; }
            $k = $l['lok'] . '|' . $l['zug'];
            if (!isset($eindeutig[$k]) || ((int)$l['aktiv'] && !(int)$eindeutig[$k]['aktiv'])) {
                $eindeutig[$k] = $l;
            }
        }
        foreach ($eindeutig as $l) {
            $plan[$tag][] = ['art' => '218', 'zeit' => $l['elmshorn_zeit']] + $l;
        }
    }
    foreach ($sonder as $z) {
        $plan[$z['tag']][] = ['art' => 'sonder', 'zeit' => $z['zeit']] + $z;
    }
    ksort($plan);
    foreach ($plan as &$fahrten) {
        usort($fahrten, static fn(array $a, array $b): int =>
            strcmp((string)($a['zeit'] ?? '99'), (string)($b['zeit'] ?? '99')));
    }
    return $plan;
}

/**
 * Link zur Wagenreihung (Lok vorne oder hinten, Wagen, Gleisabschnitte) für eine Abfahrt
 * in Elmshorn. Die Daten zeigt dbf.finalrewind.org an — bahn.de sperrt Abrufe von diesem
 * Server (OPS_BLOCKED), deshalb öffnet der Knopf die Seite im eigenen Browser, statt dass
 * Zugradar sie abholt. Nur für DB-Züge; AKN und Sonstige haben keine Wagenreihung.
 */
const WR_BASIS = 'https://dbf.finalrewind.org/carriage-formation';
const EVA_ELMSHORN = 8000092;
function wagenreihungLink(string $kategorie, string $nummer, string $tag, ?string $hhmm,
                          ?int $eva = null): ?string {
    if (!$hhmm || !preg_match('/^\d{1,2}:\d{2}$/', $hhmm) || !ctype_digit($nummer)
        || !preg_match('/^(RE|RB|IRE|IC|ICE|EC|ECE|D|NJ)$/', $kategorie)) {
        return null;
    }
    $zeit = strtotime($tag . ' ' . $hhmm);
    return $zeit ? WR_BASIS . '?' . http_build_query(
        ['tt' => $kategorie, 'tn' => $nummer, 'eva' => $eva ?: EVA_ELMSHORN, 'dt' => $zeit]) : null;
}

/**
 * Knopf, der die Wagenreihung im Fenster zeigt (Daten holt wagenreihung.php).
 * Ohne JavaScript bleibt der Link zur Quelle — dann öffnet sich dbf im Browser.
 */
function wagenreihungKnopf(string $kategorie, string $nummer, string $tag, ?string $hhmm,
                           string $titel = '', ?int $eva = null): string {
    $link = wagenreihungLink($kategorie, $nummer, $tag, $hhmm, $eva);
    if (!$link) { return ''; }
    $daten = json_encode([
        'tt' => $kategorie, 'tn' => $nummer, 'dt' => strtotime($tag . ' ' . $hhmm),
        'titel' => $titel !== '' ? $titel : trim($kategorie . ' ' . $nummer),
        'neben' => trim(($hhmm ?? '') . ' ab Elmshorn'), 'url' => $link,
    ], JSON_UNESCAPED_UNICODE);
    return '<a class="knopf-klein wr-knopf" href="' . h($link) . '" target="_blank" rel="noopener noreferrer"'
         . " data-wr='" . h($daten) . "'"
         . ' title="Wagenreihung: wo Lok und Wagen halten">🚃 Wagenreihung</a>';
}

/** "in 1 h 20 min", "in 8 min", "jetzt", "morgen" */
function bisDahin(string $tag, ?string $zeit): string {
    if (!$zeit) { return ''; }
    $d = (int)round((strtotime($tag . ' ' . $zeit) - time()) / 60);
    if ($d < 0)     { return ''; }
    if ($d <= 1)    { return 'jetzt'; }
    if ($tag === date('Y-m-d', strtotime('+1 day'))) { return 'morgen'; }
    if ($d >= 1440) { return ''; }
    return 'in ' . ($d >= 60 ? intdiv($d, 60) . ' h ' . ($d % 60 ? $d % 60 . ' min' : '') : $d . ' min');
}

$fahrten  = $lage['daten']['fahrten']   ?? [];
/**
 * Echtzeit je Zugnummer aus der Lage-Momentaufnahme. Damit bleibt eine verspätete
 * Fahrt im Plan stehen, bis sie wirklich durch ist — nach der Sollzeit zu gehen
 * hieße, sie genau dann auszublenden, wenn man noch auf sie wartet.
 */
$echtzeitJeZug = [];
foreach ($fahrten as $f) { $echtzeitJeZug[(string)$f['nummer']] = $f; }
// Ein gemeldeter Ausfall bleibt ein Ausfall, auch wenn die Bahn den Zug danach ganz
// aus der Echtzeitliste nimmt (18.09.2026, RE 11029: 16:10 als Ausfall gemeldet,
// 16:20 nicht mehr in der Liste — der Plan zeigte ihn da wieder als normale Fahrt).
try {
    foreach (kukas_db()->query("SELECT bezug FROM stoerungen
                                 WHERE tag = CURDATE() AND art = 'ausfall'")->fetchAll() as $z) {
        $nr = (string)$z['bezug'];
        if (!isset($echtzeitJeZug[$nr])) {
            $echtzeitJeZug[$nr] = ['nummer' => $nr, 'ausgefallen' => true, 'verspaetung' => 0,
                                   'ist' => null, 'gleis' => null];
        } else {
            $echtzeitJeZug[$nr]['ausgefallen'] = true;
        }
    }
} catch (Throwable $e) { /* ohne Tabelle eben ohne */ }

/**
 * Stand einer geplanten Durchfahrt: erwartete Zeit, Verspätung, und ob sie noch
 * kommt, gerade durch ist ("fertig") oder länger vorbei.
 *
 * @return array{zeit:?string, spaet:int, gleis:?string, ausfall:bool, lage:string}
 *         lage: 'kommt' | 'fertig' | 'vorbei'
 */
function fahrtStand(array $f, string $tag, array $echtzeit, int $nachlaufMinuten = 120): array {
    $soll = (string)($f['zeit'] ?? '');
    $rt = $echtzeit[preg_replace('/\D/', '', (string)($f['zug'] ?? $f['nummer'] ?? ''))] ?? null;
    $spaet = $rt ? (int)$rt['verspaetung'] : 0;
    $zeit = $rt && !empty($rt['ist']) ? (string)$rt['ist'] : $soll;
    $ausfall = (bool)($rt['ausgefallen'] ?? false);
    $lage = 'kommt';
    if ($tag < date('Y-m-d')) {
        $lage = 'vorbei';
    } elseif ($tag === date('Y-m-d') && $zeit !== '') {
        $vergangen = (strtotime(date('Y-m-d') . ' ' . date('H:i')) - strtotime($tag . ' ' . $zeit)) / 60;
        if ($vergangen > $nachlaufMinuten) { $lage = 'vorbei'; }
        elseif ($vergangen > 0)            { $lage = 'fertig'; }
    }
    return ['zeit' => $zeit ?: null, 'spaet' => $spaet, 'gleis' => $rt['gleis'] ?? ($f['gleis'] ?? null),
            'ausfall' => $ausfall, 'lage' => $lage];
}
$stoerung = $lage['daten']['meldungen'] ?? [];
$lageAlt  = $lage && (time() - strtotime((string)$lage['stand'])) > 1800;
// Öffentlicher Push-Schlüssel (legt push.py --schluessel an)
$vapidPublic = '';
try {
    $vapidPublic = (string)(kukas_db()->query("SELECT wert FROM meta WHERE schluessel = 'vapid_public'")->fetchColumn() ?: '');
} catch (Throwable $e) {
    // ohne Datenbank eben ohne Push-Knopf
}

seiten_kopf(SEITEN[$seite], $menueAktiv, '', $vapidPublic);
?>
<?php if ($dbFehler): ?>
  <div class="box" style="margin-bottom:14px"><div class="hinweis">Datenbank nicht erreichbar: <?= h($dbFehler) ?></div></div>
<?php endif; ?>

<?php if ($letzterLauf && !(int)$letzterLauf['erfolg']): ?>
  <div class="box" style="margin-bottom:14px"><div class="hinweis">
    Letzter Prüflauf ist fehlgeschlagen: <?= h((string)$letzterLauf['fehler']) ?></div></div>
<?php endif; ?>

<?php if ($seite === 'start'):
    $plan = planElmshorn($tage, $laeufe, $sonderPlan);
    $heute = date('Y-m-d');
    $jetztHM = date('H:i');
    $heuteElmshorn = count(array_filter($plan[$heute] ?? [], static fn(array $f): bool =>
        $f['art'] === '218' && (int)$f['aktiv']));
    // Eine verspätete Fahrt bleibt der Aufmacher, bis sie durch ist; eine ausgefallene
    // ist es nie. Findet sich keine, bleibt der Aufmacher leer — die frühere Abfrage
    // nach Sollzeit zeigte sonst ausgerechnet den ausgefallenen Zug.
    $naechste = null;
    $ausfaelleHeute = 0;
    foreach ($plan[$heute] ?? [] as $f) {
        if ($f['art'] === '218' && (int)$f['aktiv']
            && fahrtStand($f, $heute, $echtzeitJeZug)['ausfall']) { $ausfaelleHeute++; }
    }
    foreach ($plan as $planTag => $liste) {
        foreach ($liste as $f) {
            if ($f['art'] !== '218' || !(int)$f['aktiv']) { continue; }
            $stand = fahrtStand($f, (string)$planTag, $echtzeitJeZug, 0);
            if ($stand['lage'] === 'kommt' && !$stand['ausfall']) {
                $naechste = $f + ['tag' => $planTag, 'stand' => $stand];
                break 2;
            }
        }
    }
    $heuteLoks = count(array_unique(array_column($umlaeufe[$heute] ?? [], 'lok')));
    $verspaetet = count(array_filter($fahrten, static fn(array $f): bool => (int)$f['verspaetung'] >= 5 && !$f['ausgefallen']));
    $ausfaelle = count(array_filter($fahrten, static fn(array $f): bool => (bool)$f['ausgefallen'])); ?>

<?php
    // Beobachtete Fahrten ganz oben — bis eine Stunde nach der Ankunft am Endbahnhof,
    // danach stehen sie im Archiv (./?s=archiv).
    $beobachtetOben = [];
    foreach ($interessen as $i) {
        $lageB = beobachtungsLage($i);
        if (!$lageB['archiv']) { $beobachtetOben[] = $i + ['lage_b' => $lageB]; }
    }
?>
<?php if ($beobachtetOben): ?>
<section id="beobachtet">
  <h2>Beobachtet <a class="h2-link" href="./?s=archiv">Archiv →</a></h2>
  <div class="beob-liste">
  <?php foreach ($beobachtetOben as $i): $lb = $i['lage_b']; ?>
    <div class="box beob beob-<?= h($lb['phase']) ?>">
      <a class="beob-kopf" href="<?= h('./?s=fahrt&tag=' . rawurlencode((string)$i['tag']) . '&lok=' . rawurlencode((string)$i['lok']) . '&zug=' . rawurlencode((string)$i['zug'])) ?>">
        <span class="beob-punkt" aria-hidden="true"></span>
        <span class="stark"><?= h((string)$i['lok']) ?><?= isset($lokNamen[$i['lok']]) ? ' „' . h($lokNamen[$i['lok']]) . '“' : '' ?></span>
        <span class="dim"><?= h((string)$i['zug']) ?></span>
        <?php if ((string)$i['tag'] !== date('Y-m-d')): ?><span class="dim"><?= h(datumKurz((string)$i['tag'], $WOCHENTAGE)) ?></span><?php endif; ?>
      </a>
      <div class="beob-lage"><?= $lb['text'] ?></div>
      <div class="beob-fuss">
        <span class="klein"><?= h((string)($i['von_halt'] ?? '')) ?> <?= h((string)($i['von_zeit'] ?? '')) ?> → <?= h((string)($i['nach_halt'] ?? '')) ?> <?= h((string)($i['nach_zeit'] ?? '')) ?><?php if ($i['letzter_check']): ?> · geprüft <?= h(date('H:i', strtotime((string)$i['letzter_check']))) ?><?php endif; ?></span>
        <?= merkknopf('entfernen', (string)$i['tag'], (string)$i['lok'], (string)$i['zug'], 'nicht mehr beobachten', 'knopf-klein') ?>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
</section>
<?php endif; ?>

<!-- Aufmacher: die nächste Durchfahrt -->
<div class="hero" id="naechste">
  <?php if ($naechste): ?>
    <div class="label">Nächste 218 durch Elmshorn</div>
    <div class="zeit"><?= h((string)($naechste['stand']['zeit'] ?? $naechste['elmshorn_zeit'])) ?>
      <?php if (!empty($naechste['gleis'])): ?>
        <span class="gleis">Gleis <?= h((string)$naechste['gleis']) ?></span>
      <?php endif; ?>
      <small><?= h(datumKurz((string)$naechste['tag'], $WOCHENTAGE)) ?></small>
      <?php if (($naechste['stand']['spaet'] ?? 0) > 0): ?>
        <span class="bis warn">+<?= (int)$naechste['stand']['spaet'] ?> min</span><?php endif; ?>
      <?php if ($bis = bisDahin((string)$naechste['tag'], (string)($naechste['stand']['zeit'] ?? $naechste['elmshorn_zeit']))): ?>
        <span class="bis"><?= h($bis) ?></span><?php endif; ?>
    </div>
    <div class="lok"><?= lokLink((string)$naechste['lok'], $lokNamen) ?> ·
      <?= h((string)$naechste['zug']) ?></div>
    <div class="weg"><?= h((string)$naechste['von_halt']) ?> →
      <?= h((string)$naechste['nach_halt']) ?> · <?= h((string)$naechste['richtung']) ?>
      <?= ($naechste['zeit_quelle'] ?? '') !== 'fahrplan' ? ' · Zeit geschätzt' : '' ?></div>
  <?php else: ?>
    <div class="label">Nächste 218 durch Elmshorn</div>
    <div class="keine"><?= $ausfaelleHeute ? 'Heute keine mehr' : 'Keine geplant' ?></div>
    <div class="weg"><?php if (!empty($ausfaelleHeute)): ?>
        <?= $ausfaelleHeute === 1 ? 'Die letzte 218 heute fällt aus' : 'Die übrigen ' . $ausfaelleHeute . ' Fahrten der 218 heute fallen aus' ?>
        laut Bahn. Die Liste für morgen erscheint meist am Abend.
      <?php else: ?>
        In den vorliegenden Tageslisten fährt keine weitere 218 über Elmshorn. Die Liste für
        den nächsten Tag erscheint meist am Abend.
      <?php endif; ?></div>
  <?php endif; ?>
</div>

<div class="kacheln">
  <a class="kachel<?= $stoerung ? ' warn' : ($lage && !$verspaetet && !$ausfaelle ? ' gut' : '') ?>" href="./?s=lage">
    <div class="k-label">Elmshorn jetzt</div>
    <?php if (!$lage): ?>
      <div class="k-wert">—</div><div class="k-neben">noch keine Lage</div>
    <?php elseif ($stoerung): ?>
      <div class="k-wert">Störung</div><div class="k-neben"><?= count($stoerung) ?> Meldung<?= count($stoerung) === 1 ? '' : 'en' ?> der Bahn</div>
    <?php else: ?>
      <div class="k-wert"><?= $ausfaelle ? $ausfaelle . ($ausfaelle > 1 ? ' Ausfälle' : ' Ausfall') : ($verspaetet ? $verspaetet . ' verspätet' : 'läuft') ?></div>
      <div class="k-neben"><?= count($fahrten) ?> Fahrten · Stand <?= h(date('H:i', strtotime((string)$lage['stand']))) ?><?= $lageAlt ? ' (veraltet)' : '' ?></div>
    <?php endif; ?>
  </a>
  <a class="kachel<?= !empty($ausfaelleHeute) ? ' warn' : ($heuteElmshorn ? ' gut' : '') ?>" href="#plan">
    <div class="k-label">Heute durch Elmshorn</div>
    <div class="k-wert"><?= $heuteElmshorn ?: 'keine' ?></div>
    <div class="k-neben"><?php
      $imPlan = $heuteElmshorn === 1 ? 'Durchfahrt einer 218' : ($heuteElmshorn ? 'Durchfahrten' : (isset($plan[$heute]) ? 'laut Tagesliste' : 'Tagesliste fehlt noch'));
      echo !empty($ausfaelleHeute) ? 'davon ' . $ausfaelleHeute . ($ausfaelleHeute > 1 ? ' Ausfälle' : ' Ausfall') : $imPlan; ?></div>
  </a>
  <a class="kachel" href="./?s=loks">
    <div class="k-label">218er im Einsatz</div>
    <div class="k-wert"><?= $heuteLoks ?: '—' ?></div>
    <div class="k-neben">heute auf der Marschbahn</div>
  </a>
  <a class="kachel" href="./?s=lage#umkreis">
    <div class="k-label">Im Umkreis</div>
    <div class="k-wert"><?= $sichtungen24 ?: 'nichts' ?></div>
    <div class="k-neben">Beiträge, letzte 24 h</div>
  </a>
</div>

<?php
    // Zugnummern, die heute eine 218 fährt — für die Hervorhebung in "In deiner Nähe"
    $nahe218 = [];
    foreach ($umlaeufe[date('Y-m-d')] ?? [] as $u) {
        if (str_starts_with((string)$u['lok'], '218 ') && ($nr = preg_replace('/\D/', '', (string)$u['zug'])) !== '') {
            $nahe218[$nr] = (string)$u['lok'];
        }
    }
?>
<!-- Züge in der Nähe: GPS im Browser, strecke.php?naehe=1 (Standort auf gut 100 m gerundet) -->
<section id="naehe">
  <h2>In deiner Nähe <a class="h2-link" href="./?s=karte&amp;standort=1">Karte →</a></h2>
  <div class="box naehe-box" id="naehe-box">
    <div class="naehe-start" id="naehe-start">
      <span class="naehe-text">Welche Züge fahren gleich an dir vorbei?</span>
      <button type="button" class="knopf-klein haupt naehe-knopf" id="naehe-knopf">📍 Standort bestimmen</button>
    </div>
    <div class="naehe-status" id="naehe-status" hidden></div>
    <ul class="dz-liste naehe-liste" id="naehe-liste" hidden></ul>
  </div>
</section>
<script>
(function () {
  const NR218 = <?= json_encode($nahe218, JSON_HEX_TAG | JSON_HEX_AMP) ?>;
  const knopf = document.getElementById('naehe-knopf');
  const status = document.getElementById('naehe-status');
  const liste = document.getElementById('naehe-liste');
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const meter = (m) => (m < 1000 ? m + ' m' : (m / 1000).toFixed(1).replace('.', ',') + ' km');
  const zeig = (text) => { status.hidden = false; status.textContent = text; };
  const zeile = (f) => {
    const lok = f.nr ? NR218[f.nr] : null;
    return '<li class="' + (lok ? 'z218' : '') + '"><span class="dz-zeit">' + esc(f.zeit)
      + (f.spaet > 0 ? ' <span class="rot">+' + f.spaet + '</span>' : '')
      + '<small>' + (f.in_min <= 0 ? 'jetzt' : 'in ' + f.in_min + ' min') + '</small></span>'
      + '<span class="dz-zug"><b>' + esc(f.name.replace(/\s*\((\d+)\)/, ' $1')) + '</b>'
      + (lok ? ' <b class="rot">' + esc(lok) + '</b>' : '')
      + '<br>' + (f.start && f.ziel ? esc(f.start) + ' → ' + esc(f.ziel) : esc(f.von) + ' → ' + esc(f.nach))
      + '<br><small>' + (f.abstand < 60 ? 'direkt bei dir' : meter(f.abstand) + ' entfernt')
      + ' · zwischen ' + esc(f.von) + ' und ' + esc(f.nach) + '</small></span></li>';
  };
  const suchen = () => {
    if (!navigator.geolocation) { zeig('Dieses Gerät kann den Standort nicht bestimmen.'); return; }
    knopf.disabled = true;
    zeig('Standort wird bestimmt …');
    navigator.geolocation.getCurrentPosition((pos) => {
      zeig('Züge werden gesucht …');
      fetch('strecke.php?naehe=1&lat=' + pos.coords.latitude.toFixed(3) + '&lon=' + pos.coords.longitude.toFixed(3),
            { credentials: 'same-origin', cache: 'no-store' })
        .then((r) => r.json())
        .then((j) => {
          knopf.disabled = false;
          knopf.textContent = '↻ Aktualisieren';
          if (j.status !== 'fertig') { zeig(j.fehler || 'Gerade nicht verfügbar.'); return; }
          if (!j.fahrten.length) {
            liste.hidden = true;
            zeig('In den nächsten ' + j.minuten + ' Minuten kommt im Umkreis von 3 km kein Zug vorbei.');
            return;
          }
          zeig('Gleise bis 3 km um dich · nächste ' + j.minuten + ' Minuten · Stand ' + j.stand);
          liste.innerHTML = j.fahrten.slice(0, 8).map(zeile).join('');
          liste.hidden = false;
        })
        .catch(() => { knopf.disabled = false; zeig('Keine Verbindung.'); });
    }, (fehler) => {
      knopf.disabled = false;
      zeig(fehler.code === 1 ? 'Der Standort ist für Zugradar nicht freigegeben — in den Einstellungen des Browsers erlauben.'
        : (fehler.code === 3 ? 'Das GPS hat zu lange gebraucht. Bitte nochmal versuchen.' : 'Der Standort ließ sich gerade nicht bestimmen.'));
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 120000 });
  };
  knopf.addEventListener('click', suchen);
  // Nie von selbst — nur per Knopf. Einzige Ausnahme: der Schnellstart-Eintrag
  // "In deiner Nähe" (langes Drücken aufs App-Symbol, ?naehe=1) ist selbst der Klick darauf.
  if (new URLSearchParams(location.search).get('naehe') === '1') { suchen(); }
})();
</script>

<section id="plan">
  <h2>Plan für Elmshorn</h2>
  <?php if (!$plan): ?>
    <div class="box"><div class="leer">Für heute und die nächsten Tage liegt noch keine Tagesliste vor.</div></div>
  <?php endif; ?>
  <?php foreach ($plan as $tag => $liste):
      $morgen = $tag === date('Y-m-d', strtotime('+1 day'));
      $zahl = count(array_filter($liste, static fn(array $f): bool => $f['art'] === '218' && (int)$f['aktiv'])); ?>
    <div class="box plantag">
      <div class="boxkopf">
        <span class="titel"><?= $tag === $heute ? 'Heute' : ($morgen ? 'Morgen' : h(wochentag($tag, $WOCHENTAGE)) . '.') ?>,
          <?= h(date('d.m.', strtotime($tag))) ?></span>
        <span class="neben"><?php
          $fehlend = array_diff(['regio', 'shuttle'], array_column($tage[$tag]['quellen'] ?? [], 'quelle'));
          echo match (true) {
              (bool)$zahl => $zahl . '× 218',
              count($fehlend) === 2 => 'Liste fehlt noch',
              in_array('regio', $fehlend, true) => 'Regio-Liste fehlt',
              in_array('shuttle', $fehlend, true) => 'SyltShuttle-Liste fehlt',
              default => 'keine 218',
          }; ?></span>
      </div>
      <?php
      // Woran liegt es, wenn nichts dasteht: fehlt die Liste noch oder ist einfach
      // keine 218 dabei? Beides sieht sonst gleich aus.
      $quellenTag = $tage[$tag]['quellen'] ?? [];
      $vorhanden = array_column($quellenTag, 'quelle');
      $fahrtenJeQuelle = [];
      foreach ($umlaeufe[$tag] ?? [] as $u) {
          $fahrtenJeQuelle[(string)$u['quelle']] = ($fahrtenJeQuelle[(string)$u['quelle']] ?? 0) + 1;
      } ?>
      <?php if (!$liste): ?>
        <div class="leer">Keine 218 und kein Sonderzug durch Elmshorn.</div>
      <?php endif; ?>

      <?php foreach ($liste as $f):
          $stand = fahrtStand($f, (string)$tag, $echtzeitJeZug);
          $vorbei = $stand['lage'] === 'vorbei';
          $fertig = $stand['lage'] === 'fertig';
          if ($f['art'] === 'sonder'): ?>
        <div class="fahrt sonder<?= $stand['ausfall'] ? ' gestrichen' : '' ?><?= $vorbei ? ' vorbei' : '' ?><?= $fertig ? ' fertig' : '' ?>">
          <div class="f-zeit"><?= h((string)($stand['zeit'] ?: $f['zeit'] ?: '–')) ?>
            <?php if ($stand['gleis']): ?><span class="f-gleis">Gleis <?= h((string)$stand['gleis']) ?></span><?php endif; ?></div>
          <div>
            <div class="f-lok"><?= h(trim($f['kategorie'] . ' ' . $f['nummer'])) ?><span class="etikett">Sonderzug</span></div>
            <div class="f-weg"><?= h((string)$f['von']) ?> → <?= h((string)$f['nach']) ?></div>
          </div>
          <div class="f-knopf"><?= $tag === $heute && $stand['lage'] === 'kommt'
              ? wagenreihungKnopf((string)$f['kategorie'], (string)$f['nummer'], $tag, $f['zeit'],
                                  trim($f['kategorie'] . ' ' . $f['nummer'])) : '' ?></div>
        </div>
      <?php else:
          $weg = !(int)$f['aktiv'];
          $schl = $tag . '|' . $f['lok'] . '|' . $f['zug'];
          $bes = besonders((string)$f['lok'], $besondere); ?>
        <div class="fahrt<?= $weg || $stand['ausfall'] ? ' gestrichen' : '' ?><?= $vorbei ? ' vorbei' : '' ?><?= $fertig ? ' fertig' : '' ?>">
          <div class="f-zeit"><?= h((string)($stand['zeit'] ?: '–')) ?>
            <?php if ($stand['spaet'] > 0 && !$weg): ?><span class="f-spaet">+<?= $stand['spaet'] ?> min</span><?php endif; ?>
            <?php if ($stand['gleis']): ?><span class="f-gleis">Gleis <?= h((string)$stand['gleis']) ?></span><?php endif; ?></div>
          <div>
            <div class="f-lok"><?= lokLink((string)$f['lok'], $lokNamen) ?>
              <?= quelleEtikett($f['quelle'] ?? null) ?>
              <?php if ($weg): ?><span class="etikett warn">gestrichen</span><?php
                elseif ($stand['ausfall']): ?><span class="etikett warn">Ausfall</span><?php
                elseif ($fertig): ?><span class="etikett gut">durch, <?= h((string)$stand['zeit']) ?></span><?php
                elseif ($vorbei): ?><span class="etikett">vorbei</span><?php
                elseif ($stand['spaet'] >= 5): ?><span class="etikett warn">+<?= $stand['spaet'] ?> min</span><?php
                elseif (isset($interessen[$schl])): ?><span class="etikett gut">beobachtet</span><?php endif; ?></div>
            <div class="f-weg"><a class="titel-link" href="<?= h(fahrtUrl($tag, (string)$f['lok'], (string)$f['zug'])) ?>"><?=
              h((string)$f['zug']) ?></a> · <?= h((string)$f['von_halt']) ?> → <?= h((string)$f['nach_halt']) ?></div>
            <div class="f-zusatz"><?= h((string)$f['richtung']) ?><?= ($f['zeit_quelle'] ?? '') !== 'fahrplan' && $f['zeit'] ? ' · Zeit geschätzt' : '' ?><?= $bes ? ' · ' . h($bes) : '' ?></div>
          </div>
          <div class="f-knopf"><?php
            if (!$weg && $stand['lage'] === 'kommt' && !$stand['ausfall'] && $tag === $heute):
                // "RE 11026" → Kategorie und Nummer
                [$kat, $nr] = array_pad(explode(' ', (string)$f['zug'], 2), 2, '');
                echo wagenreihungKnopf($kat, $nr, $tag, $f['zeit'],
                                       mitName((string)$f['lok'], $lokNamen) . ' · ' . $f['zug']);
            endif;
            if (!$weg && $stand['lage'] !== 'vorbei'):
                echo isset($interessen[$schl])
                    ? merkknopf('entfernen', $tag, (string)$f['lok'], (string)$f['zug'], '★', 'knopf-klein aktiv')
                    : merkknopf('merken', $tag, (string)$f['lok'], (string)$f['zug'], '☆ merken', 'knopf-klein');
            endif; ?></div>
        </div>
      <?php endif; endforeach; ?>

      <div class="quellenstand">
        <?php foreach (['regio' => 'DB Regio', 'shuttle' => 'SyltShuttle'] as $q => $name):
            $i = array_search($q, $vorhanden, true);
            $zeile = $i === false ? null : $quellenTag[$i]; ?>
          <span class="qs-teil"><span class="qs-punkt <?= $zeile ? 'da' : 'fehlt' ?>"></span>
            <?php if ($zeile): ?>
              <a href="<?= h((string)$zeile['url']) ?>" target="_blank" rel="noopener"><?= h($name) ?></a>
              <?= !empty($zeile['zuletzt_geprueft']) ? h(date('H:i', strtotime((string)$zeile['zuletzt_geprueft']))) : '' ?>
            <?php else: ?>
              <span class="dim"><?= h($name) ?> fehlt</span>
            <?php endif; ?></span>
        <?php endforeach; ?>
      </div>
    </div>
  <?php endforeach; ?>
  <a class="mehr-link" href="./?s=tage">Alle Tageslisten mit allen Fahrten →</a>
</section>

<?php elseif ($seite === 'lage'): ?>

<!-- Live: Abfahrten in Elmshorn oder einem anderen Bahnhof -->
<?php
// Elmshorn schreibt die laufende Überwachung fort, andere Bahnhöfe holt
// bahnhof.py auf Anfrage (siehe oben) — hier nur anzeigen.
$istElmshorn = $bahnhof === 'Elmshorn';
if (!$istElmshorn) {
    $fahrten  = $bahnhofDaten['fahrten'] ?? [];
    $stoerung = $bahnhofDaten['meldungen'] ?? [];
}
$tafelStand  = $istElmshorn ? ($lage['stand'] ?? null) : $bahnhofStand;
$tafelQuelle = $istElmshorn ? ($lage['quelle'] ?? 'DB Timetables') : 'DB Timetables';
$tafelEva    = $istElmshorn ? EVA_ELMSHORN : $bahnhofEva;
?>
<section id="bahnhof">
  <h2>Live in <?= h($bahnhof) ?></h2>
  <?php $schnellwahl = ['Elmshorn' => 'Elmshorn', 'Hamburg-Altona' => 'Hamburg', 'Itzehoe' => 'Itzehoe'];
        $gesuchterBahnhof = !isset($schnellwahl[$bahnhof]); ?>
  <div class="bahnhofzeile">
    <?php foreach ($schnellwahl as $name => $kurz): ?>
      <a class="f-chip<?= $name === $bahnhof ? ' aktiv' : '' ?>" title="<?= h($name) ?>"
         href="./?s=lage<?= $name === 'Elmshorn' ? '' : '&amp;bf=' . urlencode($name) ?>"><?= h($kurz) ?></a>
    <?php endforeach; ?>
    <form method="get" class="bahnhofwahl<?= $gesuchterBahnhof ? ' aktiv' : '' ?>" id="bf-form"
          autocomplete="off" role="search">
      <input type="hidden" name="s" value="lage">
      <span class="bw-icon" aria-hidden="true">🔍</span>
      <input type="search" name="bf" id="bf-feld" value="<?= $gesuchterBahnhof ? h($bahnhof) : '' ?>"
             maxlength="40" placeholder="Suchen" aria-label="Anderen Bahnhof suchen"
             aria-autocomplete="list" aria-controls="bf-liste" enterkeyhint="go"
             data-schnell='<?= h(json_encode(array_values(array_unique(array_merge(BAHNHOEFE, $zuletztBahnhoefe))),
                                 JSON_UNESCAPED_UNICODE)) ?>'>
      <div class="bw-liste" id="bf-liste" role="listbox" hidden></div>
    </form>
  </div>
  <?php if ($bahnhofUngueltig): ?>
    <div class="box" style="margin-bottom:8px"><div class="hinweis">Dieser Name ist nicht
      zulässig — Buchstaben, Ziffern und die üblichen Zeichen, höchstens 40.</div></div>
  <?php endif; ?>
  <?php if ($bahnhofFehler): ?>
    <div class="box"><div class="hinweis"><?= h($bahnhofFehler) ?></div></div>
  <?php elseif ($bahnhofWartet): ?>
    <div class="box"><div class="leer">Abfahrten für <?= h($bahnhof) ?> werden geholt
      <?= $bahnhofStand ? '(gezeigt ist der Stand von ' . h(date('H:i', strtotime($bahnhofStand))) . ')' : '' ?>
      … <span class="klein">Die Seite lädt gleich von selbst neu.</span></div></div>
  <?php endif; ?>
</section>

<?php if ($tafelStand && $fahrten):
    // Tag der Abfahrten: das Zeitfenster reicht über Mitternacht hinaus.
    $standZeit = strtotime((string)$tafelStand);
    $lageTagFuer = static function (string $hhmm) use ($standZeit): string {
        $tag = date('Y-m-d', $standZeit);
        return ($hhmm < date('H:i', $standZeit - 6 * 3600) && (int)date('H', $standZeit) >= 18)
            ? date('Y-m-d', strtotime($tag . ' +1 day')) : $tag;
    };
    // Welche Zugnummern heute und morgen eine 218 haben — dann steht die Lok in der Zeile.
    $loks218 = [];
    foreach ($istElmshorn ? [date('Y-m-d'), date('Y-m-d', strtotime('+1 day'))] : [] as $tagX) {
        foreach ($umlaeufe[$tagX] ?? [] as $u) {
            if (str_starts_with((string)$u['lok'], '218 ')) {
                $loks218[preg_replace('/\D/', '', (string)$u['zug'])] =
                    ['lok' => (string)$u['lok'], 'quelle' => (string)$u['quelle']];
            }
        }
    }
    // Sonderzug: Linie fehlt oder die Gattung gehört nicht zum Regelverkehr (wie in sonderzuege.py).
    $istSonder = static fn(array $f): bool => trim((string)$f['linie']) === ''
        || !preg_match('/^(RE|RB|S|A|AKN|NBE|IC|ICE|EC|ECE|NJ|FLX)\d*$/i', (string)$f['linie']);
    $zaehler = ['stoerung' => 0, 'sonder' => 0, 'br218' => 0];
    foreach ($fahrten as $f) {
        $zaehler['stoerung'] += ($f['ausgefallen'] || (int)$f['verspaetung'] >= 5 || !empty($f['gleis_geaendert'])) ? 1 : 0;
        $zaehler['sonder']   += $istSonder($f) ? 1 : 0;
        $zaehler['br218']    += isset($loks218[(string)$f['nummer']]) ? 1 : 0;
    } ?>
<section>
  <?php foreach ($stoerung as $m): ?>
    <div class="box" style="margin-bottom:8px"><div class="hinweis"><strong><?= h((string)$m['kategorie']) ?></strong>
      (Priorität <?= (int)$m['prioritaet'] ?>)
      <?= $m['von'] ? '· ' . h((string)$m['von']) . ' bis ' . h((string)$m['bis']) : '' ?></div></div>
  <?php endforeach; ?>

  <?php if ($fahrten): ?>
    <div class="filter" id="lage-filter">
      <button type="button" class="f-chip aktiv" data-filter="alle">Alle <span class="dim"><?= count($fahrten) ?></span></button>
      <button type="button" class="f-chip<?= $zaehler['stoerung'] ? ' warnung' : '' ?>" data-filter="stoerung">
        Nicht pünktlich <span class="dim"><?= $zaehler['stoerung'] ?></span></button>
      <button type="button" class="f-chip" data-filter="sonder">Sonderzüge <span class="dim"><?= $zaehler['sonder'] ?></span></button>
      <?php if ($istElmshorn): ?>
        <button type="button" class="f-chip" data-filter="br218">RE6 mit 218
          <span class="dim"><?= $zaehler['br218'] ?></span></button>
      <?php endif; ?>
    </div>

    <div class="box" id="lage-liste">
      <div class="boxkopf">
        <span class="titel" id="lage-zahl"><?= count($fahrten) ?> Fahrten im Zeitfenster</span>
        <span class="neben">Stand <?= h(date('H:i', $standZeit)) ?> · <?= h((string)$lage['quelle']) ?></span>
      </div>
      <?php foreach ($fahrten as $f):
          $spaet = (int)$f['verspaetung'];
          $weg = (bool)$f['ausgefallen'];
          $auffaellig = $weg || $spaet >= 5 || !empty($f['gleis_geaendert']);
          $kat = preg_replace('/\d.*$/', '', (string)$f['linie']);
          $treffer218 = $loks218[(string)$f['nummer']] ?? null;
          $lok218 = $treffer218['lok'] ?? null; ?>
        <div class="lagezeile<?= $weg ? ' entfaellt' : '' ?><?= $lok218 ? ' br218' : '' ?>"
             data-stoerung="<?= $auffaellig ? '1' : '0' ?>"
             data-sonder="<?= $istSonder($f) ? '1' : '0' ?>"
             data-br218="<?= $lok218 ? '1' : '0' ?>">
          <div class="lz-zeit"><?= h((string)$f['soll']) ?>
            <?php if ($spaet > 0 && !$weg): ?><span class="lz-spaet">+<?= $spaet ?></span><?php endif; ?></div>
          <div class="lz-mitte">
            <div class="lz-zug"><span class="stark"><?= h((string)$f['linie']) ?></span>
              <span class="dim"><?= h((string)$f['nummer']) ?></span><?= $f['ziel'] ? ' → ' . h((string)$f['ziel']) : '' ?>
              <?php if ($weg): ?><span class="etikett warn">Ausfall</span><?php endif; ?>
              <?php if (!empty($f['gleis_geaendert'])): ?><span class="etikett warn">Gleiswechsel</span><?php endif; ?>
              <?php if ($lok218): ?><span class="etikett gut">🚂 <?= lokLink($lok218, $lokNamen) ?></span><?=
                quelleEtikett($treffer218['quelle'] ?? null) ?><?php endif; ?></div>
            <?php if ($f['ursachen'] || ($spaet > 0 && !$weg)): ?>
              <div class="lz-grund"><?= h(implode(' · ', array_filter([
                $spaet > 0 && !$weg ? 'jetzt ' . (string)$f['ist'] : '',
                implode(', ', $f['ursachen'] ?? []),
              ]))) ?></div>
            <?php endif; ?>
          </div>
          <div class="lz-gleis"<?= !empty($f['gleis_geaendert']) ? ' title="Gleis geändert"' : '' ?>><?=
            h((string)($f['gleis'] ?? '–')) ?></div>
          <div class="lz-rechts">
            <?= $weg ? '' : wagenreihungKnopf((string)$kat, (string)$f['nummer'],
                    $lageTagFuer((string)$f['soll']), (string)$f['soll'],
                    trim((string)$f['linie'] . ' ' . (string)$f['nummer'])) ?>
          </div>
        </div>
      <?php endforeach; ?>
      <div class="leer" id="lage-leer" hidden>Keine Fahrt in dieser Auswahl.</div>
    </div>
  <?php else: ?>
    <div class="box"><div class="leer">Keine Fahrten im Zeitfenster.</div></div>
  <?php endif; ?>
</section>
<?php elseif (!$bahnhofWartet && !$bahnhofFehler): ?>
  <section><div class="box"><div class="leer">Noch keine Echtzeitdaten für <?= h($bahnhof) ?>.</div></div></section>
<?php endif; ?>

<!-- Verschickte Meldungen -->
<?php if ($meldungenHeute && $istElmshorn): ?>
<section>
  <h2>Heute gemeldet</h2>
  <div class="box">
    <table>
      <thead><tr><th>Uhrzeit</th><th>Meldung</th><th>Betrifft</th></tr></thead>
      <tbody>
      <?php foreach ($meldungenHeute as $m):
          $art  = (string)$m['art'];
          $wert = (int)$m['wert'];
          [$was, $betrifft] = match ($art) {
              'meldung'     => ['Störungsmeldung der Bahn', 'Priorität ' . $wert],
              'strecke'     => ['Streckenstörung vermutet', $wert . ' Züge betroffen'],
              'ausfall'     => ['Ausfall', 'Zug ' . $m['bezug']],
              'gleis'       => ['Gleiswechsel', 'Zug ' . $m['bezug']],
              'verspaetung' => ['Verspätung der 218', 'Zug ' . $m['bezug'] . ', +' . $wert . ' min'],
              'linie'       => ['Verspätung auf der Linie', 'Zug ' . $m['bezug'] . ', +' . $wert . ' min'],
              'entwarnung'  => ['Entwarnung', 'Lage wieder normal'],
              default       => [$art, (string)$m['bezug']],
          }; ?>
        <tr>
          <td class="dim nowrap" data-l="Uhrzeit"><?= h(date('H:i', strtotime((string)$m['gemeldet_am']))) ?></td>
          <td data-l="Meldung"><?= h($was) ?></td>
          <td class="dim" data-l="Betrifft"><?= h($betrifft) ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endif; ?>

<?php if ($sonderPlan && $bahnhof === 'Elmshorn'): ?>
<section id="sonderzuege">
  <h2>Sonderzüge durch Elmshorn</h2>
  <div class="box">
    <table>
      <thead><tr><th>Tag</th><th>Zeit</th><th>Zug</th><th>Fahrt</th><th>Gleis</th></tr></thead>
      <tbody>
      <?php foreach ($sonderPlan as $z): ?>
        <tr>
          <td class="nowrap" data-l="Tag"><?= h(datumKurz((string)$z['tag'], $WOCHENTAGE)) ?></td>
          <td class="zeitzelle" data-l="Zeit"><?= h((string)$z['zeit']) ?></td>
          <td class="stark" data-l="Zug"><?= h(trim($z['kategorie'] . ' ' . $z['nummer'])) ?></td>
          <td data-l="Fahrt"><?= h((string)$z['von']) ?> → <?= h((string)$z['nach']) ?></td>
          <td class="gleiszelle" data-l="Gleis"><?= h((string)($z['gleis'] ?: '–')) ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endif; ?>

<!-- Beiträge aus dem Umkreis -->
<?php if ($sichtungen && $istElmshorn): ?>
<section id="umkreis">
  <h2>Im Umkreis von Elmshorn</h2>
  <div class="box">
    <div class="boxkopf">
      <span class="titel"><?= count($sichtungen) ?> Beiträge</span>
      <span class="neben">Bild-Sichtungen und Betriebsstörungen · 50 km</span>
    </div>
    <table>
      <thead><tr><th>Ort</th><th>Beitrag</th><th>Gefunden</th></tr></thead>
      <tbody>
      <?php foreach ($sichtungen as $x): ?>
        <tr>
          <td data-l="Ort"><span class="stark"><?= h((string)$x['ort']) ?></span>
            <span class="dim"><?= number_format((float)$x['entfernung'], 0, ',', '.') ?> km</span></td>
          <td class="voll" data-l="Beitrag">
            <a class="titel-link" href="<?= h((string)$x['url']) ?>" target="_blank"
               rel="noopener"><?= h((string)$x['titel']) ?></a>
            <?php if (!empty($x['beschreibung'])): ?>
              <div class="beschreibung"><?= h((string)$x['beschreibung']) ?></div>
            <?php endif; ?></td>
          <td class="dim nowrap" data-l="Gefunden">
            <?= h(date('d.m. H:i', strtotime((string)$x['gesehen_am']))) ?>
            · <?= h(BRETT_LABEL[$x['brett']] ?? (string)$x['brett']) ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endif; ?>
<?php if (!$sichtungen && $istElmshorn): ?>
  <section id="umkreis"><h2>Im Umkreis von Elmshorn</h2><div class="box"><div class="leer">Noch keine Beiträge aus dem Umkreis.</div></div></section>
<?php endif; ?>

<?php elseif ($seite === 'loks'): ?>

<!-- Wo die Loks stehen -->
<?php if ($standorte):
    // 218 zuerst: die sind der Grund, warum hier jemand nachsieht.
    uasort($standorte, static function (array $a, array $b): int {
        $z = static fn(array $g): int => count(array_filter(
            $g, static fn(array $x): bool => str_starts_with((string)$x['lok'], '218 ')));
        return $z($b) <=> $z($a);
    });
    if (isset($standorte['im Einsatz'])) {
        $standorte = ['im Einsatz' => $standorte['im Einsatz']]
                   + array_diff_key($standorte, ['im Einsatz' => 1]);
    }
    $anzahlLoks = array_sum(array_map('count', $standorte)); ?>
<section>
  <h2>Wo die Loks stehen <a class="h2-link" href="./?s=karte">Karte →</a></h2>
  <div class="box">
    <div class="boxkopf">
      <span class="titel"><?= $anzahlLoks ?> Loks an <?= count($standorte) ?> Orten</span>
      <span class="neben">Stand <?= h(datumKurz((string)$standortTag, $WOCHENTAGE)) ?> · Fahrzeugliste im Forum</span>
    </div>
    <?php foreach ($standorte as $ort => $gruppe): ?>
      <div class="standort">
        <div class="ortsname"><?= h((string)$ort) ?> <span class="dim"><?= count($gruppe) ?></span></div>
        <div class="lokliste">
          <?php foreach ($gruppe as $x):
              $ist218 = str_starts_with((string)$x['lok'], '218 ');
              $faehrt = $x['hat_umlauf'] !== null && (int)$x['hat_umlauf'] === 1;
              $bes    = besonders((string)$x['lok'], $besondere);
              $tipp   = trim(($faehrt ? 'fährt heute. ' : '') . ($x['weg'] ?? '') . ' ' . $bes);
              $einsatz = $lokQuellen[$x['lok']] ?? [];
              $art = isset($einsatz['regio']) && isset($einsatz['shuttle']) ? 'beide'
                   : (isset($einsatz['regio']) ? 'regio' : (isset($einsatz['shuttle']) ? 'shuttle' : '')); ?>
            <a class="lokmarke lok-link<?= $ist218 ? ' br218' : '' ?><?= $faehrt ? ' faehrt' : '' ?><?= $bes ? ' besonders' : '' ?><?= $art ? ' art-' . $art : '' ?>"
               href="#" data-lok="<?= h((string)$x['lok']) ?>"
               <?= $tipp ? 'title="' . h($tipp) . '"' : '' ?>><?= h((string)$x['lok']) ?><?php
                 if (isset($lokNamen[$x['lok']])): ?><span class="lokname"><?= h((string)$lokNamen[$x['lok']]) ?></span><?php
                 endif; ?><?= $faehrt ? ' ●' : '' ?><?php
                 if ($art): ?><span class="lokart"><?= $art === 'beide' ? 'RE6+Shuttle'
                   : ($art === 'regio' ? 'RE6' : 'Shuttle') ?></span><?php endif; ?></a>
          <?php endforeach; ?>
        </div>
        <?php foreach ($gruppe as $x): if (!empty($x['weg'])): ?>
          <div class="weg-zeile"><?= h((string)$x['lok']) ?> · <?= h((string)$x['weg']) ?></div>
        <?php endif; endforeach; ?>
      </div>
    <?php endforeach; ?>
    <div class="leer" style="padding-top:6px">● heißt: hat heute einen Umlauf.
      <b>RE6</b> fährt im Regionalverkehr, <b>Shuttle</b> vor den Autozügen und IC nach Sylt —
      aus den Umläufen der letzten 60 Tage. Eine Lok antippen für den Steckbrief.</div>
  </div>
</section>
<?php endif; ?>

<!-- Besondere 218er -->
<?php if ($besondere):
    $gesehen = array_filter($besondere, static fn(array $b): bool =>
        $b['zuletzt_stand'] !== null || $b['zuletzt_gefahren'] !== null);
    $stand = max(array_map(static fn(array $b): string => (string)$b['stand'], $besondere));
    // Wer heute fährt, steht oben.
    $heuteFahrten = $umlaeufe[date('Y-m-d')] ?? [];
    $mitLage = [];
    foreach ($gesehen as $b) {
        $mitLage[] = [$b, lageHeute((string)$b['lok'], $heuteFahrten,
                                    $standortHeute[$b['lok']] ?? null, $b, $WOCHENTAGE)];
    }
    usort($mitLage, static fn(array $x, array $y): int => (int)$y[1]['aktiv'] <=> (int)$x[1]['aktiv']);
    $aktive = count(array_filter($mitLage, static fn(array $z): bool => $z[1]['aktiv'])); ?>
<section>
  <h2>Besondere 218er</h2>
  <div class="box">
    <div class="boxkopf">
      <span class="titel"><?= $aktive ?> heute im Einsatz</span>
      <span class="neben"><?= count($gesehen) ?> hier gesehen · <?= count($besondere) ?> insgesamt</span>
    </div>
    <?php foreach ($mitLage as [$b, $lg]):
        $bes = besonders((string)$b['lok'], $besondere); ?>
      <div class="lokzeile<?= $lg['aktiv'] ? ' aktiv' : '' ?>">
        <div class="lz-name"><?= lokLink((string)$b['lok'], $lokNamen) ?>
          <?= lokEinsatz((string)$b['lok'], $lokQuellen) ?><?php
          if ($b['name'] && !(int)$b['belegt']): ?><span class="klein"
            title="Name bisher nur in Wikipedia">ohne zweite Quelle</span><?php endif; ?></div>
        <div class="lz-lage"><?= $lg['aktiv'] ? '<span class="live">●</span> ' : '' ?><?= h($lg['jetzt']) ?>
          <span class="klein"><?= h($lg['heute']) ?><?= $bes ? ' · ' . h($bes) : '' ?></span></div>
      </div>
    <?php endforeach; ?>
    <details class="alle">
      <summary>Alle <?= count($besondere) ?> besonderen 218er</summary>
      <?php foreach ($besondere as $b): ?>
        <div class="lokzeile">
          <div class="lz-name"><?= lokLink((string)$b['lok'], $lokNamen) ?></div>
          <div class="lz-lage"><?= h(besonders((string)$b['lok'], $besondere) ?: (string)$b['lackierung']) ?>
            <span class="klein"><?= h((string)$b['betreiber']) ?> · <?= h((string)$b['zustand']) ?></span></div>
        </div>
      <?php endforeach; ?>
      <p class="leer">Quelle: Bestandstabelle im Wikipedia-Artikel „DB-Baureihe 218“, wöchentlich neu
        eingelesen, Stand <?= h(date('d.m.Y', strtotime($stand))) ?>. „ohne zweite Quelle“ heißt:
        Der Name steht bisher nur dort.</p>
    </details>
    <div class="leer" style="padding-top:0">„jetzt“ ist aus den heutigen Fahrten und der Uhrzeit
      abgeleitet — der Plan, keine Ortung.</div>
  </div>
</section>
<?php endif; ?>

<?php elseif ($seite === 'tage'): ?>

<div class="status">
  <?php if ($stoerung): ?>
    <span class="chip warn">Störung gemeldet</span>
  <?php elseif ($fahrten): ?>
    <span class="chip gut">Strecke ohne Störung</span>
  <?php endif; ?>
  <span class="chip"><?= count($tage) ?> Tage erfasst</span>
  <span class="chip"><?= $anzTreffer ?> mit 218 durch Elmshorn</span>
  <?php if ($lage): ?>
    <span class="chip<?= $lageAlt ? ' warn' : '' ?>">
      Stand <?= h(date('H:i', strtotime((string)$lage['stand']))) ?><?= $lageAlt ? ' — veraltet' : '' ?></span>
  <?php endif; ?>
</div>

<!-- Die Tage -->
<section>
  <h2>Tageslisten</h2>
  <div class="schalter" style="margin-bottom:10px">
    <a href="./?s=tage" class="<?= $nurTreffer ? '' : 'aktiv' ?>">Alle Tage</a>
    <a href="./?s=tage&amp;treffer" class="<?= $nurTreffer ? 'aktiv' : '' ?>">Nur Treffer</a>
  </div>
  <div class="lok-suche">
    <input type="search" id="tage-suche" class="lok-suche-feld" list="tage-loks" autocomplete="off"
           placeholder="Lok suchen — z. B. 330 oder Konrad" aria-label="Lok suchen"
           value="<?= h((string)($_GET['lok'] ?? '')) ?>">
    <datalist id="tage-loks"></datalist>
    <div class="lok-suche-info" id="tage-suche-info" hidden></div>
  </div>
  <div class="filter" id="tage-filter">
    <button type="button" class="f-chip aktiv" data-quelle="alle">Alle Umläufe</button>
    <button type="button" class="f-chip" data-quelle="regio">RE6 · DB Regio</button>
    <button type="button" class="f-chip" data-quelle="shuttle">SyltShuttle / IC</button>
  </div>

  <?php if (!$tage && !$dbFehler): ?>
    <div class="box"><div class="leer">Noch keine Daten — der erste Prüflauf steht aus.</div></div>
  <?php endif; ?>

  <?php
  /** Eine Fahrt als kompakte Zeile: Zeit, Lok, Zug, Lauf, Merk-Knopf. */
  $fahrtZeile = static function (array $l, array $ext, string $tag, bool $elmshorn) use (
          $lokNamen, $besondere, $interessen): string {
      $weg = !empty($l['entfallen']) || ($ext && !(int)$ext['aktiv']);
      $zeit = $ext['elmshorn_zeit'] ?? ($l['elmshorn_zeit'] ?? null);
      $gleis = $ext['gleis'] ?? ($l['gleis'] ?? null);
      $geschaetzt = $ext && ($ext['zeit_quelle'] ?? '') !== 'fahrplan' && $zeit;
      $vorbei = $tag === date('Y-m-d') && !empty($l['nach_zeit'])
                && (string)$l['nach_zeit'] >= (string)($l['von_zeit'] ?? '')
                && (string)$l['nach_zeit'] < date('H:i');
      $schluessel = $tag . '|' . $l['lok'] . '|' . $l['zug'];
      $knopf = '';
      if (!$weg && !$vorbei && $tag >= date('Y-m-d')) {
          $knopf = isset($interessen[$schluessel])
              ? merkknopf('entfernen', $tag, (string)$l['lok'], (string)$l['zug'], '★', 'knopf-klein aktiv')
              : merkknopf('merken', $tag, (string)$l['lok'], (string)$l['zug'], '☆', 'knopf-klein');
      }
      $bes = besonders((string)$l['lok'], $besondere);
      return '<div class="tzeile' . ($weg ? ' gestrichen' : '') . ($elmshorn ? ' elmshorn' : '')
           . '" data-quelle="' . h((string)($l['quelle'] ?? '')) . '"'
           // Für die Lok-Suche: Nummer, Nummer ohne Leerzeichen, Name, Zug
           . ' data-such-lok="' . h((string)$l['lok']) . '" data-name="' . h((string)($lokNamen[$l['lok']] ?? '')) . '"'
           . ' data-such="' . h(mb_strtolower((string)$l['lok'] . ' ' . preg_replace('/\D/', '', (string)$l['lok']) . ' '
                                . ($lokNamen[$l['lok']] ?? '') . ' ' . $l['zug'])) . '">'
           . '<div class="tz-zeit">' . ($elmshorn ? h((string)($zeit ?: '–')) : '<span class="dim">–</span>')
           . ($gleis && $elmshorn ? '<span class="tz-gleis">Gl. ' . h((string)$gleis) . '</span>' : '')
           . ($geschaetzt ? '<span class="klein">geschätzt</span>' : '') . '</div>'
           . '<div class="tz-mitte"><div class="tz-lok">' . lokLink((string)$l['lok'], $lokNamen)
           . ' <span class="dim">' . h((string)$l['zug']) . '</span> ' . quelleEtikett($l['quelle'] ?? null)
           . ($weg ? '<span class="etikett warn">gestrichen</span>' : '') . '</div>'
           . '<div class="tz-weg">' . h((string)$l['von_halt']) . ($l['von_zeit'] ? ' ' . h((string)$l['von_zeit']) : '')
           . ' → ' . h((string)$l['nach_halt']) . ($l['nach_zeit'] ? ' ' . h((string)$l['nach_zeit']) : '')
           . ' · ' . h((string)$l['richtung']) . ($bes ? ' · ' . h($bes) : '') . '</div></div>'
           . '<div class="tz-knopf">' . $knopf . '</div></div>';
  }; ?>

  <?php foreach ($tage as $t):
      $treffer = $t['treffer'];
      $tag = (string)$t['tag'];
      // Alle Fahrten des Tages; dazu gestrichene Elmshorn-Läufe, die nicht mehr
      // im Beitrag stehen und deshalb in umlaeufe fehlen.
      $zeilen = $umlaeufe[$tag] ?? [];
      $vorhanden = [];
      foreach ($zeilen as $z) { $vorhanden[$z['lok'] . '|' . $z['zug']] = true; }
      foreach ($laeufe[$tag] ?? [] as $l) {
          if (!isset($vorhanden[$l['lok'] . '|' . $l['zug']])) {
              $zeilen[] = $l + ['elmshorn' => 1, 'entfallen' => true];
          }
      }
      $zusatz = [];
      foreach ($laeufe[$tag] ?? [] as $l) { $zusatz[$l['lok'] . '|' . $l['zug']] = $l; }

      // Elmshorn-Fahrten stehen vorn, alles andere klappt man bei Bedarf auf.
      $durch = $sonstige = [];
      foreach ($zeilen as $l) {
          $ext = $zusatz[$l['lok'] . '|' . $l['zug']] ?? null;
          if (!empty($l['elmshorn']) || $ext) { $durch[] = [$l, $ext]; } else { $sonstige[] = [$l, $ext]; }
      }
      $loksHeute = array_values(array_unique(array_map(static fn(array $z): string => (string)$z['lok'], $zeilen)));
      $meldung = $meldungen[$tag] ?? null;
      $offen = $tag >= date('Y-m-d', strtotime('-1 day')) || $treffer; ?>
    <details class="box tagkarte"<?= $offen ? ' open' : '' ?>>
      <summary class="tagkopf">
        <span class="tk-datum"><?= h(wochentag($tag, $WOCHENTAGE)) ?> <?= h(date('d.m.', strtotime($tag))) ?></span>
        <span class="chip <?= $treffer ? 'gut' : '' ?>"><?= $treffer ? count($durch) . '× Elmshorn' : 'keine 218' ?></span>
        <?php if ($meldung): ?><span class="chip" title="Tagesmeldung verschickt">✉</span><?php endif; ?>
        <span class="tk-neben"><?= count($zeilen) ?> Fahrten<?= $loksHeute ? ' · ' . h(implode(', ', array_slice($loksHeute, 0, 3)))
          . (count($loksHeute) > 3 ? ' +' . (count($loksHeute) - 3) : '') : '' ?></span>
      </summary>

      <?php foreach ($durch as [$l, $ext]) { echo $fahrtZeile($l, $ext ?: [], $tag, true); } ?>

      <?php if ($sonstige): ?>
        <details class="weitere">
          <summary><?= count($sonstige) ?> weitere Fahrten ohne Elmshorn</summary>
          <?php foreach ($sonstige as [$l, $ext]) { echo $fahrtZeile($l, $ext ?: [], $tag, false); } ?>
        </details>
      <?php endif; ?>

      <?php if (!$zeilen): ?>
        <div class="leer">
          <?php foreach ($t['quellen'] as $q): ?>
            <div><strong><?= h(QUELLE_LABEL[$q['quelle']] ?? $q['quelle']) ?></strong> —
              <?= $q['loks_218'] ? 'genannt, aber ohne Fahrten: ' . h((string)$q['loks_218']) : 'keine 218 genannt' ?></div>
          <?php endforeach; ?>
        </div>
      <?php endif; ?>

      <div class="tk-fuss">
        <?php foreach ($t['quellen'] as $i => $q): ?>
          <?= $i ? ' · ' : '' ?><a href="<?= h((string)$q['url']) ?>" target="_blank"
            rel="noopener"><?= h(QUELLE_LABEL[$q['quelle']] ?? $q['quelle']) ?> im Forum ↗</a>
        <?php endforeach; ?>
      </div>
    </details>
  <?php endforeach; ?>
</section>

<?php elseif ($seite === 'mehr'): ?>

<section>
  <h2>Menü</h2>
  <div class="box liste">
    <a href="geraete.php"><span class="l-icon">🔔</span>
      <span class="l-text"><span class="l-titel">Benachrichtigungen &amp; Geräte</span>
        <span class="l-neben">Geräte verwalten, Testnachricht senden</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=archiv"><span class="l-icon">🗄️</span>
      <span class="l-text"><span class="l-titel">Archiv beobachteter Fahrten</span>
        <span class="l-neben">Was du beobachtet hast und wie es ausging</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=verlauf"><span class="l-icon">🗒️</span>
      <span class="l-text"><span class="l-titel">Benachrichtigungsverlauf</span>
        <span class="l-neben">Was wann per Push und Mail rausging</span></span><span class="l-pfeil">›</span></a>
    <a href="#einstellungen"><span class="l-icon">⚙️</span>
      <span class="l-text"><span class="l-titel">Einstellungen</span>
        <span class="l-neben">Startbildschirm und Animationen</span></span><span class="l-pfeil">›</span></a>
    <a href="#app"><span class="l-icon">📲</span>
      <span class="l-text"><span class="l-titel">App installieren</span>
        <span class="l-neben">Zugradar auf dem Startbildschirm</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=karte"><span class="l-icon">🗺️</span>
      <span class="l-text"><span class="l-titel">Karte</span>
        <span class="l-neben">Marschbahn, 218er laut Plan, Umkreis</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=quellen"><span class="l-icon">🔄</span>
      <span class="l-text"><span class="l-titel">Quellen &amp; Abfragen</span>
        <span class="l-neben">Wann wurde was geholt, jetzt prüfen</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=logs"><span class="l-icon">📄</span>
      <span class="l-text"><span class="l-titel">Logs</span>
        <span class="l-neben">Protokolle der Hintergrunddienste</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=statistik"><span class="l-icon">📊</span>
      <span class="l-text"><span class="l-titel">Statistik</span>
        <span class="l-neben">Welche Lok wie oft, Wochentage, Uhrzeiten</span></span><span class="l-pfeil">›</span></a>
    <a href="./?s=info"><span class="l-icon">ℹ️</span>
      <span class="l-text"><span class="l-titel">Über Zugradar</span>
        <span class="l-neben">Was die App macht, woher die Daten kommen</span></span><span class="l-pfeil">›</span></a>
    <a class="gefahr" href="?logout"><span class="l-icon">⎋</span>
      <span class="l-text"><span class="l-titel">Abmelden</span>
        <span class="l-neben">Dieses Gerät vergisst die Anmeldung</span></span></a>
  </div>
</section>

<section id="einstellungen">
  <h2>Einstellungen</h2>
  <?php if (isset($_GET['gespeichert'])): ?>
    <div class="box" style="margin-bottom:8px"><div class="leer">Gespeichert.</div></div>
  <?php endif; ?>

  <form method="post" class="box" style="margin-bottom:10px">
    <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
    <input type="hidden" name="aktion" value="einstellungen">
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Live-Meldung für beobachtete Fahrten</div>
        <div class="e-neben">Eine Nachricht, die sich bis zur Abfahrt von selbst aktualisiert</div></div>
      <select name="merken_live_meldung">
        <option value="1"<?= ($einstellungen['merken_live_meldung'] ?? '1') === '1' ? ' selected' : '' ?>>an</option>
        <option value="0"<?= ($einstellungen['merken_live_meldung'] ?? '1') === '0' ? ' selected' : '' ?>>aus</option>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Ab wann</div>
        <div class="e-neben">So lange vor der Abfahrt beginnt die Live-Meldung</div></div>
      <select name="merken_live_ab_minuten">
        <?php foreach (['15' => '15 Minuten', '30' => '30 Minuten', '60' => '1 Stunde', '120' => '2 Stunden'] as $wert => $text): ?>
          <option value="<?= $wert ?>"<?= ($einstellungen['merken_live_ab_minuten'] ?? '60') === $wert ? ' selected' : '' ?>><?= $text ?></option>
        <?php endforeach; ?>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Angepinnt lassen</div>
        <div class="e-neben">Die Meldung bleibt liegen, bis du sie antippst oder wegwischst</div></div>
      <select name="merken_live_festhalten">
        <option value="1"<?= ($einstellungen['merken_live_festhalten'] ?? '1') === '1' ? ' selected' : '' ?>>ja</option>
        <option value="0"<?= ($einstellungen['merken_live_festhalten'] ?? '1') === '0' ? ' selected' : '' ?>>nein</option>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-neben">Gilt für alle Geräte, weil der Server verschickt.</div></div>
      <button type="submit" class="knopf-klein haupt">Speichern</button>
    </div>
  </form>

  <div class="box">
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Startbildschirm</div>
        <div class="e-neben">Wie lange das Lok-Bild beim Öffnen der App zu sehen ist</div></div>
      <select id="e-dauer" aria-label="Dauer des Startbildschirms">
        <option value="0">aus</option>
        <option value="900">kurz (0,9 s)</option>
        <option value="1600">normal (1,6 s)</option>
        <option value="2500">lang (2,5 s)</option>
        <option value="4000">sehr lang (4 s)</option>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Startbildschirm auch im Browser</div>
        <div class="e-neben">Sonst erscheint er nur in der installierten App, einmal je Sitzung</div></div>
      <select id="e-immer" aria-label="Startbildschirm auch im Browser">
        <option value="0">nur in der App</option>
        <option value="1">bei jedem Öffnen</option>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Animationen</div>
        <div class="e-neben">Übergänge, Aufblenden und Ladebalken</div></div>
      <select id="e-motion" aria-label="Animationen">
        <option value="an">an</option>
        <option value="aus">aus</option>
      </select>
    </div>
    <div class="einstellung">
      <div class="e-text"><div class="e-titel">Ausprobieren</div>
        <div class="e-neben">Zeigt den Startbildschirm mit der gewählten Dauer</div></div>
      <button type="button" id="e-test">Startbildschirm zeigen</button>
    </div>
  </div>
  <p class="leer">Die Einstellungen gelten für dieses Gerät und bleiben dort gespeichert.</p>
</section>

<section id="app">
  <h2>App &amp; Benachrichtigungen</h2>
<div id="app-bereich" class="app-bereich">
  <button id="app-knopf" class="app-knopf" type="button" hidden>
    <img src="icon-192.png" alt="" width="28" height="28"> Als App installieren</button>
  <div id="app-hinweis" class="app-hinweis" hidden></div>
  <div id="app-status" class="app-status" hidden>Läuft als App ✓</div>
  <div id="push-bereich" class="push-bereich" hidden></div>
  <a class="geraete-link" href="geraete.php">Geräte &amp; Testnachrichten →</a>
</div>
</section>


<?php elseif ($seite === 'info'): ?>

<section>
  <div class="box ueber">
    <img src="icon-192.png" alt="" width="84" height="84">
    <div class="ueber-titel">Zugradar</div>
    <div class="ueber-von">gemacht von <strong>Jarrit</strong> · <a href="https://jarritc.de">jarritc.de</a></div>
    <div class="ueber-version">Version <?= zugradar_version()['nummer'] ?> · Stand <?= zugradar_version()['stand'] ?></div>
  </div>
</section>

<section>
  <h2>Was Zugradar macht</h2>
  <div class="box liste">
    <div class="zeile"><span class="l-icon">🔎</span><span class="l-text">
      <span class="l-titel">Stündlich ins Forum schauen</span>
      <span class="l-neben">Die Tageslisten der Marschbahn auf Drehscheibe-Online, Regio und SyltShuttle</span></span></div>
    <div class="zeile"><span class="l-icon">📍</span><span class="l-text">
      <span class="l-titel">Elmshorn erkennen</span>
      <span class="l-neben">Elmshorn steht in keinem Beitrag — gewertet wird jeder Umlauf über Streckenkilometer 30,7</span></span></div>
    <div class="zeile"><span class="l-icon">🕒</span><span class="l-text">
      <span class="l-titel">Zeit, Gleis und Echtzeit</span>
      <span class="l-neben">Aus dem Fahrplan der Bahn, dazu Verspätungen, Ausfälle und Ursachen</span></span></div>
    <div class="zeile"><span class="l-icon">🔔</span><span class="l-text">
      <span class="l-titel">Bescheid geben</span>
      <span class="l-neben">Push aufs Handy, per Mail nur das Wichtigste; Erinnerung 30 Minuten vor der Durchfahrt</span></span></div>
    <div class="zeile"><span class="l-icon">🚃</span><span class="l-text">
      <span class="l-titel">Wagenreihung</span>
      <span class="l-neben">Wo die Lok am Bahnsteig hält, vorne oder hinten und in welchem Abschnitt</span></span></div>
    <div class="zeile"><span class="l-icon">🚂</span><span class="l-text">
      <span class="l-titel">Loks im Blick</span>
      <span class="l-neben">Standorte, Eigennamen wie „Konrad“ und „Donna“, besondere 218er, Steckbrief je Lok</span></span></div>
  </div>
</section>

<section>
  <h2>In Zahlen</h2>
  <div class="kacheln">
    <div class="kachel"><div class="k-label">Tage erfasst</div>
      <div class="k-wert"><?= (int)($zahlen['tage'] ?? 0) ?></div>
      <div class="k-neben">seit <?= h($zahlen['seit'] ? date('d.m.Y', strtotime((string)$zahlen['seit'])) : '—') ?></div></div>
    <div class="kachel gut"><div class="k-label">Durch Elmshorn</div>
      <div class="k-wert"><?= (int)($zahlen['durchfahrten'] ?? 0) ?></div>
      <div class="k-neben">an <?= (int)($zahlen['treffer_tage'] ?? 0) ?> Tagen</div></div>
    <div class="kachel"><div class="k-label">Umläufe erfasst</div>
      <div class="k-wert"><?= (int)($zahlen['umlaeufe'] ?? 0) ?></div>
      <div class="k-neben"><?= (int)($zahlen['loks'] ?? 0) ?> verschiedene Loks</div></div>
    <div class="kachel"><div class="k-label">Beiträge im Umkreis</div>
      <div class="k-wert"><?= (int)($zahlen['sichtungen'] ?? 0) ?></div>
      <div class="k-neben">50 km um Elmshorn</div></div>
    <div class="kachel"><div class="k-label">Benachrichtigungen</div>
      <div class="k-wert"><?= (int)($zahlen['meldungen'] ?? 0) ?></div>
      <div class="k-neben"><a href="./?s=verlauf">Verlauf ansehen</a></div></div>
    <div class="kachel"><div class="k-label">Geräte</div>
      <div class="k-wert"><?= (int)($zahlen['geraete'] ?? 0) ?></div>
      <div class="k-neben"><a href="geraete.php">verwalten</a></div></div>
  </div>
</section>

<section>
  <h2>So funktioniert’s</h2>
<p class="fuss">
  <strong>Elmshorn</strong> wird in den Forumsbeiträgen nie genannt. Der Ort liegt bei
  Streckenkilometer 30,7 ab Hamburg-Altona — gewertet wird jeder Umlauf, der von
  südlich nach nördlich davon führt oder umgekehrt.<br>
  <strong>Zeit und Gleis</strong> stammen aus dem Fahrplan, über die Zugnummer zugeordnet.
  Steht dort kein passender Zug, wird die Zeit interpoliert und als <em>geschätzt</em>
  gekennzeichnet; sie liegt dann typisch drei Minuten daneben.<br>
  <strong>Die Lage</strong> wird alle 10 Minuten fortgeschrieben, diese Seite fragt keine
  Schnittstelle selbst ab. Älter als 30 Minuten wird sie als veraltet markiert.<br>
  <strong>Der Umkreis</strong> ergibt sich aus Orten in Titel und Beitrag; Klammerzusätze
  zählen mit — „Harburg" liegt 40 km entfernt, „Harburg (Schwab)" dagegen 557.
  Das Live-Sichtungssystem der Drehscheibe wird nicht abgefragt, sein Pfad ist in der
  robots.txt für Automaten gesperrt.
</p>
</section>

<section>
  <h2>Daten und Dank</h2>
  <div class="box liste">
    <a href="https://www.drehscheibe-online.de/foren/list.php?006" target="_blank" rel="noopener">
      <span class="l-icon">💬</span><span class="l-text"><span class="l-titel">Drehscheibe-Online</span>
      <span class="l-neben">Die Tageslisten und die Beiträge aus dem Umkreis — geschrieben von Leuten, die hinschauen</span></span>
      <span class="l-pfeil">↗</span></a>
    <a href="https://developers.deutschebahn.com" target="_blank" rel="noopener">
      <span class="l-icon">🚉</span><span class="l-text"><span class="l-titel">Deutsche Bahn</span>
      <span class="l-neben">Fahrplan, Gleis, Verspätungen und Ursachen (Timetables-Schnittstelle)</span></span>
      <span class="l-pfeil">↗</span></a>
    <a href="https://dbf.finalrewind.org/" target="_blank" rel="noopener">
      <span class="l-icon">🚃</span><span class="l-text"><span class="l-titel">dbf von derf</span>
      <span class="l-neben">Die Wagenreihung, frei zugänglich und quelloffen</span></span>
      <span class="l-pfeil">↗</span></a>
    <a href="https://transitous.org" target="_blank" rel="noopener">
      <span class="l-icon">🌍</span><span class="l-text"><span class="l-titel">transitous</span>
      <span class="l-neben">Freier Fahrplandienst, Rückfall wenn die Bahn nicht antwortet</span></span>
      <span class="l-pfeil">↗</span></a>
    <a href="https://de.wikipedia.org/wiki/DB-Baureihe_218" target="_blank" rel="noopener">
      <span class="l-icon">📖</span><span class="l-text"><span class="l-titel">Wikipedia</span>
      <span class="l-neben">Bestandsliste der Baureihe 218: Namen, Lackierungen, Zustand</span></span>
      <span class="l-pfeil">↗</span></a>
  </div>
  <p class="leer">Alle Angaben ohne Gewähr. Zugradar ist privat, läuft auf dem eigenen Server
    und gibt nichts an Dritte weiter.</p>
</section>

<?php elseif ($seite === 'fahrt'): ?>

<?php if (!$fahrt): ?>
  <section><h2>Fahrt</h2><div class="box"><div class="leer">Diese Fahrt steht nicht (mehr) in den
    Tageslisten. <a href="./">Zur Übersicht</a></div></div></section>
<?php else:
    $fTag = (string)$fahrt['tag'];
    $stand = fahrtStand(['zeit' => $fahrt['elmshorn_zeit'], 'zug' => $fahrt['zug'],
                         'gleis' => $fahrt['gleis']], $fTag, $echtzeitJeZug);
    $weg = !(int)$fahrt['aktiv'];
    $schl = $fTag . '|' . $fahrt['lok'] . '|' . $fahrt['zug'];
    $lage = $weg ? 'gestrichen' : ($stand['ausfall'] ? 'Ausfall' : $stand['lage']); ?>
<div class="hero">
  <div class="label"><?= h(datumKurz($fTag, $WOCHENTAGE)) ?> ·
    <?= $fahrt['elmshorn_zeit'] ? 'durch Elmshorn' : 'Fahrt ohne Elmshorn' ?></div>
  <div class="zeit"><?= h((string)($stand['zeit'] ?: $fahrt['von_zeit'] ?: '–')) ?>
    <?php if ($stand['gleis']): ?><span class="gleis">Gleis <?= h((string)$stand['gleis']) ?></span><?php endif; ?>
    <?php if ($stand['spaet'] > 0): ?><span class="bis warn">+<?= $stand['spaet'] ?> min</span><?php endif; ?>
    <?php if ($lage === 'kommt' && ($bis = bisDahin($fTag, (string)$stand['zeit']))): ?>
      <span class="bis"><?= h($bis) ?></span><?php endif; ?>
  </div>
  <div class="lok"><?= lokLink((string)$fahrt['lok'], $lokNamen) ?> ·
    <?= h((string)$fahrt['zug']) ?> <?= quelleEtikett($fahrt['quelle'] ?? null) ?></div>
  <div class="weg"><?= h((string)$fahrt['von_halt']) ?> <?= h((string)$fahrt['von_zeit']) ?> →
    <?= h((string)$fahrt['nach_halt']) ?> <?= h((string)$fahrt['nach_zeit']) ?> ·
    <?= h((string)$fahrt['richtung']) ?></div>
  <div class="weg">
    <?php if ($lage === 'gestrichen'): ?><span class="etikett warn">gestrichen</span>
    <?php elseif ($lage === 'Ausfall'): ?><span class="etikett warn">Ausfall laut Bahn</span>
    <?php elseif ($lage === 'fertig'): ?><span class="etikett gut">durch, <?= h((string)$stand['zeit']) ?></span>
    <?php elseif ($lage === 'vorbei'): ?><span class="etikett">vorbei</span>
    <?php else: ?><span class="etikett gut">kommt noch</span><?php endif; ?>
    <?php if ($b = besonders((string)$fahrt['lok'], $besondere)): ?>
      <span class="etikett"><?= h($b) ?></span><?php endif; ?>
  </div>
  <div class="f-knopf" style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
    <?php if ($lage === 'kommt' && $fahrt['elmshorn_zeit']):
        [$kat, $nr] = array_pad(explode(' ', (string)$fahrt['zug'], 2), 2, '');
        echo wagenreihungKnopf($kat, $nr, $fTag, (string)$fahrt['elmshorn_zeit'],
                               mitName((string)$fahrt['lok'], $lokNamen) . ' · ' . $fahrt['zug']);
    endif;
    if (!$weg && $lage !== 'vorbei') {
        echo isset($interessen[$schl])
            ? merkknopf('entfernen', $fTag, (string)$fahrt['lok'], (string)$fahrt['zug'], '★ beobachtet', 'knopf-klein aktiv')
            : merkknopf('merken', $fTag, (string)$fahrt['lok'], (string)$fahrt['zug'], '☆ interessiert mich', 'knopf-klein');
    } ?>
  </div>
</div>

<?php if ($fahrtTag): ?>
<section>
  <h2>Der Tag dieser Lok</h2>
  <div class="box">
    <?php foreach ($fahrtTag as $u):
        $dies = $u['zug'] === $fahrt['zug']; ?>
      <div class="tzeile<?= $dies ? ' elmshorn' : '' ?>">
        <div class="tz-zeit"><?= h((string)$u['von_zeit']) ?></div>
        <div class="tz-mitte">
          <div class="tz-lok"><?= h((string)$u['zug']) ?> <?= quelleEtikett($u['quelle'] ?? null) ?>
            <?php if ((int)$u['elmshorn']): ?><span class="etikett gut">Elmshorn<?= $u['elmshorn_zeit']
              ? ' ' . h((string)$u['elmshorn_zeit']) : '' ?></span><?php endif; ?>
            <?php if ($dies): ?><span class="etikett">diese Fahrt</span><?php endif; ?></div>
          <div class="tz-weg"><?= h((string)$u['von_halt']) ?> → <?= h((string)$u['nach_halt']) ?>
            <?= h((string)$u['nach_zeit']) ?> · <?= h((string)$u['richtung']) ?></div>
        </div>
        <div class="tz-knopf"><?php if (!$dies && (int)$u['elmshorn']): ?>
          <a class="knopf-klein" href="<?= h(fahrtUrl($fTag, (string)$u['lok'], (string)$u['zug'])) ?>">›</a>
        <?php endif; ?></div>
      </div>
    <?php endforeach; ?>
  </div>
</section>
<?php endif; ?>

<section>
  <h2>Dazu</h2>
  <div class="box liste">
    <a href="./?s=lage"><span class="l-icon">📡</span><span class="l-text">
      <span class="l-titel">Live in Elmshorn</span>
      <span class="l-neben">Alle Abfahrten mit Verspätung und Gleis</span></span><span class="l-pfeil">›</span></a>
    <?php foreach ($fahrtQuellen as $q): ?>
      <a href="<?= h((string)$q['url']) ?>" target="_blank" rel="noopener"><span class="l-icon">💬</span>
        <span class="l-text"><span class="l-titel"><?= h(QUELLE_LABEL[$q['quelle']] ?? $q['quelle']) ?> im Forum</span>
        <span class="l-neben">Der Beitrag, aus dem diese Fahrt stammt</span></span><span class="l-pfeil">↗</span></a>
    <?php endforeach; ?>
  </div>
</section>
<?php endif; ?>

<?php elseif ($seite === 'statistik'):
    $wtNamen = [2 => 'Mo', 3 => 'Di', 4 => 'Mi', 5 => 'Do', 6 => 'Fr', 7 => 'Sa', 1 => 'So'];
    $maxLok = max(array_map(static fn(array $z): int => (int)$z['fahrten'], $statistik['loks'] ?: [['fahrten' => 1]]));
    $maxStunde = max(array_map(static fn(array $z): int => (int)$z['n'], $statistik['stunden'] ?: [['n' => 1]]));
    $wt = [];
    foreach ($statistik['wochentage'] as $z) { $wt[(int)$z['wt']] = $z; }
    $maxWt = max(array_map(static fn(array $z): int => (int)$z['tage'], $statistik['wochentage'] ?: [['tage' => 1]])); ?>

<section>
  <h2>Statistik</h2>
  <div class="kacheln">
    <div class="kachel"><div class="k-label">Tage mit Liste</div>
      <div class="k-wert"><?= $statistik['tage_gesamt'] ?></div>
      <div class="k-neben">seit <?= h($statistik['erste'] ? date('d.m.Y', strtotime((string)$statistik['erste'])) : '—') ?></div></div>
    <div class="kachel gut"><div class="k-label">Tage mit 218</div>
      <div class="k-wert"><?= $statistik['tage_treffer'] ?></div>
      <div class="k-neben"><?= $statistik['tage_gesamt'] ? round(100 * $statistik['tage_treffer'] / $statistik['tage_gesamt']) : 0 ?> % der Tage</div></div>
    <?php foreach ($statistik['richtung'] as $z): ?>
      <div class="kachel"><div class="k-label"><?= h((string)$z['richtung']) ?></div>
        <div class="k-wert"><?= (int)$z['n'] ?></div>
        <div class="k-neben">Durchfahrten</div></div>
    <?php endforeach; ?>
  </div>
</section>

<section>
  <h2>Welche Lok wie oft</h2>
  <div class="box">
    <?php foreach ($statistik['loks'] as $z): ?>
      <div class="balkenzeile">
        <div class="bz-name"><?= lokLink((string)$z['lok'], $lokNamen) ?>
          <?= lokEinsatz((string)$z['lok'], $lokQuellen) ?></div>
        <div class="bz-balken"><span style="width:<?= max(4, round(100 * (int)$z['fahrten'] / $maxLok)) ?>%"></span></div>
        <div class="bz-wert"><?= (int)$z['fahrten'] ?>×
          <span class="dim">an <?= (int)$z['tage'] ?> <?= (int)$z['tage'] === 1 ? 'Tag' : 'Tagen' ?></span></div>
      </div>
    <?php endforeach; ?>
    <?php if (!$statistik['loks']): ?><div class="leer">Noch keine Durchfahrt erfasst.</div><?php endif; ?>
  </div>
</section>

<section>
  <h2>Wochentage</h2>
  <div class="box">
    <?php foreach ($wtNamen as $nummer => $name): $z = $wt[$nummer] ?? null; ?>
      <div class="balkenzeile">
        <div class="bz-name"><?= $name ?></div>
        <div class="bz-balken"><span style="width:<?= $z ? max(4, round(100 * (int)$z['tage'] / $maxWt)) : 0 ?>%"></span></div>
        <div class="bz-wert"><?= $z ? (int)$z['tage'] . ((int)$z['tage'] === 1 ? ' Tag' : ' Tage') : '—' ?>
          <?php if ($z): ?><span class="dim"><?= (int)$z['fahrten'] ?> Fahrten</span><?php endif; ?></div>
      </div>
    <?php endforeach; ?>
  </div>
</section>

<section>
  <h2>Zu welcher Uhrzeit</h2>
  <div class="box">
    <?php foreach ($statistik['stunden'] as $z): ?>
      <div class="balkenzeile">
        <div class="bz-name"><?= h((string)$z['stunde']) ?> Uhr</div>
        <div class="bz-balken"><span style="width:<?= max(4, round(100 * (int)$z['n'] / $maxStunde)) ?>%"></span></div>
        <div class="bz-wert"><?= (int)$z['n'] ?>×</div>
      </div>
    <?php endforeach; ?>
    <?php if (!$statistik['stunden']): ?><div class="leer">Noch keine Zeiten erfasst.</div><?php endif; ?>
  </div>
</section>

<section>
  <h2>Gleis und Herkunft</h2>
  <div class="box">
    <?php foreach ($statistik['gleise'] as $z): ?>
      <div class="einstellung"><div class="e-text"><div class="e-titel">Gleis <?= h((string)$z['gleis']) ?></div>
        <div class="e-neben">Durchfahrten in Elmshorn</div></div><b><?= (int)$z['n'] ?></b></div>
    <?php endforeach; ?>
    <?php foreach ($statistik['quelle'] as $z): ?>
      <div class="einstellung"><div class="e-text">
        <div class="e-titel"><?= h(QUELLE_LABEL[$z['quelle']] ?? (string)$z['quelle']) ?></div>
        <div class="e-neben">erfasste Umläufe insgesamt</div></div><b><?= (int)$z['n'] ?></b></div>
    <?php endforeach; ?>
  </div>
  <p class="leer">Gezählt wird, was in den Tageslisten stand — nicht, was tatsächlich gefahren ist.</p>
</section>

<?php elseif ($seite === 'archiv'):
    $grundText = ['angekommen' => 'angekommen', 'entfernt' => 'nicht mehr beobachtet', 'abgelaufen' => 'abgelaufen'];
    $jeTag = [];
    foreach ($archiv as $a) { $jeTag[(string)$a['tag']][] = $a; } ?>

<section>
  <h2>Archiv beobachteter Fahrten</h2>
  <?php if (!$archiv): ?>
    <div class="box leer">Noch nichts im Archiv. Beobachtete Fahrten landen hier eine Stunde nach der
      Ankunft am Endbahnhof — oder sobald du sie nicht mehr beobachtest.</div>
  <?php endif; ?>
  <?php foreach ($jeTag as $tagA => $fahrtenA): ?>
    <div class="box archiv-tag">
      <div class="archiv-datum"><?= h(datumKurz($tagA, $WOCHENTAGE)) ?></div>
      <?php foreach ($fahrtenA as $a):
          $grund = (string)($a['archiv_grund'] ?? 'angekommen');
          $stand = (string)($a['stand'] ?? '');
          $spaetA = preg_match('/"verspaetung":\s*(-?\d+)/', $stand, $m) ? (int)$m[1] : 0;
          $ausfallA = (bool)preg_match('/"ausfall":\s*true/', $stand); ?>
        <a class="archiv-zeile" href="<?= h('./?s=fahrt&tag=' . rawurlencode($tagA) . '&lok=' . rawurlencode((string)$a['lok']) . '&zug=' . rawurlencode((string)$a['zug'])) ?>">
          <span class="archiv-lok"><span class="stark"><?= h((string)$a['lok']) ?></span>
            <?php if (isset($lokNamen[$a['lok']])): ?><span class="dim">„<?= h($lokNamen[$a['lok']]) ?>“</span><?php endif; ?></span>
          <span class="archiv-fahrt"><span class="stark"><?= h((string)$a['zug']) ?></span>
            <?= h((string)($a['von_halt'] ?? '')) ?> <?= h((string)($a['von_zeit'] ?? '')) ?> →
            <?= h((string)($a['nach_halt'] ?? '')) ?> <?= h((string)($a['nach_zeit'] ?? '')) ?>
            <?php if ($a['elmshorn_zeit']): ?><span class="dim">· Elmshorn <?= h((string)$a['elmshorn_zeit']) ?></span><?php endif; ?></span>
          <span class="archiv-ende">
            <?php if ($ausfallA): ?><span class="etikett warn">ausgefallen</span>
            <?php elseif ($spaetA > 0): ?><span class="etikett<?= $spaetA >= 5 ? ' warn' : '' ?>">+<?= $spaetA ?> min</span>
            <?php elseif ($grund === 'angekommen'): ?><span class="etikett gut">pünktlich</span><?php endif; ?>
            <span class="klein"><?= h($grundText[$grund] ?? $grund) ?><?php if ($a['archiviert_am']): ?> · <?= h(date('d.m. H:i', strtotime((string)$a['archiviert_am']))) ?><?php endif; ?></span>
          </span>
        </a>
      <?php endforeach; ?>
    </div>
  <?php endforeach; ?>
</section>

<?php elseif ($seite === 'karte'): ?>

<section>
  <h2>Karte</h2>
  <div class="kartenkopf">
    <button type="button" class="filter-knopf" id="filter-auf" aria-expanded="false" aria-controls="filterblatt">
      <span aria-hidden="true">☰</span> Filter</button>
    <button type="button" class="filter-stand" id="filter-stand" aria-controls="filterblatt"></button>
  </div>
  <div class="filterblatt" id="filterblatt" hidden>
    <div class="fb-gruppe"><h3>Kartentyp</h3><div class="fb-liste" id="fb-karte"></div></div>
    <div class="fb-gruppe"><h3>Anzeigen</h3><div class="fb-liste" id="fb-ebenen"></div></div>
    <div class="fb-gruppe"><h3>Gebiete <span class="fb-hinweis">je Land eine Abfrage je Minute</span></h3>
      <div class="fb-liste" id="fb-gebiete"></div></div>
    <div class="fb-gruppe"><h3>Zugarten</h3><div class="fb-liste" id="fb-arten"></div></div>
    <div class="fb-gruppe"><h3>Gleise von OpenRailwayMap</h3><div class="fb-liste" id="fb-bahn"></div></div>
  </div>
  <div class="box kartenbox">
    <div id="karte" class="karte" role="img" aria-label="Karte der Marschbahn mit den 218ern"></div>
    <div class="karte-offline" id="karte-offline" hidden>Die Karte braucht eine Verbindung —
      Kartenkacheln und Kartenprogramm kommen aus dem Netz.</div>
    <div class="karte-hinweis" id="karte-hinweis" hidden></div>
  </div>
  <div class="kartenlegende">
    <span><i class="kl-zug z218"></i>Zug mit 218</span>
    <span><i class="kl-zug re"></i>RE</span>
    <span><i class="kl-zug rb"></i>RB</span>
    <span><i class="kl-zug ice"></i>ICE</span>
    <span><i class="kl-zug ic"></i>IC · EC</span>
    <span><i class="kl-zug flx"></i>FLX</span>
    <span><i class="kl-zug spaet"></i>goldener Rand: 5+ min verspätet</span>
    <span><i class="kl-punkt lok"></i>218 laut Plan</span>
    <span><i class="kl-punkt steht"></i>218 wartet laut Plan</span>
    <span><i class="kl-linie"></i>RE7 · RE70 · RB61 · RB71</span>
    <span><i class="kl-punkt abgestellt"></i>abgestellt</span>
    <span><i class="kl-punkt umkreis"></i>Beitrag im Umkreis</span>
    <span><i class="kl-netz"></i>Streckennetz</span>
    <span><i class="kl-kreis"></i>50 km um Elmshorn</span>
  </div>
  <p class="leer">Die Züge in Schleswig-Holstein und Hamburg fahren nach Fahrplan und Echtzeit
    (transitous), jede Minute neu geholt — keine GPS-Ortung. Antippen zeigt Start und Ziel.
Über der Karte lassen sich weitere
    Gebiete dazuschalten (Niedersachsen, Meck-Pomm, Berlin, Dänemark) sowie Regio- und
    Fernverkehr getrennt ein- und ausblenden. <b>Tipp:</b> Tippe irgendwo auf ein Gleis — dann siehst du, welche Züge dort in den nächsten
    90 Minuten vorbeikommen, wann und woher. Rot sind Züge, die laut Forum heute eine 218 fährt. Ist so ein
    Zug nicht in den Daten (etwa der SyltShuttle), steht die 218 geschätzt aus dem Plan da. <?= count($karte['loks'] ?? []) ?> 218er heute im Plan,
    <?= count($karte['standorte'] ?? []) ?> Abstellorte, <?= count($karte['umkreis'] ?? []) ?> Beiträge
    der letzten sieben Tage. Züge und Fahrpläne über
    <a href="https://transitous.org" target="_blank" rel="noopener">transitous</a>
    (<a href="https://transitous.org/sources/" target="_blank" rel="noopener">Datenquellen</a>),
    Landesgrenzen von GeoBasis-DE / BKG (dl-de/by-2-0). Kartentyp und Bahn-Ebenen (Gleisplan, Höchstgeschwindigkeit, Signale)
    oben rechts wählen. Karte: © OpenStreetMap-Mitwirkende.</p>
</section>

<?php elseif ($seite === 'quellen'):
    // Name => [Klartext, Takt laut Cron, Zeitpunkt der letzten Arbeit, Auftragsname]
    $jetzt = time();
    $alter = static function (?string $zeit) use ($jetzt): ?int {
        return $zeit ? (int)round(($jetzt - strtotime($zeit)) / 60) : null;
    };
    $quellen = [
        ['Forum: Tageslisten', 'Drehscheibe-Online, Brett 006', 'stündlich (:17)',
         $quellenStand['forum'] ?? null, 'marschbahn', 90],
        ['Echtzeit Elmshorn', 'DB Timetables, sonst transitous', 'alle 10 Minuten',
         $quellenStand['lage']['stand'] ?? null, 'stoerung', 45],
        ['Umkreis', 'Drehscheibe-Online, Bretter 004 · 109 · 006', 'alle 10 Minuten',
         $quellenStand['sichtung'] ?? null, 'sichtungen', 45],
        ['Sonderzüge', 'DB Timetables, 30 Stunden voraus', 'alle 3 Stunden',
         $quellenStand['sonderzug'] ?? null, 'sonderzuege', 260],
        ['Vorgemerkte Fahrten', 'Forum und Echtzeit', 'alle 5 Minuten',
         $laeufeMeta['lauf_beobachten'] ?? null, 'beobachten', 30],
        ['Erinnerungen', 'Echtzeit und Wagenreihung', 'alle 5 Minuten',
         $laeufeMeta['lauf_erinnerung'] ?? null, 'erinnerung', 30],
        ['Züge auf der Karte', 'transitous — nur wenn jemand hinschaut', 'höchstens alle 3 Minuten',
         $laeufeMeta['lauf_zuege'] ?? null, 'zuege', 10],
        ['Züge an der Strecke', 'transitous', 'beim Antippen der Karte',
         $laeufeMeta['lauf_punkt'] ?? null, null, null],
        ['Wagenreihung', 'dbf.finalrewind.org', 'nur auf Knopfdruck',
         $quellenStand['wagenreihung'] ?? null, null, null],
        ['Besondere 218er', 'Wikipedia', 'sonntags 3:40',
         $quellenStand['besondere'] ?? null, 'besondere_loks', 60 * 24 * 8],
        ['Selbstüberwachung', 'eigene Prüfungen', 'stündlich (:50)',
         $laeufeMeta['lauf_waechter'] ?? null, 'wacht', 180],
        ['Push-Zustellung', 'Google, Apple, Mozilla', 'wenn etwas zu melden ist',
         $quellenStand['push'] ?? null, null, null],
    ];
    $offeneAuftraege = array_filter($auftraege, static fn(array $a): bool => $a['fertig_am'] === null); ?>

<?php
    // Wie viel wurde heute von transitous geholt? (zuege.py zählt mit)
    $transBytes = (int)($pdo->query('SELECT wert FROM meta WHERE schluessel = "transitous_bytes_'
                                    . date('Y-m-d') . '"')->fetchColumn() ?: 0);
?>
<section>
  <h2>Quellen &amp; Abfragen</h2>
  <div class="box leer">Von <a href="https://transitous.org" target="_blank" rel="noopener">transitous</a>
    heute geholt: <b><?= number_format($transBytes / 1048576, 1, ',', '.') ?> MB</b>.
    Die Karte fragt nur an, solange jemand sie geöffnet hat, höchstens alle drei Minuten,
    komprimiert und nur für die eingeschalteten Gebiete.</div>
  <?php if ($offeneAuftraege): ?>
    <div class="box" style="margin-bottom:8px"><div class="leer">
      <?= count($offeneAuftraege) ?> Auftrag<?= count($offeneAuftraege) === 1 ? '' : 'e' ?> läuft gerade —
      die Seite aktualisiert sich gleich von selbst.</div></div>
  <?php endif; ?>
  <div class="box">
    <?php foreach ($quellen as [$name, $woher, $takt, $zeit, $auftrag, $grenze]):
        $min = $alter($zeit ? (string)$zeit : null);
        $alt = $grenze !== null && $min !== null && $min > $grenze; ?>
      <div class="quellzeile">
        <div class="qz-text">
          <div class="qz-name"><?= h($name) ?>
            <?php if ($alt): ?><span class="etikett warn">überfällig</span><?php endif; ?></div>
          <div class="qz-neben"><?= h($woher) ?> · <?= h($takt) ?></div>
        </div>
        <div class="qz-zeit"><?= $min === null ? '<span class="dim">noch nie</span>'
            : ($min < 1 ? 'gerade eben' : ($min < 60 ? 'vor ' . $min . ' min'
            : ($min < 2880 ? 'vor ' . intdiv($min, 60) . ' h' : 'vor ' . intdiv($min, 1440) . ' Tagen')))
          ?><span class="klein"><?= $zeit ? h(date('d.m. H:i', strtotime((string)$zeit))) : '' ?></span></div>
        <div class="qz-knopf"><?php if ($auftrag): ?>
          <form method="post" class="inline">
            <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
            <input type="hidden" name="aktion" value="pruefen">
            <input type="hidden" name="skript" value="<?= h($auftrag) ?>">
            <button type="submit" class="knopf-klein">Jetzt prüfen</button>
          </form>
        <?php endif; ?></div>
      </div>
    <?php endforeach; ?>
  </div>
  <p class="leer">„Jetzt prüfen“ stellt einen Auftrag ein; der Hintergrunddienst führt ihn
    innerhalb weniger Sekunden aus. Die Webseite selbst fragt keine Schnittstelle ab.</p>
</section>

<?php if ($auftraege): ?>
<section>
  <h2>Zuletzt angestoßen</h2>
  <div class="box">
    <?php foreach ($auftraege as $a): ?>
      <div class="quellzeile">
        <div class="qz-text">
          <div class="qz-name"><?= h((string)$a['skript']) ?>
            <?php if ($a['fertig_am'] === null): ?>
              <span class="etikett">läuft …</span>
            <?php elseif ((int)$a['erfolg']): ?><span class="etikett gut">fertig</span>
            <?php else: ?><span class="etikett warn">Fehler</span><?php endif; ?></div>
          <div class="qz-neben"><?= h(date('d.m. H:i:s', strtotime((string)$a['angefordert_am']))) ?>
            <?= $a['ausgabe'] ? '· ' . h(mb_substr(strtok((string)$a['ausgabe'], "\n") ?: '', 0, 120)) : '' ?></div>
        </div>
        <div class="qz-zeit"></div>
        <div class="qz-knopf"><?php if ($a['ausgabe']): ?>
          <details class="auftrag-details"><summary>Ausgabe</summary>
            <pre><?= h((string)$a['ausgabe']) ?></pre></details>
        <?php endif; ?></div>
      </div>
    <?php endforeach; ?>
  </div>
</section>
<?php endif; ?>

<?php elseif ($seite === 'logs'):
    // Auszüge, die auftrag.py hierher spiegelt — /opt/docker ist für den Webserver gesperrt.
    $logVerzeichnis = __DIR__ . '/protokolle';
    $dateien = [];
    foreach (glob($logVerzeichnis . '/*.log') ?: [] as $pfad) {
        $dateien[basename($pfad)] = ['groesse' => filesize($pfad), 'stand' => filemtime($pfad)];
    }
    ksort($dateien);
    $wahl = (string)($_GET['datei'] ?? '');
    if (!isset($dateien[$wahl])) { $wahl = array_key_first($dateien) ?? ''; }
    $zeilenzahl = min(500, max(20, (int)($_GET['zeilen'] ?? 120)));
    $inhalt = '';
    if ($wahl !== '') {
        // Nur das Ende lesen, die Dateien werden groß.
        $pfad = $logVerzeichnis . '/' . $wahl;
        $griff = @fopen($pfad, 'rb');
        if ($griff) {
            $puffer = '';
            $bloecke = 0;
            $ende = filesize($pfad);
            while ($ende > 0 && substr_count($puffer, "\n") <= $zeilenzahl && $bloecke < 200) {
                $schritt = min(8192, $ende);
                $ende -= $schritt;
                fseek($griff, $ende);
                $puffer = fread($griff, $schritt) . $puffer;
                $bloecke++;
            }
            fclose($griff);
            $zeilen = array_slice(explode("\n", rtrim($puffer, "\n")), -$zeilenzahl);
            $inhalt = implode("\n", $zeilen);
        } else {
            $inhalt = 'Datei nicht lesbar.';
        }
    } ?>

<section>
  <h2>Logs</h2>
  <div class="filter">
    <?php foreach ($dateien as $name => $info): ?>
      <a class="f-chip<?= $name === $wahl ? ' aktiv' : '' ?>"
         href="./?s=logs&amp;datei=<?= urlencode($name) ?>"><?= h(str_replace('.log', '', $name)) ?>
        <span class="dim"><?= $info['groesse'] > 1048576
          ? round($info['groesse'] / 1048576, 1) . ' MB' : round($info['groesse'] / 1024) . ' kB' ?></span></a>
    <?php endforeach; ?>
  </div>
  <?php if ($wahl === ''): ?>
    <div class="box"><div class="leer">Keine Protokolle gefunden.</div></div>
  <?php else: ?>
    <div class="box">
      <div class="boxkopf">
        <span class="titel"><?= h($wahl) ?></span>
        <span class="neben">Stand <?= h(date('d.m. H:i', $dateien[$wahl]['stand'])) ?> ·
          letzte <?= $zeilenzahl ?> Zeilen</span>
      </div>
      <pre class="logtext"><?= h($inhalt) ?></pre>
    </div>
    <div class="filter" style="margin-top:10px">
      <?php foreach ([60, 120, 300, 500] as $n): ?>
        <a class="f-chip<?= $n === $zeilenzahl ? ' aktiv' : '' ?>"
           href="./?s=logs&amp;datei=<?= urlencode($wahl) ?>&amp;zeilen=<?= $n ?>"><?= $n ?> Zeilen</a>
      <?php endforeach; ?>
    </div>
  <?php endif; ?>
  <p class="leer">Gezeigt wird ein Auszug (letzte 400 Zeilen), den der Hintergrunddienst jede
    Minute aus <code>/opt/docker/kukas-zug/logs</code> hierher spiegelt — dorthin kommt der
    Webserver nicht. Vollständig stehen die Protokolle auf dem Server, sie werden ab 5 MB
    gedreht. Zugangsdaten stehen nicht darin.</p>
</section>

<?php elseif ($seite === 'verlauf'):
    $ART_LABEL = [
        'treffer' => '218 durch Elmshorn', 'tagesmeldung_treffer' => 'Tagesliste mit 218',
        'tagesmeldung_leer' => 'Tagesliste ohne 218', 'streichung' => 'Streichung',
        'ausfall_218' => 'Ausfall einer 218', 'nachtrag' => 'Nachtrag',
        'stoerung' => 'Störung', 'entwarnung' => 'Entwarnung', 'sichtung' => 'Umkreis',
        'sonderzug' => 'Sonderzug', 'beobachtung' => 'Beobachtete Fahrt', 'test' => 'Testnachricht',
        'erinnerung' => 'Erinnerung vor der Durchfahrt', 'wacht' => 'Selbstüberwachung',
    ];
    $wahl = (string)($_GET['art'] ?? '');
    $suche = trim((string)($_GET['q'] ?? ''));
    $filter = ['' => 'Alles', '218' => '218', 'lage' => 'Störungen', 'umkreis' => 'Umkreis', 'test' => 'Tests']; ?>

<section>
  <h2>Benachrichtigungsverlauf</h2>
  <form method="get" class="suchzeile">
    <input type="hidden" name="s" value="verlauf">
    <?php if ($wahl): ?><input type="hidden" name="art" value="<?= h($wahl) ?>"><?php endif; ?>
    <input type="search" name="q" value="<?= h($suche) ?>" placeholder="Suchen: Lok, Zug, Ort …"
           aria-label="Im Verlauf suchen">
    <button type="submit" class="knopf-klein haupt">Suchen</button>
    <?php if ($suche !== ''): ?>
      <a class="knopf-klein" href="./?s=verlauf<?= $wahl ? '&amp;art=' . h($wahl) : '' ?>">×</a>
    <?php endif; ?>
  </form>
  <div class="schalter" style="margin-bottom:12px">
    <?php foreach ($filter as $schluessel => $text): ?>
      <a href="./?s=verlauf<?= $schluessel ? '&amp;art=' . $schluessel : '' ?><?= $suche !== '' ? '&amp;q=' . urlencode($suche) : '' ?>"
         class="<?= $wahl === $schluessel ? 'aktiv' : '' ?>"><?= h($text) ?></a>
    <?php endforeach; ?>
  </div>

  <?php if (!$verlauf): ?>
    <div class="box"><div class="leer">Nichts gefunden<?= $suche !== '' ? ' für „' . h($suche) . '“' : ($wahl ? ' in dieser Auswahl' : '') ?>.
      Aufgezeichnet wird seit dem 17.09.2026; ältere Mails stehen nur im Protokoll auf dem Server.</div></div>
  <?php else: ?>
    <?php $letzterTag = null; foreach ($verlauf as $m):
        $tag = date('Y-m-d', strtotime((string)$m['erstellt_am']));
        if ($tag !== $letzterTag): $letzterTag = $tag; ?>
          <div class="verlauf-tag"><?= $tag === date('Y-m-d') ? 'Heute' : h(datumKurz($tag, $WOCHENTAGE)) ?></div>
        <?php endif;
        $push = $m['push_gesamt'] !== null;
        $pushOk = (int)$m['push_ok'];
        $wartet = $m['warteschlange_id'] !== null && $m['verschickt_am'] === null; ?>
      <div class="box verlauf-zeile">
        <div class="v-kopf">
          <span class="v-zeit"><?= h(date('H:i', strtotime((string)$m['erstellt_am']))) ?></span>
          <span class="etikett"><?= h($ART_LABEL[$m['art']] ?? (string)$m['art']) ?></span>
          <span class="v-wege">
            <?php if ((int)$m['mails']): ?>
              <span class="etikett gut" title="<?= (int)$m['mails'] ?> Mail(s) verschickt">✉ Mail</span>
            <?php endif; ?>
            <?php if ($wartet): ?>
              <span class="etikett">🔔 wird zugestellt …</span>
            <?php elseif ($push): ?>
              <span class="etikett <?= $pushOk ? 'gut' : 'warn' ?>"
                    title="<?= $pushOk ?> von <?= (int)$m['push_gesamt'] ?> Geräten">
                🔔 <?= $pushOk ?: 'kein Gerät' ?><?= $pushOk ? '/' . (int)$m['push_gesamt'] : '' ?></span>
            <?php endif; ?>
          </span>
        </div>
        <div class="v-betreff"><?= h((string)$m['betreff']) ?></div>
        <?php if ($m['text']): ?><div class="v-text"><?= h((string)$m['text']) ?></div><?php endif; ?>
        <?php if ($m['ergebnis']): ?><div class="v-text dim"><?= h((string)$m['ergebnis']) ?></div><?php endif; ?>
        <?php if ($m['url']): ?>
          <a class="mehr-link" href="<?= h((string)$m['url']) ?>" target="_blank" rel="noopener">Dazu →</a>
        <?php endif; ?>
      </div>
    <?php endforeach; ?>
    <p class="leer"><?= count($verlauf) ?> von <?= $verlaufZahl ?> Meldungen · älter als ein Jahr wird gelöscht.
      Wie es einzelnen Geräten erging, steht auf der <a href="geraete.php">Geräteseite</a>.</p>
  <?php endif; ?>
</section>

<?php endif; ?>
<?php
$lageSkript = <<<'JS'
<script>
// Bahnhofswahl: ein Feld, beim Antippen die üblichen Bahnhöfe, beim Tippen Vorschläge
// aus der ganzen Liste (vorschlag.php, eigene Tabelle — beim Tippen geht nichts nach außen).
(function () {
  const feld = document.getElementById('bf-feld');
  const liste = document.getElementById('bf-liste');
  const form = document.getElementById('bf-form');
  if (!feld) { return; }
  const schnell = JSON.parse(feld.dataset.schnell || '[]');
  const anfang = feld.value;
  let eintraege = [], markiert = -1, zeitgeber = null, frage = 0;

  const esc = (t) => t.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function zeige(namen, kopf) {
    eintraege = namen;
    markiert = -1;
    if (!namen.length) { liste.hidden = true; return; }
    liste.innerHTML = (kopf ? '<div class="bw-kopf">' + esc(kopf) + '</div>' : '')
      + namen.map((n, i) => '<div class="bw-eintrag" role="option" data-i="' + i + '">' + esc(n) + '</div>').join('');
    liste.hidden = false;
  }
  function waehle(name) {
    feld.value = name;
    liste.hidden = true;
    form.requestSubmit ? form.requestSubmit() : form.submit();
  }
  function markiere(i) {
    const alle = liste.querySelectorAll('.bw-eintrag');
    alle.forEach((el, j) => el.classList.toggle('aktiv', j === i));
    markiert = i;
  }

  const knoepfe = ['Elmshorn', 'Hamburg-Altona', 'Itzehoe'];
  const vorschlagsliste = schnell.filter((n) => !knoepfe.includes(n));
  feld.addEventListener('focus', () => {
    feld.select();
    if (feld.value.trim().length < 2) { zeige(vorschlagsliste, 'Weitere Bahnhöfe'); }
  });
  feld.addEventListener('input', () => {
    const q = feld.value.trim();
    clearTimeout(zeitgeber);
    if (q.length < 2) { zeige(vorschlagsliste, 'Weitere Bahnhöfe'); return; }
    zeitgeber = setTimeout(async () => {
      const nummer = ++frage;
      try {
        const r = await fetch('vorschlag.php?q=' + encodeURIComponent(q), { credentials: 'same-origin' });
        const namen = await r.json();
        if (nummer !== frage) { return; }          // eine neuere Eingabe ist schon unterwegs
        if (namen.length) {
          zeige(namen, '');
        } else {
          eintraege = [];
          liste.innerHTML = '<div class="bw-kopf">Kein Bahnhof gefunden</div>';
          liste.hidden = false;
        }
      } catch (e) { /* offline: dann eben ohne Vorschläge */ }
    }, 150);
  });
  feld.addEventListener('keydown', (e) => {
    if (liste.hidden || !eintraege.length) { return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); markiere(Math.min(markiert + 1, eintraege.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); markiere(Math.max(markiert - 1, 0)); }
    else if (e.key === 'Enter' && markiert >= 0) { e.preventDefault(); waehle(eintraege[markiert]); }
    else if (e.key === 'Escape') { liste.hidden = true; feld.value = anfang; feld.blur(); }
  });
  // mousedown statt click: sonst schließt der blur die Liste, bevor der Klick ankommt
  liste.addEventListener('mousedown', (e) => {
    const el = e.target.closest('.bw-eintrag');
    if (el) { e.preventDefault(); waehle(eintraege[+el.dataset.i]); }
  });
  feld.addEventListener('blur', () => setTimeout(() => {
    liste.hidden = true;
    if (!feld.value.trim()) { feld.value = anfang; }
  }, 120));
})();

// Filter auf der Lage-Seite: nur ein- und ausblenden, ohne neu zu laden.
(function () {
  const leiste = document.getElementById('lage-filter');
  if (!leiste) { return; }
  const zeilen = Array.from(document.querySelectorAll('.lagezeile'));
  const zahl = document.getElementById('lage-zahl');
  const leer = document.getElementById('lage-leer');
  leiste.addEventListener('click', (e) => {
    const chip = e.target.closest('.f-chip');
    if (!chip) { return; }
    leiste.querySelectorAll('.f-chip').forEach((c) => c.classList.toggle('aktiv', c === chip));
    const wahl = chip.dataset.filter;
    let sichtbar = 0;
    zeilen.forEach((z) => {
      const passt = wahl === 'alle' ? true : z.dataset[wahl] === '1';
      z.hidden = !passt;
      sichtbar += passt ? 1 : 0;
    });
    leer.hidden = sichtbar > 0;
    zahl.textContent = wahl === 'alle'
      ? sichtbar + ' Fahrten im Zeitfenster'
      : sichtbar + ' von ' + zeilen.length + ' Fahrten';
  });
})();
</script>
JS;

$tageSkript = <<<'JS'
<script>
// Tageslisten filtern: nach Herkunft (RE6 oder SyltShuttle) und per Lok-Suche (Nummer
// oder Name, z. B. "330" oder "Konrad"). Nur ein- und ausblenden, alles bleibt auf der Seite.
(function () {
  const leiste = document.getElementById('tage-filter');
  if (!leiste) { return; }
  const feld = document.getElementById('tage-suche');
  const info = document.getElementById('tage-suche-info');
  const zeilen = Array.from(document.querySelectorAll('.tzeile[data-quelle]'));
  let wahl = 'alle';

  // Vorschläge: jede Lok einmal, mit Namen
  const loks = new Map();
  zeilen.forEach((z) => { if (z.dataset.suchLok) { loks.set(z.dataset.suchLok, z.dataset.name || ''); } });
  document.getElementById('tage-loks').innerHTML = [...loks.entries()].sort()
    .map(([l, n]) => '<option value="' + l + '">' + (n ? n : '') + '</option>').join('');

  const anwenden = () => {
    const q = (feld.value || '').trim().toLowerCase();
    const qZiffern = q.replace(/\D/g, '');
    const passt = (z) => !q || z.dataset.such.includes(q) || (qZiffern.length >= 3 && z.dataset.such.includes(qZiffern));
    let treffer = 0;
    zeilen.forEach((z) => {
      const ok = (wahl === 'alle' || z.dataset.quelle === wahl) && passt(z);
      z.hidden = !ok;
      if (ok && q) { treffer++; }
    });
    const gefiltert = wahl !== 'alle' || q;
    let tageMit = 0;
    // Tage ohne passende Fahrt ganz ausblenden, sonst bleiben leere Karten stehen.
    document.querySelectorAll('.tagkarte').forEach((karte) => {
      const alle = karte.querySelectorAll('.tzeile[data-quelle]');
      const sichtbar = karte.querySelectorAll('.tzeile[data-quelle]:not([hidden])').length;
      // Bei einer Suche auch Tage ohne Liste ausblenden — dann zählt nur, wo die Lok fährt.
      karte.hidden = q ? sichtbar === 0 : (gefiltert && alle.length > 0 && sichtbar === 0);
      if (sichtbar) { tageMit++; }
      // Steckt das Passende im eingeklappten Teil, klappt der sich auf.
      const weitere = karte.querySelector('.weitere');
      if (weitere && gefiltert) {
        weitere.open = weitere.querySelectorAll('.tzeile:not([hidden])').length > 0;
      }
      if (q && sichtbar) { karte.open = true; }
    });
    info.hidden = !q;
    if (q) {
      info.textContent = treffer ? treffer + (treffer === 1 ? ' Fahrt' : ' Fahrten') + ' an ' + tageMit
        + (tageMit === 1 ? ' Tag' : ' Tagen') : 'Keine Fahrt mit „' + feld.value.trim() + '“ in diesen Tagen.';
    }
  };
  leiste.addEventListener('click', (e) => {
    const chip = e.target.closest('.f-chip');
    if (!chip) { return; }
    leiste.querySelectorAll('.f-chip').forEach((c) => c.classList.toggle('aktiv', c === chip));
    wahl = chip.dataset.quelle;
    anwenden();
  });
  feld.addEventListener('input', anwenden);
  if (feld.value) { anwenden(); }
})();
</script>
JS;

if ($seite === 'lage' && $bahnhofWartet) {
    // Der Hintergrunddienst braucht ein paar Sekunden — danach einmal neu laden.
    $lageSkript .= "<script>setTimeout(() => location.reload(), 4000);</script>";
}
$karteSkript = '';
if ($seite === 'karte') {
    $karteSkript = '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"'
        . ' integrity="sha384-c6Rcwz4e4CITMbu/NBmnNS8yN2sC3cUElMEMfP3vqqKFp7GOYaaBBCqmaWBjmkjb" crossorigin="anonymous">'
        . '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"'
        . ' integrity="sha384-NElt3Op+9NBMCYaef5HxeJmU4Xeard/Lku8ek6hoPTvYkQPh3zLIrJP7KiRocsxO" crossorigin="anonymous"></script>'
        . '<script>window.KARTE = ' . json_encode($karte, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP) . ';</script>'
        . <<<'JS'
<script>
(function () {
  const d = window.KARTE;
  const ziel = document.getElementById('karte');
  if (!ziel || !d) { return; }
  if (typeof L === 'undefined') {                  // offline oder Kartenprogramm nicht erreichbar
    ziel.hidden = true;
    document.getElementById('karte-offline').hidden = false;
    return;
  }
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const karte = L.map(ziel, { zoomControl: true, attributionControl: true });
  window.addEventListener('error', (e) => {
    // Fällt etwas aus, soll man es sehen statt einer leeren Karte.
    const hinweis = document.getElementById('karte-offline');
    if (hinweis) { hinweis.hidden = false; hinweis.textContent = 'Kartenfehler: ' + e.message; }
  });
  // Die Kachelserver von OpenStreetMap verlangen einen Referer und sperren Anfragen ohne
  // ("Access blocked … tile usage policy"). jarritc.de schickt aber seitenweit
  // "Referrer-Policy: no-referrer". Nur für die Kacheln darum die Herkunft mitsenden —
  // strict-origin: bloß "https://jarritc.de/", kein Pfad, keine Seitenadresse.
  // Kartentypen zur Auswahl (oben rechts). Alle bekommen dieselbe Herkunftsangabe.
  // Pflicht laut Nutzungsbedingungen: sichtbarer Hinweis auf die Quellen von transitous
  const OSM = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende'
    + ' · Züge: <a href="https://transitous.org/sources/">transitous</a>';
  const kachel = (url, extra) => L.tileLayer(url, Object.assign({ maxZoom: 18, referrerPolicy: 'strict-origin', attribution: OSM }, extra));
  const basis = {
    // Nur die Standardkarte wird im dunklen Design invertiert (Klasse kachel-invert)
    'Standard': kachel('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { className: 'kachel-invert' }),
    'Hell': kachel('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { subdomains: 'abcd', maxZoom: 19, attribution: OSM + ' · © <a href="https://carto.com/attributions">CARTO</a>' }),
    'Dunkel': kachel('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      { subdomains: 'abcd', maxZoom: 19, attribution: OSM + ' · © <a href="https://carto.com/attributions">CARTO</a>' }),
    'Topografisch': kachel('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
      { subdomains: 'abc', maxZoom: 17, attribution: OSM + ' · © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' }),
    'Satellit': kachel('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 18, attribution: 'Bilder © Esri, Maxar, Earthstar Geographics' }),
  };
  // Bahn-Ebenen von OpenRailwayMap, über jeden Kartentyp legbar
  const ORM = OSM + ' · Stil: <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> (CC-BY-SA)';
  const bahnEbenen = {
    'Gleisplan (OpenRailwayMap)': kachel('https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',
      { subdomains: 'abc', maxZoom: 19, attribution: ORM }),
    'Höchstgeschwindigkeit': kachel('https://{s}.tiles.openrailwaymap.org/maxspeed/{z}/{x}/{y}.png',
      { subdomains: 'abc', maxZoom: 19, attribution: ORM }),
    'Signale': kachel('https://{s}.tiles.openrailwaymap.org/signals/{z}/{x}/{y}.png',
      { subdomains: 'abc', maxZoom: 19, attribution: ORM }),
  };
  // Die Wahl merkt sich dieses Gerät (nur Bequemlichkeit — ohne Speicher gilt Standard)
  const merk = (k, v) => { try { if (v === undefined) { return localStorage.getItem(k); } localStorage.setItem(k, v); } catch (e) { } return null; };
  const gewaehlt = merk('zugradar_karte_typ');
  (basis[gewaehlt] || basis.Standard).addTo(karte);
  let bahnGewaehlt = [];
  try { bahnGewaehlt = JSON.parse(merk('zugradar_karte_bahn') || '[]'); } catch (e) { }
  Object.entries(bahnEbenen).forEach(([name, ebene]) => { if (bahnGewaehlt.includes(name)) { ebene.addTo(karte); } });
  karte.on('baselayerchange', (e) => merk('zugradar_karte_typ', e.name));
  const bahnMerken = () => merk('zugradar_karte_bahn',
    JSON.stringify(Object.keys(bahnEbenen).filter((n) => karte.hasLayer(bahnEbenen[n]))));
  karte.on('overlayadd overlayremove', bahnMerken);

  const elmshorn = (d.strecke.find((s) => s.name === 'Elmshorn') || {}).p || [53.754, 9.659];
  const ebenen = {};
  // Welche Gebiete und Zugarten sollen zu sehen sein? Gebiete holt der Dienst im Hintergrund
  // (regionen.php → zuege.py), Zugarten blendet nur die Karte aus.
  const merkGebiet = (k, v) => { try { if (v === undefined) { return localStorage.getItem(k); } localStorage.setItem(k, v); } catch (e) { } return null; };
  let gebieteAn = (d.gebiete_an || ['SH']).slice();
  let artenAus = [];
  try { artenAus = JSON.parse(merkGebiet('zugradar_karte_arten') || '[]'); } catch (e) { }

  // Erst den Ausschnitt setzen, dann Ebenen hinzufügen. Umgekehrt stolpert Leaflet beim
  // Zeichnen der Linie (Renderer ohne Grenzen, "reading 'min'"), bricht ab — und alles
  // danach fehlt: Kacheln da, Einträge nicht (18.09.2026).
  const alle = d.strecke.map((s) => s.p);
  karte.fitBounds(alle.length ? L.latLngBounds(alle).pad(0.08) : L.latLngBounds([elmshorn, elmshorn]).pad(1));

  // Die übrigen Linien durch Elmshorn (ohne AKN) — unter der Marschbahn, dünner.
  const LINIENFARBE = { RE7: '#1f7a4d', RE70: '#2aa198', RB60: '#d63384', RB61: '#6f42c1', RB71: '#c26a1b' };
  const linienEbene = L.layerGroup(Object.entries(d.linien || {}).map(([name, stuecke]) =>
    L.polyline(stuecke, { color: LINIENFARBE[name] || '#5b6674', weight: 3, opacity: .75 })
      .bindTooltip(esc(name), { sticky: true }))).addTo(karte);
  if (Object.keys(d.linien || {}).length) { ebenen['Linien durch Elmshorn'] = linienEbene; }

  // Streckennetz: alle Abschnitte, auf denen zuletzt ein Zug gefahren ist (netz.php, aus
  // den gemerkten Fahrten). Wird erst geholt, wenn die Ebene eingeschaltet wird — und neu,
  // sobald ein Gebiet dazukommt.
  const netzEbene = L.layerGroup();
  let netzGeholt = '';
  const netzLaden = () => {
    const wunsch = gebieteAn.join(',');
    if (!karte.hasLayer(netzEbene) || netzGeholt === wunsch) { return; }
    netzGeholt = wunsch;
    fetch('netz.php?r=' + encodeURIComponent(wunsch), { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((j) => {
        netzEbene.clearLayers();
        (j.netz || []).forEach((stueck) => L.polyline(stueck, { color: '#7a8796', weight: 2,
          opacity: .55, interactive: false }).addTo(netzEbene));
      })
      .catch(() => { netzGeholt = ''; });
  };
  ebenen['Streckennetz'] = netzEbene;

  // Alle Bahnhöfe (halte.php, aus den gefahrenen Zügen). Beim Hineinzoomen kommen immer
  // mehr dazu: erst die ICE-Halte, dann IC, dann RE, ganz nah auch die kleinen.
  const bfEbene = L.layerGroup();
  let bfMarken = [];
  let bfGeholt = '';
  const AB_ZOOM = { 3: 7, 2: 9, 1: 10, 0: 12 };      // Rang → ab diesem Zoom sichtbar
  const NAME_AB_ZOOM = { 3: 9, 2: 11, 1: 12, 0: 13 };
  // "Hannover Hauptbahnhof" heißt bei der Bahn "Hannover Hbf" — sonst findet die
  // Abfahrtstafel den Bahnhof nicht.
  const bfName = (n) => n.replace(/\s*Hauptbahnhof$/, ' Hbf').replace(/\s*(ZOB\/Bahnhof|Bahnhof)$/, '');
  const bfZeigen = () => {
    const z = karte.getZoom();
    bfMarken.forEach(({ h, m, mitName }) => {
      const sichtbarJetzt = z >= (AB_ZOOM[h.k] ?? 12);
      if (sichtbarJetzt && !bfEbene.hasLayer(m)) { bfEbene.addLayer(m); }
      if (!sichtbarJetzt && bfEbene.hasLayer(m)) { bfEbene.removeLayer(m); }
      const nameAn = z >= (NAME_AB_ZOOM[h.k] ?? 13);
      if (nameAn !== mitName.wert) {
        mitName.wert = nameAn;
        if (nameAn) {
          m.bindTooltip(esc(h.n), { permanent: true, direction: 'right', offset: [8, 0], className: 'bf-name' });
        } else {
          m.unbindTooltip();
        }
      }
    });
  };
  const bfLaden = () => {
    const wunsch = gebieteAn.join(',');
    if (!karte.hasLayer(bfEbene) || bfGeholt === wunsch) { return; }
    bfGeholt = wunsch;
    fetch('halte.php?r=' + encodeURIComponent(wunsch), { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((j) => {
        bfEbene.clearLayers();
        bfMarken = (j.halte || []).map((h) => ({
          h,
          mitName: { wert: false },
          m: L.marker(h.p, { icon: L.divIcon({ className: 'bf-marke r' + h.k, html: '<span></span>',
                                               iconSize: [22, 22], iconAnchor: [11, 11] }),
                             title: h.n, keyboard: false })
            .on('click', () => window.zeigeTafel && window.zeigeTafel(bfName(h.n))),
        }));
        bfZeigen();
      })
      .catch(() => { bfGeholt = ''; });
  };
  // Landesgrenzen (grenzen.php) — einmal geholt, dann im Speicher
  const grenzEbene = L.layerGroup();
  let grenzGeholt = false;
  const grenzLaden = () => {
    if (!karte.hasLayer(grenzEbene) || grenzGeholt) { return; }
    grenzGeholt = true;
    fetch('grenzen.php', { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((j) => {
        Object.entries(j.grenzen || {}).forEach(([land, linien]) => {
          // Die Außengrenze Deutschlands kräftig und durchgezogen, Bundesländer dünn gestrichelt
          const stil = land === 'Deutschland'
            ? { color: '#6b4b1f', weight: 2.5, opacity: .85 }
            : { color: '#8a6d3b', weight: 1.2, opacity: .6, dashArray: '6 4' };
          linien.forEach((stueck) => L.polyline(stueck, Object.assign({ interactive: false }, stil))
            .addTo(grenzEbene));
        });
      })
      .catch(() => { grenzGeholt = false; });
  };
  ebenen['Landesgrenzen'] = grenzEbene;

  ebenen['Bahnhöfe'] = bfEbene;
  karte.on('layeradd', (e) => {
    if (e.layer === netzEbene) { netzLaden(); }
    if (e.layer === bfEbene) { bfLaden(); }
    if (e.layer === grenzEbene) { grenzLaden(); }
  });
  karte.on('zoomend', bfZeigen);

  // Die Marschbahn mit ihren Halten
  // Gleisführung aus OpenStreetMap (RE6-Route); fehlt sie, die Halte gerade verbinden.
  const linie = L.polyline(d.gleise.length > 1 ? d.gleise : d.strecke.map((s) => s.p),
                           { color: '#8f2029', weight: 4, opacity: .8 });
  // Bahnhöfe: eigene Marken (HTML statt SVG-Kreis) — so liegen sie über Linie, Kreisen
  // und Abstellorten, haben eine fingergroße Tippfläche und verschwinden nicht als roter
  // Punkt auf der roten Linie. Antippen öffnet die Abfahrtstafel. Die wichtigen Bahnhöfe
  // sind dauerhaft beschriftet, die übrigen zeigen ihren Namen beim Antippen im Fenster.
  const beschriftet = ['Hamburg-Altona', 'Elmshorn', 'Itzehoe', 'Heide(Holst)', 'Husum',
                       'Niebüll', 'Westerland(Sylt)'];
  const halte = L.layerGroup(d.strecke.map((s) => {
    const haupt = s.name === 'Elmshorn';
    const marke = L.marker(s.p, {
      icon: L.divIcon({ className: 'halt-marke' + (haupt ? ' haupt' : ''), html: '<span></span>',
                        iconSize: [30, 30], iconAnchor: [15, 15] }),
      title: s.name, keyboard: true, zIndexOffset: haupt ? 500 : 0, riseOnHover: true,
    }).on('click', () => window.zeigeTafel && window.zeigeTafel(s.name));
    if (beschriftet.includes(s.name)) {
      marke.bindTooltip(esc(s.name.replace('(Sylt)', '').replace('(Holst)', '')),
        { permanent: true, direction: 'right', offset: [10, 0], className: 'halt-name' + (haupt ? ' haupt' : '') });
    }
    return marke;
  }));
  ebenen['Marschbahn'] = L.layerGroup([linie, halte]).addTo(karte);

  // Der Kreis ist nur Orientierung: nicht antippbar, damit er die Bahnhöfe darin nicht verdeckt.
  ebenen['50 km um Elmshorn'] = L.circle(elmshorn, { radius: 50000, color: '#0a66d0', weight: 1,
    dashArray: '5 6', fillOpacity: .03, interactive: false }).addTo(karte);

  // 218er laut Plan
  const planMarke = {};
  const loks = L.layerGroup(d.loks.map((l) => {
    const icon = L.divIcon({ className: 'lok-marke' + (l.unterwegs ? ' unterwegs' : ''),
      // Etwas über dem Punkt, damit eine wartende Lok den Bahnhof darunter nicht verdeckt
      html: '<span>' + esc(l.lok.slice(4)) + '</span>', iconSize: [58, 24], iconAnchor: [29, 34] });
    return (planMarke[l.lok] = L.marker(l.p, { icon, zIndexOffset: 1000 }).bindPopup(
      '<b>' + esc(l.lok) + (l.name ? ' „' + esc(l.name) + '“' : '') + '</b><br>' + esc(l.text)
      + '<br><small>laut Plan geschätzt</small>'
      + '<br><a href="#" data-lok="' + esc(l.lok) + '">Steckbrief</a>'));
  }));
  ebenen['218er laut Plan'] = loks.addTo(karte);

  // ---------- Fahrende Züge ----------
  // Abschnitte von Halt zu Halt (zuege.php, jede Minute neu). Die Position zur aktuellen
  // Sekunde entsteht hier: Anteil der Fahrzeit zwischen Abfahrt und Ankunft, entlang des
  // Verlaufs. Züge mit einer 218 (Zugnummer im heutigen Plan) sind rot und tragen die Lok.
  const nr218 = d.nr218 || {};
  const sichtbar = (a) => gebieteAn.includes(a.r || 'SH') && !artenAus.includes(art(a));
  const zugEbene = L.layerGroup().addTo(karte);
  ebenen['Züge live'] = zugEbene;
  const marken = new Map();          // Fahrt-Kennung -> { marke, zustand }
  let fahrten = new Map();           // Fahrt-Kennung -> Abschnitte nach Abfahrt sortiert

  const gruppiere = (liste) => {
    const m = new Map();
    for (const a of liste) {
      if (!m.has(a.t)) { m.set(a.t, []); }
      m.get(a.t).push(a);
    }
    for (const abschnitte of m.values()) { abschnitte.sort((x, y) => x.ab - y.ab); }
    return m;
  };
  // Punkt bei einem Anteil der Länge einer Linie (Längen einmal je Abschnitt gemerkt)
  const punktAuf = (a, anteil) => {
    const p = a.p;
    if (!a.l) {
      const k = Math.cos(p[0][0] * Math.PI / 180);
      a.l = [0];
      for (let i = 1; i < p.length; i++) {
        a.l.push(a.l[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], (p[i][1] - p[i - 1][1]) * k));
      }
    }
    const ziel = a.l[a.l.length - 1] * Math.max(0, Math.min(1, anteil));
    let i = 1;
    while (i < p.length - 1 && a.l[i] < ziel) { i++; }
    const stueck = a.l[i] - a.l[i - 1];
    const t = stueck > 0 ? (ziel - a.l[i - 1]) / stueck : 0;
    return [p[i - 1][0] + (p[i][0] - p[i - 1][0]) * t, p[i - 1][1] + (p[i][1] - p[i - 1][1]) * t];
  };
  // Wo ist der Zug jetzt? Fährt er, steht er am Halt — oder ist er (noch) nicht da?
  const lageVon = (abschnitte, jetzt) => {
    for (let i = 0; i < abschnitte.length; i++) {
      const a = abschnitte[i];
      if (jetzt >= a.ab && jetzt <= a.an) {
        return { p: punktAuf(a, (jetzt - a.ab) / Math.max(1, a.an - a.ab)), a, faehrt: true };
      }
      if (jetzt < a.ab) {
        // Zwischen zwei Abschnitten hält er; vor dem ersten nur kurz vor der Abfahrt zeigen.
        if (i > 0 || a.ab - jetzt <= 180) { return { p: a.p[0], a, faehrt: false, halt: a.von }; }
        return null;
      }
    }
    const letzter = abschnitte[abschnitte.length - 1];
    if (letzter && jetzt - letzter.an <= 60) {
      return { p: letzter.p[letzter.p.length - 1], a: letzter, faehrt: false, halt: letzter.nach };
    }
    return null;
  };
  const art = (a) => (/^(ICE|ICL|IC|EC|ECE|FLX|NJ|EN|RJ)/.test(a.linie) || /^FlixTrain/.test(a.name) ? 'fern' : 'regio');
  // Eigene Farbe je Zuggattung (siehe .zug-marke im Seitenrahmen)
  const farbgattung = (a) => {
    const l = a.linie || '';
    if (/^ICE/.test(l)) { return 'ice'; }
    if (/^(IC|EC|ECE|ICL)/.test(l)) { return 'ic'; }
    if (/^(FLX)/.test(l) || /^FlixTrain/.test(a.name || '')) { return 'flx'; }
    if (/^(NJ|EN|RJ)/.test(l)) { return 'nacht'; }
    if (/^RE/.test(l)) { return 're'; }
    if (/^RB/.test(l)) { return 'rb'; }
    return art(a) === 'fern' ? 'ic' : 'rb';
  };
  const ZUG_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M4 1.5h8a2 2 0 0 1 2 2v7a2 '
    + '2 0 0 1-1.6 2l1.3 2h-1.7l-1.2-1.8H5.2L4 15.5H2.3l1.3-2A2 2 0 0 1 2 10.5v-7a2 2 0 0 1 2-2Zm.3 2a.8.8 0 0 0-.8.8v2.4'
    + 'c0 .4.4.8.8.8h7.4c.4 0 .8-.4.8-.8V4.3a.8.8 0 0 0-.8-.8H4.3ZM5 9.2a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm6 0a1 1 0 1 0 0 2 1 '
    + '1 0 0 0 0-2Z"/></svg>';

  // Fahrzeuge (Lok, Dostos, Triebzug) und was vorne fährt — aus der Wagenreihung, nur für
  // angetippte Züge (dbf ist ein privater Dienst: nichts auf Vorrat). Abgefragt wird die
  // nächste Abfahrt des Zugs; je Fahrt einmal, das Ergebnis bleibt bis zum Neuladen.
  const fahrzeugInfo = new Map();    // Fahrt-Kennung -> {status, fz, bf, zeit, fehler}
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  // Ein Abruf je Zug und Abfahrt — Karte und Streckenfenster teilen sich das Ergebnis.
  // Nacheinander, nicht gleichzeitig: der Hintergrunddienst holt ohnehin einzeln mit Pause.
  const wagenAbrufe = new Map();
  let wagenKette = Promise.resolve();
  const holeWagen = (tt, nr, abPlan, bf) => {
    const schl = [tt, nr, abPlan, bf].join('|');
    if (!wagenAbrufe.has(schl)) {
      const abruf = wagenKette.then(() => fetch('wagenreihung.php?tt=' + encodeURIComponent(tt) + '&tn=' + encodeURIComponent(nr)
          + '&dt=' + abPlan + '&bf=' + encodeURIComponent(bf), { credentials: 'same-origin', cache: 'no-store' }))
        .then((r) => r.json())
        .then((j) => {
          const fz = j.status === 'fertig' && j.daten ? j.daten.fahrzeuge : null;
          return fz ? { status: 'fertig', fz } : { status: 'fehler', fehler: j.fehler || 'Keine Angaben zu den Fahrzeugen.' };
        })
        .catch(() => ({ status: 'fehler', fehler: 'Keine Verbindung.' }));
      wagenKette = abruf.then(() => {}, () => {});
      wagenAbrufe.set(schl, abruf);
    }
    return wagenAbrufe.get(schl);
  };
  const gattung = (linie) => ((linie || '').match(/^[A-Z]{1,4}/) || [''])[0];
  const ladeFahrzeuge = (t) => {
    if (fahrzeugInfo.has(t)) { return; }
    const abschnitte = fahrten.get(t) || [];
    const jetzt = Date.now() / 1000;
    const a = abschnitte.find((x) => x.ab >= jetzt) || abschnitte[abschnitte.length - 1];
    const tt = a && gattung(a.linie);
    if (!a || !a.nr || !tt) { fahrzeugInfo.set(t, { status: 'keine' }); return; }
    fahrzeugInfo.set(t, { status: 'laedt', bf: a.von, zeit: hhmm(a.ab_plan) });
    const fertig = (info) => {
      fahrzeugInfo.set(t, Object.assign({ bf: a.von, zeit: hhmm(a.ab_plan) }, info));
      const e = marken.get(t);
      if (e) { e.zustand = ''; }
      zeichneZuege();
    };
    holeWagen(tt, a.nr, a.ab_plan, a.von).then(fertig);
    const e = marken.get(t);
    if (e) { e.zustand = ''; }
    zeichneZuege();
  };
  // Immer genau zwei Zeilen gleicher Höhe — beim Laden, mit Ergebnis und ohne —, damit
  // das Fenster nicht springt, wenn die Fahrzeuge da sind.
  const fahrzeugZeile = (info) => {
    if (!info || info.status === 'keine') { return ''; }
    const quelle = 'ab ' + esc(info.bf) + ' ' + esc(info.zeit);
    let oben; let unten = quelle;
    if (info.status === 'laedt') {
      oben = '<span class="leise">Fahrzeuge werden geholt …</span>';
    } else if (info.status !== 'fertig') {
      oben = '<span class="leise">Keine Wagenreihung</span>';
      unten = esc(info.fehler);
    } else {
      oben = esc(info.fz.text);
      if (info.fz.vorne) { unten = 'vorne: ' + esc(info.fz.vorne) + ' · ' + quelle; }
    }
    return '<div class="zug-fz"><b>' + oben + '</b><small>' + unten + '</small></div>';
  };

  const zeichneZuege = () => {
    const jetzt = Date.now() / 1000;
    const gesehen = new Set();
    const live218 = new Set();
    for (const [t, abschnitte] of fahrten) {
      const lage = lageVon(abschnitte, jetzt);
      if (!lage || !sichtbar(lage.a)) { continue; }
      gesehen.add(t);
      const a = lage.a;
      const lok = a.nr ? nr218[a.nr] : null;
      if (lok) { live218.add(lok.lok); }
      const spaet = Math.round((a.ab - a.ab_plan) / 60);
      const info = fahrzeugInfo.get(t);
      const zustand = [lage.faehrt, a.von, a.nach, spaet, lok ? lok.lok : '', a.ziel || '',
                       info ? info.status : ''].join('|');
      let e = marken.get(t);
      if (!e) {
        e = { marke: L.marker(lage.p, { keyboard: false }).addTo(zugEbene), zustand: '' };
        // Fenster einmal mit fester Breite anlegen, danach nur den Inhalt tauschen — so
        // bleibt es beim Nachladen an Ort und Stelle.
        e.marke.bindPopup('', { minWidth: 250, maxWidth: 250, className: 'zug-fenster' });
        e.marke.on('popupopen', () => ladeFahrzeuge(t));
        marken.set(t, e);
      } else {
        e.marke.setLatLng(lage.p);
      }
      if (e.zustand === zustand) { continue; }
      e.zustand = zustand;
      const klasse = 'zug-marke ' + (lok ? 'z218' : farbgattung(a)) + (lage.faehrt ? '' : ' steht')
                   + (spaet >= 5 ? ' spaet' : '');
      // Ist die Lok bekannt, steht ihre Baureihe mit auf der Marke ("RE6 · 245")
      const fzLok = info && info.fz && info.fz.loks.length ? info.fz.loks[0].slice(0, 3) : '';
      const iceL = info && info.fz && info.fz.text.startsWith('ICE L');
      const beschriftung = lok ? lok.lok.slice(0, 7) : (iceL ? 'ICE L' : a.linie) + (fzLok ? ' · ' + fzLok : '');
      e.marke.setIcon(L.divIcon({ className: klasse, iconSize: null,
        html: '<span>' + ZUG_SVG + '<b>' + esc(beschriftung) + '</b></span>' }));
      e.marke.setZIndexOffset(lok ? 1200 : 200);
      const kopf = esc(a.name.replace(/\s*\((\d+)\)/, ' $1'));
      e.marke.setPopupContent('<b>' + kopf + '</b>'
        + (lok ? '<br><b class="rot">' + esc(lok.lok) + (lok.name ? ' „' + esc(lok.name) + '“' : '') + '</b>' : '')
        + (a.start && a.ziel ? '<div class="zug-lauf"><span>' + esc(a.start_zeit || '') + '</span> ' + esc(a.start)
            + '<br><span>' + esc(a.ziel_zeit || '') + '</span> ' + esc(a.ziel) + '</div>' : '')
        + fahrzeugZeile(info)
        + (lage.faehrt ? 'gerade ' + esc(a.von) + ' → ' + esc(a.nach) : 'hält in ' + esc(lage.halt))
        + '<br>' + (spaet > 0 ? '<span class="rot">+' + spaet + ' min</span>' : (spaet < 0 ? spaet + ' min' : 'pünktlich'))
        + (a.echt ? '' : ' <small>(Fahrplan, keine Echtzeit)</small>')
        + (lok ? '<br><a href="#" data-lok="' + esc(lok.lok) + '">Steckbrief</a>' : ''));
    }
    for (const [t, e] of marken) {
      if (!gesehen.has(t)) { zugEbene.removeLayer(e.marke); marken.delete(t); }
    }
    // Ist eine 218 live zu sehen, braucht es die Schätzung aus dem Plan nicht mehr.
    for (const [lok, m] of Object.entries(planMarke)) {
      if (live218.has(lok) && loks.hasLayer(m)) { loks.removeLayer(m); }
      if (!live218.has(lok) && !loks.hasLayer(m)) { loks.addLayer(m); }
    }
  };
  // ---------- Antippen der Strecke ----------
  // Tippen irgendwo auf ein Gleis (Marschbahn oder eine Linie durch Elmshorn): der Punkt
  // rastet auf das nächste Gleis ein (bis 28 px daneben), strecke.php nennt die Züge, die
  // dort in den nächsten 90 Minuten vorbeikommen — wann, woher, wohin.
  const gleisStuecke = [d.gleise.length > 1 ? d.gleise : d.strecke.map((s) => s.p)]
    .concat(...Object.values(d.linien || {}));
  const einrasten = (latlng) => {
    const p = karte.latLngToLayerPoint(latlng);
    let best = null; let bestD = Infinity;
    for (const stueck of gleisStuecke) {
      let a = karte.latLngToLayerPoint(stueck[0]);
      for (let i = 1; i < stueck.length; i++) {
        const b = karte.latLngToLayerPoint(stueck[i]);
        const q = L.LineUtil.closestPointOnSegment(p, a, b);
        const dd = q.distanceTo(p);
        if (dd < bestD) { bestD = dd; best = q; }
        a = b;
      }
    }
    return best && bestD <= 28 ? karte.layerPointToLatLng(best) : null;
  };
  const durchfahrtZeile = (f, i) => {
    const lok = f.nr ? nr218[f.nr] : null;
    const spaet = f.spaet > 0 ? ' <span class="rot">+' + f.spaet + '</span>' : '';
    const wann = f.in_min <= 0 ? 'jetzt' : 'in ' + f.in_min + ' min';
    return '<li class="' + (lok ? 'z218' : '') + '"><span class="dz-zeit">' + esc(f.zeit) + spaet
      + '<small>' + wann + '</small></span><span class="dz-zug"><b>' + esc(f.name.replace(/\s*\((\d+)\)/, ' $1')) + '</b>'
      + (lok ? ' <b class="rot">' + esc(lok.lok) + '</b>' : '')
      + '<br>' + (f.start && f.ziel ? esc(f.start) + ' → ' + esc(f.ziel) : esc(f.von) + ' → ' + esc(f.nach))
      + '<br><small>' + (f.halt ? 'hält hier' : 'kommt aus ' + esc(f.von) + ', fährt nach ' + esc(f.nach))
      + (f.echt ? '' : ' · Fahrplan') + '</small>'
      + (f.ab_plan && f.nr && gattung(f.linie) ? '<span class="dz-fz" data-i="' + i + '"><span class="leise">…</span></span>' : '')
      + '</span></li>';
  };
  // Fahrzeuge in der Streckenliste: die ersten AUTO_FZ Züge von selbst, die übrigen auf
  // Antippen (die Wagenreihung kommt von einem privaten Dienst — nicht alles auf einmal).
  const AUTO_FZ = 5;
  const fzText = (info) => (info.status === 'fertig'
    ? '<b>' + esc(info.fz.text) + '</b>' + (info.fz.vorne ? ' · vorne ' + esc(info.fz.vorne.replace(/^Lok /, 'Lok ')) : '')
    : '<span class="leise">keine Wagenreihung</span>');
  const fuelleFahrzeuge = (fahrtenListe, anfrageNr) => {
    const inhalt = punktFenster.getElement();
    if (!inhalt) { return; }
    inhalt.querySelectorAll('.dz-fz').forEach((feld) => {
      const f = fahrtenListe[Number(feld.dataset.i)];
      const laden = () => {
        feld.innerHTML = '<span class="leise">Fahrzeuge werden geholt …</span>';
        holeWagen(gattung(f.linie), f.nr, f.ab_plan, f.von).then((info) => {
          if (anfrageNr === punktAnfrage && feld.isConnected) { feld.innerHTML = fzText(info); }
        });
      };
      if (Number(feld.dataset.i) < AUTO_FZ) {
        laden();
      } else {
        feld.innerHTML = '<button type="button" class="dz-fz-knopf">Fahrzeuge anzeigen</button>';
        feld.querySelector('button').addEventListener('click', (ev) => { L.DomEvent.stop(ev); laden(); });
      }
    });
  };
  const punktFenster = L.popup({ minWidth: 290, maxWidth: 290, maxHeight: 360, className: 'durchfahrt-fenster', autoPanPadding: [20, 20] });
  let punktAnfrage = 0;
  karte.on('click', (e) => {
    const p = einrasten(e.latlng);
    if (p) { zeigeDurchfahrten(p, ''); }
  });
  // Züge an einem Punkt der Strecke; "hinweis" steht oben im Fenster (z. B. der Standort).
  const zeigeDurchfahrten = (p, hinweis) => {
    const nr = ++punktAnfrage;
    punktFenster.setLatLng(p).setContent(hinweis + '<div class="dz-kopf">Züge an dieser Stelle</div><p class="dz-laedt">Wird geholt …</p>').openOn(karte);
    fetch('strecke.php?lat=' + p.lat.toFixed(4) + '&lon=' + p.lng.toFixed(4), { credentials: 'same-origin', cache: 'no-store' })
      .then((r) => r.json())
      .then((j) => {
        if (nr !== punktAnfrage) { return; }
        if (j.status !== 'fertig') {
          punktFenster.setContent('<div class="dz-kopf">Züge an dieser Stelle</div><p>' + esc(j.fehler || 'Nicht verfügbar.') + '</p>');
          return;
        }
        const kopf = hinweis + '<div class="dz-kopf">' + (j.zwischen ? 'Zwischen ' + esc(j.zwischen[0]) + ' und ' + esc(j.zwischen[1]) : 'Züge an dieser Stelle')
          + '<small>nächste ' + j.minuten + ' Minuten · Stand ' + esc(j.stand) + '</small></div>';
        punktFenster.setContent(kopf + (j.fahrten.length
          ? '<ul class="dz-liste">' + j.fahrten.map(durchfahrtZeile).join('') + '</ul>'
          : '<p>Hier kommt in den nächsten ' + j.minuten + ' Minuten kein Zug vorbei.</p>'));
        fuelleFahrzeuge(j.fahrten, nr);
      })
      .catch(() => { if (nr === punktAnfrage) { punktFenster.setContent('<p>Keine Verbindung.</p>'); } });
  };

  // ---------- Mein Standort ----------
  // GPS des Handys (bleibt im Browser), dann der nächste Punkt auf einem Gleis — auf der
  // Marschbahn, den Linien durch Elmshorn oder dem Weg eines gerade fahrenden Zugs. Nur
  // dieser Gleispunkt geht an strecke.php, nicht der eigene Standort.
  const naechstesGleis = (pos) => {
    const k = Math.cos(pos.lat * Math.PI / 180);
    const stuecke = gleisStuecke.concat(...[...fahrten.values()].map((ab) => ab.map((a) => a.p)));
    let best = null; let bestD = Infinity;
    for (const st of stuecke) {
      for (let i = 1; i < st.length; i++) {
        const ax = (st[i - 1][1] - pos.lng) * k; const ay = st[i - 1][0] - pos.lat;
        const bx = (st[i][1] - pos.lng) * k; const by = st[i][0] - pos.lat;
        const dx = bx - ax; const dy = by - ay; const l2 = dx * dx + dy * dy;
        const t = l2 > 0 ? Math.max(0, Math.min(1, (-ax * dx - ay * dy) / l2)) : 0;
        const qx = ax + t * dx; const qy = ay + t * dy;
        const dd = Math.hypot(qx, qy) * 111320;
        if (dd < bestD) { bestD = dd; best = L.latLng(pos.lat + qy, pos.lng + qx / k); }
      }
    }
    return best ? { p: best, m: bestD } : null;
  };
  const standortEbene = L.layerGroup().addTo(karte);
  const meter = (m) => (m < 1000 ? Math.round(m / 10) * 10 + ' m' : (m / 1000).toFixed(1).replace('.', ',') + ' km');
  const standortSuchen = (knopf) => {
    if (!navigator.geolocation) {
      L.popup().setLatLng(karte.getCenter()).setContent('Dieses Gerät kann den Standort nicht bestimmen.').openOn(karte);
      return;
    }
    knopf.classList.add('sucht');
    navigator.geolocation.getCurrentPosition((pos) => {
      knopf.classList.remove('sucht');
      const ich = L.latLng(pos.coords.latitude, pos.coords.longitude);
      const genau = pos.coords.accuracy || 0;
      standortEbene.clearLayers();
      L.circle(ich, { radius: genau, color: '#0a66d0', weight: 1, fillOpacity: .08, interactive: false }).addTo(standortEbene);
      L.marker(ich, { icon: L.divIcon({ className: 'ich-marke', html: '<span></span>', iconSize: [22, 22], iconAnchor: [11, 11] }),
                      interactive: false, zIndexOffset: 1500 }).addTo(standortEbene);
      const g = naechstesGleis(ich);
      if (!g) { return; }
      const genauText = genau ? ' · GPS auf ±' + meter(genau) : '';
      if (g.m <= 3000) {
        L.polyline([ich, g.p], { color: '#0a66d0', weight: 2, dashArray: '3 5', interactive: false }).addTo(standortEbene);
        karte.setView(g.p, Math.max(karte.getZoom(), 13));
        zeigeDurchfahrten(g.p, '<div class="dz-standort">📍 Du bist ' + (g.m < 60 ? 'am Gleis' : meter(g.m) + ' vom Gleis') + genauText + '</div>');
      } else {
        karte.setView(ich, Math.max(karte.getZoom(), 11));
        const fenster = L.popup({ maxWidth: 260 }).setLatLng(ich).setContent('<div class="dz-standort">📍 Dein Standort' + genauText
          + '</div>Das nächste Gleis auf der Karte ist ' + meter(g.m) + ' entfernt.<br>'
          + '<button type="button" class="dz-fz-knopf" id="zum-gleis">Züge dort zeigen</button>').openOn(karte);
        const b = fenster.getElement() && fenster.getElement().querySelector('#zum-gleis');
        if (b) {
          b.addEventListener('click', () => {
            L.polyline([ich, g.p], { color: '#0a66d0', weight: 2, dashArray: '3 5', interactive: false }).addTo(standortEbene);
            karte.setView(g.p, Math.max(karte.getZoom(), 12));
            zeigeDurchfahrten(g.p, '<div class="dz-standort">📍 ' + meter(g.m) + ' von deinem Standort</div>');
          });
        }
      }
    }, (fehler) => {
      knopf.classList.remove('sucht');
      const text = fehler.code === 1 ? 'Der Standort ist für Zugradar nicht freigegeben. Bitte in den Einstellungen des Browsers erlauben.'
        : (fehler.code === 3 ? 'Das GPS hat zu lange gebraucht. Bitte nochmal versuchen, am besten draußen.' : 'Der Standort ließ sich gerade nicht bestimmen.');
      L.popup({ maxWidth: 260 }).setLatLng(karte.getCenter()).setContent(text).openOn(karte);
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 30000 });
  };
  const StandortKnopf = L.Control.extend({
    options: { position: 'topleft' },
    onAdd() {
      const box = L.DomUtil.create('div', 'leaflet-bar standort-knopf');
      const a = L.DomUtil.create('a', '', box);
      a.href = '#';
      a.title = 'Mein Standort: Züge am nächsten Gleis';
      a.setAttribute('role', 'button');
      a.setAttribute('aria-label', 'Mein Standort');
      a.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" fill="currentColor"/>'
        + '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<path d="M12 1v4M12 19v4M1 12h4M19 12h4" stroke="currentColor" stroke-width="2"/></svg>';
      L.DomEvent.disableClickPropagation(box);
      L.DomEvent.on(a, 'click', (ev) => { L.DomEvent.preventDefault(ev); standortSuchen(a); });
      this.knopf = a;
      return box;
    },
  });
  const standortKnopf = new StandortKnopf().addTo(karte);
  // Von der Startseite ("In deiner Nähe" → Karte) kommt ?standort=1: gleich suchen.
  if (new URLSearchParams(location.search).get('standort') === '1') { standortSuchen(standortKnopf.knopf); }

  // ---------- Gebiete und Zugarten ----------
  // Ein Tipp auf ein Gebiet schaltet es beim Hintergrunddienst an oder aus (regionen.php);
  // die Züge sind dann binnen einer Minute da. Zugarten wirken sofort, nur in der Anzeige.
  const gebietLeiste = document.getElementById('fb-gebiete');
  const artLeiste = document.getElementById('fb-arten');
  if (gebietLeiste) {
    const knopf = (wohin, text, titel) => {
      const k = document.createElement('button');
      k.type = 'button';
      k.className = 'fb-schalter';
      k.textContent = text;
      if (titel) { k.title = titel; }
      wohin.appendChild(k);
      return k;
    };
    // Meldet der Dienst für ein Land ein Problem (zu viele Züge, noch nicht drangekommen),
    // steht das am Knopf.
    const gebietProblem = { teilweise: 'zu viele Züge — dort unvollständig',
                            ausgelassen: 'wartet auf den nächsten Durchgang',
                            fehler: 'ließ sich zuletzt nicht holen' };
    const gebietStatus = d.gebiete_status || {};
    const gebietKnoepfe = (d.gebiete || []).map((g) => {
      const hinweis = gebietStatus[g.k] ? ' · ' + gebietProblem[gebietStatus[g.k]] : '';
      const k = knopf(gebietLeiste, g.kurz + (hinweis ? ' !' : ''), g.name + hinweis);
      if (hinweis) { k.classList.add('klemmt'); }
      return [g, k];
    });
    const artKnoepfe = [['regio', 'Regio · RE, RB'], ['fern', 'Fern · ICE, IC, FLX']]
      .map(([wert, text]) => [wert, knopf(artLeiste, text)]);
    const zeigen = () => {
      gebietKnoepfe.forEach(([g, k]) => {
        const an = gebieteAn.includes(g.k);
        k.classList.toggle('an', an);
        k.setAttribute('aria-pressed', an ? 'true' : 'false');
        if (g.k === 'SH' || g.k === 'HH') { k.classList.add('fest'); k.title = g.name + ' — immer an'; }
      });
      artKnoepfe.forEach(([wert, k]) => {
        k.classList.toggle('an', !artenAus.includes(wert));
        k.setAttribute('aria-pressed', artenAus.includes(wert) ? 'false' : 'true');
      });
      if (window.filterStandZeigen) { window.filterStandZeigen(); }
    };
    gebietKnoepfe.forEach(([g, k]) => k.addEventListener('click', () => {
      if (g.k === 'SH' || g.k === 'HH') { return; }     // Elmshorn und Hamburg bleiben an
      const an = gebieteAn.includes(g.k);
      gebieteAn = an ? gebieteAn.filter((x) => x !== g.k) : gebieteAn.concat(g.k);
      zeigen();
      zeichneZuege();
      netzGeholt = '';
      netzLaden();
      bfGeholt = '';
      bfLaden();
      k.classList.add('laedt');
      const daten = new URLSearchParams({ r: gebieteAn.join(',') });
      fetch('regionen.php', { method: 'POST', credentials: 'same-origin', body: daten })
        .then((r) => r.json())
        .then((j) => { if (j && j.regionen) { gebieteAn = j.regionen; zeigen(); } })
        .catch(() => {})
        // Der Dienst holt jede Minute — kurz darauf ist das neue Gebiet da.
        .finally(() => {
          if (!an) {
            let versuche = 0;
            const warten = setInterval(() => {
              nachladen();
              if (++versuche >= 8 || (fahrten.size && [...fahrten.values()].some((ab) => ab[0].r === g.k))) {
                clearInterval(warten);
                k.classList.remove('laedt');
              }
            }, 10000);
          } else {
            k.classList.remove('laedt');
          }
        });
    }));
    artKnoepfe.forEach(([wert, k]) => k.addEventListener('click', () => {
      artenAus = artenAus.includes(wert) ? artenAus.filter((x) => x !== wert) : artenAus.concat(wert);
      merkGebiet('zugradar_karte_arten', JSON.stringify(artenAus));
      zeigen();
      zeichneZuege();
    }));
    zeigen();
  }

  const klein = () => ziel.classList.toggle('klein', karte.getZoom() < 9);
  karte.on('zoomend', klein);
  klein();

  fahrten = gruppiere(d.zuege || []);
  zeichneZuege();
  setInterval(() => { if (!document.hidden) { zeichneZuege(); } }, 2000);
  // Ohne Netz zeigt die Karte den zuletzt geholten Stand (der Service Worker hebt ihn auf).
  // Damit niemand veraltete Züge für aktuell hält, steht das Datum dann über der Karte.
  const hinweisFeld = document.getElementById('karte-hinweis');
  let datenStand = d.zuege_stand || 0;
  const standZeigen = () => {
    const alter = datenStand ? Date.now() / 1000 - datenStand : 0;
    const alt = !datenStand || alter > 180;
    hinweisFeld.hidden = !alt;
    if (alt) {
      const uhr = datenStand
        ? new Date(datenStand * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
        : null;
      hinweisFeld.textContent = (navigator.onLine ? '⚠ Züge nicht aktuell' : '📴 Offline')
        + (uhr ? ' — gespeicherter Stand von ' + uhr : ' — keine gespeicherten Züge')
        + (alter > 3600 ? ' (die Züge sind längst weitergefahren)' : '');
    }
  };
  const nachladen = () => {
    if (document.hidden) { return; }
    fetch('zuege.php', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j && Array.isArray(j.zuege)) {
          fahrten = gruppiere(j.zuege);
          datenStand = j.stand || datenStand;
          zeichneZuege();
        }
        standZeigen();
      })
      .catch(() => standZeigen());
  };
  window.addEventListener('online', () => { standZeigen(); nachladen(); });
  window.addEventListener('offline', standZeigen);
  setInterval(standZeigen, 30000);
  standZeigen();
  setInterval(nachladen, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { nachladen(); } });

  // abgestellte Loks
  const standortMarken = d.standorte.map((s) => ({ s, m: L.circleMarker(s.p, {
    radius: 6 + Math.min(8, s.loks.length), color: '#5b6674', weight: 2, fillColor: '#93a1b1', fillOpacity: .7,
  }).bindPopup('<b>' + esc(s.ort) + '</b> — ' + s.loks.length + ' Lok' + (s.loks.length > 1 ? 's' : '')
      + '<br>' + s.loks.map(esc).join('<br>')) }));
  ebenen['Abgestellt'] = L.layerGroup(standortMarken.map((x) => x.m)).addTo(karte);

  // Beiträge aus dem Umkreis
  ebenen['Umkreis (7 Tage)'] = L.layerGroup(d.umkreis.map((u) => L.circleMarker(u.p, {
    radius: 6, color: '#b35c00', weight: 2, fillColor: '#f0a04b', fillOpacity: .85,
  }).bindPopup('<b>' + esc(u.ort) + '</b> · ' + esc(u.wann) + '<br><a href="' + esc(u.url)
      + '" target="_blank" rel="noopener">' + esc(u.titel) + '</a>'))).addTo(karte);

  // ---------- Filtertafel über der Karte ----------
  // Alles an einer Stelle: Kartentyp, Ebenen, Gebiete, Zugarten, Gleisplan. Das
  // Ebenen-Menü von Leaflet entfällt dafür. Ausgeblendetes merkt sich das Gerät.
  const FILTER = [['Züge live', 'Züge', '🚆'], ['Bahnhöfe', 'Bahnhöfe', '⏺'],
                  ['Streckennetz', 'Streckennetz', '〰'], ['Marschbahn', 'Marschbahn', '🛤'],
                  ['Linien durch Elmshorn', 'Linien durch Elmshorn', '📈'],
                  ['218er laut Plan', '218er laut Plan', '🚂'], ['Abgestellt', 'Abgestellte Loks', '🅿'],
                  ['Umkreis (7 Tage)', 'Beiträge im Umkreis', '💬'],
                  ['Landesgrenzen', 'Landesgrenzen', '🗺'], ['50 km um Elmshorn', '50-km-Kreis', '⭕']]
    .filter(([name]) => ebenen[name]);
  let aus = [];
  try { aus = JSON.parse(merk('zugradar_karte_aus') || '[]'); } catch (e) { }
  FILTER.forEach(([name]) => { if (aus.includes(name)) { karte.removeLayer(ebenen[name]); } });

  const tafelKnopf = (wohin, text, zeichen) => {
    const k = document.createElement('button');
    k.type = 'button';
    k.className = 'fb-schalter';
    k.innerHTML = (zeichen ? '<span class="fb-zeichen" aria-hidden="true">' + zeichen + '</span>' : '') + esc(text);
    wohin.appendChild(k);
    return k;
  };
  const ebenenListe = document.getElementById('fb-ebenen');
  const knoepfe = FILTER.map(([name, text, zeichen]) => {
    const k = tafelKnopf(ebenenListe, text, zeichen);
    k.addEventListener('click', () => {
      if (karte.hasLayer(ebenen[name])) { karte.removeLayer(ebenen[name]); } else { karte.addLayer(ebenen[name]); }
    });
    return [name, text, k];
  });
  // Kartentyp (eine Wahl) und die Gleisebenen von OpenRailwayMap (mehrere möglich)
  const basisListe = document.getElementById('fb-karte');
  const basisKnoepfe = Object.entries(basis).map(([name, ebene]) => {
    const k = tafelKnopf(basisListe, name, '');
    k.addEventListener('click', () => {
      Object.values(basis).forEach((b) => { if (b !== ebene && karte.hasLayer(b)) { karte.removeLayer(b); } });
      if (!karte.hasLayer(ebene)) { karte.addLayer(ebene); }
      merk('zugradar_karte_typ', name);
      zeigeTafelstand();
    });
    return [ebene, k];
  });
  const bahnListe = document.getElementById('fb-bahn');
  const bahnKnoepfe = Object.entries(bahnEbenen).map(([name, ebene]) => {
    const k = tafelKnopf(bahnListe, name.replace(' (OpenRailwayMap)', ''), '');
    k.addEventListener('click', () => {
      if (karte.hasLayer(ebene)) { karte.removeLayer(ebene); } else { karte.addLayer(ebene); }
      zeigeTafelstand();
    });
    return [ebene, k];
  });
  const stand = document.getElementById('filter-stand');
  const zeigeTafelstand = () => {
    basisKnoepfe.forEach(([ebene, k]) => k.classList.toggle('an', karte.hasLayer(ebene)));
    bahnKnoepfe.forEach(([ebene, k]) => k.classList.toggle('an', karte.hasLayer(ebene)));
  };
  window.filterStandZeigen = () => {
    const anEbenen = knoepfe.filter(([name]) => karte.hasLayer(ebenen[name])).map(([, text]) => text);
    stand.textContent = (anEbenen.slice(0, 3).join(' · ') || 'nichts eingeblendet')
      + (anEbenen.length > 3 ? ' +' + (anEbenen.length - 3) : '')
      + ' · ' + gebieteAn.join(', ');
  };
  const filterZeigen = () => {
    knoepfe.forEach(([name, , k]) => {
      const an = karte.hasLayer(ebenen[name]);
      k.classList.toggle('an', an);
      k.setAttribute('aria-pressed', an ? 'true' : 'false');
    });
    zeigeTafelstand();
    window.filterStandZeigen();
    merk('zugradar_karte_aus', JSON.stringify(FILTER.map(([n]) => n).filter((n) => !karte.hasLayer(ebenen[n]))));
  };
  // Auf- und zuklappen
  const blatt = document.getElementById('filterblatt');
  const aufKnopf = document.getElementById('filter-auf');
  const umschalten = () => {
    const offen = !blatt.hasAttribute('hidden');
    blatt.toggleAttribute('hidden', offen);
    aufKnopf.setAttribute('aria-expanded', offen ? 'false' : 'true');
    aufKnopf.classList.toggle('offen', !offen);
    if (!offen) { setTimeout(() => karte.invalidateSize(), 50); }
  };
  aufKnopf.addEventListener('click', umschalten);
  stand.addEventListener('click', umschalten);
  // Tippen außerhalb schließt die Tafel wieder
  document.addEventListener('click', (e) => {
    if (blatt.hasAttribute('hidden') || blatt.contains(e.target) || aufKnopf.contains(e.target)
        || stand.contains(e.target)) { return; }
    umschalten();
  });
  // ---------- Lok suchen (?lok=218 330-9, aus dem Steckbrief "Auf der Karte") ----------
  // Reihenfolge: live als Zug unterwegs → laut Plan → abgestellt. Ist die passende Ebene
  // ausgeblendet, wird sie dafür wieder eingeblendet.
  const suchLok = new URLSearchParams(location.search).get('lok');
  if (suchLok) {
    const ziffern = (x) => String(x || '').replace(/\D/g, '').slice(0, 6);
    const z = ziffern(suchLok);
    const name = suchLok.toLowerCase();
    const istEs = (lok, lokName) => (z.length >= 3 ? ziffern(lok) === z : (lokName || '').toLowerCase() === name);
    const namen = {};
    Object.values(nr218).forEach((l) => { namen[l.lok] = l.name; });
    d.loks.forEach((l) => { namen[l.lok] = l.name; });
    const zeige = (ebene, marke, zoom) => {
      if (!karte.hasLayer(ebene)) { karte.addLayer(ebene); }
      karte.setView(marke.getLatLng(), Math.max(karte.getZoom(), zoom));
      marke.openPopup();
    };
    const finden = () => {
      for (const [t, e] of marken) {
        const a = (fahrten.get(t) || [])[0];
        const l = a && a.nr ? nr218[a.nr] : null;
        if (l && istEs(l.lok, l.name)) { zeige(zugEbene, e.marke, 12); return true; }
      }
      for (const [lok, m] of Object.entries(planMarke)) {
        if (istEs(lok, namen[lok])) { if (!loks.hasLayer(m)) { loks.addLayer(m); } zeige(loks, m, 11); return true; }
      }
      for (const { s: st, m } of standortMarken) {
        if (st.loks.some((eintrag) => istEs(eintrag.slice(0, 9), (eintrag.match(/„([^“]+)“/) || [])[1]))) {
          zeige(ebenen['Abgestellt'], m, 11);
          return true;
        }
      }
      return false;
    };
    if (!finden()) {
      L.popup({ maxWidth: 260 }).setLatLng(elmshorn).setContent('<b>' + esc(suchLok) + '</b><br>'
        + 'Heute weder unterwegs noch im Plan noch abgestellt gemeldet.'
        + '<br><a href="./?s=tage&amp;lok=' + encodeURIComponent(suchLok) + '">Alle Fahrten</a> · '
        + '<a href="#" data-lok="' + esc(suchLok) + '">Steckbrief</a>').openOn(karte);
    }
  }

  // Nur auf die Ebenen selbst hören — Zugmarken kommen und gehen ständig.
  karte.on('layeradd layerremove', (e) => { if (FILTER.some(([n]) => ebenen[n] === e.layer)) { filterZeigen(); } });
  filterZeigen();
  // Der Steckbrief-Link im Popup braucht nichts Eigenes: data-lok fängt der
  // allgemeine Klick-Handler im Seitenrahmen ab und öffnet das Lok-Fenster.
})();
</script>
JS;
}
$skript = $seite === 'karte' ? $karteSkript : ($seite === 'tage' ? $tageSkript : ($seite === 'lage' ? $lageSkript : ($seite === 'mehr' ? '<script src="push-client.js?v=2"></script>
<script>
ZugradarPush.knopf(document.getElementById(\'push-bereich\'));
' . <<<'JS'
// ---- Einstellungen: nur auf diesem Gerät, im Speicher des Browsers
(function () {
  const wurzel = document.documentElement;
  const lies = (name, ersatz) => { try { return localStorage.getItem(name) ?? ersatz; } catch (e) { return ersatz; } };
  const schreib = (name, wert) => { try { localStorage.setItem(name, wert); } catch (e) { /* egal */ } };
  const dauer = document.getElementById('e-dauer');
  const immer = document.getElementById('e-immer');
  const motion = document.getElementById('e-motion');
  const test = document.getElementById('e-test');
  if (!dauer) { return; }
  dauer.value = lies('zr-start-dauer', '1600');
  if (![...dauer.options].some((o) => o.value === dauer.value)) { dauer.value = '1600'; }
  immer.value = lies('zr-start-immer', '0');
  motion.value = lies('zr-animationen', 'an');

  dauer.addEventListener('change', () => {
    schreib('zr-start-dauer', dauer.value);
    wurzel.style.setProperty('--splash-dauer', dauer.value + 'ms');
  });
  immer.addEventListener('change', () => schreib('zr-start-immer', immer.value));
  motion.addEventListener('change', () => {
    schreib('zr-animationen', motion.value);
    if (motion.value === 'aus') { wurzel.dataset.motion = 'aus'; } else { delete wurzel.dataset.motion; }
  });
  test.addEventListener('click', () => {
    if (motion.value === 'aus') { motion.value = 'an'; motion.dispatchEvent(new Event('change')); }
    wurzel.style.setProperty('--splash-dauer', (parseInt(dauer.value, 10) || 1600) + 'ms');
    delete wurzel.dataset.splash;
    void document.body.offsetWidth;                 // Animation neu starten
    wurzel.dataset.splash = 'an';
  });
})();
JS
. <<<'JS'
// ---- Installieren-Knopf
(function () {
  const knopf = document.getElementById('app-knopf');
  const hinweis = document.getElementById('app-hinweis');
  const status = document.getElementById('app-status');
  if (!knopf) { return; }                // nur auf der Seite „Mehr“
  const alsApp = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent)
           || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  // Samsung Internet baut das App-Paket selbst auf dem Handy, mit veralteter
  // Android-Zielversion — Android warnt dann "unsichere App". Chrome lässt das
  // Paket von Google bauen, dort kommt die Warnung nicht.
  const samsung = /SamsungBrowser/i.test(navigator.userAgent);
  let angebot = null;

  if (alsApp) { status.hidden = false; return; }

  if (samsung) {
    knopf.hidden = false;
    hinweis.hidden = false;
    hinweis.innerHTML = '<strong>Tipp:</strong> Bitte diese Seite in <strong>Chrome</strong> öffnen und dort '
      + 'installieren. Samsung Internet erzeugt eine App, vor der Android als „unsicher“ bzw. '
      + '„für eine ältere Android-Version entwickelt“ warnt.';
    knopf.addEventListener('click', () => {
      location.href = 'intent://jarritc.de/zugradar/#Intent;scheme=https;package=com.android.chrome;end';
    });
    knopf.lastChild.textContent = ' In Chrome öffnen';
    return;
  }

  // Chrome, Edge, Android: der Browser liefert einen echten Installationsdialog
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    angebot = e;
    knopf.hidden = false;
    hinweis.hidden = true;
  });
  knopf.addEventListener('click', async () => {
    if (angebot) {
      angebot.prompt();
      const wahl = await angebot.userChoice;
      if (wahl.outcome === 'accepted') { knopf.hidden = true; }
      angebot = null;
      return;
    }
    hinweis.hidden = !hinweis.hidden;
  });
  window.addEventListener('appinstalled', () => {
    knopf.hidden = true; hinweis.hidden = true; status.hidden = false;
  });

  // Safari (iPhone, iPad) und Firefox kennen keinen Dialog — dann eine Anleitung
  setTimeout(() => {
    if (angebot) { return; }
    knopf.hidden = false;
    hinweis.innerHTML = ios
      ? 'Unten auf <strong>Teilen</strong> <span aria-hidden="true">⬆︎</span> tippen, dann '
        + '<strong>„Zum Home-Bildschirm“</strong>.'
      : 'Im Browsermenü <strong>„App installieren“</strong> bzw. '
        + '<strong>„Zum Startbildschirm hinzufügen“</strong> wählen.';
  }, 1500);
})();
JS
. '</script>' : '')));
seiten_fuss($menueAktiv, $skript);

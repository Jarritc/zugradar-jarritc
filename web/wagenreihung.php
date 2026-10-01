<?php
/**
 * Wagenreihung einer Abfahrt in Elmshorn als JSON für das Fenster in der App.
 *
 * Diese Seite holt nichts von außen: Sie schreibt die Anfrage in die Tabelle
 * `wagenreihung` und wartet kurz, bis /opt/docker/kukas-zug/wagenreihung.py sie
 * beantwortet hat (Cron, alle paar Sekunden). Frisch Geholtes kommt sofort aus
 * der Tabelle.
 *
 *   wagenreihung.php?tt=RE&tn=11030&dt=1789667880
 *   wagenreihung.php?tt=RE&tn=11030&dt=1789667880&bf=Itzehoe   (Abfahrt an einem anderen
 *   Bahnhof — für die Karte; der Name wird in `bahnhofsliste` zur EVA-Nummer)
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function raus(int $code, array $daten): never {
    http_response_code($code);
    echo json_encode($daten, JSON_UNESCAPED_UNICODE);
    exit;
}

if (!angemeldet()) {
    raus(401, ['fehler' => 'nicht angemeldet']);
}

$kategorie = (string)($_GET['tt'] ?? '');
$nummer    = (string)($_GET['tn'] ?? '');
$zeitpunkt = (int)($_GET['dt'] ?? 0);
if (!preg_match('/^[A-Z]{1,4}$/', $kategorie) || !preg_match('/^\d{1,6}$/', $nummer)
    || $zeitpunkt < time() - 3 * 86400 || $zeitpunkt > time() + 8 * 86400) {
    raus(400, ['fehler' => 'ungültige Fahrt']);
}

$pdo = kukas_db();
$eva = null;
$bf = trim((string)($_GET['bf'] ?? ''));
if ($bf !== '' && $bf !== 'Elmshorn') {
    if (!preg_match("/^[\\p{L}\\d .()\\/'-]{2,60}$/u", $bf)) {
        raus(400, ['fehler' => 'ungültiger Bahnhof']);
    }
    $st = $pdo->prepare('SELECT eva FROM bahnhofsliste WHERE name = ? COLLATE utf8mb4_bin LIMIT 1');
    $st->execute([$bf]);
    $eva = $st->fetchColumn() ?: null;
    if ($eva === null) {
        raus(200, ['status' => 'fehler', 'fehler' => 'Bahnhof „' . $bf . '“ ist nicht in der Bahnhofsliste.']);
    }
    $eva = (int)$eva;
}

const FRISCH_SEKUNDEN = 180;      // wie in wagenreihung.py
const WARTEN_SEKUNDEN = 12;       // so lange auf das Hintergrundskript warten

$schluessel = $kategorie . '|' . $nummer . '|' . $zeitpunkt . ($eva ? '|' . $eva : '');

/** Zeile lesen, wenn sie frisch beantwortet ist. */
function fertig(PDO $pdo, string $schluessel): ?array {
    $st = $pdo->prepare('SELECT status, daten, fehler, geholt_am FROM wagenreihung
                          WHERE schluessel = ? AND status <> "offen" AND geholt_am IS NOT NULL
                            AND geholt_am > NOW() - INTERVAL ' . FRISCH_SEKUNDEN . ' SECOND');
    $st->execute([$schluessel]);
    $r = $st->fetch();
    if (!$r) { return null; }
    return ['status' => $r['status'], 'fehler' => $r['fehler'],
            'daten' => $r['daten'] ? json_decode((string)$r['daten'], true) : null,
            'stand' => date('H:i', strtotime((string)$r['geholt_am']))];
}

try {
    if ($fertig = fertig($pdo, $schluessel)) {
        raus(200, $fertig);
    }
    $pdo->prepare('INSERT INTO wagenreihung (schluessel, kategorie, nummer, abfahrt, eva, status, angefragt_am)
                   VALUES (?, ?, ?, FROM_UNIXTIME(?), ?, "offen", NOW())
                   ON DUPLICATE KEY UPDATE status = "offen", angefragt_am = NOW()')
        ->execute([$schluessel, $kategorie, $nummer, $zeitpunkt, $eva]);

    $bis = time() + WARTEN_SEKUNDEN;
    do {
        usleep(400000);
        if ($fertig = fertig($pdo, $schluessel)) {
            raus(200, $fertig);
        }
    } while (time() < $bis);
} catch (Throwable $e) {
    raus(500, ['status' => 'fehler', 'fehler' => 'Datenbank: ' . $e->getMessage()]);
}

raus(200, ['status' => 'wartet',
           'fehler' => 'Die Wagenreihung kam gerade nicht rechtzeitig. Bitte nochmal antippen.']);

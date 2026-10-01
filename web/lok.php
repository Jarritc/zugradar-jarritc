<?php
/**
 * Steckbrief einer Lok als JSON für das Fenster in der App:
 * Name, Besonderheiten, wo sie steht, was sie heute macht, ihre letzten Fahrten
 * durch Elmshorn und wie oft sie dort war.
 *
 * Liest nur die Datenbank — wie index.php fragt diese Seite keine Schnittstelle ab.
 *
 *   lok.php?lok=218+453-9
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

$lok = trim((string)($_GET['lok'] ?? ''));
if (!preg_match('/^\d{3} \d{3}-\d$/', $lok)) {
    raus(400, ['fehler' => 'ungültige Loknummer']);
}

try {
    $pdo = kukas_db();
    $heute = date('Y-m-d');

    $name = null;
    $st = $pdo->prepare('SELECT name FROM lok_namen WHERE lok = ?');
    $st->execute([$lok]);
    $name = $st->fetchColumn() ?: null;

    $besonders = null;
    try {
        $st = $pdo->prepare('SELECT * FROM besondere_loks WHERE lok = ?');
        $st->execute([$lok]);
        $besonders = $st->fetch() ?: null;
        if ($besonders && !$name) { $name = $besonders['name'] ?: null; }
    } catch (Throwable $e) { /* Tabelle fehlt noch */ }

    // Wo sie zuletzt stand (Fahrzeugliste im Forum)
    $st = $pdo->prepare('SELECT ort, tag FROM standorte WHERE lok = ? ORDER BY tag DESC LIMIT 1');
    $st->execute([$lok]);
    $standort = $st->fetch() ?: null;

    // Was sie heute fährt
    $st = $pdo->prepare('SELECT zug, quelle, von_halt, von_zeit, nach_halt, nach_zeit
                           FROM umlaeufe WHERE lok = ? AND tag = ? ORDER BY von_zeit');
    $st->execute([$lok, $heute]);
    $heuteFahrten = $st->fetchAll();

    // Die nächsten und letzten Durchfahrten in Elmshorn
    $st = $pdo->prepare('SELECT tag, zug, quelle, elmshorn_zeit, gleis, richtung, von_halt, nach_halt, aktiv
                           FROM laeufe WHERE lok = ? AND tag >= ? AND aktiv = 1
                          ORDER BY tag, elmshorn_zeit LIMIT 5');
    $st->execute([$lok, $heute]);
    $naechste = $st->fetchAll();

    $st = $pdo->prepare('SELECT tag, zug, quelle, elmshorn_zeit, gleis, richtung
                           FROM laeufe WHERE lok = ? AND tag < ? ORDER BY tag DESC, elmshorn_zeit DESC LIMIT 5');
    $st->execute([$lok, $heute]);
    $letzte = $st->fetchAll();

    $st = $pdo->prepare('SELECT COUNT(*) AS gesamt,
                                SUM(tag >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)) AS monat,
                                MIN(tag) AS seit
                           FROM laeufe WHERE lok = ?');
    $st->execute([$lok]);
    $zahlen = $st->fetch() ?: ['gesamt' => 0, 'monat' => 0, 'seit' => null];

    $st = $pdo->prepare('SELECT COUNT(DISTINCT tag) FROM umlaeufe WHERE lok = ?');
    $st->execute([$lok]);
    $einsatztage = (int)$st->fetchColumn();

    raus(200, [
        'lok' => $lok,
        'name' => $name,
        'besonders' => $besonders ? [
            'lackierung' => $besonders['lackierung'] ?: null,
            'bemerkung' => $besonders['bemerkung'] ?: null,
            'betreiber' => $besonders['betreiber'] ?: null,
            'zustand' => $besonders['zustand'] ?: null,
            'belegt' => (bool)$besonders['belegt'],
        ] : null,
        'standort' => $standort ? ['ort' => $standort['ort'], 'tag' => $standort['tag']] : null,
        'heute' => $heuteFahrten,
        'naechste' => $naechste,
        'letzte' => $letzte,
        'zahlen' => [
            'elmshorn_gesamt' => (int)$zahlen['gesamt'],
            'elmshorn_30_tage' => (int)$zahlen['monat'],
            'seit' => $zahlen['seit'],
            'einsatztage' => $einsatztage,
        ],
    ]);
} catch (Throwable $e) {
    raus(500, ['fehler' => 'Datenbank: ' . $e->getMessage()]);
}

<?php
/**
 * Welche Gebiete die Karte holen soll (Schleswig-Holstein, Niedersachsen, …).
 *
 * Die Auswahl steht in `meta.einst_karte_regionen`; zuege.py liest sie beim nächsten Lauf
 * (jede Minute) und fragt dann je Gebiet einen eigenen Kartenausschnitt ab. Mehr Gebiete
 * heißt mehr Abfragen und mehr Daten — deshalb wird die Auswahl bewusst geschaltet.
 *
 *   POST regionen.php   r=SH,NI      → {"ok":true,"regionen":["SH","NI"]}
 *   GET  regionen.php                → {"ok":true,"regionen":["SH"]}
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

// Wie REGIONEN in zuege.py: alle Bundesländer plus dänischer Grenzraum
const ERLAUBTE_REGIONEN = ['SH', 'HH', 'NI', 'HB', 'MV', 'BE', 'BB', 'ST', 'SN', 'TH', 'HE',
                           'NW', 'RP', 'SL', 'BW', 'BY', 'DK'];
const IMMER_AN = ['SH', 'HH'];                               // Elmshorn und Hamburg

if (!angemeldet()) {
    http_response_code(401);
    echo '{"ok":false,"fehler":"nicht angemeldet"}';
    exit;
}
try {
    $pdo = kukas_db();
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $wunsch = array_values(array_intersect(
            array_map('trim', explode(',', (string)($_POST['r'] ?? ''))), ERLAUBTE_REGIONEN));
        foreach (array_reverse(IMMER_AN) as $pflicht) {      // ohne die beiden nie
            if (!in_array($pflicht, $wunsch, true)) { array_unshift($wunsch, $pflicht); }
        }
        $pdo->prepare('INSERT INTO meta (schluessel, wert) VALUES ("einst_karte_regionen", ?)
                       ON DUPLICATE KEY UPDATE wert = VALUES(wert)')->execute([implode(',', $wunsch)]);
        echo json_encode(['ok' => true, 'regionen' => $wunsch]);
        exit;
    }
    $wert = (string)($pdo->query('SELECT wert FROM meta WHERE schluessel = "einst_karte_regionen"')
                         ->fetchColumn() ?: 'SH');
    echo json_encode(['ok' => true, 'regionen' => array_values(array_intersect(explode(',', $wert), ERLAUBTE_REGIONEN))]);
} catch (Throwable $e) {
    http_response_code(500);
    echo '{"ok":false,"fehler":"Datenbank"}';
}

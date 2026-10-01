<?php
/**
 * Fahrende Züge für die Karte, als JSON.
 *
 * Liest nur die Tabelle `karte_zuege` — die schreibt zuege.py jede Minute aus transitous.
 * Jeder Eintrag ist ein Abschnitt von Halt zu Halt mit Abfahrt, Ankunft (Unix-Zeit) und
 * Verlauf; die Position zur aktuellen Sekunde rechnet die Karte selbst aus.
 *
 *   zuege.php            alle Gebiete
 *   zuege.php?r=SH,NI    nur diese Gebiete (spart Daten auf dem Handy)
 *
 *   → {"stand": …, "zuege": [{t, r, linie, nr, name, von, nach, ab, an, ab_plan, echt, p}, …]}
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

if (!angemeldet()) {
    http_response_code(401);
    echo '{"fehler":"nicht angemeldet"}';
    exit;
}
$nur = array_values(array_filter(array_map('trim', explode(',', (string)($_GET['r'] ?? ''))),
                                  static fn(string $r): bool => (bool)preg_match('/^[A-Z]{2}$/', $r)));
try {
    $pdo = kukas_db();
    // Vermerken, dass gerade jemand auf die Karte schaut — zuege.py holt sonst nichts
    // (Rücksicht auf transitous, 01.10.2026).
    $pdo->query('INSERT INTO meta (schluessel, wert) VALUES ("karte_angefragt", NOW())
                 ON DUPLICATE KEY UPDATE wert = NOW()');
    $z = $pdo->query('SELECT stand, daten FROM karte_zuege WHERE id = 1')->fetch();
    if (!$nur) {
        // Ohne Auswahl liegen die Daten schon als JSON vor — unverändert durchreichen.
        echo '{"stand":' . (int)($z['stand'] ?? 0) . ',"zuege":' . ($z ? (string)$z['daten'] : '[]') . '}';
        exit;
    }
    $alle = $z ? (json_decode((string)$z['daten'], true) ?: []) : [];
    $gefiltert = array_values(array_filter($alle, static fn(array $a): bool => in_array($a['r'] ?? 'SH', $nur, true)));
    echo json_encode(['stand' => (int)($z['stand'] ?? 0), 'zuege' => $gefiltert], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    echo '{"fehler":"Datenbank"}';
}

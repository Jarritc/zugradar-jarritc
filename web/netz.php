<?php
/**
 * Streckennetz für die Karte: alle Abschnitte, auf denen in den letzten drei Wochen ein
 * Zug gefahren ist — je Gebiet einmal zusammengesetzt von zuege.py (Tabelle `karte_netz`).
 *
 *   netz.php?r=SH,NI  → {"stand":"22:29","netz":[[[53.75,9.65],[53.76,9.65]], …]}
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, max-age=300');

if (!angemeldet()) {
    http_response_code(401);
    echo '{"fehler":"nicht angemeldet"}';
    exit;
}
$nur = array_values(array_filter(array_map('trim', explode(',', (string)($_GET['r'] ?? 'SH'))),
                                 static fn(string $r): bool => (bool)preg_match('/^[A-Z]{2}$/', $r)));
if (!$nur) { $nur = ['SH']; }
try {
    $st = kukas_db()->prepare('SELECT region, stand, daten FROM karte_netz
                                WHERE region IN (' . implode(',', array_fill(0, count($nur), '?')) . ')');
    $st->execute($nur);
    $netz = [];
    $stand = null;
    foreach ($st->fetchAll() as $z) {
        foreach (json_decode((string)$z['daten'], true) ?: [] as $stueck) { $netz[] = $stueck; }
        $stand = max($stand ?? '', (string)$z['stand']);
    }
    echo json_encode(['stand' => $stand ? date('H:i', strtotime($stand)) : null, 'netz' => $netz]);
} catch (Throwable $e) {
    http_response_code(500);
    echo '{"fehler":"Datenbank"}';
}

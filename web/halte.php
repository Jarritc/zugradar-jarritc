<?php
/**
 * Bahnhöfe für die Karte: alles, was in den letzten drei Wochen von einem Zug angefahren
 * wurde (Tabelle `karte_halte`, gefüllt von zuege.py aus den Fahrten selbst).
 *
 * rang: 3 = ICE-Halt, 2 = IC/EC, 1 = RE, 0 = RB. Danach zeigt die Karte beim Hineinzoomen
 * immer mehr Halte.
 *
 *   halte.php?r=SH,NI → {"halte":[{"n":"Elmshorn","p":[53.75,9.66],"k":1,"z":42}, …]}
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
    $st = kukas_db()->prepare('SELECT name, lat, lon, rang, fahrten FROM karte_halte
                                WHERE region IN (' . implode(',', array_fill(0, count($nur), '?')) . ')
                                ORDER BY rang DESC, fahrten DESC');
    $st->execute($nur);
    $halte = array_map(static fn(array $h): array => [
        'n' => (string)$h['name'], 'p' => [(float)$h['lat'], (float)$h['lon']],
        'k' => (int)$h['rang'], 'z' => (int)$h['fahrten'],
    ], $st->fetchAll());
    echo json_encode(['halte' => $halte], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    echo '{"fehler":"Datenbank"}';
}

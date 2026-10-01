<?php
/**
 * Welche Züge an einer Stelle der Strecke vorbeikommen, als JSON — für das Antippen
 * eines Gleises auf der Karte.
 *
 * Die Seite fragt selbst keine Schnittstelle an: Der Punkt kommt in `strecken_punkte`,
 * punkt.py (Cron jede Minute, Schleife) beantwortet ihn, hier wird kurz gewartet.
 * Punkte auf etwa 10 m gerundet; eine Antwort gilt zwei Minuten.
 *
 *   strecke.php?lat=53.7436&lon=9.6625
 *   strecke.php?lat=53.758&lon=9.670&naehe=1   Züge in der Nähe eines Standorts (Startseite):
 *       alle Gleise bis 3 km, Standort auf gut 100 m gerundet (3 Nachkommastellen)
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
$lat = filter_var($_GET['lat'] ?? null, FILTER_VALIDATE_FLOAT);
$lon = filter_var($_GET['lon'] ?? null, FILTER_VALIDATE_FLOAT);
// Nur der Kartenausschnitt (Schleswig-Holstein, Hamburg)
if ($lat === false || $lon === false || $lat < 53.3 || $lat > 55.2 || $lon < 8.0 || $lon > 11.5) {
    raus(400, ['fehler' => 'Punkt außerhalb der Karte']);
}
$naehe = !empty($_GET['naehe']);
$stellen = $naehe ? 3 : 4;
$lat = round($lat, $stellen);
$lon = round($lon, $stellen);
$schluessel = $naehe ? sprintf('n:%.3f,%.3f', $lat, $lon) : sprintf('%.4f,%.4f', $lat, $lon);

try {
    $pdo = kukas_db();
    $lies = static function () use ($pdo, $schluessel): ?array {
        $st = $pdo->prepare('SELECT * FROM strecken_punkte WHERE schluessel = ?');
        $st->execute([$schluessel]);
        return $st->fetch() ?: null;
    };
    $frisch = static fn(?array $z): bool => $z && $z['status'] !== 'offen' && $z['geholt_am'] !== null
        && strtotime((string)$z['geholt_am']) > time() - 120;

    $zeile = $lies();
    if (!$frisch($zeile)) {
        $pdo->prepare('INSERT INTO strecken_punkte (schluessel, lat, lon, status, angefragt_am)
                       VALUES (?, ?, ?, "offen", NOW())
                       ON DUPLICATE KEY UPDATE status = "offen", angefragt_am = NOW()')
            ->execute([$schluessel, $lat, $lon]);
        $bis = time() + 14;
        do {
            usleep(400000);
            $zeile = $lies();
        } while (!$frisch($zeile) && time() < $bis);
    }
    if (!$frisch($zeile)) {
        raus(200, ['status' => 'wartet', 'fehler' => 'Die Antwort kam gerade nicht rechtzeitig. Bitte nochmal antippen.']);
    }
    if ($zeile['status'] === 'fehler') {
        raus(200, ['status' => 'fehler', 'fehler' => (string)$zeile['fehler']]);
    }
    $daten = json_decode((string)$zeile['daten'], true) ?: [];
    raus(200, ['status' => 'fertig', 'stand' => date('H:i', strtotime((string)$zeile['geholt_am']))] + $daten);
} catch (Throwable $e) {
    raus(500, ['status' => 'fehler', 'fehler' => 'Datenbank: ' . $e->getMessage()]);
}

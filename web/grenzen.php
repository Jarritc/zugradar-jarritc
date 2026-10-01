<?php
/**
 * Landesgrenzen für die Karte (Tabelle `karte_grenzen`, gefüllt von karte.py --grenzen).
 *
 * Quelle: deutschlandGeoJSON auf Grundlage der Verwaltungsgebiete des Bundesamts für
 * Kartographie und Geodäsie (dl-de/by-2-0) — darf mit Quellenangabe verwendet werden.
 *
 *   grenzen.php → {"grenzen":{"Schleswig-Holstein":[[[54.5,9.0], …], …], …}}
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, max-age=86400');

if (!angemeldet()) {
    http_response_code(401);
    echo '{"fehler":"nicht angemeldet"}';
    exit;
}
try {
    $aus = [];
    foreach (kukas_db()->query('SELECT land, punkte FROM karte_grenzen ORDER BY land')->fetchAll() as $z) {
        $aus[(string)$z['land']] = json_decode((string)$z['punkte'], true) ?: [];
    }
    echo json_encode(['grenzen' => $aus], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    echo '{"fehler":"Datenbank"}';
}

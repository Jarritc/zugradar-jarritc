<?php
/**
 * Vorschläge beim Tippen eines Bahnhofs (Live-Seite), als JSON.
 *
 * Liest nur die eigene Tabelle `bahnhofsliste` — die lädt bahnhof.py --liste einmal die
 * Woche aus der Bahn-Schnittstelle. Beim Tippen geht also nichts nach außen.
 *
 *   vorschlag.php?q=hamb   → ["Hamburg Hbf", "Hamburg-Altona", …]
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, max-age=300');

if (!angemeldet()) {
    http_response_code(401);
    echo '[]';
    exit;
}

$q = trim((string)($_GET['q'] ?? ''));
if (mb_strlen($q) < 2 || mb_strlen($q) > 40) {
    echo '[]';
    exit;
}

// LIKE-Sonderzeichen entschärfen, dann: erst Namensanfang, dann Wortanfang, dann irgendwo.
// Kurze Namen und Hauptbahnhöfe zuerst — "Hamb" soll "Hamburg Hbf" liefern, nicht
// "Hamburg-Bahrenfeld Bf Bus".
$muster = addcslashes($q, '%_\\');
try {
    $st = kukas_db()->prepare(
        "SELECT name FROM bahnhofsliste
          WHERE name LIKE CONCAT(?, '%') OR name LIKE CONCAT('% ', ?, '%')
             OR name LIKE CONCAT('%-', ?, '%') OR name LIKE CONCAT('%(', ?, '%')
             OR name LIKE CONCAT('%', ?, '%')
          ORDER BY (name LIKE CONCAT(?, '%')) DESC,
                   (name LIKE '%Hbf%') DESC,
                   (name LIKE '%Bus%' OR name LIKE '%ZOB%' OR name LIKE '%Ersatzverkehr%') ASC,
                   CHAR_LENGTH(name), name
          LIMIT 8");
    $st->execute([$muster, $muster, $muster, $muster, $muster, $muster]);
    echo json_encode(array_column($st->fetchAll(), 'name'), JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    http_response_code(500);
    echo '[]';
}

<?php
/**
 * Abfahrtstafel eines Bahnhofs als JSON — für das Fenster beim Antippen auf der Karte.
 *
 * Elmshorn kommt aus der laufenden Überwachung (Tabelle `lage`), andere Bahnhöfe aus
 * `bahnhof_lage`. Ist die Tafel älter als fünf Minuten, wird sie beim Hintergrunddienst
 * (bahnhof.py) angefragt und hier kurz auf die Antwort gewartet. Die Seite fragt selbst
 * keine Schnittstelle an.
 *
 *   tafel.php?bf=Itzehoe
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
$bf = trim((string)($_GET['bf'] ?? ''));
if (!preg_match("/^[\\p{L}\\d .()\\/'-]{2,40}$/u", $bf)) {
    raus(400, ['fehler' => 'ungültiger Bahnhof']);
}

/** Nur die nächsten Abfahrten, kompakt. */
function kurz(array $fahrten): array {
    $jetzt = (int)date('G') * 60 + (int)date('i');
    $aus = [];
    foreach ($fahrten as $f) {
        $soll = (string)($f['soll'] ?? '');
        $ist = (string)($f['ist'] ?? $soll);
        if (preg_match('/^(\d{1,2}):(\d{2})/', $ist ?: $soll, $m)) {
            // Minuten seit der Abfahrt; über Mitternacht (mehr als zwölf Stunden
            // Unterschied) zählt die Uhrzeit zum nächsten Tag.
            $vorbei = $jetzt - ((int)$m[1] * 60 + (int)$m[2]);
            if ($vorbei < -720) { $vorbei += 1440; }
            if ($vorbei > 720) { $vorbei -= 1440; }
            if ($vorbei > 5) { continue; }                                       // schon weg
        }
        $aus[] = ['soll' => $soll, 'ist' => $ist, 'spaet' => (int)($f['verspaetung'] ?? 0),
                  'linie' => (string)($f['linie'] ?? ''), 'nummer' => (string)($f['nummer'] ?? ''),
                  'ziel' => (string)($f['ziel'] ?? ''), 'gleis' => (string)($f['gleis'] ?? ''),
                  'gleis_neu' => !empty($f['gleis_geaendert']), 'ausfall' => !empty($f['ausgefallen'])];
        if (count($aus) >= 14) { break; }
    }
    return $aus;
}

try {
    $pdo = kukas_db();

    if ($bf === 'Elmshorn') {
        $lage = $pdo->query('SELECT stand, daten FROM lage WHERE id = 1')->fetch();
        $daten = $lage ? (json_decode((string)$lage['daten'], true) ?: []) : [];
        raus(200, ['status' => 'fertig', 'bahnhof' => $bf,
                   'stand' => $lage ? date('H:i', strtotime((string)$lage['stand'])) : null,
                   'fahrten' => kurz($daten['fahrten'] ?? [])]);
    }

    $lies = static function () use ($pdo, $bf): ?array {
        $st = $pdo->prepare('SELECT * FROM bahnhof_lage WHERE name = ?');
        $st->execute([$bf]);
        return $st->fetch() ?: null;
    };
    $frisch = static fn(?array $z): bool => $z && $z['status'] !== 'offen' && $z['geholt_am'] !== null
        && strtotime((string)$z['geholt_am']) > time() - 300;

    $zeile = $lies();
    if (!$frisch($zeile)) {
        $pdo->prepare('INSERT INTO bahnhof_lage (name, status, angefragt_am) VALUES (?, "offen", NOW())
                       ON DUPLICATE KEY UPDATE status = "offen", angefragt_am = NOW()')->execute([$bf]);
        $bis = time() + 14;
        do {
            usleep(500000);
            $zeile = $lies();
        } while (!$frisch($zeile) && time() < $bis);
    }
    if (!$frisch($zeile)) {
        raus(200, ['status' => 'wartet', 'bahnhof' => $bf,
                   'fehler' => 'Die Abfahrten kamen gerade nicht rechtzeitig. Bitte nochmal antippen.']);
    }
    if ($zeile['status'] === 'fehler') {
        raus(200, ['status' => 'fehler', 'bahnhof' => $bf, 'fehler' => (string)$zeile['fehler']]);
    }
    $daten = json_decode((string)$zeile['daten'], true) ?: [];
    raus(200, ['status' => 'fertig', 'bahnhof' => $bf,
               'stand' => date('H:i', strtotime((string)$zeile['geholt_am'])),
               'fahrten' => kurz($daten['fahrten'] ?? [])]);
} catch (Throwable $e) {
    raus(500, ['status' => 'fehler', 'fehler' => 'Datenbank: ' . $e->getMessage()]);
}

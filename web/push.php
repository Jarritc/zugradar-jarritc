<?php
/**
 * Anmeldung eines Geräts für Push-Benachrichtigungen (JSON, nur POST).
 *
 *   aktion=anmelden   Abo speichern oder reaktivieren      — angemeldet + CSRF
 *   aktion=abmelden   Abo deaktivieren                      — angemeldet + CSRF
 *   aktion=status     ist dieses Abo bekannt und aktiv?      — angemeldet
 *   aktion=erneuern   Browser hat das Abo selbst getauscht   — ohne Sitzung, aber nur
 *                     mit der bisherigen Abo-Adresse (die kennt nur das Gerät)
 *
 * Verschickt wird hier nichts, das macht /opt/docker/kukas-zug/push.py.
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

session_start();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function antwort(int $code, array $daten): never {
    http_response_code($code);
    echo json_encode($daten, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    antwort(405, ['fehler' => 'nur POST']);
}
$ein = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($ein)) {
    antwort(400, ['fehler' => 'kein JSON']);
}
$aktion = (string)($ein['aktion'] ?? '');

/** Abo-Daten aus der Browser-Struktur (PushSubscription.toJSON()) prüfen. */
function abo(mixed $a): ?array {
    if (!is_array($a)) { return null; }
    $endpoint = (string)($a['endpoint'] ?? '');
    $p256dh   = (string)($a['keys']['p256dh'] ?? '');
    $auth     = (string)($a['keys']['auth'] ?? '');
    if (!preg_match('~^https://[^\s]{10,1000}$~', $endpoint)
        || !preg_match('/^[A-Za-z0-9_-]{80,100}$/', $p256dh)
        || !preg_match('/^[A-Za-z0-9_-]{16,32}$/', $auth)) {
        return null;
    }
    return ['endpoint' => $endpoint, 'p256dh' => $p256dh, 'auth' => $auth,
            'hash' => hash('sha256', $endpoint)];
}

$pdo = kukas_db();

if ($aktion === 'erneuern') {
    $alt = (string)($ein['alt'] ?? '');
    $neu = abo($ein['abo'] ?? null);
    if ($alt === '' || !$neu) { antwort(400, ['fehler' => 'unvollständig']); }
    $st = $pdo->prepare('UPDATE push_geraete SET endpoint_hash = ?, endpoint = ?, p256dh = ?, auth = ?,
                                zuletzt_gesehen = NOW(), aktiv = 1, fehler_folge = 0, letzter_fehler = NULL
                          WHERE endpoint_hash = ?');
    $st->execute([$neu['hash'], $neu['endpoint'], $neu['p256dh'], $neu['auth'], hash('sha256', $alt)]);
    antwort($st->rowCount() ? 200 : 404, ['ok' => (bool)$st->rowCount()]);
}

if (!angemeldet()) {
    antwort(401, ['fehler' => 'nicht angemeldet']);
}

if ($aktion === 'status') {
    $a = abo($ein['abo'] ?? null);
    if (!$a) { antwort(200, ['aktiv' => false]); }
    $st = $pdo->prepare('SELECT id, name, aktiv FROM push_geraete WHERE endpoint_hash = ?');
    $st->execute([$a['hash']]);
    $g = $st->fetch();
    if ($g && (int)$g['aktiv']) {
        $pdo->prepare('UPDATE push_geraete SET zuletzt_gesehen = NOW() WHERE id = ?')->execute([$g['id']]);
    }
    antwort(200, ['aktiv' => $g && (int)$g['aktiv'], 'id' => $g['id'] ?? null, 'name' => $g['name'] ?? null]);
}

if (!hash_equals((string)($_SESSION['csrf'] ?? ''), (string)($_SERVER['HTTP_X_CSRF'] ?? ''))) {
    antwort(403, ['fehler' => 'Sitzung abgelaufen — Seite neu laden']);
}

if ($aktion === 'anmelden') {
    $a = abo($ein['abo'] ?? null);
    if (!$a) { antwort(400, ['fehler' => 'ungültiges Abo']); }
    $name = trim(mb_substr(strip_tags((string)($ein['name'] ?? '')), 0, 80)) ?: 'Gerät';
    $ua   = mb_substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 400);
    // Ein bekanntes Gerät behält seinen (evtl. umbenannten) Namen.
    $pdo->prepare('INSERT INTO push_geraete (endpoint_hash, endpoint, p256dh, auth, name, user_agent,
                          aktiv, erstellt_am, zuletzt_gesehen)
                   VALUES (?,?,?,?,?,?,1,NOW(),NOW())
                   ON DUPLICATE KEY UPDATE p256dh = VALUES(p256dh), auth = VALUES(auth), aktiv = 1,
                          user_agent = VALUES(user_agent), zuletzt_gesehen = NOW(),
                          fehler_folge = 0, letzter_fehler = NULL')
        ->execute([$a['hash'], $a['endpoint'], $a['p256dh'], $a['auth'], $name, $ua]);
    $st = $pdo->prepare('SELECT id, name FROM push_geraete WHERE endpoint_hash = ?');
    $st->execute([$a['hash']]);
    $g = $st->fetch();
    // Begrüßung, damit man sofort sieht, dass es klappt.
    $pdo->prepare("INSERT INTO push_warteschlange (geraet_id, titel, text, url, erstellt_am)
                   VALUES (?, 'Zugradar', ?, NULL, NOW())")
        ->execute([$g['id'], 'Benachrichtigungen sind auf „' . $g['name'] . '“ eingeschaltet.']);
    antwort(200, ['ok' => true, 'id' => $g['id'], 'name' => $g['name']]);
}

if ($aktion === 'abmelden') {
    $a = abo($ein['abo'] ?? null);
    if (!$a) { antwort(400, ['fehler' => 'ungültiges Abo']); }
    $pdo->prepare('UPDATE push_geraete SET aktiv = 0 WHERE endpoint_hash = ?')->execute([$a['hash']]);
    antwort(200, ['ok' => true]);
}

antwort(400, ['fehler' => 'unbekannte Aktion']);

<?php
/**
 * Knöpfe in der Benachrichtigung: „Nicht mehr beobachten“ und „Merken“.
 *
 * Der Service Worker ruft das ohne offene Seite auf, deshalb reicht hier ein signierter
 * Auftrag (gleiches Verfahren wie die Merk-Links in den Mails, siehe
 * marschbahn.aktion_token). Ohne gültige Signatur passiert nichts.
 *
 *   aktion.php?t=<nutzlast>.<signatur>          JSON-Antwort für den Service Worker
 *   aktion.php?t=…&seite=1                      Bestätigungsseite im Browser
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

$alsSeite = isset($_GET['seite']);
if (!$alsSeite) {
    header('Content-Type: application/json; charset=utf-8');
}
header('Cache-Control: no-store');

/** Auftrag aus dem Token, oder null. */
function auftrag(string $token): ?array {
    $teile = explode('.', $token);
    if (count($teile) !== 2) { return null; }
    [$nutz, $sig] = $teile;
    $erwartet = rtrim(strtr(base64_encode(hash_hmac('sha256', $nutz, MERKEN_GEHEIMNIS, true)), '+/', '-_'), '=');
    if (!hash_equals($erwartet, $sig)) { return null; }
    $roh = base64_decode(strtr($nutz, '-_', '+/'), true);
    if ($roh === false) { return null; }
    $felder = explode('|', $roh);
    if (count($felder) !== 4 || !in_array($felder[0], ['merken', 'entfernen'], true)
        || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $felder[1])) {
        return null;
    }
    return ['aktion' => $felder[0], 'tag' => $felder[1], 'lok' => $felder[2], 'zug' => $felder[3]];
}

$a = auftrag((string)($_GET['t'] ?? ''));
$ergebnis = ['ok' => false, 'text' => 'Ungültiger oder abgelaufener Auftrag.'];

if ($a) {
    try {
        $pdo = kukas_db();
        if ($a['aktion'] === 'entfernen') {
            $pdo->prepare("UPDATE interesse SET aktiv = 0, archiviert_am = NOW(), archiv_grund = 'entfernt'
                            WHERE tag = ? AND lok = ? AND zug = ? AND aktiv = 1")
                ->execute([$a['tag'], $a['lok'], $a['zug']]);
            $ergebnis = ['ok' => true, 'text' => $a['lok'] . ' ' . $a['zug'] . ' wird nicht mehr beobachtet.'];
        } else {
            $st = $pdo->prepare('SELECT von_halt, von_zeit, nach_halt, nach_zeit FROM umlaeufe
                                  WHERE tag = ? AND lok = ? AND zug = ? ORDER BY von_zeit LIMIT 1');
            $st->execute([$a['tag'], $a['lok'], $a['zug']]);
            $f = $st->fetch() ?: [];
            $pdo->prepare("INSERT INTO interesse (tag, lok, zug, von_halt, von_zeit, nach_halt, nach_zeit,
                                  quelle, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,?,'push',1,NOW())
                           ON DUPLICATE KEY UPDATE aktiv = 1, archiviert_am = NULL, archiv_grund = NULL")
                ->execute([$a['tag'], $a['lok'], $a['zug'], $f['von_halt'] ?? null, $f['von_zeit'] ?? null,
                           $f['nach_halt'] ?? null, $f['nach_zeit'] ?? null]);
            $ergebnis = ['ok' => true, 'text' => $a['lok'] . ' ' . $a['zug'] . ' wird jetzt beobachtet.'];
        }
    } catch (Throwable $e) {
        $ergebnis = ['ok' => false, 'text' => 'Datenbank: ' . $e->getMessage()];
    }
}

if (!$alsSeite) {
    http_response_code($ergebnis['ok'] ? 200 : 400);
    echo json_encode($ergebnis, JSON_UNESCAPED_UNICODE);
    exit;
}

require __DIR__ . '/_layout.php';
session_start();
seiten_kopf('Zugradar', 'start');
?>
<section>
  <div class="box ueber">
    <img src="icon-192.png" alt="" width="84" height="84">
    <div class="ueber-titel"><?= $ergebnis['ok'] ? 'Erledigt' : 'Nicht erledigt' ?></div>
    <div class="ueber-sub"><?= htmlspecialchars($ergebnis['text'], ENT_QUOTES, 'UTF-8') ?></div>
    <div class="ueber-von"><a href="./">Zur Übersicht</a></div>
  </div>
</section>
<?php
seiten_fuss('start');

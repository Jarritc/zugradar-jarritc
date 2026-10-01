<?php
/**
 * Ziel der Links "Diese Fahrt interessiert mich" aus den Mails.
 *
 * Braucht kein Passwort: Der Link ist mit dem Schlüssel aus _kukas.php signiert
 * und trägt nur Tag, Lok und Zug. Mehr als eine Fahrt vormerken kann man damit
 * nicht, und einen gültigen Link kann nur der Wächter selbst erzeugen.
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';

function h(?string $s): string { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

$ziel   = merken_pruefen((string)($_GET['t'] ?? ''));
$fehler = '';
$fahrt  = null;

if (!$ziel) {
    http_response_code(400);
    $fehler = 'Dieser Link ist ungültig oder beschädigt.';
} elseif ($ziel['tag'] < date('Y-m-d')) {
    $fehler = 'Diese Fahrt liegt in der Vergangenheit.';
} else {
    try {
        $pdo = kukas_db();
        // Einzelheiten aus dem aktuellen Stand; steht die Fahrt nicht mehr drin,
        // wird sie trotzdem vorgemerkt — dann meldet der Wächter genau das.
        $stmt = $pdo->prepare('SELECT von_halt, von_zeit, nach_halt, nach_zeit FROM umlaeufe
                                WHERE tag = ? AND lok = ? AND zug = ? ORDER BY von_zeit LIMIT 1');
        $stmt->execute([$ziel['tag'], $ziel['lok'], $ziel['zug']]);
        $fahrt = $stmt->fetch() ?: [];
        $pdo->prepare('INSERT INTO interesse (tag, lok, zug, von_halt, von_zeit, nach_halt, nach_zeit,
                              quelle, aktiv, erstellt_am)
                       VALUES (?,?,?,?,?,?,?,\'mail\',1,NOW())
                       ON DUPLICATE KEY UPDATE aktiv = 1, archiviert_am = NULL, archiv_grund = NULL')
            ->execute([$ziel['tag'], $ziel['lok'], $ziel['zug'], $fahrt['von_halt'] ?? null,
                       $fahrt['von_zeit'] ?? null, $fahrt['nach_halt'] ?? null, $fahrt['nach_zeit'] ?? null]);
    } catch (Throwable $e) {
        http_response_code(500);
        $fehler = 'Konnte nicht gespeichert werden.';
    }
}
?><!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Fahrt vorgemerkt</title>
<link rel="icon" href="favicon.ico" sizes="any"><link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="theme-color" content="#8f2029">
<style>
  :root { color-scheme: light dark; --bg:#f2f4f7; --card:#fff; --line:#e2e6eb; --fg:#151b23;
          --muted:#5b6674; --ok:#0a5c2e; --okbg:#dff5e6; --warn:#8f3610; --warnbg:#fdefe6; --link:#0a66d0; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0c1117; --card:#161c24; --line:#262e39;
          --fg:#e6edf3; --muted:#93a1b1; --ok:#63d98d; --okbg:#0f2c1d; --warn:#ffb491; --warnbg:#3a1d12; --link:#5aa3f7; } }
  body { margin:0; min-height:100dvh; display:grid; place-items:center; padding:20px;
         background:var(--bg); color:var(--fg); font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; }
  .karte { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:24px; width:min(420px,100%); }
  .zeichen { font-size:32px; }
  h1 { font-size:20px; margin:6px 0 10px; }
  .gut { background:var(--okbg); color:var(--ok); border-radius:10px; padding:12px 14px; margin-bottom:14px; }
  .schlecht { background:var(--warnbg); color:var(--warn); border-radius:10px; padding:12px 14px; margin-bottom:14px; }
  p { color:var(--muted); font-size:14px; }
  a.knopf { display:inline-block; background:var(--link); color:#fff; text-decoration:none;
            padding:11px 18px; border-radius:10px; font-weight:600; margin-top:6px; }
</style></head><body><div class="karte">
<?php if ($fehler): ?>
  <div class="zeichen">⚠️</div><h1>Nicht vorgemerkt</h1>
  <div class="schlecht"><?= h($fehler) ?></div>
<?php else: ?>
  <img src="icon-192.png" alt="" width="64" height="64"><h1>Fahrt wird beobachtet</h1>
  <div class="gut"><strong><?= h($ziel['lok']) ?></strong> · <?= h($ziel['zug']) ?><br>
    <?= $fahrt ? h($fahrt['von_halt'] . ' ' . $fahrt['von_zeit'] . ' → ' . $fahrt['nach_halt'] . ' ' . $fahrt['nach_zeit']) : '' ?><br>
    <?= h(date('d.m.Y', strtotime($ziel['tag']))) ?></div>
  <p>Diese Fahrt wird jetzt engmaschig nachgeprüft: ab 5 Stunden vor der Abfahrt alle 10 Minuten,
     ab 2 Stunden vorher alle 5 Minuten. Du bekommst eine Mail, wenn sie gestrichen wird, sich die
     Zeiten ändern, eine andere Lok sie übernimmt, sie ab 5 Minuten verspätet ist, ausfällt oder das
     Gleis wechselt.</p>
<?php endif; ?>
  <a class="knopf" href="./">Zur Übersicht</a>
</div></body></html>

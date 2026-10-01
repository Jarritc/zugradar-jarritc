<?php
/**
 * Geräte mit Push-Benachrichtigungen: Übersicht, Testnachrichten, umbenennen, abschalten.
 * Nur angemeldet (gleiche Sitzung wie die Übersicht). Nachrichten landen in
 * push_warteschlange; push.py stellt sie innerhalb weniger Sekunden zu.
 */
declare(strict_types=1);
require __DIR__ . '/_kukas.php';
require __DIR__ . '/_layout.php';

session_start();
if (!angemeldet()) {
    header('Location: ./');
    exit;
}
if (empty($_SESSION['csrf'])) {
    $_SESSION['csrf'] = bin2hex(random_bytes(16));
}

function h(?string $s): string {
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

$pdo = kukas_db();

if ($_SERVER['REQUEST_METHOD'] === 'POST'
        && hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? ''))) {
    $aktion = (string)($_POST['aktion'] ?? '');
    $id     = (int)($_POST['id'] ?? 0);
    $titel  = trim(mb_substr((string)($_POST['titel'] ?? ''), 0, 120)) ?: 'Zugradar · Test';
    $text   = trim(mb_substr((string)($_POST['text'] ?? ''), 0, 500))
              ?: 'Testnachricht von ' . date('H:i:s') . ' Uhr — kommt an ✓';
    $neu    = $pdo->prepare('INSERT INTO push_warteschlange (geraet_id, titel, text, url, erstellt_am)
                             VALUES (?, ?, ?, NULL, NOW())');
    /** Testnachricht in die Warteschlange und in den Verlauf der App. */
    $merken = static function (?int $geraet, string $titel, string $text) use ($pdo, $neu): void {
        $neu->execute([$geraet, $titel, $text]);
        $pdo->prepare("INSERT INTO meldungen_log (art, betreff, text, kanaele, mails, push_ok,
                              push_gesamt, warteschlange_id, erstellt_am)
                       VALUES ('test', ?, ?, 'push', 0, NULL, NULL, ?, NOW())")
            ->execute([$titel, $text, (int)$pdo->lastInsertId()]);
    };
    switch ($aktion) {
        case 'test':
            $merken($id, $titel, $text);
            break;
        case 'alle':
            $merken(null, $titel, $text);
            break;
        case 'umbenennen':
            $name = trim(mb_substr(strip_tags((string)($_POST['name'] ?? '')), 0, 80));
            if ($name !== '') {
                $pdo->prepare('UPDATE push_geraete SET name = ? WHERE id = ?')->execute([$name, $id]);
            }
            break;
        case 'aus':
            $pdo->prepare('UPDATE push_geraete SET aktiv = 0 WHERE id = ?')->execute([$id]);
            break;
        case 'an':
            $pdo->prepare('UPDATE push_geraete SET aktiv = 1, fehler_folge = 0 WHERE id = ?')->execute([$id]);
            break;
        case 'loeschen':
            $pdo->prepare('DELETE FROM push_geraete WHERE id = ?')->execute([$id]);
            break;
    }
    header('Location: geraete.php' . (in_array($aktion, ['test', 'alle'], true) ? '#verlauf' : ''));
    exit;
}

$vapid = '';
$geraete = [];
$verlauf = [];
$dbFehler = null;
try {
    $vapid   = (string)($pdo->query("SELECT wert FROM meta WHERE schluessel = 'vapid_public'")->fetchColumn() ?: '');
    $geraete = $pdo->query('SELECT * FROM push_geraete ORDER BY aktiv DESC, zuletzt_gesehen DESC, id DESC')->fetchAll();
    $verlauf = $pdo->query('SELECT w.*, g.name FROM push_warteschlange w
                             LEFT JOIN push_geraete g ON g.id = w.geraet_id
                             ORDER BY w.id DESC LIMIT 25')->fetchAll();
} catch (Throwable $e) {
    $dbFehler = $e->getMessage();
}
$offen = array_filter($verlauf, static fn($v) => $v['verschickt_am'] === null);

/** "vor 5 min", "gestern 14:02", "12.09. 08:15" */
function wann(?string $zeit): string {
    if (!$zeit) { return '–'; }
    $t = strtotime($zeit);
    $d = time() - $t;
    if ($d < 60)    { return 'gerade eben'; }
    if ($d < 3600)  { return 'vor ' . intdiv($d, 60) . ' min'; }
    if (date('Y-m-d', $t) === date('Y-m-d')) { return 'heute ' . date('H:i', $t); }
    if (date('Y-m-d', $t) === date('Y-m-d', strtotime('-1 day'))) { return 'gestern ' . date('H:i', $t); }
    return date('d.m. H:i', $t);
}

/** Push-Dienst aus der Abo-Adresse — verrät, welcher Hersteller zustellt. */
function dienst(string $endpoint): string {
    $host = (string)parse_url($endpoint, PHP_URL_HOST);
    return match (true) {
        str_contains($host, 'googleapis.com') => 'Google (FCM)',
        str_contains($host, 'push.apple.com') => 'Apple',
        str_contains($host, 'mozilla')        => 'Mozilla',
        str_contains($host, 'windows.com'),
        str_contains($host, 'notify.windows') => 'Microsoft',
        default                               => $host,
    };
}

function knopf(string $aktion, int $id, string $beschriftung, string $klasse = 'knopf-klein', string $frage = ''): string {
    return '<form method="post" class="inline"' . ($frage ? ' onsubmit="return confirm(\'' . h($frage) . '\')"' : '') . '>'
         . '<input type="hidden" name="csrf" value="' . h($_SESSION['csrf']) . '">'
         . '<input type="hidden" name="aktion" value="' . h($aktion) . '">'
         . '<input type="hidden" name="id" value="' . $id . '">'
         . '<button type="submit" class="' . h($klasse) . '">' . h($beschriftung) . '</button></form>';
}

$eigenCss = <<<'CSS'
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:26px 0 10px 2px; font-weight:600; }
  .geraet { padding:14px 16px; border-bottom:1px solid var(--line); }
  .geraet:last-child { border-bottom:0; }
  .geraet.aus { opacity:.6; }
  .kopf { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
  .name { font-weight:700; font-size:16px; }
  .chip { font-size:12px; padding:3px 10px; border-radius:20px; background:var(--neutral-bg); color:var(--neutral-fg); }
  .chip.gut { background:var(--ok-bg); color:var(--ok-fg); font-weight:600; }
  .chip.warn { background:var(--warn-bg); color:var(--warn-fg); font-weight:600; }
  .daten { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:4px 16px;
           font-size:13px; color:var(--muted); margin:8px 0 10px; }
  .daten b { color:var(--fg); font-weight:600; }
  .fehler { font-size:13px; color:var(--warn-fg); background:var(--warn-bg); border-radius:8px; padding:6px 10px; margin-bottom:10px; word-break:break-word; }
  .aktionen { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  form.inline { display:inline; margin:0; }
  .knopf-klein { font:inherit; font-size:13px; padding:7px 12px; border-radius:20px; cursor:pointer; min-height:36px;
                 border:1px solid var(--line); background:var(--card); color:var(--link); white-space:nowrap; }
  .knopf-klein.haupt { background:var(--akzent); color:#fff; border-color:transparent; font-weight:600; }
  .knopf-klein.gefahr { color:var(--warn); }
  .umbenennen { display:flex; gap:6px; }
  input[type=text], textarea { font:inherit; font-size:15px; padding:9px 11px; border:1px solid var(--line);
                               border-radius:10px; background:var(--bg); color:var(--fg); width:100%; }
  .umbenennen input { max-width:220px; font-size:14px; padding:6px 10px; }
  .senden { padding:16px; display:grid; gap:10px; }
  .senden label { font-size:13px; color:var(--muted); }
  details summary { cursor:pointer; color:var(--link); font-size:13px; margin-top:8px; }
  .ua { font-size:12px; color:var(--muted); word-break:break-all; margin-top:6px; }
  .erklaerung { color:var(--muted); font-size:13px; line-height:1.6; margin-top:24px; }
  .kopf-zurueck { display:inline-block; margin-bottom:6px; font-size:14px; text-decoration:none; padding:4px 0; }
  .geraete-verlauf td { vertical-align:top; }
CSS;
seiten_kopf('Benachrichtigungen', 'mehr', $eigenCss, $vapid);
?>
<a class="kopf-zurueck" href="./?s=mehr">← Mehr</a>
<?php if ($dbFehler): ?>
  <div class="box"><div class="fehler" style="margin:12px">Datenbank: <?= h($dbFehler) ?></div></div>
<?php endif; ?>

<h2>Benachrichtigungen · dieses Gerät</h2>
<div id="push-bereich" class="app-bereich"></div>

<h2>Alle Geräte (<?= count(array_filter($geraete, static fn($g) => (int)$g['aktiv'])) ?> aktiv)</h2>
<div class="box">
<?php if (!$geraete): ?>
  <div class="leer">Noch kein Gerät angemeldet. Auf dem Handy die App öffnen und oben
    „Benachrichtigungen einschalten“ antippen.</div>
<?php endif; ?>
<?php foreach ($geraete as $g):
    $aktiv = (int)$g['aktiv'];
    $zustand = !$aktiv ? ['aus', ''] : ((int)$g['fehler_folge'] > 0 ? ['Fehler', 'warn'] : ['aktiv', 'gut']); ?>
  <div class="geraet<?= $aktiv ? '' : ' aus' ?>" data-endpoint-hash="<?= h((string)$g['endpoint_hash']) ?>">
    <div class="kopf">
      <span class="name"><?= h((string)$g['name']) ?></span>
      <span class="chip <?= $zustand[1] ?>"><?= $zustand[0] ?></span>
      <span class="chip">#<?= (int)$g['id'] ?> · <?= h(dienst((string)$g['endpoint'])) ?></span>
    </div>
    <div class="daten">
      <span>Angemeldet <b><?= h(wann((string)$g['erstellt_am'])) ?></b></span>
      <span>Zuletzt gesehen <b><?= h(wann($g['zuletzt_gesehen'])) ?></b></span>
      <span>Zuletzt zugestellt <b><?= h(wann($g['zuletzt_ok'])) ?></b></span>
    </div>
    <?php if ($g['letzter_fehler']): ?>
      <div class="fehler"><?= h((string)$g['letzter_fehler']) ?>
        <?= (int)$g['fehler_folge'] > 1 ? ' (' . (int)$g['fehler_folge'] . '× in Folge)' : '' ?></div>
    <?php endif; ?>
    <div class="aktionen">
      <?php if ($aktiv): ?>
        <?= knopf('test', (int)$g['id'], '🔔 Test senden', 'knopf-klein haupt') ?>
        <?= knopf('aus', (int)$g['id'], 'Ausschalten') ?>
      <?php else: ?>
        <?= knopf('an', (int)$g['id'], 'Wieder einschalten') ?>
      <?php endif; ?>
      <?= knopf('loeschen', (int)$g['id'], 'Löschen', 'knopf-klein gefahr', 'Gerät „' . $g['name'] . '“ löschen?') ?>
    </div>
    <details>
      <summary>Umbenennen · Details</summary>
      <form method="post" class="umbenennen" style="margin-top:8px">
        <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
        <input type="hidden" name="aktion" value="umbenennen">
        <input type="hidden" name="id" value="<?= (int)$g['id'] ?>">
        <input type="text" name="name" value="<?= h((string)$g['name']) ?>" maxlength="80" aria-label="Name">
        <button class="knopf-klein" type="submit">Speichern</button>
      </form>
      <div class="ua"><?= h((string)$g['user_agent']) ?></div>
    </details>
  </div>
<?php endforeach; ?>
</div>

<h2>Nachricht an alle aktiven Geräte</h2>
<div class="box">
  <form method="post" class="senden">
    <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
    <input type="hidden" name="aktion" value="alle">
    <label>Titel <input type="text" name="titel" maxlength="120" placeholder="Zugradar · Test"></label>
    <label>Text <textarea name="text" rows="2" maxlength="500" placeholder="Testnachricht — kommt an ✓"></textarea></label>
    <div><button class="knopf-klein haupt" type="submit">An alle senden</button></div>
  </form>
</div>

<h2 id="verlauf">Verlauf<?= $offen ? ' · wird zugestellt …' : '' ?></h2>
<div class="box">
<?php if (!$verlauf): ?>
  <div class="leer">Noch nichts verschickt.</div>
<?php else: ?>
  <table class="geraete-verlauf">
    <thead><tr><th>Zeit</th><th>An</th><th>Nachricht</th><th>Ergebnis</th></tr></thead>
    <tbody>
    <?php foreach ($verlauf as $v): ?>
      <tr>
        <td class="nowrap dim" data-l="Zeit"><?= h(date('d.m. H:i:s', strtotime((string)$v['erstellt_am']))) ?></td>
        <td data-l="An"><?= $v['geraet_id'] === null ? 'alle' : h((string)($v['name'] ?? '#' . $v['geraet_id'])) ?></td>
        <td class="voll" data-l="Nachricht"><b><?= h((string)$v['titel']) ?></b><br><span class="dim"><?= h((string)$v['text']) ?></span></td>
        <td data-l="Ergebnis"><?= $v['verschickt_am'] === null ? '<span class="dim">wartet …</span>' : h((string)$v['ergebnis']) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
<?php endif; ?>
</div>

<p class="erklaerung">
  „Zugestellt“ heißt: der Push-Dienst (Google, Apple, Mozilla …) hat die Nachricht angenommen.
  Ob sie angezeigt wird, entscheidet danach das Handy — bei ausgeschaltetem Gerät kommt sie,
  sobald es wieder online ist (bis zu 12 Stunden). Meldet der Dienst, dass es das Abo nicht mehr
  gibt, wird das Gerät automatisch ausgeschaltet.
</p>

<?php
seiten_fuss('mehr', '<script src="push-client.js?v=2"></script>
<script>
ZugradarPush.knopf(document.getElementById(\'push-bereich\'), () => setTimeout(() => location.reload(), 800));
' . ($offen ? 'setTimeout(() => location.reload(), 3000);   // bis alles zugestellt ist
' : '') . '</script>');

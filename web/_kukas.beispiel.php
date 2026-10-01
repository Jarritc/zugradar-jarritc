<?php
/**
 * Gemeinsame Einstellungen für die Kukas-Seiten. Wird eingebunden, nicht aufgerufen —
 * ein direkter Abruf gibt nichts aus.
 *
 * MERKEN_GEHEIMNIS muss mit "merken.geheimnis" in /opt/docker/kukas-zug/config.json
 * übereinstimmen: Der Wächter signiert damit die Links in den Mails, diese Seite prüft sie.
 */
declare(strict_types=1);

const DB_HOST = 'localhost';
const DB_NAME = 'zugradar';
const DB_USER = 'zugradar';
const DB_PASS = 'HIER EINTRAGEN';   // wie db.password in config.json

// Gültige Passwörter stehen als Hash in PW_HASHES; Leerzeichen und
// Bindestriche werden vor dem Vergleich entfernt.
const PW_HASHES = [
    // php -r 'echo password_hash("deinPasswort", PASSWORD_DEFAULT);'
    '$2y$12$HIER.EINEN.HASH.EINTRAGEN',
];

/** Passwort prüfen — Leerzeichen und Striche jeder Art sind egal. */
function passwort_ok(string $eingabe): bool {
    $sauber = preg_replace('/[\s\x{2010}-\x{2015}\-]+/u', '', $eingabe) ?? '';
    foreach (PW_HASHES as $hash) {
        if (password_verify($sauber, $hash)) { return true; }
    }
    return false;
}

const MERKEN_GEHEIMNIS = 'HIER 64 HEX-ZEICHEN EINTRAGEN';   // php -r 'echo bin2hex(random_bytes(32));'

date_default_timezone_set('Europe/Berlin');

// ---------------------------------------------------------------- Angemeldet bleiben
// Nach der Anmeldung bekommt das Gerät ein signiertes Cookie für ein Jahr; die App
// fragt dann nicht bei jedem Öffnen nach dem Passwort. Das Cookie enthält nur das
// Ablaufdatum und eine Signatur, kein Passwort.
// LOGIN_VERSION erhöhen = alle Geräte sind abgemeldet (z. B. nach Passwortwechsel).
const LOGIN_COOKIE  = 'zugradar_login';
const LOGIN_PFAD    = '/zugradar/';
const LOGIN_TAGE    = 365;
const LOGIN_VERSION = 1;

function login_signatur(int $bis): string {
    return hash_hmac('sha256', 'login|' . $bis . '|' . LOGIN_VERSION, MERKEN_GEHEIMNIS);
}

function login_cookie_setzen(int $bis): void {
    setcookie(LOGIN_COOKIE, $bis . '.' . login_signatur($bis), [
        'expires' => $bis, 'path' => LOGIN_PFAD, 'secure' => true,
        'httponly' => true, 'samesite' => 'Lax',
    ]);
}

/** Nach erfolgreicher Passworteingabe. */
function login_merken(): void {
    $_SESSION['kukas'] = true;
    login_cookie_setzen(time() + LOGIN_TAGE * 86400);
}

/** Ablaufzeit eines gültigen Merk-Cookies, sonst null. Auch für das alte
 *  marschbahn_login-Cookie — /marschbahn/index.php übernimmt es damit. */
function login_cookie_gueltig(string $wert): ?int {
    $teile = explode('.', $wert, 2);
    if (count($teile) !== 2 || !ctype_digit($teile[0])) {
        return null;
    }
    $bis = (int)$teile[0];
    return ($bis >= time() && hash_equals(login_signatur($bis), $teile[1])) ? $bis : null;
}

/** Sitzung aktiv oder gültiges Cookie? Stellt die Sitzung aus dem Cookie wieder her. */
function angemeldet(): bool {
    if (!empty($_SESSION['kukas'])) {
        return true;
    }
    $bis = login_cookie_gueltig((string)($_COOKIE[LOGIN_COOKIE] ?? ''));
    if ($bis === null) {
        return false;
    }
    session_regenerate_id(true);
    $_SESSION['kukas'] = true;
    // Wer die App nutzt, bleibt angemeldet: das Cookie wird wieder auf ein Jahr verlängert.
    if ($bis - time() < (LOGIN_TAGE - 30) * 86400) {
        login_cookie_setzen(time() + LOGIN_TAGE * 86400);
    }
    return true;
}

function abmelden(): void {
    setcookie(LOGIN_COOKIE, '', ['expires' => 1, 'path' => LOGIN_PFAD, 'secure' => true,
                                 'httponly' => true, 'samesite' => 'Lax']);
    $_SESSION = [];
    session_destroy();
}

function kukas_db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO('mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4',
            DB_USER, DB_PASS,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
             PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]);
    }
    return $pdo;
}

/** Nutzlast "tag|lok|zug" aus einem signierten Mail-Link, oder null bei falscher Signatur. */
function merken_pruefen(string $token): ?array {
    $teile = explode('.', $token);
    if (count($teile) !== 2) { return null; }
    [$nutz, $sig] = $teile;
    $erwartet = rtrim(strtr(base64_encode(hash_hmac('sha256', $nutz, MERKEN_GEHEIMNIS, true)), '+/', '-_'), '=');
    if (!hash_equals($erwartet, $sig)) { return null; }
    $roh = base64_decode(strtr($nutz, '-_', '+/'), true);
    if ($roh === false) { return null; }
    $felder = explode('|', $roh);
    if (count($felder) !== 3 || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $felder[0])) { return null; }
    return ['tag' => $felder[0], 'lok' => $felder[1], 'zug' => $felder[2]];
}

/** Farben, Schrift und Grundlayout — gemeinsam für alle Seiten der App. */
function grundstil(): string {
    return <<<CSS
  :root {
    color-scheme: light dark;
    --bg:#f2f4f7; --card:#ffffff; --line:#e2e6eb; --fg:#151b23; --muted:#5b6674;
    --link:#0a66d0; --akzent:#0a66d0;
    --ok-bg:#dff5e6; --ok-fg:#0a5c2e;
    --neutral-bg:#eef1f5; --neutral-fg:#5b6674;
    --warn:#c8342b; --warn-bg:#fdefe6; --warn-fg:#8f3610;
    --schatten:0 1px 2px rgba(16,24,40,.05), 0 4px 14px rgba(16,24,40,.05);
    --radius:14px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0c1117; --card:#161c24; --line:#262e39; --fg:#e6edf3; --muted:#93a1b1;
      --link:#5aa3f7; --akzent:#5aa3f7;
      --ok-bg:#0f2c1d; --ok-fg:#63d98d;
      --neutral-bg:#1e252e; --neutral-fg:#93a1b1;
      --warn:#ff8177; --warn-bg:#3a1d12; --warn-fg:#ffb491;
      --schatten:0 1px 2px rgba(0,0,0,.4);
    }
  }
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body {
    margin:0; background:var(--bg); color:var(--fg);
    font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
    padding-bottom: env(safe-area-inset-bottom);
  }
  a { color:var(--link); }
  /* Ohne das übersteuert jede eigene display-Regel das hidden-Attribut — dann bleiben
     versteckte Sachen sichtbar (die Offline-Marke stand deshalb dauerhaft da). */
  [hidden] { display:none !important; }
CSS;
}

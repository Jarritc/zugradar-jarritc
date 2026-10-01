#!/usr/bin/env python3
"""
Push-Benachrichtigungen an die installierte Marschbahn-App (Web Push).

Ablauf:
  1. Auf der Webseite tippt man "Benachrichtigungen einschalten". Der Browser legt
     ein Abo beim Push-Dienst seines Herstellers an (Google, Apple, Mozilla …);
     push.php speichert Adresse und Schlüssel in push_geraete.
  2. Zum Senden wird die Nachricht mit den Schlüsseln des Geräts verschlüsselt
     (RFC 8291) und mit dem eigenen VAPID-Schlüssel signiert (RFC 8292) an diese
     Adresse geschickt. Der Push-Dienst stellt sie zu, der Service Worker (sw.js)
     zeigt sie an.

Die Webseite (PHP) verschickt selbst nichts: geraete.php legt Testnachrichten in
push_warteschlange, dieses Skript arbeitet sie ab. Der Cron startet es jede
Minute und es schaut dann eine knappe Minute lang alle paar Sekunden nach — eine
Testnachricht ist so nach wenigen Sekunden auf dem Handy.

Meldet ein Push-Dienst 404 oder 410, gibt es das Abo nicht mehr (App gelöscht,
Berechtigung entzogen); das Gerät wird dann deaktiviert.

    push.py --schluessel               VAPID-Schlüssel anlegen (einmalig) und Tabellen
    push.py --warteschlange [--sekunden 55]
    push.py --test "Text" [--geraet ID]   direkt senden, ohne Warteschlange
    push.py --liste                    Geräte anzeigen

Aus anderen Skripten:  push.an_alle(conn, cfg, titel, text, url)
Kein Paket außer python3-cryptography nötig.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import logging
import os
import re
import struct
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "push.log")
LOCK_PATH = os.path.join(BASE_DIR, "logs", "push.lock")
log = logging.getLogger("push")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS push_geraete (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        endpoint_hash    CHAR(64)     NOT NULL UNIQUE,
        endpoint         TEXT         NOT NULL,
        p256dh           VARCHAR(128) NOT NULL,
        auth             VARCHAR(64)  NOT NULL,
        name             VARCHAR(80)  NOT NULL,
        user_agent       VARCHAR(400) NULL,
        aktiv            TINYINT(1)   NOT NULL DEFAULT 1,
        erstellt_am      DATETIME     NOT NULL,
        zuletzt_gesehen  DATETIME     NULL,
        zuletzt_gesendet DATETIME     NULL,
        zuletzt_ok       DATETIME     NULL,
        letzter_fehler   VARCHAR(300) NULL,
        fehler_folge     INT          NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS push_warteschlange (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        geraet_id     INT          NULL,
        titel         VARCHAR(120) NOT NULL,
        text          VARCHAR(500) NOT NULL,
        url           VARCHAR(300) NULL,
        erstellt_am   DATETIME     NOT NULL,
        verschickt_am DATETIME     NULL,
        ergebnis      VARCHAR(300) NULL,
        KEY offen (verschickt_am)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

TTL_SEKUNDEN = 3 * 3600       # nicht zugestellt nach 3 h → verfällt beim Push-Dienst
# Kürzer, wo eine späte Zustellung nichts mehr nützt (Sekunden). Der Push-Dienst hebt eine
# Nachricht für ein ausgeschaltetes Gerät nur so lange auf; danach wirft er sie weg. So
# kommt beim Einschalten des Rechners nicht mehr der ganze Tag nachgeschoben.
TTL_JE_ART = {
    "erinnerung": 20 * 60,      # "in 30 min in Elmshorn" — danach wertlos
    "beobachtung": 30 * 60,
    "live": 15 * 60,
    "stoerung": 60 * 60,
    "entwarnung": 60 * 60,
    "ausfall_218": 2 * 3600,
    "test": 120,
    "wacht": 6 * 3600,          # Selbstüberwachung darf auch später ankommen
}
THEMA_RE = re.compile(r"[^A-Za-z0-9_-]")


def thema_aus(markierung: str | None, art: str | None) -> str | None:
    """Topic nach RFC 8030: Ein neuer Push mit demselben Thema ersetzt den noch nicht
    zugestellten alten — höchstens 32 Zeichen aus dem URL-sicheren Alphabet. Lange
    Markierungen werden gekürzt und mit ihrer Prüfsumme eindeutig gehalten (sonst ersetzen
    sich zwei verschiedene Fahrten gegenseitig)."""
    roh = markierung or art
    if not roh:
        return None
    sauber = THEMA_RE.sub("-", roh)
    if len(sauber) <= 32:
        return sauber
    kurz = hashlib.sha256(roh.encode()).hexdigest()[:10]
    return f"{sauber[:21]}-{kurz}"
AUFBEWAHRUNG_TAGE = 30        # erledigte Warteschlangen-Einträge


# ---------------------------------------------------------------- Kodierung
def b64u(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


def b64u_dekodieren(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hkdf(salt: bytes, ikm: bytes, info: bytes, laenge: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=laenge, salt=salt, info=info).derive(ikm)


def punkt(oeffentlich: ec.EllipticCurvePublicKey) -> bytes:
    return oeffentlich.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)


def verschluesseln(klartext: bytes, p256dh: str, auth: str) -> bytes:
    """Inhalt nach RFC 8291 (aes128gcm) für ein Gerät verschlüsseln."""
    ua_punkt = b64u_dekodieren(p256dh)
    auth_geheim = b64u_dekodieren(auth)
    ua_schluessel = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_punkt)

    eigener = ec.generate_private_key(ec.SECP256R1())
    as_punkt = punkt(eigener.public_key())
    geteilt = eigener.exchange(ec.ECDH(), ua_schluessel)

    ikm = hkdf(auth_geheim, geteilt, b"WebPush: info\x00" + ua_punkt + as_punkt, 32)
    salz = os.urandom(16)
    cek = hkdf(salz, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salz, ikm, b"Content-Encoding: nonce\x00", 12)

    # Ein einziger Datensatz; \x02 kennzeichnet den letzten.
    chiffrat = AESGCM(cek).encrypt(nonce, klartext + b"\x02", None)
    kopf = salz + struct.pack("!IB", 4096, len(as_punkt)) + as_punkt
    return kopf + chiffrat


# ---------------------------------------------------------------- VAPID
def vapid_privat(cfg: dict) -> ec.EllipticCurvePrivateKey:
    pem = cfg.get("push", {}).get("vapid_privat")
    if not pem:
        raise RuntimeError("Kein VAPID-Schlüssel — erst push.py --schluessel ausführen")
    return serialization.load_pem_private_key(pem.encode(), password=None)


def vapid_kopf(cfg: dict, endpoint: str) -> str:
    teile = urllib.parse.urlsplit(endpoint)
    anspruch = {
        "aud": f"{teile.scheme}://{teile.netloc}",
        "exp": int(time.time()) + 12 * 3600,
        "sub": cfg["push"].get("vapid_absender", "mailto:dein.name@example.org"),
    }
    kopf = {"typ": "JWT", "alg": "ES256"}
    signiert = (b64u(json.dumps(kopf, separators=(",", ":")).encode()) + "."
                + b64u(json.dumps(anspruch, separators=(",", ":")).encode()))
    schluessel = vapid_privat(cfg)
    r, s = decode_dss_signature(schluessel.sign(signiert.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signiert + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64u(punkt(schluessel.public_key()))}"


# ---------------------------------------------------------------- Senden
def sende_an_geraet(conn, cfg: dict, geraet: dict, titel: str, text: str,
                    url: str | None = None, markierung: str | None = None,
                    optionen: dict | None = None,
                    aktionen: list[dict] | None = None,
                    gueltig: int | None = None, thema: str | None = None) -> tuple[bool, str]:
    """Eine Nachricht an ein Gerät. Ergebnis wird am Gerät vermerkt.

    optionen steuert die Anzeige (siehe sw.js):
      festhalten  bleibt liegen, bis man sie antippt oder wegwischt
      still       aktualisiert ohne Ton und Vibration (für Live-Meldungen)
      ersetzen    False: gleiche Markierung still ersetzen, ohne erneut zu melden
    """
    nutzlast = json.dumps({
        "titel": titel, "text": text,
        "url": url or cfg.get("push", {}).get("start_url", "https://jarritc.de/zugradar/"),
        "tag": markierung,
        "optionen": optionen or {},
        "aktionen": aktionen or [],     # Knöpfe in der Meldung: {id, titel, url}
        # Bis wann die Meldung etwas taugt — sw.js zeigt später Angekommenes gesammelt an.
        "gueltig_bis": int(time.time()) + int(gueltig or TTL_SEKUNDEN),
    }, ensure_ascii=False).encode()
    kopf = {
        "Authorization": vapid_kopf(cfg, geraet["endpoint"]),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(int(gueltig or TTL_SEKUNDEN)),
        "Urgency": "high",
    }
    if thema:
        kopf["Topic"] = thema
    try:
        antwort = requests.post(
            geraet["endpoint"],
            data=verschluesseln(nutzlast, geraet["p256dh"], geraet["auth"]),
            headers=kopf,
            timeout=15,
        )
        status = antwort.status_code
        ok = 200 <= status < 300
        meldung = (f"HTTP {status} " + ("" if ok else antwort.text.strip()[:200])).strip()
    except Exception as exc:                                  # Netz, Schlüssel kaputt …
        status, ok, meldung = 0, False, f"{type(exc).__name__}: {exc}"[:300]

    abgemeldet = status in (404, 410)
    with conn.cursor() as cur:
        if ok:
            cur.execute("UPDATE push_geraete SET zuletzt_gesendet=NOW(), zuletzt_ok=NOW(), "
                        "letzter_fehler=NULL, fehler_folge=0 WHERE id=%s", (geraet["id"],))
        else:
            cur.execute("UPDATE push_geraete SET zuletzt_gesendet=NOW(), letzter_fehler=%s, "
                        "fehler_folge=fehler_folge+1, aktiv=IF(%s, 0, aktiv) WHERE id=%s",
                        (("Abo erloschen — " if abgemeldet else "") + meldung,
                         1 if abgemeldet else 0, geraet["id"]))
    conn.commit()
    log.info("Push an %s (#%s): %s", geraet["name"], geraet["id"], meldung)
    return ok, meldung


def aktive_geraete(conn, geraet_id: int | None = None) -> list[dict]:
    with conn.cursor() as cur:
        if geraet_id is None:
            cur.execute("SELECT * FROM push_geraete WHERE aktiv=1 ORDER BY id")
        else:
            cur.execute("SELECT * FROM push_geraete WHERE id=%s", (geraet_id,))
        return list(cur.fetchall())


def an_alle(conn, cfg: dict, titel: str, text: str, url: str | None = None,
            markierung: str | None = None, optionen: dict | None = None,
            aktionen: list[dict] | None = None, art: str | None = None) -> tuple[int, int]:
    """An alle aktiven Geräte. Liefert (erfolgreich, versucht).

    art bestimmt die Haltbarkeit (TTL_JE_ART) und zusammen mit der Markierung das Thema."""
    geraete = aktive_geraete(conn)
    gueltig = TTL_JE_ART.get(art or "", TTL_SEKUNDEN)
    thema = thema_aus(markierung, art)
    gut = sum(sende_an_geraet(conn, cfg, g, titel, text, url, markierung, optionen, aktionen,
                              gueltig, thema)[0]
              for g in geraete)
    return gut, len(geraete)


def warteschlange(conn, cfg: dict, sekunden: int) -> None:
    ende = time.monotonic() + sekunden
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM push_warteschlange WHERE verschickt_am IS NULL ORDER BY id LIMIT 20")
            offen = list(cur.fetchall())
        conn.commit()                  # sonst sieht die Verbindung neue Zeilen nicht
        for eintrag in offen:
            if eintrag["geraet_id"] is None:
                geraete = aktive_geraete(conn)
            else:
                geraete = aktive_geraete(conn, eintrag["geraet_id"])
            ergebnisse = [(g["name"], *sende_an_geraet(conn, cfg, g, eintrag["titel"],
                                                       eintrag["text"], eintrag["url"]))
                          for g in geraete]
            if not ergebnisse:
                zusammen = "kein aktives Gerät"
            else:
                gut = sum(1 for _, ok, _ in ergebnisse if ok)
                zusammen = f"{gut} von {len(ergebnisse)} zugestellt"
                fehler = [f"{n}: {m}" for n, ok, m in ergebnisse if not ok]
                if fehler:
                    zusammen += " · " + " · ".join(fehler)
            with conn.cursor() as cur:
                cur.execute("UPDATE push_warteschlange SET verschickt_am=NOW(), ergebnis=%s WHERE id=%s",
                            (zusammen[:300], eintrag["id"]))
            conn.commit()
        if time.monotonic() + 5 > ende:
            break
        time.sleep(5)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM push_warteschlange WHERE verschickt_am < NOW() - INTERVAL %s DAY",
                    (AUFBEWAHRUNG_TAGE,))
    conn.commit()


# ---------------------------------------------------------------- Einrichtung
def schluessel_anlegen(conn, cfg: dict) -> None:
    with conn.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)
    conn.commit()

    push = cfg.setdefault("push", {})
    if not push.get("vapid_privat"):
        neu = ec.generate_private_key(ec.SECP256R1())
        push["vapid_privat"] = neu.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        push.setdefault("vapid_absender", "mailto:dein.name@example.org")
        push.setdefault("start_url", "https://jarritc.de/zugradar/")
        speichere_config(cfg)
        print("Neuer VAPID-Schlüssel angelegt.")
    else:
        print("VAPID-Schlüssel ist schon da — bleibt unverändert (sonst wären alle Abos ungültig).")

    oeffentlich = b64u(punkt(vapid_privat(cfg).public_key()))
    mb.meta_set(conn, "vapid_public", oeffentlich)   # die Webseite liest ihn von hier
    conn.commit()
    print("Öffentlicher Schlüssel:", oeffentlich)


def speichere_config(cfg: dict) -> None:
    alt = os.stat(mb.CONFIG_PATH)
    tmp = mb.CONFIG_PATH + ".neu"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    try:
        os.chown(tmp, alt.st_uid, alt.st_gid)
    except PermissionError:
        pass
    os.replace(tmp, mb.CONFIG_PATH)


def main() -> None:
    p = argparse.ArgumentParser(description="Push-Benachrichtigungen der Marschbahn-App")
    p.add_argument("--schluessel", action="store_true", help="VAPID-Schlüssel und Tabellen anlegen")
    p.add_argument("--warteschlange", action="store_true", help="Warteschlange abarbeiten")
    p.add_argument("--sekunden", type=int, default=0, help="so lange auf neue Einträge warten")
    p.add_argument("--test", metavar="TEXT", help="Testnachricht direkt senden")
    p.add_argument("--geraet", type=int, help="nur an dieses Gerät")
    p.add_argument("--liste", action="store_true", help="Geräte anzeigen")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)

    if args.schluessel:
        schluessel_anlegen(conn, cfg)
    elif args.warteschlange:
        # Der Cron startet jede Minute; ein noch laufender Durchgang hat Vorrang.
        sperre = open(LOCK_PATH, "w")
        try:
            fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        warteschlange(conn, cfg, args.sekunden)
    elif args.test:
        geraete = aktive_geraete(conn, args.geraet)
        if not geraete:
            print("Kein aktives Gerät.")
        for g in geraete:
            ok, meldung = sende_an_geraet(conn, cfg, g, "Zugradar · Test", args.test)
            print(f"#{g['id']} {g['name']}: {'OK' if ok else 'FEHLER'} {meldung}")
    elif args.liste:
        for g in _alle(conn):
            print(f"#{g['id']:<3} {'aktiv ' if g['aktiv'] else 'aus   '} {g['name']:<28} "
                  f"seit {g['erstellt_am']:%d.%m. %H:%M}  zuletzt ok {g['zuletzt_ok'] or '–'}"
                  f"{'  Fehler: ' + g['letzter_fehler'] if g['letzter_fehler'] else ''}")
    else:
        p.print_help()


def _alle(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM push_geraete ORDER BY id")
        return list(cur.fetchall())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Aufträge aus der App ausführen: „Jetzt prüfen“ auf der Seite Quellen.

Die Webseite läuft als www-data und darf die Skripte nicht starten (Zugangsdaten
in config.json gehören dem Hintergrunddienst). Sie schreibt deshalb nur eine Zeile
in die Tabelle `auftraege`; dieses Skript arbeitet sie ab — wie bei den
Push-Nachrichten. Der Cron startet es jede Minute, es schaut knapp eine Minute lang
alle zwei Sekunden nach.

Nur die hier eingetragenen Skripte lassen sich starten (feste Liste, keine
Parameter aus der Webseite), und immer nur eines zur Zeit.

    auftrag.py --warteschlange [--sekunden 55]
    auftrag.py --setup
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "auftrag.log")
LOCK_PATH = os.path.join(BASE_DIR, "logs", "auftrag.lock")
log = logging.getLogger("auftrag")

# Was sich aus der App starten lässt: Name -> (Skript, Zusatzargumente, Klartext)
ERLAUBT = {
    "marschbahn":     ("marschbahn.py", [], "Forum: Tageslisten prüfen"),
    "stoerung":       ("stoerung.py", [], "Echtzeitlage in Elmshorn"),
    "sichtungen":     ("sichtungen.py", [], "Beiträge im Umkreis"),
    "sonderzuege":    ("sonderzuege.py", [], "Sonderzüge im Fahrplan"),
    "beobachten":     ("beobachten.py", [], "Vorgemerkte Fahrten"),
    "erinnerung":     ("erinnerung.py", [], "Erinnerungen vor der Durchfahrt"),
    "besondere_loks": ("besondere_loks.py", [], "Besondere 218er aus Wikipedia"),
    "wacht":          ("wacht.py", [], "Selbstüberwachung"),
    "zuege":          ("zuege.py", [], "Fahrende Züge für die Karte"),
}
ZEITGRENZE = 600          # ein Lauf darf höchstens zehn Minuten dauern
AUFBEWAHRUNG_TAGE = 14

# Die Webseite läuft als www-data und kommt an /opt/docker nicht heran (Rechte 770,
# darunter liegen auch andere Projekte). Statt dort zu lockern, legt dieser Dienst bei
# jedem Lauf einen Auszug der Protokolle in den App-Ordner; .htaccess sperrt den
# direkten Abruf, index.php liest ihn für die Seite "Logs".
PROTOKOLL_ZIEL = "/var/www/html/jarritc.de/zugradar/protokolle"
PROTOKOLL_ZEILEN = 400


def protokolle_spiegeln() -> None:
    quelle = os.path.join(BASE_DIR, "logs")
    try:
        os.makedirs(PROTOKOLL_ZIEL, exist_ok=True)
    except OSError as exc:
        log.warning("Protokollordner nicht anlegbar: %s", exc)
        return
    for name in sorted(os.listdir(quelle)):
        if not name.endswith(".log"):
            continue
        pfad = os.path.join(quelle, name)
        try:
            with open(pfad, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                ende = fh.tell()
                puffer = b""
                while ende > 0 and puffer.count(b"\n") <= PROTOKOLL_ZEILEN:
                    schritt = min(16384, ende)
                    ende -= schritt
                    fh.seek(ende)
                    puffer = fh.read(schritt) + puffer
            zeilen = puffer.decode("utf-8", "replace").splitlines()[-PROTOKOLL_ZEILEN:]
            kopf = f"# {name} · Stand {time.strftime('%d.%m.%Y %H:%M')} · letzte {len(zeilen)} Zeilen\n"
            with open(os.path.join(PROTOKOLL_ZIEL, name), "w", encoding="utf-8") as ziel:
                ziel.write(kopf + "\n".join(zeilen) + "\n")
        except OSError as exc:
            log.warning("%s nicht spiegelbar: %s", name, exc)

SCHEMA = """
CREATE TABLE IF NOT EXISTS auftraege (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    skript         VARCHAR(32)  NOT NULL,
    angefordert_am DATETIME     NOT NULL,
    gestartet_am   DATETIME     NULL,
    fertig_am      DATETIME     NULL,
    erfolg         TINYINT(1)   NULL,
    ausgabe        TEXT         NULL,
    KEY offen (fertig_am)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def fuehre_aus(conn, auftrag: dict) -> None:
    name = str(auftrag["skript"])
    if name not in ERLAUBT:
        with conn.cursor() as cur:
            cur.execute("UPDATE auftraege SET fertig_am=NOW(), erfolg=0, ausgabe=%s WHERE id=%s",
                        ("Unbekannter Auftrag.", auftrag["id"]))
        conn.commit()
        return

    skript, extra, klartext = ERLAUBT[name]
    with conn.cursor() as cur:
        cur.execute("UPDATE auftraege SET gestartet_am=NOW() WHERE id=%s", (auftrag["id"],))
    conn.commit()
    log.info("Starte %s (%s)", skript, klartext)

    start = time.monotonic()
    try:
        fertig = subprocess.run([sys.executable, os.path.join(BASE_DIR, skript), *extra],
                                capture_output=True, text=True, timeout=ZEITGRENZE)
        erfolg = fertig.returncode == 0
        ausgabe = (fertig.stdout + fertig.stderr).strip() or f"Fertig (Rückgabewert {fertig.returncode})."
    except subprocess.TimeoutExpired:
        erfolg, ausgabe = False, f"Abgebrochen nach {ZEITGRENZE // 60} Minuten."
    except Exception as exc:
        erfolg, ausgabe = False, f"{type(exc).__name__}: {exc}"

    dauer = round(time.monotonic() - start)
    ausgabe = f"{'Fertig' if erfolg else 'Fehlgeschlagen'} nach {dauer} s.\n" + ausgabe[-4000:]
    with conn.cursor() as cur:
        cur.execute("UPDATE auftraege SET fertig_am=NOW(), erfolg=%s, ausgabe=%s WHERE id=%s",
                    (1 if erfolg else 0, ausgabe, auftrag["id"]))
    conn.commit()
    log.info("%s: %s in %d s", skript, "ok" if erfolg else "Fehler", dauer)


def warteschlange(conn, sekunden: int) -> None:
    ende = time.monotonic() + sekunden
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM auftraege WHERE fertig_am IS NULL ORDER BY id LIMIT 3")
            offen = list(cur.fetchall())
        conn.commit()
        for auftrag in offen:
            fuehre_aus(conn, auftrag)
        if offen:
            protokolle_spiegeln()          # nach einem Lauf gleich das frische Protokoll
        if time.monotonic() + 2 > ende:
            break
        time.sleep(2)

    protokolle_spiegeln()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auftraege WHERE fertig_am < NOW() - INTERVAL %s DAY",
                    (AUFBEWAHRUNG_TAGE,))
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description="Aufträge aus der App ausführen")
    p.add_argument("--warteschlange", action="store_true")
    p.add_argument("--sekunden", type=int, default=0)
    p.add_argument("--setup", action="store_true")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()
    if args.setup:
        print("Tabelle auftraege angelegt.")
        return 0
    if args.warteschlange:
        sperre = open(LOCK_PATH, "w")
        try:
            fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0                  # es läuft schon einer
        warteschlange(conn, args.sekunden)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

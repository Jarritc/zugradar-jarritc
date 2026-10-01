#!/usr/bin/env python3
"""
Live-Abfahrten anderer Bahnhöfe — auf Anfrage aus der App.

Elmshorn schreibt stoerung.py ohnehin alle zehn Minuten fort. Für die übrigen
Bahnhöfe wäre ein fester Takt Verschwendung: Man schaut selten hin, jede Abfrage
kostet mehrere Aufrufe der Bahn-Schnittstelle. Deshalb holt dieser Dienst nur, was
in der App auch aufgeschlagen wird, und hält es GUELTIG_SEKUNDEN lang.

Die Webseite ruft die Schnittstelle nicht selbst auf (Zugangsdaten gehören dem
Hintergrunddienst): live.php trägt eine Anfrage in `bahnhof_lage` ein, dieses
Skript beantwortet sie — wie bei Wagenreihung und Aufträgen.

    bahnhof.py --warteschlange [--sekunden 55]
    bahnhof.py --abruf "Itzehoe"
    bahnhof.py --setup
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import beobachten as bo
import db_timetables as dbt
import marschbahn as mb

TZ = ZoneInfo("Europe/Berlin")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "bahnhof.log")
LOCK_PATH = os.path.join(BASE_DIR, "logs", "bahnhof.lock")
log = logging.getLogger("bahnhof")

GUELTIG_SEKUNDEN = 300          # so lange gilt eine geholte Abfahrtstafel als frisch
STUNDEN = 3
AUFBEWAHRUNG_TAGE = 3

# Schnellauswahl in der App — gesucht werden darf darüber hinaus jeder Bahnhof.
BAHNHOEFE = [
    "Elmshorn", "Pinneberg", "Tornesch", "Itzehoe", "Wrist", "Husum",
    "Niebüll", "Westerland(Sylt)", "Hamburg-Altona", "Heide(Holst)",
]

# Freie Suche: Der Name geht in die Bahn-Schnittstelle, deshalb nur Buchstaben, Ziffern
# und die in Bahnhofsnamen üblichen Zeichen — und höchstens 40 davon.
NAME_RE = re.compile(r"^[\w .()/\-']{2,40}$", re.UNICODE)


LISTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bahnhofsliste (
    eva    INT          NOT NULL PRIMARY KEY,
    name   VARCHAR(100) NOT NULL,       -- nicht eindeutig: "Münchhausen" und "Munchhausen"
    ds100  VARCHAR(16)  NULL,           -- gelten der Sortierung ci als gleich
    KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def liste_laden(conn, cfg: dict) -> int:
    """Alle Bahnhöfe der Bahn in die Tabelle bahnhofsliste — Grundlage der Vorschläge
    beim Tippen. Die Timetables-Schnittstelle liefert mit dem Muster "*" die ganze
    Liste (rund 25 000 Halte); genommen werden die mit db="true" (rund 17 000)."""
    dbcfg = cfg.get("db_api") or {}
    xml = dbt._hole(dbcfg, "station/*")
    eintraege = [dict(re.findall(r'(\w+)="([^"]*)"', m)) for m in re.findall(r"<station\b([^>]*)/?>", xml)]
    gesehen: dict[int, tuple[str, str]] = {}
    for e in eintraege:
        name = (e.get("name") or "").strip()
        if e.get("db") != "true" or not name or not e.get("eva", "").isdigit() or len(name) > 100:
            continue
        gesehen.setdefault(int(e["eva"]), (name, e.get("ds100") or None))
    if len(gesehen) < 1000:
        raise RuntimeError(f"Nur {len(gesehen)} Bahnhöfe erhalten — Liste bleibt unverändert.")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM bahnhofsliste")
        cur.executemany("INSERT INTO bahnhofsliste (eva, name, ds100) VALUES (%s,%s,%s)",
                        [(eva, n, ds) for eva, (n, ds) in gesehen.items()])
    conn.commit()
    log.info("Bahnhofsliste: %d Bahnhöfe", len(gesehen))
    return len(gesehen)


SCHEMA = """
CREATE TABLE IF NOT EXISTS bahnhof_lage (
    name         VARCHAR(64) NOT NULL PRIMARY KEY,
    eva          INT         NULL,
    status       VARCHAR(12) NOT NULL,          -- offen | fertig | fehler
    daten        MEDIUMTEXT  NULL,
    fehler       VARCHAR(300) NULL,
    angefragt_am DATETIME    NOT NULL,
    geholt_am    DATETIME    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def hole(conn, cfg: dict, name: str) -> dict:
    """Abfahrten eines Bahnhofs holen und ablegen."""
    dbcfg = cfg.get("db_api") or {}
    if not dbcfg.get("client_id"):
        raise RuntimeError("Kein Zugang zur DB-Schnittstelle hinterlegt.")
    with conn.cursor() as cur:
        cur.execute("SELECT eva FROM bahnhofsliste WHERE name = %s COLLATE utf8mb4_bin", (name,))
        treffer = cur.fetchone()
    eva = treffer["eva"] if treffer else bo.eva_fuer(conn, dbcfg, name)
    if not eva:
        raise ValueError(f"Bahnhof „{name}“ nicht gefunden — Schreibweise wie im Fahrplan, "
                         f"z. B. „Hamburg Hbf“ oder „Westerland(Sylt)“.")

    lage, meldungen = dbt.ist_lage(dbcfg, eva, stunden=STUNDEN)
    daten = {
        "fahrten": [{
            "soll": e["soll_hhmm"], "ist": e["ist_hhmm"], "linie": e["linie"],
            "nummer": e["nummer"], "ziel": e["ziel"], "gleis": e["gleis"],
            "gleis_geaendert": e["gleis_geaendert"], "verspaetung": e["verspaetung"],
            "ausgefallen": e["ausgefallen"], "ursachen": e["ursachen"],
        } for e in lage],
        "meldungen": [{"kategorie": m["kategorie"], "prioritaet": m["prioritaet"],
                       "von": m["von"].strftime("%d.%m. %H:%M") if m.get("von") else None,
                       "bis": m["bis"].strftime("%d.%m. %H:%M") if m.get("bis") else None}
                      for m in dbt.aktive_stoerungen(meldungen)],
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bahnhof_lage (name, eva, status, daten, fehler, angefragt_am, geholt_am) "
            "VALUES (%s,%s,'fertig',%s,NULL,NOW(),NOW()) "
            "ON DUPLICATE KEY UPDATE eva=VALUES(eva), status='fertig', daten=VALUES(daten), "
            "       fehler=NULL, geholt_am=NOW()",
            (name, eva, json.dumps(daten, ensure_ascii=False)))
    conn.commit()
    log.info("%s (EVA %s): %d Fahrten", name, eva, len(daten["fahrten"]))
    return daten


def warteschlange(conn, cfg: dict, sekunden: int) -> None:
    ende = time.monotonic() + sekunden
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM bahnhof_lage WHERE status='offen' ORDER BY angefragt_am LIMIT 5")
            offen = [r["name"] for r in cur.fetchall()]
        conn.commit()
        for name in offen:
            if not NAME_RE.match(name):
                with conn.cursor() as cur:
                    cur.execute("UPDATE bahnhof_lage SET status='fehler', fehler=%s, geholt_am=NOW() "
                                " WHERE name=%s", ("Ungültiger Name.", name))
                conn.commit()
                continue
            try:
                hole(conn, cfg, name)
            except Exception as exc:
                meldung = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("%s: %s", name, meldung)
                with conn.cursor() as cur:
                    cur.execute("UPDATE bahnhof_lage SET status='fehler', fehler=%s, geholt_am=NOW() "
                                " WHERE name=%s", (meldung, name))
                conn.commit()
        if time.monotonic() + 2 > ende:
            break
        time.sleep(2)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM bahnhof_lage WHERE geholt_am < NOW() - INTERVAL %s DAY",
                    (AUFBEWAHRUNG_TAGE,))
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description="Live-Abfahrten anderer Bahnhöfe")
    p.add_argument("--warteschlange", action="store_true")
    p.add_argument("--sekunden", type=int, default=0)
    p.add_argument("--abruf", metavar="BAHNHOF")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--liste", action="store_true", help="Bahnhofsliste neu laden (wöchentlich)")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(LISTE_SCHEMA)
    conn.commit()

    if args.liste:
        print("Bahnhofsliste:", liste_laden(conn, cfg), "Bahnhöfe")
        return 0
    if args.setup:
        print("Tabelle bahnhof_lage angelegt. Auswahl:", ", ".join(BAHNHOEFE))
    elif args.abruf:
        daten = hole(conn, cfg, args.abruf)
        for f in daten["fahrten"][:12]:
            print(f"{f['soll']} {f['linie']:>5} {f['nummer']:<7} → {f['ziel'][:28]:<30} "
                  f"Gleis {f['gleis'] or '–':<4} "
                  f"{'Ausfall' if f['ausgefallen'] else ('+' + str(f['verspaetung']) if f['verspaetung'] else '')}")
    elif args.warteschlange:
        sperre = open(LOCK_PATH, "w")
        try:
            fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        warteschlange(conn, cfg, args.sekunden)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

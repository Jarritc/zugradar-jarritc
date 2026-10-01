#!/usr/bin/env python3
"""
Sonderzüge durch Elmshorn — aus dem Soll-Fahrplan der DB-Schnittstelle.

Am Halt Elmshorn verkehren planmäßig nur RE, AKN und nordbahn (NBE), jeweils mit
Liniennummer (gemessen über 30 Stunden am 16.09.2026). Ein Zug mit anderer
Gattung (D, DPE, TRI …) oder ohne Linie ist deshalb ein Sonderzug.

Läuft alle 3 Stunden und schaut 30 Stunden voraus — so kommt die Meldung
rechtzeitig vorher. Der Soll-Fahrplan kommt stundenweise, das sind 31 Abrufe je
Lauf; bei 60 erlaubten Abrufen pro Minute unkritisch, aber kein Grund, es alle
10 Minuten zu tun.

Die zweite Quelle für Sonderzüge — Ankündigungen im Fahrzeiten-Forum — liegt in
sichtungen.py, weil dort die Umkreisprüfung schon vorhanden ist.

    sonderzuege.py            ein Durchlauf
    sonderzuege.py --dry-run  ohne Mailversand, merkt sich nichts
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_timetables as dbt
import marschbahn as mb

TZ = ZoneInfo("Europe/Berlin")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "sonderzuege.log")
log = logging.getLogger("sonderzuege")

REGEL_GATTUNGEN = {"RE", "RB", "AKN", "NBE", "IC", "ICE", "EC", "S"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sonderzuege (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    tag         DATE         NOT NULL,
    kategorie   VARCHAR(12)  NOT NULL,
    nummer      VARCHAR(12)  NOT NULL,
    linie       VARCHAR(16)  NULL,
    zeit        VARCHAR(8)   NULL,
    gleis       VARCHAR(8)   NULL,
    von         VARCHAR(96)  NULL,
    nach        VARCHAR(96)  NULL,
    quelle      VARCHAR(16)  NOT NULL DEFAULT 'fahrplan',
    gesehen_am  DATETIME     NOT NULL,
    gemeldet_am DATETIME     NULL,
    UNIQUE KEY uniq_zug (tag, kategorie, nummer),
    KEY idx_tag (tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ist_sonderzug(eintrag: dict, regel: set[str]) -> bool:
    return eintrag["kategorie"] not in regel or not eintrag["linie"]


def voraus(cfg: dict, stunden: int) -> list[dict]:
    """Soll-Fahrplan Elmshorn ab jetzt, Stunde für Stunde."""
    db = cfg["db_api"]
    start = dt.datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    aus = []
    for n in range(stunden + 1):
        stunde = start + dt.timedelta(hours=n)
        try:
            xml = dbt._hole(db, f"plan/{db['eva_elmshorn']}/{stunde:%y%m%d}/{stunde:%H}")
        except Exception as exc:
            log.debug("Stunde %s nicht abrufbar: %s", stunde, exc)
            continue
        import xml.etree.ElementTree as ET
        for s in ET.fromstring(xml).findall("s"):
            tl, ar, dp = s.find("tl"), s.find("ar"), s.find("dp")
            if tl is None:
                continue
            zeit = dbt._zeit((dp if dp is not None else ar).get("pt")) if (ar is not None or dp is not None) else None
            if not zeit:
                continue
            aus.append({
                "kategorie": tl.get("c", ""), "nummer": tl.get("n", ""),
                "linie": ((dp if dp is not None else ar).get("l") or ""),
                "zeitpunkt": zeit, "zeit": zeit.strftime("%H:%M"), "tag": zeit.date(),
                "gleis": (dp if dp is not None else ar).get("pp"),
                # ppth: bisherige Halte bei der Ankunft, folgende bei der Abfahrt
                "von": (ar.get("ppth", "").split("|")[0] if ar is not None else "Elmshorn"),
                "nach": (dp.get("ppth", "").split("|")[-1] if dp is not None else "Elmshorn"),
            })
        time.sleep(1.1)
    return aus


def baue_mail(zuege: list[dict]) -> tuple[str, str, str]:
    z0 = zuege[0]
    if len(zuege) == 1:
        betreff = mb.mit_datum(f"Sonderzug Elmshorn {z0['zeit']}: "
                               f"{z0['kategorie']} {z0['nummer']} → {z0['nach']}", z0["tag"])
    else:
        betreff = mb.mit_datum(f"{len(zuege)} Sonderzüge durch Elmshorn, ab {z0['zeit']}", z0["tag"])
    zeilen_t, zeilen_h = [], []
    for z in zuege:
        gl = f", Gleis {z['gleis']}" if z.get("gleis") else ""
        zeilen_t.append(f"  {mb.datum_mini(z['tag'])} {z['zeit']}  {z['kategorie']} {z['nummer']}"
                        f"  {z['von']} → {z['nach']}{gl}")
        zeilen_h.append(
            '<tr><td style="padding:8px 10px;font-weight:bold;white-space:nowrap;'
            f'border-bottom:1px solid #eaeef2">{html.escape(mb.datum_mini(z["tag"]))} {html.escape(z["zeit"])}</td>'
            f'<td style="padding:8px 10px;white-space:nowrap;border-bottom:1px solid #eaeef2">'
            f'{html.escape(z["kategorie"])} {html.escape(z["nummer"])}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eaeef2">'
            f'{html.escape(z["von"])} &rarr; {html.escape(z["nach"])}{html.escape(gl)}</td></tr>')
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n" + "\n".join(zeilen_t)
            + "\n\nLaut Fahrplan der Bahn für den Halt Elmshorn. Regelverkehr (RE, AKN, "
              "nordbahn) ist ausgenommen.\nÜbersicht: https://jarritc.de/zugradar/\n")
    body = (mb.RAHMEN_AUF
            + f'<h2 style="margin:0 0 12px;font-size:19px">{html.escape(betreff)}</h2>'
            '<table style="width:100%;border-collapse:collapse;font-size:14px">' + "".join(zeilen_h) + '</table>'
            '<p style="color:#57606a;font-size:12px;margin:16px 0 0">Laut Fahrplan der Bahn für den '
            'Halt Elmshorn. Regelverkehr (RE, AKN, nordbahn) ist ausgenommen.</p>'
            '<p style="margin:16px 0 0"><a href="https://jarritc.de/zugradar/" style="color:#0969da;'
            'text-decoration:none;font-size:14px">Übersicht</a></p>' + mb.RAHMEN_ZU)
    return betreff, body, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    sz = cfg.get("sonderzuege") or {}
    if not sz.get("aktiv", True) or not (cfg.get("db_api") or {}).get("client_id"):
        log.info("Sonderzug-Überwachung aus oder keine DB-Schnittstelle.")
        return 0
    if not cfg["mail"].get("verify_tls", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    regel = set(sz.get("regel_gattungen") or REGEL_GATTUNGEN)
    fahrten = voraus(cfg, int(sz.get("vorausschau_stunden", 30)))
    jetzt = dt.datetime.now(TZ)
    sonder = [f for f in fahrten if ist_sonderzug(f, regel) and f["zeitpunkt"] >= jetzt]
    log.info("%d Fahrten in %s h geprüft, %d Sonderzüge", len(fahrten),
             sz.get("vorausschau_stunden", 30), len(sonder))

    conn = mb.db_connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            neu = []
            for z in sonder:
                cur.execute("SELECT id FROM sonderzuege WHERE tag=%s AND kategorie=%s AND nummer=%s",
                            (z["tag"], z["kategorie"], z["nummer"]))
                if cur.fetchone():
                    continue
                neu.append(z)
        if args.dry_run:
            for z in neu:
                log.info("dry-run, neu: %s %s %s %s → %s", z["tag"], z["zeit"], z["kategorie"], z["nummer"], z["nach"])
            return 0
        if not neu:
            return 0
        mb.lade_namen(conn)
        betreff, body, text = baue_mail(sorted(neu, key=lambda z: z["zeitpunkt"]))
        erfolge = mb.melde(cfg, "sonderzug", betreff, body, text)
        with conn.cursor() as cur:
            for z in neu:
                cur.execute(
                    "INSERT INTO sonderzuege (tag, kategorie, nummer, linie, zeit, gleis, von, nach, "
                    " gesehen_am, gemeldet_am) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (z["tag"], z["kategorie"], z["nummer"], z["linie"] or None, z["zeit"], z["gleis"],
                     z["von"][:96], z["nach"][:96], dt.datetime.now(),
                     dt.datetime.now() if erfolge else None))
        conn.commit()
        log.info("%s — %d Zustellungen", betreff, erfolge)
    finally:
        mb.herzschlag(conn, "sonderzuege")
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

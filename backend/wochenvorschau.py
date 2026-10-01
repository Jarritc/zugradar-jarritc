#!/usr/bin/env python3
"""
Sonntagsnachricht: Sonderzüge der kommenden Woche — und nur dann.

Eine Wochenvorschau auf die 218-Umläufe geht nicht: Die Tageslisten im Forum
erscheinen erst am Vorabend (gemessen: nie mehr als drei Stunden vor Mitternacht),
und der Soll-Fahrplan der Bahn reicht nur zwei Tage. Was wirklich vorher bekannt ist,
sind Sonderzüge — die kündigt jemand an oder sie stehen schon im Fahrplan.

Deshalb: sonntags nachsehen, ob für die nächsten sieben Tage etwas ansteht. Wenn ja,
eine Nachricht; wenn nein, gar keine.

Zwei Quellen:
  * Tabelle `sonderzuege` — Sonderzüge durch Elmshorn laut Fahrplan der Bahn,
  * Tabelle `sichtungen` (Brett 006, Stichwortsuche) — angekündigte Sonderfahrten
    aus dem Forum, die in der vergangenen Woche gefunden wurden.

    wochenvorschau.py            ein Durchlauf (Cron: sonntags 18 Uhr)
    wochenvorschau.py --dry-run  zeigt nur, verschickt nichts
    wochenvorschau.py --jetzt    auch außerhalb des Sonntags
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import html as htmlmod
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "wochenvorschau.log")
log = logging.getLogger("wochenvorschau")

TAGE_VORAUS = 7
SICHTUNG_RUECKBLICK_TAGE = 7


def sonderzuege(conn, ab: dt.date, bis: dt.date) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM sonderzuege WHERE tag BETWEEN %s AND %s ORDER BY tag, zeit",
                    (ab, bis))
        return list(cur.fetchall())


DATUM_RE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?")


def termin_in_woche(text: str, ab: dt.date, bis: dt.date) -> dt.date | None:
    """Erstes genanntes Datum, das in die kommende Woche fällt.

    Ohne diese Prüfung landen Fragen und Berichte über vergangene Fahrten in der
    Vorschau ("Frage zu Dampf durch den Hamburger Hafen") — angekündigt ist etwas
    erst, wenn ein Datum dabeisteht."""
    for tag, monat, jahr in DATUM_RE.findall(text or ""):
        for j in ([int(jahr) if len(jahr) == 4 else 2000 + int(jahr)] if jahr else [ab.year, ab.year + 1]):
            try:
                datum = dt.date(j, int(monat), int(tag))
            except ValueError:
                continue
            if ab <= datum <= bis:
                return datum
    return None


def ankuendigungen(conn, seit: dt.datetime, ab: dt.date, bis: dt.date) -> list[dict]:
    """Angekündigte Sonderfahrten aus dem Forum: gefunden in der vergangenen Woche und
    mit einem Datum in der kommenden."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM sichtungen WHERE treffer = 1 AND brett = '006' "
                    "  AND gesehen_am >= %s ORDER BY gesehen_am DESC", (seit,))
        gefunden = []
        for b in cur.fetchall():
            termin = termin_in_woche(f"{b['titel']} {b.get('beschreibung') or ''}", ab, bis)
            if termin:
                gefunden.append({**b, "termin": termin})
    return gefunden


def baue(zuege: list[dict], beitraege: list[dict], ab: dt.date, bis: dt.date) -> tuple[str, str, str, str]:
    anzahl = len(zuege)
    if anzahl == 1:
        z = zuege[0]
        kopf = f"Sonderzug {mb.datum_mini(z['tag'])} {z['zeit']}: {z['kategorie']} {z['nummer']}"
    elif anzahl:
        kopf = f"{anzahl} Sonderzüge durch Elmshorn diese Woche"
    else:
        kopf = (f"{len(beitraege)} Sonderfahrt{'en' if len(beitraege) != 1 else ''} angekündigt")
    betreff = f"{kopf} · {mb.datum_mini(ab)}–{mb.datum_mini(bis)}"

    zeilen = []
    for z in zuege:
        gleis = f", Gleis {z['gleis']}" if z.get("gleis") else ""
        zeilen.append(f"{mb.datum_mini(z['tag'])} {z['zeit']} {z['kategorie']} {z['nummer']}: "
                      f"{z['von']} → {z['nach']}{gleis}")
    for b in beitraege:
        zeilen.append(f"{mb.datum_mini(b['termin'])} angekündigt: {b['titel']} "
                      f"({b['ort']}, {float(b['entfernung']):.0f} km)")

    text = (f"{betreff}\n{'=' * len(betreff)}\n\n" + "\n".join(f"- {z}" for z in zeilen)
            + "\n\nSonderzüge laut Fahrplan der Bahn für Elmshorn und Ankündigungen aus dem Forum.\n"
              "Die Umläufe der 218er stehen erst am Vorabend fest — deshalb nur die Sonderfahrten.\n"
            + f"Übersicht: {mb.UEBERSICHT_URL}\n")
    body = (mb.RAHMEN_AUF
            + f'<div style="font-size:13px;color:#57606a">Woche vom {htmlmod.escape(mb.datum_lang(ab))}</div>'
            + f'<h2 style="margin:2px 0 10px;font-size:19px">{htmlmod.escape(betreff)}</h2>'
            + '<ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.6">'
            + "".join(f"<li>{htmlmod.escape(z)}</li>" for z in zeilen) + "</ul>"
            + '<p style="color:#57606a;font-size:12px;line-height:1.5;margin:14px 0 0">'
              'Nur Sonderfahrten: Welche 218 im Regeldienst fährt, steht erst am Vorabend im Forum.</p>'
            + f'<p style="margin:16px 0 0">{mb.knopf(mb.UEBERSICHT_URL, "Übersicht")}</p>'
            + mb.RAHMEN_ZU)
    return betreff, body, text, " · ".join(zeilen)


def lauf(conn, cfg: dict, dry_run: bool = False) -> int:
    heute = dt.date.today()
    bis = heute + dt.timedelta(days=TAGE_VORAUS)
    zuege = sonderzuege(conn, heute, bis)
    beitraege = ankuendigungen(conn, dt.datetime.now() - dt.timedelta(days=SICHTUNG_RUECKBLICK_TAGE),
                               heute, bis)

    if not zuege and not beitraege:
        log.info("Keine Sonderfahrten für %s bis %s — keine Nachricht.", heute, bis)
        return 0

    betreff, body, text, push = baue(zuege, beitraege, heute, bis)
    if dry_run:
        log.info("dry-run: %s", betreff)
        print(betreff, "\n", push)
        return 0
    erfolge = mb.melde(cfg, "sonderzug", betreff, body, text, push_text=push)
    log.info("%s — %d Zustellungen", betreff, erfolge)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Sonntagsnachricht zu Sonderzügen der kommenden Woche")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--jetzt", action="store_true", help="auch an anderen Wochentagen")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handler: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose or args.dry_run:
        handler.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handler,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    if dt.date.today().weekday() != 6 and not (args.jetzt or args.dry_run):
        return 0                       # nur sonntags

    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    try:
        lauf(conn, cfg, dry_run=args.dry_run)
        mb.herzschlag(conn, "wochenvorschau")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

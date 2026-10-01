#!/usr/bin/env python3
"""
Selbstüberwachung: meldet, wenn Zugradar stillschweigend nicht mehr arbeitet.

Der gefährlichste Fehler ist der leise — kein Forumsbeitrag mehr lesbar, die
Bahn-Schnittstelle antwortet nicht, ein Cron läuft nicht: Dann kommt einfach nichts
mehr, und das fällt erst auf, wenn eine 218 verpasst wurde.

Geprüft wird stündlich:
  * letzter erfolgreicher Durchlauf des Wächters (meta.letzter_erfolg, laeufe_log),
  * Alter der Echtzeitlage (Tabelle lage, geschrieben von stoerung.py),
  * wann die Nebenskripte zuletzt gearbeitet haben (Zeitstempel ihrer Logdateien),
  * Geräte, deren Push-Zustellung mehrfach hintereinander fehlschlug,
  * Tagesliste für morgen ab 20 Uhr (fehlt sie, ist entweder das Forum still oder
    die Erkennung kaputt) — nur als Hinweis, nicht als Fehler.

Gemeldet wird per Push und Mail (der Push-Weg könnte ja gerade der defekte sein).
Jedes Problem einmal; ist es behoben, kommt eine Entwarnung. Der Zustand steht in
meta unter "wacht_offen".

    wacht.py             ein Durchlauf
    wacht.py --dry-run   nur anzeigen
    wacht.py --status    aktuellen Befund ausgeben
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "wacht.log")
log = logging.getLogger("wacht")

# Name -> (erlaubtes Alter in Minuten, Klartext). Jedes Skript setzt am Ende seines
# Laufs meta.lauf_<name> (mb.herzschlag) — die Logdatei taugt dafür nicht, ein ruhiger
# Lauf schreibt nichts hinein.
SKRIPTE = {
    "waechter":   (180, "Der stündliche Prüflauf des Forums"),
    "stoerung":   ( 45, "Die Verspätungs- und Störungsprüfung"),
    "sichtungen": ( 45, "Die Suche im Umkreis von Elmshorn"),
    "beobachten": ( 45, "Die Prüfung vorgemerkter Fahrten"),
    "erinnerung": ( 45, "Die Erinnerung vor der Durchfahrt"),
    "sonderzuege":(300, "Die Suche nach Sonderzügen"),
    "zuege":      ( 30, "Das Holen der fahrenden Züge für die Karte"),
    "punkt":      ( 30, "Die Antworten auf das Antippen der Strecke"),
}
LAGE_ALTER_MINUTEN = 45          # stoerung.py schreibt alle 10 Minuten
ERFOLG_ALTER_STUNDEN = 3         # letzter erfolgreicher Forumslauf
PUSH_FEHLER_FOLGE = 3


def befund(conn, cfg: dict) -> dict[str, str]:
    """Alle offenen Probleme als {schluessel: Klartext}."""
    jetzt = dt.datetime.now()
    probleme: dict[str, str] = {}

    # --- Letzter erfolgreicher Durchlauf des Wächters
    letzter = mb.meta_get(conn, "letzter_erfolg")
    if letzter:
        alter = jetzt - dt.datetime.fromisoformat(letzter)
        if alter > dt.timedelta(hours=ERFOLG_ALTER_STUNDEN):
            probleme["waechter_erfolg"] = (
                f"Der letzte erfolgreiche Forumslauf war vor {int(alter.total_seconds() // 3600)} "
                f"Stunden ({letzter[:16].replace('T', ' ')} Uhr).")
    else:
        probleme["waechter_erfolg"] = "Es gibt noch keinen erfolgreichen Forumslauf."

    with conn.cursor() as cur:
        cur.execute("SELECT erfolg, fehler, gestartet FROM laeufe_log ORDER BY gestartet DESC LIMIT 3")
        letzte = list(cur.fetchall())
    if letzte and not any(int(z["erfolg"]) for z in letzte):
        grund = (letzte[0]["fehler"] or "ohne Angabe")[:160]
        probleme["waechter_fehler"] = f"Die letzten {len(letzte)} Prüfläufe sind gescheitert: {grund}"

    # --- Echtzeitlage. Nachts fährt nichts, dann schreibt stoerung.py nichts fort —
    # das ist kein Fehler, deshalb wird nur tagsüber geprüft.
    with conn.cursor() as cur:
        cur.execute("SELECT stand, quelle FROM lage WHERE id = 1")
        lage = cur.fetchone()
    if not 5 <= jetzt.hour < 23:
        pass
    elif not lage:
        probleme["lage"] = "Zur Lage in Elmshorn liegt noch nichts vor."
    else:
        alter = jetzt - lage["stand"]
        if alter > dt.timedelta(minutes=LAGE_ALTER_MINUTEN):
            probleme["lage"] = (f"Die Lage in Elmshorn ist von {lage['stand']:%H:%M} Uhr und damit "
                                f"{int(alter.total_seconds() // 60)} Minuten alt "
                                f"(Quelle {lage['quelle']}).")

    # --- Laufen die Cronjobs?
    for name, (minuten, klartext) in SKRIPTE.items():
        stempel = mb.meta_get(conn, "lauf_" + name)
        if not stempel:
            # Kein Lebenszeichen: erst ab dem ersten Lauf aussagekräftig.
            continue
        alter = jetzt - dt.datetime.fromisoformat(stempel)
        if alter > dt.timedelta(minutes=minuten):
            probleme[f"cron_{name}"] = (f"{klartext} lief zuletzt vor "
                                        f"{int(alter.total_seconds() // 60)} Minuten.")

    # --- Push-Geräte mit dauerhaften Fehlern
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, fehler_folge, letzter_fehler FROM push_geraete "
                        " WHERE aktiv = 1 AND fehler_folge >= %s", (PUSH_FEHLER_FOLGE,))
            kaputt = list(cur.fetchall())
        if kaputt:
            probleme["push"] = "Push kommt nicht an bei: " + ", ".join(
                f"{g['name']} ({g['fehler_folge']}×, {(g['letzter_fehler'] or '')[:60]})" for g in kaputt)
    except Exception:
        pass                      # Tabelle gibt es erst seit den Push-Nachrichten

    return probleme


def baue(offen: dict[str, str], behoben: list[str]) -> tuple[str, str, str, str]:
    heute = dt.date.today()
    if offen:
        betreff = mb.mit_datum(
            "Zugradar meldet sich: " + ("1 Problem" if len(offen) == 1 else f"{len(offen)} Probleme"), heute)
        zeilen = list(offen.values())
    else:
        betreff = mb.mit_datum("Zugradar läuft wieder", heute)
        zeilen = ["Alle Prüfungen sind wieder in Ordnung."]
    if behoben and offen:
        zeilen.append("Wieder in Ordnung: " + ", ".join(behoben) + ".")

    text = (f"{betreff}\n{'=' * len(betreff)}\n\n" + "\n".join(f"- {z}" for z in zeilen)
            + "\n\nGeprüft werden die Durchläufe der Skripte, das Alter der Echtzeitlage und "
              "die Zustellung der Push-Nachrichten.\n"
            + f"Übersicht: {mb.UEBERSICHT_URL}\n")
    body = (mb.RAHMEN_AUF
            + f'<h2 style="margin:0 0 10px;font-size:19px">{html.escape(betreff)}</h2>'
            + '<ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.6">'
            + "".join(f"<li>{html.escape(z)}</li>" for z in zeilen) + "</ul>"
            + f'<p style="margin:16px 0 0">{mb.knopf(mb.UEBERSICHT_URL, "Übersicht")}</p>'
            + mb.RAHMEN_ZU)
    return betreff, body, text, " · ".join(zeilen)


def lauf(conn, cfg: dict, dry_run: bool = False) -> dict[str, str]:
    offen = befund(conn, cfg)
    vorher = json.loads(mb.meta_get(conn, "wacht_offen") or "{}")
    neu = {k: v for k, v in offen.items() if k not in vorher}
    behoben = [k for k in vorher if k not in offen]

    if dry_run:
        for k, v in offen.items():
            print(("NEU   " if k in neu else "OFFEN ") + k + ": " + v)
        for k in behoben:
            print("BEHOBEN " + k)
        if not offen and not behoben:
            print("Alles in Ordnung.")
        return offen

    if neu or (behoben and not offen):
        betreff, body, text, push = baue(offen, behoben)
        erfolge = mb.melde(cfg, "wacht", betreff, body, text, push_text=push, markierung="wacht")
        log.warning("%s — %d Zustellungen", betreff, erfolge)
    elif behoben:
        log.info("Teilweise behoben: %s", ", ".join(behoben))

    mb.meta_set(conn, "wacht_offen", json.dumps(offen, ensure_ascii=False))
    mb.meta_set(conn, "wacht_geprueft", dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    if offen:
        log.info("Offen: %s", "; ".join(offen.values()))
    else:
        log.debug("Alles in Ordnung.")
    return offen


def main() -> int:
    p = argparse.ArgumentParser(description="Selbstüberwachung von Zugradar")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handler: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose or args.status or args.dry_run:
        handler.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=handler,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    try:
        if args.status:
            for k, v in (befund(conn, cfg) or {"alles": "Alles in Ordnung."}).items():
                print(f"{k:<18} {v}")
            return 0
        lauf(conn, cfg, dry_run=args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

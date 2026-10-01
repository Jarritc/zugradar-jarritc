#!/usr/bin/env python3
"""
Besondere 218er aus der Bestandstabelle des Wikipedia-Artikels "DB-Baureihe 218".

"Besonders" heißt: nicht zerlegt, und mindestens eines davon
  * ein Eigenname in der Betreiberbezeichnung ("218 453-9 "Lukas""),
  * eine andere Lackierung als verkehrsrot,
  * eine Aufschrift, Beklebung oder Werbung in der Bemerkung.

Gelesen wird der Rohtext über die MediaWiki-API und selbst ausgewertet — nicht
über eine KI-Zusammenfassung der Seite. Eine solche hatte beim Aufbau dieser
Liste einen Namen in die falsche Spalte gesetzt und die Tabelle ab 218 400 als
"endet hier" abgeschnitten.

Ergänzungen, die nicht in der Tabelle stehen (z. B. die PIKO/Märklin-Werbung der
218 497-6), kommen aus ERGAENZUNGEN mit eigener Quellenangabe.

    besondere_loks.py            Tabelle neu einlesen und in die Datenbank schreiben
    besondere_loks.py --anzeigen nur ausgeben, nichts schreiben
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import marschbahn as mb

WIKI = "https://de.wikipedia.org/w/api.php"
ARTIKEL = "DB-Baureihe 218"
UA = "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"

# Belege außerhalb von Wikipedia, beim Aufbau am 16.09.2026 geprüft. Namen ohne
# zweite Quelle bekommen belegt=0 und werden auf der Webseite so gekennzeichnet.
ZWEITE_QUELLE = {
    "218 330-9": "LOK Report, Bahnbilder („D-DB 218 330-9 (Konrad)“)",
    "218 406-7": "YouTube „Kemptener Retro-Starlok 218 406 Guste“",
    "218 443-0": "LOK Report „BR 218 443 Donna zur Unterstützung in Mühldorf“, DSO-Forum",
    "218 446-3": "Bahnbilder/Flickr („Meike“)",
    "218 460-4": "DT5 Online („218 460 Conny“)",
}

ERGAENZUNGEN = {
    "218 497-6": {
        "bemerkung": "Werbelok PIKO/Märklin, Gestaltung der DB Fahrzeuginstandhaltung Cottbus",
        "quelle": "https://www.piko.de/DE/index.php/de/piko-news/1687-pressenotiz-zur-maerklin-piko-lok-218-497-6-roll-out.html",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS besondere_loks (
    lok          VARCHAR(32)  NOT NULL PRIMARY KEY,
    name         VARCHAR(48)  NULL,
    lackierung   VARCHAR(80)  NULL,
    betreiber    VARCHAR(48)  NULL,
    zustand      VARCHAR(48)  NULL,
    bemerkung    VARCHAR(255) NULL,
    belegt       TINYINT(1)   NOT NULL DEFAULT 0,  -- Name durch eine zweite Quelle bestätigt
    quelle       VARCHAR(255) NOT NULL,
    stand        DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

BESONDERE_BEMERKUNG = re.compile(r"Aufschrift|Beklebung|Werbe|Design|Sonder", re.I)


def hole_wikitext() -> tuple[str, str]:
    url = WIKI + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content|timestamp",
        "rvslots": "main", "format": "json", "formatversion": "2",
        "redirects": "1", "titles": ARTIKEL})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as a:
        rev = json.load(a)["query"]["pages"][0]["revisions"][0]
    return rev["slots"]["main"]["content"], rev["timestamp"]


def sauber(zelle: str) -> str:
    z = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", zelle, flags=re.S)
    z = re.sub(r'data-sort-value="[^"]*"\s*\|', "", z)
    z = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", z)
    z = re.sub(r"<[^>]+>", "", z).replace("''", "").replace("&shy;", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", z).strip()


def zeilen(wikitext: str) -> list[dict]:
    a = wikitext.find("{|")
    b = wikitext.find("|}", a)
    aus = []
    for block in wikitext[a:b].split("\n|-")[1:]:
        if "\n" not in block:
            continue
        zellen = [sauber(c) for c in re.split(r"\n\|", "\n" + block.split("\n", 1)[1])[1:]]
        if len(zellen) < 7:
            continue
        aus.append(dict(zip(
            ["serie", "nummer", "bezeichnung", "betreiber", "lackierung", "zustand", "bemerkung"],
            zellen[:7])))
    return aus


def besondere(wikitext: str) -> list[dict]:
    aus = []
    for z in zeilen(wikitext):
        if z["zustand"].startswith("zerlegt"):
            continue
        nr = re.match(r"218 \d{3}-\d", z["nummer"])
        # "(Erstbesetzung)" ist die frühere Lok mit derselben Nummer, keine eigene.
        if not nr or "Erstbesetzung" in z["nummer"]:
            continue
        name_m = re.search(r"[„\"“»]([^„\"“”»«]{2,40})[“\"”«]", z["bezeichnung"])
        name = name_m.group(1).strip() if name_m else None
        lack = z["lackierung"].strip("„“\" ")
        sonderlack = lack and lack.lower() != "verkehrsrot"
        if not (name or sonderlack or BESONDERE_BEMERKUNG.search(z["bemerkung"])):
            continue
        aus.append({"lok": nr.group(0), "name": name, "lackierung": lack or None,
                    "betreiber": z["betreiber"] or None, "zustand": z["zustand"] or None,
                    "bemerkung": z["bemerkung"] or None})
    return aus


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--anzeigen", action="store_true")
    args = p.parse_args()

    wikitext, revision = hole_wikitext()
    liste = besondere(wikitext)
    quelle_wiki = f"Wikipedia „{ARTIKEL}“, Fassung vom {revision[:10]}"
    for lok, erg in ERGAENZUNGEN.items():
        eintrag = next((e for e in liste if e["lok"] == lok), None)
        if eintrag:
            eintrag["bemerkung"] = "; ".join(x for x in (eintrag["bemerkung"], erg["bemerkung"]) if x)
            eintrag["zusatzquelle"] = erg["quelle"]

    print(f"{len(liste)} besondere 218er ({quelle_wiki}), davon "
          f"{sum(1 for e in liste if e['name'])} mit Namen")
    if args.anzeigen:
        for e in liste:
            print(f"  {e['lok']:<11}{('„' + e['name'] + '“') if e['name'] else '':<14}"
                  f"{(e['lackierung'] or '')[:28]:<30}{(e['betreiber'] or '')[:10]:<12}"
                  f"{(e['bemerkung'] or '')[:60]}")
        return 0

    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    jetzt = dt.datetime.now()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            # Ganz neu schreiben: eine Lok, die inzwischen zerlegt ist, fällt raus.
            cur.execute("DELETE FROM besondere_loks")
            for e in liste:
                quelle = quelle_wiki + (f"; {e['zusatzquelle']}" if e.get("zusatzquelle") else "")
                if e["lok"] in ZWEITE_QUELLE:
                    quelle += f"; {ZWEITE_QUELLE[e['lok']]}"
                cur.execute(
                    "INSERT INTO besondere_loks (lok, name, lackierung, betreiber, zustand, "
                    " bemerkung, belegt, quelle, stand) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (e["lok"], e["name"], (e["lackierung"] or "")[:80] or None,
                     (e["betreiber"] or "")[:48] or None, (e["zustand"] or "")[:48] or None,
                     (e["bemerkung"] or "")[:255] or None,
                     1 if e["lok"] in ZWEITE_QUELLE else 0, quelle[:255], jetzt))
        conn.commit()
        print("in besondere_loks geschrieben")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

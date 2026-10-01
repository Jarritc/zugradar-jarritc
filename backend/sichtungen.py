#!/usr/bin/env python3
"""
Überwacht zwei Foren von Drehscheibe-Online auf Beiträge aus der Umgebung von
Elmshorn und meldet sie per Mail.

    Board 004  Bild-Sichtungen      (bundesweit, deshalb selten ein Treffer)
    Board 109  Betriebsstörungen

Ausdrücklich NICHT ausgewertet wird /ds_sifo/ (das Live-Sichtungssystem): Die
robots.txt der Seite sperrt diesen Pfad für alle Automaten. Die Foren unter
/foren/ sind dagegen freigegeben.

Die Orte stehen als Freitext in Titel und Beitrag. Kandidaten werden über
Muster herausgelesen ("in Itzehoe", "Glückstadt Hbf"), dann bei transitous
geokodiert und gegen Elmshorn gemessen. Jeder Ort wird nur einmal
nachgeschlagen und danach in der Tabelle `orte` behalten.

    sichtungen.py            ein Durchlauf
    sichtungen.py --dry-run  ohne Mailversand, merkt sich nichts
    sichtungen.py --setup    Tabellen anlegen
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmlmod
import gzip
import json
import logging
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import marschbahn as mb

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "sichtungen.log")
log = logging.getLogger("sichtungen")

ELMSHORN = (53.754025, 9.659408)
GEOCODE = "https://api.transitous.org/api/v1/geocode"
UA = "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"

BRETTER = {"004": "Bild-Sichtungen", "109": "Betriebsstörungen", "006": "Sonderzug-Ankündigung"}

# Im Fahrzeiten-Forum (006) stehen auch die täglichen Lokeinsatz-Listen und
# Fahrzeitfragen aller Art. Dort zählt nur, was nach Sonderzug klingt — alles
# andere wird ohne Ortssuche als erledigt vermerkt.
STICHWORT_BRETTER = {"006"}

SCHEMA = ["""
CREATE TABLE IF NOT EXISTS orte (
    name       VARCHAR(96)  NOT NULL PRIMARY KEY,
    gefunden   TINYINT(1)   NOT NULL DEFAULT 0,
    treffer    VARCHAR(128) NULL,
    lat        DOUBLE       NULL,
    lon        DOUBLE       NULL,
    entfernung DOUBLE       NULL,
    geprueft   DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", """
CREATE TABLE IF NOT EXISTS sichtungen (
    thread_id  BIGINT       NOT NULL PRIMARY KEY,
    brett      VARCHAR(4)   NOT NULL,
    titel      VARCHAR(255) NOT NULL,
    url        VARCHAR(255) NOT NULL,
    ort        VARCHAR(96)  NULL,
    entfernung DOUBLE       NULL,
    beschreibung TEXT       NULL,
    treffer    TINYINT(1)   NOT NULL DEFAULT 0,
    gesehen_am DATETIME     NOT NULL,
    gemeldet_am DATETIME    NULL,
    KEY idx_treffer (treffer, gesehen_am)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""]

# Ortsangaben stehen meist hinter einer Präposition oder vor "Hbf"/"Bahnhof".
# Der Klammerzusatz gehört zwingend dazu: "Harburg" liegt 40 km von Elmshorn,
# "Harburg (Schwab)" dagegen 557 km. Wer ihn wegwirft, meldet Bayern als Nachbarort.
ZUSATZ = r"(\s*\([A-ZÄÖÜ][\wÄÖÜäöüß.\s-]{1,18}\))?"
ORT_MUSTER = [
    re.compile(r"\b(?:in|bei|ab|nach|von|durch|zwischen|am|vor)\s+"
               r"([A-ZÄÖÜ][\wÄÖÜäöüß.-]{2,}(?:[- ][A-ZÄÖÜ][\wÄÖÜäöüß.-]{2,})?)" + ZUSATZ),
    re.compile(r"\b([A-ZÄÖÜ][\wÄÖÜäöüß.-]{3,}(?:[- ][A-ZÄÖÜ][\wÄÖÜäöüß.-]{2,})?)" + ZUSATZ
               + r"\s+(?:Hbf|Hauptbahnhof|Bahnhof|Bf\b)"),
]

# Alles im 50-km-Umkreis von Elmshorn liegt in einem dieser Länder. Ein Treffer
# außerhalb heißt: der Geocoder hat den falschen Ort erwischt.
LAENDER = {"schleswig-holstein", "hamburg", "niedersachsen", "mecklenburg-vorpommern"}

# Wörter, die in Titeln großgeschrieben auftauchen, aber keine Orte sind.
KEIN_ORT = {
    "sichtungen", "sichtung", "bilder", "bild", "dampflok", "sonderzug", "güterzug",
    "problemen", "hilfe", "woche", "wochenende", "nachmittag", "vormittag", "morgen",
    "heute", "gestern", "einigkeit", "epoche", "zugvorbereitung", "familienfest",
    "foren", "forentipps", "nutzungsbedingungen", "wagen", "achse", "januar",
    "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
    "oktober", "november", "dezember", "montag", "dienstag", "mittwoch",
    "donnerstag", "freitag", "samstag", "sonntag", "richtung", "rückfahrt",
    "getreide", "getreidezug", "kalkzug", "containerzug", "kesselzug", "holzzug",
}


def entfernung_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def normal(s: str) -> str:
    return (s.lower().replace("ä", "ae").replace("ö", "oe")
            .replace("ü", "ue").replace("ß", "ss").strip(" .,;:-"))


def passt(kandidat: str, name: str) -> bool:
    """Nur übernehmen, wenn der gefundene Ort auch wirklich der gesuchte ist."""
    k, n = normal(kandidat), normal(name)
    return k == n or n.startswith(k) or k.startswith(n)


def entpacke(roh: bytes) -> bytes:
    """gzip-Antwort auspacken — am Anfang steht immer 1f 8b (so bleibt der Test einfach,
    der urlopen durch eine Attrappe ersetzt)."""
    return gzip.decompress(roh) if roh[:2] == b"\x1f\x8b" else roh


def geokodiere(kandidat: str) -> dict | None:
    url = GEOCODE + "?" + urllib.parse.urlencode({"text": kandidat})
    # gzip spart dem Dienst viel Verkehr (transitous, 01.10.2026)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25) as antwort:
        roh = antwort.read()
    daten = json.loads(entpacke(roh).decode("utf-8"))
    # Der beste passende Treffer entscheidet. Früher wurden Treffer außerhalb des
    # Nordens übersprungen und weitergesucht — dann fand sich für "Neuenrade"
    # (Sauerland, 303 km) irgendwo weiter unten ein gleichnamiger Nebentreffer in
    # Schleswig-Holstein, und ein Beitrag aus dem Hönnetal ging als Nachbarort raus.
    for t in daten:
        if not (t.get("country") == "DE" and t.get("type") in ("STOP", "PLACE")
                and passt(kandidat, t.get("name", ""))):
            continue
        land = next((a["name"] for a in t.get("areas", [])
                     if a.get("adminLevel") == 4), "")
        return {"treffer": t["name"], "lat": t["lat"], "lon": t["lon"], "land": land}
    return None


def ort_pruefen(conn, kandidat: str) -> dict | None:
    """Ort nachschlagen, mit dauerhaftem Gedächtnis in der Tabelle `orte`."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM orte WHERE name = %s", (kandidat,))
        row = cur.fetchone()
    if row:
        return row if row["gefunden"] else None

    gefunden = None
    try:
        gefunden = geokodiere(kandidat)
        time.sleep(0.4)                    # höflich gegenüber einem freien Dienst
    except Exception as exc:
        log.debug("Geokodierung von %r fehlgeschlagen: %s", kandidat, exc)
        return None                        # nichts merken, nächstes Mal neu versuchen

    jetzt = dt.datetime.now()
    with conn.cursor() as cur:
        if gefunden:
            km = entfernung_km(ELMSHORN, (gefunden["lat"], gefunden["lon"]))
            cur.execute(
                "INSERT INTO orte (name, gefunden, treffer, lat, lon, entfernung, geprueft) "
                "VALUES (%s,1,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE geprueft=VALUES(geprueft)",
                (kandidat, gefunden["treffer"], gefunden["lat"], gefunden["lon"], km, jetzt))
        else:
            cur.execute(
                "INSERT INTO orte (name, gefunden, geprueft) VALUES (%s,0,%s) "
                "ON DUPLICATE KEY UPDATE geprueft=VALUES(geprueft)", (kandidat, jetzt))
    conn.commit()
    if not gefunden:
        return None
    return {"name": kandidat, "treffer": gefunden["treffer"], "entfernung": km,
            "lat": gefunden["lat"], "lon": gefunden["lon"]}


def kandidaten(text: str) -> list[str]:
    """Ortskandidaten. Steht ein Klammerzusatz dabei, wird nur die vollständige
    Form zurückgegeben — die bloße Form wäre irreführend."""
    aus: list[str] = []
    for muster in ORT_MUSTER:
        for name, zusatz in muster.findall(text):
            k = (name.strip(" .,;:-") + (zusatz or "").rstrip()).strip()
            if len(k) >= 4 and normal(name) not in KEIN_ORT and k not in aus:
                aus.append(k)
    return aus[:6]


# Fußzeilen, die unter fast jedem Beitrag stehen und nichts zur Sache sagen.
BALLAST = re.compile(r"\d+-mal bearbeitet\.\s*Zuletzt am.*$|Angehängte Datei.*$",
                     re.I | re.S)


def beschreibung(text: str, laenge: int = 400) -> str | None:
    """Beitragstext auf das Wesentliche kürzen.

    Bei den Bild-Sichtungen steht die Fahrtrichtung meist erst hier im Text,
    nicht im Titel — "Heute 218 450 Richtung Maschen".
    """
    t = BALLAST.sub("", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if len(t) > laenge:
        # An einer Satz- oder Wortgrenze abschneiden, nicht mitten im Wort.
        schnitt = t.rfind(". ", 0, laenge)
        if schnitt < laenge // 2:
            schnitt = t.rfind(" ", 0, laenge)
        t = t[:schnitt if schnitt > 0 else laenge].rstrip(" ,;–-") + " …"
    return t


def themen(session: requests.Session, brett: str) -> list[dict]:
    """Themen eines Bretts: id, Titel, Adresse."""
    h = mb.http_get(session, f"https://www.drehscheibe-online.de/foren/list.php?{brett}")
    aus, gesehen = [], set()
    for m in re.finditer(
            r'<a[^>]+href="([^"]*read\.php\?' + re.escape(brett) + r',(\d+))"[^>]*>(.*?)</a>',
            h, re.S):
        tid = int(m.group(2))
        if tid in gesehen:
            continue
        gesehen.add(tid)
        titel = re.sub(r"\s+", " ", mb.strip_tags(m.group(3))).strip()
        if titel:
            aus.append({"thread_id": tid, "brett": brett, "titel": titel[:250],
                        "url": m.group(1)})
    return aus


def naechster_ort(conn, texte: list[str], grenze: float) -> dict | None:
    """Der nächstgelegene erkannte Ort innerhalb der Grenze."""
    beste = None
    for text in texte:
        for k in kandidaten(text):
            o = ort_pruefen(conn, k)
            if o and o["entfernung"] is not None and o["entfernung"] <= grenze:
                if beste is None or o["entfernung"] < beste["entfernung"]:
                    beste = {"name": k, "treffer": o.get("treffer"),
                             "entfernung": float(o["entfernung"])}
    return beste


def baue_mail(treffer: list[dict], grenze: float) -> tuple[str, str, str]:
    if len(treffer) == 1:
        t = treffer[0]
        titel = t["titel"] if len(t["titel"]) <= 45 else t["titel"][:44].rstrip() + "…"
        vorne = "Sonderzug " if t["brett"] in STICHWORT_BRETTER else ""
        betreff = mb.mit_datum(f"{vorne}{t['ort']} ({t['entfernung']:.0f} km): {titel}", dt.date.today())
    else:
        betreff = mb.mit_datum(f"{len(treffer)} Beiträge aus dem Umkreis: " + ", ".join(
            dict.fromkeys(t["ort"] for t in treffer)), dt.date.today())

    zeilen_text, zeilen_html = [], []
    for t in treffer:
        text_besch = t.get("beschreibung")
        zeilen_text.append(
            f"  [{BRETTER.get(t['brett'], t['brett'])}] {t['titel']}\n"
            f"     {t['ort']}, {t['entfernung']:.0f} km von Elmshorn\n"
            + (f"     {text_besch}\n" if text_besch else "")
            + f"     {t['url']}")
        zeilen_html.append(
            '<tr><td style="padding:10px;border-bottom:1px solid #eaeef2">'
            f'<a href="{htmlmod.escape(t["url"])}" style="color:#0969da;'
            f'text-decoration:none;font-weight:bold">{htmlmod.escape(t["titel"])}</a>'
            f'<div style="color:#57606a;font-size:12px;margin-top:3px">'
            f'{htmlmod.escape(BRETTER.get(t["brett"], t["brett"]))} &middot; '
            f'{htmlmod.escape(t["ort"])}, {t["entfernung"]:.0f} km von Elmshorn</div>'
            + (f'<div style="margin-top:6px;font-size:13px;line-height:1.5">'
               f'{htmlmod.escape(text_besch)}</div>' if text_besch else '')
            + '</td></tr>')

    text = (f"{betreff}\n{'=' * len(betreff)}\n\n" + "\n\n".join(zeilen_text)
            + f"\n\nGefunden im Umkreis von {grenze:.0f} km um Elmshorn.\n"
              "Übersicht: https://jarritc.de/zugradar/\n")
    body_html = (
        mb.RAHMEN_AUF
        + f'<h2 style="margin:0 0 12px;font-size:19px">{htmlmod.escape(betreff)}</h2>'
        '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        + "".join(zeilen_html) + '</table>'
        '<p style="color:#57606a;font-size:12px;line-height:1.5;margin:16px 0 0">'
        f'Gefunden im Umkreis von {grenze:.0f}&nbsp;km um Elmshorn. Der Ort wird aus '
        'Titel und Beitrag gelesen und geokodiert &mdash; steht dort keiner, taucht der '
        'Beitrag hier nicht auf.</p>'
        '<p style="margin:16px 0 0"><a href="https://jarritc.de/zugradar/" '
        'style="color:#0969da;text-decoration:none;font-size:14px">Übersicht</a></p>'
        + mb.RAHMEN_ZU)
    return betreff, body_html, text


def lauf(cfg: dict, conn, dry_run: bool = False) -> None:
    su = cfg.get("sichtungen") or {}
    if not su.get("aktiv", True):
        log.info("Sichtungsüberwachung ist abgeschaltet.")
        return
    grenze = float(su.get("umkreis_km", 50))
    max_beitraege = int(su.get("max_beitraege_je_lauf", 8))

    with conn.cursor() as cur:
        cur.execute("SELECT thread_id, brett FROM sichtungen")
        zeilen = cur.fetchall()
    bekannt = {r["thread_id"] for r in zeilen}
    # Beim allerersten Lauf gilt der gesamte Bestand als neu — der wird nur
    # erfasst, nicht gemeldet, sonst käme eine Mail mit über hundert Themen.
    # Das gilt je Brett: ein später hinzugenommenes Brett startet ebenfalls still.
    bekannte_bretter = {r["brett"] for r in zeilen}
    erstlauf = not bekannt

    import re as _re
    woerter = (cfg.get("sonderzuege") or {}).get("forum_stichwoerter") or ["Sonderzug"]
    stichwort = _re.compile("|".join(_re.escape(w) for w in woerter), _re.I)

    session = requests.Session()
    neu: list[dict] = []
    for brett in su.get("bretter") or list(BRETTER):
        try:
            for t in themen(session, brett):
                if t["thread_id"] not in bekannt:
                    neu.append(t)
        except Exception as exc:
            log.warning("Brett %s nicht abrufbar: %s", brett, exc)
        time.sleep(1.2)

    if not neu:
        log.debug("Keine neuen Themen.")
        return
    log.info("%d neue Themen", len(neu))

    treffer, jetzt = [], dt.datetime.now()
    geholt = 0
    still_bretter = {t["brett"] for t in neu} - bekannte_bretter if not erstlauf else set()
    if still_bretter:
        log.info("Brett(er) %s neu in der Überwachung — Bestand wird still erfasst",
                 ", ".join(sorted(still_bretter)))
    for t in neu:
        t["still"] = t["brett"] in still_bretter
        if t["brett"] in STICHWORT_BRETTER and not stichwort.search(t["titel"]):
            t.update(ort=None, entfernung=None, treffer=False, beschreibung=None)
            continue
        texte = [t["titel"]]
        # Steht im Titel kein Ort, lohnt ein Blick in den Beitrag — aber begrenzt,
        # damit ein schwatzhafter Tag nicht Dutzende Abrufe auslöst.
        if not kandidaten(t["titel"]) and geholt < max_beitraege:
            try:
                texte.append(mb.beitrag_text(mb.http_get(session, t["url"]))[:4000])
                geholt += 1
                time.sleep(1.2)
            except Exception as exc:
                log.debug("Beitrag %s nicht abrufbar: %s", t["thread_id"], exc)

        ort = naechster_ort(conn, texte, grenze)
        t["ort"] = ort["name"] if ort else None
        t["entfernung"] = ort["entfernung"] if ort else None
        t["treffer"] = bool(ort)
        t["beschreibung"] = None
        if ort:
            # Für einen Treffer lohnt der Beitrag immer — dort steht die
            # Richtung. Wurde er oben schon geholt, kein zweiter Abruf.
            if len(texte) > 1:
                t["beschreibung"] = beschreibung(texte[1])
            else:
                try:
                    t["beschreibung"] = beschreibung(
                        mb.beitrag_text(mb.http_get(session, t["url"])))
                    time.sleep(1.2)
                except Exception as exc:
                    log.debug("Beitrag %s nicht abrufbar: %s", t["thread_id"], exc)
            treffer.append(t)
            log.info("Treffer: %s — %s, %.0f km", t["titel"][:60],
                     ort["name"], ort["entfernung"])

    if dry_run:
        log.info("dry-run: %d Themen geprüft, %d Treffer, nichts gemerkt",
                 len(neu), len(treffer))
        for t in treffer:
            print(f"  {t['titel']}\n     {t['ort']}, {t['entfernung']:.0f} km\n     {t['url']}")
        return

    with conn.cursor() as cur:
        for t in neu:
            cur.execute(
                "INSERT INTO sichtungen (thread_id, brett, titel, url, ort, entfernung,"
                " beschreibung, treffer, gesehen_am) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE titel=VALUES(titel), "
                "beschreibung=COALESCE(VALUES(beschreibung), beschreibung)",
                (t["thread_id"], t["brett"], t["titel"], t["url"], t["ort"],
                 t["entfernung"], t.get("beschreibung"),
                 1 if t["treffer"] else 0, jetzt))
    conn.commit()

    stille = [t for t in treffer if t.get("still")]
    if stille and not erstlauf:
        with conn.cursor() as cur:
            for t in stille:
                cur.execute("UPDATE sichtungen SET gemeldet_am=%s WHERE thread_id=%s",
                            (jetzt, t["thread_id"]))
        conn.commit()
        log.info("%d Treffer aus neuen Brettern still erfasst", len(stille))
        treffer = [t for t in treffer if not t.get("still")]

    if treffer and erstlauf:
        with conn.cursor() as cur:
            for t in treffer:
                cur.execute("UPDATE sichtungen SET gemeldet_am=%s, treffer=1 "
                            "WHERE thread_id=%s", (jetzt, t["thread_id"]))
        conn.commit()
        log.info("Erstlauf: %d Treffer im Bestand erfasst, keine Mail verschickt.",
                 len(treffer))
    elif treffer:
        betreff, body_html, text = baue_mail(treffer, grenze)
        erfolge = mb.melde(cfg, "sichtung", betreff, body_html, text,
                           url=treffer[0]["url"] if len(treffer) == 1 else None)
        with conn.cursor() as cur:
            for t in treffer:
                cur.execute("UPDATE sichtungen SET gemeldet_am=%s WHERE thread_id=%s",
                            (jetzt, t["thread_id"]))
        conn.commit()
        log.info("%d Treffer gemeldet, %d Zustellungen", len(treffer), erfolge)


def main() -> None:
    p = argparse.ArgumentParser(description="Beiträge aus dem Umkreis von Elmshorn")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg["mail"].get("verify_tls", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    conn = mb.db_connect(cfg)
    try:
        with conn.cursor() as cur:
            for ddl in SCHEMA:
                cur.execute(ddl)
            # Später dazugekommen: nur anlegen, wenn sie fehlt.
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = 'sichtungen' "
                        "AND column_name = 'beschreibung'")
            if not cur.fetchone()["n"]:
                cur.execute("ALTER TABLE sichtungen ADD COLUMN beschreibung TEXT NULL "
                            "AFTER entfernung")
        conn.commit()
        if args.setup:
            print("Tabellen orte und sichtungen angelegt.")
            return
        lauf(cfg, conn, dry_run=args.dry_run)
        mb.herzschlag(conn, "sichtungen")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

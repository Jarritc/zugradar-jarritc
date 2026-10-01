#!/usr/bin/env python3
"""
Überwacht Verspätungen und Störungen in Elmshorn.

Zwei Stufen, alle 10 Minuten:

1. Fährt heute eine 218 durch Elmshorn und liegt sie im Beobachtungsfenster,
   wird sie einzeln verfolgt — Verspätung ab Schwelle, Ausfall sofort.
2. Unabhängig davon die allgemeine Lage: mehrere ausgefallene oder stark
   verspätete Züge deuten auf eine Sperrung hin, dazu einzelne schwere
   Verspätungen auf den überwachten Linien (Standard RE6, die Marschbahn).

Stufe 2 kommt mit einem einzigen Abruf aus, Stufe 1 holt mehr Seiten. Klärt
sich eine gemeldete Störung wieder, geht eine Entwarnung raus.

    stoerung.py            ein Durchlauf
    stoerung.py --dry-run  ohne Mailversand, hakt nichts ab
    stoerung.py --setup    Tabelle anlegen
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import html
import logging
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_timetables
import fahrplan
import marschbahn as mb

TZ = ZoneInfo("Europe/Berlin")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "stoerung.log")
log = logging.getLogger("stoerung")

SCHEMA_LAGE = """
CREATE TABLE IF NOT EXISTS lage (
    id      TINYINT      NOT NULL PRIMARY KEY,
    stand   DATETIME     NOT NULL,
    quelle  VARCHAR(24)  NOT NULL,
    daten   JSON         NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS stoerungen (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    tag         DATE         NOT NULL,
    art         VARCHAR(16)  NOT NULL,      -- verspaetung | ausfall | strecke
    bezug       VARCHAR(32)  NOT NULL,      -- Zugnummer oder 'strecke'
    wert        INT          NOT NULL DEFAULT 0,
    verschickt  TINYINT(1)   NOT NULL DEFAULT 1,
    gemeldet_am DATETIME     NOT NULL,
    UNIQUE KEY uniq_meldung (tag, art, bezug),
    KEY idx_tag (tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def schreibe_lage(conn, lage: list[dict], meldungen: list[dict], quelle: str) -> None:
    """Momentaufnahme für die Webseite ablegen.

    Die Seite soll die Lage zeigen, ohne bei jedem Aufruf die APIs anzufassen —
    deshalb legt der Wächter sie hier ab und die Seite liest nur noch mit.
    """
    daten = {
        "fahrten": [{
            "linie": e["linie"], "nummer": e["nummer"], "ziel": e["ziel"],
            "soll": e["soll_hhmm"], "ist": e["ist_hhmm"],
            "verspaetung": e["verspaetung"], "ausgefallen": e["ausgefallen"],
            "ursachen": e.get("ursachen") or [],
            "gleis": e.get("gleis"), "gleis_geaendert": bool(e.get("gleis_geaendert")),
        } for e in lage[:24]],
        "meldungen": [{
            "kategorie": m["kategorie"], "prioritaet": m["prioritaet"],
            "von": m["von"].strftime("%d.%m. %H:%M") if m.get("von") else None,
            "bis": m["bis"].strftime("%d.%m. %H:%M") if m.get("bis") else None,
        } for m in db_timetables.aktive_stoerungen(meldungen)],
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO lage (id, stand, quelle, daten) VALUES (1,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE stand=VALUES(stand), quelle=VALUES(quelle), "
            "daten=VALUES(daten)",
            (dt.datetime.now(), quelle or "?",
             json.dumps(daten, ensure_ascii=False)))


# Arten, die die 218 betreffen — die gehen immer raus, sie sind der eigentliche
# Zweck. Alles andere ist Lagemeldung und wird gedrosselt.
ARTEN_218 = {"verspaetung", "ausfall", "gleis"}


def stoerung_laeuft(conn, tag: dt.date) -> bool:
    """Ist heute schon eine Streckenstörung gemeldet und noch nicht entwarnt?

    Solange sie läuft, sind Ausfälle und Verspätungen einzelner Züge nur ihre
    Folgen — die einzeln zu melden, war der Grund für die Mailflut.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT art, gemeldet_am FROM stoerungen WHERE tag=%s "
                    "AND verschickt=1 AND art IN ('strecke','meldung','entwarnung') "
                    # id als zweites Kriterium: bei gleicher Sekunde wäre die
                    # Reihenfolge sonst offen und die Entwarnung ginge unter.
                    "ORDER BY gemeldet_am DESC, id DESC LIMIT 1", (tag,))
        letzte = cur.fetchone()
    return bool(letzte and letzte["art"] != "entwarnung")


def allgemein_gesperrt(conn, tag: dt.date, sperrfrist: int,
                       hoechstens: int) -> str | None:
    """Grund, warum eine allgemeine Meldung gerade unterbleibt — oder None."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n, MAX(gemeldet_am) AS letzte FROM stoerungen "
                    "WHERE tag=%s AND verschickt=1 AND art NOT IN %s "
                    "AND art <> 'entwarnung'", (tag, tuple(ARTEN_218)))
        r = cur.fetchone()
    if r["n"] >= hoechstens:
        return f"Tagesobergrenze von {hoechstens} allgemeinen Meldungen erreicht"
    if r["letzte"] and (dt.datetime.now() - r["letzte"]).total_seconds() < sperrfrist * 60:
        rest = int(sperrfrist - (dt.datetime.now() - r["letzte"]).total_seconds() / 60)
        return f"Sperrfrist läuft noch {rest} min"
    return None


def grund(eintrag: dict) -> str:
    """Ursachenzusatz für eine einzelne Fahrt, oder leer."""
    u = eintrag.get("ursachen") or []
    return f" — Ursache: {', '.join(u)}" if u else ""


def gruende_sammeln(eintraege: list[dict]) -> str:
    """Die genannten Ursachen aller betroffenen Fahrten, nach Häufigkeit.

    Die Störungsmeldung der Bahn selbst enthält keinen Freitext; die Ursachen
    der betroffenen Züge sind das Nächstbeste, um zu sagen, woran es liegt.
    """
    import collections
    zaehler = collections.Counter(
        u for e in eintraege for u in (e.get("ursachen") or []))
    if not zaehler:
        return ""
    teile = [f"{u} ({n}×)" if n > 1 else u for u, n in zaehler.most_common(4)]
    return " — genannte Ursachen: " + ", ".join(teile)


def heutige_laeufe(conn, tag: dt.date) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM laeufe WHERE tag=%s AND aktiv=1 ORDER BY elmshorn_zeit",
                    (tag,))
        return cur.fetchall()


def im_fenster(laeufe: list[dict], jetzt: dt.datetime, vorlauf: int,
               nachlauf: int) -> list[dict]:
    """Läufe, deren Durchfahrt nah genug ist, dass eine Meldung noch nützt."""
    aus = []
    for l in laeufe:
        if not l.get("elmshorn_zeit"):
            continue
        stunde, minute = (int(x) for x in l["elmshorn_zeit"].split(":"))
        wann = dt.datetime.combine(l["tag"], dt.time(stunde, minute), tzinfo=TZ)
        if -nachlauf <= (wann - jetzt).total_seconds() / 60 <= vorlauf:
            aus.append({**l, "wann": wann})
    return aus


def abfrage_stunden(laeufe: list[dict], jetzt: dt.datetime) -> int:
    """Wie weit die Echtzeit vorausschauen muss: mindestens drei Stunden, aber bis zur
    letzten 218 des Tages (höchstens zehn). Ausfälle kennt die Bahn oft Stunden
    vorher — am 18.09.2026 stand RE 11030 (19:58) schon um 16:40 als ausgefallen
    drin, Zugradar sah aber nur bis 19:20."""
    spaeteste = 0.0
    for l in laeufe:
        if not l.get("elmshorn_zeit"):
            continue
        stunde, minute = (int(x) for x in str(l["elmshorn_zeit"]).split(":"))
        wann = dt.datetime.combine(l["tag"], dt.time(stunde, minute), tzinfo=TZ)
        spaeteste = max(spaeteste, (wann - jetzt).total_seconds() / 3600)
    return max(3, min(10, int(spaeteste) + 1))


def schon_gemeldet(conn, tag: dt.date) -> dict[tuple[str, str], int]:
    with conn.cursor() as cur:
        cur.execute("SELECT art, bezug, wert FROM stoerungen WHERE tag=%s", (tag,))
        return {(r["art"], r["bezug"]): r["wert"] for r in cur.fetchall()}


def wurde_verschickt(conn, tag: dt.date, art: str, bezug: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT verschickt FROM stoerungen WHERE tag=%s AND art=%s AND bezug=%s",
                    (tag, art, bezug))
        r = cur.fetchone()
    return bool(r and r["verschickt"])


def merke(conn, tag: dt.date, art: str, bezug: str, wert: int,
          verschickt: bool = True) -> None:
    """Meldung vormerken. `verschickt=False` heißt: erkannt, aber zurückgehalten —
    zählt dann auch nicht gegen die Tagesobergrenze."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stoerungen (tag, art, bezug, wert, verschickt, gemeldet_am) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "wert=VALUES(wert), gemeldet_am=VALUES(gemeldet_am), "
            "verschickt=GREATEST(verschickt, VALUES(verschickt))",
            (tag, art, bezug, wert, 1 if verschickt else 0, dt.datetime.now()))


def baue_entwarnung(tag: dt.date, lage: list[dict]) -> tuple[str, str, str]:
    betreff = mb.mit_datum("Entwarnung Elmshorn: Lage wieder normal", tag)
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            f"Von {len(lage)} Zügen im Zeitfenster ist keiner mehr ausgefallen "
            f"oder stark verspätet.\n\nÜbersicht: https://jarritc.de/zugradar/\n")
    body_html = (
        mb.RAHMEN_AUF
        + f'<div style="font-size:13px;color:#57606a">{html.escape(mb.datum_lang(tag))}</div>'
        '<h2 style="margin:2px 0 12px;font-size:19px">Entwarnung</h2>'
        '<div style="background:#dafbe1;color:#0a5c2e;border-radius:8px;padding:10px 14px;'
        f'font-size:14px">Von {len(lage)} Zügen im Zeitfenster ist keiner mehr '
        'ausgefallen oder stark verspätet.</div>'
        '<p style="margin:16px 0 0"><a href="https://jarritc.de/zugradar/" '
        'style="color:#0969da;text-decoration:none;font-size:14px">Übersicht</a></p>'
        + mb.RAHMEN_ZU)
    return betreff, body_html, text


def baue_meldung(tag: dt.date, punkte: list[dict]) -> tuple[str, str, str]:
    arten = {p["art"] for p in punkte}
    ausfaelle = [p for p in punkte if p["art"] == "ausfall"]
    verspaetungen = [p for p in punkte if p["art"] in ("verspaetung", "linie")]
    gleiswechsel = [p for p in punkte if p["art"] == "gleis"]
    # Live-Meldungen betreffen immer heute — das Datum sparen wir uns.
    strecke = next((p for p in punkte if p["art"] == "strecke"), None)
    if strecke:
        betreff = f"Störung Elmshorn: {strecke.get('kurz') or str(strecke['wert']) + ' Züge betroffen'}"
    elif "meldung" in arten:
        betreff = "Störung Elmshorn (Meldung der Bahn)"
    elif gleiswechsel and len(punkte) == len(gleiswechsel):
        g = gleiswechsel[0]
        betreff = g.get("kurz") or f"Gleiswechsel: {g['titel']}"
    elif ausfaelle:
        betreff = (f"Ausfall: {ausfaelle[0].get('kurz') or ausfaelle[0]['titel']}"
                   + (f" +{len(ausfaelle) - 1}" if len(ausfaelle) > 1 else ""))
    else:
        p = max(verspaetungen, key=lambda x: x["wert"])
        betreff = f"+{p['wert']} min: {p.get('kurz') or p['titel']}"
    betreff = mb.mit_datum(betreff, tag)

    zeilen_text, zeilen_html = [], []
    for p in punkte:
        zeilen_text.append(f"  {p['titel']}: {p['text']}")
        zeilen_html.append(
            f'<tr><td style="padding:8px 10px;font-weight:bold;white-space:nowrap;'
            f'border-bottom:1px solid #eaeef2">{html.escape(p["titel"])}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eaeef2">'
            f'{html.escape(p["text"])}</td></tr>')

    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            + "\n".join(zeilen_text)
            + "\n\nDie Angaben stammen aus den Echtzeitdaten für Elmshorn.\n"
              "Übersicht: https://jarritc.de/zugradar/\n")

    body_html = (
        mb.RAHMEN_AUF
        + f'<div style="font-size:13px;color:#57606a">{html.escape(mb.datum_lang(tag))}</div>'
        f'<h2 style="margin:2px 0 12px;font-size:19px">{html.escape(betreff.split(" — ")[0])}</h2>'
        '<div style="background:#fff1e5;color:#9a3412;border-radius:8px;padding:10px 14px;'
        'font-size:14px;margin-bottom:14px">'
        + ('Betrifft die heutige 218 durch Elmshorn.'
           if any(p.get("ist_218") for p in punkte)
           else 'Betrifft die Strecke bei Elmshorn.') + '</div>'
        '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        + "".join(zeilen_html) + '</table>'
        '<p style="color:#57606a;font-size:12px;line-height:1.5;margin:16px 0 0">'
        'Grundlage sind die Echtzeitdaten für den Halt Elmshorn. Eine Meldung kommt je '
        'Zug einmal und dann erst wieder, wenn sich die Verspätung deutlich erhöht.</p>'
        '<p style="margin:16px 0 0"><a href="https://jarritc.de/zugradar/" '
        'style="color:#0969da;text-decoration:none;font-size:14px">Übersicht</a></p>'
        + mb.RAHMEN_ZU)
    return betreff, body_html, text


def lauf(cfg: dict, conn, dry_run: bool = False) -> None:
    fp = cfg.get("fahrplan") or {}
    if not fp.get("stop_id") or not fp.get("aktiv", True):
        log.info("Fahrplanquelle nicht konfiguriert — nichts zu tun.")
        return

    jetzt = dt.datetime.now(TZ)
    tag = jetzt.date()
    allgemein = bool(fp.get("allgemein_aktiv", True))
    mb.lade_namen(conn)          # für kurze Betreffzeilen mit Loknamen

    laeufe = heutige_laeufe(conn, tag)
    relevant = im_fenster(laeufe, jetzt, int(fp.get("vorlauf_minuten", 120)),
                          int(fp.get("nachlauf_minuten", 30))) if laeufe else []
    if not relevant and not allgemein:
        log.debug("Keine 218 im Fenster und allgemeine Überwachung aus.")
        return

    # Vorrang hat die DB-Schnittstelle: nur sie kennzeichnet Ausfälle ausdrücklich
    # und liefert die Störungsmeldungen der Bahn. Fällt sie aus, übernimmt
    # transitous — dann ohne Meldungstexte, aber die Lage stimmt trotzdem.
    lage, meldungen, quelle = [], [], None
    dbcfg = cfg.get("db_api") or {}
    if dbcfg.get("client_id"):
        try:
            lage, meldungen = db_timetables.ist_lage(
                dbcfg, dbcfg["eva_elmshorn"], stunden=abfrage_stunden(laeufe, jetzt))
            quelle = "DB Timetables"
        except Exception as exc:
            log.warning("DB Timetables nicht abrufbar: %s", exc)
    if not lage:
        try:
            # Ohne konkreten 218-Anlass genügt eine Seite — schont den freien Dienst.
            lage = fahrplan.ist_lage(fp["stop_id"], stunden=3,
                                     seiten=4 if relevant else 1)
            quelle = "transitous"
        except Exception as exc:
            log.warning("Auch transitous nicht abrufbar: %s — Durchlauf endet", exc)
            return
    if not lage:
        log.debug("Keine Fahrten im Zeitfenster.")
        return
    log.debug("%d Fahrten von %s", len(lage), quelle)

    schreibe_lage(conn, lage, meldungen, quelle)
    conn.commit()

    schwelle = int(fp.get("verspaetung_ab_minuten", 15))
    schwer = int(fp.get("allgemein_verspaetung_ab", 30))
    ab_zuegen = int(fp.get("stoerung_ab_zuegen", 3))
    linien = set(fp.get("allgemein_linien") or [])

    nach_nummer = {e["nummer"]: e for e in lage}
    bekannt = schon_gemeldet(conn, tag)
    punkte: list[dict] = []
    erledigt: set[str] = set()

    # ---- Stufe 1a: Ausfälle sofort — für jede 218, die heute noch kommt.
    # Verspätung und Gleis sind Stunden vorher nichts wert, ein Ausfall schon: Dann
    # braucht man gar nicht erst zum Bahnhof.
    zukunft = im_fenster(laeufe, jetzt, 24 * 60, 0) if laeufe else []
    fenster_nummern = {"".join(c for c in l["zug"] if c.isdigit()) for l in relevant}
    for l in zukunft:
        nummer = "".join(c for c in l["zug"] if c.isdigit())
        e = nach_nummer.get(nummer)
        if nummer in fenster_nummern or e is None or not e["ausgefallen"]:
            continue
        erledigt.add(nummer)
        if ("ausfall", nummer) not in bekannt:
            punkte.append({"art": "ausfall", "bezug": nummer, "wert": 1,
                           "titel": f"{l['lok']}, {l['zug']}", "ist_218": True,
                           "kurz": f"{mb.lok_kurz(l['lok'])} {l['zug']}",
                           "text": f"fällt aus (geplant {e['soll_hhmm']} in Elmshorn)" + grund(e)})

    # ---- Stufe 1: die heutige 218 einzeln
    for l in relevant:
        nummer = "".join(c for c in l["zug"] if c.isdigit())
        e = nach_nummer.get(nummer)
        if e is None:
            continue                       # Zug (noch) nicht in den Echtzeitdaten
        erledigt.add(nummer)
        titel = f"{l['lok']}, {l['zug']}"
        if e["ausgefallen"]:
            if ("ausfall", nummer) not in bekannt:
                punkte.append({"art": "ausfall", "bezug": nummer, "wert": 1,
                               "titel": titel, "ist_218": True,
                               "kurz": f"{mb.lok_kurz(l['lok'])} {l['zug']}",
                               "text": f"fällt aus (geplant {e['soll_hhmm']} in Elmshorn)"
                                       + grund(e)})
        elif e.get("gleis_geaendert") and ("gleis", nummer) not in bekannt:
            # Ein Gleiswechsel entscheidet, auf welchem Bahnsteig man steht.
            punkte.append({"art": "gleis", "bezug": nummer, "wert": 1,
                           "titel": titel, "ist_218": True,
                           "kurz": f"Gl. {e['gleis']} statt {e.get('gleis_geplant') or '?'} — {mb.lok_kurz(l['lok'])} {e['soll_hhmm']}",
                           "text": f"Gleiswechsel auf Gleis {e['gleis']} "
                                   f"(geplant war {e.get('gleis_geplant') or '?'}), "
                                   f"Elmshorn {e['soll_hhmm']}" + grund(e)})
        elif e["verspaetung"] >= schwelle:
            vorher = bekannt.get(("verspaetung", nummer))
            # Erneut erst, wenn es deutlich schlimmer wurde — sonst alle 10 Minuten Post.
            if vorher is None or e["verspaetung"] >= vorher + 10:
                punkte.append({"art": "verspaetung", "bezug": nummer,
                               "wert": e["verspaetung"], "titel": titel, "ist_218": True,
                               "kurz": f"{mb.lok_kurz(l['lok'])} Elmshorn {e['soll_hhmm']}",
                               "text": f"+{e['verspaetung']} min in Elmshorn — "
                                       f"planmäßig {e['soll_hhmm']}, jetzt "
                                       f"{e['ist_hhmm']}"
                                       + (f", Gleis {e['gleis']}" if e.get("gleis") else "")
                                       + grund(e)})

    # ---- Stufe 2: allgemeine Lage, unabhängig von der 218
    betroffen = [e for e in lage if e["ausgefallen"] or e["verspaetung"] >= schwelle]
    if allgemein:
        ausfaelle = sum(1 for e in betroffen if e["ausgefallen"])
        # Reine Verspätungen sind Alltag. Eine große Lage erkennt man daran, dass
        # viele Züge betroffen sind und davon etliche ausfallen.
        grosse_lage = (len(betroffen) >= ab_zuegen
                       and ausfaelle >= int(fp.get("stoerung_ab_ausfaellen", 1)))
        if grosse_lage and ("strecke", "strecke") not in bekannt:
            punkte.append({
                "art": "strecke", "bezug": "strecke", "wert": len(betroffen),
                "titel": "Strecke bei Elmshorn", "ist_218": False,
                "kurz": f"{len(betroffen)} Züge, {ausfaelle} Ausfälle",
                "text": f"{len(betroffen)} von {len(lage)} Zügen betroffen "
                        f"({ausfaelle} Ausfälle) — deutet auf eine Störung oder "
                        f"Sperrung hin" + gruende_sammeln(betroffen)})

        # Einzelne fremde Züge sind normaler Betriebsalltag und standardmäßig aus.
        # Was zählt, ist die Lage als Ganzes — siehe Streckenstörung oben.
        for e in (lage if fp.get("einzelmeldungen_linie", False) else []):
            if e["nummer"] in erledigt or (linien and e["linie"] not in linien):
                continue
            if e["ausgefallen"] and ("ausfall", e["nummer"]) not in bekannt:
                punkte.append({"art": "ausfall", "bezug": e["nummer"], "wert": 1,
                               "titel": f"{e['linie']} {e['nummer']}", "ist_218": False,
                               "text": f"fällt aus (geplant {e['soll_hhmm']}, "
                                       f"Richtung {e['ziel']})" + grund(e)})
            elif e["verspaetung"] >= schwer:
                vorher = bekannt.get(("linie", e["nummer"]))
                if vorher is None or e["verspaetung"] >= vorher + 15:
                    punkte.append({"art": "linie", "bezug": e["nummer"],
                                   "wert": e["verspaetung"], "ist_218": False,
                                   "titel": f"{e['linie']} {e['nummer']}",
                                   "text": f"+{e['verspaetung']} min in Elmshorn — "
                                           f"planmäßig {e['soll_hhmm']}, jetzt "
                                           f"{e['ist_hhmm']}, Richtung {e['ziel']}"
                                           + grund(e)})

        # Die Bahn meldet Störungen selbst — das ist belastbarer als der
        # Rückschluss aus der Zahl betroffener Züge.
        # pr=1 ist die höchste Stufe. Alles darunter ist Hinweisqualität und
        # muss nicht als Mail kommen — auf der Webseite steht es trotzdem.
        hoechstens_prio = int(fp.get("meldung_ab_prioritaet", 1))
        # Eine Bahn-Meldung allein reicht nicht mehr — sie kommt nur, wenn die
        # Lage selbst groß ist. Sonst hätte sie die Zugschwelle einfach umgangen.
        meldungen_erlaubt = grosse_lage or not fp.get("meldung_nur_bei_grosser_stoerung", True)
        for m in (db_timetables.aktive_stoerungen(meldungen) if meldungen_erlaubt else []):
            if ("meldung", m["id"]) in bekannt or m["prioritaet"] > hoechstens_prio:
                continue
            zeitraum = ""
            if m["von"] and m["bis"]:
                zeitraum = f", gemeldet {m['von']:%H:%M} bis {m['bis']:%H:%M}"
            punkte.append({"art": "meldung", "bezug": m["id"],
                           "wert": m["prioritaet"], "ist_218": False,
                           "titel": f"{m['kategorie']} (Bahn-Meldung)",
                           "text": f"Priorität {m['prioritaet']}{zeitraum}"
                                   # Für die Meldung selbst zählen die Ursachen
                                   # aller Fahrten im Fenster, nicht nur die der
                                   # stark verspäteten — sonst bliebe sie bei
                                   # hoher Schwelle ohne jede Angabe.
                                   + (gruende_sammeln(lage)
                                      or " — die Bahn nennt keinen Grund dazu")})

    # ---- Allgemeine Meldungen drosseln. Die 218 bleibt davon unberührt.
    if punkte:
        istEigen = lambda p: p["art"] in ARTEN_218 and p.get("ist_218")
        eigene = [p for p in punkte if istEigen(p)]
        fremde = [p for p in punkte if not istEigen(p)]
        if fremde:
            sperre = None
            # Eine Sperrung der Marschbahn ist nur dann eine Nachricht wert, wenn
            # an diesem Tag überhaupt eine 218 hier fährt. Sonst betrifft sie
            # eine Strecke, auf der nichts wartet, worauf man hinfahren würde.
            if fp.get("allgemein_nur_an_218_tagen", True) and not laeufe:
                sperre = "heute fährt keine 218 durch Elmshorn"
            elif stoerung_laeuft(conn, tag) and not any(
                    p["art"] in ("strecke", "meldung") for p in fremde):
                sperre = "Streckenstörung läuft bereits — Einzelmeldungen sind ihre Folgen"
            if sperre is None:
                sperre = allgemein_gesperrt(
                    conn, tag, int(fp.get("sperrfrist_minuten", 60)),
                    int(fp.get("allgemein_hoechstens_am_tag", 3)))
            if sperre:
                log.info("%d allgemeine Meldung(en) zurückgehalten: %s",
                         len(fremde), sperre)
                # Trotzdem vermerken, sonst kommen sie nach der Sperrfrist verspätet.
                if not dry_run:
                    for p in fremde:
                        merke(conn, tag, p["art"], p["bezug"], p["wert"],
                              verschickt=False)
                    conn.commit()
                punkte = eigene

    # ---- Entwarnung, wenn eine gemeldete Streckenstörung vorbei ist
    # Entwarnung nur, wenn die Störung auch wirklich rausging — sonst käme eine
    # Entwarnung zu etwas, wovon man nie gehört hat.
    entwarnung = (fp.get("entwarnung", True)
                  and ("entwarnung", "strecke") not in bekannt
                  and not betroffen
                  and (wurde_verschickt(conn, tag, "strecke", "strecke")
                       or any(a == "meldung" and wurde_verschickt(conn, tag, a, b)
                              for a, b in bekannt)))

    if not punkte and not entwarnung:
        log.debug("Alles im Rahmen (%d Fahrten geprüft).", len(lage))
        return

    if dry_run:
        if punkte:
            log.info("dry-run, nicht verschickt: %s", baue_meldung(tag, punkte)[0])
            print(baue_meldung(tag, punkte)[2])
        if entwarnung:
            log.info("dry-run, Entwarnung nicht verschickt")
            print(baue_entwarnung(tag, lage)[2])
        return

    if punkte:
        betreff, body_html, text = baue_meldung(tag, punkte)
        # Fällt eine 218 in Elmshorn aus, kommt sie nicht — das geht auch per Mail.
        art = ("ausfall_218" if any(p["art"] == "ausfall" and p.get("ist_218") for p in punkte)
               else "stoerung")
        erfolge = mb.melde(cfg, art, betreff, body_html, text)
        for p in punkte:
            merke(conn, tag, p["art"], p["bezug"], p["wert"])
        log.info("%s — %d Zustellungen", betreff, erfolge)

    if entwarnung:
        betreff, body_html, text = baue_entwarnung(tag, lage)
        erfolge = mb.melde(cfg, "entwarnung", betreff, body_html, text)
        merke(conn, tag, "entwarnung", "strecke", len(lage))
        log.info("%s — %d Zustellungen", betreff, erfolge)

    conn.commit()


def main() -> None:
    p = argparse.ArgumentParser(description="Verspätungs- und Störungswächter Elmshorn")
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

    cfg = mb.lade_config() if hasattr(mb, "lade_config") else __import__("json").load(
        open(mb.CONFIG_PATH, encoding="utf-8"))
    if not cfg["mail"].get("verify_tls", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    conn = mb.db_connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(SCHEMA_LAGE)
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = 'stoerungen' "
                        "AND column_name = 'verschickt'")
            if not cur.fetchone()["n"]:
                cur.execute("ALTER TABLE stoerungen ADD COLUMN verschickt TINYINT(1) "
                            "NOT NULL DEFAULT 1 AFTER wert")
        conn.commit()
        if args.setup:
            print("Tabelle stoerungen angelegt.")
            return
        lauf(cfg, conn, dry_run=args.dry_run)
        mb.herzschlag(conn, "stoerung")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

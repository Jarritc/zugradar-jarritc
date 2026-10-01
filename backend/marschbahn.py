#!/usr/bin/env python3
"""
Marschbahn-Wächter — überwacht das DSO-Forum 006 auf BR-218-Umläufe durch Elmshorn.

Quelle : https://www.drehscheibe-online.de/foren/list.php?006
Threads: "Lokeinsätze auf der Marschbahn [DB Regio], <Wochentag>, <TT.MM.JJ>"

Elmshorn wird in den Beiträgen nie genannt. Der Ort liegt bei Streckenkilometer
30,7 ab Hamburg-Altona, daher wird geometrisch entschieden: ein Umlauf berührt
Elmshorn, wenn ein Endpunkt südlich und einer nördlich davon liegt (STATIONEN).
Die Durchfahrtszeit wird über FAHRZEIT_PROFIL geschätzt und als "ca." ausgewiesen.

Aufruf:
    marschbahn.py --setup    Tabellen anlegen
    marschbahn.py            ein Durchlauf (für den stündlichen Cron)
    marschbahn.py --dry-run  wie oben, aber ohne Mailversand
"""
from __future__ import annotations

import argparse
import base64
import hmac
import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import unicodedata

import pymysql
import requests

import db_timetables
import fahrplan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "logs", "run.log")

UA = "Mozilla/5.0 (X11; Linux x86_64) Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"

log = logging.getLogger("marschbahn")


# --------------------------------------------------------------------------
# Streckengeometrie
# --------------------------------------------------------------------------
# Position entlang der Marschbahn, Süd -> Nord. Elmshorn = 20.
# Die Lücken zwischen den Werten lassen Platz für später ergänzte Halte.
# Streckenkilometer ab Hamburg-Altona, Quelle: Streckenbänder der Wikipedia-Artikel
# "Bahnstrecke Hamburg-Altona–Kiel" und "Bahnstrecke Elmshorn–Westerland".
# Die Kilometrierung läuft durchgehend, deshalb ordnet sie zugleich die Halte Süd->Nord.
ELMSHORN_KM = 30.697

STATIONEN: dict[str, float] = {
    "hamburg-altona": 0.0,
    "hamburg altona": 0.0,
    "hamburg hbf": 0.0,
    "hamburg dammtor": 0.0,
    "hamburg-diebsteich": 1.399,
    "hamburg-langenfelde": 2.635,
    "langenfelde": 2.635,
    "hamburg-eidelstedt": 6.1,
    "eidelstedt": 6.1,
    "elbgaustrasse": 7.234,
    "halstenbek": 12.028,
    "thesdorf": 14.233,
    "pinneberg": 15.953,
    "prisdorf": 19.262,
    "tornesch": 23.173,
    "elmshorn": ELMSHORN_KM,
    "herzhorn": 43.5,
    "glueckstadt": 47.4,
    "krempe": 54.2,
    "kremperheide": 59.6,
    "itzehoe": 64.9,
    "wilster": 74.2,
    "burg (dithm)": 91.4,
    "burg(dithm)": 91.4,
    "st. michaelisdonn": 101.2,
    "st.michaelisdonn": 101.2,
    "sankt michaelisdonn": 101.2,
    "windbergen": 105.5,
    "meldorf": 112.4,
    "hemmingstedt": 119.6,
    "heide (holstein)": 124.5,
    "heide(holstein)": 124.5,
    "heide (holst)": 124.5,
    "heide": 124.5,
    "lunden": 140.8,
    "friedrichstadt": 147.1,
    "husum": 158.3,
    "hattstedt": 165.8,
    "bredstedt": 176.8,
    "langenhorn": 184.3,
    "stedesand": 190.3,
    "lindholm": 194.1,
    "niebuell": 198.5,
    "klanxbuell": 211.7,
    "morsum": 228.8,
    "keitum": 233.3,
    "tinnum": 235.7,
    "westerland (sylt)": 237.7,
    "westerland(sylt)": 237.7,
    "westerland": 237.7,
}

# Fahrzeitprofil des RE6: Kilometer -> Minuten ab Hamburg-Altona. Die Stützstellen
# stammen aus 833 Zeitbeobachtungen in den Forenbeiträgen selbst (Median je Halt),
# nicht aus einem Fahrplan. Zwischen den Stützstellen wird linear interpoliert;
# gegen 41 Beiträge mit bekannter Zwischenhaltzeit geprüft: mittlerer Fehler
# 2,7 min, 90 % unter 5 min, kein systematischer Versatz.
FAHRZEIT_PROFIL: list[tuple[float, float]] = [
    (0.0, 0.0),        # Hamburg-Altona
    (15.953, 11.5),    # Pinneberg
    (64.9, 48.5),      # Itzehoe
    (124.5, 86.0),     # Heide (Holstein)
    (158.3, 116.0),    # Husum
    (198.5, 141.0),    # Niebüll
    (237.7, 183.0),    # Westerland (Sylt)
]


def profil_minuten(km: float) -> float:
    """Fahrzeit ab Hamburg-Altona bis Streckenkilometer km."""
    if km <= FAHRZEIT_PROFIL[0][0]:
        return FAHRZEIT_PROFIL[0][1]
    for (k1, m1), (k2, m2) in zip(FAHRZEIT_PROFIL, FAHRZEIT_PROFIL[1:]):
        if k1 <= km <= k2:
            return m1 + (m2 - m1) * (km - k1) / (k2 - k1)
    return FAHRZEIT_PROFIL[-1][1]


# Zeilen, die einen Zuglauf einleiten. "Verkehrt als 2. Wageneinheit ..." fehlt
# hier bewusst: dabei läuft nur der Wagenpark mit, nicht die Lok.
ZUGART_RE = re.compile(r"^(RE|RB|IC|ICE|EC|NJ|AS|Lr|Lz|Lt|Tfzf|DPN|DPE)\b", re.I)

# Lok-Kopfzeile, z. B.  "218 330-9" (Ozeanblau/Beige) + 36-81 026-7 + ...
LOK_RE = re.compile(r'^"?\s*(\d{3})\s+(\d{3}-\d)')

# Zugnummer am Zeilenanfang, z. B.  RE 11029,
ZUGNR_RE = re.compile(r"^([A-Za-z]{2,4})\s*(\d{3,5})\b")

UHRZEIT_RE = re.compile(r"\((\d{1,2}:\d{2})(?:/\d{1,2}:\d{2})?\)")

# Ab hier listet der SyltShuttle-Thread nur noch abgestellte Loks auf.
AUSSER_DIENST_RE = re.compile(r"nicht\s+im\s+einsatz", re.I)

# Überschriften wie "Husum:", "Hennigsdorf:" oder "Niebüll (BW/Süd/Terminal):"
# nennen den Standort der darunter aufgeführten Loks. Abschnitte für Wagen,
# Umläufe und Fremdstrecken sehen genauso aus und müssen raus — sonst stünde
# eine Lok plötzlich in "Married-Pair-Wagen".
KEIN_STANDORT_RE = re.compile(
    r"wagen|refresh|umlauf|intercity|übersicht|loks\s*/|weitere\s+loks|sonderfall|"
    r"doppelstock|married|einsätze|fahrzeug", re.I)


# In den Lok-Kopfzeilen stehen hinter der Nummer Lackierung, Programm, UIC-Nummer
# und manchmal ein Eigenname: "218 443-0 "DB Gebrauchtzug" (Donna)". Alles außer
# dem Namen muss raus — sonst hieße jede zweite Lok "Verkehrsrot".
KEIN_NAME = {
    "verkehrsrot", "ozeanblau", "beige", "ozeanblau/beige", "blau", "gruen", "grün",
    "orientrot", "altrot", "purpurrot", "tuerkis", "türkis", "silber", "weiss", "weiß",
    "schwarz", "schwarz/weiss", "schwarz/weiß", "warnlack", "intercity", "talgo",
    "db regio", "db fernverkehr", "db gebrauchtzug", "db cargo", "nah.sh", "press",
    "hvo sticker", "sylt", "werkstatt",
}
NAME_VERDAECHTIG = re.compile(r"\d|^uic\b|sticker|werkstatt|übersicht|uebersicht|"
                              r"aufenthalt|einsatz|führend|fuehrend|nach\s|ab\s", re.I)


def lok_name(zeile: str) -> str | None:
    """Eigenname aus einer Lok-Kopfzeile, oder None.

    Genommen wird nur, was in Klammern oder Anführungszeichen steht, höchstens
    zwei Wörter lang ist, groß anfängt und in keiner der bekannten Kategorien
    liegt. Im Zweifel lieber kein Name als ein falscher.
    """
    rest = re.sub(r'^"?\s*\d{3}\s+\d{3}-\d"?', "", zeile).split("+")[0]
    # Ein "(" direkt am Wort gehört zum Ortsnamen ("Westerland(Sylt)"), nicht zur Lok.
    stuecke = re.findall(r'(?:^|\s)\(([^)]{2,30})\)|"([^"]{2,30})"', rest)
    for a, b in stuecke:
        wert = (a or b).strip(" .,;:")
        if not wert or NAME_VERDAECHTIG.search(wert):
            continue
        if wert.lower() in KEIN_NAME or len(wert.split()) > 2:
            continue
        if not re.fullmatch(r"[A-ZÄÖÜ][\wÄÖÜäöüß.-]{2,}(?: [A-ZÄÖÜ][\wÄÖÜäöüß.-]{1,})?", wert):
            continue
        return wert
    return None


def standort_aus_ueberschrift(zeile: str) -> str | None:
    """Ortsname aus einer Abschnittsüberschrift, oder None."""
    name = zeile.rstrip(":").strip()
    if not name or len(name) > 48 or KEIN_STANDORT_RE.search(name):
        return None
    return name

# Zwei Threadreihen werden ausgewertet:
#   regio   "Lokeinsätze auf der Marschbahn [DB Regio], Montag, 08.09.26"
#   shuttle "Lokübersicht der SyltShuttle und ICE's auf der Marschbahn 08.09.26 / 245 024-5"
QUELLEN: dict[str, re.Pattern] = {
    "regio": re.compile(
        r"Lokeins[äa]tze\s+auf\s+der\s+Marschbahn\s*\[DB\s*Regio\]\s*,.*?"
        r"(\d{2})\.(\d{2})\.(\d{2})\s*$", re.I),
    "shuttle": re.compile(
        r"Lok[üu]bersicht\s+der\s+SyltShuttle\s+und\s+ICE.{0,3}s?\s+auf\s+der\s+"
        r"Marschbahn\s+(\d{2})\.(\d{2})\.(\d{2})", re.I),
}

QUELLE_LABEL = {"regio": "DB Regio", "shuttle": "SyltShuttle / ICE"}


def erkenne_thread(titel: str) -> tuple[str, dt.date] | None:
    """Quelle und Datum aus dem Threadtitel, oder None wenn uninteressant."""
    for quelle, muster in QUELLEN.items():
        m = muster.search(titel)
        if not m:
            continue
        tag, monat, jahr = (int(x) for x in m.groups())
        try:
            return quelle, dt.date(2000 + jahr, monat, tag)
        except ValueError:
            return None
    return None


def normalise(text: str) -> str:
    """Kleinschreibung + Umlaute auf ae/oe/ue/ss, damit Schreibvarianten treffen."""
    t = text.strip().lower()
    t = (t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
          .replace("ß", "ss"))
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t)


PLATZHALTER = {"", "-", "–", "?", "n/a"}
PLATZHALTER_NORM = {normalise(p) for p in PLATZHALTER}


def station_km(rohname: str) -> float | None:
    """Streckenkilometer eines Halts ab Hamburg-Altona, oder None wenn unbekannt."""
    n = normalise(rohname)
    n = re.sub(r"\((\d{1,2}:\d{2}).*?\)", "", n).strip()          # Zeiten raus
    n = re.sub(r"^(ab|an|via|ueber)\s+", "", n).strip()
    n = n.strip(" .,;:")
    if n in PLATZHALTER_NORM:
        return None
    if n in STATIONEN:
        return STATIONEN[n]
    # Längster passender Namensbestandteil gewinnt ("westerland(sylt)" vor "westerland")
    treffer = [(len(k), v) for k, v in STATIONEN.items() if k in n]
    if treffer:
        return max(treffer)[1]
    return None


# --------------------------------------------------------------------------
# Forum abrufen und zerlegen
# --------------------------------------------------------------------------
def http_get(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=25, headers={"User-Agent": UA})
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>", "", fragment, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def thread_liste(session: requests.Session, cfg: dict) -> dict[dt.date, dict[str, dict]]:
    """Alle interessanten Threads ab start_date: Datum -> Quelle -> Thread-Info."""
    base = cfg["forum"]["base"]
    board = cfg["forum"]["board"]
    start = dt.date.fromisoformat(cfg["start_date"])
    aktive_quellen = set(cfg["forum"].get("quellen", list(QUELLEN)))
    gefunden: dict[dt.date, dict[str, dict]] = {}

    for seite in range(1, int(cfg["forum"]["max_pages"]) + 1):
        url = f"{base}list.php?{board}" + (f",page={seite}" if seite > 1 else "")
        html_text = http_get(session, url)
        aelteste: dt.date | None = None

        for m in re.finditer(
            r'<a[^>]+href="([^"]*read\.php\?' + re.escape(board) + r',(\d+))"[^>]*>(.*?)</a>',
            html_text, re.S):
            titel = re.sub(r"\s+", " ", strip_tags(m.group(3))).strip()
            erkannt = erkenne_thread(titel)
            if not erkannt:
                continue
            quelle, datum = erkannt
            if aelteste is None or datum < aelteste:
                aelteste = datum
            if quelle not in aktive_quellen or datum < start:
                continue
            gefunden.setdefault(datum, {}).setdefault(quelle, {
                "datum": datum,
                "quelle": quelle,
                "thread_id": int(m.group(2)),
                "titel": titel,
                "url": m.group(1),
            })

        # Sobald eine Seite komplett vor dem Startdatum liegt, sind wir durch.
        if aelteste is not None and aelteste < start:
            break
        time.sleep(1.0)

    return gefunden


def beitrag_text(html_text: str) -> str:
    """Text des ersten Beitrags. Diese Threads bestehen immer nur aus einem."""
    i = html_text.find('class="message"')
    if i < 0:
        return ""
    j = html_text.find('class="body"', i)
    if j < 0:
        return ""
    # Hinter das schließende > des Tags springen, sonst steht der Attributrest
    # ('class="body">') als erstes im Text.
    start = html_text.find(">", j)
    start = start + 1 if start > 0 else j
    k = html_text.find('<div class="options"', start)
    return strip_tags(html_text[start:k if k > 0 else None])


def parse_umlauf(zeile: str) -> dict | None:
    """Eine Umlaufzeile in Zugnummer, Halte und Positionen zerlegen."""
    m = ZUGNR_RE.match(zeile)
    if m:
        zug = f"{m.group(1)} {m.group(2)}"   # Schreibweise aus dem Beitrag (Lz, RE, AS)
        rest = zeile[m.end():]
    else:
        kopf, _, rest = zeile.partition(",")
        if not rest:
            return None
        zug = kopf.strip()
    rest = rest.lstrip(" ,")

    teile = [t for t in re.split(r"\s+(?:->|-|–|—)\s+", rest) if t.strip()]
    halte: list[dict] = []
    unbekannt: list[str] = []
    for teil in teile:
        name = teil.split(",")[0].strip().lstrip("/ ").strip()
        zeit_m = UHRZEIT_RE.search(teil)
        km = station_km(name)
        klarname = re.sub(r"\s*\(\d{1,2}:\d{2}.*?\)", "", name).strip(" .,;:")
        if km is None:
            if klarname and not re.fullmatch(r"[\W\d]+", klarname):
                unbekannt.append(klarname)
            continue
        halte.append({"name": klarname, "km": km,
                      "zeit": zeit_m.group(1) if zeit_m else None})

    if not halte:
        return None
    return {"zug": zug, "halte": halte, "unbekannt": unbekannt, "zeile": zeile.strip()}


def beruehrt_elmshorn(halte: list[dict]) -> bool:
    """Wahr, wenn der Lauf Elmshorn quert oder dort beginnt/endet."""
    kms = [h["km"] for h in halte]
    lo, hi = min(kms), max(kms)
    return lo != hi and lo <= ELMSHORN_KM <= hi


def minuten(hhmm: str) -> int:
    stunde, minute = hhmm.split(":")
    return int(stunde) * 60 + int(minute)


def elmshorn_zeit(halte: list[dict]) -> str | None:
    """Geschätzte Durchfahrtszeit in Elmshorn, "HH:MM", oder None.

    Zwischen den beiden Halten mit Uhrzeit, die Elmshorn einschließen, wird über
    FAHRZEIT_PROFIL interpoliert — also nicht linear in Kilometern, sondern in
    Fahrzeit, damit der langsame Abschnitt über den Hindenburgdamm nicht auf die
    schnelle Geestrecke abfärbt.
    """
    mit_zeit = [h for h in halte if h["zeit"]]
    if len(mit_zeit) < 2:
        return None

    for h in mit_zeit:
        if abs(h["km"] - ELMSHORN_KM) < 0.01:      # hält selbst in Elmshorn
            return h["zeit"]

    paar = None
    for a, b in zip(mit_zeit, mit_zeit[1:]):
        lo, hi = sorted((a["km"], b["km"]))
        if lo <= ELMSHORN_KM <= hi:
            paar = (a, b)
            break
    if paar is None:                                # Elmshorn liegt außerhalb
        return None

    a, b = paar
    p_a, p_b, p_e = (profil_minuten(a["km"]), profil_minuten(b["km"]),
                     profil_minuten(ELMSHORN_KM))
    if p_b == p_a:
        return None

    t_a, t_b = minuten(a["zeit"]), minuten(b["zeit"])
    if t_b < t_a:                                   # Lauf über Mitternacht
        t_b += 1440
    t_e = t_a + (t_b - t_a) * (p_e - p_a) / (p_b - p_a)
    return f"{int(round(t_e)) // 60 % 24:02d}:{int(round(t_e)) % 60:02d}"


def analysiere_beitrag(text: str, alle_baureihen: bool = False) -> dict:
    """Beitragstext -> alle BR-218-Umläufe, markiert ob sie Elmshorn berühren.

    Der Beitrag ist eine Folge von Blöcken: eine Lok-Kopfzeile, darunter ihre
    Umläufe. Legendenzeilen in eckigen Klammern, Trennstriche und Abschnitts-
    überschriften beenden einen Block — sonst würde die Kopfzeile der Fahrzeug-
    liste ganz oben fälschlich Umläufe weiter unten einsammeln.
    """
    loks: list[str] = []          # aktueller Block, mehrere bei Doppeltraktion
    sammelt = False               # vorige Kopfzeile endete auf "+"
    ausser_dienst = False         # ab "Weitere Loks (Nicht im Einsatz)"
    standort: str | None = None   # Ort aus der letzten Abschnittsüberschrift
    laeufe: list[dict] = []
    alle_218: set[str] = set()
    unbekannte_halte: set[str] = set()
    standorte: dict[str, str] = {}
    namen: dict[str, str] = {}

    for roh in text.split("\n"):
        zeile = roh.strip()
        if not zeile:
            continue
        if AUSSER_DIENST_RE.search(zeile):
            ausser_dienst = True
        if re.fullmatch(r"[_\-–—=\s]{3,}", zeile) or zeile.startswith("["):
            loks, sammelt, standort = [], False, None
            continue

        m = LOK_RE.match(zeile)
        if m:
            lok = f"{m.group(1)} {m.group(2)}"
            # "218 497-6 (...) +" am Zeilenende heisst: die naechste Lok gehoert
            # zum selben Umlauf (Doppeltraktion vor dem Sylt Shuttle).
            loks = (loks + [lok]) if sammelt else [lok]
            sammelt = zeile.rstrip().endswith("+")
            if m.group(1) == "218" and not ausser_dienst:
                alle_218.add(lok)
            if standort:
                standorte.setdefault(lok, standort)
            gefunden = lok_name(zeile)
            if gefunden:
                namen.setdefault(lok, gefunden)
            continue

        sammelt = False
        if zeile.rstrip().endswith(":"):
            loks = []
            standort = standort_aus_ueberschrift(zeile)
            continue

        # Standard: nur die 218. Mit alle_baureihen=True auch 245/246 — der
        # Beobachtungs-Job braucht das, um einen Lokwechsel zu erkennen.
        aktiv_218 = [l for l in loks if alle_baureihen or l.startswith("218 ")]
        if not aktiv_218 or not ZUGART_RE.match(zeile):
            continue

        lauf = parse_umlauf(zeile)
        if not lauf:
            continue
        unbekannte_halte.update(lauf["unbekannt"])
        halte = lauf["halte"]
        von, nach = halte[0], halte[-1]
        for lok in aktiv_218:
            laeufe.append({
                "lok": lok,
                "zug": lauf["zug"],
                "von": von["name"], "von_zeit": von["zeit"],
                "nach": nach["name"], "nach_zeit": nach["zeit"],
                "richtung": "Richtung Hamburg" if von["km"] > nach["km"] else "Richtung Sylt",
                "zeile": lauf["zeile"],
                "elmshorn": beruehrt_elmshorn(halte),
                "elmshorn_zeit": elmshorn_zeit(halte),
            })

    return {"laeufe": laeufe, "loks_218": sorted(alle_218),
            "unbekannte_halte": sorted(unbekannte_halte),
            "standorte": standorte, "namen": namen}


def match_key(datum: dt.date, quelle: str, lauf: dict) -> str:
    """Wiedererkennung einer Fahrt: Tag, Quelle, Lok und Zugnummer.

    Bis 18.09.2026 ging hier der Wortlaut der ganzen Zeile ein. Dann wurde im Forum bei
    RE 11029 nachträglich ein Zwischenhalt ergänzt — dieselbe Fahrt galt als neu und die
    alte als gestrichen. Eine Zugnummer fährt je Tag nur einmal; mit der Lok im Schlüssel
    bleibt auch eine Doppeltraktion auseinander."""
    zug = re.sub(r"\s+", " ", str(lauf["zug"])).strip().upper()
    roh = f"{datum.isoformat()}|{quelle}|{lauf['lok']}|{zug}"
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS tage (
        thread_id       BIGINT       NOT NULL PRIMARY KEY,
        tag             DATE         NOT NULL,
        quelle          VARCHAR(16)  NOT NULL,
        titel           VARCHAR(255) NOT NULL,
        url             VARCHAR(255) NOT NULL,
        body_hash       CHAR(64)     NOT NULL,
        hat_218         TINYINT(1)   NOT NULL DEFAULT 0,
        hat_elmshorn    TINYINT(1)   NOT NULL DEFAULT 0,
        loks_218        VARCHAR(255) NOT NULL DEFAULT '',
        zuerst_gesehen  DATETIME     NOT NULL,
        zuletzt_geprueft DATETIME    NOT NULL,
        zuletzt_geaendert DATETIME   NULL,
        UNIQUE KEY uniq_tag_quelle (tag, quelle),
        KEY idx_tag (tag),
        KEY idx_elmshorn (hat_elmshorn)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS laeufe (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        tag             DATE         NOT NULL,
        quelle          VARCHAR(16)  NOT NULL,
        thread_id       BIGINT       NOT NULL,
        match_key       CHAR(64)     NOT NULL,
        lok             VARCHAR(32)  NOT NULL,
        zug             VARCHAR(32)  NOT NULL DEFAULT '',
        von_halt        VARCHAR(64)  NOT NULL DEFAULT '',
        von_zeit        VARCHAR(8)   NULL,
        nach_halt       VARCHAR(64)  NOT NULL DEFAULT '',
        nach_zeit       VARCHAR(8)   NULL,
        richtung        VARCHAR(24)  NOT NULL DEFAULT '',
        elmshorn_zeit   VARCHAR(8)   NULL,
        zeit_quelle     VARCHAR(12)  NULL,
        gleis           VARCHAR(8)   NULL,
        rohzeile        VARCHAR(500) NOT NULL,
        aktiv           TINYINT(1)   NOT NULL DEFAULT 1,
        gestrichen_am   DATETIME     NULL,
        streichung_gemeldet_am DATETIME NULL,
        gemeldet_am     DATETIME     NULL,
        melde_grund     VARCHAR(32)  NULL,
        zuerst_gesehen  DATETIME     NOT NULL,
        zuletzt_gesehen DATETIME     NOT NULL,
        UNIQUE KEY uniq_match (match_key),
        KEY idx_tag (tag),
        CONSTRAINT fk_lauf_thread FOREIGN KEY (thread_id)
            REFERENCES tage (thread_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS interesse (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        tag           DATE         NOT NULL,
        lok           VARCHAR(32)  NOT NULL,
        zug           VARCHAR(32)  NOT NULL,
        von_halt      VARCHAR(64)  NULL,
        von_zeit      VARCHAR(8)   NULL,
        nach_halt     VARCHAR(64)  NULL,
        nach_zeit     VARCHAR(8)   NULL,
        quelle        VARCHAR(8)   NOT NULL DEFAULT 'web',   -- 'mail' oder 'web'
        aktiv         TINYINT(1)   NOT NULL DEFAULT 1,
        erstellt_am   DATETIME     NOT NULL,
        letzter_check DATETIME     NULL,
        stand         VARCHAR(255) NULL,   -- zuletzt gemeldeter Zustand, gegen Wiederholungen
        stand_text    VARCHAR(255) NULL,   -- dasselbe lesbar, für die Webseite
        UNIQUE KEY uniq_fahrt (tag, lok, zug),
        KEY idx_aktiv (aktiv, tag)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS stationen (
        name     VARCHAR(64) NOT NULL PRIMARY KEY,
        eva      INT         NULL,
        geprueft DATETIME    NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tages_loks (
        tag             DATE        NOT NULL,
        lok             VARCHAR(32) NOT NULL,   -- '__stand__' = Ausgangsstand erfasst
        zuerst_gesehen  DATETIME    NOT NULL,
        in_tagesmeldung TINYINT(1)  NOT NULL DEFAULT 0,
        nachtrag_am     DATETIME    NULL,
        grund           VARCHAR(24) NULL,
        PRIMARY KEY (tag, lok)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS umlaeufe (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        tag        DATE         NOT NULL,
        quelle     VARCHAR(16)  NOT NULL,
        lok        VARCHAR(32)  NOT NULL,
        zug        VARCHAR(32)  NOT NULL DEFAULT '',
        von_halt   VARCHAR(64)  NOT NULL DEFAULT '',
        von_zeit   VARCHAR(8)   NULL,
        nach_halt  VARCHAR(64)  NOT NULL DEFAULT '',
        nach_zeit  VARCHAR(8)   NULL,
        richtung   VARCHAR(24)  NOT NULL DEFAULT '',
        elmshorn   TINYINT(1)   NOT NULL DEFAULT 0,
        elmshorn_zeit VARCHAR(8) NULL,
        gesehen_am DATETIME     NOT NULL,
        KEY idx_tag (tag, lok)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS lok_namen (
        lok            VARCHAR(32) NOT NULL PRIMARY KEY,
        name           VARCHAR(32) NOT NULL,
        zuerst_gesehen DATETIME    NOT NULL,
        zuletzt_gesehen DATETIME   NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS standorte (
        tag        DATE        NOT NULL,
        quelle     VARCHAR(16) NOT NULL,
        lok        VARCHAR(32) NOT NULL,
        ort        VARCHAR(64) NOT NULL,
        -- Nur für die 218 gefüllt: nur deren Umläufe wertet der Parser aus.
        hat_umlauf TINYINT(1)  NULL,
        gesehen_am DATETIME    NOT NULL,
        PRIMARY KEY (tag, quelle, lok),
        KEY idx_tag (tag)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tagesmeldung (
        tag         DATE        NOT NULL PRIMARY KEY,
        gesendet_am DATETIME    NOT NULL,
        treffer     TINYINT(1)  NOT NULL DEFAULT 0,
        anzahl      INT         NOT NULL DEFAULT 0,
        erfolg      TINYINT(1)  NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS laeufe_log (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        gestartet       DATETIME     NOT NULL,
        beendet         DATETIME     NULL,
        erfolg          TINYINT(1)   NOT NULL DEFAULT 0,
        threads_geprueft INT         NOT NULL DEFAULT 0,
        neue_laeufe     INT          NOT NULL DEFAULT 0,
        mails_versandt  INT          NOT NULL DEFAULT 0,
        unbekannte_halte TEXT        NULL,
        fehler          TEXT         NULL,
        KEY idx_gestartet (gestartet)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS meldungen_log (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        art              VARCHAR(32)  NOT NULL,      -- treffer, stoerung, sichtung, test …
        betreff          VARCHAR(255) NOT NULL,
        text             VARCHAR(500) NULL,          -- Kurzfassung (wie in der Push-Nachricht)
        kanaele          VARCHAR(32)  NOT NULL,      -- "mail", "push" oder "mail+push"
        mails            INT          NOT NULL DEFAULT 0,
        push_ok          INT          NULL,
        push_gesamt      INT          NULL,
        warteschlange_id INT          NULL,          -- Testnachrichten von der Geräteseite
        url              VARCHAR(300) NULL,
        erstellt_am      DATETIME     NOT NULL,
        KEY idx_zeit (erstellt_am)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        schluessel VARCHAR(64) NOT NULL PRIMARY KEY,
        wert       TEXT        NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def db_connect(cfg: dict) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=cfg["db"]["host"], user=cfg["db"]["user"],
        password=cfg["db"]["password"], database=cfg["db"]["database"],
        charset="utf8mb4", autocommit=False, connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )


# Spalten, die später dazugekommen sind. Werden nur angelegt, wenn sie fehlen —
# so bleibt --setup gefahrlos wiederholbar.
NACHRUESTUNG = [
    ("laeufe", "elmshorn_zeit",
     "ALTER TABLE laeufe ADD COLUMN elmshorn_zeit VARCHAR(8) NULL AFTER richtung"),
    ("laeufe", "zeit_quelle",
     "ALTER TABLE laeufe ADD COLUMN zeit_quelle VARCHAR(12) NULL AFTER elmshorn_zeit"),
    ("laeufe", "gleis",
     "ALTER TABLE laeufe ADD COLUMN gleis VARCHAR(8) NULL AFTER zeit_quelle"),
    ("laeufe", "gestrichen_am",
     "ALTER TABLE laeufe ADD COLUMN gestrichen_am DATETIME NULL AFTER aktiv"),
    ("laeufe", "streichung_gemeldet_am",
     "ALTER TABLE laeufe ADD COLUMN streichung_gemeldet_am DATETIME NULL AFTER gestrichen_am"),
]


def db_setup(conn) -> None:
    with conn.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)
        for tabelle, spalte, ddl in NACHRUESTUNG:
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (tabelle, spalte))
            if not cur.fetchone()["n"]:
                log.info("Spalte %s.%s wird angelegt", tabelle, spalte)
                cur.execute(ddl)
    conn.commit()


def meta_get(conn, schluessel: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT wert FROM meta WHERE schluessel = %s", (schluessel,))
        row = cur.fetchone()
    return row["wert"] if row else None


def herzschlag(conn, name: str) -> None:
    """Lebenszeichen eines Skripts. Die Logdatei taugt dafür nicht: Ein ruhiger Lauf
    schreibt nichts hinein, und dann hielte die Selbstüberwachung ihn für tot."""
    meta_set(conn, "lauf_" + name, dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()


def einstellung(conn, cfg: dict, bereich: str, name: str, standard):
    """Einstellung aus der Datenbank (in der App änderbar), sonst aus config.json.
    Die Webseite kann config.json nicht schreiben (chmod 600), deshalb landen dort
    gesetzte Werte in meta unter "einst_<bereich>_<name>"."""
    wert = meta_get(conn, f"einst_{bereich}_{name}")
    if wert is None:
        return (cfg.get(bereich) or {}).get(name, standard)
    if isinstance(standard, bool):
        return wert == "1"
    if isinstance(standard, int):
        try:
            return int(wert)
        except ValueError:
            return standard
    return wert


def meta_set(conn, schluessel: str, wert: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meta (schluessel, wert) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE wert = VALUES(wert)", (schluessel, wert))


# --------------------------------------------------------------------------
# Benachrichtigung
# --------------------------------------------------------------------------
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
              "Freitag", "Samstag", "Sonntag"]
WOCHENTAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

RAHMEN_AUF = (
    '<div style="background:#f4f5f7;padding:24px 0;margin:0">'
    '<div style="max-width:600px;margin:0 auto;font-family:Arial,Helvetica,sans-serif;color:#24292f">'
    '<div style="text-align:center;padding:0 0 14px">'
    '<img src="https://jarritc.de/zugradar/logo-mail.png" width="56" height="56" alt="Zugradar" '
    'style="display:inline-block;border:0"></div>'
    '<div style="background:#fff;border:1px solid #d8dee4;border-radius:10px;padding:20px">')
RAHMEN_ZU = (
    '</div><div style="color:#8b949e;font-size:11px;text-align:center;padding:12px 0">'
    'Automatische Meldung von Zugradar &middot; stündliche Prüfung von '
    'Drehscheibe-Online, Forum 49</div></div></div>')

HINWEIS_HTML = (
    '<p style="color:#57606a;font-size:12px;line-height:1.5;margin:16px 0 0">'
    'Die Elmshorn-Zeit ist die <b>Soll-Abfahrt laut Fahrplan</b>, über die Zugnummer '
    'zugeordnet. Steht dort kein passender Zug, wird sie aus den Forumsangaben '
    'geschätzt &mdash; dann ist sie mit &bdquo;geschätzt&ldquo; gekennzeichnet und kann '
    'einige Minuten danebenliegen.</p>')
HINWEIS_TEXT = (
    "Die Elmshorn-Zeit ist die Soll-Abfahrt laut Fahrplan, über die Zugnummer "
    "zugeordnet. Wo kein passender Zug gefunden wurde, steht \"geschätzt\" dabei — "
    "dann kann sie einige Minuten danebenliegen.")


def datum_lang(datum: dt.date) -> str:
    return f"{WOCHENTAGE[datum.weekday()]}, {datum.strftime('%d.%m.%Y')}"


def datum_kurz(datum: dt.date) -> str:
    return f"{WOCHENTAGE_KURZ[datum.weekday()]}, {datum.strftime('%d.%m.%y')}"


# Zwei Threadreihen, zwei Zugarten: die Regionalzüge der Marschbahn (RE6, DB Regio)
# und die Autozüge/IC nach Sylt (SyltShuttle). Beides sind 218er, aber eben nicht
# dasselbe — deshalb steht die Herkunft an jedem Umlauf.
QUELLE_KURZ = {"regio": "RE6 · DB Regio", "shuttle": "SyltShuttle / IC"}


def quelle_kurz(lauf: dict) -> str:
    return QUELLE_KURZ.get(str(lauf.get("quelle") or ""), "")


def render_laeufe(laeufe: list[dict], datum: dt.date | None = None) -> tuple[str, str]:
    """Umlaufliste als Klartext und HTML-Tabelle, nach Lok gruppiert.
    Vorne steht die geschätzte Durchfahrtszeit in Elmshorn — das ist die Zahl,
    nach der man sich richtet."""
    text, rows = [], []
    for lok in sorted({l["lok"] for l in laeufe}):
        text.append(f"\n{mit_besonderheit(lok)}")
        rows.append(
            f'<tr><td colspan="3" style="padding:14px 10px 4px;font-weight:bold;'
            f'font-size:15px;border-bottom:1px solid #d8dee4">'
            f'{html.escape(mit_besonderheit(lok))}</td></tr>')
        for l in (x for x in laeufe if x["lok"] == lok):
            vz = f" ({l['von_zeit']})" if l["von_zeit"] else ""
            nz = f" ({l['nach_zeit']})" if l["nach_zeit"] else ""
            zeit, exakt = zeit_text(l)
            gleis = l.get("gleis")
            strecke = f"{l['von']}{vz} -> {l['nach']}{nz}"
            text.append(f"  {zeit or '--:--':<9} Elmshorn"
                        f"{f', Gleis {gleis}' if gleis else ''}"
                        f"{'' if exakt else ' (geschätzt)'}"
                        f"   {l['zug']}: {strecke}  [{l['richtung']}"
                        + (f", {quelle_kurz(l)}" if quelle_kurz(l) else "") + "]"
                        + (merken_text(datum, lok, l["zug"]) if datum else ""))
            rows.append(
                '<tr>'
                '<td style="padding:8px 10px;white-space:nowrap;font-size:17px;'
                'font-weight:bold;border-bottom:1px solid #eaeef2">'
                + (html.escape(zeit) if zeit
                   else '<span style="color:#8b949e;font-size:13px;font-weight:normal">'
                        'Zeit unbekannt</span>')
                + (f'<div style="font-weight:600;font-size:12px;color:#0969da">'
                   f'Gleis {html.escape(str(gleis))}</div>' if gleis else '')
                + ('' if exakt or not zeit else
                   '<div style="font-weight:normal;font-size:11px;color:#8b949e">'
                   'geschätzt</div>')
                + '</td>'
                f'<td style="padding:8px 10px;white-space:nowrap;color:#57606a;'
                f'border-bottom:1px solid #eaeef2">{html.escape(l["zug"])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #eaeef2">'
                f'{html.escape(l["von"] + vz)} &rarr; {html.escape(l["nach"] + nz)}'
                f'<span style="color:#8b949e;font-size:12px"> &middot; '
                f'{html.escape(l["richtung"])}'
                + (f' &middot; {html.escape(quelle_kurz(l))}' if quelle_kurz(l) else '')
                + '</span>'
                + (merken_html(datum, lok, l["zug"]) if datum else '')
                + '</td></tr>')
    tabelle = ('<table style="width:100%;border-collapse:collapse;font-size:14px">'
               + "".join(rows) + '</table>') if rows else ""
    return "\n".join(text), tabelle


# Eigennamen der Loks, einmal je Lauf aus der Datenbank geholt.
LOK_NAMEN: dict[str, str] = {}


# Kurzbeschreibung besonderer Loks ("ozeanblau/elfenbein", "Werbelok PIKO/Märklin").
LOK_BESONDERS: dict[str, str] = {}


def lade_namen(conn) -> None:
    """Namen aus zwei Quellen: besondere_loks (Wikipedia) als Grundlage, darüber
    die im Forum selbst gesehenen — die sind näher am aktuellen Stand."""
    LOK_NAMEN.clear()
    LOK_BESONDERS.clear()
    with conn.cursor() as cur:
        cur.execute("SELECT table_name AS t FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = 'besondere_loks'")
        if cur.fetchone():
            cur.execute("SELECT lok, name, lackierung, bemerkung FROM besondere_loks")
            for r in cur.fetchall():
                if r["name"]:
                    LOK_NAMEN[r["lok"]] = r["name"]
                kurz = besonders_kurz(r["lackierung"], r["bemerkung"], r["name"])
                if kurz:
                    LOK_BESONDERS[r["lok"]] = kurz
        cur.execute("SELECT lok, name FROM lok_namen")
        LOK_NAMEN.update({r["lok"]: r["name"] for r in cur.fetchall()})


def besonders_kurz(lackierung: str | None, bemerkung: str | None,
                   name: str | None = None) -> str | None:
    """Das, was man an der Lok sieht — nicht Eigentümer oder Motorvariante."""
    teile = []
    if name and bemerkung:
        bemerkung = bemerkung.replace(f"Aufschrift „{name}“", "")   # steht schon als Name da
    if bemerkung and "PIKO/Märklin" in bemerkung:
        teile.append("Werbelok PIKO/Märklin")
    elif bemerkung and re.search(r"IC-Design|HERING-Design", bemerkung):
        teile.append(re.search(r"IC-Design|HERING-Design", bemerkung).group(0))
    elif bemerkung and (m := re.search(r"Aufschrift „([^“]+)“", bemerkung)):
        teile.append(f"Aufschrift „{m.group(1)}“")
    if lackierung and lackierung.lower() != "verkehrsrot" and not teile:
        teile.append(lackierung)
    elif lackierung and lackierung.lower() != "verkehrsrot" and "PIKO" not in teile[0]:
        teile.append(lackierung)
    return ", ".join(teile) or None


def merken_link(cfg: dict, datum: dt.date, lok: str, zug: str) -> str | None:
    """Signierter Link "diese Fahrt interessiert mich". Die Webseite (_kukas.php)
    prüft die Signatur mit demselben Schlüssel — ohne ihn lässt sich kein gültiger
    Link bauen, also auch keine fremde Fahrt eintragen."""
    import base64, hmac, hashlib
    m = cfg.get("merken") or {}
    if not m.get("geheimnis"):
        return None
    nutz = base64.urlsafe_b64encode(f"{datum.isoformat()}|{lok}|{zug}".encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(m["geheimnis"].encode(), nutz.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{m.get('basis_url', 'https://jarritc.de/zugradar/merken.php')}?t={nutz}.{sig}"


MERKEN_CFG: dict = {}      # in lauf() gesetzt, damit die Mailbausteine Links bauen können


def merken_text(datum: dt.date, lok: str, zug: str) -> str:
    url = merken_link(MERKEN_CFG, datum, lok, zug) if MERKEN_CFG else None
    return f"\n        ★ interessiert mich: {url}" if url else ""


def merken_html(datum: dt.date, lok: str, zug: str) -> str:
    url = merken_link(MERKEN_CFG, datum, lok, zug) if MERKEN_CFG else None
    return (f' <a href="{html.escape(url)}" style="color:#0969da;text-decoration:none;'
            f'font-size:12px;white-space:nowrap">☆ interessiert mich</a>') if url else ""


def lok_kurz(lok: str) -> str:
    """Für Betreffzeilen: "218 330-9 „Konrad“" — volle Nummer, mit Namen."""
    name = LOK_NAMEN.get(lok)
    return f"{lok} \u201e{name}\u201c" if name else lok


def mit_datum(text: str, datum: dt.date) -> str:
    """Betreffzeilen einheitlich: Inhalt vorn, Datum immer am Ende."""
    return f"{text} · {datum_mini(datum)}"


def datum_mini(datum: dt.date) -> str:
    """"Mi 16.9." — das kürzeste eindeutige Datum für Betreffzeilen."""
    return f"{WOCHENTAGE_KURZ[datum.weekday()]} {datum.day}.{datum.month}."


def mit_besonderheit(lok: str) -> str:
    """"218 330-9 „Konrad“ (ozeanblau/elfenbein)" — für Mailtexte, nicht für Betreffzeilen."""
    zusatz = LOK_BESONDERS.get(lok)
    return mit_name(lok) + (f" ({zusatz})" if zusatz else "")


def mit_name(lok: str) -> str:
    """"218 443-0" wird zu "218 443-0 \u201eDonna\u201c", wenn ein Name bekannt ist."""
    name = LOK_NAMEN.get(lok)
    return f"{lok} \u201e{name}\u201c" if name else lok


def zeit_text(l: dict) -> tuple[str, bool]:
    """Anzeigetext der Elmshorn-Zeit und ob sie exakt aus dem Fahrplan stammt."""
    zeit = l.get("elmshorn_zeit")
    if not zeit:
        return "", False
    exakt = l.get("zeit_quelle") == "fahrplan"
    return (zeit if exakt else f"ca. {zeit}"), exakt


def knopf(url: str, beschriftung: str) -> str:
    return (f'<a href="{html.escape(url)}" style="background:#0969da;color:#fff;'
            'text-decoration:none;padding:9px 16px;border-radius:6px;font-size:14px;'
            f'display:inline-block;margin:0 6px 6px 0">{html.escape(beschriftung)}</a>')


def baue_mail(datum: dt.date, laeufe: list[dict], url: str) -> tuple[str, str, str]:
    """Sofortmeldung, wenn in einem bereits bekannten Thread neue Läufe auftauchen."""
    loks = sorted({l["lok"] for l in laeufe})
    zeiten = sorted(l["elmshorn_zeit"] for l in laeufe if l.get("elmshorn_zeit"))
    erste = min((l for l in laeufe if l.get("elmshorn_zeit")),
                key=lambda l: l["elmshorn_zeit"], default=None)
    if len(loks) == 1 and erste:
        gleis = erste.get("gleis")
        betreff = mit_datum(f"{lok_kurz(loks[0])} {zeit_text(erste)[0]} Elmshorn"
                            + (f" Gl. {gleis}" if gleis else ""), datum)
    elif len(loks) == 1:
        betreff = mit_datum(f"{lok_kurz(loks[0])} durch Elmshorn", datum)
    else:
        betreff = mit_datum(f"{len(loks)}× 218 durch Elmshorn", datum)

    text_teil, tabelle = render_laeufe(laeufe, datum)
    text = (f"{betreff}\n{'=' * len(betreff)}\n{text_teil}\n\n{HINWEIS_TEXT}\n\n"
            f"Thread:    {url}\n"
            "Übersicht: https://jarritc.de/zugradar/\n")
    body_html = (
        RAHMEN_AUF
        + f'<h2 style="margin:0 0 4px;font-size:19px">{html.escape(betreff)}</h2>'
        '<div style="color:#57606a;font-size:13px;margin-bottom:14px">Marschbahn '
        '&middot; RE6 Westerland(Sylt) &ndash; Hamburg-Altona</div>'
        + tabelle + HINWEIS_HTML
        + '<p style="margin:16px 0 0">' + knopf(url, "Thread im Forum")
        + '<a href="https://jarritc.de/zugradar/" style="color:#0969da;text-decoration:none;'
          'padding:9px 8px;font-size:14px">Übersicht</a></p>'
        + RAHMEN_ZU)
    return betreff, body_html, text


def fahrt_kurz(f: dict) -> str:
    """"Husum 07:52 → Niebüll 08:20" aus einem Umlauf."""
    von = f["von"] + (f" {f['von_zeit']}" if f.get("von_zeit") else "")
    nach = f["nach"] + (f" {f['nach_zeit']}" if f.get("nach_zeit") else "")
    return f"{von} → {nach}"


def standort_uebersicht(standorte: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Abgestellte 218er je Ort, der größte Standort zuerst."""
    nach_ort: dict[str, list[str]] = {}
    for lok, ort in sorted(standorte.items()):
        if lok.startswith("218 "):
            nach_ort.setdefault(ort, []).append(mit_besonderheit(lok))
    return sorted(nach_ort.items(), key=lambda x: (-len(x[1]), x[0]))


def baue_tagesmeldung(datum: dt.date, laeufe: list[dict],
                      quellen: dict[str, dict]) -> tuple[str, str, str]:
    """Tagesmeldung: geht einmal raus, sobald die Liste für einen Tag da ist —
    ausdrücklich auch dann, wenn keine 218 durch Elmshorn fährt."""
    loks = sorted({l["lok"] for l in laeufe})
    frueheste = min((l for l in laeufe if l.get("elmshorn_zeit")),
                    key=lambda l: l["elmshorn_zeit"], default=None)
    if laeufe:
        if len(loks) == 1 and frueheste:
            gleis = frueheste.get("gleis")
            betreff = mit_datum(f"{lok_kurz(loks[0])} {zeit_text(frueheste)[0]} Elmshorn"
                                + (f" Gl. {gleis}" if gleis else ""), datum)
        else:
            betreff = mit_datum(f"{len(loks)}× 218 durch Elmshorn"
                                + (f", ab {zeit_text(frueheste)[0]}" if frueheste else ""), datum)
        kopf = (f"{len(laeufe)} Umlauf durch Elmshorn" if len(laeufe) == 1
                else f"{len(laeufe)} Umläufe durch Elmshorn")
        farbe, hintergrund = "#0a5c2e", "#dafbe1"
    else:
        # Keine in Elmshorn — dann wenigstens sagen, welche 218er überhaupt fahren.
        fahrende = sorted({f["lok"] for info in quellen.values() for f in info.get("fahrten") or []})
        betreff = mit_datum("Keine 218 in Elmshorn"
                            + (f", {', '.join(LOK_NAMEN.get(l) or l for l in fahrende[:2])}"
                               f"{' +' + str(len(fahrende) - 2) if len(fahrende) > 2 else ''} unterwegs"
                               if fahrende else ""), datum)
        kopf, farbe = "Keine 218 durch Elmshorn", "#57606a"
        hintergrund = "#eef1f4"

    text_teil, tabelle = render_laeufe(laeufe, datum)

    quell_text, quell_html, knoepfe = [], [], []
    for quelle, info in sorted(quellen.items()):
        label = QUELLE_LABEL.get(quelle, quelle)
        genannt = info.get("loks_218") or "keine 218 genannt"
        orte = standort_uebersicht(info.get("standorte") or {})
        zeilen = [f"      {ort}: {', '.join(loks)}" for ort, loks in orte]
        fahrten = [f"      {mit_besonderheit(f['lok'])}  {f['zug']}  {fahrt_kurz(f)}"
                   + merken_text(datum, f["lok"], f["zug"])
                   for f in (info.get("fahrten") or [])]
        quell_text.append(f"  {label}: {genannt}\n    {info['url']}"
                          + ("\n    Fahrten:\n" + "\n".join(fahrten) if fahrten else "")
                          + ("\n    Abgestellt:\n" + "\n".join(zeilen) if orte else ""))
        quell_html.append(
            f'<li style="margin-bottom:8px"><b>{html.escape(label)}</b>: '
            f'{html.escape(genannt)}'
            + ('<div style="color:#57606a;font-size:12px;margin-top:3px">Fahrten:'
               + "".join(
                   f'<div style="margin-left:10px"><b>{html.escape(mit_besonderheit(f["lok"]))}</b> '
                   f'{html.escape(f["zug"])}: {html.escape(fahrt_kurz(f))}'
                   + (' <b style="color:#0a5c2e">· durch Elmshorn</b>' if f.get("elmshorn") else '')
                   + merken_html(datum, f["lok"], f["zug"])
                   + '</div>'
                   for f in info.get("fahrten") or [])
               + '</div>' if info.get("fahrten") else '')
            + ('<div style="color:#57606a;font-size:12px;margin-top:3px">Abgestellt:'
               + "".join(
                   f'<div style="margin-left:10px"><b>{html.escape(ort)}</b> '
                   f'({len(loks)}): {html.escape(", ".join(loks))}</div>'
                   for ort, loks in orte)
               + '</div>' if orte else '') + '</li>')
        knoepfe.append(knopf(info["url"], f"Thread {label}"))

    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            f"{datum_lang(datum)} — {kopf}.\n"
            + (f"{text_teil}\n" if laeufe else "")
            + "\nIm Beitrag genannte 218er:\n" + "\n".join(quell_text)
            + f"\n\n{HINWEIS_TEXT}\n\nÜbersicht: https://jarritc.de/zugradar/\n")

    body_html = (
        RAHMEN_AUF
        + f'<div style="font-size:13px;color:#57606a">{html.escape(datum_lang(datum))}</div>'
        f'<h2 style="margin:2px 0 12px;font-size:19px">{html.escape(kopf)}</h2>'
        f'<div style="background:{hintergrund};color:{farbe};border-radius:8px;'
        f'padding:10px 14px;font-size:14px;margin-bottom:14px">'
        + (html.escape(", ".join(loks)) if laeufe
           else "In der Tagesliste fährt keine 218 über Elmshorn.")
        + '</div>'
        + tabelle
        + '<p style="font-size:13px;color:#57606a;margin:16px 0 4px">Im Beitrag genannte 218er</p>'
        f'<ul style="font-size:13px;margin:0;padding-left:20px">{"".join(quell_html)}</ul>'
        + HINWEIS_HTML
        + '<p style="margin:16px 0 0">' + "".join(knoepfe)
        + '<a href="https://jarritc.de/zugradar/" style="color:#0969da;text-decoration:none;'
          'padding:9px 8px;font-size:14px">Übersicht</a></p>'
        + RAHMEN_ZU)
    return betreff, body_html, text


# Markierung in tages_loks: für diesen Tag ist der Ausgangsstand festgehalten.
# Fehlt sie, kennt der Wächter die Loks der Tagesmeldung nicht — dann wird der
# Stand still erfasst, statt jede Lok als "neu" nachzumelden.
STAND = "__stand__"


def loks_mit_fahrten(quellen: dict[str, dict]) -> dict[str, list[dict]]:
    """Alle 218er mit mindestens einer Fahrt, über beide Threads hinweg."""
    aus: dict[str, list[dict]] = {}
    for quelle, info in sorted(quellen.items()):
        for f in info.get("fahrten") or []:
            aus.setdefault(f["lok"], []).append({**f, "quelle": quelle})
    return aus


def merke_tages_loks(conn, datum: dt.date, loks, in_tagesmeldung: bool,
                     grund: str | None = None, nachtrag: bool = False) -> None:
    now = dt.datetime.now()
    with conn.cursor() as cur:
        for lok in [STAND, *loks]:
            cur.execute(
                "INSERT INTO tages_loks (tag, lok, zuerst_gesehen, in_tagesmeldung, "
                " nachtrag_am, grund) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE lok=lok",
                (datum, lok, now, 1 if in_tagesmeldung else 0,
                 now if nachtrag and lok != STAND else None,
                 None if lok == STAND else grund))


def baue_nachtrag(datum: dt.date, neue: dict[str, list[dict]],
                  quellen: dict[str, dict]) -> tuple[str, str, str]:
    """Kurze Mail: nach der Tagesmeldung ist eine weitere 218 in den Beitrag gekommen."""
    loks = sorted(neue)
    if len(loks) == 1:
        betreff = mit_datum(f"Nachtrag: {lok_kurz(loks[0])} fährt", datum)
    else:
        betreff = mit_datum(f"Nachtrag: {len(loks)} weitere 218 fahren", datum)

    text_teile, html_teile = [], []
    for lok in loks:
        fahrten = neue[lok]
        text_teile.append(f"\n{mit_besonderheit(lok)}  ({len(fahrten)} Fahrten)")
        html_teile.append(
            f'<div style="font-weight:bold;font-size:15px;margin:14px 0 6px">'
            f'{html.escape(mit_besonderheit(lok))} '
            f'<span style="font-weight:normal;color:#57606a;font-size:13px">'
            f'{len(fahrten)} Fahrten</span></div>')
        for f in fahrten:
            text_teile.append(f"  {f['zug']:<10} {fahrt_kurz(f)}" + merken_text(datum, lok, f["zug"]))
            html_teile.append(
                f'<div style="font-size:14px;padding:3px 0 3px 10px;border-left:3px solid #d8dee4">'
                f'<span style="color:#57606a">{html.escape(f["zug"])}</span> &nbsp;'
                f'{html.escape(fahrt_kurz(f))}{merken_html(datum, lok, f["zug"])}</div>')

    urls = sorted({(q, quellen[q]["url"]) for f in sum(neue.values(), [])
                   for q in [f["quelle"]] if q in quellen})
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            f"Nach der Tagesmeldung wurde der Beitrag für {datum_lang(datum)} ergänzt.\n"
            + "\n".join(text_teile)
            + "\n\nKeine dieser Fahrten führt durch Elmshorn.\n\n"
            + "".join(f"Thread {QUELLE_LABEL.get(q, q)}: {u}\n" for q, u in urls)
            + "Übersicht: https://jarritc.de/zugradar/\n")
    body_html = (
        RAHMEN_AUF
        + f'<div style="font-size:13px;color:#57606a">{html.escape(datum_lang(datum))}</div>'
        f'<h2 style="margin:2px 0 8px;font-size:19px">Nachtrag: '
        f'{html.escape(", ".join(mit_name(l) for l in loks))}</h2>'
        '<div style="color:#57606a;font-size:13px;margin-bottom:6px">Nach der Tagesmeldung '
        'wurde der Beitrag ergänzt. Keine dieser Fahrten führt durch Elmshorn.</div>'
        + "".join(html_teile)
        + '<p style="margin:18px 0 0">'
        + "".join(knopf(u, f"Thread {QUELLE_LABEL.get(q, q)}") for q, u in urls)
        + '<a href="https://jarritc.de/zugradar/" style="color:#0969da;text-decoration:none;'
          'padding:9px 8px;font-size:14px">Übersicht</a></p>'
        + RAHMEN_ZU)
    return betreff, body_html, text


def baue_streichung(datum: dt.date, laeufe: list[dict],
                    url: str) -> tuple[str, str, str]:
    """Meldung, wenn ein bereits angekündigter Lauf wieder aus dem Beitrag
    verschwindet — sonst führe man umsonst nach Elmshorn."""
    loks = sorted({l["lok"] for l in laeufe})
    if len(laeufe) == 1:
        l = laeufe[0]
        zeit = f" {zeit_text(l)[0]}" if l.get("elmshorn_zeit") else ""
        betreff = mit_datum(f"Gestrichen: {lok_kurz(l['lok'])}{zeit} Elmshorn", datum)
    else:
        betreff = mit_datum(f"Gestrichen: {len(laeufe)}× 218 Elmshorn", datum)

    text_teil, tabelle = render_laeufe(laeufe)
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            f"Diese Umläufe standen vorher im Beitrag für {datum_lang(datum)} "
            f"und wurden dort inzwischen entfernt:\n"
            f"{text_teil}\n\n"
            f"Betroffene Loks: {', '.join(loks)}\n\n"
            f"Thread:    {url}\nÜbersicht: https://jarritc.de/zugradar/\n")

    body_html = (
        RAHMEN_AUF
        + f'<div style="font-size:13px;color:#57606a">{html.escape(datum_lang(datum))}</div>'
        f'<h2 style="margin:2px 0 12px;font-size:19px">{html.escape(betreff.split(":", 1)[0])}: '
        f'{html.escape(", ".join(loks))}</h2>'
        '<div style="background:#fff1e5;color:#9a3412;border-radius:8px;padding:10px 14px;'
        'font-size:14px;margin-bottom:14px">Diese Umläufe standen vorher im Beitrag und '
        'wurden dort inzwischen entfernt.</div>'
        + tabelle
        + '<p style="color:#57606a;font-size:12px;line-height:1.5;margin:16px 0 0">'
          'Die Tagesbeiträge werden im Lauf des Tages mehrfach bearbeitet. Was hier steht, '
          'ist also nicht ausgefallen, sondern aus der Planung genommen &mdash; oft ersetzt '
          'durch eine 245 oder 246.</p>'
        + '<p style="margin:16px 0 0">' + knopf(url, "Thread im Forum")
        + '<a href="https://jarritc.de/zugradar/" style="color:#0969da;text-decoration:none;'
          'padding:9px 8px;font-size:14px">Übersicht</a></p>'
        + RAHMEN_ZU)
    return betreff, body_html, text


def sende_mail(cfg: dict, betreff: str, body_html: str, text: str) -> int:
    """Verschickt an alle Empfänger einzeln. Gibt die Zahl der Erfolge zurück."""
    erfolge = 0
    for empf in cfg["mail"]["recipients"]:
        payload = {
            "to": [{"email": empf["email"], "name": empf.get("name", "")}],
            "from_name": cfg["mail"]["from_name"],
            "subject": betreff,
            "body_html": body_html,
            "body_text": text,
            "auto_submitted": "auto-generated",
        }
        try:
            r = requests.post(
                cfg["mail"]["api"], json=payload, timeout=20,
                verify=bool(cfg["mail"].get("verify_tls", False)),
                headers={"Content-Type": "application/json",
                         "X-API-Key": cfg["mail"]["key"]})
            if 200 <= r.status_code < 300:
                erfolge += 1
                log.info("Mail an %s: ok", empf["email"])
            else:
                log.error("Mail an %s: HTTP %s — %s",
                          empf["email"], r.status_code, r.text[:300])
        except Exception as exc:
            log.error("Mail an %s fehlgeschlagen: %s", empf["email"], exc)
    return erfolge


# --------------------------------------------------------------------------
# Meldewege: Push aufs Handy, Mail nur für das Wichtigste
# --------------------------------------------------------------------------
# Seit 17.09.2026 geht alles als Push an die App; per Mail nur noch, ob eine 218
# durch Elmshorn kommt (oder doch nicht). In config.json unter "kanaele" lässt sich
# jede Art einzeln umstellen, z. B. "sichtung": ["mail", "push"].
KANAELE_STANDARD: dict[str, tuple[str, ...]] = {
    "treffer":              ("mail", "push"),   # neue Fahrt durch Elmshorn
    "tagesmeldung_treffer": ("mail", "push"),   # Tagesliste mit 218 durch Elmshorn
    "streichung":           ("mail", "push"),   # gemeldete Elmshorn-Fahrt entfällt
    "ausfall_218":          ("mail", "push"),   # 218 in Elmshorn fällt laut Echtzeit aus
    "tagesmeldung_leer":    ("push",),          # Tagesliste ohne Treffer
    "nachtrag":             ("push",),          # weitere 218, aber nicht durch Elmshorn
    "stoerung":             ("push",),          # Verspätung, Gleis, Streckenstörung
    "entwarnung":           ("push",),
    "sichtung":             ("push",),          # Forenbeiträge im Umkreis
    "sonderzug":            ("push",),
    "beobachtung":          ("push",),          # vorgemerkte Fahrten
    "erinnerung":           ("push",),          # kurz vor der Durchfahrt (ohne Mail-Ersatz)
    "wacht":                ("mail", "push"),   # Selbstüberwachung: auch per Mail, denn
                                                # womöglich ist gerade der Push-Weg gestört
}
UEBERSICHT_URL = "https://jarritc.de/zugradar/"


def fahrt_url(datum: dt.date, lok: str, zug: str) -> str:
    """Adresse der Seite zu genau dieser Fahrt — Ziel der Benachrichtigungen."""
    frage = urllib.parse.urlencode({"s": "fahrt", "tag": datum.isoformat(), "lok": lok, "zug": zug})
    return f"{UEBERSICHT_URL}?{frage}"


def aktion_knopf(cfg: dict, aktion: str, titel: str, datum: dt.date, lok: str, zug: str) -> dict:
    """Ein Knopf für die Benachrichtigung."""
    return {"id": aktion, "titel": titel,
            "url": f"{UEBERSICHT_URL}aktion.php?t={aktion_token(cfg, aktion, datum, lok, zug)}"}


def aktion_token(cfg: dict, aktion: str, datum: dt.date, lok: str, zug: str) -> str:
    """Signierter Auftrag für die Knöpfe in der Benachrichtigung (aktion.php prüft ihn).
    Gleiches Verfahren wie bei den Merk-Links: Nutzlast und HMAC, ohne Sitzung."""
    geheim = (cfg.get("merken") or {}).get("geheimnis", "")
    nutz = base64.urlsafe_b64encode(
        f"{aktion}|{datum.isoformat()}|{lok}|{zug}".encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(geheim.encode(), nutz.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{nutz}.{sig}"
_KURZTEXT_ENDE = ("Thread", "Übersicht", "Beitrag:", "Forum:", "Die Elmshorn-Zeit", "http",
                  "Interessiert", "Diese Fahrt", "Merken", "Die Angaben stammen", "Laut Fahrplan der Bahn")
_KURZTEXT_WEG = ("Diese Umläufe standen vorher",)


def kurztext(text: str, betreff: str, laenge: int = 240) -> str:
    """Die ersten Inhaltszeilen des Mailtextes als Push-Text: ohne Betreff,
    Unterstreichung, Hinweise und Links."""
    teile: list[str] = []
    for zeile in text.splitlines():
        zeile = " ".join(zeile.split()).replace("->", "→")
        if not zeile or zeile == betreff or set(zeile) <= set("=-─_·* "):
            continue
        if zeile.startswith(_KURZTEXT_ENDE):
            break
        if zeile.startswith(_KURZTEXT_WEG):
            continue
        teile.append(zeile)
        if sum(len(t) + 3 for t in teile) >= laenge:
            break
    kurz = " · ".join(teile)
    return kurz if len(kurz) <= laenge else kurz[:laenge - 1].rstrip() + "…"


def melde(cfg: dict, art: str, betreff: str, body_html: str, text: str, *,
          push_text: str | None = None, url: str | None = None,
          markierung: str | None = None, ersatz_mail: bool = True,
          optionen: dict | None = None, aktionen: list[dict] | None = None) -> int:
    """Eine Meldung über die für ihre Art eingestellten Wege. Liefert die Zahl der
    erfolgreichen Zustellungen (Mails + Geräte) — 0 heißt: nichts kam an.

    Geht eine reine Push-Meldung an kein einziges Gerät (noch keins angemeldet,
    Push-Dienst gestört), wird sie ersatzweise gemailt — verloren geht nichts.
    Push nutzt eine eigene DB-Verbindung, damit keine offene Transaktion des
    Aufrufers mittendrin festgeschrieben wird."""
    wege = set(cfg.get("kanaele", {}).get(art, KANAELE_STANDARD.get(art, ("mail", "push"))))
    erfolge = 0
    push_ok = 0
    versucht = 0
    if "push" in wege:
        try:
            import push                                   # push importiert dieses Modul
            verbindung = db_connect(cfg)
            try:
                push_ok, versucht = push.an_alle(
                    verbindung, cfg, betreff, push_text or kurztext(text, betreff),
                    url or UEBERSICHT_URL, markierung, optionen, aktionen, art)
            finally:
                verbindung.close()
            log.info("Push [%s] %s: %d von %d Geräten", art, betreff, push_ok, versucht)
        except Exception as exc:
            log.error("Push [%s] fehlgeschlagen: %s", art, exc)
        erfolge += push_ok
    kurz = push_text or kurztext(text, betreff)
    ersatz = ("mail" not in wege and not push_ok and ersatz_mail
              and cfg.get("push", {}).get("mail_wenn_push_scheitert", True))
    mails = 0
    if "mail" in wege or ersatz:
        if ersatz:
            log.info("Push [%s] kam nirgends an — geht ersatzweise per Mail", art)
        mails = sende_mail(cfg, betreff, body_html, text)
        erfolge += mails

    # Für den Verlauf in der App: was ging wann über welchen Weg raus.
    try:
        verbindung = db_connect(cfg)
        try:
            protokolliere(verbindung, art, betreff, kurz,
                          "+".join(w for w in ("mail", "push") if w in wege or (w == "mail" and ersatz)),
                          mails, push_ok if "push" in wege else None,
                          versucht if "push" in wege else None, url)
        finally:
            verbindung.close()
    except Exception as exc:
        log.error("Verlauf nicht gespeichert: %s", exc)
    return erfolge


def protokolliere(conn, art: str, betreff: str, text: str | None, kanaele: str,
                  mails: int = 0, push_ok: int | None = None, push_gesamt: int | None = None,
                  url: str | None = None, warteschlange_id: int | None = None) -> None:
    """Eine Zeile im Benachrichtigungsverlauf."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meldungen_log (art, betreff, text, kanaele, mails, push_ok, push_gesamt, "
            "       warteschlange_id, url, erstellt_am) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
            (art[:32], betreff[:255], (text or "")[:500] or None, kanaele[:32], mails,
             push_ok, push_gesamt, warteschlange_id, (url or None) and url[:300]))
    conn.commit()


# --------------------------------------------------------------------------
# Hauptlauf
# --------------------------------------------------------------------------
def zu_pruefen(cfg: dict, threads: dict, bekannt: dict) -> list[tuple[dt.date, str, dict]]:
    """Neue Threads immer, bekannte nur im Zeitfenster um heute — ältere Tage
    werden nicht mehr abgerufen, das spart Zugriffe auf das Forum."""
    heute = dt.date.today()
    frueh = heute - dt.timedelta(days=int(cfg["recheck_days_back"]))
    spaet = heute + dt.timedelta(days=int(cfg["recheck_days_forward"]))
    aufgaben = []
    for datum in sorted(threads):
        for quelle, info in sorted(threads[datum].items()):
            if info["thread_id"] not in bekannt or frueh <= datum <= spaet:
                aufgaben.append((datum, quelle, info))
    return aufgaben


def zeile_zu_lauf(row: dict) -> dict:
    """DB-Zeile in die Form bringen, die die Mailbausteine erwarten."""
    return {"lok": row["lok"], "zug": row["zug"],
            "von": row["von_halt"], "von_zeit": row["von_zeit"],
            "nach": row["nach_halt"], "nach_zeit": row["nach_zeit"],
            "richtung": row["richtung"], "elmshorn_zeit": row.get("elmshorn_zeit"),
            "zeit_quelle": row.get("zeit_quelle"), "gleis": row.get("gleis"),
            "quelle": row.get("quelle")}


def markiere_gemeldet(conn, keys: list[str], zeitpunkt, grund: str) -> None:
    if not keys:
        return
    platzhalter = ",".join(["%s"] * len(keys))
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE laeufe SET gemeldet_am=%s, melde_grund=%s "
            f"WHERE match_key IN ({platzhalter})", (zeitpunkt, grund, *keys))


def db_nachschlag(datum: dt.date, ungefaehr: str, nummer: str) -> dict | None:
    """Zug bei der DB-Timetables-API nachschlagen: exakte Zeit und Gleis.

    Der Soll-Fahrplan kommt stundenweise, ein Nachschlag kostet also drei Abrufe.
    Deshalb ein Budget je Durchlauf — und weil das Ergebnis in `laeufe.gleis`
    landet, wird jeder Umlauf ohnehin nur einmal nachgeschlagen.
    """
    cfg = db_nachschlag.cfg or {}
    dbcfg = cfg.get("db_api") or {}
    if not dbcfg.get("client_id") or db_nachschlag.budget <= 0:
        return None
    db_nachschlag.budget -= 1
    try:
        return db_timetables.soll_fuer_zug(dbcfg, dbcfg["eva_elmshorn"],
                                           datum, ungefaehr, nummer)
    except Exception as exc:
        log.debug("DB-Nachschlag für %s fehlgeschlagen: %s", nummer, exc)
        return None


db_nachschlag.cfg = None      # wird in lauf() gesetzt
db_nachschlag.budget = 0


def verarbeite_thread(conn, datum: dt.date, quelle: str, info: dict,
                      text: str, bekannt: dict, soll: dict[str, str]) -> list[dict]:
    """Einen Beitrag auswerten und speichern.

    Gibt (neue Läufe, gestrichene Läufe) zurück. Gestrichen heißt: stand beim
    letzten Mal noch im Beitrag, steht jetzt nicht mehr drin.
    """
    body_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ergebnis = analysiere_beitrag(text)
    treffer = [l for l in ergebnis["laeufe"] if l["elmshorn"]]
    geaendert = info["thread_id"] in bekannt and bekannt[info["thread_id"]] != body_hash
    now = dt.datetime.now()
    info["loks_218"] = ", ".join(ergebnis["loks_218"])
    info["unbekannte_halte"] = ergebnis["unbekannte_halte"]
    info["standorte"] = ergebnis.get("standorte", {})
    info["fahrten"] = ergebnis["laeufe"]

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tage (thread_id, tag, quelle, titel, url, body_hash, hat_218,
                                 hat_elmshorn, loks_218, zuerst_gesehen,
                                 zuletzt_geprueft, zuletzt_geaendert)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 titel=VALUES(titel), url=VALUES(url), body_hash=VALUES(body_hash),
                 hat_218=VALUES(hat_218), hat_elmshorn=VALUES(hat_elmshorn),
                 loks_218=VALUES(loks_218), zuletzt_geprueft=VALUES(zuletzt_geprueft),
                 zuletzt_geaendert=IF(%s, VALUES(zuletzt_geaendert), zuletzt_geaendert)""",
            (info["thread_id"], datum, quelle, info["titel"], info["url"], body_hash,
             1 if ergebnis["loks_218"] else 0, 1 if treffer else 0,
             info["loks_218"], now, now, now if geaendert else None,
             1 if geaendert else 0))

        # Alle Fahrten der 218er — nicht nur die durch Elmshorn. Sonst wüsste man
        # zwar, dass eine Lok im Einsatz ist, aber nicht wohin sie fährt.
        cur.execute("DELETE FROM umlaeufe WHERE tag=%s AND quelle=%s", (datum, quelle))
        for l in ergebnis["laeufe"]:
            cur.execute(
                "INSERT INTO umlaeufe (tag, quelle, lok, zug, von_halt, von_zeit, "
                " nach_halt, nach_zeit, richtung, elmshorn, elmshorn_zeit, gesehen_am) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (datum, quelle, l["lok"], l["zug"], l["von"], l["von_zeit"],
                 l["nach"], l["nach_zeit"], l["richtung"], 1 if l["elmshorn"] else 0,
                 l.get("elmshorn_zeit"), now))

        # Eigennamen gelten dauerhaft, nicht nur für diesen Tag.
        for lok, name in (ergebnis.get("namen") or {}).items():
            cur.execute(
                "INSERT INTO lok_namen (lok, name, zuerst_gesehen, zuletzt_gesehen) "
                "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "name=VALUES(name), zuletzt_gesehen=VALUES(zuletzt_gesehen)",
                (lok, name[:32], now, now))

        # Wo die Loks stehen. Erst die alten Einträge dieses Threads weg, damit
        # eine umgesetzte Lok nicht an zwei Orten gleichzeitig steht.
        im_umlauf = {l["lok"] for l in ergebnis["laeufe"]}
        cur.execute("DELETE FROM standorte WHERE tag=%s AND quelle=%s", (datum, quelle))
        for lok, ort in sorted(ergebnis.get("standorte", {}).items()):
            cur.execute(
                "INSERT INTO standorte (tag, quelle, lok, ort, hat_umlauf, gesehen_am) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (datum, quelle, lok, ort[:64],
                 (1 if lok in im_umlauf else 0) if lok.startswith("218 ") else None, now))

        aktuelle_keys, neue = [], []
        for l in treffer:
            # Exakte Zeit aus dem Fahrplan, wenn die Zugnummer dort bekannt ist —
            # sonst bleibt die Interpolation aus FAHRZEIT_PROFIL stehen.
            nummer = re.sub(r"\D", "", l["zug"])
            key = match_key(datum, quelle, l)
            aktuelle_keys.append(key)
            cur.execute("SELECT id, gleis FROM laeufe WHERE match_key = %s", (key,))
            vorhanden = cur.fetchone()
            ist_neu = vorhanden is None
            l["gleis"] = vorhanden["gleis"] if vorhanden else None

            exakt = soll.get(nummer)
            if exakt:
                l["elmshorn_zeit"], l["zeit_quelle"] = exakt, "fahrplan"
            # Das Gleis liefert nur die DB-Schnittstelle. Nachgeschlagen wird
            # einmal je Umlauf — danach steht es in der Datenbank.
            if l["gleis"] is None and l.get("elmshorn_zeit"):
                nach = db_nachschlag(datum, l["elmshorn_zeit"], nummer)
                if nach:
                    l["elmshorn_zeit"] = nach["zeit"]
                    l["zeit_quelle"] = "fahrplan"
                    l["gleis"] = nach.get("gleis")
            if not l.get("zeit_quelle"):
                l["zeit_quelle"] = "schaetzung" if l.get("elmshorn_zeit") else None
            cur.execute(
                """INSERT INTO laeufe (tag, quelle, thread_id, match_key, lok, zug,
                        von_halt, von_zeit, nach_halt, nach_zeit, richtung, elmshorn_zeit,
                        zeit_quelle, gleis, rohzeile, aktiv, zuerst_gesehen, zuletzt_gesehen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)
                   ON DUPLICATE KEY UPDATE aktiv=1, zuletzt_gesehen=VALUES(zuletzt_gesehen),
                        von_halt=VALUES(von_halt), von_zeit=VALUES(von_zeit),
                        nach_halt=VALUES(nach_halt), nach_zeit=VALUES(nach_zeit),
                        richtung=VALUES(richtung), rohzeile=VALUES(rohzeile),
                        elmshorn_zeit=VALUES(elmshorn_zeit), zeit_quelle=VALUES(zeit_quelle),
                        gleis=COALESCE(VALUES(gleis), gleis),
                        gestrichen_am=NULL, streichung_gemeldet_am=NULL""",
                (datum, quelle, info["thread_id"], key, l["lok"], l["zug"], l["von"],
                 l["von_zeit"], l["nach"], l["nach_zeit"], l["richtung"],
                 l["elmshorn_zeit"], l["zeit_quelle"], l["gleis"],
                 l["zeile"][:500], now, now))
            if ist_neu:
                neue.append({**l, "quelle": quelle, "key": key})

        # Was nach einer Bearbeitung nicht mehr im Beitrag steht, bleibt als
        # Historie erhalten, wird aber als gestrichen markiert — und gemeldet,
        # sonst führe man umsonst nach Elmshorn.
        if aktuelle_keys:
            platzhalter = ",".join(["%s"] * len(aktuelle_keys))
            bedingung = (f"thread_id=%s AND match_key NOT IN ({platzhalter})",
                         (info["thread_id"], *aktuelle_keys))
        else:
            bedingung = ("thread_id=%s", (info["thread_id"],))

        cur.execute(
            f"SELECT * FROM laeufe WHERE {bedingung[0]} AND aktiv=1 "
            f"AND streichung_gemeldet_am IS NULL "
            f"AND melde_grund IN ('gemeldet', 'tagesmeldung')", bedingung[1])
        gestrichen = [{**zeile_zu_lauf(r), "quelle": quelle, "key": r["match_key"]}
                      for r in cur.fetchall()]
        cur.execute(
            f"UPDATE laeufe SET aktiv=0, gestrichen_am=IFNULL(gestrichen_am, %s) "
            f"WHERE {bedingung[0]}", (now, *bedingung[1]))

    return neue, gestrichen


def lauf(cfg: dict, conn, dry_run: bool = False) -> None:
    jetzt = dt.datetime.now()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO laeufe_log (gestartet) VALUES (%s)", (jetzt,))
        log_id = cur.lastrowid
    conn.commit()

    db_nachschlag.cfg = cfg
    MERKEN_CFG.clear(); MERKEN_CFG.update(cfg)
    db_nachschlag.budget = int(cfg.get("nachschlag_je_lauf", 12))
    lade_namen(conn)
    threads_geprueft = neue_gesamt = mails_gesamt = 0
    unbekannte: set[str] = set()
    fehler: str | None = None
    erstlauf = meta_get(conn, "bootstrapped") is None
    tagesmeldung_an = bool(cfg.get("tagesmeldung", False))

    try:
        session = requests.Session()
        threads = thread_liste(session, cfg)
        log.info("%d Tage mit %d Threads ab %s in der Forenliste", len(threads),
                 sum(len(q) for q in threads.values()), cfg["start_date"])

        with conn.cursor() as cur:
            cur.execute("SELECT thread_id, body_hash FROM tage")
            bekannt = {r["thread_id"]: r["body_hash"] for r in cur.fetchall()}
            cur.execute("SELECT tag FROM tagesmeldung")
            schon_gemeldet = {r["tag"] for r in cur.fetchall()}

        heute = dt.date.today()
        neu_pro_tag: dict[dt.date, list[dict]] = {}
        weg_pro_tag: dict[dt.date, list[dict]] = {}
        infos_pro_tag: dict[dt.date, dict[str, dict]] = {}

        soll_cache: dict[dt.date, dict[str, str]] = {}

        def sollzeiten(tag: dt.date) -> dict[str, str]:
            """Fahrplanzeiten für Elmshorn, einmal je Tag. Fällt der Dienst aus,
            bleibt es bei der Schätzung — die Überwachung läuft weiter."""
            if tag in soll_cache:
                return soll_cache[tag]
            stop = (cfg.get("fahrplan") or {}).get("stop_id")
            treffer: dict[str, str] = {}
            if stop and (cfg.get("fahrplan") or {}).get("aktiv", True):
                try:
                    treffer = fahrplan.soll_zeiten(stop, tag)
                    log.info("Fahrplan %s: %d Zugnummern für Elmshorn", tag, len(treffer))
                except Exception as exc:
                    log.warning("Fahrplan für %s nicht abrufbar (%s) — nutze Schätzung",
                                tag, exc)
            soll_cache[tag] = treffer
            return treffer

        for datum, quelle, info in zu_pruefen(cfg, threads, bekannt):
            seite = http_get(session, info["url"])
            threads_geprueft += 1
            text = beitrag_text(seite)
            if not text.strip():
                log.warning("%s/%s: kein Beitragstext (%s)", datum, quelle, info["url"])
                continue

            neue, gestrichen = verarbeite_thread(conn, datum, quelle, info, text,
                                                 bekannt, sollzeiten(datum))
            unbekannte.update(info["unbekannte_halte"])
            infos_pro_tag.setdefault(datum, {})[quelle] = info
            if neue:
                neu_pro_tag.setdefault(datum, []).extend(neue)
                neue_gesamt += len(neue)
                log.info("%s/%s: %d neue 218-Läufe durch Elmshorn",
                         datum, quelle, len(neue))
            if gestrichen:
                weg_pro_tag.setdefault(datum, []).extend(gestrichen)
                log.info("%s/%s: %d gemeldete Läufe gestrichen",
                         datum, quelle, len(gestrichen))
            conn.commit()
            time.sleep(1.5)

        # ---- Tagesmeldung: einmal je Tag, sobald die [DB Regio]-Liste da ist
        for datum in sorted(infos_pro_tag):
            if not (tagesmeldung_an and datum not in schon_gemeldet
                    and datum >= heute and not erstlauf
                    and "regio" in infos_pro_tag[datum]):
                continue

            with conn.cursor() as cur:
                cur.execute("SELECT * FROM laeufe WHERE tag=%s AND aktiv=1 "
                            "ORDER BY lok, von_zeit", (datum,))
                aktive = [zeile_zu_lauf(r) for r in cur.fetchall()]

            betreff, body_html, text_mail = baue_tagesmeldung(
                datum, aktive, infos_pro_tag[datum])
            if dry_run:
                log.info("dry-run, Tagesmeldung nicht verschickt: %s", betreff)
                continue

            erfolge = melde(cfg, "tagesmeldung_treffer" if aktive else "tagesmeldung_leer",
                            betreff, body_html, text_mail)
            mails_gesamt += erfolge
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tagesmeldung (tag, gesendet_am, treffer, anzahl, erfolg) "
                    "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                    "gesendet_am=VALUES(gesendet_am), erfolg=VALUES(erfolg)",
                    (datum, dt.datetime.now(), 1 if aktive else 0, len(aktive),
                     1 if erfolge else 0))
            # Die Tagesmeldung enthält diese Läufe schon — sonst käme gleich
            # noch eine zweite Mail mit demselben Inhalt.
            markiere_gemeldet(conn, [n["key"] for n in neu_pro_tag.get(datum, [])],
                              dt.datetime.now(), "tagesmeldung")
            # Welche 218er die Tagesmeldung schon enthielt — Grundlage für Nachträge.
            merke_tages_loks(conn, datum, loks_mit_fahrten(infos_pro_tag[datum]),
                             in_tagesmeldung=True)
            neu_pro_tag.pop(datum, None)
            schon_gemeldet.add(datum)
            conn.commit()

        # ---- Sofortmeldung für Läufe, die nachträglich dazugekommen sind
        for datum, neue in sorted(neu_pro_tag.items()):
            for quelle in sorted({n["quelle"] for n in neue}):
                teil = [n for n in neue if n["quelle"] == quelle]
                url = infos_pro_tag[datum][quelle]["url"]
                now = dt.datetime.now()
                if dry_run:
                    # Nichts abhaken: sonst verschluckt ein Probelauf die echte Mail.
                    log.info("dry-run, Sofortmeldung nicht verschickt: %s/%s, %d Läufe",
                             datum, quelle, len(teil))
                    continue
                if erstlauf or datum < heute:
                    grund = "erstlauf" if erstlauf else "vergangen"
                else:
                    betreff, body_html, text_mail = baue_mail(datum, teil, url)
                    erfolge = melde(cfg, "treffer", betreff, body_html, text_mail)
                    mails_gesamt += erfolge
                    grund = "gemeldet" if erfolge else "versand_fehler"
                markiere_gemeldet(conn, [n["key"] for n in teil], now, grund)
            conn.commit()

        # ---- Nachtrag: nach der Tagesmeldung ist eine weitere 218 dazugekommen
        for datum in sorted(infos_pro_tag):
            if not (cfg.get("nachtrag", True) and datum in schon_gemeldet
                    and datum >= heute and not erstlauf):
                continue
            jetzt_loks = loks_mit_fahrten(infos_pro_tag[datum])
            with conn.cursor() as cur:
                cur.execute("SELECT lok FROM tages_loks WHERE tag=%s", (datum,))
                bekannt_loks = {r["lok"] for r in cur.fetchall()}

            if STAND not in bekannt_loks:
                # Tagesmeldung stammt aus der Zeit vor dieser Funktion: Stand still
                # erfassen. Sonst ginge jede Lok des Tages als Nachtrag raus.
                if not dry_run:
                    merke_tages_loks(conn, datum, jetzt_loks, in_tagesmeldung=False,
                                     grund="ausgangsstand")
                    conn.commit()
                log.info("%s: Ausgangsstand für Nachträge %s (%d Loks)", datum,
                         "würde erfasst (dry-run)" if dry_run else "erfasst", len(jetzt_loks))
                continue

            neue = {l: f for l, f in jetzt_loks.items() if l not in bekannt_loks}
            if not neue:
                continue
            # Fährt eine neue Lok durch Elmshorn, meldet das schon die Sofortmeldung.
            mit_elmshorn = {l for l, f in neue.items() if any(x.get("elmshorn") for x in f)}
            zu_melden = {l: f for l, f in neue.items() if l not in mit_elmshorn}

            if dry_run:
                log.info("dry-run, Nachtrag nicht verschickt: %s: %s",
                         datum, ", ".join(sorted(zu_melden)) or "—")
                continue
            if zu_melden:
                betreff, body_html, text_mail = baue_nachtrag(
                    datum, zu_melden, infos_pro_tag[datum])
                erfolge = melde(cfg, "nachtrag", betreff, body_html, text_mail)
                mails_gesamt += erfolge
                log.info("Nachtrag %s: %s — %d Mails", datum,
                         ", ".join(sorted(zu_melden)), erfolge)
                merke_tages_loks(conn, datum, zu_melden, in_tagesmeldung=False,
                                 grund="nachtrag" if erfolge else "versand_fehler",
                                 nachtrag=True)
            if mit_elmshorn:
                merke_tages_loks(conn, datum, mit_elmshorn, in_tagesmeldung=False,
                                 grund="sofortmeldung")
            conn.commit()

        # ---- Streichungen: was vorher gemeldet war und jetzt fehlt
        for datum, weg in sorted(weg_pro_tag.items()):
            for quelle in sorted({w["quelle"] for w in weg}):
                teil = [w for w in weg if w["quelle"] == quelle]
                now = dt.datetime.now()
                if dry_run:
                    log.info("dry-run, Streichung nicht verschickt: %s/%s, %d Läufe",
                             datum, quelle, len(teil))
                    continue
                if datum >= heute:
                    betreff, body_html, text_mail = baue_streichung(
                        datum, teil, infos_pro_tag[datum][quelle]["url"])
                    mails_gesamt += melde(cfg, "streichung", betreff, body_html, text_mail)
                with conn.cursor() as cur:
                    keys = [w["key"] for w in teil]
                    platzhalter = ",".join(["%s"] * len(keys))
                    cur.execute(
                        f"UPDATE laeufe SET streichung_gemeldet_am=%s "
                        f"WHERE match_key IN ({platzhalter})", (now, *keys))
            conn.commit()

        if erstlauf:
            meta_set(conn, "bootstrapped", dt.datetime.now().isoformat(timespec="seconds"))
            log.info("Erstlauf: Altbestand nur erfasst, keine Mails verschickt.")
        meta_set(conn, "letzter_erfolg", dt.datetime.now().isoformat(timespec="seconds"))
        herzschlag(conn, "waechter")
        conn.commit()

        # Einmal am Tag aufräumen — kein eigener Cron nötig.
        if meta_get(conn, "aufgeraeumt_am") != dt.date.today().isoformat():
            aufraeumen(conn, cfg)

    except Exception as exc:
        conn.rollback()
        fehler = f"{type(exc).__name__}: {exc}"
        log.exception("Lauf abgebrochen")

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE laeufe_log SET beendet=%s, erfolg=%s, threads_geprueft=%s,
                   neue_laeufe=%s, mails_versandt=%s, unbekannte_halte=%s, fehler=%s
               WHERE id=%s""",
            (dt.datetime.now(), 0 if fehler else 1, threads_geprueft, neue_gesamt,
             mails_gesamt, ", ".join(sorted(unbekannte)) or None, fehler, log_id))
    conn.commit()

    if unbekannte:
        log.warning("Unbekannte Halte (ggf. in STATIONEN ergänzen): %s",
                    ", ".join(sorted(unbekannte)))
    log.info("Fertig: %d Threads, %d neue Läufe, %d Mails%s",
             threads_geprueft, neue_gesamt, mails_gesamt,
             f", FEHLER: {fehler}" if fehler else "")
    if fehler:
        sys.exit(1)


# --------------------------------------------------------------------------
# Aufräumen
# --------------------------------------------------------------------------
# Was wie lange bleibt. Der Verlauf, den die Webseite zeigt, wird großzügig
# behalten; reine Protokolle fliegen früh raus, sonst wachsen sie unbegrenzt.
AUFBEWAHRUNG = {
    "laeufe_log_tage": 30,          # Protokoll je Durchlauf, 24 Zeilen am Tag
    "stoerungen_tage": 90,          # Dublettenschutz der Störungsmeldungen
    "sichtungen_tage": 30,          # geprüfte Forenthemen ohne Treffer
    "sichtungen_treffer_tage": 365, # Themen aus dem Umkreis
    "orte_ohne_treffer_tage": 90,   # nicht auflösbare Orte irgendwann neu versuchen
    "verlauf_tage": 365,            # Tage und Umläufe — das ist die eigentliche Historie
    "meldungen_log_tage": 365,      # Benachrichtigungsverlauf in der App
    "log_max_mb": 5,
}

LOGDATEIEN = [
    os.path.join(BASE_DIR, "logs", "run.log"),
    os.path.join(BASE_DIR, "logs", "stoerung.log"),
    os.path.join(BASE_DIR, "logs", "sichtungen.log"),
    "/tmp/marschbahn-cron.log",
    "/tmp/marschbahn-stoerung.log",
    "/tmp/marschbahn-sichtungen.log",
]


def rotiere_logs(grenze_mb: float) -> list[str]:
    """Zu große Logdateien einmal weglegen und neu anfangen.

    Kein logrotate-Eintrag nötig, und der Platzbedarf bleibt bei höchstens dem
    Doppelten der Grenze je Datei.
    """
    gedreht = []
    for pfad in LOGDATEIEN:
        try:
            if os.path.exists(pfad) and os.path.getsize(pfad) > grenze_mb * 1024 * 1024:
                os.replace(pfad, pfad + ".1")
                gedreht.append(os.path.basename(pfad))
        except OSError as exc:
            log.debug("Log %s nicht drehbar: %s", pfad, exc)
    return gedreht


def aufraeumen(conn, cfg: dict) -> dict[str, int]:
    """Alte Zeilen löschen. Gibt zurück, was jeweils weggefallen ist."""
    regeln = {**AUFBEWAHRUNG, **(cfg.get("aufbewahrung") or {})}
    weg: dict[str, int] = {}

    def loesche(name: str, sql: str, *werte) -> None:
        with conn.cursor() as cur:
            cur.execute(sql, werte)
            if cur.rowcount:
                weg[name] = cur.rowcount

    loesche("laeufe_log", "DELETE FROM laeufe_log WHERE gestartet < %s",
            dt.datetime.now() - dt.timedelta(days=int(regeln["laeufe_log_tage"])))
    loesche("meldungen_log", "DELETE FROM meldungen_log WHERE erstellt_am < %s",
            dt.datetime.now() - dt.timedelta(days=int(regeln.get("meldungen_log_tage", 365))))
    loesche("stoerungen", "DELETE FROM stoerungen WHERE tag < %s",
            dt.date.today() - dt.timedelta(days=int(regeln["stoerungen_tage"])))

    # Tabellen der anderen Skripte nur anfassen, wenn es sie schon gibt.
    with conn.cursor() as cur:
        cur.execute("SELECT table_name AS t FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()")
        vorhanden = {r["t"] for r in cur.fetchall()}

    if "sichtungen" in vorhanden:
        loesche("sichtungen", "DELETE FROM sichtungen WHERE treffer = 0 AND gesehen_am < %s",
                dt.datetime.now() - dt.timedelta(days=int(regeln["sichtungen_tage"])))
        loesche("sichtungen_treffer",
                "DELETE FROM sichtungen WHERE treffer = 1 AND gesehen_am < %s",
                dt.datetime.now() - dt.timedelta(days=int(regeln["sichtungen_treffer_tage"])))
    if "orte" in vorhanden:
        loesche("orte", "DELETE FROM orte WHERE gefunden = 0 AND geprueft < %s",
                dt.datetime.now() - dt.timedelta(days=int(regeln["orte_ohne_treffer_tage"])))

    # laeufe hängt per Fremdschlüssel an tage und verschwindet mit.
    grenze = dt.date.today() - dt.timedelta(days=int(regeln["verlauf_tage"]))
    loesche("umlaeufe", "DELETE FROM umlaeufe WHERE tag < %s", grenze)
    loesche("tages_loks", "DELETE FROM tages_loks WHERE tag < %s", grenze)
    loesche("standorte", "DELETE FROM standorte WHERE tag < %s", grenze)
    loesche("tagesmeldung", "DELETE FROM tagesmeldung WHERE tag < %s", grenze)
    loesche("tage", "DELETE FROM tage WHERE tag < %s", grenze)

    conn.commit()

    gedreht = rotiere_logs(float(regeln["log_max_mb"]))
    if gedreht:
        weg["logs_gedreht"] = len(gedreht)
    meta_set(conn, "aufgeraeumt_am", dt.date.today().isoformat())
    conn.commit()

    if weg:
        log.info("Aufgeräumt: %s", ", ".join(f"{k} {v}" for k, v in weg.items()))
    else:
        log.debug("Aufräumen: nichts zu löschen.")
    return weg


def main() -> None:
    p = argparse.ArgumentParser(description="Marschbahn-Wächter (BR 218 durch Elmshorn)")
    p.add_argument("--setup", action="store_true", help="Tabellen anlegen und beenden")
    p.add_argument("--aufraeumen", action="store_true",
                   help="alte Zeilen löschen und Logs drehen, dann beenden")
    p.add_argument("--dry-run", action="store_true", help="ohne Mailversand")
    p.add_argument("--verbose", action="store_true", help="Log auch auf stdout")
    args = p.parse_args()

    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    with open(CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)

    if not cfg["mail"].get("verify_tls", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    conn = db_connect(cfg)
    try:
        db_setup(conn)
        if args.setup:
            log.info("Schema angelegt.")
            print("Schema in %s angelegt." % cfg["db"]["database"])
            return
        if args.aufraeumen:
            weg = aufraeumen(conn, cfg)
            print("Aufgeräumt: " + (", ".join(f"{k}: {v}" for k, v in weg.items())
                                    or "nichts zu löschen"))
            return
        lauf(cfg, conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

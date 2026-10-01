#!/usr/bin/env python3
"""
Fahrende Züge für die Karte — und die Linien durch Elmshorn.

Quelle ist transitous (api/v1/map/trips): alle Fahrtabschnitte im Kartenausschnitt, von
Halt zu Halt, mit Abfahrt, Ankunft, Echtzeit-Kennzeichen und dem Streckenverlauf als
Polyline. Daraus entstehen zwei Dinge:

  1. Die Züge, die gerade oder in den nächsten Minuten fahren (meta.karte_zuege). Die
     Position rechnet der Browser selbst aus Zeit und Verlauf — so bewegen sich die Züge
     auf der Karte flüssig, ohne dass jede Sekunde nachgefragt wird.
  2. Der Verlauf der Linien durch Elmshorn (Tabelle linien_abschnitte). Jeder Abschnitt,
     den ein Zug dieser Linien fährt, wird gemerkt; nach ein, zwei Stunden ist jede Linie
     vollständig. Das ersetzt eine Abfrage bei OpenStreetMap (Overpass ist oft überlastet).

Nur Eisenbahn: Regional- und Fernverkehr. Ohne AKN (A1–A3), ohne S-Bahn, U-Bahn, Bus, Fähre.

    zuege.py                ein Durchlauf (Cron jede Minute)
    zuege.py --vorfuellen   Linien mit den Fahrten der nächsten drei Stunden füllen
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import time
import zlib
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "zuege.log")
log = logging.getLogger("zuege")

API = "https://api.transitous.org/api/v1/map/trips"
# Kennung mit Rückadresse: die öffentlich erreichbare Infoseite (die App selbst ist privat).
UA = "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"


VERBRAUCH: list[int] = [0]       # übertragene Bytes dieses Laufs (für die Buchführung)


def json_holen(url: str, zeit: int = 45):
    """Antwort als JSON — immer gzip anfragen. Ohne das schickt transitous die Rohdaten
    (11 MB je Abfrage statt 1,6 MB); das hat uns am 01.10.2026 eine Beschwerde eingebracht."""
    anfrage = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(anfrage, timeout=zeit) as antwort:
        roh = antwort.read()
    VERBRAUCH[0] += len(roh)
    return json.loads((gzip.decompress(roh) if roh[:2] == b"\x1f\x8b" else roh).decode("utf-8"))


def verbrauch_buchen(conn) -> None:
    """Wie viel wurde heute von transitous geholt? Steht in der App unter „Quellen“ —
    damit nicht wieder unbemerkt Gigabytes zusammenkommen."""
    if not VERBRAUCH[0]:
        return
    schluessel = "transitous_bytes_" + dt.date.today().isoformat()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO meta (schluessel, wert) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE wert = wert + VALUES(wert)", (schluessel, VERBRAUCH[0]))
        cur.execute("DELETE FROM meta WHERE schluessel LIKE 'transitous_bytes_%%' AND schluessel < %s",
                    ("transitous_bytes_" + (dt.date.today() - dt.timedelta(days=30)).isoformat(),))
    conn.commit()
    VERBRAUCH[0] = 0
# Schleswig-Holstein westlich bis Sylt, östlich bis Kiel/Neumünster, südlich bis Hamburg
AUSSCHNITT = {"min": "53.45,8.2", "max": "55.05,10.35"}
# Nur Schleswig-Holstein und Hamburg: grobes Vieleck (Breite, Länge). Im Süden folgt es der
# Elbe und der Hamburger Landesgrenze (Harburg und Neugraben gehören dazu, Stade, Buxtehude,
# Meckelfeld, Winsen nicht), im Norden der dänischen Grenze.
GEBIET = [
    (55.10, 8.20), (54.91, 8.60), (54.91, 9.00), (54.84, 9.30), (54.81, 9.35), (54.83, 9.45),
    (54.87, 9.60), (54.80, 10.10), (54.50, 11.50), (53.90, 11.00), (53.50, 10.75), (53.37, 10.60),
    (53.40, 10.30), (53.42, 10.10), (53.43, 10.00), (53.44, 9.90), (53.44, 9.85), (53.48, 9.76),
    (53.53, 9.74), (53.57, 9.66), (53.66, 9.45), (53.80, 9.30), (53.86, 9.10), (53.93, 8.75),
    (54.00, 8.20),
]
# Gebiete zum Dazuschalten (App: Karte → Filter): alle 16 Bundesländer und der dänische
# Grenzraum. Je Gebiet eine eigene Abfrage — ein einziger großer Ausschnitt wird von der
# Schnittstelle abgelehnt (HTTP 422). Die Umrisse kommen aus `karte_grenzen`
# (karte.py --grenzen); nur Dänemark bleibt ein grober Kasten.
def _kasten(sued: float, west: float, nord: float, ost: float) -> list[tuple[float, float]]:
    return [(sued, west), (nord, west), (nord, ost), (sued, ost)]


LAENDER = {
    "SH": "Schleswig-Holstein", "HH": "Hamburg", "NI": "Niedersachsen", "HB": "Bremen",
    "MV": "Mecklenburg-Vorpommern", "BE": "Berlin", "BB": "Brandenburg",
    "ST": "Sachsen-Anhalt", "SN": "Sachsen", "TH": "Thüringen", "HE": "Hessen",
    "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland",
    "BW": "Baden-Württemberg", "BY": "Bayern",
}
REGIONEN: dict[str, dict] = {k: {"name": name, "gebiet": None, "ausschnitt": None}
                             for k, name in LAENDER.items()}
REGIONEN["DK"] = {"name": "Dänemark (Grenzgebiet)", "gebiet": _kasten(54.75, 8.00, 56.00, 11.50),
                  "ausschnitt": {"min": "54.75,8.00", "max": "56.00,11.30"}}
STANDARD_REGIONEN = ["SH", "HH"]


def umrisse_laden(conn) -> None:
    """Die Umrisse der Bundesländer einmal je Lauf aus `karte_grenzen` holen: Vieleck für
    die Zuordnung, Umschlag (bbox) für die Abfrage. Fehlt die Tabelle (karte.py --grenzen
    noch nie gelaufen), bleibt es beim groben Kasten aus GEBIET."""
    with conn.cursor() as cur:
        cur.execute("SELECT land, punkte FROM karte_grenzen")
        je_land = {z["land"]: z["punkte"] for z in cur.fetchall()}
    for schluessel, name in LAENDER.items():
        roh = je_land.get(name)
        if not roh:
            continue
        ringe = json.loads(roh)
        # Das Festland ist der längste Ring; kleine Inseln bleiben für die Zuordnung dabei.
        ringe.sort(key=len, reverse=True)
        alle = [(p[0], p[1]) for ring in ringe for p in ring]
        REGIONEN[schluessel]["vielecke"] = [[(p[0], p[1]) for p in ring] for ring in ringe]
        REGIONEN[schluessel]["gebiet"] = REGIONEN[schluessel]["vielecke"][0]
        sued, nord = min(p[0] for p in alle), max(p[0] for p in alle)
        west, ost = min(p[1] for p in alle), max(p[1] for p in alle)
        REGIONEN[schluessel]["umschlag"] = (sued, west, nord, ost)
        REGIONEN[schluessel]["ausschnitt"] = {"min": f"{sued - 0.05:.3f},{west - 0.05:.3f}",
                                              "max": f"{nord + 0.05:.3f},{ost + 0.05:.3f}"}

ZUGARTEN = {"REGIONAL_RAIL", "REGIONAL_FAST_RAIL", "HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "RAIL"}
ELMSHORN_LINIEN = ("RE6", "RE7", "RE70", "RB60", "RB61", "RB71")
AKN_RE = re.compile(r"^A\d")
VORAUS_MINUTEN = 6              # so weit reichen die Züge, bis die nächste Minute neu holt
ANFRAGEN_JE_GEBIET = 3          # ein dichtes Land wird in Kacheln geholt — aber nicht endlos
ANFRAGEN_JE_LAUF = 6            # Rücksicht auf transitous: so viele Abrufe höchstens je Minute
RUHE_MINUTEN = 10               # schaut niemand auf die Karte, wird auch nichts geholt
TAKT_SEKUNDEN = 180             # und wenn doch, höchstens alle drei Minuten
VEREINFACHUNG_M = 25


def dekodiere(text: str, genauigkeit: int = 5) -> list[tuple[float, float]]:
    """Google-Polyline (transitous: Genauigkeit 5)."""
    punkte, i, lat, lon, faktor = [], 0, 0, 0, 10 ** genauigkeit
    while i < len(text):
        werte = []
        for _ in (0, 1):
            verschiebung, ergebnis = 0, 0
            while True:
                b = ord(text[i]) - 63
                i += 1
                ergebnis |= (b & 0x1F) << verschiebung
                verschiebung += 5
                if b < 0x20:
                    break
            werte.append(~(ergebnis >> 1) if ergebnis & 1 else ergebnis >> 1)
        lat += werte[0]
        lon += werte[1]
        punkte.append((lat / faktor, lon / faktor))
    return punkte


def vereinfache(punkte: list[tuple[float, float]], toleranz: float) -> list[tuple[float, float]]:
    """Douglas-Peucker in Metern — die Verläufe haben oft einige hundert Punkte."""
    if len(punkte) < 3:
        return punkte
    k = math.cos(math.radians(punkte[0][0]))
    xy = [(p[1] * 111_320 * k, p[0] * 111_320) for p in punkte]
    behalten = [False] * len(punkte)
    behalten[0] = behalten[-1] = True
    stapel = [(0, len(punkte) - 1)]
    while stapel:
        a, b = stapel.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        laenge = math.hypot(bx - ax, by - ay) or 1.0
        weiteste, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / laenge
            if d > weiteste:
                weiteste, idx = d, i
        if weiteste > toleranz and idx > 0:
            behalten[idx] = True
            stapel += [(a, idx), (idx, b)]
    return [p for p, k2 in zip(punkte, behalten) if k2]


def viertele(ausschnitt: dict) -> list[dict]:
    """Einen Ausschnitt in vier gleiche Teile zerlegen."""
    sued, west = (float(x) for x in ausschnitt["min"].split(","))
    nord, ost = (float(x) for x in ausschnitt["max"].split(","))
    mitte_b, mitte_l = (sued + nord) / 2, (west + ost) / 2
    return [{"min": f"{a:.4f},{b:.4f}", "max": f"{c:.4f},{d:.4f}"}
            for a, b, c, d in ((sued, west, mitte_b, mitte_l), (sued, mitte_l, mitte_b, ost),
                               (mitte_b, west, nord, mitte_l), (mitte_b, mitte_l, nord, ost))]


def hole_gebiet(start: dt.datetime, ende: dt.datetime, ausschnitt: dict,
                budget: int = ANFRAGEN_JE_GEBIET) -> tuple[list[dict], int, int]:
    """Ein Gebiet holen. Sind es zu viele Züge (HTTP 422), wird der Ausschnitt geviertelt
    und stückweise geholt. Ergebnis: (Abschnitte, Abrufe, nicht geschaffte Stücke)."""
    stapel = [(ausschnitt, 0)]
    roh, abrufe, offen = [], 0, 0
    while stapel and abrufe < budget:
        stueck, tiefe = stapel.pop()
        abrufe += 1
        try:
            roh += hole(start, ende, stueck)
        except urllib.error.HTTPError as exc:
            if exc.code != 422 or tiefe >= 3:
                offen += 1
                continue
            stapel += [(k, tiefe + 1) for k in viertele(stueck)]
    return roh, abrufe, offen + len(stapel)


def hole(start: dt.datetime, ende: dt.datetime, ausschnitt: dict | None = None) -> list[dict]:
    params = {"zoom": "10", **(ausschnitt or AUSSCHNITT),
              "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "endTime": ende.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return json_holen(API + "?" + urllib.parse.urlencode(params))


def zeit(text: str) -> int:
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def linie_und_nummer(name: str) -> tuple[str, str]:
    """"RE6 (11029)" -> ("RE6", "11029"); "ICE 385" -> ("ICE", "385")."""
    if m := re.match(r"^\s*([A-Za-z]+\s?\d*)\s*\((\d+)\)", name):
        return m.group(1).replace(" ", ""), m.group(2)
    if m := re.match(r"^\s*([A-Za-z]+)\s*(\d+)", name):
        return m.group(1), m.group(2)
    return name.strip(), ""


def im_vieleck(lat: float, lon: float, ecken: list[tuple[float, float]]) -> bool:
    """Punkt im Vieleck (Strahlverfahren)."""
    drin = False
    for (y1, x1), (y2, x2) in zip(ecken, ecken[1:] + ecken[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            drin = not drin
    return drin


def im_gebiet(lat: float, lon: float) -> bool:
    """Schleswig-Holstein oder Hamburg."""
    return im_vieleck(lat, lon, GEBIET)


def _flaeche(r: dict | None) -> float:
    u = (r or {}).get("umschlag")
    return (u[2] - u[0]) * (u[3] - u[1]) if u else 9e9


def region_von(lat: float, lon: float, erlaubt: list[str]) -> str | None:
    """In welchem eingeschalteten Gebiet liegt der Punkt? Erst der grobe Umschlag (billig),
    dann die Umrisse selbst — Inseln zählen mit. Kleine Länder zuerst: Bremen und Hamburg
    liegen mitten in Niedersachsen bzw. Schleswig-Holstein, Berlin in Brandenburg."""
    erlaubt = sorted(erlaubt, key=lambda k: _flaeche(REGIONEN.get(k)))
    for schluessel in erlaubt:
        r = REGIONEN.get(schluessel)
        if not r:
            continue
        umschlag = r.get("umschlag")
        if umschlag and not (umschlag[0] <= lat <= umschlag[2] and umschlag[1] <= lon <= umschlag[3]):
            continue
        for ecken in r.get("vielecke") or [r["gebiet"] or GEBIET]:
            if im_vieleck(lat, lon, ecken):
                return schluessel
    return None


def zug_abschnitte(roh: list[dict], regionen: list[str] | None = None,
                   halte: list[tuple] | None = None) -> list[dict]:
    """Nur Eisenbahn, ohne AKN — je Abschnitt Zeiten, Name und vereinfachter Verlauf.
    Behalten wird, was in einem der eingeschalteten Gebiete beginnt und endet."""
    regionen = regionen or STANDARD_REGIONEN
    aus = []
    for s in roh:
        if s.get("mode") not in ZUGARTEN or not s.get("trips"):
            continue
        name = (s["trips"][0].get("routeShortName") or s["trips"][0].get("displayName") or "").strip()
        if not name or AKN_RE.match(name):
            continue
        # Beide Enden in einem eingeschalteten Gebiet — sonst nicht
        r_von = region_von(s["from"]["lat"], s["from"]["lon"], regionen)
        r_nach = region_von(s["to"]["lat"], s["to"]["lon"], regionen)
        if not r_von or not r_nach:
            continue
        linie, nummer = linie_und_nummer(name)
        if halte is not None:
            rang = rang_der_linie(linie, name)
            for ende, region in ((s["from"], r_von), (s["to"], r_nach)):
                halte.append((ende["name"], ende["lat"], ende["lon"], region, rang))
        punkte = vereinfache(dekodiere(s.get("polyline") or ""), VEREINFACHUNG_M)
        if len(punkte) < 2:
            continue
        aus.append({
            # Kennung der Fahrt: Abschnitte derselben Fahrt gehören zu einem Zug auf der Karte
            "t": format(zlib.crc32((s["trips"][0].get("tripId") or name).encode()), "x"),
            "r": r_von,                     # Gebiet (SH, NI, MV, BB, DK) — Filter in der App
            "trip": s["trips"][0].get("tripId") or "",
            "linie": linie, "nr": nummer, "name": name,
            "von": s["from"]["name"], "nach": s["to"]["name"],
            "ab": zeit(s["departure"]), "an": zeit(s["arrival"]),
            "ab_plan": zeit(s.get("scheduledDeparture") or s["departure"]),
            "echt": bool(s.get("realTime")),
            "p": [[round(a, 5), round(b, 5)] for a, b in punkte],
        })
    return aus


SCHEMA_ZUEGE = """
CREATE TABLE IF NOT EXISTS karte_zuege (
    id     TINYINT     NOT NULL PRIMARY KEY,
    stand  INT         NOT NULL,             -- Unix-Zeit des Abrufs
    daten  MEDIUMTEXT  NOT NULL              -- für meta.wert (TEXT, 64 kB) zu groß
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS linien_abschnitte (
    linie      VARCHAR(12)  NOT NULL,
    von        VARCHAR(100) NOT NULL,
    nach       VARCHAR(100) NOT NULL,
    punkte     MEDIUMTEXT   NOT NULL,
    gesehen_am DATETIME     NOT NULL,
    PRIMARY KEY (linie, von, nach)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""
# Das Streckennetz: aus allen gemerkten Abschnitten je Gebiet einmal zusammengesetzt, damit
# die Karte nicht tausende Zeilen einzeln laden muss.
SCHEMA_NETZ = """
CREATE TABLE IF NOT EXISTS karte_netz (
    region VARCHAR(2)  NOT NULL PRIMARY KEY,
    stand  DATETIME    NOT NULL,
    daten  MEDIUMTEXT  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""
NETZ_ALTER_MINUTEN = 15         # so oft wird das Netz neu zusammengesetzt

# Bahnhöfe: fallen bei den Fahrten ohnehin an (jeder Abschnitt nennt Start und Ziel mit
# Koordinaten). Der Rang sagt, wie wichtig ein Halt ist — danach zeigt die Karte beim
# Hineinzoomen immer mehr.
SCHEMA_HALTE = """
CREATE TABLE IF NOT EXISTS karte_halte (
    name       VARCHAR(100) NOT NULL PRIMARY KEY,
    lat        DOUBLE       NOT NULL,
    lon        DOUBLE       NOT NULL,
    region     VARCHAR(2)   NOT NULL,
    rang       TINYINT      NOT NULL DEFAULT 0,   -- 3 ICE, 2 IC/EC, 1 RE, 0 RB
    fahrten    INT          NOT NULL DEFAULT 0,
    gesehen_am DATETIME     NOT NULL,
    KEY idx_rang (rang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""


def rang_der_linie(linie: str, name: str) -> int:
    if linie.startswith("ICE"):
        return 3
    if linie.startswith(("IC", "EC", "ECE", "ICL", "NJ", "EN", "RJ")) or name.startswith("FlixTrain"):
        return 2
    return 1 if linie.startswith("RE") else 0


SCHEMA_ENDEN = """
CREATE TABLE IF NOT EXISTS fahrt_enden (
    trip       VARCHAR(120) NOT NULL PRIMARY KEY,
    start      VARCHAR(100) NULL,
    start_zeit CHAR(5)      NULL,
    ziel       VARCHAR(100) NULL,
    ziel_zeit  CHAR(5)      NULL,
    geholt_am  DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""
TRIP_API = "https://api.transitous.org/api/v5/trip"
ENDEN_JE_LAUF = 40              # neue Fahrten je Minute nachschlagen, der Rest im nächsten Lauf
ENDEN_SEKUNDEN = 30
TZ = ZoneInfo("Europe/Berlin")


def haltname(name: str) -> str:
    """"Westerland(Sylt) ZOB/Bahnhof" -> "Westerland(Sylt)"."""
    return re.sub(r"\s+(ZOB/Bahnhof|Bahnhof|Bf\.?)$", "", (name or "").strip())


def uhrzeit(text: str | None) -> str | None:
    if not text:
        return None
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")


def hole_enden(trip: str) -> tuple:
    daten = json_holen(TRIP_API + "?" + urllib.parse.urlencode({"tripId": trip}), 20)
    leg = next(l for l in daten["legs"] if l.get("mode") != "WALK")
    return (haltname(leg["from"]["name"]), uhrzeit(leg.get("scheduledStartTime") or leg.get("startTime")),
            haltname(leg["to"]["name"]), uhrzeit(leg.get("scheduledEndTime") or leg.get("endTime")))


def enden_ergaenzen(conn, abschnitte: list[dict]) -> None:
    """Start- und Zielbahnhof der ganzen Fahrt an jeden Abschnitt hängen. Eine Fahrt wird
    nur einmal nachgeschlagen (Tabelle fahrt_enden), neue höchstens ENDEN_JE_LAUF je Lauf."""
    # Übernommene Abschnitte aus dem letzten Lauf tragen keine Fahrt-Kennung mehr (sie wird
    # beim Ablegen entfernt) — sie haben Start und Ziel schon.
    trips = sorted({a["trip"] for a in abschnitte if a.get("trip")})
    bekannt: dict[str, dict] = {}
    if trips:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM fahrt_enden WHERE trip IN (" + ",".join(["%s"] * len(trips)) + ")", trips)
            bekannt = {r["trip"]: r for r in cur.fetchall()}
    frist = time.monotonic() + ENDEN_SEKUNDEN
    neu = 0
    for trip in trips:
        if trip in bekannt or neu >= ENDEN_JE_LAUF or time.monotonic() > frist:
            continue
        neu += 1
        try:
            start, start_zeit, ziel, ziel_zeit = hole_enden(trip)
        except Exception as exc:
            log.info("Fahrt %s: %s", trip, exc)
            continue
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fahrt_enden (trip, start, start_zeit, ziel, ziel_zeit, geholt_am) "
                        "VALUES (%s,%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE geholt_am=NOW()",
                        (trip[:120], start[:100], start_zeit, ziel[:100], ziel_zeit))
        bekannt[trip] = {"start": start, "start_zeit": start_zeit, "ziel": ziel, "ziel_zeit": ziel_zeit}
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fahrt_enden WHERE geholt_am < NOW() - INTERVAL 2 DAY")
    conn.commit()
    for a in abschnitte:
        e = bekannt.get(a.pop("trip", ""))
        if e:
            a["start"], a["start_zeit"], a["ziel"], a["ziel_zeit"] = e["start"], e["start_zeit"], e["ziel"], e["ziel_zeit"]


def spalte_region(conn) -> None:
    """Gebiet je Abschnitt (seit 25.09.2026) — vorher gab es nur Schleswig-Holstein."""
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM linien_abschnitte LIKE 'region'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE linien_abschnitte ADD COLUMN region VARCHAR(2) NOT NULL DEFAULT 'SH', "
                        "ADD KEY idx_region (region)")
        # Seit alle Linien gemerkt werden, sind längere Namen dabei ("FlixTrain FLX10")
        cur.execute("ALTER TABLE linien_abschnitte MODIFY linie VARCHAR(32) NOT NULL")
    conn.commit()


def linien_merken(conn, abschnitte: list[dict]) -> int:
    """Jeden gefahrenen Abschnitt merken — daraus entstehen die Linien durch Elmshorn und
    das Streckennetz der Karte. Nach 21 Tagen ohne Fahrt fällt ein Abschnitt heraus."""
    n = 0
    with conn.cursor() as cur:
        for a in abschnitte:
            cur.execute("INSERT INTO linien_abschnitte (linie, von, nach, punkte, region, gesehen_am) "
                        "VALUES (%s,%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE punkte=VALUES(punkte), "
                        "region=VALUES(region), gesehen_am=NOW()",
                        (a["linie"][:32], a["von"][:100], a["nach"][:100], json.dumps(a["p"]),
                         a.get("r", "SH")))
            n += 1
        # Was lange nicht mehr gefahren ist (Baustelle, Fahrplanwechsel), fällt raus.
        cur.execute("DELETE FROM linien_abschnitte WHERE gesehen_am < NOW() - INTERVAL 21 DAY")
    conn.commit()
    return n


def halte_merken(conn, roh_halte: list[tuple]) -> int:
    """Jeden gesehenen Halt festhalten: Lage, Gebiet, höchster Rang, Zahl der Fahrten."""
    with conn.cursor() as cur:
        for name, lat, lon, region, rang in roh_halte:
            cur.execute(
                "INSERT INTO karte_halte (name, lat, lon, region, rang, fahrten, gesehen_am) "
                "VALUES (%s,%s,%s,%s,%s,1,NOW()) ON DUPLICATE KEY UPDATE lat=VALUES(lat), lon=VALUES(lon), "
                "region=VALUES(region), rang=GREATEST(rang, VALUES(rang)), fahrten=fahrten+1, gesehen_am=NOW()",
                (name[:100], round(lat, 5), round(lon, 5), region, rang))
        # Was drei Wochen lang kein Zug mehr angefahren hat, verschwindet wieder.
        cur.execute("DELETE FROM karte_halte WHERE gesehen_am < NOW() - INTERVAL 21 DAY")
    conn.commit()
    return len(roh_halte)


def netz_bauen(conn, regionen: list[str]) -> dict[str, int]:
    """Je Gebiet alle Abschnitte zu einem Streckennetz zusammenfassen: gleiche Halte-Paare
    (Hin- und Rückrichtung, mehrere Linien auf demselben Gleis) nur einmal."""
    gebaut = {}
    for region in regionen:
        with conn.cursor() as cur:
            cur.execute("SELECT stand FROM karte_netz WHERE region=%s AND stand > NOW() - INTERVAL %s MINUTE",
                        (region, NETZ_ALTER_MINUTEN))
            if cur.fetchone():
                continue                                     # noch frisch genug
            cur.execute("SELECT von, nach, punkte FROM linien_abschnitte WHERE region=%s", (region,))
            zeilen = cur.fetchall()
        gesehen, netz = set(), []
        for z in zeilen:
            paar = tuple(sorted((z["von"], z["nach"])))
            if paar in gesehen:
                continue
            gesehen.add(paar)
            netz.append(json.loads(z["punkte"]))
        with conn.cursor() as cur:
            cur.execute("INSERT INTO karte_netz (region, stand, daten) VALUES (%s,NOW(),%s) "
                        "ON DUPLICATE KEY UPDATE stand=NOW(), daten=VALUES(daten)",
                        (region, json.dumps(netz, separators=(",", ":"))))
        conn.commit()
        gebaut[region] = len(netz)
    if gebaut:
        log.info("Streckennetz: %s", ", ".join(f"{k} {v} Abschnitte" for k, v in gebaut.items()))
    return gebaut


def main() -> int:
    p = argparse.ArgumentParser(description="Fahrende Züge und Linien für die Karte")
    p.add_argument("--vorfuellen", action="store_true")
    p.add_argument("--regionen", help="Gebiete setzen, z. B. SH,NI,MV (leer: nur anzeigen)",
                   nargs="?", const="")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(SCHEMA_ZUEGE)
            cur.execute(SCHEMA_ENDEN)
            cur.execute(SCHEMA_NETZ)
            cur.execute(SCHEMA_HALTE)
        conn.commit()
        spalte_region(conn)
        jetzt = dt.datetime.now(dt.timezone.utc)

        if args.regionen is not None:
            umrisse_laden(conn)
            if args.regionen:
                gueltig = [r.strip() for r in args.regionen.split(",") if r.strip() in REGIONEN]
                mb.meta_set(conn, "einst_karte_regionen", ",".join(gueltig or STANDARD_REGIONEN))
                conn.commit()
            print("Gebiete:", mb.einstellung(conn, cfg, "karte", "regionen", ",".join(STANDARD_REGIONEN)))
            for k, v in REGIONEN.items():
                print(f"  {k:3} {v['name']}")
            return 0

        if args.vorfuellen:
            # Die Schnittstelle begrenzt die Treffer je Anfrage (HTTP 422 schon bei zehn
            # Minuten zur Hauptverkehrszeit) — in Stücken von fünf Minuten holen.
            gesamt = 0
            for stueck in range(36):
                start = jetzt + dt.timedelta(minutes=5 * stueck)
                try:
                    gesamt += linien_merken(conn, zug_abschnitte(hole(start, start + dt.timedelta(minutes=5))))
                except Exception as exc:
                    log.warning("Vorfüllen %s: %s", start.strftime("%H:%M"), exc)
                time.sleep(1)
            print("Linienabschnitte gemerkt:", gesamt)
            return 0

        # Rücksicht auf transitous (Beschwerde vom 01.10.2026): Es wird nur geholt, wenn
        # jemand auf die Karte schaut (zuege.php vermerkt jeden Abruf), und auch dann
        # höchstens alle TAKT_SEKUNDEN. Eine Abfrage sind rund 1 MB — fast nur Busse, die
        # Schnittstelle kann nicht nach Zuggattung filtern.
        gesehen_am = mb.meta_get(conn, "karte_angefragt")
        ruhe = not gesehen_am or (dt.datetime.now() - dt.datetime.fromisoformat(gesehen_am)
                                  ).total_seconds() > RUHE_MINUTEN * 60
        takt = int(mb.einstellung(conn, cfg, "karte", "takt_sekunden", TAKT_SEKUNDEN))
        with conn.cursor() as cur:
            cur.execute("SELECT stand FROM karte_zuege WHERE id = 1")
            vorher = cur.fetchone()
        zu_frisch = bool(vorher) and jetzt.timestamp() - int(vorher["stand"]) < takt
        if ruhe or zu_frisch:
            mb.herzschlag(conn, "zuege")
            log.debug("Keine Abfrage (%s).", "niemand schaut hin" if ruhe else "eben erst geholt")
            return 0

        # Welche Gebiete sollen auf die Karte? (App: Karte → Filter, Tabelle meta)
        gewaehlt = [r for r in str(mb.einstellung(conn, cfg, "karte", "regionen",
                                                  ",".join(STANDARD_REGIONEN))).split(",")
                    if r.strip() in REGIONEN]
        gewaehlt = [r.strip() for r in gewaehlt] or list(STANDARD_REGIONEN)
        umrisse_laden(conn)

        # Je Gebiet eine Abfrage — ein Ausschnitt über alles lehnt die Schnittstelle ab.
        # Zur Hauptverkehrszeit ist auch ein einzelnes Land zu groß (HTTP 422): dann erst
        # mit kürzerem Vorlauf, dann in Kacheln (hole_gebiet). Scheitert ein Land trotzdem,
        # laufen die übrigen weiter — sonst steht die ganze Karte wegen Bayern still
        # (26.09.2026).
        abschnitte, gesehen, halte, status = [], set(), [], {}
        abrufe_gesamt = 0
        # Reichen die Abrufe nicht für alle Gebiete, kommt beim nächsten Lauf das nächste
        # dran (Ringtausch) — sonst bliebe das letzte Land dauerhaft ohne Züge.
        # Schleswig-Holstein und Hamburg immer zuerst — dafür ist die App da; die übrigen
        # Länder reihum.
        fest = [r for r in STANDARD_REGIONEN if r in gewaehlt]
        weitere = [r for r in gewaehlt if r not in fest]
        versatz = int(mb.meta_get(conn, "karte_gebiete_versatz") or 0) % max(1, len(weitere))
        reihenfolge = fest + weitere[versatz:] + weitere[:versatz]
        for region in reihenfolge:
            if abrufe_gesamt >= ANFRAGEN_JE_LAUF:
                status[region] = "ausgelassen"
                continue
            roh, offen = [], 0
            for voraus in (VORAUS_MINUTEN, 4, 2):
                try:
                    roh, abrufe, offen = hole_gebiet(
                        jetzt - dt.timedelta(minutes=1), jetzt + dt.timedelta(minutes=voraus),
                        REGIONEN[region]["ausschnitt"],
                        min(ANFRAGEN_JE_GEBIET, ANFRAGEN_JE_LAUF - abrufe_gesamt))
                    abrufe_gesamt += abrufe
                    if not offen:
                        break
                except Exception as exc:                 # Netz, Zeitüberschreitung …
                    log.warning("Gebiet %s: %s", region, exc)
                    status[region] = "fehler"
                    roh, offen = [], 1
                    break
            if offen and region not in status:
                status[region] = "teilweise"
                log.info("Gebiet %s: %d Stück(e) zu voll — Karte dort unvollständig", region, offen)
            for a in zug_abschnitte(roh, gewaehlt, halte):
                # Die Ausschnitte überlappen sich — jeden Abschnitt nur einmal übernehmen.
                schluessel = (a["t"], a["von"], a["nach"], a["ab"])
                if schluessel not in gesehen:
                    gesehen.add(schluessel)
                    abschnitte.append(a)
        mb.meta_set(conn, "karte_gebiete_status", json.dumps(status, ensure_ascii=False))
        mb.meta_set(conn, "karte_gebiete_versatz", str((versatz + 1) % max(1, len(weitere))))
        conn.commit()
        if status:
            log.info("Gebiete mit Problemen: %s", status)
        # Gebiete, die diesmal nicht drankamen, behalten ihre Züge aus dem letzten Lauf —
        # sonst blinken sie im Ringtausch weg. Abgelaufenes fällt dabei heraus.
        uebersprungen = {r for r, wie in status.items() if wie == "ausgelassen"}
        if uebersprungen:
            with conn.cursor() as cur:
                cur.execute("SELECT daten FROM karte_zuege WHERE id = 1")
                alt = cur.fetchone()
            for a in (json.loads(alt["daten"]) if alt else []):
                if a.get("r") in uebersprungen and a.get("an", 0) > jetzt.timestamp():
                    schluessel = (a["t"], a["von"], a["nach"], a["ab"])
                    if schluessel not in gesehen:
                        gesehen.add(schluessel)
                        abschnitte.append(a)

        enden_ergaenzen(conn, abschnitte)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO karte_zuege (id, stand, daten) VALUES (1, %s, %s) "
                        "ON DUPLICATE KEY UPDATE stand=VALUES(stand), daten=VALUES(daten)",
                        (int(jetzt.timestamp()), json.dumps(abschnitte, separators=(",", ":"))))
        conn.commit()
        linien_merken(conn, abschnitte)
        halte_merken(conn, halte)
        netz_bauen(conn, gewaehlt)
        mb.herzschlag(conn, "zuege")
        verbrauch_buchen(conn)
        log.debug("%d Zugabschnitte aus %s (%d kB geholt)", len(abschnitte), "+".join(gewaehlt),
                  VERBRAUCH[0] // 1024)
    except Exception as exc:
        log.warning("Züge nicht geholt: %s", exc)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

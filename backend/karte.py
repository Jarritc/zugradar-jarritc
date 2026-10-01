#!/usr/bin/env python3
"""
Koordinaten für die Karte in der App.

Die Karte zeigt die Marschbahn, wo die 218er laut Plan gerade sind, wo sie abgestellt
stehen und die Beiträge aus dem Umkreis. Dafür braucht sie zu jedem Ortsnamen eine
Position. Die Webseite fragt dafür keinen Dienst an: dieses Skript geokodiert (über
sichtungen.ort_pruefen, also transitous mit dauerhaftem Gedächtnis in `orte`) und legt
das Ergebnis in `koordinaten` ab. Stündlich, neue Namen kommen selten dazu.

Die Namen im Forum weichen von denen der Geokodierung ab ("Westerland(Sylt)",
"Niebüll (BW/Süd/Terminal)") — ALIAS bildet sie ab.

    karte.py            Koordinaten ergänzen
    karte.py --liste    zeigen, was da ist
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb
import sichtungen as si

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "karte.log")
log = logging.getLogger("karte")

# Die Marschbahn in Fahrtrichtung Sylt, so wie die Linie auf der Karte gezeichnet wird.
# (Name in der App, Suchbegriff für die Geokodierung.) "Langenhorn" fehlt mit Absicht:
# die Geokodierung landet in Hamburg-Langenhorn statt in Nordfriesland.
STRECKE = [
    ("Hamburg-Altona", "Hamburg-Altona"), ("Pinneberg", "Pinneberg"), ("Tornesch", "Tornesch"),
    ("Elmshorn", "Elmshorn"), ("Glückstadt", "Glückstadt"), ("Krempe", "Krempe"),
    ("Itzehoe", "Itzehoe"), ("Wilster", "Wilster"), ("Burg(Dithm)", "Burg (Dithmarschen)"),
    ("St Michaelisdonn", "St. Michaelisdonn"), ("Meldorf", "Meldorf"),
    ("Heide(Holst)", "Heide (Holstein)"), ("Lunden", "Lunden"), ("Friedrichstadt", "Friedrichstadt"),
    ("Husum", "Husum"), ("Bredstedt", "Bredstedt"), ("Niebüll", "Niebüll"),
    ("Klanxbüll", "Klanxbüll"), ("Morsum", "Morsum"), ("Keitum", "Keitum"),
    ("Westerland(Sylt)", "Westerland"),
]

# Schreibweisen aus dem Forum -> Suchbegriff
ALIAS = {name: suche for name, suche in STRECKE}
ALIAS.update({
    "Westerland": "Westerland", "Niebüll (BW/Süd/Terminal)": "Niebüll",
    "Heide": "Heide (Holstein)", "Burg (Dithm)": "Burg (Dithmarschen)",
})
MAX_KM = 900       # alles darüber ist sicher ein Fehlgriff der Geokodierung

# Gleisführung: die RE6-Route in OpenStreetMap (Hamburg-Altona => Westerland), einmal im
# Monat über Overpass geholt, zu einer Linie zusammengesetzt und vereinfacht.
OVERPASS = ["https://overpass-api.de/api/interpreter",          # Hauptserver
            "https://overpass.kumi.systems/api/interpreter"]      # Ausweichserver
RE6_RELATION = 1175046
GLEISE_ALTER_TAGE = 30
VEREINFACHUNG_M = 12          # Abweichung, die beim Vereinfachen in Kauf genommen wird

SCHEMA = """
CREATE TABLE IF NOT EXISTS koordinaten (
    name        VARCHAR(100) NOT NULL PRIMARY KEY,
    lat         DOUBLE       NOT NULL,
    lon         DOUBLE       NOT NULL,
    strecke_nr  INT          NULL,              -- Reihenfolge auf der Marschbahn, sonst NULL
    geprueft    DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""


def _abstand_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def _vereinfache(punkte: list[tuple[float, float]], toleranz: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterativ (die Linie hat einige tausend Punkte)."""
    import math
    if len(punkte) < 3:
        return punkte
    behalten = [False] * len(punkte)
    behalten[0] = behalten[-1] = True
    stapel = [(0, len(punkte) - 1)]
    while stapel:
        a, b = stapel.pop()
        pa, pb = punkte[a], punkte[b]
        weiteste, idx = 0.0, -1
        for i in range(a + 1, b):
            # Abstand des Punkts von der Geraden pa-pb (lokal eben gerechnet)
            p = punkte[i]
            ax, ay = 0.0, 0.0
            bx = (pb[1] - pa[1]) * 111_320 * math.cos(math.radians(pa[0]))
            by = (pb[0] - pa[0]) * 111_320
            px = (p[1] - pa[1]) * 111_320 * math.cos(math.radians(pa[0]))
            py = (p[0] - pa[0]) * 111_320
            laenge = math.hypot(bx - ax, by - ay) or 1.0
            d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / laenge
            if d > weiteste:
                weiteste, idx = d, i
        if weiteste > toleranz and idx > 0:
            behalten[idx] = True
            stapel += [(a, idx), (idx, b)]
    return [p for p, k in zip(punkte, behalten) if k]


def overpass_holen() -> dict:
    """Die Route bei Overpass abfragen; ist der Hauptserver überlastet (504 kommt vor),
    den Ausweichserver versuchen."""
    import urllib.parse, urllib.request
    abfrage = f"[out:json][timeout:90];relation({RE6_RELATION});out geom;"
    letzter = None
    for server in OVERPASS:
        try:
            anfrage = urllib.request.Request(server, data=urllib.parse.urlencode({"data": abfrage}).encode(),
                                             headers={"User-Agent": "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"})
            with urllib.request.urlopen(anfrage, timeout=120) as antwort:
                return json.loads(antwort.read().decode("utf-8"))
        except Exception as exc:
            letzter = exc
            log.info("Overpass %s: %s", server, exc)
    raise RuntimeError(f"Kein Overpass-Server erreichbar ({letzter})")


def gleise_laden(conn, daten: dict | None = None) -> int:
    """RE6-Route aus OpenStreetMap: Wege in Reihenfolge aneinanderhängen, dabei jeden so
    drehen, dass er am Ende des vorigen anschließt."""
    daten = daten or overpass_holen()
    relation = daten["elements"][0]
    wege = [[(g["lat"], g["lon"]) for g in m.get("geometry", [])]
            for m in relation["members"] if m["type"] == "way" and m.get("role", "") in ("", "route")]
    linie: list[tuple[float, float]] = []
    for weg in wege:
        if not weg:
            continue
        if not linie:
            linie = list(weg)
            continue
        ende = linie[-1]
        # passenden Anschluss wählen: vorwärts oder rückwärts
        if _abstand_m(ende, weg[-1]) < _abstand_m(ende, weg[0]):
            weg = weg[::-1]
        # Anfang der Linie falsch herum? (erster Weg verkehrt eingereiht)
        if len(linie) == len(wege[0]) and _abstand_m(linie[0], weg[0]) < _abstand_m(ende, weg[0]):
            linie.reverse()
        linie += weg[1:] if _abstand_m(linie[-1], weg[0]) < 1 else weg
    einfach = _vereinfache(linie, VEREINFACHUNG_M)
    mb.meta_set(conn, "karte_gleise", json.dumps([[round(a, 5), round(b, 5)] for a, b in einfach]))
    mb.meta_set(conn, "karte_gleise_stand", dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    log.info("Gleisführung: %d Wege, %d Punkte, vereinfacht auf %d", len(wege), len(linie), len(einfach))
    return len(einfach)


def bahnhof_position(name: str) -> tuple[float, float] | None:
    """Position des Bahnhofs selbst, nicht der Ortsmitte: transitous nach Haltestellen
    (type=STOP) mit der Schreibweise der Bahn fragen. Ohne das lag "Burg(Dithm)" 1,2 km
    neben dem Gleis, weil die Ortssuche die Ortsmitte lieferte."""
    import urllib.parse, urllib.request
    url = si.GEOCODE + "?" + urllib.parse.urlencode({"text": name, "type": "STOP"})
    anfrage = urllib.request.Request(url, headers={
        "User-Agent": "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)",
        "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(anfrage, timeout=25) as antwort:
        roh = antwort.read()
    daten = json.loads((gzip.decompress(roh) if roh[:2] == b"\x1f\x8b" else roh).decode("utf-8"))
    rein = lambda t: re.sub(r"[^a-z0-9äöüß]", "", t.lower())
    for t in daten:
        # "St Michaelisdonn" passt zu "St.Michaelisdonn", "Westerland(Sylt)" zu
        # "Westerland(Sylt) ZOB/Bahnhof" — aber nicht zu einer Bushaltestelle anderswo.
        if t.get("type") == "STOP" and t.get("country", "DE") == "DE" and rein(t["name"]).startswith(rein(name)):
            return float(t["lat"]), float(t["lon"])
    return None


def halte_neu(conn) -> int:
    """Die Bahnhöfe der Strecke auf ihre Haltestellen-Position setzen."""
    import time
    n = 0
    for nr, (name, _) in enumerate(STRECKE):
        try:
            pos = bahnhof_position(name)
        except Exception as exc:
            log.warning("%s: %s", name, exc)
            pos = None
        time.sleep(0.4)
        if not pos:
            log.info("%s: keine Haltestelle gefunden, Position bleibt", name)
            continue
        with conn.cursor() as cur:
            cur.execute("INSERT INTO koordinaten (name, lat, lon, strecke_nr, geprueft) VALUES (%s,%s,%s,%s,NOW()) "
                        "ON DUPLICATE KEY UPDATE lat=VALUES(lat), lon=VALUES(lon), strecke_nr=VALUES(strecke_nr), "
                        "geprueft=NOW()", (name, pos[0], pos[1], nr))
        n += 1
    conn.commit()
    mb.meta_set(conn, "karte_halte_stand", dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    return n


def suchbegriff(name: str) -> str:
    if name in ALIAS:
        return ALIAS[name]
    # "Niebüll (BW)" -> "Niebüll", "Itzehoe Pbf" -> "Itzehoe"
    return re.sub(r"\s*\(.*\)$", "", name).strip()


def ergaenze(conn, namen: list[tuple[str, int | None]]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM koordinaten")
        vorhanden = {r["name"] for r in cur.fetchall()}
    neu = 0
    for name, nr in namen:
        if not name:
            continue
        if name in vorhanden:
            if nr is not None:
                with conn.cursor() as cur:
                    cur.execute("UPDATE koordinaten SET strecke_nr=%s WHERE name=%s", (nr, name))
            continue
        treffer = si.ort_pruefen(conn, suchbegriff(name))
        conn.commit()
        if not treffer or float(treffer.get("entfernung") or 0) > MAX_KM:
            log.info("Keine brauchbare Position für %r", name)
            continue
        with conn.cursor() as cur:
            cur.execute("INSERT INTO koordinaten (name, lat, lon, strecke_nr, geprueft) "
                        "VALUES (%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE lat=VALUES(lat), "
                        "lon=VALUES(lon), strecke_nr=VALUES(strecke_nr), geprueft=NOW()",
                        (name, treffer["lat"], treffer["lon"], nr))
        conn.commit()
        vorhanden.add(name)
        neu += 1
        log.info("%s -> %.4f, %.4f (%s)", name, treffer["lat"], treffer["lon"], treffer.get("treffer"))
    conn.commit()
    return neu


def namen_aus_daten(conn) -> list[tuple[str, int | None]]:
    """Alle Orte, die die Karte gerade zeigen könnte."""
    namen: list[tuple[str, int | None]] = [(n, i) for i, (n, _) in enumerate(STRECKE)]
    grenze = dt.date.today() - dt.timedelta(days=3)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT von_halt AS n FROM umlaeufe WHERE tag >= %s "
                    "UNION SELECT DISTINCT nach_halt FROM umlaeufe WHERE tag >= %s "
                    "UNION SELECT DISTINCT ort FROM standorte WHERE tag >= %s",
                    (grenze, grenze, grenze))
        namen += [(r["n"], None) for r in cur.fetchall()]
    return namen


# ---------------------------------------------------------------- Landesgrenzen
# Overpass ist für die Grenzrelationen zu langsam (504 auch auf dem Ausweichserver,
# 25.09.2026). Stattdessen der fertige, freie Datensatz von deutschlandGeoJSON — Grundlage
# sind die Verwaltungsgebiete des Bundesamts für Kartographie und Geodäsie (dl-de/by-2-0).
GRENZEN_URL = ("https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/main/"
               "2_bundeslaender/3_mittel.geo.json")
# Die Außengrenze Deutschlands — ohne sie fehlt die Grenze nach Dänemark, Polen und
# Tschechien, weil die Bundesländer nur als einzelne Umrisse vorliegen.
GRENZE_DE_URL = ("https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/main/"
                 "1_deutschland/3_mittel.geo.json")
# Alle Bundesländer: Die Umrisse dienen auf der Karte als Grenzen — und zuege.py ordnet
# damit jedem Zug sein Bundesland zu (Gebietsfilter).
GRENZEN_LAENDER = ["Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg",
                   "Hessen", "Mecklenburg-Vorpommern", "Niedersachsen", "Nordrhein-Westfalen",
                   "Rheinland-Pfalz", "Saarland", "Sachsen", "Sachsen-Anhalt",
                   "Schleswig-Holstein", "Thüringen"]
GRENZEN_VEREINFACHUNG_M = 200
SCHEMA_GRENZEN = """
CREATE TABLE IF NOT EXISTS karte_grenzen (
    land   VARCHAR(40) NOT NULL PRIMARY KEY,
    punkte MEDIUMTEXT  NOT NULL,        -- Liste von Linienzügen [[[lat,lon],…],…]
    stand  DATETIME    NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""


def _vereinfache_ring(punkte: list[tuple[float, float]], toleranz: float) -> list[tuple[float, float]]:
    """Douglas-Peucker auf einem geschlossenen Ring: Anfang und Ende sind derselbe Punkt,
    die Strecke dazwischen also null lang — dann fiele alles weg. Deshalb in zwei Hälften."""
    if len(punkte) < 4 or punkte[0] != punkte[-1]:
        return _vereinfache(punkte, toleranz)
    mitte = len(punkte) // 2
    return _vereinfache(punkte[:mitte + 1], toleranz) + _vereinfache(punkte[mitte:], toleranz)[1:]


def grenzen_laden(conn) -> dict[str, int]:
    """Die Grenzen der nördlichen Bundesländer holen, vereinfachen und ablegen."""
    import urllib.request
    anfrage = urllib.request.Request(GRENZEN_URL,
        headers={"User-Agent": "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:
        daten = json.loads(antwort.read().decode("utf-8"))

    def ringe(geo: dict) -> list[list[list[float]]]:
        """GeoJSON-Koordinaten [lon,lat] → Linienzüge [lat,lon]."""
        art, koord = geo["type"], geo["coordinates"]
        gruppen = [koord] if art == "Polygon" else koord
        aus = []
        for gruppe in gruppen:
            for ring in gruppe:
                punkte = [(p[1], p[0]) for p in ring]
                klein = _vereinfache_ring(punkte, GRENZEN_VEREINFACHUNG_M)
                if len(klein) > 2:
                    aus.append([[round(a, 5), round(b, 5)] for a, b in klein])
        return aus

    def ablegen(cur, name: str, geo: dict) -> int:
        linien = ringe(geo)
        cur.execute("INSERT INTO karte_grenzen (land, punkte, stand) VALUES (%s,%s,NOW()) "
                    "ON DUPLICATE KEY UPDATE punkte=VALUES(punkte), stand=NOW()",
                    (name, json.dumps(linien, separators=(",", ":"))))
        return sum(len(l) for l in linien)

    gespeichert = {}
    with conn.cursor() as cur:
        # Zuerst das Land selbst
        anfrage_de = urllib.request.Request(
            GRENZE_DE_URL, headers={"User-Agent": "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"})
        with urllib.request.urlopen(anfrage_de, timeout=60) as antwort:
            deutschland = json.loads(antwort.read().decode("utf-8"))
        for f in deutschland.get("features", []):
            gespeichert["Deutschland"] = ablegen(cur, "Deutschland", f["geometry"])
        for f in daten.get("features", []):
            name = (f.get("properties") or {}).get("name")
            if name not in GRENZEN_LAENDER:
                continue
            gespeichert[name] = ablegen(cur, name, f["geometry"])
    conn.commit()
    log.info("Landesgrenzen: %s", ", ".join(f"{k} {v} Punkte" for k, v in gespeichert.items()))
    return gespeichert


def main() -> int:
    p = argparse.ArgumentParser(description="Koordinaten für die Karte")
    p.add_argument("--liste", action="store_true")
    p.add_argument("--gleise", action="store_true", help="Gleisführung jetzt neu laden")
    p.add_argument("--halte", action="store_true", help="Bahnhöfe der Strecke auf ihre Haltestellen setzen")
    p.add_argument("--gleise-datei", metavar="JSON", help="Gleisführung aus einer gespeicherten Overpass-Antwort")
    p.add_argument("--grenzen", action="store_true", help="Landesgrenzen laden (selten nötig)")
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
            cur.execute(SCHEMA_GRENZEN)
        conn.commit()
        if args.grenzen:
            for land, punkte in grenzen_laden(conn).items():
                print(f"  {land:<26}{punkte:>6} Punkte")
            return 0
        if args.liste:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM koordinaten ORDER BY strecke_nr IS NULL, strecke_nr, name")
                for r in cur.fetchall():
                    print(f"{r['strecke_nr'] if r['strecke_nr'] is not None else '–':>3} "
                          f"{r['name']:<28} {r['lat']:.4f}, {r['lon']:.4f}")
            return 0
        neu = ergaenze(conn, namen_aus_daten(conn))
        if args.halte or not mb.meta_get(conn, "karte_halte_stand"):
            print("Bahnhöfe auf Haltestellen gesetzt:", halte_neu(conn)) if args.halte else halte_neu(conn)
            if args.halte:
                return 0
        if args.gleise_datei:
            with open(args.gleise_datei, encoding="utf-8") as fh:
                print("Gleisführung:", gleise_laden(conn, json.load(fh)), "Punkte")
            return 0
        stand = mb.meta_get(conn, "karte_gleise_stand")
        if args.gleise or not stand or (dt.datetime.now() - dt.datetime.fromisoformat(stand)).days >= GLEISE_ALTER_TAGE:
            try:
                print("Gleisführung:", gleise_laden(conn), "Punkte") if args.gleise else gleise_laden(conn)
            except Exception as exc:
                log.warning("Gleisführung nicht geladen: %s", exc)
        if neu:
            log.info("%d neue Positionen", neu)
        mb.herzschlag(conn, "karte")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

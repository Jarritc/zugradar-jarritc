#!/usr/bin/env python3
"""
Welche Züge kommen an einer Stelle der Strecke vorbei — auf Anfrage aus der Karte.

Tippt man auf der Karte auf ein Gleis, trägt strecke.php den Punkt in `strecken_punkte`
ein; dieser Dienst beantwortet ihn (wie bahnhof.py die Abfahrtstafeln). Grundlage ist
transitous map/trips für einen kleinen Ausschnitt um den Punkt — bei so wenig Fläche sind
auch 90 Minuten kein Problem (der große Kartenausschnitt schafft nur wenige Minuten).

Für jeden Fahrtabschnitt, dessen Verlauf höchstens NAH_M am Punkt vorbeiführt, wird die
Durchfahrtszeit anteilig nach der Strecke zwischen Abfahrt und Ankunft geschätzt.

    punkt.py --warteschlange [--sekunden 55]
    punkt.py --abruf 53.83 9.55
    punkt.py --abruf 53.75 9.65 --naehe      Züge in der Nähe eines Standorts (Startseite)
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marschbahn as mb
import zuege

TZ = ZoneInfo("Europe/Berlin")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "punkt.log")
LOCK_PATH = os.path.join(BASE_DIR, "logs", "punkt.lock")
log = logging.getLogger("punkt")

VORAUS_MINUTEN = 90
NAH_M = 200                     # so nah muss ein Verlauf am Punkt vorbeiführen
GUELTIG_SEKUNDEN = 120
HOECHSTENS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS strecken_punkte (
    schluessel   VARCHAR(24)  NOT NULL PRIMARY KEY,   -- "53.8300,9.5500"
    lat          DOUBLE       NOT NULL,
    lon          DOUBLE       NOT NULL,
    status       VARCHAR(12)  NOT NULL,               -- offen | fertig | fehler
    daten        MEDIUMTEXT   NULL,
    fehler       VARCHAR(300) NULL,
    angefragt_am DATETIME     NOT NULL,
    geholt_am    DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""


def durchgang(punkte: list[tuple[float, float]], lat: float, lon: float) -> tuple[float, float] | None:
    """(Abstand in m, Anteil der Länge) des nächsten Punkts auf dem Verlauf."""
    k = math.cos(math.radians(lat))
    xy = [((p[1] - lon) * 111_320 * k, (p[0] - lat) * 111_320) for p in punkte]
    laengen = [0.0]
    for i in range(1, len(xy)):
        laengen.append(laengen[-1] + math.dist(xy[i - 1], xy[i]))
    if laengen[-1] <= 0:
        return None
    best, bei = math.inf, 0.0
    for i in range(1, len(xy)):
        (ax, ay), (bx, by) = xy[i - 1], xy[i]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, (-ax * dx - ay * dy) / l2)) if l2 > 0 else 0.0
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < best:
            best, bei = d, laengen[i - 1] + t * math.sqrt(l2)
    return best, bei / laengen[-1]


NAEHE_M = 3000                  # "Züge in der Nähe" (Startseite): Gleis höchstens so weit weg
NAEHE_HOECHSTENS = 12


def _abruf(lat: float, lon: float, rand: float, minuten: int) -> list[dict]:
    jetzt = dt.datetime.now(dt.timezone.utc)
    params = {"zoom": "14", "min": f"{lat - rand},{lon - rand * 1.7}", "max": f"{lat + rand},{lon + rand * 1.7}",
              "startTime": (jetzt - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "endTime": (jetzt + dt.timedelta(minutes=minuten)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return zuege.json_holen(zuege.API + "?" + urllib.parse.urlencode(params), 30)


def hole(conn, lat: float, lon: float, naehe: bool = False) -> dict:
    """Züge an einem Punkt der Strecke — oder (naehe) an allen Gleisen bis NAEHE_M um einen
    Standort, je Zug die Stelle, an der er dem Standort am nächsten kommt."""
    jetzt = dt.datetime.now(dt.timezone.utc)
    rand = 0.03 if naehe else 0.012                  # gut 3 bzw. 1 km um den Punkt
    grenze = NAEHE_M if naehe else NAH_M
    # Mitten in Hamburg zur Hauptverkehrszeit kann das Fenster zu groß sein (HTTP 422)
    minuten = VORAUS_MINUTEN
    while True:
        try:
            roh = _abruf(lat, lon, rand, minuten)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 422 or minuten <= 20:
                raise
            minuten //= 2

    je_fahrt: dict[str, dict] = {}
    for s in roh:
        if s.get("mode") not in zuege.ZUGARTEN or not s.get("trips"):
            continue
        name = (s["trips"][0].get("routeShortName") or "").strip()
        if not name or zuege.AKN_RE.match(name):
            continue
        treffer = durchgang(zuege.dekodiere(s.get("polyline") or ""), lat, lon)
        if not treffer or treffer[0] > grenze:
            continue
        ab, an = zuege.zeit(s["departure"]), zuege.zeit(s["arrival"])
        ab_plan = zuege.zeit(s.get("scheduledDeparture") or s["departure"])
        an_plan = zuege.zeit(s.get("scheduledArrival") or s["arrival"])
        anteil = treffer[1]
        wann = ab + (an - ab) * anteil
        plan = ab_plan + (an_plan - ab_plan) * anteil
        if wann < jetzt.timestamp() - 60:
            continue                                # schon vorbei
        trip = s["trips"][0].get("tripId") or name
        # Am Bahnhof enden ein Abschnitt und der nächste beginnt — nur die frühere Zeit zählt;
        # in der Nähe-Suche der Abschnitt, der dem Standort am nächsten kommt.
        if trip in je_fahrt:
            alt = je_fahrt[trip]
            if naehe and (round(alt["abstand"], -1), alt["wann"]) <= (round(treffer[0], -1), wann):
                continue
            if not naehe and alt["wann"] <= wann:
                continue
        linie, nummer = zuege.linie_und_nummer(name)
        je_fahrt[trip] = {"trip": trip, "wann": wann, "plan": plan, "name": name, "linie": linie, "nr": nummer,
                          "von": s["from"]["name"], "nach": s["to"]["name"], "echt": bool(s.get("realTime")),
                          "ab_plan": ab_plan, "abstand": treffer[0],
                          "halt": anteil < 0.02 or anteil > 0.98}
    fahrten = sorted(je_fahrt.values(), key=lambda f: f["wann"])[:NAEHE_HOECHSTENS if naehe else HOECHSTENS]
    zuege.enden_ergaenzen(conn, fahrten)            # Start und Ziel der ganzen Fahrt
    aus = []
    for f in fahrten:
        aus.append({
            "zeit": dt.datetime.fromtimestamp(f["wann"], TZ).strftime("%H:%M"),
            "plan": dt.datetime.fromtimestamp(f["plan"], TZ).strftime("%H:%M"),
            "spaet": round((f["wann"] - f["plan"]) / 60), "in_min": round((f["wann"] - jetzt.timestamp()) / 60),
            "name": f["name"], "linie": f["linie"], "nr": f["nr"], "von": f["von"], "nach": f["nach"],
            "start": f.get("start"), "ziel": f.get("ziel"), "echt": f["echt"], "halt": f["halt"],
            # Planabfahrt am Halt "von" — damit fragt die Karte bei Bedarf die Wagenreihung an
            "ab_plan": f["ab_plan"], "abstand": int(round(f["abstand"], -1)),
        })
    # Die zwei Halte, zwischen denen der Punkt liegt (häufigster Abschnitt)
    abschnitt = None
    if aus:
        paare: dict[tuple, int] = {}
        for f in aus:
            paar = tuple(sorted((f["von"], f["nach"])))
            paare[paar] = paare.get(paar, 0) + 1
        abschnitt = max(paare, key=paare.get)
    return {"fahrten": aus, "zwischen": None if naehe else (list(abschnitt) if abschnitt else None),
            "minuten": minuten, "naehe": naehe}


def beantworte(conn, schluessel: str, lat: float, lon: float) -> None:
    try:
        daten = hole(conn, lat, lon, naehe=schluessel.startswith("n:"))
        zuege.verbrauch_buchen(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE strecken_punkte SET status='fertig', daten=%s, fehler=NULL, geholt_am=NOW() "
                        " WHERE schluessel=%s", (json.dumps(daten, ensure_ascii=False), schluessel))
        log.info("%s: %d Züge", schluessel, len(daten["fahrten"]))
    except Exception as exc:
        meldung = f"{type(exc).__name__}: {exc}"[:300]
        log.warning("%s: %s", schluessel, meldung)
        with conn.cursor() as cur:
            cur.execute("UPDATE strecken_punkte SET status='fehler', fehler=%s, geholt_am=NOW() "
                        " WHERE schluessel=%s", (meldung, schluessel))
    conn.commit()


def warteschlange(conn, sekunden: int) -> None:
    ende = time.monotonic() + sekunden
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT schluessel, lat, lon FROM strecken_punkte WHERE status='offen' "
                        " ORDER BY angefragt_am LIMIT 5")
            offen = cur.fetchall()
        conn.commit()
        for z in offen:
            beantworte(conn, z["schluessel"], float(z["lat"]), float(z["lon"]))
        if time.monotonic() + 1 > ende:
            break
        time.sleep(1)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM strecken_punkte WHERE angefragt_am < NOW() - INTERVAL 1 DAY")
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description="Züge an einer Stelle der Strecke")
    p.add_argument("--warteschlange", action="store_true")
    p.add_argument("--sekunden", type=int, default=0)
    p.add_argument("--abruf", nargs=2, type=float, metavar=("LAT", "LON"))
    p.add_argument("--naehe", action="store_true", help="mit --abruf: Züge in der Nähe")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(zuege.SCHEMA_ENDEN)
    conn.commit()

    if args.abruf:
        daten = hole(conn, *args.abruf, naehe=args.naehe)
        print("zwischen:", daten["zwischen"])
        for f in daten["fahrten"]:
            print(f"{f['zeit']} ({'+' + str(f['spaet']) if f['spaet'] else '±0'}) {f['abstand']:>5} m {f['name']:<16} "
                  f"{f['von']} → {f['nach']}  | {f['start']} → {f['ziel']}")
    elif args.warteschlange:
        sperre = open(LOCK_PATH, "w")
        try:
            fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        warteschlange(conn, args.sekunden)
        mb.herzschlag(conn, "punkt")
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Fahrplan- und Echtzeitdaten für Elmshorn über transitous.

transitous (api.transitous.org) ist ein offener MOTIS-Dienst auf Basis der
DELFI-Open-Data. Kostenlos, ohne Schlüssel, mit Echtzeit aus GTFS-RT.

Warum nicht die DB direkt: transport.rest antwortet dauerhaft mit 503,
bahn.de blockt automatisierte Zugriffe (403 OPS_BLOCKED), und die APIs im
DB API Marketplace kosten Geld.

Verlässlichkeit: ein von Freiwilligen betriebener Dienst ohne Zusage. Jede
Funktion hier wirft im Fehlerfall, und der Aufrufer fällt auf die Schätzung
aus marschbahn.FAHRZEIT_PROFIL zurück — es fällt also nie eine Meldung aus,
sie wird nur ungenauer.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASIS = "https://api.transitous.org/api/v1"
TZ = ZoneInfo("Europe/Berlin")
KOPF = {"User-Agent": "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)",
        "Accept": "application/json"}

# Zugnummer aus "RE6 (11029)" bzw. "RB61 (75536)"
NUMMER_RE = re.compile(r"\((\d{3,5})\)")


def _hole(pfad: str, **params) -> dict:
    url = f"{BASIS}/{pfad}?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers=KOPF)
    with urllib.request.urlopen(req, timeout=30) as antwort:
        return json.loads(antwort.read().decode("utf-8"))


def _lokal(iso: str | None) -> dt.datetime | None:
    if not iso:
        return None
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def _hhmm(zeitpunkt: dt.datetime | None) -> str | None:
    return zeitpunkt.strftime("%H:%M") if zeitpunkt else None


def halt_suchen(name: str) -> tuple[str, str] | None:
    """Haltestellen-ID zu einem Namen, z. B. um die ID in der Config zu prüfen."""
    for treffer in _hole("geocode", text=name):
        if treffer.get("type") == "STOP":
            return treffer["id"], treffer["name"]
    return None


def _tafel(stop_id: str, ab: dt.datetime, seiten: int = 8,
           pro_seite: int = 50) -> list[dict]:
    """Rohe Halte-Einträge ab einem Zeitpunkt, über mehrere Seiten."""
    eintraege, cursor = [], None
    for _ in range(seiten):
        if cursor:
            d = _hole("stoptimes", stopId=stop_id, n=pro_seite, pageCursor=cursor)
        else:
            d = _hole("stoptimes", stopId=stop_id, n=pro_seite,
                      time=ab.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        eintraege.extend(d.get("stopTimes", []))
        cursor = d.get("nextPageCursor")
        if not cursor:
            break
        time.sleep(0.5)                      # höflich gegenüber einem freien Dienst
    return eintraege


def _zerlegen(f: dict) -> dict | None:
    p = f.get("place", {})
    m = NUMMER_RE.search(f.get("routeShortName", ""))
    if not m:
        return None
    soll = _lokal(p.get("scheduledDeparture") or p.get("scheduledArrival"))
    ist = _lokal(p.get("departure") or p.get("arrival"))
    if not soll:
        return None
    verspaetung = int((ist - soll).total_seconds() // 60) if ist else 0
    return {
        "nummer": m.group(1),
        "linie": f.get("routeShortName", "").split(" (")[0],
        "ziel": f.get("headsign", ""),
        "soll": soll,
        "ist": ist,
        "soll_hhmm": _hhmm(soll),
        "ist_hhmm": _hhmm(ist),
        "verspaetung": verspaetung,
        "ausgefallen": bool(p.get("cancelled")),
        "echtzeit": bool(f.get("realTime")),
    }


def soll_zeiten(stop_id: str, tag: dt.date) -> dict[str, str]:
    """Zugnummer -> Soll-Zeit in Elmshorn ("HH:MM") für einen ganzen Tag."""
    start = dt.datetime.combine(tag, dt.time(0, 0), tzinfo=TZ)
    aus: dict[str, str] = {}
    for f in _tafel(stop_id, start):
        e = _zerlegen(f)
        if e and e["soll"].date() == tag:
            aus.setdefault(e["nummer"], e["soll_hhmm"])
    return aus


def ist_lage(stop_id: str, ab: dt.datetime | None = None,
             stunden: int = 3, seiten: int = 4) -> list[dict]:
    """Aktuelle Lage in Elmshorn: Verspätungen und Ausfälle im Zeitfenster.

    `seiten` steuert die Zahl der Abrufe: für die laufende Beobachtung ohne
    konkreten 218-Anlass genügt eine Seite, das hält die Last auf einem freien
    Dienst klein.
    """
    ab = ab or dt.datetime.now(TZ)
    bis = ab + dt.timedelta(hours=stunden)
    aus = []
    for f in _tafel(stop_id, ab, seiten=seiten):
        e = _zerlegen(f)
        if e and ab - dt.timedelta(minutes=30) <= e["soll"] <= bis:
            aus.append(e)
    return aus


if __name__ == "__main__":
    import sys
    stop = sys.argv[1] if len(sys.argv) > 1 else "de-DELFI_de:01056:97960"
    print("Halt:", halt_suchen("Elmshorn"))
    lage = ist_lage(stop, stunden=2)
    print(f"\n{len(lage)} Fahrten im Zeitfenster:")
    for e in lage:
        print(f"  {e['linie']:<8} {e['nummer']:<7} {e['soll_hhmm']} "
              f"{'+' + str(e['verspaetung']) + ' min' if e['verspaetung'] else 'pünktlich':<12}"
              f"{'AUSFALL' if e['ausgefallen'] else ''}")

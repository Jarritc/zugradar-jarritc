#!/usr/bin/env python3
"""
Zweite Quelle: die Timetables-API der Deutschen Bahn (DB API Marketplace).

Ergänzt fahrplan.py (transitous). Zwei Dinge kann sie, die transitous nicht
liefert:

* ein ausdrückliches Ausfallkennzeichen (cs="c"),
* Störungsmeldungen der Bahn selbst (<m cat="Störung">) — damit muss eine
  Sperrung nicht mehr aus "mehrere Züge betroffen" erschlossen werden.

Aufbau der Schnittstelle, aus echten Antworten abgelesen:

    plan/{eva}/{JJMMTT}/{HH}   Soll-Fahrplan einer Stunde
      <s id="..."><tl c="RE" n="11034"/>
                  <ar pt="2609062128" pp="1" l="RE6" ppth="..."/>
                  <dp pt="2609062129" .../></s>

    fchg/{eva}                 alle Änderungen der nächsten ~24 h
      <s id="..." eva="..."><ar ct="2609062157" l="RE6"/>...</s>

fchg enthält **keine Zugnummer** — die Verbindung läuft ausschließlich über die
id, die in beiden Antworten identisch ist. Wer das übersieht, bekommt Änderungen
ohne zu wissen, zu welchem Zug sie gehören.

Der Soll-Fahrplan kommt stundenweise, ein ganzer Tag wären also 24 Abrufe.
Deshalb bleibt für Tagesfahrpläne transitous zuständig; hier wird nur das
Echtzeitfenster von wenigen Stunden geholt.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")

# Die API nennt Verspätungsursachen nur als Zahl (<m t="d" c="48"/>) und liefert
# keine Auflösung mit. Die Zuordnung stammt aus ursachen.json — Herkunft steht
# in der Datei. Fehlt sie, wird der nackte Code ausgewiesen.
def _ursachen() -> dict[str, str]:
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ursachen.json")
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh).get("codes", {})
    except Exception:
        return {}


URSACHEN = _ursachen()


def ursache(code: str | None) -> str | None:
    if not code:
        return None
    return URSACHEN.get(str(code)) or f"Ursache {code}"


def _hole(cfg: dict, pfad: str) -> str:
    req = urllib.request.Request(
        f"{cfg['basis']}/{pfad}",
        headers={"DB-Client-Id": cfg["client_id"], "DB-Api-Key": cfg["api_key"],
                 "Accept": "application/xml"})
    with urllib.request.urlopen(req, timeout=30) as antwort:
        return antwort.read().decode("utf-8", "replace")


def _zeit(wert: str | None) -> dt.datetime | None:
    """"2609062157" -> 2026-09-06 21:57 (Ortszeit)."""
    if not wert or len(wert) != 10 or not wert.isdigit():
        return None
    return dt.datetime(2000 + int(wert[0:2]), int(wert[2:4]), int(wert[4:6]),
                       int(wert[6:8]), int(wert[8:10]), tzinfo=TZ)


def plan(cfg: dict, eva: int, ab: dt.datetime, stunden: int = 3) -> dict[str, dict]:
    """Soll-Fahrplan mehrerer Stunden, nach Fahrt-id."""
    aus: dict[str, dict] = {}
    for n in range(stunden + 1):
        stunde = ab + dt.timedelta(hours=n)
        try:
            xml = _hole(cfg, f"plan/{eva}/{stunde:%y%m%d}/{stunde:%H}")
        except Exception:
            continue                       # einzelne Stunde fehlt: nicht schlimm
        for s in ET.fromstring(xml).findall("s"):
            tl, ar, dp = s.find("tl"), s.find("ar"), s.find("dp")
            if tl is None:
                continue
            aus[s.get("id", "")] = {
                "nummer": tl.get("n", ""),
                "kategorie": tl.get("c", ""),
                # pp = geplantes Gleis. transitous liefert für Elmshorn keins,
                # diese Angabe gibt es also nur über die DB-Schnittstelle.
                "gleis": (dp.get("pp") if dp is not None else None)
                         or (ar.get("pp") if ar is not None else None),
                "linie": (dp if dp is not None else ar).get("l", "") if (ar is not None or dp is not None) else "",
                "soll_an": _zeit(ar.get("pt")) if ar is not None else None,
                "soll_ab": _zeit(dp.get("pt")) if dp is not None else None,
                "ziel": (dp.get("ppth", "").split("|")[-1] if dp is not None else ""),
            }
    return aus


def changes(cfg: dict, eva: int) -> tuple[dict[str, dict], list[dict]]:
    """Änderungen nach Fahrt-id, dazu die Meldungen der Station."""
    xml = _hole(cfg, f"fchg/{eva}")
    wurzel = ET.fromstring(xml)
    aend: dict[str, dict] = {}
    for s in wurzel.findall("s"):
        ar, dp = s.find("ar"), s.find("dp")
        # t="d" sind die Verspätungsursachen dieser Fahrt; t="q" betrifft nur
        # die Wagenreihung und wird hier nicht gebraucht.
        gruende = [ursache(m.get("c")) for m in s.iter("m") if m.get("t") == "d"]
        aend[s.get("id", "")] = {
            "ist_an": _zeit(ar.get("ct")) if ar is not None else None,
            "ist_ab": _zeit(dp.get("ct")) if dp is not None else None,
            "ausgefallen": any(k is not None and k.get("cs") == "c" for k in (ar, dp)),
            "ursachen": list(dict.fromkeys(g for g in gruende if g)),
            # cp = geändertes Gleis
            "gleis_neu": (dp.get("cp") if dp is not None else None)
                         or (ar.get("cp") if ar is not None else None),
        }

    meldungen = []
    for m in wurzel.iter("m"):
        if m.get("cat"):
            meldungen.append({
                "id": m.get("id"), "kategorie": m.get("cat"),
                "prioritaet": int(m.get("pr") or 9),
                "von": _zeit(m.get("from")), "bis": _zeit(m.get("to")),
            })
    # Dubletten entfernen: dieselbe Meldung hängt an vielen Fahrten.
    einmalig = {(m["id"], m["kategorie"]): m for m in meldungen}
    return aend, sorted(einmalig.values(), key=lambda m: m["prioritaet"])


def ist_lage(cfg: dict, eva: int, ab: dt.datetime | None = None,
             stunden: int = 3) -> tuple[list[dict], list[dict]]:
    """Lage am Halt in derselben Form wie fahrplan.ist_lage, plus Meldungen."""
    ab = ab or dt.datetime.now(TZ)
    bis = ab + dt.timedelta(hours=stunden)
    soll = plan(cfg, eva, ab - dt.timedelta(hours=1), stunden)
    aend, meldungen = changes(cfg, eva)

    aus = []
    for fahrt_id, p in soll.items():
        a = aend.get(fahrt_id, {})
        planzeit = p["soll_ab"] or p["soll_an"]
        istzeit = a.get("ist_ab") or a.get("ist_an") or planzeit
        if not planzeit or not (ab - dt.timedelta(minutes=30) <= planzeit <= bis):
            continue
        aus.append({
            "nummer": p["nummer"],
            "linie": p["linie"] or p["kategorie"],
            "ziel": p["ziel"],
            "soll": planzeit,
            "ist": istzeit,
            "soll_hhmm": planzeit.strftime("%H:%M"),
            "ist_hhmm": istzeit.strftime("%H:%M") if istzeit else None,
            "verspaetung": int((istzeit - planzeit).total_seconds() // 60) if istzeit else 0,
            "ausgefallen": bool(a.get("ausgefallen")),
            "ursachen": a.get("ursachen") or [],
            "gleis": a.get("gleis_neu") or p.get("gleis"),
            "gleis_geplant": p.get("gleis"),
            "gleis_geaendert": bool(a.get("gleis_neu")
                                    and a.get("gleis_neu") != p.get("gleis")),
            "echtzeit": fahrt_id in aend,
        })
    return sorted(aus, key=lambda e: e["soll"]), meldungen


def soll_fuer_zug(cfg: dict, eva: int, tag: dt.date, ungefaehr: str,
                  nummer: str) -> dict | None:
    """Soll-Zeit eines einzelnen Zuges, gesucht um eine ungefähre Uhrzeit herum.

    Gedacht als Nachschlag, wenn die Hauptquelle die Zugnummer nicht kennt: Der
    Soll-Fahrplan kommt stundenweise, ein ganzer Tag wären 24 Abrufe — hier sind
    es drei rund um die geschätzte Zeit.
    """
    try:
        stunde = int(ungefaehr.split(":")[0])
    except (ValueError, AttributeError):
        return None
    mitte = dt.datetime.combine(tag, dt.time(stunde), tzinfo=TZ) - dt.timedelta(hours=1)
    for eintrag in plan(cfg, eva, mitte, stunden=2).values():
        if eintrag["nummer"] == nummer:
            zeitpunkt = eintrag["soll_ab"] or eintrag["soll_an"]
            if zeitpunkt:
                return {"zeit": zeitpunkt.strftime("%H:%M"), "gleis": eintrag.get("gleis")}
    return None


def aktive_stoerungen(meldungen: list[dict], jetzt: dt.datetime | None = None) -> list[dict]:
    jetzt = jetzt or dt.datetime.now(TZ)
    return [m for m in meldungen
            if m["kategorie"].lower() != "information"
            and (m["von"] is None or m["von"] <= jetzt)
            and (m["bis"] is None or jetzt <= m["bis"])]


if __name__ == "__main__":
    import json
    cfg = json.load(open("/opt/docker/kukas-zug/config.json", encoding="utf-8"))["db_api"]
    lage, meldungen = ist_lage(cfg, cfg["eva_elmshorn"], stunden=3)
    print(f"{len(lage)} Fahrten im Fenster:")
    for e in lage[:14]:
        print(f"  {e['linie']:<7}{e['nummer']:<8}{e['soll_hhmm']}  "
              f"Gl. {str(e['gleis'] or '?'):<4}"
              f"{'+' + str(e['verspaetung']) + ' min' if e['verspaetung'] else 'pünktlich':<12}"
              f"{'GLEISWECHSEL ' if e['gleis_geaendert'] else ''}"
              f"{'AUSFALL' if e['ausgefallen'] else ''}")
    print(f"\nMeldungen: {len(meldungen)}")
    for m in meldungen[:6]:
        print(f"  {m['kategorie']:<14} Prio {m['prioritaet']}  "
              f"{m['von']:%d.%m %H:%M} - {m['bis']:%d.%m %H:%M}" if m["von"] and m["bis"]
              else f"  {m['kategorie']}")
    print("\naktive Störungen:", len(aktive_stoerungen(meldungen)))

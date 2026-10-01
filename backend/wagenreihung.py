#!/usr/bin/env python3
"""
Wagenreihung einer Abfahrt in Elmshorn: wo Lok und Wagen am Bahnsteig halten.

Woher die Daten kommen: Die Wagenreihung gibt es nur aus der internen Schnittstelle von
bahn.de. Die sperrt diesen Server (HTTP 403 OPS_BLOCKED) — das wird nicht umgangen. Die
offizielle Schnittstelle der Bahn (RIS::Transports) kostet Geld. Deshalb wird die fertige
Seite von dbf.finalrewind.org gelesen, einem frei zugänglichen, privat betriebenen Dienst
(quelloffen, /carriage-formation ist in seiner robots.txt nicht gesperrt).

Rücksicht auf diesen Dienst:
  * nur auf Knopfdruck, nichts wird auf Vorrat geholt,
  * jede Abfahrt höchstens alle GUELTIG_SEKUNDEN einmal (Zwischenspeicher in der Datenbank),
  * höchstens eine Anfrage gleichzeitig und ABSTAND_SEKUNDEN Pause dazwischen,
  * ehrliche Kennung mit Rückadresse.

Die Webseite ruft dbf nicht selbst auf: wagenreihung.php legt die Anfrage in die Tabelle,
dieses Skript beantwortet sie (Cron, wie bei den Push-Nachrichten).

    wagenreihung.py --warteschlange [--sekunden 55]   Anfragen beantworten (Cron)
    wagenreihung.py --abruf RE 11030 1789667880       einmal holen und anzeigen
    wagenreihung.py --setup                           Tabelle anlegen
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import html
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

import marschbahn as mb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "wagenreihung.log")
LOCK_PATH = os.path.join(BASE_DIR, "logs", "wagenreihung.lock")
log = logging.getLogger("wagenreihung")

QUELLE = "https://dbf.finalrewind.org/carriage-formation"
UA = "Zugradar/1.0 (+https://jarritc.de/zugradar/ueber.html; mail@jarritc.de)"
GUELTIG_SEKUNDEN = 180        # so lange gilt eine geholte Reihung als frisch
ABSTAND_SEKUNDEN = 3          # Mindestpause zwischen zwei Abrufen bei dbf
AUFBEWAHRUNG_TAGE = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS wagenreihung (
    schluessel   VARCHAR(48) NOT NULL PRIMARY KEY,   -- RE|11030|1789667880
    kategorie    VARCHAR(8)  NOT NULL,
    nummer       VARCHAR(12) NOT NULL,
    abfahrt      DATETIME    NOT NULL,
    status       VARCHAR(12) NOT NULL,               -- offen | fertig | fehler
    daten        MEDIUMTEXT  NULL,
    fehler       VARCHAR(300) NULL,
    angefragt_am DATETIME    NOT NULL,
    geholt_am    DATETIME    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


# ---------------------------------------------------------------- Lesen
EVA_ELMSHORN = 8000092


def hole(kategorie: str, nummer: str, zeitpunkt: int, eva: int = EVA_ELMSHORN) -> str:
    antwort = requests.get(QUELLE, params={"tt": kategorie, "tn": nummer,
                                           "eva": eva, "dt": zeitpunkt},
                           headers={"User-Agent": UA}, timeout=25)
    antwort.raise_for_status()
    return antwort.text


def _text(roh: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", roh)).split())


def parse(seite: str) -> dict:
    """Aus der dbf-Seite die Reihung herausziehen.

    Ergebnis: {gleis, richtung, ziel, zusammenfassung, abschnitte:[{name,von,bis}],
               wagen:[{nummer,typ,uic,lok,erste_klasse,barrierefrei,bistro,von,bis,pfeil}]}
    dbf zeichnet den Zug senkrecht; top/bottom sind Prozentwerte am Bahnsteig. Die
    übernehmen wir, dann stimmt die Lage der Wagen zu den Abschnitten auch bei uns.
    """
    aus: dict = {"gleis": None, "richtung": None, "ziel": None, "zusammenfassung": None,
                 "abschnitte": [], "wagen": []}

    if m := re.search(r'<div class="container">\s*<div style="text-align: center;">\s*Gleis ([^<]+)', seite):
        aus["gleis"] = _text(m.group(1))

    for m in re.finditer(r'<div class="section" style="\s*top: ([\d.]+)%; bottom: ([\d.]+)%;">\s*([A-Z])', seite):
        aus["abschnitte"].append({"von": float(m.group(1)), "bis": 100 - float(m.group(2)),
                                  "name": m.group(3)})

    # Wagen und die zugehörigen Angaben stehen als Paar hintereinander.
    wagen_roh = re.findall(
        r'<div class="wagon ([^"]*)" style="\s*top: ([\d.]+)%; bottom: ([\d.]+)%;\s*">(.*?)'
        r'<div class="details" style="\s*top: [\d.]+%; bottom: [\d.]+%;">(.*?)</div>',
        seite, re.S)
    for klassen, top, bottom, kasten, details in wagen_roh:
        typ = ""
        if t := re.search(r'<(?:span|a)[^>]*class="type"[^>]*>(.*?)</(?:span|a)>', details, re.S):
            typ = _text(t.group(1))
        uic = "".join(re.findall(r'<span class="uic[^"]*">([^<]*)</span>', details))
        # Loknummern lesbar machen: 92 80 1 245 201 9 -> "245 201-9"
        bezeichnung = typ
        if len(uic) == 12 and uic.startswith("9280"):
            bezeichnung = f"{uic[4:7]} {uic[7:10]}-{uic[11]}" if uic[4] == "1" else uic
        if len(uic) == 12 and uic[:5] == "92801":
            bezeichnung = f"{uic[5:8]} {uic[8:11]}-{uic[11]}"
        nummer = ""
        if n := re.search(r'<span class="wagonnumber">\s*(\d+)', kasten):
            nummer = n.group(1)
        aus["wagen"].append({
            "nummer": nummer,
            "typ": typ,
            "uic": uic,
            "bezeichnung": bezeichnung,
            "lok": "powercar" in klassen or "locomotive" in klassen,
            "erste_klasse": "firstclass" in klassen,
            "zweite_klasse": "secondclass" in klassen,
            "barrierefrei": "accessible" in kasten,
            "bistro": "restaurant" in kasten or "bistro" in kasten,
            "geschlossen": "closed" in klassen,
            "von": float(top),
            "bis": 100 - float(bottom),
            "pfeil": "hoch" if "arrow_upward" in kasten else ("runter" if "arrow_downward" in kasten else None),
        })

    if m := re.search(r'</div>\s*<div style="text-align: center;">\s*(Zug mit[^<]*(?:<[^>]+>[^<]*)*?)</div>', seite, re.S):
        aus["zusammenfassung"] = _text(m.group(1))
    if m := re.search(r'<div style="text-align: center;">\s*nach\s*([^<]+)</div>', seite):
        aus["ziel"] = _text(m.group(1))
    if m := re.search(r'in Abschnitt ([A-Z])', seite):
        aus["lok_abschnitt"] = m.group(1)

    if not aus["wagen"]:
        # dbf zeigt bei fehlenden Daten nur eine Meldung an.
        meldung = ""
        if m := re.search(r'<div class="error">(.*?)</div>', seite, re.S):
            meldung = _text(m.group(1))
        raise ValueError(meldung[:200] or "Für diese Fahrt liegt keine Wagenreihung vor.")
    return aus


def _baureihe(w: dict) -> str:
    """Lokbezeichnung: "218 453-9" aus der UIC-Nummer, sonst aus dem Typ ("V2450" -> "245")."""
    if re.match(r"^\d{3} \d{3}-\d$", w.get("bezeichnung") or ""):
        return w["bezeichnung"]
    uic = (w.get("uic") or "").replace(" ", "")
    if len(uic) == 12 and (uic.startswith("92801") or uic.startswith("9180")):
        # 92 80 1245 201-9 -> 245 201-9 (Diesel), 91 80 6193 123-4 -> 193 123-4 (Elektro)
        return f"{uic[5:8]} {uic[8:11]}-{uic[11]}"
    if m := re.match(r"^[VE](\d{3})\d$", w.get("typ") or ""):
        return m.group(1)
    return w.get("typ") or "Lok"


def _steuerwagen(typ: str) -> bool:
    """Gattungszeichen: das kleine "f" hinter den Großbuchstaben heißt Steuerwagen
    (Bpmdfa, DABpzfa, DABpbzfa). Bei Triebzügen ist "Ef" der Endwagen mit Führerstand."""
    return bool(re.match(r"^[A-Z]+[a-z]*f[a-z]*$", typ or ""))


# Triebzüge: Baureihe aus der UIC-Nummer (Stellen 5–8, "94 80 1429 …" -> 1429), Klartext dazu.
# Mittelwagen tragen oft eine eigene Nummer (1829, 812) — maßgeblich ist der Endwagen.
TRIEBZUG_NAMEN = {
    "445": "Twindexx", "1427": "FLIRT", "1428": "FLIRT", "1429": "FLIRT", "1430": "FLIRT",
    "3427": "FLIRT", "3428": "FLIRT", "3429": "FLIRT",
    "1440": "Coradia Continental", "1441": "Coradia Continental",
    "1442": "Talent 2", "1443": "Talent 2", "1462": "Mireo", "1463": "Mireo",
    "425": "Quietschie", "426": "Quietschie", "422": "ET 422", "423": "ET 423",
    "622": "LINT 54", "623": "LINT 41", "648": "LINT 41", "642": "Desiro", "643": "Talent",
    "401": "ICE 1", "402": "ICE 2", "403": "ICE 3", "406": "ICE 3", "407": "ICE 3 Velaro",
    "408": "ICE 3neo", "411": "ICE T", "415": "ICE T", "412": "ICE 4", "812": "ICE 4",
    "4110": "IC 2 KISS", "4010": "Railjet",
}
# Wo der Endwagen anders nummeriert ist, als die Baureihe heißt
TRIEBZUG_REIHE = {"812": "412", "1812": "412", "1829": "1429", "1830": "1430"}
TRAKTION_UIC = ("91", "93", "94", "95", "96")      # Triebköpfe, Hochgeschwindigkeit, ET, VT


def _triebzug_reihe(w: dict) -> str | None:
    uic = (w.get("uic") or "").replace(" ", "")
    if len(uic) == 12 and uic[:2] in TRAKTION_UIC:
        reihe = uic[4:8]
        return reihe.lstrip("0") if reihe.startswith("0") else reihe
    return None


def _married_pair(w: dict) -> bool:
    """Die Wagen der Marschbahn: je zwei fest gekuppelt (ABpma, Bpmdza, Bpmdfa, 55 80 …)."""
    uic = (w.get("uic") or "").replace(" ", "")
    return uic.startswith("5580") and bool(re.match(r"^A?Bpm", w.get("typ") or ""))


def fahrzeuge(wagen: list[dict], kategorie: str = "") -> dict:
    """Was fährt da und was ist vorne — für die Karte.

    {"text": "218 453-9 + 6 Married-Pair-Wagen" | "445 Twindexx" | "1429 FLIRT · 2 Einheiten",
     "art": "lok"|"triebzug", "dosto": bool, "vorne": "Lok 218 453-9"|"Steuerwagen"|None,
     "loks": ["218 453-9"]}
    """
    loks = [w for w in wagen if w.get("lok")]
    rest = [w for w in wagen if not w.get("lok")]
    reihen = [r for r in (_triebzug_reihe(w) for w in rest) if r]
    triebzug = not loks and bool(rest) and (bool(reihen) or all(
        re.match(r"^([EV]f?|I\d+)$", w.get("typ") or "") for w in rest))
    dosto = bool(rest) and sum(1 for w in rest if (w.get("typ") or "").startswith("D")) * 2 > len(rest)
    if triebzug:
        # Endwagen (mit Führerstand) bestimmen die Baureihe, sonst die häufigste
        enden = [_triebzug_reihe(w) for w in rest if _steuerwagen(w.get("typ") or "") and _triebzug_reihe(w)]
        reihe = (enden or reihen or [""])[0]
        name = TRIEBZUG_NAMEN.get(reihe)
        if not name and rest[0].get("typ", "").startswith("I"):
            name = "ICE"
        text = f"{TRIEBZUG_REIHE.get(reihe, reihe)} {name}".strip() if name else (f"Triebzug {reihe}" if reihe else "Triebzug")
        einheiten = sum(1 for w in rest if _steuerwagen(w.get("typ") or "")) // 2
        if einheiten > 1:
            text += f" · {einheiten} Einheiten"
    elif kategorie.upper() == "ICE" and loks:
        # ICE L: die einzigen lokbespannten ICE — Talgo-Wagen hinter Vectron (193) oder
        # der eigenen Talgo-Lok (105). Die Bahn führt sie als "ICE" mit Nummer.
        text = "ICE L · " + " + ".join(_baureihe(w) for w in loks)
        if rest:
            text += f" + {len(rest)} Talgo-Wagen"
    else:
        teile = [" + ".join(_baureihe(w) for w in loks)] if loks else []
        if rest:
            if sum(1 for w in rest if _married_pair(w)) * 2 > len(rest):
                art = "Married-Pair-Wagen"
            else:
                art = "Dostos" if dosto else "Wagen"
            teile.append(f"{len(rest)} {art}")
        text = " + ".join(teile)
        if not loks and rest:
            text += " (Lok nicht gemeldet)"
    vorne = None
    pfeil = next((w.get("pfeil") for w in wagen if w.get("pfeil")), None)
    if pfeil and len(wagen) > 1 and not triebzug:
        # "hoch": der erste Wagen der Liste fährt vorn (wie in kurzfassung)
        spitze = wagen[0] if pfeil == "hoch" else wagen[-1]
        if spitze.get("lok"):
            vorne = "Lok " + _baureihe(spitze)
        elif _steuerwagen(spitze.get("typ") or ""):
            vorne = "Steuerwagen" + (" (Dosto)" if (spitze.get("typ") or "").startswith("D") else "")
        else:
            vorne = "Wagen " + (spitze.get("typ") or "")
    return {"text": text, "art": "triebzug" if triebzug else "lok", "dosto": dosto, "vorne": vorne,
            "loks": [_baureihe(w) for w in loks]}


def kurzfassung(daten: dict) -> str | None:
    """"Lok vorne, Abschnitt F" — für Benachrichtigungen. None, wenn keine Lok erkennbar."""
    if not daten:
        return None
    wagen = daten.get("wagen") or []
    lok = next((w for w in wagen if w.get("lok")), None)
    if not lok:
        return None
    abschnitt = None
    mitte = (lok["von"] + lok["bis"]) / 2
    for a in daten.get("abschnitte") or []:
        if a["von"] <= mitte <= a["bis"]:
            abschnitt = a["name"]
    # Die Pfeile zeigen die Fahrtrichtung: "hoch" ist das Ende mit dem letzten Abschnitt.
    pfeil = next((w.get("pfeil") for w in wagen if w.get("pfeil")), None)
    vorne = lok is (wagen[-1] if pfeil == "runter" else wagen[0]) if pfeil else None
    teile = [lok.get("bezeichnung") or "Lok"]
    if vorne is not None and len(wagen) > 1:
        teile.append("vorne" if vorne else "hinten")
    if abschnitt:
        teile.append(f"Abschnitt {abschnitt}")
    return ", ".join(teile)


def fuer_fahrt(conn, kategorie: str, nummer: str, zeitpunkt: int, erzwingen: bool = False,
               eva: int | None = None) -> dict:
    """Aus dem Zwischenspeicher, sonst frisch holen. Ohne eva: Abfahrt in Elmshorn."""
    eva = eva or EVA_ELMSHORN
    schluessel = f"{kategorie}|{nummer}|{zeitpunkt}" + ("" if eva == EVA_ELMSHORN else f"|{eva}")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM wagenreihung WHERE schluessel=%s", (schluessel,))
        alt = cur.fetchone()
    frisch = (alt and alt["geholt_am"]
              and (dt.datetime.now() - alt["geholt_am"]).total_seconds() < GUELTIG_SEKUNDEN)
    if frisch and not erzwingen:
        return {"status": alt["status"], "daten": json.loads(alt["daten"]) if alt["daten"] else None,
                "fehler": alt["fehler"], "geholt_am": alt["geholt_am"]}

    status, daten, fehler = "fertig", None, None
    try:
        daten = parse(hole(kategorie, nummer, zeitpunkt, eva))
        daten["fahrzeuge"] = fahrzeuge(daten["wagen"], kategorie)
    except requests.HTTPError as exc:
        # dbf antwortet mit 500, wenn die Bahn zu dieser Fahrt nichts liefert.
        log.info("%s %s: HTTP %s von dbf", kategorie, nummer, exc.response.status_code)
        status, fehler = "fehler", ("Für diese Fahrt liegt keine Wagenreihung vor. "
                                    "Meist gibt es sie erst kurz vor der Abfahrt.")
    except requests.RequestException as exc:
        log.warning("%s %s: %s", kategorie, nummer, exc)
        status, fehler = "fehler", "Die Quelle war gerade nicht erreichbar."
    except Exception as exc:
        status = "fehler"
        fehler = (str(exc) or type(exc).__name__)[:300]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO wagenreihung (schluessel, kategorie, nummer, abfahrt, eva, status, daten, "
            "       fehler, angefragt_am, geholt_am) VALUES (%s,%s,%s,FROM_UNIXTIME(%s),%s,%s,%s,%s,NOW(),NOW()) "
            "ON DUPLICATE KEY UPDATE status=VALUES(status), daten=VALUES(daten), "
            "       fehler=VALUES(fehler), geholt_am=NOW()",
            (schluessel, kategorie, nummer, zeitpunkt, eva, status,
             json.dumps(daten, ensure_ascii=False) if daten else None, fehler))
    conn.commit()
    log.info("%s %s (%s): %s", kategorie, nummer,
             dt.datetime.fromtimestamp(zeitpunkt).strftime("%d.%m. %H:%M"), fehler or "ok")
    return {"status": status, "daten": daten, "fehler": fehler, "geholt_am": dt.datetime.now()}


def warteschlange(conn, sekunden: int) -> None:
    """Offene Anfragen der Webseite beantworten."""
    ende = time.monotonic() + sekunden
    zuletzt = 0.0
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wagenreihung WHERE status='offen' ORDER BY angefragt_am LIMIT 5")
            offen = list(cur.fetchall())
        conn.commit()
        for a in offen:
            warte = ABSTAND_SEKUNDEN - (time.monotonic() - zuletzt)
            if warte > 0:
                time.sleep(warte)
            zuletzt = time.monotonic()
            fuer_fahrt(conn, a["kategorie"], a["nummer"], int(a["abfahrt"].timestamp()), erzwingen=True,
                       eva=a.get("eva"))
        if time.monotonic() + 2 > ende:
            break
        time.sleep(2)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM wagenreihung WHERE angefragt_am < NOW() - INTERVAL %s DAY",
                    (AUFBEWAHRUNG_TAGE,))
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description="Wagenreihung für Abfahrten in Elmshorn")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--warteschlange", action="store_true")
    p.add_argument("--sekunden", type=int, default=0)
    p.add_argument("--abruf", nargs=3, metavar=("KATEGORIE", "NUMMER", "UNIXZEIT"))
    p.add_argument("--eva", type=int, help="Bahnhof für --abruf (Standard Elmshorn)")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        # Seit der Karte auch andere Bahnhöfe als Elmshorn (18.09.2026)
        cur.execute("SHOW COLUMNS FROM wagenreihung LIKE 'eva'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE wagenreihung ADD COLUMN eva INT NULL AFTER abfahrt")
    conn.commit()

    if args.abruf:
        ergebnis = fuer_fahrt(conn, args.abruf[0], args.abruf[1], int(args.abruf[2]), erzwingen=True,
                              eva=args.eva)
        print(json.dumps(ergebnis, ensure_ascii=False, indent=2, default=str))
    elif args.warteschlange:
        sperre = open(LOCK_PATH, "w")
        try:
            fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0                     # ein Durchgang läuft noch
        warteschlange(conn, args.sekunden)
    elif not args.setup:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

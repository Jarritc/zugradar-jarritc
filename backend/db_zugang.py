#!/usr/bin/env python3
"""
Trägt die Zugangsdaten der DB-Timetables-API in config.json ein.

Die Eingabe erfolgt interaktiv, damit der Schlüssel weder in der Shell-Historie
noch in der Prozessliste landet. Geschrieben wird erst, wenn die API die Daten
akzeptiert hat — ein Fehlversuch lässt config.json unangetastet.

    python3 db_zugang.py            fragt nach und prüft
    python3 db_zugang.py --pruefen  prüft nur den gespeicherten Zugang
    python3 db_zugang.py --diagnose klopft mehrere Endpunkte ab (speichert nichts)
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASIS = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def abfrage(pfad: str, client_id: str, api_key: str) -> str:
    req = urllib.request.Request(
        f"{BASIS}/{pfad}",
        headers={"DB-Client-Id": client_id, "DB-Api-Key": api_key,
                 "Accept": "application/xml"})
    with urllib.request.urlopen(req, timeout=25) as antwort:
        return antwort.read().decode("utf-8", "replace")


def eva_elmshorn(client_id: str, api_key: str) -> tuple[int, str] | None:
    """EVA-Nummer über die API bestimmen, statt sie zu raten."""
    xml = abfrage("station/Elmshorn", client_id, api_key)
    for m in re.finditer(r"<station\b([^>]*)/?>", xml):
        attr = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if attr.get("name", "").strip().lower() == "elmshorn":
            return int(attr["eva"]), attr["name"]
    m = re.search(r"<station\b([^>]*)/?>", xml)
    if m:
        attr = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if "eva" in attr:
            return int(attr["eva"]), attr.get("name", "?")
    return None


MARKT = "https://apis.deutschebahn.com/db-api-marketplace/apis"

def diagnose(client_id: str, api_key: str) -> None:
    """Mehrere Endpunkte anklopfen, um einzugrenzen, woran es hängt."""
    import datetime as dt
    jetzt = dt.datetime.now()
    proben = [
        ("Timetables — Station Elmshorn",
         f"{MARKT}/timetables/v1/station/Elmshorn"),
        ("Timetables — Ist-Daten (fchg)",
         f"{MARKT}/timetables/v1/fchg/8001307"),
        ("Timetables — Soll-Fahrplan (plan)",
         f"{MARKT}/timetables/v1/plan/8001307/"
         f"{jetzt:%y%m%d}/{jetzt:%H}"),
        ("StaDa — Stationsdaten (andere API, prüft das Konto)",
         f"{MARKT}/station-data/v2/stations?searchstring=Elmshorn"),
        # Wagenreihung. Ohne Parameter antwortet die API mit 400/404/406, sobald das Abo
        # greift — nur 401/403 heißt "kein Abo".
        ("RIS::Transports — Wagenreihung (Abo-Test)",
         f"{MARKT}/ris-transports/v3/vehicle-sequences/departures/8000092"),
        ("Alter Host api.deutschebahn.com",
         "https://api.deutschebahn.com/timetables/v1/station/Elmshorn"),
    ]
    print(f"\n{'Endpunkt':<52} Ergebnis")
    print("-" * 78)
    for name, url in proben:
        req = urllib.request.Request(url, headers={
            "DB-Client-Id": client_id, "DB-Api-Key": api_key,
            "Accept": "application/xml"})
        try:
            with urllib.request.urlopen(req, timeout=25) as antwort:
                print(f"{name:<52} 200 OK ({len(antwort.read())} Bytes)")
        except urllib.error.HTTPError as exc:
            print(f"{name:<52} {exc.code} — {api_meldung(exc)[:70]}")
        except Exception as exc:
            print(f"{name:<52} {type(exc).__name__}")
    print("-" * 78)
    print("Deutung:")
    print("  Alles 403          -> Konto/Anwendung noch nicht freigeschaltet.")
    print("  Nur Timetables 403 -> das Abo für Timetables fehlt oder greift noch nicht.")
    print("  Timetables 200     -> alles da, sag mir Bescheid.")
    print("  RIS::Transports 401 -> Abo für die Wagenreihung fehlt noch.")
    print("  RIS::Transports 400/404/406 -> Abo greift (die Probe hat absichtlich keine Parameter).")


def lade() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def speichere(cfg: dict) -> None:
    # Eigentümer der bestehenden Datei übernehmen: Wird das Skript als root
    # ausgeführt, gehörte config.json danach sonst root und der Cron (Benutzer
    # claude) könnte sie nicht mehr lesen.
    alt_stat = os.stat(CONFIG)
    tmp = CONFIG + ".neu"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    try:
        os.chown(tmp, alt_stat.st_uid, alt_stat.st_gid)
    except PermissionError:
        pass                      # als normaler Benutzer bereits richtig
    os.replace(tmp, CONFIG)


def api_meldung(exc: urllib.error.HTTPError) -> str:
    """Den Klartext der API aus dem Fehlerkörper ziehen — der sagt meist genau,
    was fehlt, und ist jedem geratenen Hinweis überlegen."""
    try:
        koerper = exc.read().decode("utf-8", "replace")
    except Exception:
        return ""
    for muster in (r"<moreInformation>(.*?)</moreInformation>",
                   r'"moreInformation"\s*:\s*"(.*?)"',
                   r"<httpMessage>(.*?)</httpMessage>",
                   r'"message"\s*:\s*"(.*?)"'):
        m = re.search(muster, koerper, re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return koerper.strip()[:200]


def fehler_hinweis(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        meldung = api_meldung(exc)
        zusatz = f"\n  Antwort der API: {meldung}" if meldung else ""
        if exc.code == 401:
            return ("Die API weist die Daten zurück (401) — Client ID oder API Key "
                    "stimmen nicht." + zusatz)
        if exc.code == 403:
            return ("Zugangsdaten gültig, aber keine Berechtigung (403). Die "
                    "Anwendung hat die Timetables-API noch nicht freigeschaltet."
                    + zusatz +
                    "\n  Zum Eingrenzen: python3 db_zugang.py --diagnose")
        if exc.code == 404:
            return "Endpunkt nicht gefunden (404)." + zusatz
        if exc.code == 429:
            return "Zu viele Anfragen (429) — kurz warten." + zusatz
        return f"HTTP {exc.code} von der API." + zusatz
    return f"{type(exc).__name__}: {exc}"


def main() -> int:
    nur_pruefen = "--pruefen" in sys.argv
    nur_diagnose = "--diagnose" in sys.argv
    cfg = lade()

    if nur_diagnose:
        zugang = cfg.get("db_api") or {}
        if zugang.get("client_id"):
            client_id, api_key = zugang["client_id"], zugang["api_key"]
            print("Nutze den bereits hinterlegten Zugang.")
        else:
            print("Zugangsdaten für die Diagnose (werden nicht gespeichert):")
            client_id = getpass.getpass("Client ID: ").strip()
            api_key = getpass.getpass("API Key  : ").strip()
            if not client_id or not api_key:
                print("Abgebrochen: Eingabe war leer.")
                return 1
        diagnose(client_id, api_key)
        return 0

    if nur_pruefen:
        zugang = cfg.get("db_api") or {}
        if not zugang.get("client_id"):
            print("Es ist noch kein Zugang hinterlegt. Starte das Skript ohne --pruefen.")
            return 1
        client_id, api_key = zugang["client_id"], zugang["api_key"]
    else:
        print("DB API Marketplace — Zugangsdaten für die Timetables-API")
        print("Registrierung: https://developers.deutschebahn.com")
        print("Die Eingabe wird nicht angezeigt und nicht in der Historie gespeichert.\n")
        client_id = getpass.getpass("Client ID: ").strip()
        api_key = getpass.getpass("API Key  : ").strip()
        if not client_id or not api_key:
            print("\nAbgebrochen: Eingabe war leer. config.json bleibt unverändert.")
            return 1

    print("\nPrüfe den Zugang gegen die API ...")
    try:
        treffer = eva_elmshorn(client_id, api_key)
    except Exception as exc:
        print("Fehlgeschlagen. " + fehler_hinweis(exc))
        print("config.json wurde nicht verändert.")
        return 1

    if not treffer:
        print("Der Zugang wird akzeptiert, aber Elmshorn war in der Antwort nicht zu finden.")
        return 1

    eva, name = treffer
    print(f"Zugang akzeptiert. Station: {name}, EVA-Nummer {eva}")

    if nur_pruefen:
        return 0

    cfg["db_api"] = {
        "basis": BASIS,
        "client_id": client_id,
        "api_key": api_key,
        "eva_elmshorn": eva,
    }   # Schwellenwerte stehen unter "fahrplan", nicht doppelt hier
    speichere(cfg)
    print(f"\nIn {CONFIG} eingetragen (Rechte 600).")
    print("Fertig — sag mir Bescheid, dann baue ich die Fahrplan-Abfrage ein.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

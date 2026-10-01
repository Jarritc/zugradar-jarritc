#!/usr/bin/env python3
"""
Beobachtet vorgemerkte Fahrten ("diese Fahrt interessiert mich") engmaschig.

Der Cron ruft alle 5 Minuten auf; wie oft eine einzelne Fahrt tatsächlich geprüft
wird, hängt vom Abstand zur Abfahrt ab (takt_fuer):

    mehr als 5 Stunden vorher (oder morgen)   stündlich, nur der Forumsbeitrag
    5 bis 2 Stunden vorher                    alle 10 Minuten, dazu Echtzeit
    ab 2 Stunden vorher bis 30 min nach Ankunft  alle 5 Minuten, dazu Echtzeit
    danach                                    nicht mehr

1. Der Tagesbeitrag im Forum wird neu gelesen. Gemeldet wird, wenn die Fahrt
   gestrichen wird, eine andere Lok sie übernimmt oder sich Abfahrt bzw. Ankunft
   ändern.
2. Echtzeit am Startbahnhof über die DB-Schnittstelle — Verspätung (ab
   merken.verspaetung_ab_minuten, Standard 5), Ausfall, Gleiswechsel.

Gemeldet wird jede Änderung einmal. Der zuletzt gemeldete Zustand steht in
interesse.stand; beim allerersten Blick auf eine Fahrt wird nur dann gemailt, wenn
schon etwas nicht stimmt.

    beobachten.py            ein Durchlauf
    beobachten.py --dry-run  ohne Mailversand, merkt sich nichts
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

import db_timetables as dbt
import marschbahn as mb

TZ = ZoneInfo("Europe/Berlin")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "beobachten.log")
log = logging.getLogger("beobachten")


# ---------------------------------------------------------------- Forum
def fahrten_des_tages(conn, session, tag: dt.date, cache: dict) -> list[dict] | None:
    """Alle Umläufe aller Baureihen aus den Beiträgen des Tages, oder None, wenn
    kein Beitrag abrufbar war — dann wird nichts als gestrichen gemeldet."""
    if tag in cache:
        return cache[tag]
    with conn.cursor() as cur:
        cur.execute("SELECT quelle, url FROM tage WHERE tag=%s", (tag,))
        threads = cur.fetchall()
    if not threads:
        cache[tag] = None
        return None
    fahrten, gelesen = [], 0
    for t in threads:
        try:
            text = mb.beitrag_text(mb.http_get(session, t["url"]))
            gelesen += 1
        except Exception as exc:
            log.warning("Beitrag %s/%s nicht abrufbar: %s", tag, t["quelle"], exc)
            continue
        fahrten.extend(mb.analysiere_beitrag(text, alle_baureihen=True)["laeufe"])
        time.sleep(1.0)
    cache[tag] = fahrten if gelesen == len(threads) else None
    return cache[tag]


# ---------------------------------------------------------------- Echtzeit
def eva_fuer(conn, dbcfg: dict, name: str) -> int | None:
    """EVA-Nummer eines Halts, dauerhaft zwischengespeichert.

    Nur exakte Namenstreffer zählen: "Westerland (Sylt)" mit Leerzeichen liefert
    sonst die Autoverladung statt des Bahnhofs.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT eva FROM stationen WHERE name=%s", (name,))
        r = cur.fetchone()
    if r:
        return r["eva"]
    eva = None
    for suche in dict.fromkeys([name, re.sub(r"\s*\(.*\)$", "", name)]):
        try:
            xml = dbt._hole(dbcfg, "station/" + urllib.parse.quote(suche))
        except Exception:
            continue
        for m in re.finditer(r"<station\b([^>]*)/?>", xml):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            if mb.normalise(a.get("name", "")).replace(" ", "") in (
                    mb.normalise(name).replace(" ", ""), mb.normalise(suche).replace(" ", "")):
                eva = int(a["eva"])
                break
        if eva:
            break
        time.sleep(0.8)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stationen (name, eva, geprueft) VALUES (%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE eva=VALUES(eva), geprueft=VALUES(geprueft)",
                    (name, eva, dt.datetime.now()))
    conn.commit()
    return eva


def echtzeit(conn, dbcfg: dict, halt: str, zeit: str, tag: dt.date, zugnummer: str) -> dict | None:
    """Lage eines Zuges an seinem Startbahnhof: Verspätung, Ausfall, Gleis."""
    eva = eva_fuer(conn, dbcfg, halt)
    if not eva:
        return None
    stunde, minute = (int(x) for x in zeit.split(":"))
    ab = dt.datetime.combine(tag, dt.time(stunde, minute), tzinfo=TZ)
    try:
        soll = dbt.plan(dbcfg, eva, ab - dt.timedelta(hours=1), stunden=1)
        aend, _ = dbt.changes(dbcfg, eva)
    except Exception as exc:
        log.debug("Echtzeit für %s nicht abrufbar: %s", halt, exc)
        return None
    for fid, p in soll.items():
        if p["nummer"] != zugnummer:
            continue
        a = aend.get(fid, {})
        plan = p["soll_ab"] or p["soll_an"]
        ist = a.get("ist_ab") or a.get("ist_an") or plan
        return {"verspaetung": int((ist - plan).total_seconds() // 60) if plan and ist else 0,
                "ausgefallen": bool(a.get("ausgefallen")),
                "gleis": a.get("gleis_neu") or p.get("gleis"),
                "gleis_plan": p.get("gleis"),
                "ursachen": a.get("ursachen") or []}
    return None


# ---------------------------------------------------------------- Bewertung
def zustand(i: dict, fahrten: list[dict] | None, rt: dict | None) -> dict:
    """Aktueller Zustand einer vorgemerkten Fahrt als vergleichbares Wörterbuch."""
    z: dict = {}
    if fahrten is not None:
        gleicher_zug = [f for f in fahrten if f["zug"] == i["zug"]]
        eigene = [f for f in gleicher_zug if f["lok"] == i["lok"]]
        if eigene:
            z["plan"] = f"{eigene[0]['von_zeit']}-{eigene[0]['nach_zeit']}"
        elif gleicher_zug:
            z["lokwechsel"] = gleicher_zug[0]["lok"]
            z["plan"] = f"{gleicher_zug[0]['von_zeit']}-{gleicher_zug[0]['nach_zeit']}"
        else:
            z["gestrichen"] = True
    if rt:
        z["ausfall"] = rt["ausgefallen"]
        z["verspaetung"] = rt["verspaetung"]
        if rt["gleis"] and rt["gleis_plan"] and rt["gleis"] != rt["gleis_plan"]:
            z["gleis"] = rt["gleis"]
        if rt.get("ursachen"):
            z["ursache"] = ", ".join(rt["ursachen"])
    return z


def aenderungen(alt: dict | None, neu: dict, schwelle: int) -> list[str]:
    """Was sich gegenüber dem zuletzt gemeldeten Zustand geändert hat — lesbar."""
    erster = alt is None
    alt = alt or {}
    aus = []
    if neu.get("gestrichen") and not alt.get("gestrichen"):
        aus.append("gestrichen")
    if neu.get("lokwechsel") and neu.get("lokwechsel") != alt.get("lokwechsel"):
        aus.append(f"lokwechsel:{neu['lokwechsel']}")
    if not erster and neu.get("plan") and alt.get("plan") and neu["plan"] != alt["plan"]:
        aus.append(f"zeit:{alt['plan']}>{neu['plan']}")
    if neu.get("ausfall") and not alt.get("ausfall"):
        aus.append("ausfall")
    v_neu, v_alt = neu.get("verspaetung") or 0, alt.get("verspaetung") or 0
    if v_neu >= schwelle and (v_alt < schwelle or v_neu >= v_alt + 10):
        aus.append(f"verspaetung:{v_neu}")
    if neu.get("gleis") and neu.get("gleis") != alt.get("gleis"):
        aus.append(f"gleis:{neu['gleis']}")
    return aus


def live_text(i: dict, z: dict, jetzt: dt.datetime) -> tuple[str, str]:
    """(Titel, Text) der angepinnten Live-Meldung zu einer beobachteten Fahrt."""
    ab, _ = zeitpunkte(i)
    spaet = int(z.get("verspaetung") or 0)
    erwartet = (ab + dt.timedelta(minutes=spaet)) if ab else None
    rest = int((erwartet - jetzt).total_seconds() // 60) if erwartet else None

    lok = mb.lok_kurz(str(i["lok"]))
    if z.get("gestrichen"):
        return f"Gestrichen: {lok} {i['zug']}", "Die Fahrt steht nicht mehr im Beitrag."
    if z.get("ausfall"):
        return f"Fällt aus: {lok} {i['zug']}", "Die Bahn meldet die Fahrt als ausgefallen."

    zeit = erwartet.strftime("%H:%M") if erwartet else (i.get("von_zeit") or "")
    if rest is None:
        kopf = f"{lok} {zeit}"
    elif rest > 0:
        kopf = f"In {rest} min: {lok} {zeit}"
    elif rest > -10:
        kopf = f"Jetzt: {lok} {zeit}"
    else:
        kopf = f"Abgefahren: {lok} {zeit}"
    if spaet > 0:
        kopf += f" (+{spaet})"
    if z.get("gleis"):
        kopf += f", Gleis {z['gleis']}"

    teile = [f"{i['zug']} · {i.get('von_halt') or '?'} → {i.get('nach_halt') or '?'}"]
    if spaet > 0:
        teile.append(f"+{spaet} min gegenüber {i.get('von_zeit') or 'Plan'}")
    elif rest is not None and rest > 0:
        teile.append("pünktlich laut Echtzeitdaten")
    if z.get("lokwechsel"):
        teile.append(f"jetzt mit {mb.lok_kurz(z['lokwechsel'])}")
    if z.get("ursache"):
        teile.append(f"Ursache: {z['ursache']}")
    return kopf, " · ".join(teile)


def live_melden(cfg: dict, i: dict, z: dict, jetzt: dt.datetime, wichtig: bool,
                festhalten: bool = True) -> int:
    """Eine Meldung, die auf dem Handy liegen bleibt und sich still aktualisiert.
    Nur die erste und wirkliche Änderungen machen sich bemerkbar."""
    titel, text = live_text(i, z, jetzt)
    return mb.melde(cfg, "beobachtung", mb.mit_datum(titel, i["tag"]), "", titel + "\n" + text,
                    push_text=text, url=mb.fahrt_url(i["tag"], str(i["lok"]), str(i["zug"])),
                    markierung=f"fahrt-{i['id']}", ersatz_mail=False,
                    aktionen=[mb.aktion_knopf(cfg, "entfernen", "Nicht mehr beobachten",
                                              i["tag"], str(i["lok"]), str(i["zug"]))],
                    optionen={"festhalten": festhalten, "still": not wichtig,
                              "ersetzen": wichtig})


def stand_text(z: dict) -> str:
    if z.get("gestrichen"):
        return "gestrichen"
    teile = []
    if z.get("lokwechsel"):
        teile.append(f"jetzt mit {z['lokwechsel']}")
    if z.get("plan"):
        teile.append("planmäßig " + z["plan"].replace("-", "–"))
    if z.get("ausfall"):
        teile.append("fällt aus")
    elif z.get("verspaetung"):
        teile.append(f"+{z['verspaetung']} min")
    if z.get("gleis"):
        teile.append(f"Gleis {z['gleis']}")
    return ", ".join(teile) or "unverändert"


def baue_mail(i: dict, was: list[str], z: dict) -> tuple[str, str, str]:
    lok, zug, tag = mb.lok_kurz(i["lok"]), i["zug"], i["tag"]
    wo = f"ab {i['von_halt']}" if i.get("von_halt") else ""
    haupt = was[0]
    if haupt == "gestrichen":
        betreff = f"Gestrichen: {lok} {zug}"
    elif haupt.startswith("lokwechsel"):
        betreff = f"Lokwechsel: {zug} jetzt mit {mb.lok_kurz(haupt.split(':', 1)[1])}"
    elif haupt.startswith("zeit"):
        betreff = f"Neue Zeit: {lok} {zug} {z['plan'].replace('-', '–')}"
    elif haupt == "ausfall":
        betreff = f"Ausfall: {lok} {zug} {wo}".strip()
    elif haupt.startswith("verspaetung"):
        betreff = f"+{haupt.split(':')[1]} min: {lok} {zug} {wo}".strip()
    else:
        betreff = f"Gleis {haupt.split(':')[1]}: {lok} {zug} {wo} {i.get('von_zeit') or ''}".strip()
    betreff = mb.mit_datum(betreff, tag)

    lesbar = {"gestrichen": "Die Fahrt steht nicht mehr im Beitrag.",
              "ausfall": "Die Bahn meldet die Fahrt als ausgefallen."}
    punkte = []
    for w in was:
        art, _, wert = w.partition(":")
        if art == "lokwechsel":
            punkte.append(f"Die Fahrt übernimmt jetzt {mb.mit_besonderheit(wert)}.")
        elif art == "zeit":
            alt_, neu_ = wert.split(">")
            punkte.append(f"Zeiten geändert: {alt_.replace('-', '–')} → {neu_.replace('-', '–')}.")
        elif art == "verspaetung":
            punkte.append(f"Verspätung am Startbahnhof: +{wert} min.")
        elif art == "gleis":
            punkte.append(f"Abfahrt jetzt von Gleis {wert}.")
        else:
            punkte.append(lesbar.get(art, art))
    if z.get("ursache"):
        punkte.append(f"Ursache laut Bahn: {z['ursache']}.")
    fahrt = (f"{i.get('von_halt') or '?'} {i.get('von_zeit') or ''} → "
             f"{i.get('nach_halt') or '?'} {i.get('nach_zeit') or ''}").strip()
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n"
            f"Vorgemerkte Fahrt: {mb.mit_besonderheit(i['lok'])}, {zug}\n"
            f"{fahrt} · {mb.datum_lang(tag)}\n\n" + "\n".join(f"- {p}" for p in punkte)
            + f"\n\nAktueller Stand: {stand_text(z)}\n\nÜbersicht: https://jarritc.de/zugradar/\n")
    body = (mb.RAHMEN_AUF
            + f'<div style="font-size:13px;color:#57606a">Vorgemerkte Fahrt · {html.escape(mb.datum_lang(tag))}</div>'
            f'<h2 style="margin:2px 0 6px;font-size:19px">{html.escape(betreff)}</h2>'
            f'<div style="font-size:14px;margin-bottom:12px"><b>{html.escape(mb.mit_besonderheit(i["lok"]))}</b>, '
            f'{html.escape(zug)}<br>{html.escape(fahrt)}</div>'
            '<ul style="font-size:14px;padding-left:20px;margin:0 0 12px">'
            + "".join(f"<li>{html.escape(p)}</li>" for p in punkte) + '</ul>'
            f'<div style="background:#eef1f5;border-radius:8px;padding:9px 12px;font-size:13px">'
            f'Aktueller Stand: <b>{html.escape(stand_text(z))}</b></div>'
            '<p style="margin:16px 0 0"><a href="https://jarritc.de/zugradar/" style="color:#0969da;'
            'text-decoration:none;font-size:14px">Übersicht</a></p>' + mb.RAHMEN_ZU)
    return betreff, body, text


# ---------------------------------------------------------------- Takt
def zeitpunkte(i: dict) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Abfahrt und Ankunft einer vorgemerkten Fahrt als Zeitpunkte."""
    def zp(hhmm):
        if not hhmm or not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            return None
        s_, m_ = (int(x) for x in hhmm.split(":"))
        return dt.datetime.combine(i["tag"], dt.time(s_, m_), tzinfo=TZ)
    ab, an = zp(i.get("von_zeit")), zp(i.get("nach_zeit"))
    if ab and an and an < ab:
        an += dt.timedelta(days=1)                      # über Mitternacht
    return ab, an


def takt_fuer(i: dict, jetzt: dt.datetime) -> tuple[int | None, bool]:
    """(Prüfabstand in Minuten, mit Echtzeit?) — None heißt: nicht mehr prüfen."""
    ab, an = zeitpunkte(i)
    if ab is None:
        return 60, False
    # Eine verspätete Fahrt ist noch unterwegs, wenn die Planankunft längst vorbei ist —
    # deshalb wird das Fenster um die zuletzt bekannte Verspätung verlängert.
    try:
        spaet = int(json.loads(i.get("stand") or "{}").get("verspaetung") or 0)
    except (ValueError, TypeError):
        spaet = 0
    ende = ((an or ab + dt.timedelta(hours=4))
            + dt.timedelta(minutes=30 + max(0, min(spaet, 240))))
    if jetzt > ende:
        return None, False
    bis_ab = (ab - jetzt).total_seconds() / 60
    if bis_ab <= 120:
        return 5, True
    if bis_ab <= 300:
        return 10, True
    return 60, False


def faellig(i: dict, takt: int, jetzt: dt.datetime) -> bool:
    """Ist seit der letzten Prüfung der Takt verstrichen? Eine Minute Spielraum,
    damit ein Lauf, der ein paar Sekunden früher startet, nicht übersprungen wird."""
    if not i.get("letzter_check"):
        return True
    letzte = i["letzter_check"].replace(tzinfo=TZ)
    return (jetzt - letzte).total_seconds() / 60 >= takt - 1


# ---------------------------------------------------------------- Archiv
ARCHIV_NACH_MINUTEN = 60        # so lange nach der Ankunft am Endbahnhof bleibt eine Fahrt oben


def archiv_spalten(conn) -> None:
    """Spalten fürs Archiv (seit 18.09.2026): wann und warum eine Fahrt aus der Beobachtung ging."""
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM interesse LIKE 'archiviert_am'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE interesse ADD COLUMN archiviert_am DATETIME NULL, "
                        "ADD COLUMN archiv_grund VARCHAR(16) NULL, ADD KEY idx_archiv (aktiv, archiviert_am)")
    conn.commit()


def archiv_faellig(i: dict, jetzt: dt.datetime) -> bool:
    """Eine Stunde nach der (verspäteten) Ankunft am Endbahnhof — bei einem Ausfall eine
    Stunde nach der geplanten Ankunft."""
    ab, an = zeitpunkte(i)
    ende = an or (ab + dt.timedelta(hours=4) if ab else None)
    if ende is None:                                    # ohne Zeiten: am Tag danach
        return jetzt.date() > i["tag"]
    try:
        stand = json.loads(i.get("stand") or "{}")
    except (ValueError, TypeError):                     # stand ist auf 255 Zeichen gekürzt
        stand = {}
    if not stand.get("ausfall"):
        ende += dt.timedelta(minutes=int(stand.get("verspaetung") or 0))
    return jetzt > ende + dt.timedelta(minutes=ARCHIV_NACH_MINUTEN)


def archivieren(conn, jetzt: dt.datetime) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM interesse WHERE aktiv=1 AND tag <= %s", (jetzt.date(),))
        fertig = [i for i in cur.fetchall() if archiv_faellig(i, jetzt)]
        for i in fertig:
            cur.execute("UPDATE interesse SET aktiv=0, archiviert_am=NOW(), archiv_grund='angekommen' "
                        " WHERE id=%s", (i["id"],))
            log.info("Archiv: %s %s %s (%s → %s)", i["tag"], i["lok"], i["zug"], i.get("von_halt"), i.get("nach_halt"))
    conn.commit()
    return len(fertig)


# ---------------------------------------------------------------- Lauf
def lauf(cfg: dict, conn, dry_run: bool = False) -> None:
    heute = dt.date.today()
    if not dry_run:
        archiv_spalten(conn)
        archivieren(conn, dt.datetime.now(TZ))
    with conn.cursor() as cur:
        # Sicherheitsnetz: was zwei Tage alt ist, kommt ohnehin ins Archiv.
        if not dry_run:
            cur.execute("UPDATE interesse SET aktiv=0, archiviert_am=NOW(), archiv_grund='abgelaufen' "
                        " WHERE aktiv=1 AND tag < %s", (heute - dt.timedelta(days=1),))
        cur.execute("SELECT * FROM interesse WHERE aktiv=1 AND tag BETWEEN %s AND %s ORDER BY tag, von_zeit",
                    (heute, heute + dt.timedelta(days=1)))
        vorgemerkt = cur.fetchall()
    conn.commit()
    if not vorgemerkt:
        log.debug("Keine vorgemerkten Fahrten für heute oder morgen.")
        return

    mb.lade_namen(conn)
    dbcfg = cfg.get("db_api") or {}
    schwelle = int((cfg.get("merken") or {}).get("verspaetung_ab_minuten", 5))
    session, cache = requests.Session(), {}
    jetzt = dt.datetime.now(TZ)

    for i in vorgemerkt:
        takt, mit_echtzeit = takt_fuer(i, jetzt)
        if takt is None or not faellig(i, takt, jetzt):
            continue
        fahrten = fahrten_des_tages(conn, session, i["tag"], cache)

        rt = None
        if mit_echtzeit and dbcfg.get("client_id") and i.get("von_halt"):
            rt = echtzeit(conn, dbcfg, i["von_halt"], i["von_zeit"], i["tag"],
                          re.sub(r"\D", "", i["zug"]))

        z = zustand(i, fahrten, rt)
        alt = json.loads(i["stand"]) if i.get("stand") else None
        was = aenderungen(alt, z, schwelle)
        log.info("%s %s %s [alle %d min%s]: %s%s", i["tag"], i["lok"], i["zug"], takt,
                 ", Echtzeit" if rt is not None else "", stand_text(z),
                 f" — neu: {', '.join(was)}" if was else "")
        if dry_run:
            continue

        # ---- Angepinnte Live-Meldung, sobald die Fahrt näher rückt: dieselbe Nachricht
        # wird immer wieder ersetzt, damit auf dem Handy nur eine aktuelle Zeile liegt.
        ab, _ = zeitpunkte(i)
        vorlauf = int(mb.einstellung(conn, cfg, "merken", "live_ab_minuten", 60))
        bis_ab = (ab - jetzt).total_seconds() / 60 if ab else None
        live_an = (mb.einstellung(conn, cfg, "merken", "live_meldung", True)
                   and bis_ab is not None and -15 <= bis_ab <= vorlauf)
        if live_an:
            # Bemerkbar macht sie sich beim ersten Mal und bei echten Änderungen.
            wichtig = bool(was) or (alt or {}).get("__live__") is None
            live_melden(cfg, i, z, jetzt, wichtig,
                        festhalten=bool(mb.einstellung(conn, cfg, "merken", "live_festhalten", True)))
            z["__live__"] = jetzt.strftime("%H:%M")

        if was and not live_an:
            betreff, body, text = baue_mail(i, was, z)
            # Aufs Handy nur, was sich geändert hat — die Fahrt steht schon im Titel.
            aenderung = " ".join(z[2:] for z in text.splitlines() if z.startswith("- "))
            erfolge = mb.melde(cfg, "beobachtung", betreff, body, text, push_text=aenderung or None,
                               url=mb.fahrt_url(i["tag"], str(i["lok"]), str(i["zug"])),
                               aktionen=[mb.aktion_knopf(cfg, "entfernen", "Nicht mehr beobachten",
                                                         i["tag"], str(i["lok"]), str(i["zug"]))],
                               markierung=f"beobachtung-{i['id']}")
            log.info("%s — %d Zustellungen", betreff, erfolge)
        with conn.cursor() as cur:
            cur.execute("UPDATE interesse SET letzter_check=%s, stand=%s, stand_text=%s WHERE id=%s",
                        (dt.datetime.now(), json.dumps(z, ensure_ascii=False)[:255],
                         stand_text(z)[:255], i["id"]))
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg["mail"].get("verify_tls", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    conn = mb.db_connect(cfg)
    try:
        lauf(cfg, conn, dry_run=args.dry_run)
        mb.herzschlag(conn, "beobachten")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

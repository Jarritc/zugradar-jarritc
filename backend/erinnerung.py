#!/usr/bin/env python3
"""
Erinnerung kurz vor der Durchfahrt: „In 30 min: 218 453-9 „Lukas“ 14:32, Gleis 3“.

Der Cron ruft alle 5 Minuten auf. Gemeldet wird jede aktive Elmshorn-Durchfahrt einmal,
sobald sie im Vorlauffenster liegt (Standard: 30 Minuten vorher, Fenster 10 Minuten —
so geht auch nach einem Ausfall des Cron nichts verloren, solange die Fahrt noch bevorsteht).

In der Nachricht steht, was man am Bahnsteig braucht:
  * Soll-Zeit, Verspätung und Gleis aus den Echtzeitdaten der Bahn,
  * wo die Lok hält (Wagenreihung, siehe wagenreihung.py) — vorne oder hinten und
    in welchem Abschnitt,
  * fällt der Zug aus, sagt die Erinnerung genau das,
  * Lok-Check: Die Wagenreihung nennt die Lok. Fährt statt der geplanten 218 eine andere
    218 oder gar keine (etwa eine 245), steht das in der Nachricht — bei „keine 218“ im Titel.

Nur Push, kein Mail-Ersatz: Eine Erinnerung, die erst später per Mail ankommt, hilft nicht.

    erinnerung.py              ein Durchlauf
    erinnerung.py --dry-run    zeigt nur an, verschickt nichts und merkt sich nichts
    erinnerung.py --setup      Tabelle anlegen
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmlmod
import json
import logging
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_timetables as dbt
import marschbahn as mb
import wagenreihung as wr

TZ = ZoneInfo("Europe/Berlin")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "erinnerung.log")
log = logging.getLogger("erinnerung")

SCHEMA = """
CREATE TABLE IF NOT EXISTS erinnerungen (
    tag         DATE        NOT NULL,
    lok         VARCHAR(24) NOT NULL,
    zug         VARCHAR(24) NOT NULL,
    gesendet_am DATETIME    NOT NULL,
    spaet       INT         NOT NULL DEFAULT 0,   -- zuletzt gemeldete Verspätung
    ausfall     TINYINT(1)  NOT NULL DEFAULT 0,
    PRIMARY KEY (tag, lok, zug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
NACHRUESTUNG = [
    ("spaet", "ALTER TABLE erinnerungen ADD COLUMN spaet INT NOT NULL DEFAULT 0"),
    ("ausfall", "ALTER TABLE erinnerungen ADD COLUMN ausfall TINYINT(1) NOT NULL DEFAULT 0"),
]

STANDARD = {"aktiv": True, "vorlauf_minuten": 30, "fenster_minuten": 10, "mit_wagenreihung": True,
            "nachmelden_ab_minuten": 5}


def faellige(conn, jetzt: dt.datetime, vorlauf: int, fenster: int) -> list[dict]:
    """Durchfahrten, die in vorlauf ± fenster Minuten anstehen und noch nicht erinnert sind."""
    frueh = jetzt + dt.timedelta(minutes=vorlauf - fenster)
    spaet = jetzt + dt.timedelta(minutes=vorlauf)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.* FROM laeufe l LEFT JOIN erinnerungen e"
            "    ON e.tag = l.tag AND e.lok = l.lok AND e.zug = l.zug "
            " WHERE l.aktiv = 1 AND l.elmshorn_zeit IS NOT NULL AND e.tag IS NULL"
            "   AND l.tag = %s AND l.elmshorn_zeit BETWEEN %s AND %s"
            " ORDER BY l.elmshorn_zeit",
            (jetzt.date(), frueh.strftime("%H:%M"), spaet.strftime("%H:%M")))
        return list(cur.fetchall())


def schon_erinnert(conn, jetzt: dt.datetime) -> list[dict]:
    """Bereits erinnerte Fahrten von heute, die noch nicht durch sind — für Nachmeldungen.
    Fenster großzügig: Eine Fahrt kann sich um eine Stunde verspäten, dann wartet man
    noch auf sie, und genau dann darf die Meldung nicht aufhören."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.*, e.spaet AS gemeldet_spaet, e.ausfall AS gemeldet_ausfall "
            "  FROM erinnerungen e JOIN laeufe l"
            "    ON l.tag = e.tag AND l.lok = e.lok AND l.zug = e.zug "
            " WHERE e.tag = %s AND l.aktiv = 1 AND l.elmshorn_zeit IS NOT NULL"
            "   AND ADDTIME(l.elmshorn_zeit, SEC_TO_TIME(e.spaet * 60)) >= %s",
            (jetzt.date(), (jetzt - dt.timedelta(minutes=5)).strftime("%H:%M:%S")))
        return list(cur.fetchall())


def echtzeit(cfg: dict, nummer: str, jetzt: dt.datetime) -> dict | None:
    """Verspätung, Gleis und Ausfall für diese Zugnummer in Elmshorn."""
    db = cfg.get("db_api") or {}
    if not db.get("client_id"):
        return None
    try:
        lage, _ = dbt.ist_lage(db, db.get("eva_elmshorn", 8000092), ab=jetzt, stunden=2)
    except Exception as exc:
        log.warning("Echtzeit nicht abrufbar: %s", exc)
        return None
    ziffern = "".join(c for c in nummer if c.isdigit())
    return next((e for e in lage if e["nummer"] == ziffern), None)


def lok_check(plan_lok: str, daten: dict, kategorie: str) -> dict | None:
    """Fährt laut Wagenreihung wirklich die geplante Lok? Vergleich über die Ziffern
    ("218 453-9" → 218453). None: die Wagenreihung nennt keine Lok — dann wissen wir nichts.

    {"ergebnis": "bestaetigt" | "andere_218" | "keine_218", "loks": ["245 213-4"], "text": "…"}
    """
    wagen = (daten or {}).get("wagen") or []
    if not wagen:
        return None
    loks = wr.fahrzeuge(wagen, kategorie)["loks"]
    nummern = ["".join(c for c in l if c.isdigit())[:6] for l in loks]
    if not any(len(n) >= 3 for n in nummern):
        return None
    plan = "".join(c for c in plan_lok if c.isdigit())[:6]
    if plan and plan in nummern:
        return {"ergebnis": "bestaetigt", "loks": loks, "text": f"Lok bestätigt: {loks[nummern.index(plan)]} laut Wagenreihung."}
    if any(n.startswith("218") for n in nummern):
        andere = next(l for l, n in zip(loks, nummern) if n.startswith("218"))
        return {"ergebnis": "andere_218", "loks": loks,
                "text": f"Andere 218: laut Wagenreihung {andere} statt {plan_lok}."}
    return {"ergebnis": "keine_218", "loks": loks,
            "text": f"Lok-Wechsel: laut Wagenreihung fährt {' + '.join(loks)} statt {plan_lok}."}


def reihung(conn, lauf: dict, rt: dict | None, cfg: dict) -> tuple[str | None, dict | None]:
    """Wo die Lok am Bahnsteig hält — kurz, für die Benachrichtigung — und der Lok-Check."""
    if not (cfg.get("erinnerung") or {}).get("mit_wagenreihung", True):
        return None, None
    teile = str(lauf["zug"]).split()
    if len(teile) < 2:
        return None, None
    zeit = (rt or {}).get("soll") or dt.datetime.combine(
        lauf["tag"], dt.time.fromisoformat(str(lauf["elmshorn_zeit"])), tzinfo=TZ)
    try:
        ergebnis = wr.fuer_fahrt(conn, teile[0], "".join(c for c in teile[1] if c.isdigit()),
                                 int(zeit.timestamp()))
    except Exception as exc:
        log.warning("Wagenreihung nicht abrufbar: %s", exc)
        return None, None
    daten = ergebnis.get("daten") or {}
    return wr.kurzfassung(daten), lok_check(str(lauf["lok"]), daten, teile[0])


def baue(lauf: dict, rt: dict | None, wagen: str | None, minuten: int,
         check: dict | None = None) -> tuple[str, str, str, str]:
    """(Betreff, HTML, Text, Push-Text)"""
    lok = mb.lok_kurz(str(lauf["lok"]))
    zeit = str(lauf["elmshorn_zeit"])
    gleis = (rt or {}).get("gleis") or lauf.get("gleis")
    spaet = int((rt or {}).get("verspaetung") or 0)
    aus = bool((rt or {}).get("ausgefallen"))

    if aus:
        kopf = f"Fällt aus: {lok} {zeit}"
    elif minuten < 0:                      # Nachmeldung: die Sollzeit ist schon vorbei
        kopf = f"Jetzt {(rt or {}).get('ist_hhmm') or zeit}: {lok} (+{spaet})"
    else:
        kopf = f"In {minuten} min: {lok} {zeit}"
    if gleis and not aus:
        kopf += f", Gleis {gleis}"
    # Lok-Check: fährt laut Wagenreihung gar keine 218, steht das ganz vorn im Titel.
    if check and not aus and check["ergebnis"] == "keine_218":
        kopf = f"Lok-Wechsel: {lauf['zug']} {zeit} mit {check['loks'][0].split()[0]} statt {lok}"
    elif check and not aus and check["ergebnis"] == "andere_218":
        kopf += f" — andere 218: {mb.lok_kurz(check['loks'][0])}"
    betreff = mb.mit_datum(kopf, lauf["tag"])

    zeilen = [f"{lauf['zug']} · {lauf['von_halt']} → {lauf['nach_halt']} · {lauf['richtung']}"]
    if check and not aus:
        zeilen.append(check["text"])
    if aus:
        zeilen.append("Die Bahn meldet die Fahrt als ausgefallen.")
    elif spaet > 0:
        zeilen.append(f"+{spaet} min, also etwa {(rt or {}).get('ist_hhmm') or ''} in Elmshorn."
                      .replace(" .", "."))
    elif rt:
        zeilen.append("Pünktlich laut Echtzeitdaten.")
    if (rt or {}).get("gleis_geaendert"):
        zeilen.append(f"Gleiswechsel: jetzt Gleis {gleis}.")
    if (rt or {}).get("ursachen"):
        zeilen.append("Ursache laut Bahn: " + ", ".join(rt["ursachen"]) + ".")
    if wagen and not aus:
        zeilen.append(f"Am Bahnsteig: {wagen}.")
    # Sonderlackierung oder Werbung, falls bekannt ("218 497-6 (Werbelok PIKO/Märklin)")
    mit_bes = mb.mit_besonderheit(str(lauf["lok"]))
    if mit_bes != str(lauf["lok"]) and "(" in mit_bes:
        zeilen.append(mit_bes[mit_bes.index("(") + 1:].rstrip(")").strip().capitalize() + ".")

    push_text = " · ".join(z.rstrip(".") for z in zeilen)
    text = (f"{betreff}\n{'=' * len(betreff)}\n\n" + "\n".join(f"- {z}" for z in zeilen)
            + f"\n\nÜbersicht: {mb.UEBERSICHT_URL}\n")
    body = (mb.RAHMEN_AUF
            + f'<div style="font-size:13px;color:#57606a">Erinnerung · {htmlmod.escape(mb.datum_lang(lauf["tag"]))}</div>'
            + f'<h2 style="margin:2px 0 10px;font-size:19px">{htmlmod.escape(betreff)}</h2>'
            + "<ul style=\"margin:0 0 4px;padding-left:18px;font-size:14px;line-height:1.6\">"
            + "".join(f"<li>{htmlmod.escape(z)}</li>" for z in zeilen) + "</ul>"
            + f'<p style="margin:16px 0 0">{mb.knopf(mb.UEBERSICHT_URL, "Übersicht")}</p>'
            + mb.RAHMEN_ZU)
    return betreff, body, text, push_text


def lauf(conn, cfg: dict, dry_run: bool = False) -> int:
    e = {**STANDARD, **(cfg.get("erinnerung") or {})}
    if not e["aktiv"]:
        return 0
    jetzt = dt.datetime.now(TZ)
    offen = faellige(conn, jetzt, int(e["vorlauf_minuten"]), int(e["fenster_minuten"]))
    if not offen:
        log.debug("Nichts fällig.")
        return 0

    mb.lade_namen(conn)
    gesendet = 0
    for l in offen:
        rt = echtzeit(cfg, str(l["zug"]), jetzt)
        gesendet += verschicke(conn, cfg, l, rt, jetzt, dry_run)

    # ---- Nachmeldung: wird die Fahrt deutlich später, muss man das erfahren —
    # sonst steht man nach der ersten Erinnerung zur falschen Zeit am Bahnsteig.
    schwelle = int(e["nachmelden_ab_minuten"])
    for l in schon_erinnert(conn, jetzt):
        rt = echtzeit(cfg, str(l["zug"]), jetzt)
        if not rt:
            continue
        spaet = int(rt.get("verspaetung") or 0)
        aus = bool(rt.get("ausgefallen"))
        gemeldet = int(l["gemeldet_spaet"] or 0)
        neu_ausfall = aus and not int(l["gemeldet_ausfall"] or 0)
        if not neu_ausfall and spaet - gemeldet < schwelle:
            continue
        gesendet += verschicke(conn, cfg, l, rt, jetzt, dry_run, nachmeldung=True)
    return gesendet


def verschicke(conn, cfg: dict, l: dict, rt: dict | None, jetzt: dt.datetime,
               dry_run: bool, nachmeldung: bool = False) -> int:
    """Eine Erinnerung oder Nachmeldung verschicken und den Stand festhalten."""
    wagen, check = reihung(conn, l, rt, cfg)
    if check:
        log.info("Lok-Check %s %s: %s", l["zug"], l["lok"], check["text"])
    soll = dt.datetime.combine(l["tag"], dt.time.fromisoformat(str(l["elmshorn_zeit"])), tzinfo=TZ)
    minuten = int((soll + dt.timedelta(minutes=int((rt or {}).get("verspaetung") or 0))
                   - jetzt).total_seconds() // 60)
    betreff, body, text, push_text = baue(l, rt, wagen, minuten, check)
    if dry_run:
        log.info("dry-run%s: %s | %s", " (Nachmeldung)" if nachmeldung else "", betreff, push_text)
        print(betreff, "|", push_text)
        return 0
    erfolge = mb.melde(cfg, "erinnerung", betreff, body, text, push_text=push_text,
                       markierung=f"erinnerung-{l['tag']}-{l['lok']}-{l['zug']}",
                       url=mb.fahrt_url(l["tag"], str(l["lok"]), str(l["zug"])),
                       aktionen=[mb.aktion_knopf(cfg, "merken", "Beobachten",
                                                 l["tag"], str(l["lok"]), str(l["zug"]))],
                       ersatz_mail=False)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO erinnerungen (tag, lok, zug, gesendet_am, spaet, ausfall) "
            "VALUES (%s,%s,%s,NOW(),%s,%s) ON DUPLICATE KEY UPDATE gesendet_am = NOW(), "
            "       spaet = VALUES(spaet), ausfall = VALUES(ausfall)",
            (l["tag"], l["lok"], l["zug"], int((rt or {}).get("verspaetung") or 0),
             1 if (rt or {}).get("ausgefallen") else 0))
    conn.commit()
    log.info("%s%s — %d Zustellungen", "Nachmeldung: " if nachmeldung else "", betreff, erfolge)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Erinnerung kurz vor der Durchfahrt in Elmshorn")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handler: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if args.verbose:
        handler.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=handler,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    with open(mb.CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    conn = mb.db_connect(cfg)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        for spalte, ddl in NACHRUESTUNG:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns "
                        " WHERE table_schema = DATABASE() AND table_name = 'erinnerungen'"
                        "   AND column_name = %s", (spalte,))
            if not cur.fetchone()["n"]:
                cur.execute(ddl)
    conn.commit()
    if args.setup:
        return 0
    try:
        lauf(conn, cfg, dry_run=args.dry_run)
        mb.herzschlag(conn, "erinnerung")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

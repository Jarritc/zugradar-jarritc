#!/usr/bin/env python3
"""Regressionstest der Elmshorn-Erkennung. Aufruf: python3 test_parser.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marschbahn import analysiere_beitrag, minuten, lok_name

PROBE = """
Fahrzeugeinsätze der Baureihen 218, 245 & 246 auf der Marschbahn.
____________________________________________________________
Loks / Wagenparks:
Husum:
"218 443-0 "DB Gebrauchtzug" (Donna)
"218 453-9" (Verkehrsrot) "DB Gebrauchtzug"
Married-Pair-Wagen:
11-75 002-0 + 22-75 004-3 + 80-75 001-8
__________________________________________________________
[ Doppelstockwagen mit 218, 245, 246 | RE6 / RE6x Westerland(Sylt) - Husum - Hamburg-Altona ]
"218 330-9" (Ozeanblau/Beige) + 36-81 026-7 + 26-81 054-1
RE 11029, Westerland(Sylt) (15:19) - Hamburg-Altona (18:22)
RE 4198, Hamburg-Altona (08:09) - Husum (10:01/10:03) - Westerland(Sylt) (11:05)
RE 11007, Itzehoe (06:34) - Hamburg-Altona (07:22)
RE 11031, Westerland(Sylt) (16:19) - Hamburg-Altona (19:22), Doppeltraktion mit 245 209-2
RE 11005, Elmshorn (05:30) - Westerland(Sylt) (08:34)
RE 11061, Westerland(Sylt) (06:50) - Niebüll (07:29)
RE 11003, Hamburg-Altona (04:32) - Pinneberg (04:51)
Lr, Husum -> Heide(Holstein)
Verkehrt als 2. Wageneinheit mit RE 11048 von Husum nach Hamburg-Altona
__________________________________________________________
"245 005-4" (Verkehrsrot) + 36-75 086-9
RE 11025, Westerland(Sylt) (13:17) - Hamburg-Altona (16:22)
"""

ERWARTET = {
    "RE 11029": True,   # Sylt -> Hamburg, quert Elmshorn
    "RE 4198":  True,   # mit Zwischenhalt und Doppelzeit
    "RE 11007": True,   # Itzehoe -> Hamburg, quert Elmshorn
    "RE 11031": True,   # Nachsatz "Doppeltraktion mit ..." stoert nicht
    "RE 11005": True,   # beginnt in Elmshorn
    "RE 11061": False,  # rein noerdlich
    "RE 11003": False,  # rein suedlich von Elmshorn
    "Lr":       False,  # Leerfahrt Husum -> Heide
}


# Zweite Threadreihe: "Lokübersicht der SyltShuttle und ICE's" — anderes Format,
# Zugart mit Schrägstrich, Doppeltraktion über zwei Zeilen, Abstellliste am Ende,
# dazu ein Umlauf in Thüringen, der gar nicht auf der Marschbahn liegt.
PROBE_SHUTTLE = """
Lokumlauf Sylt Shuttle :
218 497-6 (UIC 92 80 1218 497-6 D-DB) (Schwarz/Weiß) +
218 834-0 (UIC 92 80 1218 834-0 D-DB) (Verkehrsrot)
AS 1401/ Westerland(Sylt) (05:05) -> Niebüll (05:45)
Intercity-Express Westerland -> Itzehoe (-> Westerland) :
248 515-9 (UIC 90 80 2248 515-9 D-DB) (Intercity) + Tz 1807 (Talgo)
ICE 2311/ Westerland(Sylt) (09:53) -> Itzehoe (12:01)
Sonderfall :
218 385-3 (UIC 92 80 1218 385-3 D-DB) (Verkehrsrot)
Lz 76543/ Niebüll (03:10) -> Hamburg-Eidelstedt (06:40)
Intercity Gera -> Gotha (-> Gera) :
246 049-2 (UIC 92 80 1246 011-1 D-PRESS) (Blau)
IC 2156/ Gera Hbf (06:04) -> Gotha (07:35)
Weitere Loks (Nicht im Einsatz) :
Niebüll (BW/Süd/Terminal) :
218 341-6
218 832-4
"""


def pruefe_shuttle() -> int:
    e = analysiere_beitrag(PROBE_SHUTTLE)
    laeufe = {(l["lok"], l["zug"]): l for l in e["laeufe"]}
    treffer = {l["zug"] for l in e["laeufe"] if l["elmshorn"]}
    fehler = 0
    for name, bed in [
        ("Doppeltraktion: beide Loks bekommen den Umlauf",
         ("218 497-6", "AS 1401") in laeufe and ("218 834-0", "AS 1401") in laeufe),
        ("AS-Umlauf Niebuell<->Westerland ist kein Treffer", "AS 1401" not in treffer),
        ("ICE endet in Itzehoe, also kein Treffer",          "ICE 2311" not in treffer),
        ("Lz nach Hamburg-Eidelstedt ist ein Treffer",       "Lz 76543" in treffer),
        ("Zugart behaelt Schreibweise (Lz, nicht LZ)",       "Lz 76543" in {l["zug"] for l in e["laeufe"]}),
        ("Uhrzeiten erfasst",
         laeufe.get(("218 385-3", "Lz 76543"), {}).get("von_zeit") == "03:10"),
        ("Umlauf ausserhalb der Marschbahn ignoriert (Gera/Gotha)",
         not any("Gera" in l["von"] or "Gotha" in l["nach"] for l in e["laeufe"])),
        ("Gera/Gotha nicht als unbekannte Halte gemeldet",   not e["unbekannte_halte"]),
        ("abgestellte Loks zaehlen nicht als im Einsatz",
         not {"218 341-6", "218 832-4"} & set(e["loks_218"])),
        ("218er im Einsatz korrekt",
         set(e["loks_218"]) == {"218 385-3", "218 497-6", "218 834-0"}),
    ]:
        fehler += not bed
        print(f"  {'OK    ' if bed else 'FEHLER'}  {name}")
    return fehler


# Geschätzte Durchfahrtszeit in Elmshorn. Geprüft wird der Abstand zum bekannten
# Endpunkt, nicht ein fester Uhrzeit-String — sonst bricht der Test, sobald das
# Fahrzeitprofil neu kalibriert wird.
PROBE_ZEIT = """
"218 330-9" (Ozeanblau/Beige)
RE 11029, Westerland(Sylt) (15:19) - Hamburg-Altona (18:22)
RE 11030, Hamburg-Altona (19:32) - Westerland(Sylt) (22:34)
RE 11007, Itzehoe (06:34) - Hamburg-Altona (07:22)
RE 11036, Hamburg-Altona (22:32) - Husum (00:36)
RE 11005, Elmshorn (05:30) - Westerland(Sylt) (08:34)
RE 11061, Westerland(Sylt) (06:50) - Niebüll (07:29)
"""


def pruefe_zeiten() -> int:
    laeufe = {l["zug"]: l for l in analysiere_beitrag(PROBE_ZEIT)["laeufe"]}
    fehler = 0

    def abstand(zug: str, bezug: str) -> float | None:
        """Minuten zwischen Elmshorn-Schaetzung und einer bekannten Uhrzeit."""
        z = laeufe[zug].get("elmshorn_zeit")
        if not z:
            return None
        d = minuten(z) - minuten(bezug)
        return d + 1440 if d < -720 else d - 1440 if d > 720 else d

    # Hamburg-Altona <-> Elmshorn sind rund 23 Minuten; 18-28 laesst Luft fuer
    # eine spaetere Neukalibrierung, schlaegt aber bei einem echten Fehler an.
    for name, bed in [
        ("Ankunft Altona 18:22 -> Elmshorn gut 20 min vorher",
         (v := abstand("RE 11029", "18:22")) is not None and -28 <= v <= -18),
        ("Abfahrt Altona 19:32 -> Elmshorn gut 20 min spaeter",
         (v := abstand("RE 11030", "19:32")) is not None and 18 <= v <= 28),
        ("Itzehoe -> Altona: Elmshorn dazwischen",
         (v := abstand("RE 11007", "07:22")) is not None and -28 <= v <= -18),
        ("Lauf ueber Mitternacht korrekt gerechnet",
         (v := abstand("RE 11036", "22:32")) is not None and 18 <= v <= 28),
        ("Halt in Elmshorn wird exakt uebernommen",
         laeufe["RE 11005"].get("elmshorn_zeit") == "05:30"),
        ("kein Elmshorn-Bezug -> keine Zeit",
         laeufe["RE 11061"].get("elmshorn_zeit") is None),
    ]:
        fehler += not bed
        print(f"  {'OK    ' if bed else 'FEHLER'}  {name}")
    return fehler


# Eigennamen in den Lok-Kopfzeilen. Dort stehen auch Lackierung, Programm und
# UIC-Nummer — im Zweifel lieber kein Name als ein falscher.
NAMEN_PROBEN = [
    ('"218 443-0 "DB Gebrauchtzug" (Donna)',                              "Donna"),
    ('"218 330-9" (Ozeanblau/Beige) "Konrad"',                            "Konrad"),
    ('"218 460-4" (Verkehrsrot) (Lukas)',                                 "Lukas"),
    ('"218 453-9" (Verkehrsrot) "DB Gebrauchtzug"',                       None),
    ('"218 330-9" (Ozeanblau/Beige) + 36-81 026-7',                       None),
    ('218 834-0 Führend nach Westerland(Sylt) (?)',                       None),
    ('248 503-5 (UIC 90 80 2248 503-5 D-DB) (Intercity) + Tz 1823',       None),
    ('"245 017-9" (Verkehrsrot) (Nur Werkstattaufenthalt)',               None),
    ('"218 470-3" (Verkehrsrot) "NAH.SH" (Ab morgen raus aus der Übersicht)', None),
    ('245 023-7 (UIC 92 80 1245 023-7 D-DB) (HVO Sticker)',               None),
]


def pruefe_namen() -> int:
    fehler = 0
    for zeile, soll in NAMEN_PROBEN:
        ist = lok_name(zeile)
        ok = ist == soll
        fehler += not ok
        was = f"Name {soll!r}" if soll else "kein Name"
        print(f"  {'OK    ' if ok else 'FEHLER'}  {was:<16}{zeile[:56]}")
    return fehler


def pruefe_geokodierung() -> int:
    """Der beste Treffer entscheidet — kein Weitersuchen nach einem Nahtreffer.

    Geprüft ohne Netz: geokodiere() bekommt eine nachgebaute Antwort untergeschoben.
    """
    import json as _json, urllib.request as _ur
    import sichtungen as si

    antworten = {
        "Neuenrade": [  # Sauerland zuerst, gleichnamiger Nebentreffer im Norden dahinter
            {"type": "PLACE", "country": "DE", "name": "Neuenrade", "lat": 51.28, "lon": 7.78,
             "areas": [{"adminLevel": 4, "name": "Nordrhein-Westfalen"}]},
            {"type": "STOP", "country": "DE", "name": "Neuenrade", "lat": 54.08, "lon": 10.12,
             "areas": [{"adminLevel": 4, "name": "Schleswig-Holstein"}]}],
        "Glückstadt": [
            {"type": "PLACE", "country": "DE", "name": "Glückstadt", "lat": 53.79, "lon": 9.42,
             "areas": [{"adminLevel": 4, "name": "Schleswig-Holstein"}]}],
    }

    class Antwort:
        def __init__(self, daten): self.daten = daten
        def read(self): return _json.dumps(self.daten).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    original = _ur.urlopen
    _ur.urlopen = lambda req, timeout=0: Antwort(
        antworten[req.full_url.split("text=")[1].replace("%C3%BC", "ü")])
    fehler = 0
    try:
        for name, soll_nah in (("Neuenrade", False), ("Glückstadt", True)):
            t = si.geokodiere(name)
            km = si.entfernung_km(si.ELMSHORN, (t["lat"], t["lon"]))
            ok = (km <= 50) == soll_nah
            fehler += not ok
            print(f"  {'OK    ' if ok else 'FEHLER'}  {name:<11} -> {km:4.0f} km "
                  f"({'im Umkreis' if soll_nah else 'weit weg, trotz Namensvetter im Norden'})")
    finally:
        _ur.urlopen = original
    return fehler


def pruefe_beobachtung() -> int:
    """Was an einer vorgemerkten Fahrt als Änderung gemeldet wird."""
    from beobachten import zustand, aenderungen
    from sonderzuege import ist_sonderzug, REGEL_GATTUNGEN
    i = {"lok": "218 453-9", "zug": "RE 11052"}
    plan = [{"lok": "218 453-9", "zug": "RE 11052", "von_zeit": "07:00", "nach_zeit": "08:04"}]
    zeit = [{"lok": "218 453-9", "zug": "RE 11052", "von_zeit": "07:10", "nach_zeit": "08:14"}]
    wechsel = [{"lok": "245 205-0", "zug": "RE 11052", "von_zeit": "07:00", "nach_zeit": "08:04"}]
    ok_rt = {"verspaetung": 0, "ausgefallen": False, "gleis": "2", "gleis_plan": "2", "ursachen": []}
    spaet = {"verspaetung": 14, "ausgefallen": False, "gleis": "2", "gleis_plan": "2", "ursachen": []}
    faelle = [
        ("erster Blick, alles normal: still", None, zustand(i, plan, ok_rt), []),
        ("erster Blick, schon gestrichen", None, zustand(i, [], None), ["gestrichen"]),
        ("Zeit geändert", zustand(i, plan, None), zustand(i, zeit, None), ["zeit:07:00-08:04>07:10-08:14"]),
        ("andere Lok übernimmt", zustand(i, plan, None), zustand(i, wechsel, None), ["lokwechsel:245 205-0"]),
        ("Verspätung über Schwelle", zustand(i, plan, ok_rt), zustand(i, plan, spaet), ["verspaetung:14"]),
        ("gleiche Verspätung nicht nochmal", zustand(i, plan, spaet), zustand(i, plan, spaet), []),
        ("Forum nicht erreichbar ist nicht gestrichen", zustand(i, plan, None), zustand(i, None, None), []),
    ]
    fehler = 0
    for name, alt, neu_, soll in faelle:
        ok = aenderungen(alt, neu_, 10) == soll
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    for e, soll, name in (({"kategorie": "RE", "linie": "RE6"}, False, "RE6 ist Regelverkehr"),
                          ({"kategorie": "D", "linie": ""}, True, "D ohne Linie ist Sonderzug"),
                          ({"kategorie": "RE", "linie": ""}, True, "RE ohne Linie ist Sonderzug")):
        ok = ist_sonderzug(e, REGEL_GATTUNGEN) == soll
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_push() -> int:
    """Verschlüsselung gegen das Beispiel aus RFC 8291 Anhang A — ein Fehler dort
    fiele sonst nie auf: der Push-Dienst nimmt die Nachricht an, das Handy verwirft sie."""
    import re
    import push
    from cryptography.hazmat.primitives.asymmetric import ec
    d = int.from_bytes(push.b64u_dekodieren("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big")
    fest = ec.derive_private_key(d, ec.SECP256R1())
    alt_schluessel, alt_zufall = push.ec.generate_private_key, push.os.urandom
    push.ec.generate_private_key = lambda kurve: fest
    push.os.urandom = lambda n: push.b64u_dekodieren("DGv6ra1nlYgDCS1FRnbzlw")
    try:
        aus = push.b64u(push.verschluesseln(
            b"When I grow up, I want to be a watermelon",
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
            "BTBZMqHH6r4Tts7J_aSIgg"))
    finally:
        push.ec.generate_private_key, push.os.urandom = alt_schluessel, alt_zufall
    ok = aus == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vC"
                 "YLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXL"
                 "WyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
    print(f"  {'OK    ' if ok else 'FEHLER'}  Web-Push-Verschlüsselung = RFC 8291")
    return 0 if ok else 1


def pruefe_aktion_token() -> int:
    """Signierte Aufträge für die Knöpfe in der Benachrichtigung: nur echte gelten."""
    import base64
    import datetime as dt
    import marschbahn as mb
    cfg = {"merken": {"geheimnis": "probe"}}
    token = mb.aktion_token(cfg, "entfernen", dt.date(2026, 9, 17), "218 453-9", "RE 11026")
    nutz, sig = token.split(".")
    inhalt = base64.urlsafe_b64decode(nutz + "=" * (-len(nutz) % 4)).decode()
    faelle = [
        ("Inhalt vollständig", inhalt == "entfernen|2026-09-17|218 453-9|RE 11026"),
        ("Signatur vorhanden", len(sig) > 20),
        ("anderes Geheimnis ergibt andere Signatur",
         mb.aktion_token({"merken": {"geheimnis": "anders"}}, "entfernen",
                         dt.date(2026, 9, 17), "218 453-9", "RE 11026") != token),
        ("andere Fahrt ergibt andere Signatur",
         mb.aktion_token(cfg, "entfernen", dt.date(2026, 9, 18), "218 453-9", "RE 11026") != token),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_meldewege() -> int:
    """Mail nur für 218 durch Elmshorn; alles andere per Push, ohne Gerät ersatzweise per Mail."""
    import marschbahn as mb
    import re
    import push
    cfg = {"push": {}}
    wege: list[str] = []
    alt_mail, alt_push = mb.sende_mail, push.an_alle
    geraete = {"n": 0}
    mb.sende_mail = lambda c, b, h, t: (wege.append("mail"), 1)[1]
    push.an_alle = lambda conn, c, ti, te, u=None, m=None, o=None, a=None, art=None: (
        wege.append("push"), (geraete["n"], geraete["n"]))[1]
    alt_connect = mb.db_connect
    # Verbindung nur als Attrappe: melde() schreibt darüber auch den Verlauf.
    class Zeiger:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): return 0
    class Verbindung:
        def cursor(self): return Zeiger()
        def commit(self): pass
        def close(self): pass
    mb.db_connect = lambda c: Verbindung()
    fehler = 0
    try:
        for art, n, erwartet in [("treffer", 1, ["push", "mail"]), ("streichung", 1, ["push", "mail"]),
                                 ("sichtung", 1, ["push"]), ("stoerung", 1, ["push"]),
                                 ("tagesmeldung_leer", 1, ["push"]), ("sichtung", 0, ["push", "mail"])]:
            wege.clear()
            geraete["n"] = n
            mb.melde(cfg, art, "B", "<p>", "B\nText")
            ok = wege == erwartet
            fehler += not ok
            print(f"  {'OK    ' if ok else 'FEHLER'}  {art} mit {n} Gerät(en): {' + '.join(wege)}")
    finally:
        mb.sende_mail, push.an_alle, mb.db_connect = alt_mail, alt_push, alt_connect
    return fehler


def pruefe_erinnerung() -> int:
    """Erinnerungstext: Ausfall, Verspätung, Gleiswechsel und Wagenreihung."""
    import datetime as dt
    import erinnerung as er
    import wagenreihung as wr
    fehler = 0
    lauf = {"tag": dt.date(2026, 9, 17), "lok": "218 453-9", "zug": "RE 11026",
            "elmshorn_zeit": "17:58", "gleis": "1", "von_halt": "Hamburg-Altona",
            "nach_halt": "Westerland(Sylt)", "richtung": "Richtung Sylt"}
    faelle = [
        ("pünktlich", None, "In 30 min", "Gleis 1"),
        ("Verspätung", {"gleis": "3", "verspaetung": 12, "ausgefallen": False, "ursachen": ["Defekte Tür"],
                        "ist_hhmm": "18:10", "gleis_geaendert": True}, "Gleis 3", "+12 min"),
        ("Ausfall", {"gleis": "1", "verspaetung": 0, "ausgefallen": True, "ursachen": [],
                     "ist_hhmm": None, "gleis_geaendert": False}, "Fällt aus", "ausgefallen"),
    ]
    for name, rt, im_betreff, im_text in faelle:
        betreff, _, _, push_text = er.baue(lauf, rt, "218 453-9, vorne, Abschnitt F", 30)
        ok = im_betreff in betreff and im_text in (betreff + " " + push_text)
        # Bei einem Ausfall ist die Wagenreihung uninteressant.
        if name == "Ausfall":
            ok = ok and "Am Bahnsteig" not in push_text
        else:
            ok = ok and "vorne, Abschnitt F" in push_text
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  Erinnerung: {name}")

    beispiel = {"abschnitte": [{"name": "A", "von": 50.0, "bis": 100.0},
                               {"name": "B", "von": 0.0, "bis": 50.0}],
                "wagen": [{"bezeichnung": "218 453-9", "lok": True, "von": 5.0, "bis": 20.0, "pfeil": "hoch"},
                          {"bezeichnung": "DBpza", "lok": False, "von": 20.0, "bis": 40.0, "pfeil": "hoch"}]}
    kurz = wr.kurzfassung(beispiel)
    ok = kurz == "218 453-9, vorne, Abschnitt B"
    fehler += not ok
    print(f"  {'OK    ' if ok else 'FEHLER'}  Wagenreihung kurz: {kurz}")
    return fehler


def pruefe_verspaetung() -> int:
    """Eine verspätete Fahrt bleibt im Blick, auch wenn die Planankunft vorbei ist."""
    import datetime as dt
    import json as jsonmod
    from zoneinfo import ZoneInfo
    import beobachten as bo
    tz = ZoneInfo("Europe/Berlin")
    jetzt = dt.datetime.now(tz)
    def takt(an_minuten: int, spaet: int):
        an = jetzt + dt.timedelta(minutes=an_minuten)
        return bo.takt_fuer({"tag": jetzt.date(), "von_halt": "A",
                             "von_zeit": (jetzt - dt.timedelta(hours=2)).strftime("%H:%M"),
                             "nach_halt": "B", "nach_zeit": an.strftime("%H:%M"),
                             "stand": jsonmod.dumps({"verspaetung": spaet})}, jetzt)[0]
    faelle = [
        ("pünktlich, 40 min nach Ankunft: fertig", takt(-40, 0) is None),
        ("+60 min, 40 min nach Planankunft: weiter", takt(-40, 60) == 5),
        ("+5 min, 40 min nach Planankunft: fertig", takt(-5 - 40, 5) is None),
        ("Ankunft in 30 min: alle 5 Minuten", takt(30, 0) == 5),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_haltbarkeit() -> int:
    """Push: kurze Haltbarkeit je Art, eindeutige Themen (sonst ersetzen sich zwei Fahrten)."""
    import re
    import push
    lang_a = "erinnerung-2026-09-25-218 497-6-RE 11005"
    lang_b = "erinnerung-2026-09-25-218 443-0-RE 11052"
    themen = [push.thema_aus(lang_a, "erinnerung"), push.thema_aus(lang_b, "erinnerung"),
              push.thema_aus("fahrt-12", "beobachtung"), push.thema_aus(None, "stoerung")]
    faelle = [
        ("Erinnerung verfällt nach 20 min", push.TTL_JE_ART["erinnerung"] == 20 * 60),
        ("Standard-Haltbarkeit 3 h", push.TTL_SEKUNDEN == 3 * 3600),
        ("Themen höchstens 32 Zeichen", all(len(t) <= 32 for t in themen)),
        ("Themen URL-sicher", all(re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in themen)),
        ("zwei Fahrten, zwei Themen", themen[0] != themen[1]),
        ("gleiche Markierung, gleiches Thema", push.thema_aus(lang_a, "erinnerung") == themen[0]),
        ("ohne Markierung die Art", themen[3] == "stoerung"),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_lok_check() -> int:
    """Lok-Check: Wagenreihung gegen die geplante Lok — bestätigt, andere 218, keine 218."""
    import datetime as dt
    import erinnerung as er
    def wagen(uic_lok: str, typ: str = "V2180"):
        return {"wagen": [{"typ": typ, "uic": uic_lok, "bezeichnung": "", "lok": True, "pfeil": "hoch"}]
                + [{"typ": "Bpmdza", "uic": "558022750027", "lok": False, "pfeil": "hoch"}] * 5}
    plan = "218 453-9"
    a = er.lok_check(plan, wagen("928012184539"), "RE")          # 218 453-9
    b = er.lok_check(plan, wagen("928012183903"), "RE")          # 218 390-3
    c = er.lok_check(plan, wagen("928012452134", "VX022"), "RE") # 245 213-4
    d = er.lok_check(plan, {"wagen": [{"typ": "DBpza", "uic": "508026813735", "lok": False}]}, "RE")
    lauf = {"tag": dt.date(2026, 9, 18), "lok": plan, "zug": "RE 11029", "elmshorn_zeit": "18:00",
            "von_halt": "Westerland(Sylt)", "nach_halt": "Hamburg-Altona", "richtung": "Richtung Hamburg"}
    betreff = er.baue(lauf, None, None, 30, c)[0]
    faelle = [
        ("gleiche Lok: bestätigt", a and a["ergebnis"] == "bestaetigt"),
        ("218 390 statt 453: andere 218", b and b["ergebnis"] == "andere_218" and "218 390-3" in b["text"]),
        ("245 statt 218: keine 218", c and c["ergebnis"] == "keine_218" and "245 213-4" in c["text"]),
        ("ohne Lok in der Reihung: keine Aussage", d is None),
        (f"Titel bei Lok-Wechsel: {betreff}", "Lok-Wechsel" in betreff and "245" in betreff),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_archiv() -> int:
    """Beobachtete Fahrten gehen eine Stunde nach der (verspäteten) Ankunft ins Archiv."""
    import datetime as dt
    import json as jsonmod
    from zoneinfo import ZoneInfo
    import beobachten as bo
    tz = ZoneInfo("Europe/Berlin")
    jetzt = dt.datetime(2026, 9, 18, 20, 0, tzinfo=tz)
    def fahrt(ab: str, an: str, spaet: int = 0, ausfall: bool = False, tag=dt.date(2026, 9, 18)):
        return {"tag": tag, "von_zeit": ab, "nach_zeit": an,
                "stand": jsonmod.dumps({"verspaetung": spaet, "ausfall": ausfall})}
    faelle = [
        ("an 18:30, jetzt 20:00: ins Archiv", bo.archiv_faellig(fahrt("15:30", "18:30"), jetzt)),
        ("an 19:10, jetzt 20:00: bleibt oben", not bo.archiv_faellig(fahrt("16:10", "19:10"), jetzt)),
        ("an 18:30 +45 min: bleibt oben", not bo.archiv_faellig(fahrt("15:30", "18:30", 45), jetzt)),
        ("Ausfall, geplant an 18:30: ins Archiv", bo.archiv_faellig(fahrt("15:30", "18:30", 45, True), jetzt)),
        ("über Mitternacht (23:10–01:20): bleibt oben", not bo.archiv_faellig(fahrt("23:10", "01:20"), jetzt)),
        ("gekürzter Stand (kein JSON): kein Absturz", bo.archiv_faellig({**fahrt("15:30", "18:30"), "stand": '{"plan": "15:'}, jetzt)),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def pruefe_wochenvorschau() -> int:
    """Sonntags nur, was wirklich ansteht: ein Datum in der kommenden Woche muss dabeistehen."""
    import datetime as dt
    import wochenvorschau as wv
    ab = dt.date(2026, 9, 20)
    bis = ab + dt.timedelta(days=7)
    faelle = [
        ("Sonderzug am 24.09.2026 nach Sylt", True),
        ("Ankündigung: Sonderfahrt 25.9. Hamburg–Westerland", True),
        ("IGE Dampfzug nach Hamburg 12.09.2026", False),
        ("Frage zu Dampf durch den Hamburger Hafen", False),
        ("Termin steht: 1.10. Abschiedsfahrt", False),
        ("Unsinnsdatum 32.13.2026", False),
    ]
    fehler = 0
    for text, erwartet in faelle:
        ok = (wv.termin_in_woche(text, ab, bis) is not None) == erwartet
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {'gilt  ' if erwartet else 'ignor.'} {text[:44]}")
    return fehler


def pruefe_schluessel() -> int:
    """Eine Fahrt bleibt dieselbe, wenn im Forum nachträglich ein Zwischenhalt dazukommt."""
    import datetime as dt
    import marschbahn as mb
    tag = dt.date(2026, 9, 18)
    vorher = {"lok": "218 453-9", "zug": "RE 11029",
              "zeile": "RE 11029, Westerland(Sylt) (15:19) - Hamburg-Altona (18:22)"}
    nachher = {"lok": "218 453-9", "zug": "RE 11029",
               "zeile": "RE 11029, Westerland(Sylt) (15:19) - Husum (16:29/16:31) - Hamburg-Altona (18:22)"}
    andere_lok = dict(nachher, lok="218 330-9")
    anderer_zug = dict(nachher, zug="RE 11030")
    faelle = [
        ("Zwischenhalt ergänzt: gleiche Fahrt", mb.match_key(tag, "regio", vorher) == mb.match_key(tag, "regio", nachher)),
        ("Leerzeichen egal", mb.match_key(tag, "regio", vorher) == mb.match_key(tag, "regio", dict(vorher, zug="RE  11029"))),
        ("andere Lok (Doppeltraktion): andere Fahrt", mb.match_key(tag, "regio", nachher) != mb.match_key(tag, "regio", andere_lok)),
        ("anderer Zug: andere Fahrt", mb.match_key(tag, "regio", nachher) != mb.match_key(tag, "regio", anderer_zug)),
        ("anderer Tag: andere Fahrt", mb.match_key(tag, "regio", nachher) != mb.match_key(dt.date(2026, 9, 19), "regio", nachher)),
    ]
    fehler = 0
    for name, ok in faelle:
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {name}")
    return fehler


def main() -> int:
    e = analysiere_beitrag(PROBE)
    ist = {l["zug"]: l["elmshorn"] for l in e["laeufe"]}
    loks = {l["lok"] for l in e["laeufe"]}
    fehler = 0

    for zug, soll in ERWARTET.items():
        ok = ist.get(zug) == soll
        fehler += not ok
        print(f"  {'OK    ' if ok else 'FEHLER'}  {zug:<10} erwartet={soll!s:<5} bekommen={ist.get(zug)}")

    for name, bed in [
        ("nur 218er erfasst, keine 245",             loks == {"218 330-9"}),
        ("Legendenzeile erzeugt keinen Lauf",        "RE6" not in ist),
        ("'Verkehrt als 2. Wageneinheit' ignoriert", "RE 11048" not in ist),
        ("Fahrzeugliste erzeugt keine Laeufe",       not {"218 443-0", "218 453-9"} & loks),
        ("keine unbekannten Halte",                  not e["unbekannte_halte"]),
    ]:
        fehler += not bed
        print(f"  {'OK    ' if bed else 'FEHLER'}  {name}")

    print("  --- SyltShuttle / ICE ---")
    fehler += pruefe_shuttle()

    print("  --- Durchfahrtszeit Elmshorn ---")
    fehler += pruefe_zeiten()

    print("  --- Eigennamen der Loks ---")
    fehler += pruefe_namen()

    print("  --- Ortsauflösung im Umkreis ---")
    fehler += pruefe_geokodierung()

    print("  --- Beobachtete Fahrten und Sonderzüge ---")
    fehler += pruefe_beobachtung()

    print("  --- Push-Benachrichtigungen ---")
    fehler += pruefe_push()
    fehler += pruefe_meldewege()
    fehler += pruefe_aktion_token()

    print("  --- Verspätung: weiter beobachten ---")
    fehler += pruefe_verspaetung()
    print("  --- Haltbarkeit der Push-Nachrichten ---")
    fehler += pruefe_haltbarkeit()
    print("  --- Lok-Check vor der Durchfahrt ---")
    fehler += pruefe_lok_check()
    print("  --- Archiv beobachteter Fahrten ---")
    fehler += pruefe_archiv()

    print("  --- Wiedererkennung einer Fahrt ---")
    fehler += pruefe_schluessel()

    print("  --- Sonntagsnachricht zu Sonderzügen ---")
    fehler += pruefe_wochenvorschau()

    print("  --- Erinnerung vor der Durchfahrt ---")
    fehler += pruefe_erinnerung()

    print("\n=>", "ALLE TESTS BESTANDEN" if fehler == 0 else f"{fehler} FEHLER")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())

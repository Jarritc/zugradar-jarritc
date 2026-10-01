# Zugradar

Eine kleine, selbst betriebene Anwendung rund um die **Marschbahn** (Hamburg – Westerland/Sylt)
und die Einsätze der **Baureihe 218 durch Elmshorn**.

Zugradar liest die öffentlich einsehbaren Tageslisten im Forum von Drehscheibe-Online, verbindet
sie mit Fahrplan- und Echtzeitdaten, meldet per Push-Nachricht, wenn eine 218 durch Elmshorn
kommt, sich etwas ändert oder ein Zug ausfällt — und zeigt auf einer Karte, wo die Züge gerade
fahren.

Das Ganze ist ein **privates Hobbyprojekt für einen Haushalt**, kein Produkt. Es läuft so, wie
es hier steht, auf einem kleinen Server. Wer es nachbauen will: gern, aber es ist auf diesen
einen Zweck zugeschnitten.

## Aufbau

| Teil | Was | Wo |
|---|---|---|
| **Backend** | Python 3.13, von Cron gestartet, schreibt in MySQL | `backend/` |
| **Web** | PHP 8.4 als Progressive Web App, liest nur die Datenbank | `web/` |

Die Webseite fragt **keine** fremde Schnittstelle selbst an. Braucht sie etwas (Wagenreihung,
Abfahrten eines Bahnhofs, Züge an einer Stelle der Strecke), trägt sie eine Anfrage in eine
Tabelle ein; ein Hintergrunddienst beantwortet sie. So liegen Zugangsdaten nur beim Backend.

Ausführlich beschrieben ist alles in [`backend/README.md`](backend/README.md) — Aufbau,
Datenbanktabellen, Benachrichtigungswege, Karte, und was dabei schiefging und warum es jetzt so
gelöst ist.

## Datenquellen

* [transitous.org](https://transitous.org) — Fahrplan und Echtzeit für die Karte
  ([deren Quellen](https://transitous.org/sources/)). Abgefragt wird nur, während jemand die
  Karte geöffnet hat, höchstens alle drei Minuten, gzip-komprimiert, mit Kennung und Kontakt.
* Deutsche Bahn, Timetables-Schnittstelle — Abfahrten und Echtzeit in Elmshorn.
* [Drehscheibe-Online](https://www.drehscheibe-online.de/foren/list.php?006) — die Tageslisten.
* [dbf.finalrewind.org](https://dbf.finalrewind.org/) — Wagenreihung, nur auf Knopfdruck.
* [OpenStreetMap](https://www.openstreetmap.org/copyright) und
  [OpenRailwayMap](https://www.openrailwaymap.org/) — Karte, Gleise, Signale.
* Landesgrenzen: GeoBasis-DE / BKG (dl-de/by-2-0) über
  [deutschlandGeoJSON](https://github.com/isellsoap/deutschlandGeoJSON).
* Wikipedia, Artikel „DB-Baureihe 218“ — Angaben zu einzelnen Loks.

## Einrichten

1. MySQL-Datenbank anlegen.
2. `backend/config.beispiel.json` nach `config.json` kopieren und ausfüllen
   (Datenbank, Mailversand, Zugang zur DB-Schnittstelle). Rechte: `chmod 600`.
3. `web/_kukas.beispiel.php` nach `_kukas.php` kopieren und ausfüllen
   (Datenbank, Passwort-Hashes, Geheimnis für „angemeldet bleiben“).
4. `python3 backend/push.py --schluessel` erzeugt die VAPID-Schlüssel für Push-Nachrichten.
5. Cron einrichten — die Takte stehen in `backend/README.md`.
6. `python3 backend/test_parser.py` prüft Parser, Meldewege und Rechnerei ohne Netzzugriff.

## Was nicht im Repository liegt

`config.json`, `_kukas.php`, Protokolle und die Datenbank selbst — also alles mit Zugangsdaten
oder persönlichen Daten.

## Lizenz

MIT, siehe [LICENSE](LICENSE).

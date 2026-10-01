# Marschbahn-Wächter — BR 218 durch Elmshorn

Prüft stündlich das DSO-Forum 006 auf BR-218-Umläufe, die Elmshorn berühren,
schickt Mails und pflegt die Übersicht unter <https://jarritc.de/zugradar/>
(passwortgeschützt, siehe `PW_HASHES` in `_kukas.php`).

## Zwei Threadreihen

| Quelle | Titel | Strecke |
|---|---|---|
| `regio` | „Lokeinsätze auf der Marschbahn [DB Regio], …" | Westerland – Hamburg-Altona |
| `shuttle` | „Lokübersicht der SyltShuttle und ICE's …" | Sylt Shuttle Niebüll–Westerland, ICE-Talgo bis Itzehoe |

Die `shuttle`-Reihe liefert praktisch nie einen Treffer: der Autozug pendelt nur
Niebüll–Westerland, und die ICE-Talgos enden in Itzehoe, wo eine 193 elektrisch
übernimmt — alles nördlich von Elmshorn. Sie wird trotzdem ausgewertet, damit
Leerfahrten und Überführungen Richtung Hamburg nicht durchrutschen.

Ihr Format weicht ab und wird gesondert behandelt: Zugart mit Schrägstrich
(`AS 1401/ …`), Doppeltraktionen über zwei Zeilen (erste Kopfzeile endet auf `+`,
beide Loks bekommen denselben Umlauf), ein Abschnitt „Weitere Loks (Nicht im
Einsatz)" am Ende, und ein Intercity-Umlauf in Thüringen (Gera–Gotha), der
komplett außerhalb der Marschbahn liegt und ignoriert wird.

## Dateien

| Pfad | Zweck |
|---|---|
| `marschbahn.py` | Scraper, Erkennung, Datenbank, Mailversand |
| `test_parser.py` | Regressionstest der Elmshorn-Erkennung |
| `fahrplan.py` | Soll- und Echtzeitdaten für Elmshorn (transitous) |
| `stoerung.py` | Verspätungs- und Störungswächter, alle 10 Minuten |
| `sichtungen.py` | Forenbeiträge aus dem 50-km-Umkreis, alle 10 Minuten |
| `db_zugang.py` | trägt die Zugangsdaten der DB-Timetables-API ein (interaktiv) |
| `icons_erzeugen.py` | Favicon, App-Icons und Mail-Logo aus einem Bild |
| `besondere_loks.py` | besondere 218er aus Wikipedia einlesen (wöchentlich) |
| `beobachten.py` | vorgemerkte Fahrten alle 10 Minuten nachprüfen |
| `sonderzuege.py` | Sonderzüge durch Elmshorn laut Fahrplan, alle 3 Stunden |
| `push.py` | Push-Benachrichtigungen an die App: Verschlüsselung, Versand, Warteschlange (jede Minute) |
| `erinnerung.py` | Erinnerung 30 min vor der Durchfahrt, mit Echtzeit und Wagenreihung (alle 5 min) |
| `wacht.py` | Selbstüberwachung: meldet stille Ausfälle (stündlich) |
| `wagenreihung.py` | Wagenreihung einer Abfahrt holen und zerlegen (Warteschlange, jede Minute) |
| `ursachen.json` | Verspätungsursachen: Code → Klartext (99 Einträge) |
| `config.json` | DB-Zugang, Mail-API, Empfänger (chmod 600) |
| `logs/run.log` | Laufprotokoll |
| `/var/www/html/jarritc.de/zugradar/index.php` | Übersichtsseite (passwortgeschützt) |

## Aufruf

    python3 marschbahn.py            # ein Durchlauf (so läuft der Cron)
    python3 marschbahn.py --dry-run  # ohne Mailversand, hakt auch nichts ab
    python3 marschbahn.py --verbose  # Log zusätzlich auf stdout
    python3 marschbahn.py --setup    # Tabellen anlegen
    python3 marschbahn.py --aufraeumen  # alte Zeilen löschen, Logs drehen
    python3 test_parser.py           # Erkennung testen

Cron: `17 * * * *`, Log zusätzlich in `/tmp/marschbahn-cron.log`.

## Zwei Fahrplanquellen

| Quelle | Modul | wofür |
|---|---|---|
| **DB Timetables** | `db_timetables.py` | Echtzeitlage, Störungsmeldungen, Ursachen, Nachschlag einzelner Züge |
| **transitous** | `fahrplan.py` | Soll-Zeiten für ganze Tage, Rückfall für die Echtzeit |
| Schätzung | `marschbahn.FAHRZEIT_PROFIL` | letzter Rückfall, wenn beide schweigen |

Die Aufteilung folgt den Kosten je Abruf: Die DB liefert den Soll-Fahrplan
**stundenweise** — ein ganzer Tag wären 24 Abrufe, transitous schafft ihn in
vier. Umgekehrt kennzeichnet nur die DB Ausfälle ausdrücklich (`cs="c"`) und
liefert die Störungsmeldungen der Bahn (`<m cat="Störung">`), womit eine
Sperrung nicht mehr aus „mehrere Züge betroffen" erschlossen werden muss.

**Fallstrick bei der DB-Schnittstelle:** `fchg` (die Änderungen) enthält *keine*
Zugnummer, nur die Fahrt-`id`. Die Verbindung zum Soll-Fahrplan läuft
ausschließlich über diese id. Wer das übersieht, bekommt Änderungen, ohne zu
wissen, zu welchem Zug sie gehören.

Die EVA-Nummer für Elmshorn ist **8000092** — `db_zugang.py` schlägt sie über die
API nach, statt sie zu raten (die naheliegende 8001307 wäre falsch gewesen).

## Fahrplanquelle: transitous

`fahrplan.py` holt Soll- und Echtzeitdaten für den Halt Elmshorn von
**api.transitous.org** — einem offenen MOTIS-Dienst auf Basis der DELFI-Open-Data.
Kostenlos, ohne Schlüssel, mit Echtzeit aus GTFS-RT.

Die Alternativen wurden geprüft und verworfen: `transport.rest` antwortet
dauerhaft mit 503, `bahn.de` mit `403 OPS_BLOCKED` (aktive Bot-Sperre, wird
bewusst nicht umgangen), und die APIs im DB API Marketplace kosten Geld
(`db_zugang.py` liegt für diesen Fall bereit, wird aber nicht gebraucht).

Zuordnung über die Zugnummer: transitous liefert `RE6 (11029)`, das Forum
`RE 11029`. Der Halt ist als `de-DELFI_de:01056:97960` in `config.json` hinterlegt
und lässt sich mit `python3 fahrplan.py` prüfen.

**transitous ist ein Freiwilligenprojekt ohne Zusage.** Jeder Aufruf ist deshalb
abgesichert: Fällt der Dienst aus, greift die Schätzung aus `FAHRZEIT_PROFIL`,
und der Störungswächter schweigt, statt zu scheitern. Es fällt also nie eine
Meldung aus — sie wird höchstens ungenauer.

## Wie „durch Elmshorn" erkannt wird

Elmshorn wird in den Beiträgen **nie genannt**. In `STATIONEN` steht deshalb für
jeden Halt der echte Streckenkilometer ab Hamburg-Altona (Quelle: Streckenbänder
der Wikipedia-Artikel zu den beiden Strecken). Elmshorn liegt bei **km 30,697**.
Ein Umlauf zählt, wenn ein Endpunkt südlich und einer nördlich davon liegt.

## Durchfahrtszeit in Elmshorn

Vorrangig kommt die **Soll-Abfahrt aus dem Fahrplan**, über die Zugnummer
zugeordnet. Nur wenn dort kein passender Zug steht, wird geschätzt — dann steht
„geschätzt" an der Zeit (`laeufe.zeit_quelle`).

### Die Schätzung als Rückfall

Die Forumsquelle nennt nur Start und Ziel. `FAHRZEIT_PROFIL` bildet die Fahrzeit des RE6
über die Strecke ab (km → Minuten ab Hamburg-Altona); zwischen den beiden Halten,
die Elmshorn einschließen, wird darin interpoliert — nicht linear in Kilometern,
sonst färbte der langsame Hindenburgdamm auf die schnelle Geestrecke ab.
Anschließend wird auf die tatsächliche Dauer des konkreten Laufs skaliert.

Die Stützstellen stammen aus **833 Zeitangaben der Beiträge selbst** (Median je
Halt), nicht aus einem Fahrplan — es gibt also keine Abhängigkeit von einer
externen API. Elmshorn selbst kommt in den Daten nie als Halt vor und wird
zwischen Pinneberg (11,5 min) und Itzehoe (48,5 min) eingeordnet: rund 23 Minuten
ab Hamburg-Altona.

**Gemessene Genauigkeit** an 41 Fahrten mit bekannter Zwischenhaltzeit: mittlerer
Fehler 2,7 min, Median 3,0, 90 % unter 4,8, größter 8,5, Versatz −0,2 min. Das ist
der ungünstigste Fall (Husum, weit von beiden Endpunkten); Elmshorn liegt mit 23
von 183 Minuten viel dichter am Endpunkt, dort ist der Hebel kürzer. Die Zeit wird
überall als „ca." ausgewiesen.

Gegen den echten Fahrplan geprüft (37 Züge des 07.09.): mittlerer Fehler 3,2 min,
**systematischer Versatz −3,1 min** — die Schätzung liegt durchgängig etwa drei
Minuten zu früh. Wer sie neu kalibriert, sollte dort ansetzen; `FAHRZEIT_PROFIL`
anpassen genügt. Der Regressionstest prüft Abstände mit Toleranz, nicht feste
Uhrzeiten, und bleibt dabei gültig.

## Das Gleis in Elmshorn

Kommt ausschließlich aus der DB-Schnittstelle: `pp` im Soll-Fahrplan ist das
geplante Gleis, `cp` in den Änderungen ein Gleiswechsel. transitous hat die
Felder `track`/`scheduledTrack` zwar, füllt sie für Elmshorn aber nicht.

Nachgeschlagen wird **einmal je Umlauf** — das Ergebnis landet in `laeufe.gleis`
und wird bei späteren Durchläufen nicht neu geholt (`COALESCE` beim Schreiben,
damit ein späterer leerer Nachschlag den Wert nicht überbügelt). Ein Nachschlag
kostet drei Abrufe, deshalb zusätzlich das Budget `nachschlag_je_lauf`
(Standard 12) je Durchlauf.

Ein **Gleiswechsel** am Tag selbst meldet `stoerung.py` gesondert — er entscheidet,
auf welchem Bahnsteig man steht.

## Aufräumen

Läuft **einmal am Tag von selbst** am Ende eines erfolgreichen Durchlaufs
(vermerkt in `meta.aufgeraeumt_am`), kein eigener Cron nötig. Von Hand:
`python3 marschbahn.py --aufraeumen`.

| Was | bleibt |
|---|---|
| `laeufe_log` | 30 Tage |
| `stoerungen` | 90 Tage |
| `sichtungen` ohne Treffer | 30 Tage |
| `sichtungen` mit Treffer | 365 Tage |
| `orte` ohne Treffer | 90 Tage, danach neuer Versuch |
| `orte` mit Treffer | dauerhaft (reiner Zwischenspeicher) |
| `tage`, `laeufe`, `tagesmeldung` | 365 Tage |

Einstellbar unter `aufbewahrung` in `config.json`. `laeufe` hängt per
Fremdschlüssel an `tage` und verschwindet mit; die Historie, die die Webseite
zeigt, wird also bewusst großzügig behalten, während reine Protokolle früh
rausfliegen.

**Logdateien** werden bei Überschreiten von `log_max_mb` (Standard 5) einmal nach
`.1` weggelegt und neu begonnen — kein logrotate-Eintrag nötig, der Platzbedarf
bleibt bei höchstens dem Doppelten je Datei. Erfasst sind auch die
Cron-Ausgaben unter `/tmp/marschbahn-*.log`.

## Verspätungen und Störungen

`stoerung.py` läuft alle 10 Minuten, in zwei Stufen.

**Stufe 1 — die heutige 218**, sofern eine fährt und im Fenster
`vorlauf_minuten` vor bis `nachlauf_minuten` nach der Durchfahrt liegt
(Standard 120/30; eine Meldung sechs Stunden vorher nützt nichts):

* **Verspätung** ab `verspaetung_ab_minuten` (Standard 15). Erneut erst, wenn sie
  um weitere 10 Minuten gestiegen ist — sonst käme alle 10 Minuten Post.
* **Ausfall** sofort.

**Stufe 2 — die allgemeine Lage**, unabhängig davon, ob heute eine 218 fährt
(`allgemein_aktiv`):

* **Störungsmeldung der Bahn**, sobald die DB-Schnittstelle eine Meldung mit
  einer anderen Kategorie als „Information" für Elmshorn führt und diese gerade
  gültig ist. Das ist die belastbarste Sperrungsanzeige.
* **Störungsverdacht** als Rückfall, wenn mindestens `stoerung_ab_zuegen`
  (Standard 3) Züge in Elmshorn ausfallen oder stark verspätet sind — greift auch
  dann, wenn nur transitous erreichbar ist.
* **Einzelne schwere Verspätungen** auf den Linien in `allgemein_linien`
  (Standard `RE6`, die Marschbahn) ab `allgemein_verspaetung_ab` (Standard 30 min),
  Wiederholung erst bei weiteren 15 Minuten.
* **Entwarnung**, sobald eine gemeldete Streckenstörung vorbei ist
  (`entwarnung`) — eine Störungsmeldung ohne Auflösung ist nur halb nützlich.

### Woran es liegt

Die Störungsmeldung der Bahn (`<m t="h" cat="Störung"/>`) enthält **keinen
Freitext** — nur Kategorie, Priorität und Gültigkeit. Der Grund steckt woanders:
in den Verspätungsursachen der einzelnen Fahrten (`<m t="d" c="48"/>`), und zwar
als nackte Zahl. Die Auflösung liefert die API nicht mit.

`ursachen.json` enthält deshalb 99 Zuordnungen (Code → Text), übernommen aus
`Travel::Status::DE::IRIS` von Daniel Friesel, das dieselben Daten auswertet;
Herkunft und Datum stehen in der Datei. Fehlt sie, wird der nackte Code
ausgewiesen statt zu raten.

In den Mails steht die Ursache dadurch bei jeder einzelnen Verspätung
(„— Ursache: Technische Störung am Zug"), und die Störungsmeldung fasst die
genannten Ursachen aller Fahrten im Zeitfenster zusammen. Nennt niemand einen
Grund, sagt die Mail das ausdrücklich, statt eine Erklärung zu erfinden.

### Drosselung der allgemeinen Meldungen

Ohne Bremse wird aus einer Streckensperrung eine Mailflut: Am 07.09.2026 gingen
für **einen** Vorfall am Vormittag sieben Meldungen raus — die Streckenstörung
selbst, die Meldung der Bahn dazu, und dann alle 10 Minuten die nächsten
ausgefallenen Züge, die nichts weiter als ihre Folgen waren.

### Was überhaupt eine Meldung wert ist

Vier Schwellen entscheiden das, alle unter `fahrplan` in `config.json`:

| Einstellung | Standard | Wirkung |
|---|---|---|
| `allgemein_nur_an_218_tagen` | `false` | Auf `true` gesetzt kämen Störungen nur an Tagen mit einer 218. Bewusst aus: **große Störungen sollen unabhängig davon kommen.** |
| `einzelmeldungen_linie` | `false` | Einzelne fremde Züge (nicht die 218) lösen **gar keine** Mail aus. Verspätete Züge sind Alltag. |
| `stoerung_ab_zuegen` | 10 | So viele Züge müssen betroffen sein. |
| `stoerung_ab_ausfaellen` | 5 | Und so viele davon müssen **ausfallen** — reine Verspätungen sind keine Sperrung. |
| `meldung_ab_prioritaet` | 1 | Von den Störungsmeldungen der Bahn nur die höchste Stufe. Prio 2 ist Hinweisqualität. |
| `meldung_nur_bei_grosser_stoerung` | `true` | Auch eine Prio-1-Meldung der Bahn geht nur raus, wenn die beiden Schwellen darüber erreicht sind. Sonst würde sie die Zugschwelle umgehen. |

Auf der Webseite steht trotzdem alles, auch das nicht Gemeldete — die Schwellen
regeln nur, was als Mail rausgeht.

Seit dem 15.09.2026 gilt: **mindestens 10 betroffene Züge, davon mindestens 5
Ausfälle.** Das ist bewusst hoch — keine der Streckenstörungen vom 07. bis 15.09.
hätte die Schwelle erreicht, die größte hatte 6 betroffene Züge. Gemeldet werden
also nur noch echte Großlagen.

### Drei Bremsen obendrauf

Zusätzlich, ebenfalls nur für **allgemeine** Meldungen:

1. **Läuft schon eine Streckenstörung** (gemeldet und noch nicht entwarnt), werden
   einzelne Ausfälle und Verspätungen gar nicht mehr gemeldet — sie sind ihre Folgen.
2. **Sperrfrist** `sperrfrist_minuten` (Standard 60) nach jeder allgemeinen Meldung.
3. **Tagesobergrenze** `allgemein_hoechstens_am_tag` (Standard 2). Entwarnungen
   zählen nicht mit, damit auf eine Sperrung immer auch die Auflösung folgen kann.

Zurückgehaltenes wird trotzdem in `stoerungen` vermerkt, aber mit
`verschickt = 0` — sonst käme es nach Ablauf der Sperrfrist verspätet doch noch,
und es zählte fälschlich gegen die Tagesobergrenze. Die Webseite zeigt unter
„Heute verschickt" nur `verschickt = 1`.

**Meldungen zur 218 selbst sind davon ausgenommen** — Verspätung, Ausfall und
Gleiswechsel gehen immer sofort raus. Sie sind selten und der eigentliche Zweck.

Die **Entwarnung** wird nur verschickt, wenn die Störung vorher auch wirklich
rausging; sonst käme eine Entwarnung zu etwas, wovon man nie gehört hat.

Stufe 2 kommt mit **einem** Abruf aus, Stufe 1 holt vier Seiten. Ein Zug, der
schon in Stufe 1 gemeldet wurde, wird in Stufe 2 übersprungen. Dublettenschutz in
der Tabelle `stoerungen`, alles einstellbar unter `fahrplan` in `config.json`.

Der Beitrag ist eine Folge von Blöcken — Lok-Kopfzeile, darunter ihre Umläufe.
Legendenzeilen in eckigen Klammern, Trennstriche und Überschriften auf `:` beenden
einen Block. Ohne das würde die Fahrzeugliste ganz oben Umläufe weiter unten
einsammeln, und die Legendenzeile
`[ Doppelstockwagen mit 218, 245, 246 | RE6 ... Hamburg-Altona ]` würde jeden Tag
einen Fehlalarm auslösen. Zeilen mit „Verkehrt als 2. Wageneinheit" werden
ignoriert: da läuft nur der Wagenpark mit, nicht die Lok.

**Taucht ein unbekannter Halt auf**, landet er in `laeufe_log.unbekannte_halte`
und im Log — dann gehört er in `STATIONEN` ergänzt.

## Wo die Loks stehen

Die Beiträge nennen im Kopfteil, wo die Fahrzeuge stationiert bzw. abgestellt
sind. Überschriften wie `Husum:`, `Hennigsdorf:` oder
`Niebüll (BW/Süd/Terminal):` gelten als Ortsangabe für alle Loks darunter.

Abschnitte, die genauso aussehen, aber keine Orte sind, filtert
`KEIN_STANDORT_RE` heraus — `Married-Pair-Wagen:`, `Refresh:`,
`Doppelstockwagen:`, `Lokumlauf Sylt Shuttle:`, `Intercity …:`. Ohne das stünde
eine Lok plötzlich in „Married-Pair-Wagen".

Gespeichert wird in `standorte` (ein Datensatz je Tag, Quelle und Lok). Vor dem
Schreiben werden die alten Einträge desselben Threads gelöscht, damit eine
umgesetzte Lok nicht an zwei Orten gleichzeitig steht. `hat_umlauf` ist nur für
die 218 gefüllt — nur deren Umläufe wertet der Parser aus.

**Loks im Umlauf nennen keinen Standort**, sie stehen im Beitrag unter ihren
Fahrten. Die Webseite ergänzt sie deshalb aus `tage.loks_218` als eigene Gruppe
„im Einsatz" — sonst fehlte ausgerechnet die, die an dem Tag fährt.

Zu sehen ist das im Abschnitt „Wo die Loks stehen" (218er hervorgehoben, nach
Anzahl der 218er sortiert) und in der Tagesmeldung, dort mit den Loknummern
je Ort und dem größten Standort zuerst:

    Abgestellt:
      Niebüll (BW/Süd/Terminal): 218 341-6, 218 385-3, 218 390-3, …
      Husum: 218 453-9

In der HTML-Fassung steht zusätzlich die Anzahl hinter dem Ort. Die übrigen
Mailarten (Sofortmeldung, Streichung, Störung) führen das nicht mit — dort geht
es um einen einzelnen Umlauf oder die Betriebslage.

## Betreffzeilen

Kurz, das Wichtigste vorn, **das Datum immer am Ende** („· Do 17.9."), Loknummer
**mit** Prüfziffer und Namen. Hilfsfunktionen: `lok_kurz()`, `datum_mini()`,
`mit_datum()`. Beispiele:

    218 330-9 „Konrad" 18:03 Elmshorn Gl. 2 · Do 17.9.
    Keine 218 in Elmshorn, Lukas unterwegs · Do 17.9.
    Nachtrag: 218 453-9 „Lukas" fährt · Do 17.9.
    Gestrichen: 218 330-9 „Konrad" 18:03 Elmshorn · Do 17.9.
    +7 min: 218 453-9 „Lukas" RE 11052 ab Husum · Do 17.9.
    Störung Elmshorn: 11 Züge, 6 Ausfälle · Do 17.9.
    Sonderzug Elmshorn 14:32: D 13345 → Westerland(Sylt) · Do 17.9.

## „Diese Fahrt interessiert mich"

In Tagesmeldung, Sofortmeldung und Nachtrag steht bei jeder Fahrt ein Link, auf
der Webseite ein Knopf. Vorgemerkte Fahrten stehen in `interesse` und werden von
**`beobachten.py`** nachgeprüft. Der Cron läuft alle 5 Minuten; wie oft eine Fahrt
wirklich geprüft wird, hängt vom Abstand zur Abfahrt ab (`takt_fuer()`):

| Abstand zur Abfahrt | Takt | Echtzeit |
|---|---|---|
| mehr als 5 Stunden (oder morgen) | stündlich | nein |
| 5 bis 2 Stunden | alle 10 Minuten | ja |
| ab 2 Stunden bis 30 min nach Ankunft | alle 5 Minuten | ja |
| danach | nicht mehr | — |

Was geprüft wird:

1. Tagesbeitrag neu lesen — gestrichen, andere Lok, geänderte Zeiten. Dafür
   liefert `analysiere_beitrag(…, alle_baureihen=True)` auch 245/246-Umläufe.
2. Im Echtzeit-Fenster: Lage am **Startbahnhof** (nicht Elmshorn) — Verspätung ab
   `merken.verspaetung_ab_minuten` (Standard **5**), Ausfall, Gleiswechsel. Eine
   Verspätung wird erneut gemeldet, wenn sie um weitere 10 Minuten wächst.

Jede Änderung wird einmal gemeldet (`interesse.stand`). Beim ersten Blick auf eine
Fahrt nur dann eine Mail, wenn schon etwas nicht stimmt. Ist das Forum nicht
erreichbar, wird **nicht** „gestrichen" gemeldet.

**Mail-Links ohne Passwort, aber signiert.** `merken_link()` signiert
„tag|lok|zug" per HMAC-SHA256 mit `merken.geheimnis` aus `config.json`;
`merken.php` prüft mit demselben Schlüssel (`MERKEN_GEHEIMNIS` in `_kukas.php`).
Beide Werte müssen übereinstimmen. Ohne Schlüssel lässt sich kein gültiger Link
bauen. Die Knöpfe auf der Webseite brauchen die Anmeldung und ein
Sitzungsgeheimnis gegen untergeschobene Formulare.

**Stationsnummern** für die Echtzeit schlägt `eva_fuer()` über die DB-Schnittstelle
nach und speichert sie in `stationen`. Nur exakte Namenstreffer zählen:
„Westerland (Sylt)" mit Leerzeichen liefert sonst die *Autoverladung* statt des
Bahnhofs.

## Sonderzüge

Zwei Quellen:

* **`sonderzuege.py`**, alle 3 Stunden, 30 Stunden voraus: Soll-Fahrplan Elmshorn.
  Dort verkehren planmäßig nur RE, AKN und nordbahn (NBE), jeweils mit Linie
  (gemessen über 30 Stunden am 16.09.2026). Alles andere — andere Gattung oder
  keine Linie — ist ein Sonderzug. Einmal je Zug und Tag (`sonderzuege`).
* **Fahrzeiten-Forum (006)** in `sichtungen.py`: Themen mit Stichwörtern aus
  `sonderzuege.forum_stichwoerter` (Sonderzug, Dampf, Überführung …) und einem Ort
  im 50-km-Umkreis. Alles andere aus Brett 006 wird ohne Ortssuche als erledigt
  vermerkt. Ein neu hinzugenommenes Brett startet still (`still_bretter`), sonst
  käme der ganze Bestand als Mail.

## Besondere 218er

`besondere_loks.py` liest die Bestandstabelle des Wikipedia-Artikels
„DB-Baureihe 218" ein und schreibt alle nicht zerlegten Loks mit Eigenname,
Sonderlackierung oder Aufschrift/Werbung in `besondere_loks` (Stand 16.09.2026:
73 Loks, 7 mit Namen). Läuft sonntags 03:40 per Cron.

**Gelesen wird der Rohtext über die MediaWiki-API, nicht eine Zusammenfassung
der Seite.** Beim Aufbau lieferte eine KI-Zusammenfassung zwei falsche Dinge: Sie
schnitt die Tabelle ab 218 400 als „endet hier" ab — genau dort stehen die
Marschbahn-Loks — und sie setzte „Konrad" in die Bemerkungsspalte, wo er nicht
steht. Die Namen stehen in der Spalte **Betreiberbezeichnung**
(`218 453-9 "Lukas"`).

Namen mit einer zweiten Quelle außerhalb von Wikipedia sind als `belegt = 1`
markiert (Konrad, Guste, Donna, Meike, Conny). Lukas und Betty Boom stehen
bisher nur bei Wikipedia; die Webseite vermerkt das. Zusätzliche Angaben, die
nicht in der Tabelle stehen, kommen aus `ERGAENZUNGEN` mit eigener Quelle —
derzeit die PIKO/Märklin-Werbung der 218 497-6.

Auf der Webseite steht je besondere Lok **„jetzt"** und **„heute"**: aus ihren
heutigen Fahrten und der Uhrzeit abgeleitet — unterwegs von A nach B, wartet in C
bis zur nächsten Fahrt, fertig in D — oder der heutige Standort laut
Fahrzeugliste. Das ist der Plan, keine Ortung.

Verwendung: Namen gelten als Grundlage, die im Forum selbst gesehenen Namen
(`lok_namen`) gehen darüber. Die Besonderheit (Werbung, Design, Lackierung —
nicht Eigentümer oder Motor) steht in den Mailtexten hinter der Lok
(`mit_besonderheit()`), nicht in Betreffzeilen. Die Webseite zeigt die
besonderen 218er, die auf der Marschbahn gesehen wurden, mit letzter Fahrt und
letztem Standort, und die vollständige Liste aufklappbar.

## Wo die 218er fahren

`laeufe` enthält nur die Fahrten **durch Elmshorn** — darauf hängen Alarm,
Dublettenschutz und Aufmacher. Wo eine 218 sonst unterwegs ist (etwa nur
Husum–Westerland), stand dadurch nirgends.

`umlaeufe` speichert deshalb **alle** 218-Fahrten je Tag und Quelle, mit
Kennzeichen `elmshorn`. Die Tabelle dient nur der Anzeige; bei jedem Lauf werden
die Einträge des Threads neu geschrieben, damit gestrichene Fahrten verschwinden.
Die Streichungshistorie der Elmshorn-Läufe bleibt in `laeufe` und wird auf der
Webseite dazugemischt (durchgestrichen).

Angezeigt wird das in der Tageskarte (alle Fahrten, Elmshorn-Zeit und Gleis wo
zutreffend), in der Gruppe „im Einsatz" unter „Wo die Loks stehen" (erster Start →
letztes Ziel, Zahl der Fahrten) und in der Tagesmeldung unter „Fahrten:".

**Beschriftung beachten:** `tage.loks_218` enthält alle im Beitrag *genannten*
218er, auch die unter einem Standort abgestellten. Es heißt deshalb nicht
„im Einsatz". Ob eine Lok fährt, sagt allein `umlaeufe`.

## Eigennamen der Loks

Manche Maschinen tragen einen Namen: `"218 443-0 "DB Gebrauchtzug" (Donna)`.
In derselben Kopfzeile stehen aber auch Lackierung, Programm, UIC-Nummer und
freie Notizen — `(Verkehrsrot)`, `(Intercity)`, `(HVO Sticker)`,
`(Nur Werkstattaufenthalt)`, `(Ab morgen raus aus der Übersicht)`. Ohne Filter
hieße jede zweite Lok „Verkehrsrot".

`lok_name()` nimmt deshalb nur, was in Klammern oder Anführungszeichen steht,
höchstens zwei Wörter hat, groß anfängt, keine Ziffern enthält und in keiner
bekannten Kategorie liegt (`KEIN_NAME`, `NAME_VERDAECHTIG`). Im Zweifel lieber
kein Name als ein falscher.

Eine Feinheit: Eine Klammer direkt am Wort gehört zum Ortsnamen, nicht zur Lok —
sonst hätte `218 834-0 Führend nach Westerland(Sylt)` die Lok „Sylt" getauft.
Verlangt wird deshalb ein Leerzeichen vor der Klammer.

Namen gelten dauerhaft und stehen in `lok_namen` (nicht je Tag). Angezeigt werden
sie überall, wo eine Lok auftaucht: Aufmacher, Umlauftabelle, Standortliste und
in den Mails (`mit_name()`).

## Meldewege: Push oder Mail (seit 17.09.2026)

Die App heißt **Zugradar** und liegt seit 17.09.2026 unter `https://jarritc.de/zugradar/`.

Alle Meldungen gehen über `marschbahn.melde(cfg, art, …)`. Standard:

| Art | Mail | Push |
|---|---|---|
| `treffer` — neue Fahrt durch Elmshorn | ✓ | ✓ |
| `tagesmeldung_treffer` — Tagesliste mit 218 durch Elmshorn | ✓ | ✓ |
| `streichung` — gemeldete Elmshorn-Fahrt entfällt | ✓ | ✓ |
| `ausfall_218` — 218 fällt in Elmshorn laut Echtzeit aus | ✓ | ✓ |
| `tagesmeldung_leer` — Tagesliste ohne Treffer | | ✓ |
| `nachtrag` — weitere 218, nicht durch Elmshorn | | ✓ |
| `stoerung`, `entwarnung` | | ✓ |
| `sichtung` — Forenbeiträge im Umkreis (Antippen öffnet den Beitrag) | | ✓ |
| `sonderzug` | | ✓ |
| `beobachtung` — vorgemerkte Fahrten (ersetzt die vorige Meldung zur selben Fahrt) | | ✓ |
| `erinnerung` — 30 min vor der Durchfahrt (ohne Mail-Ersatz: eine späte Mail hilft nicht) | | ✓ |
| `wacht` — Selbstüberwachung | ✓ | ✓ |

Jede Meldung wird in `meldungen_log` festgehalten (Art, Betreff, Kurztext, Wege, Zahl der Mails
und erreichten Geräte) — das speist den Verlauf in der App. Testnachrichten von `geraete.php`
stehen dort mit Art `test` und dem Ergebnis aus der Warteschlange.

**Ersatzweg**: Kommt eine reine Push-Meldung auf keinem Gerät an (noch keins
angemeldet, Push-Dienst gestört), wird sie gemailt (`push.mail_wenn_push_scheitert`).
**Umstellen** je Art in `config.json`, z. B. `"kanaele": {"sichtung": ["mail", "push"]}`.
Der Push-Text entsteht aus den ersten Inhaltszeilen der Mail (`kurztext`), bei
beobachteten Fahrten nur aus den Änderungen.

## Mailregeln

Es gibt zwei Mailarten:

1. **Tagesmeldung** — geht **einmal je Tag** raus, sobald die `[DB Regio]`-Liste
   für diesen Tag im Forum auftaucht, und zwar ausdrücklich auch dann, wenn
   *keine* 218 durch Elmshorn fährt. Gesteuert über `config.json` →
   `"tagesmeldung": true`; zum Abschalten auf `false` setzen. Vermerkt in der
   Tabelle `tagesmeldung`, deshalb kommt sie pro Tag nur ein einziges Mal.
2. **Sofortmeldung** — wenn in einem schon bekannten Thread nachträglich ein
   Lauf dazukommt (die Beiträge werden tagsüber mehrfach bearbeitet).
3. **Streichung** — wenn ein Lauf, der vorher gemeldet wurde, wieder aus dem
   Beitrag verschwindet. Ohne diese Mail führe man umsonst nach Elmshorn.
   Gemeldet wird nur, was vorher auch wirklich rausging
   (`melde_grund IN ('gemeldet','tagesmeldung')`) — sonst käme eine Absage zu
   etwas, wovon man nie gehört hat. Taucht der Lauf später wieder auf, werden
   `gestrichen_am` und `streichung_gemeldet_am` geleert, eine erneute Streichung
   meldet also wieder.

4. **Nachtrag** — wenn nach der Tagesmeldung eine *weitere* 218 mit Fahrten in
   den Beitrag kommt. Die Beiträge werden oft Stunden nach dem Erscheinen noch
   ergänzt: Am 15.09.2026 ging die Tagesmeldung um 09:17 raus, die 218 453-9
   stand erst nach späteren Bearbeitungen (zuletzt 18:32) im Beitrag. Einmal je
   Lok und Tag, abschaltbar mit `"nachtrag": false`.

   Welche Loks die Tagesmeldung schon enthielt, steht in `tages_loks`. Die Zeile
   `lok = '__stand__'` markiert, dass der Ausgangsstand eines Tages erfasst ist.
   Fehlt sie — etwa für Tage, deren Tagesmeldung vor dieser Funktion rausging —,
   wird der Stand still erfasst, statt jede Lok des Tages nachzumelden.
   Fährt eine neue Lok durch Elmshorn, kommt keine Nachtragsmail: das deckt die
   Sofortmeldung ab (`grund = 'sofortmeldung'`).

Läuft beides im selben Durchgang zusammen, gewinnt die Tagesmeldung: die neuen
Läufe werden als `melde_grund = 'tagesmeldung'` abgehakt, damit nicht zwei Mails
mit demselben Inhalt rausgehen.

* Gemailt wird nur bei **neuen** Treffern (`laeufe.match_key`, enthält die Quelle).
* Nur für Tage **ab heute** — eine Meldung für gestern nützt nichts.
* Der allererste Lauf hat den Altbestand nur erfasst, ohne zu mailen
  (`meta.bootstrapped`).
* Läufe, die nach einer Bearbeitung aus dem Beitrag verschwinden, bleiben als
  Historie erhalten und werden `aktiv = 0` (auf der Seite durchgestrichen).

## Beiträge aus dem Umkreis

`sichtungen.py` liest zwei Foren und meldet Beiträge aus der Umgebung:

| Brett | Name | Anmerkung |
|---|---|---|
| 004 | Bild-Sichtungen | bundesweit, deshalb selten ein Treffer |
| 109 | Betriebsstörungen | bundesweit, schlägt häufiger an |

**Das Live-Sichtungssystem `/ds_sifo/ds_live.php` wird nicht abgefragt.** Die
robots.txt der Seite sperrt diesen Pfad ausdrücklich für alle Automaten:

    Disallow: /ds_sifo/

Das Forum „Live-Sichtungen" (Board 117) wäre erlaubt, ist aber seit 2024 tot —
die Sichtungen sind genau in das gesperrte System umgezogen. Wer das ändern
will, fragt bei der Redaktion nach einem Zugang; der Code dafür wäre klein.

### Wie der Umkreis bestimmt wird

Orte stehen als Freitext in Titel und Beitrag. Kandidaten werden über Muster
gelesen („in Itzehoe", „Glückstadt Bahnhof"), bei transitous geokodiert und gegen
Elmshorn gemessen. Jeder Ort wird **einmal** nachgeschlagen und bleibt in der
Tabelle `orte`.

Zwei Vorkehrungen gegen Fehltreffer, beide an echten Beiträgen erprobt:

* **Klammerzusätze zählen mit.** „Harburg" liegt 40 km entfernt, „Harburg
  (Schwab)" dagegen 557. Ohne den Zusatz meldet man Bayern als Nachbarort — genau
  das ist beim ersten Testlauf passiert.
* **Bundeslandprüfung.** Alles im 50-km-Umkreis liegt in Schleswig-Holstein,
  Hamburg, Niedersachsen oder Mecklenburg-Vorpommern. Ein Treffer außerhalb heißt:
  falscher Ort gleichen Namens.

Steht im Titel kein Ort, wird der Beitrag geholt — aber höchstens
`max_beitraege_je_lauf` (Standard 8) pro Durchlauf, damit ein reger Tag nicht
Dutzende Abrufe auslöst. Der erste Lauf hat den Bestand nur erfasst, ohne zu
melden.

### Die Beschreibung

Für einen Treffer wird der Beitragstext immer geholt und gekürzt in
`sichtungen.beschreibung` abgelegt — er steht dann in Mail und Webseite unter dem
Titel. Grund: **Die Fahrtrichtung steht meist erst im Text, nicht im Titel.** Aus
„218 450 Ein Fischkop in Hamburg-Harburg" wird so „… Heute 218 450 *Richtung
Maschen*". Wurde der Beitrag für die Ortssuche schon geholt, gibt es keinen
zweiten Abruf.

Gekürzt wird auf 400 Zeichen an einer Satz- oder Wortgrenze; Fußzeilen wie
„1-mal bearbeitet. Zuletzt am …" fliegen raus.

## Umzug nach /zugradar (17.09.2026)

Webordner jetzt `/var/www/html/jarritc.de/zugradar` (Sicherung des alten Stands:
`logs/webordner-marschbahn-vor-umzug`). Unter `/marschbahn` liegen nur noch:

* `.htaccess` — leitet alles per 301 nach `/zugradar` weiter (Merk-Links `?t=…` und
  Bilder in alten Mails funktionieren weiter).
* `index.php` — Startadresse: übernimmt die gespeicherte Anmeldung (altes Cookie
  `marschbahn_login`, Pfad `/marschbahn/`) als `zugradar_login` und springt per 302
  weiter. Sonst müsste jedes Gerät das Passwort neu eingeben.
* `sw.js` — Übergangs-Service-Worker: stellt Push-Nachrichten an Geräte, die sie noch
  unter `/marschbahn` eingeschaltet haben, weiter zu (Service Worker und Push-Abo hängen
  am Pfad). Schaltet ein Gerät Benachrichtigungen unter `/zugradar` ein, meldet
  `push-client.js` das alte Abo auf dem Server und im Browser ab und entfernt den alten
  Worker — keine doppelten Nachrichten.

`/kukas` leitet direkt nach `/zugradar`. **Keinen der beiden alten Ordner löschen.**
Die installierte App muss einmal neu installiert werden (die alte öffnet sich sonst mit
Adressleiste).

## Umzug nach /marschbahn

Seit dem 16.09.2026 liegt die Seite unter <https://jarritc.de/zugradar/>
(`/var/www/html/jarritc.de/marschbahn`). Die alte Adresse `/kukas` enthält nur
noch zwei Dateien:

* `.htaccess` leitet alles per 301 nach `/marschbahn` weiter, samt Abfrageteil —
  die Merk-Links in bereits verschickten Mails funktionieren dadurch weiter.
  Das Ziel steht absolut auf `https://`, weil Apache hinter dem Proxy sonst auf
  `http://` weiterleitet und ein zweiter Sprung nötig wäre.
* `sw.js` ist ein Service Worker, der sich selbst abmeldet. Browser folgen bei
  Service-Worker-Updates keiner Weiterleitung; ohne diese Datei bliebe ein Gerät,
  das die alte App schon geladen hat, dauerhaft auf `/kukas` hängen.

Die internen Namen (`_kukas.php`, Datenbank `zugradar_db`, Projektordner) sind
geblieben. Der alte Webordner ist gesichert unter
`logs/webordner-kukas-vor-umzug`.

## Die App (PWA)

`/marschbahn` lässt sich als App installieren — unten auf der Seite gibt es dafür einen
Knopf. Chrome/Edge/Android zeigen einen echten Installationsdialog; Safari auf
iPhone/iPad und Firefox kennen keinen, dort blendet der Knopf eine Anleitung ein.

| Datei | Zweck |
|---|---|
| `manifest.webmanifest` | Name, Farben, Icons, Startadresse |
| `sw.js` | Service Worker |
| `offline.html` | wird ohne Netz gezeigt, wenn keine alte Übersicht vorliegt |
| `.htaccess` | `no-cache` für `sw.js` und Manifest; `_kukas.php` gesperrt |

**Wird bei jedem Öffnen alles neu heruntergeladen? Nein.** Die App ist kein Paket,
das sich aktualisieren muss:

* Die **Übersicht selbst** kommt bei jedem Öffnen frisch vom Server
  („network first") — so wie beim normalen Aufruf. Nur ohne Netz wird die zuletzt
  geladene Fassung gezeigt.
* **Icons und Manifest** liegen im Speicher des Geräts, die paar Kilobyte kommen
  nicht jedes Mal neu.
* Der Browser prüft bei jedem Öffnen, ob sich `sw.js` geändert hat (`no-cache`).
  Unverändert: nichts passiert. Geändert: die neue Fassung wird übernommen
  (`skipWaiting`, `clients.claim`). Dafür `VERSION` in `sw.js` erhöhen —
  `icons_erzeugen.py` macht das bei neuen Icons von selbst.

**Icons**: `python3 icons_erzeugen.py BILD` schneidet ein rundes Emblem vom
einfarbigen Hintergrund aus und erzeugt Favicon, App-Icons (auch „maskable" für
Android, Kreis in der sicheren Zone), das iOS-Icon ohne Transparenz und das Logo
im Mailkopf (`logo-mail.png`). `--platzhalter` zeichnet ein Ersatz-Emblem.

Das aktuelle Emblem stammt aus `/root/8544c176-0a89-48f0-8e19-41ce0810b38e.jpg`.
Der Kreis wird um 1,2 % nach innen versetzt ausgeschnitten, sonst bleibt am Ring
ein heller Saum aus dem Hintergrundgrau.

## Push-Benachrichtigungen

**Stand 17.09.2026: eingebaut und getestet, aber noch mit keiner Meldung verknüpft.**
Welche Meldungen zusätzlich oder statt der Mail aufs Handy gehen, ist noch offen.
Für den Anschluss genügt in jedem Skript:

```python
import push
push.an_alle(conn, cfg, "218 453-9 Lukas · 14:32", "RE 11023 Gleis 3 · Do 17.9.", url)
```

**Einschalten**: In der App bzw. auf der Seite unten „Benachrichtigungen einschalten".
Der Browser fragt nach der Erlaubnis, legt ein Abo bei seinem Push-Dienst an
(Chrome/Android → Google FCM, Safari → Apple, Firefox → Mozilla), `push.php` speichert
es. Zur Bestätigung kommt sofort eine Begrüßung. Auf iPhone/iPad geht das nur in der
installierten App (iOS 16.4+), nicht in Safari selbst.

**Geräte-Seite**: `https://jarritc.de/zugradar/geraete.php` (gleiche Anmeldung).
Zeigt jedes Gerät mit Push-Dienst, wann angemeldet, zuletzt gesehen und zuletzt
zugestellt, den letzten Fehler. Je Gerät: Test senden, aus-/einschalten, umbenennen,
löschen. Dazu eine Nachricht an alle und der Verlauf der letzten 25 Sendungen; solange
etwas wartet, lädt die Seite alle 3 Sekunden neu.

**Versand**: PHP verschickt nichts selbst, es schreibt in `push_warteschlange`. Der
Cron startet `push.py --warteschlange --sekunden 55` jede Minute; das Skript schaut
knapp eine Minute lang alle 5 Sekunden nach (Sperrdatei `logs/push.lock` gegen
Überschneidungen). Eine Testnachricht ist so nach höchstens ~5 Sekunden unterwegs.

**Technik** ohne Zusatzpakete, nur `python3-cryptography`:
* Inhalt verschlüsselt nach RFC 8291 (`aes128gcm`). `test_parser.py` prüft das Byte
  für Byte gegen das Beispiel aus dem RFC — ein Fehler dort fiele sonst nie auf, denn
  der Push-Dienst nimmt die Nachricht an und das Handy verwirft sie still.
* Absender-Nachweis per VAPID (RFC 8292, ES256). Privater Schlüssel in `config.json`
  unter `push.vapid_privat`, der öffentliche in `meta.vapid_public` für die Webseite.
  **Nicht neu erzeugen** — alle bestehenden Abos würden ungültig. `--schluessel`
  lässt einen vorhandenen Schlüssel deshalb in Ruhe.
* TTL 12 Stunden, `Urgency: high`. Antwortet der Dienst 404/410, gibt es das Abo
  nicht mehr (App gelöscht, Erlaubnis entzogen) → Gerät wird ausgeschaltet.
* `sw.js` zeigt die Nachricht an (Icon, `badge-96.png` für die Statusleiste,
  optional `tag` zum Ersetzen einer älteren Meldung); Antippen holt die App nach
  vorn bzw. öffnet `url`. Tauscht der Browser das Abo selbst aus
  (`pushsubscriptionchange`), meldet der Service Worker die neue Adresse über die
  alte — dafür braucht er keine Anmeldung.

Ende-zu-Ende getestet am 17.09.2026 über den echten Mozilla-Push-Dienst: Anmeldung
über `push.php`, Testknopf auf `geraete.php`, Versand durch `push.py`, Empfang und
Entschlüsselung — Begrüßung und Testnachricht kamen an; ein ungültiges Abo (404)
schaltete das Gerät ab. Testdaten danach per ID gelöscht.

```bash
python3 push.py --liste                         # Geräte
python3 push.py --test "Hallo" [--geraet 3]     # direkt senden, ohne Warteschlange
```

## Angemeldet bleiben

Nach der richtigen Passworteingabe setzt die Seite das Cookie `marschbahn_login` (ein
Jahr, nur `/marschbahn/`, `Secure`, `HttpOnly`). Es enthält nur das Ablaufdatum und
eine HMAC-Signatur (Schlüssel `MERKEN_GEHEIMNIS`), kein Passwort. Die App fragt dann
nicht mehr nach dem Passwort; bei Nutzung verlängert sich das Cookie wieder auf ein
Jahr. „Abmelden" löscht es. **Alle Geräte abmelden**: `LOGIN_VERSION` in `_kukas.php`
erhöhen. Seit 17.09.2026 heißt die Seite in Titel, Kopfleiste und Manifest nur noch
„Marschbahn“.

## Fahrtseite, Knöpfe in der Meldung, Suche, Statistik

* **Fahrtseite** `./?s=fahrt&tag=…&lok=…&zug=…` (`marschbahn.fahrt_url`): erwartete Zeit mit
  Verspätung und Gleis, Zustand (kommt · durch · vorbei · gestrichen · Ausfall), Wagenreihung,
  Merk-Knopf, der ganze Tag dieser Lok und der Forumsbeitrag. **Alle Benachrichtigungen zu
  einer Fahrt zeigen dorthin** (Erinnerung, Nachmeldung, beobachtete Fahrten).
* **Knöpfe in der Benachrichtigung** (`push.aktionen` → `sw.js` → `aktion.php`): „Beobachten“
  bei der Erinnerung, „Nicht mehr beobachten“ bei der Live-Meldung. Der Service Worker ruft
  `aktion.php` ohne offene Seite auf; legitimiert wird das mit einem HMAC-signierten Auftrag
  (`marschbahn.aktion_token`, gleiches Verfahren wie die Merk-Links), nicht mit der Sitzung.
  Danach erscheint eine stille Quittung; scheitert der Aufruf, öffnet sich die Bestätigungsseite
  (`&seite=1`). Android zeigt höchstens zwei Knöpfe, deshalb die Begrenzung.
* **Verlauf durchsuchen**: Suchfeld auf `./?s=verlauf` (`q`), sucht in Betreff und Kurztext,
  lässt sich mit den Artfiltern kombinieren.
* **Statistik** `./?s=statistik` (Mehr → Statistik): Anteil der Tage mit 218, Durchfahrten je
  Richtung, welche Lok wie oft (Balken), Wochentage, Uhrzeiten, Gleisverteilung und Umläufe je
  Herkunft. Gezählt wird, was in den Tageslisten stand.

## Karte

`./?s=karte` (Mehr → Karte, auch über „Karte →“ auf der Loks-Seite): Leaflet 1.9.4 von cdnjs
(mit Prüfsumme/SRI) und OpenStreetMap-Kacheln, im dunklen Design abgedunkelt. Ebenen, einzeln
abschaltbar:

* **Bahnhöfe** (Knopf „Bahnhöfe“, aus): alle Halte, die in den letzten drei Wochen von einem
  Zug angefahren wurden — `zuege.py` schreibt sie aus den Fahrten selbst nach `karte_halte`
  (Name, Lage, Gebiet, Rang: 3 ICE, 2 IC/EC, 1 RE, 0 RB, Zahl der Fahrten). `halte.php?r=SH,NI`
  liefert sie, die Karte zeigt sie **nach Zoom gestaffelt**: ICE-Halte ab Zoom 7, IC ab 9,
  RE ab 10, alle ab 12; Namen ab 9/11/12/13 (`AB_ZOOM`, `NAME_AB_ZOOM`). Punktgröße und Farbe
  nach Rang. Antippen öffnet die Abfahrtstafel — „Hannover Hauptbahnhof“ wird dafür zu
  „Hannover Hbf“ (`bfName`), weil die Bahn andere Schreibweisen nutzt,
* **Landesgrenzen** (in der Filtertafel, aus): **Deutschland** als kräftige durchgezogene
  Linie (sonst fehlt die Grenze nach Dänemark, Polen und Tschechien) und die Schleswig-Holstein, Hamburg, Niedersachsen,
  Bremen, Mecklenburg-Vorpommern, Berlin und Brandenburg als gestrichelte Linien.
  `karte.py --grenzen` holt sie einmalig von deutschlandGeoJSON (Grundlage: Verwaltungsgebiete
  des BKG, dl-de/by-2-0 — Quellenangabe steht unter der Karte), vereinfacht auf 200 m und legt
  sie in `karte_grenzen` ab (zusammen 66 kB). Overpass taugte dafür nicht: Die Grenzrelationen
  laufen dort in den Timeout (504, auch auf dem Ausweichserver). **Falle**: Douglas-Peucker auf
  einem geschlossenen Ring lässt alles wegfallen (Anfang = Ende, Strecke null lang) — deshalb
  `_vereinfache_ring()`, das den Ring in zwei Hälften teilt,
* **Streckennetz** (Knopf „Strecken“, standardmäßig aus): alle Abschnitte, auf denen in den
  letzten drei Wochen ein Zug gefahren ist — dünn und grau unter allem anderen. Grundlage ist
  `linien_abschnitte`: seit 25.09.2026 merkt sich `zuege.py` **jede** Linie (nicht nur die
  durch Elmshorn) mit Gebiet (`region`); `netz_bauen()` fasst daraus alle 15 Minuten je Gebiet
  ein Netz zusammen (gleiches Halte-Paar nur einmal, Hin- und Rückrichtung zusammengelegt) und
  legt es in `karte_netz` ab. `netz.php?r=SH,NI` liefert es; die Karte holt es erst beim
  Einschalten der Ebene und neu, sobald ein Gebiet dazukommt. Größe: SH rund 30 kB, NI rund
  35 kB. Das Netz wächst mit der Zeit — was nachts nicht gefahren wird, kommt tagsüber dazu,
* **Grenzen der Schnittstelle** (26.09.2026): Mit allen 16 Ländern brach der Lauf morgens
  ab — Niedersachsen, NRW, Baden-Württemberg und Bayern liefern tagsüber zu viele Züge
  (HTTP 422 selbst bei einer Minute Vorlauf), und die Ausnahme riss den ganzen Durchlauf mit.
  Seitdem: `hole_gebiet()` viertelt einen zu vollen Ausschnitt (bis zu `ANFRAGEN_JE_GEBIET`
  Abrufe, höchstens drei Teilungen), ein gescheitertes Land stoppt die übrigen nicht mehr,
  `ANFRAGEN_JE_LAUF` (24) begrenzt die Abrufe je Minute, und die Länder kommen reihum dran
  (`karte_gebiete_versatz`; SH und HH immer zuerst). Nicht geholte Gebiete behalten ihre Züge
  aus dem letzten Lauf, solange deren Ankunft in der Zukunft liegt. Der Zustand steht in
  `meta.karte_gebiete_status` (`teilweise` | `ausgelassen` | `fehler`) und erscheint als „!“
  am Knopf in der Filtertafel. **Maß halten**: 17 Gebiete sind rund 1300 Abschnitte und
  860 kB je Abruf — jede Minute, auf jedem Gerät,
* **Gebiete und Zugarten** (in der Filtertafel): Neben Schleswig-Holstein und Hamburg (beide
  immer an) lassen sich **alle 16 Bundesländer** einzeln und der **dänische Grenzraum**
  dazuschalten — `regionen.php` schreibt die Auswahl nach
  `meta.einst_karte_regionen`, `zuege.py` fragt beim nächsten Lauf je Gebiet einen eigenen
  Ausschnitt ab (`REGIONEN`; ein Ausschnitt über alles wird mit HTTP 422 abgelehnt) und hängt
  jedem Abschnitt sein Gebiet als `r` an. **Zuordnung über die echten Landesumrisse**:
  `umrisse_laden()` holt sie aus `karte_grenzen` (karte.py --grenzen, alle 16 Länder), daraus
  entstehen Vieleck, Umschlag und Abfrage-Ausschnitt je Land; `region_von()` prüft erst den
  Umschlag, dann die Umrisse, und nimmt kleine Länder zuerst (Bremen liegt in Niedersachsen,
  Hamburg in Schleswig-Holstein, Berlin in Brandenburg). Kommandozeile: `zuege.py --regionen SH,NI,MV`. Zum Vergleich: SH+HH allein
  rund 75 Abschnitte (40 kB), mit NI und DK rund 280 (156 kB), mit Bayern und NRW 606 (388 kB)
  und je Land eine Abfrage in der Minute — deshalb sind standardmäßig nur SH und HH an. Die Seite bettet nur SH ein und holt den Rest über
  `zuege.php` nach (`?r=SH,NI` filtert serverseitig). **Regio** und **Fern** (ICE, IC, EC,
  FLX …) blenden nur in der Anzeige aus (`zugradar_karte_arten` im Gerät),
* **Filtertafel** über der Karte (Knopf „☰ Filter“, daneben der aktuelle Stand als Text):
  klappt auf und zeigt alles nach Gruppen — **Kartentyp** (eine Wahl), **Anzeigen** (die
  Ebenen mit Zeichen), **Gebiete**, **Zugarten**, **Gleise von OpenRailwayMap**. Sie ersetzt
  das Ebenen-Menü von Leaflet (`L.control.layers` entfällt) und die früheren zwei Knopfreihen.
  Tippen außerhalb schließt sie. Ausgeblendetes merkt sich das Gerät (`zugradar_karte_aus`),
* **Kartentypen** (Auswahl oben rechts, das Gerät merkt sich die Wahl in `localStorage`):
  Standard (OSM, im dunklen Design invertiert — nur diese Ebene trägt `kachel-invert`), Hell
  und Dunkel (CARTO), Topografisch (OpenTopoMap, bis Zoom 17), Satellit (Esri World Imagery).
  Darüber legbar: **Gleisplan**, **Höchstgeschwindigkeit** und **Signale** von OpenRailwayMap.
  Alle Kachelebenen senden `referrerPolicy: 'strict-origin'` (OpenRailwayMap lehnt Anfragen
  ohne Browser-Kennung/Referer mit 403 ab — vom Server per curl nur mit Browser-UA prüfbar).

* **Marschbahn** mit der **echten Gleisführung** aus OpenStreetMap (RE6-Route, Relation
  1175046: 303 Wege, 2 876 Punkte, per Douglas-Peucker auf 255 Punkte vereinfacht, 12 m
  Toleranz; Länge 236,5 km — stimmt mit der realen Strecke überein). `karte.py` lädt sie alle
  30 Tage über Overpass (bei 504 der Ausweichserver) nach `meta.karte_gleise`;
  `--gleise-datei` verarbeitet eine gespeicherte Antwort,
* **21 Bahnhöfe** als eigene HTML-Marken (nicht als SVG-Kreise): 30 px Tippfläche, weißer
  Punkt mit rotem Rand, Elmshorn größer; Hamburg-Altona, Elmshorn, Itzehoe, Heide, Husum,
  Niebüll und Westerland dauerhaft beschriftet. Vorher waren es kleine Kreise im selben
  SVG wie Linie, 50-km-Kreis und Abstellorte — Elmshorn als roter Punkt auf roter Linie
  unsichtbar, und die später gezeichneten Kreise fingen das Antippen ab. Der 50-km-Kreis ist
  jetzt `interactive: false`, Lok-Marken sitzen etwas über ihrem Punkt, damit eine wartende
  Lok den Bahnhof nicht verdeckt. **Darstellung**: Leaflets Formatvorlage wird nach unserer
  geladen und setzt `.leaflet-marker-icon` auf `display:block` — der Punkt wurde dadurch zu
  einem leeren Inline-Element, sichtbar nur als schmales Oval. Deshalb `.leaflet-marker-icon
  .halt-marke` (höherer Vorrang) und `span` als Block. **Position**: `karte.py --halte` setzt
  die Bahnhöfe auf ihre Haltestelle (transitous, `type=STOP`, Schreibweise der Bahn), statt
  der Ortsmitte — vorher lag Burg(Dithm) 1,2 km neben dem Gleis; die Seite projiziert jeden
  Bahnhof zusätzlich auf den nächsten Gleisabschnitt (größter Rest: 43 m in Altona). Abstellorte
  werden ebenso aufs Gleis gesetzt, wenn es höchstens 3 km entfernt ist. Antippbar: Das Fenster zeigt die **Abfahrtstafel** (`tafel.php` —
  Elmshorn aus `lage`, andere über `bahnhof_lage`/`bahnhof.py`, 2–3 s beim ersten Mal),
  Zeit, Verspätung, Linie, Ziel, Gleis, Ausfall, dazu „Alle Abfahrten mit Filtern →“ zur
  Live-Seite. Die Namen in der Strecke entsprechen denen der Bahn („St Michaelisdonn“ ohne Punkt),
* **50 km um Elmshorn** (der Umkreis der Beitragssuche),
* **218er laut Plan**: Position aus den heutigen Umläufen und der Uhrzeit — unterwegs
  anteilig **entlang der echten Gleise** (jeder Halt ist dem nächsten Gleispunkt zugeordnet), sonst am Halt, an dem sie wartet
  oder angekommen ist. Ausgefallene Fahrten (Echtzeitlage und gemeldete Ausfälle) zählen nicht
  mit und stehen im Popup („fällt aus: RE 11029“). Popup mit Link zum Steckbrief,
* **Züge live** — alle fahrenden Züge im Norden (Regional- und Fernverkehr, ohne AKN,
  S-Bahn, Bus) als Zug-Pillen mit Liniennamen: je Gattung eine eigene Farbe (RE blau #0a5ec7,
  RB grün #2b6a3f, ICE weiß mit rotem Rand, IC/EC anthrazit, FLX grün, Nacht­züge violett —
  `farbgattung()` in index.php, Klassen `.zug-marke.re/.rb/.ice/.ic/.flx/.nacht`), **Züge mit
  218 rot und größer** mit der Lok („218 330“). Die 218 erkennt die Karte an der Zugnummer aus den
  heutigen Umläufen (`nr218`, ohne ausgefallene Fahrten). Quelle: `zuege.py` (Cron jede
  Minute) holt transitous `map/trips` für den Ausschnitt 53,45–55,05 N / 8,2–10,35 O und legt
  die Abschnitte von Halt zu Halt in `karte_zuege` (MEDIUMTEXT, meta.wert ist zu klein).
  Jeder Abschnitt: Fahrt-Kennung `t` (CRC32 der tripId), Linie, Nummer, von/nach, Abfahrt
  und Ankunft (Unix-Zeit, mit Echtzeit), `ab_plan`, `echt`, Verlauf `p` (auf 25 m
  vereinfacht). Die Seite bekommt den Stand eingebettet und holt `zuege.php` jede Minute
  nach; die Position rechnet der Browser alle 2 s aus Zeitanteil und Verlauf. Zwischen zwei
  Abschnitten „hält“ der Zug (Pille über dem Punkt, damit der Bahnhof antippbar bleibt), vor
  der ersten Abfahrt erscheint er 3 min vorher, nach der letzten Ankunft bleibt er 1 min.
  Unter Zoom 9 nur das Zugsymbol (außer 218). Popup: Zug, **Start- und Zielbahnhof der ganzen
  Fahrt mit Plan-Uhrzeit**, aktueller Abschnitt, Verspätung, bei 218 Lok und Steckbrief. Start
  und Ziel schlägt `zuege.py` je Fahrt einmal über transitous `api/v5/trip` nach (höchstens 40
  neue je Minute, Tabelle `fahrt_enden`, nach zwei Tagen gelöscht; „ZOB/Bahnhof“ wird
  abgeschnitten). **Nur Schleswig-Holstein und Hamburg**: ein Abschnitt zählt nur, wenn beide
  Halte im Vieleck `GEBIET` liegen (Elbe und Hamburger Landesgrenze im Süden — Harburg und
  Neugraben ja, Stade, Buxtehude, Meckelfeld, Winsen, Cuxhaven nicht; dänische Grenze im
  Norden). Ein Zug nach Bremen verschwindet also in Harburg. **Grenze der Schnittstelle**: Fenster über etwa 7–10 min lehnt transitous
  zur Hauptverkehrszeit mit HTTP 422 ab — der Lauf nimmt dann 4 oder 2 min Vorlauf,
  `--vorfuellen` holt drei Stunden in 5-Minuten-Stücken,
* **Fahrzeuge im Zug-Fenster** — beim Antippen eines Zugs holt die Karte einmal dessen
  Wagenreihung (`wagenreihung.php?…&bf=<nächster Abfahrtsbahnhof>`, EVA aus `bahnhofsliste`;
  `wagenreihung.py` kann seit 18.09.2026 jeden Bahnhof, Spalte `eva`, Elmshorn bleibt Standard)
  und zeigt `fahrzeuge()`: mit Lok „245 213-4 + 6 Married-Pair-Wagen“ (Marschbahn-Wagen:
  55 80 …, ABpma/Bpmdza/Bpmdfa), „146 + 6 Dostos“ und **was vorne fährt** („Lok 218 453-9“,
  „Steuerwagen (Dosto)“; Richtung aus den Pfeilen, Steuerwagen am kleinen „f“ der Gattung);
  Triebzüge in einer Zeile mit Baureihe aus der UIC-Nummer des Endwagens (Stellen 5–8):
  „445 Twindexx“, „1429 FLIRT · 2 Einheiten“, „412 ICE 4“ (`TRIEBZUG_NAMEN`,
  `TRIEBZUG_REIHE` für abweichend nummerierte Endwagen), Unbekanntes als „Triebzug 2826“.
  **ICE L** (lokbespannt: Vectron 193 oder Talgo-Lok 105 + Talgo-Wagen) läuft bei der Bahn
  als „ICE“ — `fahrzeuge(wagen, kategorie)` erkennt es an „ICE mit Lok“: „ICE L · 193 123-4 +
  17 Talgo-Wagen“, die Marke heißt dann „ICE L · 193“. „ICL“ in transitous ist dagegen der
  dänische IC Lyn (außerhalb der Karte).
  Das Fenster hat feste Breite (einmal gebunden, danach `setPopupContent`) und der
  Fahrzeug-Block feste Höhe (zwei einzeilige Zeilen) — beim Nachladen springt nichts. Danach trägt die Marke die Baureihe („RE6 · 245“). Nur angetippte Züge — dbf ist
  ein privater Dienst, nichts wird auf Vorrat geholt. Goldener Rand an der Marke: 5 Minuten
  oder mehr verspätet,
* **Strecke antippen** — Tippen irgendwo auf ein gezeichnetes Gleis (Marschbahn oder Linie
  durch Elmshorn; der Punkt rastet bis 28 px daneben aufs nächste Gleis ein, Tippen daneben tut
  nichts) öffnet ein Fenster „Zwischen Elmshorn und Tornesch“: alle Züge der nächsten 90
  Minuten, die dort vorbeikommen — Uhrzeit (mit Verspätung, „in 5 min“), Zug, Start → Ziel
  der ganzen Fahrt, „kommt aus …, fährt nach …“ bzw. „hält hier“; 218er rot mit Lok.
  `strecke.php` trägt den Punkt (auf 4 Nachkommastellen, ~10 m) in `strecken_punkte` ein,
  `punkt.py --warteschlange` (Cron jede Minute, Schleife 55 s, Takt 1 s) holt transitous
  `map/trips` für gut 1 km um den Punkt — so kleine Ausschnitte gehen auch über 90 Minuten
  ohne HTTP 422. Treffer: Verlauf höchstens 200 m am Punkt vorbei; Durchfahrtszeit anteilig
  nach der Strecke zwischen Abfahrt und Ankunft des Abschnitts; je Fahrt nur die erste Zeit
  (am Bahnhof enden und beginnen zwei Abschnitte). Start und Ziel aus `fahrt_enden`. Eine
  Antwort gilt zwei Minuten, Anfragen werden nach einem Tag gelöscht. Je Zeile steht darunter
  eine Fahrzeugzeile fester Höhe („1429 FLIRT · 2 Einheiten“, „218 390-3 + 6
  Married-Pair-Wagen · vorne Lok …“): die ersten fünf Züge von selbst, die übrigen per
  „Fahrzeuge anzeigen“. Abgefragt wird die Wagenreihung am Halt `von` zur Planabfahrt
  `ab_plan` (punkt.py liefert beides); `holeWagen()` fragt nacheinander und merkt sich jedes
  Ergebnis, Karte und Streckenfenster teilen sich den Speicher. Test:
  `punkt.py --abruf 53.7436 9.6625`,
* **In deiner Nähe** (Startseite, unter den Kacheln): „📍 Standort bestimmen“ → GPS im
  Browser → `strecke.php?naehe=1&lat=…&lon=…` (Standort auf 3 Nachkommastellen ≈ 100 m
  gerundet, Schlüssel `n:…` in `strecken_punkte`) → `punkt.py` sucht alle Gleise bis 3 km
  (Ausschnitt ±0,03°, bei HTTP 422 kürzeres Zeitfenster) und nimmt je Zug den Abschnitt, der
  dem Standort am nächsten kommt: Uhrzeit dort, „in 5 min“, Zug, Start → Ziel, „760 m
  entfernt · zwischen Elmshorn und Horst“; 218er rot. Höchstens 8 Züge, „↻ Aktualisieren“,
  „Karte →“ öffnet `?s=karte&standort=1` (die Karte sucht dann gleich selbst). **Nie von
  selbst** (Wunsch 18.09.2026) — nur per Knopf oder über den Schnellstart „In deiner Nähe“
  (`?naehe=1`, das Antippen dort ist der Klick),
* **Mein Standort** (Knopf oben links unter dem Zoom): GPS des Handys
  (`getCurrentPosition`, hohe Genauigkeit, 20 s), blauer Punkt mit Genauigkeitskreis, dann der
  nächste Punkt auf einem Gleis — Marschbahn, Linien durch Elmshorn oder Verlauf eines gerade
  fahrenden Zugs (in Metern, Lotfußpunkt). Bis 3 km: Karte springt hin und das
  Streckenfenster öffnet mit „📍 Du bist 150 m vom Gleis · GPS auf ±30 m“; weiter weg nur
  Standort, Entfernung und „Züge dort zeigen“. Der eigene Standort bleibt im Browser, an
  `strecke.php` geht nur der Gleispunkt. **Header**: Der Proxy (Caddy) schickt seitenweit
  `Feature-Policy: geolocation 'none'` — damit sperrt Chrome GPS. `.htaccess` setzt deshalb
  für /zugradar/ `Permissions-Policy: geolocation=(self), microphone=(), camera=()`; hat
  Chrome beide, gilt Permissions-Policy,
* **Linien durch Elmshorn** — RE7, RE70, RB61, RB71 (RB60, wenn sie fährt; RE6 ist die
  Marschbahn-Linie, AKN nicht). `zuege.py` merkt sich jeden Abschnitt dieser Linien in
  `linien_abschnitte` (Linie, von, nach, Verlauf; nach 21 Tagen ohne Fahrt gelöscht); die
  Seite zeichnet je Halte-Paar einen Strich, dünner und unter der Marschbahn,
* **218er laut Plan** werden ausgeblendet, sobald dieselbe Lok live als Zug zu sehen ist —
  übrig bleiben 218er ohne Treffer in den Daten (etwa SyltShuttle-Züge, abgestellte oder
  wartende Loks),
* **Abgestellt** (neueste Fahrzeugliste; Orte am selben Punkt, z. B. „Niebüll“ und „Niebüll
  (BW/Süd/Terminal)“, als ein Kreis),
* **Umkreis** — Beiträge der letzten sieben Tage.

Die Koordinaten pflegt `karte.py` (Cron stündlich, :25) in `koordinaten`: Strecke plus alle
Halte und Standorte der letzten drei Tage, geokodiert über `sichtungen.ort_pruefen`
(transitous, mit Gedächtnis in `orte`). `ALIAS` bildet Forumsschreibweisen ab
(„Westerland(Sylt)“ → Westerland). „Langenhorn“ fehlt absichtlich in der Strecke: die
Geokodierung landet in Hamburg-Langenhorn. Die Webseite fragt keinen Dienst an; nur der
Browser lädt Leaflet und die Kartenkacheln — ohne Netz zeigt die Seite einen Hinweis.

**Reihenfolge beim Aufbau**: erst `fitBounds`, dann die Ebenen. Umgekehrt bricht Leaflet
beim Zeichnen der Linie ab (Renderer ohne Grenzen, „Cannot read properties of undefined
(reading 'min')“), und alles danach fehlt — Kacheln sichtbar, Einträge nicht (18.09.2026).
Ein Fehler erscheint seitdem als Hinweis unter der Karte statt als leere Fläche. Nachgeprüft
in jsdom mit dem echten Leaflet: 6 Lok-Marken, 30 Pfade, keine Fehler.

**Referer für die Kacheln**: jarritc.de schickt seitenweit `Referrer-Policy: no-referrer`
(vom vorgeschalteten Proxy). Die Kachelserver von OpenStreetMap sperren Browser-Anfragen ohne
Referer („Access blocked … tile usage policy“, 18.09.2026). Deshalb setzt nur die Kachelebene
`referrerPolicy: 'strict-origin'` — gesendet wird bloß `https://jarritc.de/`, kein Pfad. Die
seitenweite Einstellung bleibt unangetastet.

## Bahnhofswahl mit Vorschlägen

Auf der Live-Seite ist die Bahnhofswahl **eine Zeile**: ein Feld mit dem aktuellen Bahnhof.
Beim Antippen erscheinen die üblichen Bahnhöfe und die zuletzt gesuchten, beim Tippen ab zwei
Zeichen Vorschläge aus der ganzen Liste (`vorschlag.php`, Namensanfang zuerst, Hauptbahnhöfe
vor Bus und ZOB, kurze Namen zuerst). Pfeiltasten, Enter und Escape funktionieren.

Die Liste stammt aus der Bahn-Schnittstelle selbst: `station/*` liefert alle rund 25 000
Halte, genommen werden die mit `db="true"` (16 964). `bahnhof.py --liste` lädt sie sonntags
neu in `bahnhofsliste` (Schlüssel ist die EVA-Nummer — unter der Sortierung `unicode_ci`
gelten „Münchhausen“ und „Munchhausen“ als gleich). Beim Tippen geht damit nichts nach außen;
beim Abruf nimmt `bahnhof.py` die EVA-Nummer gleich aus dieser Tabelle. Die frühere Quelle für
eine Bahnhofsliste (download-data.deutschebahn.com) gibt es nicht mehr.

## Andere Bahnhöfe live

Elmshorn schreibt `stoerung.py` ohnehin alle zehn Minuten fort. Für die übrigen Bahnhöfe
wäre ein fester Takt Verschwendung — man schaut selten hin, und jede Abfrage kostet mehrere
Aufrufe. Deshalb holt `bahnhof.py --warteschlange` (Cron, jede Minute, alle 2 s) nur, was in
der App aufgeschlagen wird, und hält es fünf Minuten (`bahnhof_lage`). Die Schnellauswahl (`bahnhof.BAHNHOEFE`, gleiche Liste in
`index.php`) deckt die Marschbahn ab; dazu gibt es ein **Suchfeld für jeden Bahnhof**
(Schreibweise wie im Fahrplan, „Kiel Hbf“, „Westerland(Sylt)“). Der Name geht an die
Bahn-Schnittstelle, deshalb wird er beidseitig streng geprüft (`^[\p{L}\d .()/'-]{2,40}$`
in PHP, `bahnhof.NAME_RE` in Python); unbekannte Namen enden mit einer verständlichen
Meldung. Schon gesuchte Bahnhöfe erscheinen als zusätzliche Knöpfe. Beim ersten Aufruf steht „wird geholt …“, die Seite lädt sich nach vier
Sekunden selbst neu. Die Wagenreihung nutzt dann die EVA-Nummer des gewählten Bahnhofs.
218-Hervorhebung, Umkreis, Sonderzüge und gemeldete Störungen bleiben Elmshorn vorbehalten.

## Sonntagsnachricht zu Sonderzügen

Eine echte Wochenvorschau auf die 218-Umläufe gibt es nicht, und das ist gemessen, nicht
geschätzt: Die Tageslisten im Forum erschienen bei den letzten 24 Listen **nie mehr als drei
Stunden vor Mitternacht** (SyltShuttle meist 21–23 Uhr, DB Regio oft erst nach Mitternacht,
einmal erst um 9 Uhr), und der Soll-Fahrplan der Bahn liefert für Elmshorn nur **heute und
morgen** (übermorgen: 0 Fahrten).

Was vorher feststeht, sind Sonderfahrten. `wochenvorschau.py` (Cron sonntags 18 Uhr) schaut
deshalb sieben Tage voraus in `sonderzuege` und nimmt dazu Ankündigungen aus Brett 006, aber
nur solche mit einem **Datum in der kommenden Woche** (`termin_in_woche`) — sonst landen
Fragen und Berichte über vergangene Fahrten in der Vorschau. Gibt es nichts, kommt keine
Nachricht.

## Jetzt prüfen und Logs

`auftrag.py --warteschlange` (Cron, jede Minute, prüft alle 2 s) führt Aufträge aus der App
aus. Die Webseite darf die Skripte nicht selbst starten — `config.json` mit den Zugangsdaten
gehört dem Hintergrunddienst —, sie schreibt nur eine Zeile in `auftraege`. Startbar ist nur,
was in `auftrag.ERLAUBT` steht (feste Liste, keine Parameter aus dem Web), Zeitgrenze 10
Minuten, Ausgabe und Ergebnis landen in der Tabelle und stehen auf der Seite Quellen.

Dasselbe Skript spiegelt bei jedem Lauf die letzten 400 Zeilen jedes Protokolls nach
`zugradar/protokolle/`. Grund: `/opt/docker` hat Rechte 770 und enthält auch andere Projekte —
statt sie zu lockern, bekommt der Webserver nur diesen Auszug. Direkt abrufbar ist er nicht
(`RedirectMatch 404` in der `.htaccess`), lesbar nur über die angemeldete Seite **Logs**.

## Live-Meldung für beobachtete Fahrten

Ab `merken.live_ab_minuten` (60) vor der Abfahrt schickt `beobachten.py` zu jeder
vorgemerkten Fahrt eine Meldung mit der Markierung `fahrt-<id>`. Jede weitere Prüfung
(alle 5 Minuten) ersetzt sie, sodass auf dem Handy immer nur eine aktuelle Zeile liegt:
„In 12 min: 218 453-9 „Lukas“ 14:32 (+4), Gleis 3“. Bis 15 Minuten nach der Abfahrt läuft
sie weiter („Jetzt“, dann „Abgefahren“).

Anzeige über `push.optionen` → `sw.js`: `festhalten` (requireInteraction — sie bleibt
liegen, bis man sie antippt oder wegwischt), `still` (Aktualisierung ohne Ton und
Vibration) und `ersetzen` (bei echten Änderungen meldet sie sich erneut). Bemerkbar macht
sie sich nur beim ersten Mal und bei Ausfall, Gleiswechsel, Lokwechsel oder wachsender
Verspätung — die Minutenaktualisierung bleibt still.

Änderbar in der App unter **Mehr → Einstellungen** (an/aus, ab 15–120 Minuten, angepinnt
ja/nein). Diese Werte gelten für alle Geräte, weil der Server verschickt; sie stehen in
`meta` unter `einst_merken_*` und gehen `config.json` vor (`mb.einstellung()`), denn die
Webseite darf `config.json` nicht schreiben.

## Ausfälle früh erkennen

Die Bahn kennt Ausfälle oft Stunden vorher (Personalausfall). `stoerung.py` fragt die Echtzeit
deshalb nicht mehr nur drei Stunden voraus, sondern bis zur letzten 218 des Tages
(`abfrage_stunden`, höchstens zehn), und meldet einen Ausfall für **jede** noch kommende 218
sofort — Verspätung und Gleis weiterhin erst im Zwei-Stunden-Fenster, weil sie vorher nichts
wert sind. Anlass 18.09.2026: RE 11030 (19:58) stand um 16:40 schon als ausgefallen bei der
Bahn, Zugradar sah nur bis 19:20.

In der App bleibt ein gemeldeter Ausfall stehen, auch wenn die Bahn den Zug danach aus der
Echtzeitliste nimmt (Abgleich mit `stoerungen`, Art `ausfall`): Zeile durchgestrichen mit
„Ausfall“, ohne Wagenreihung; der Aufmacher überspringt ausgefallene Fahrten („Heute keine mehr
— die übrigen 2 Fahrten der 218 heute fallen aus“), die Kachel zeigt „davon 2 Ausfälle“.

## Wiedererkennung einer Fahrt

`match_key` = Tag | Quelle | Lok | Zugnummer. Bis 18.09.2026 ging der **Wortlaut der ganzen
Forumszeile** ein. Dann wurde im Beitrag bei RE 11029 und RE 11030 nachträglich ein
Zwischenhalt „Husum“ ergänzt: Dieselbe Fahrt galt als neu, die alte als gestrichen, und im Plan
standen beide. Eine falsche Streichungsmeldung blieb nur aus, weil der Lauf danach wegen einer
Zeitüberschreitung beim Forum abbrach. Umgestellt am 18.09.2026: alle Schlüssel neu berechnet,
die zwei Paare zusammengeführt (die alte Zeile behielt ihre Meldehistorie und bekam den neuen
Inhalt, die überzähligen Zeilen 526 und 527 wurden per ID gelöscht). Eine geänderte Zeile
aktualisiert jetzt die vorhandene Fahrt (Halte, Zeiten, Rohzeile). Im Plan gilt als Absicherung
je Lok und Zug nur eine Fahrt, die aktive vor der gestrichenen. Test: `pruefe_schluessel`.

## Verspätung: dranbleiben statt ausblenden

Nach der Sollzeit zu gehen wäre der häufigste Fehler — eine verspätete Fahrt verschwände
genau dann, wenn man noch auf sie wartet. Deshalb:

* **Plan und Aufmacher** (`fahrtStand()` in `index.php`) rechnen mit der Echtzeit aus der
  Lage-Momentaufnahme: erwartete Zeit statt Sollzeit, dazu „+12 min“ und das aktuelle Gleis.
  Zustände: `kommt` (auch wenn die Sollzeit vorbei ist), `fertig` (durch, bis 2 Stunden
  danach — die Zeile bleibt mit grünem „durch, 18:12“ stehen) und erst dann `vorbei`.
  Merk- und Wagenreihungsknopf gibt es, solange die Fahrt noch kommt.
* **Beobachtete Fahrten** (`beobachten.takt_fuer`): Das Prüffenster endet nicht bei
  Planankunft + 30 min, sondern verlängert sich um die zuletzt bekannte Verspätung
  (höchstens 4 Stunden).
* **Erinnerung** (`erinnerung.py`): Wird es nach der ersten Erinnerung um mindestens
  `nachmelden_ab_minuten` (5) später oder fällt die Fahrt aus, kommt eine Nachmeldung —
  „Jetzt 18:20: 218 330-9 (+22), Gleis 3“. Der zuletzt gemeldete Stand steht in
  `erinnerungen.spaet`/`.ausfall`, damit dieselbe Verspätung nicht zweimal gemeldet wird.

## Erinnerung vor der Durchfahrt

`erinnerung.py` (Cron alle 5 min) meldet jede aktive Elmshorn-Durchfahrt einmal, sobald sie
im Fenster `vorlauf_minuten` (30) bis `vorlauf_minuten - fenster_minuten` (20) liegt — das
Fenster fängt ausgefallene Durchläufe auf. In der Nachricht stehen Soll-Zeit, Gleis,
Verspätung und Ursache aus den Echtzeitdaten sowie die Wagenreihung als Kurzfassung
(`wagenreihung.kurzfassung`, z. B. „218 453-9, vorne, Abschnitt F“); bei einem Ausfall
entfällt die Wagenreihung und der Betreff lautet „Fällt aus: …“. Verschickt wird nur per
Push (`ersatz_mail=False`). Was schon erinnert wurde, steht in `erinnerungen`.
Einstellungen in `config.json` unter `erinnerung`.

## Selbstüberwachung

`wacht.py` (Cron stündlich, :50) prüft und meldet per Push **und** Mail (der Push-Weg könnte
ja der defekte sein):

* letzter erfolgreicher Forumslauf älter als 3 h, oder die letzten drei Läufe gescheitert,
* Echtzeitlage älter als 45 min (nur zwischen 5 und 23 Uhr — nachts schreibt `stoerung.py`
  nichts fort, weil nichts fährt),
* **Herzschlag** jedes Skripts: `mb.herzschlag()` setzt am Ende jedes Laufs
  `meta.lauf_<name>`; fehlt der Stempel zu lange, gilt der Cron als tot. Die Logdatei taugt
  dafür nicht — ein ruhiger Lauf schreibt nichts hinein (genau darüber wäre die erste
  Fassung fast gestolpert),
* Geräte mit drei oder mehr Push-Fehlern hintereinander.

Jedes Problem wird einmal gemeldet, der Stand steht in `meta.wacht_offen`; ist alles wieder
in Ordnung, kommt eine Entwarnung. `wacht.py --status` zeigt den Befund, `--dry-run` meldet nichts.

## Lok-Steckbrief

Jede Loknummer in der App ist anklickbar und öffnet ein Fenster: Name, Lackierung, Bemerkung,
Betreiber, Zustand, letzter Standort, die heutigen Fahrten, die nächsten und letzten
Durchfahrten in Elmshorn sowie Zahlen (Durchfahrten gesamt und in 30 Tagen, Einsatztage).
Daten liefert `lok.php` (nur Datenbank, Anmeldung nötig, Loknummer streng geprüft).

## Menü und Seiten (seit 17.09.2026)

Gemeinsamer Rahmen in `_layout.php` (Kopf, Stil, Menü). Auf dem Handy liegt das Menü als
Leiste unten, ab 900 px Breite oben in der Kopfleiste. Dateien mit `_` am Anfang sind per
`.htaccess` nicht direkt abrufbar.

| Seite | Adresse | Inhalt |
|---|---|---|
| Start | `./` | nächste 218 durch Elmshorn (mit „in 1 h 20 min“), Kacheln (Elmshorn jetzt, heute durch Elmshorn, 218er im Einsatz, Umkreis 24 h), beobachtete Fahrten, **Plan für Elmshorn** ab heute je Tag: 218-Durchfahrten (vorbei, gestrichen, beobachtet) und Sonderzüge, mit Merk-Knopf |
| Live | `./?s=lage` (`&bf=<Bahnhof>`) | Abfahrten in Elmshorn als kompakte Liste (Zeit mit Verspätung, Linie, Ziel, Ursache, Gleis, Wagenreihung) mit Filtern (Alle · Nicht pünktlich · Sonderzüge · RE6 mit 218, rein im Browser); darüber die Bahnhofsauswahl. Für Elmshorn zusätzlich heute gemeldete Störungen, Sonderzüge und Beiträge im Umkreis |
| Loks | `./?s=loks` | „Wo die Loks stehen“ als dichte Marken je Ort (anklickbar, ● = hat heute einen Umlauf), darunter die besonderen 218er als eine Zeile je Lok (Name, Lage jetzt, heute, Besonderheit) statt als vierspaltige Tabelle; alle 73 hinter einem Aufklapper |
| Tage | `./?s=tage` (`&treffer`) | je Tag eine aufklappbare Karte: Kopf mit Datum, „4× Elmshorn“, Zahl der Fahrten und den beteiligten Loks; darin die Elmshorn-Fahrten grün hervorgehoben als kompakte Zeilen (Zeit, Gleis, Lok, Zug, Lauf, Merk-Knopf), alles andere hinter „N weitere Fahrten ohne Elmshorn“. Vergangene Tage ohne Treffer starten eingeklappt. Der Herkunftsfilter blendet leere Tage aus und klappt Treffer im versteckten Teil auf |
| Verlauf | `./?s=verlauf` (`&art=218|lage|umkreis|test`) | Benachrichtigungsverlauf: jede Meldung mit Uhrzeit, Art, Betreff, Kurztext und Weg (✉ Mail, 🔔 x/y Geräte), aus der Tabelle `meldungen_log` (ein Jahr) |
| Quellen | `./?s=quellen` | Wann welche Quelle zuletzt abgefragt wurde (Forum, Echtzeit, Umkreis, Sonderzüge, Wagenreihung, Wikipedia, Push), mit Takt laut Cron, Kennzeichnung „überfällig“ und Knopf **Jetzt prüfen** je Dienst; darunter die letzten Aufträge mit Ausgabe |
| Logs | `./?s=logs` | Protokolle der Hintergrunddienste, letzte 60–500 Zeilen je Datei |
| Über Zugradar | `./?s=info` | Titelkarte („gemacht von Jarrit“), was die App macht, Zahlen aus der Datenbank (Tage, Durchfahrten, Umläufe, Loks, Umkreisbeiträge, Benachrichtigungen, Geräte), „So funktioniert's“ und die Quellen mit Dank |
| Mehr | `./?s=mehr` | Benachrichtigungen & Geräte (`geraete.php`), App installieren, Erklärung, Abmelden |

**Stand der Forumsbeiträge im Plan**: Jede Tageskarte auf der Startseite zeigt unten, ob die
Listen schon da sind — grüner Punkt „DB Regio steht im Forum · 14 Fahrten, 218er: … · geprüft
22:17“ (verlinkt) oder grauer Punkt „noch nicht im Forum — die Liste kommt meist am Abend
davor“. Heute und morgen stehen immer im Plan, auch ohne Beitrag; in der Kopfzeile steht dann
„Regio-Liste fehlt“ statt „keine 218“. Sonst sieht ein fehlender Beitrag genauso aus wie ein
Tag ohne 218.

**Wagenreihung** (Knopf „🚃 Wagenreihung“ auf Lage und im heutigen Plan): öffnet ein Fenster
in der App — Bahnsteig mit Abschnitten A–F, der Zug als Balken darauf, darunter die Wagenliste
mit Abschnittsbuchstabe. Oben steht, wo die Lok hält und ob sie in Fahrtrichtung vorne oder
hinten ist (aus den Richtungspfeilen der Quelle). Ohne JavaScript führt derselbe Knopf als Link
zur Quelle.

* Woher: Die Wagenreihung gibt es nur aus der internen Schnittstelle von bahn.de. Die sperrt
  diesen Server (`OPS_BLOCKED`) — wird nicht umgangen. Die offizielle Schnittstelle
  RIS::Transports kostet Geld (`db_zugang.py --diagnose` prüft das Abo mit). Gelesen wird
  deshalb die fertige Seite von `dbf.finalrewind.org/carriage-formation` (quelloffen, privat
  betrieben, in dessen robots.txt nicht gesperrt).
* Rücksicht auf diesen Dienst: nur auf Knopfdruck, je Abfahrt höchstens alle 180 Sekunden ein
  Abruf (Tabelle `wagenreihung`), ein Abruf zur Zeit mit 3 Sekunden Abstand, ehrliche Kennung.
* Ablauf: `wagenreihung.php` schreibt die Anfrage in die Tabelle und wartet bis zu 12 Sekunden;
  `wagenreihung.py --warteschlange` (Cron, jede Minute, prüft alle 2 s) holt und zerlegt die
  Seite. Gemessen am 17.09.2026: 2,4–2,8 s für eine neue Fahrt, sofort aus dem Zwischenspeicher.
  Liefert die Bahn nichts, antwortet dbf mit HTTP 500 → „Für diese Fahrt liegt keine
  Wagenreihung vor.“
* Nur DB-Züge (RE, RB, IC …), keine AKN, keine ausgefallenen Züge.

**Regio oder SyltShuttle**: Beide Threadreihen liefern 218er, sind aber verschiedene Züge —
der Regionalverkehr (RE6, DB Regio) und die Autozüge/IC nach Sylt. Die Herkunft steht als
Etikett an jedem Umlauf: im Plan, in den Tageslisten (dort auch als Filter „Alle · RE6 ·
SyltShuttle“, der leere Tageskarten gleich mit ausblendet), in der Lage neben der 218 und im
Lok-Steckbrief. In Mails und Push steht sie hinter der Richtung, z. B.
„[Richtung Sylt, SyltShuttle / IC]“ (`marschbahn.quelle_kurz`).

**`[hidden]` gilt nur mit `!important`**: In `grundstil()` steht
`[hidden] { display:none !important; }`. Ohne diese Regel übersteuert jede eigene
`display`-Angabe (etwa `.offline-marke { display:inline-flex }`) das HTML-Attribut, und
versteckte Elemente bleiben sichtbar — genau so stand die Offline-Marke am 17.09.2026
dauerhaft in der Kopfleiste. Betraf auch den Installieren-Knopf und die Push-Zeile.

**Offline**: Jede besuchte Menüseite liegt als eigene Kopie im Zwischenspeicher
(`sw.js`, network-first). Ohne Netz kommt erst diese Seite, sonst die Übersicht, sonst
`offline.html`. Oben rechts erscheint dann **Offline · Stand HH:MM** (Zeitstempel aus
`<meta name="erzeugt">`, also von wann die Anzeige ist). Erkannt wird das nicht nur über
`navigator.onLine` — das sagt bloß, ob ein Netz da ist —, sondern zusätzlich über einen
kurzen Abruf von `ping.txt` (3 Bytes, am Zwischenspeicher vorbei), auch beim Zurückkehren
zur App. So wird auch ein nicht erreichbarer Server als offline erkannt.

**Androids eigenes Startbild** (vor unserem) kommt aus dem Manifest: `background_color`,
Symbol und Name; abschalten lässt es sich nicht. Seit 17.09.2026 steht dort `#0c1117`, also der
dunkle App-Hintergrund — das Gerät des Nutzers läuft dunkel, und Android kennt nur **eine**
feste Farbe ohne helles/dunkles Gegenstück. Unser eigener Startbildschirm folgt dagegen dem
Handy-Design. **Achtung:** Eine Manifest-Änderung übernimmt ein installiertes WebAPK erst nach
Chromes nächstem Abgleich (Tage) oder sofort nach Neuinstallation der App.

**Einstellungen** (Mehr → Einstellungen, alles in `localStorage`, je Gerät): Dauer des
Startbildschirms (aus bis 4 s, Voreinstellung 1,6 s — das Kopfskript setzt daraus
`--splash-dauer`, die CSS-Animation blendet danach aus), Startbildschirm auch im Browser
statt nur in der App, Animationen an/aus (`data-motion="aus"` wirkt wie
`prefers-reduced-motion`) und ein Knopf zum Ausprobieren.

**Herkunft je Lok** (Loks-Seite): Aus den Umläufen der letzten 60 Tage steht an jeder Lok, ob
sie im Regionalverkehr (**RE6**), vor den Autozügen nach Sylt (**Shuttle**) oder in beidem
gefahren ist — an den Marken bei den Standorten und in der Liste der besonderen 218er.

**Bewegung** (alles in `_layout.php`): Startbildschirm mit Lok, Name und fahrendem Licht auf
dem Gleis — nur in der installierten App und nur einmal je Sitzung (`sessionStorage`), er
blendet sich auch ohne JavaScript per CSS-Animation wieder aus. Beim Seitenwechsel läuft oben
ein dünner Ladebalken (Seiten kommen frisch vom Server), er wird bei `pageshow` zurückgesetzt,
damit er nach dem Zurück-Knopf nicht hängen bleibt. Inhalte tauchen gestaffelt auf, Kacheln und
Zeilen geben beim Antippen leicht nach, im Fenster laufen Wartebalken statt eines nackten
Texts. Wer im System **Bewegung reduzieren** eingestellt hat, sieht nichts davon — eine
einzige `prefers-reduced-motion`-Regel schaltet alle Animationen und Übergänge ab.

Merk-Knöpfe schicken `zurueck` mit, nach dem Speichern geht es auf dieselbe Seite zurück.
Der Service Worker legt jede Menüseite als eigene Offline-Kopie ab.

## Die Webseite

Gemeinsame Einstellungen (Datenbank, Passwort-Hash, Merk-Schlüssel, Zeitzone)
stehen in `_kukas.php` und werden von `index.php` und `merken.php` eingebunden.
Die Zeitzone ist wichtig: Ohne `date_default_timezone_set('Europe/Berlin')`
rechnete PHP in UTC, die Datenbank aber in Ortszeit — die Kennzeichnung
„veraltet" konnte dadurch nie greifen.


`/marschbahn` zeigt drei Blöcke:

1. **Lage in Elmshorn** — Störungsbanner, nächste Fahrten mit Verspätung, Ausfall
   und Ursache, dazu Stand und Quelle.
2. **Heute verschickt** — welche Meldungen heute rausgingen, in Klartext.
3. **Wo die Loks stehen** — Standorte des neuesten Tages, 218er hervorgehoben.
4. **Im Umkreis von Elmshorn** — Forenbeiträge mit Ort und Entfernung.
5. **Die Tage** — 218-Umläufe je Tag wie bisher.

Ganz oben steht die **nächste bevorstehende Durchfahrt** mit Zeit und Gleis —
auf dem Handy meist das Einzige, was man wissen will.

Das Layout ist auf schmale Schirme ausgelegt: Unter 700 px werden aus den Tabellen
gestapelte Karten. Jede Zelle trägt dafür ein `data-l`-Etikett, das per CSS als
Beschriftung davor gesetzt wird — ohne JavaScript und ohne doppeltes Markup.
Leere Zellen blendet `td:empty` dabei aus, damit keine Geisterzeilen entstehen.

Die Seite fragt **keine API selbst ab**. `stoerung.py` legt bei jedem Lauf eine
Momentaufnahme in der Tabelle `lage` ab (eine Zeile, `id = 1`), die Seite liest
nur die. Sonst würde jeder Seitenaufruf die APIs anfassen und der Seitenaufbau
von deren Antwortzeit abhängen. Ist die Momentaufnahme älter als 30 Minuten,
weist die Seite sie als *veraltet* aus — sonst sähe ein hängender Cron aus wie
eine ruhige Strecke.

## Tabellen in `zugradar_db`

`tage` — ein Datensatz je Thread (bis zu zwei pro Tag, Schlüssel `thread_id`) ·
`laeufe` — die gefundenen Elmshorn-Läufe · `tagesmeldung` — welcher Tag schon
gemeldet wurde · `stoerungen` — welche Störungsmeldung schon raus ist
(Dublettenschutz) · `lage` — Momentaufnahme für die Webseite · `standorte` — wo die Loks stehen · `lok_namen` — Eigennamen · `umlaeufe` — alle 218-Fahrten · `tages_loks` — Stand für Nachträge · `interesse` — vorgemerkte Fahrten · `stationen` — EVA-Nummern · `sonderzuege` — gemeldete Sonderzüge · `besondere_loks` — Namen/Lackierungen laut Wikipedia · `sichtungen` — geprüfte Forenbeiträge samt Beschreibung · `push_geraete` — Push-Abos der Geräte · `push_warteschlange` — zu sendende und gesendete Push-Nachrichten (30 Tage) · `wagenreihung` — Zwischenspeicher der Wagenreihungen (7 Tage) ·
`orte` — Geokodierungs-Gedächtnis ·
`laeufe_log` — Protokoll je Durchlauf · `meta` — Schlüssel/Wert.

## Versionsnummer

Unten im Seitenfuß („Version 1.19“, verlinkt auf „Über Zugradar“) und auf der Über-Seite mit
Stand. Sie kommt aus `VERSION` in `sw.js` ('v19' → 1.19), Stand ist die Änderungszeit von
`sw.js` (`zugradar_version()` in `_layout.php`). Also bei jeder Änderung nur `VERSION` in
`sw.js` erhöhen — Handy-Update und angezeigte Nummer bleiben gleich.

## Schnellstart vom Homescreen

Langes Drücken aufs App-Symbol (Android; iOS kennt das für Web-Apps nicht) zeigt vier
Einträge aus `shortcuts` im Manifest: **Nächste 218** (`/zugradar/#naechste`, der Aufmacher
der Startseite), **Karte**, **In deiner Nähe** (`?naehe=1#naehe`, startet die Suche) und
**Live Elmshorn**. Symbole `kurz-*.png` (96 px, weiß auf Rot) erzeugt
`icons_erzeugen.py --kurzbefehle`. Chrome übernimmt Manifest-Änderungen in eine installierte
App erst bei seiner nächsten Prüfung (bis zu einem Tag) — oder nach Neuinstallation.

## Beobachtete Fahrten: oben auf der Startseite, danach im Archiv

Solange eine Fahrt beobachtet wird, steht sie **ganz oben auf der Startseite** (vor der nächsten
218) als Karte mit Lok, Zug und dem aktuellen Stand (`beobachtungsLage()` in `index.php`):
„Abfahrt 15:25 +6 in Westerland(Sylt) · durch Elmshorn 18:00“ → „unterwegs · durch Elmshorn
um 18:06 +6 · auf dem Weg nach Hamburg-Altona“ → „**Elmshorn durch** · auf dem Weg nach
Hamburg-Altona · an 18:28 +6“ → „angekommen in Hamburg-Altona um 18:28“ (bzw. „fällt aus“).
Die Elmshorn-Zeit kommt aus `umlaeufe.elmshorn_zeit`, die Verspätung aus `interesse.stand`
(auf 255 Zeichen gekürzt — deshalb per Muster gelesen, nicht als JSON). Antippen öffnet die
Fahrtseite.

**Eine Stunde nach der Ankunft am Endbahnhof** (Planankunft + letzte bekannte Verspätung; bei
Ausfall die Planankunft) legt `beobachten.py` die Fahrt ins Archiv: `aktiv=0`,
`archiviert_am`, `archiv_grund='angekommen'`. „Nicht mehr beobachten“ (Webseite, Push-Knopf)
setzt `archiv_grund='entfernt'`; was zwei Tage alt ist, geht als `abgelaufen` hinein. Die
Startseite wendet dieselbe Regel an (der Dienst läuft nur alle fünf Minuten). Wieder
beobachten holt eine Fahrt aus dem Archiv zurück. **Archiv**: `./?s=archiv` (Mehr → Archiv,
„Archiv →“ über der Karte) — je Tag Lok, Zug, Lauf, Elmshorn-Zeit, Ergebnis (pünktlich / +n min /
ausgefallen) und Grund. Test: `pruefe_archiv` in `test_parser.py`.

## Lok-Check vor der Durchfahrt

Die Erinnerung 30 Minuten vor der Elmshorn-Durchfahrt holt ohnehin die Wagenreihung; seit
18.09.2026 vergleicht `erinnerung.lok_check()` die darin genannte Lok mit der geplanten
(Ziffernvergleich, „218 453-9“ → 218453): **bestätigt** („Lok bestätigt: 218 453-9 laut
Wagenreihung“), **andere 218** (Zusatz im Titel „— andere 218: 218 390-3“) oder **keine 218**
(Titel „Lok-Wechsel: RE 11029 18:00 mit 245 statt 218 453-9“). Nennt die Wagenreihung keine
Lok, sagt der Check nichts. Test: `pruefe_lok_check`.

## Lok-Suche und „Auf der Karte“

* **Tage**: Suchfeld über den Filtern — Nummer („330“, „218330“) oder Name („Konrad“), mit
  Vorschlägen aller Loks der Seite. Zeigt nur passende Fahrten, blendet Tage ohne Treffer aus,
  klappt Treffer auf, „39 Fahrten an 6 Tagen“. Vorausgefüllt über `./?s=tage&lok=…`. Die Zeilen
  tragen dafür `data-such`/`data-such-lok` — **nicht** `data-lok`: das öffnet seitenweit den
  Steckbrief.
* **Steckbrief** (überall beim Antippen einer Lok): Knöpfe „🗺️ Auf der Karte“ und „📅 Alle
  Fahrten“.
* **Karte** `./?s=karte&lok=218 330-9` (oder Name): sucht die Lok live als Zug (über die
  Zugnummern des Plans), sonst laut Plan, sonst abgestellt — zoomt hin und öffnet das Fenster;
  ist die Ebene ausgeblendet, wird sie eingeblendet. Sonst Hinweis „Heute weder unterwegs noch
  im Plan noch abgestellt gemeldet“ mit Links.

## Keine alten Meldungen beim Einschalten

Push-Dienste heben eine Nachricht für ein ausgeschaltetes Gerät auf und liefern sie beim
Start nach — auf dem Rechner kam so morgens der halbe Vortag an. Dagegen drei Dinge:

* **Haltbarkeit (TTL)**: Standard 3 h statt 12 h, je Art kürzer (`push.TTL_JE_ART`):
  Erinnerung 20 min, Live 15 min, Beobachtung 30 min, Störung/Entwarnung 1 h, Ausfall 2 h,
  Test 2 min, Selbstüberwachung 6 h. Danach wirft der Push-Dienst sie weg. `melde()` reicht
  die Art dafür an `push.an_alle(..., art)` durch.
* **Thema (Topic, RFC 8030)**: Ein neuer Push mit demselben Thema ersetzt den noch nicht
  zugestellten alten — aus der Markierung, sonst der Art (`push.thema_aus`, ≤ 32 Zeichen,
  lange mit SHA-256-Kürzel eindeutig gehalten). Von zehn liegengebliebenen Live-Meldungen
  kommt so nur die neueste an.
* **sw.js**: Jede Nachricht trägt `gueltig_bis`. Kommt sie später an, erscheint sie nicht
  einzeln, sondern still als eine Zeile „Zugradar: ältere Meldungen“ (Tag `veraltet`) mit
  Verweis auf den Verlauf. Chrome verlangt zu jedem Push eine Meldung — deshalb eine
  gesammelte statt gar keiner.

Test: `pruefe_haltbarkeit` in `test_parser.py`; am 25.09.2026 mit einer echten Testmeldung an
Windows/Chrome geprüft (HTTP 201, Topic wird angenommen).

## Karte ohne Netz

Die Karte funktioniert offline mit dem zuletzt geholten Stand — nicht als Notbehelf, sondern
weil im Funkloch genau dann jemand draufschaut. Der Service Worker (`sw.js`) hält dafür drei
eigene Speicher, die eine neue Version **nicht** wegwirft:

* `zugradar-karte`: Leaflet (JS und CSS von cdnjs, beim Einbau mit `mode: 'cors'` geholt) —
  ohne das bliebe die Karte offline ganz leer,
* `zugradar-daten`: die letzten Antworten von `zuege.php`, `netz.php`, `halte.php`,
  `grenzen.php` und `tafel.php` (erst Netz, dann Speicher),
* `zugradar-kacheln`: schon einmal gesehene Kartenkacheln aller Kartentypen, höchstens 400
  (die ältesten fallen heraus). Nie besuchte Gegenden bleiben grau, die Linien liegen aber da.

Über der Karte steht dann eine Zeile: „📴 Offline — gespeicherter Stand von 14:21“ bzw.
„⚠ Züge nicht aktuell …“, wenn die Daten älter als drei Minuten sind, mit Zusatz „(die Züge
sind längst weitergefahren)“ ab einer Stunde. Die Züge selbst verschwinden von allein: Was
laut Fahrplan angekommen ist, wird nicht mehr gezeichnet.


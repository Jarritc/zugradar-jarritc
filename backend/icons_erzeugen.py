#!/usr/bin/env python3
"""
Erzeugt Favicon, App-Icons und Mail-Logo für /zugradar aus einem Bild.

    icons_erzeugen.py BILD        aus einem eigenen Bild (z. B. das Lok-Emblem)
    icons_erzeugen.py --platzhalter   gezeichneter Platzhalter im selben Stil
    icons_erzeugen.py --kurzbefehle   nur die Symbole der Schnellstart-Einträge (Manifest)

Erwartet wird ein rundes Emblem auf einfarbigem Hintergrund. Der Kreis wird am
Hintergrund entlang ausgeschnitten, außen transparent gemacht und in alle Größen
gebracht:

    favicon.ico (16/32/48), favicon-32.png   Browser-Tab
    icon-192.png, icon-512.png               App (transparent um den Kreis)
    icon-maskable-512.png                    Android — Kreis in der sicheren Zone
    apple-touch-icon.png (180)               iOS Home-Bildschirm, ohne Transparenz
    logo-mail.png (96)                       Kopf der Mails
    badge-96.png                             Android-Statusleiste bei Push (nur Form, weiß)

Danach wird die Versionsnummer im Service Worker erhöht, damit installierte Apps
das neue Icon übernehmen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ZIEL = Path("/var/www/html/jarritc.de/zugradar")
HINTERGRUND_MASKABLE = (201, 205, 212, 255)     # das helle Grau des Emblems


def emblem_ausschneiden(bild: Image.Image) -> Image.Image:
    """Quadrat um das Emblem, außerhalb des Kreises transparent."""
    rgb = bild.convert("RGB")
    b, h = rgb.size
    # Hintergrundfarbe aus den vier Ecken schätzen
    ecken = [rgb.getpixel(p) for p in ((2, 2), (b - 3, 2), (2, h - 3), (b - 3, h - 3))]
    grund = tuple(sum(c[i] for c in ecken) // 4 for i in range(3))
    maske = Image.new("L", rgb.size, 0)
    px, mp = rgb.load(), maske.load()
    for y in range(0, h):
        for x in range(0, b):
            r, g, bl = px[x, y]
            if abs(r - grund[0]) + abs(g - grund[1]) + abs(bl - grund[2]) > 40:
                mp[x, y] = 255
    kasten = maske.getbbox() or (0, 0, b, h)
    x0, y0, x1, y1 = kasten
    seite = max(x1 - x0, y1 - y0)
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    quadrat = (mx - seite // 2, my - seite // 2, mx - seite // 2 + seite, my - seite // 2 + seite)
    teil = bild.convert("RGBA").crop(quadrat)
    # Weiche Kreismaske (4-fach gerechnet, dann verkleinert = glatter Rand)
    gross = Image.new("L", (seite * 4, seite * 4), 0)
    # Etwas nach innen versetzt: Am Rand mischt sich sonst Hintergrundgrau in den
    # Ring, und das Icon bekommt einen hellen Saum.
    innen = max(4, int(seite * 4 * 0.012))
    ImageDraw.Draw(gross).ellipse((innen, innen, seite * 4 - innen, seite * 4 - innen), fill=255)
    kreis = gross.resize((seite, seite), Image.LANCZOS)
    teil.putalpha(kreis)
    return teil


def platzhalter(groesse: int = 1024) -> Image.Image:
    """Rotes Ringemblem mit "218" — bis das echte Bild da ist."""
    g = groesse * 4
    bild = Image.new("RGBA", (g, g), (0, 0, 0, 0))
    d = ImageDraw.Draw(bild)
    d.ellipse((0, 0, g - 1, g - 1), fill=(143, 32, 41, 255))
    rand = int(g * 0.055)
    d.ellipse((rand, rand, g - rand, g - rand), fill=(125, 190, 226, 255))
    try:
        schrift = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(g * 0.30))
    except OSError:
        schrift = ImageFont.load_default()
    text = "218"
    kasten = d.textbbox((0, 0), text, font=schrift)
    tb, th = kasten[2] - kasten[0], kasten[3] - kasten[1]
    d.rounded_rectangle((g * 0.16, g * 0.30, g * 0.84, g * 0.70), radius=int(g * 0.06), fill=(196, 38, 45, 255))
    d.rectangle((g * 0.16, g * 0.47, g * 0.84, g * 0.53), fill=(238, 226, 200, 255))
    d.text(((g - tb) / 2 - kasten[0], (g - th) / 2 - kasten[1]), text, font=schrift, fill=(255, 255, 255, 255))
    return bild.resize((groesse, groesse), Image.LANCZOS)


def badge(groesse: int = 96) -> Image.Image:
    """Android zeigt in der Statusleiste nur die Deckkraft des Bildes, einfarbig.
    Ein Foto-Emblem wird dort zum Klecks — deshalb ein Ring mit "218"."""
    bild = Image.new("RGBA", (groesse, groesse), (0, 0, 0, 0))
    zeichnen = ImageDraw.Draw(bild)
    rand = groesse * 0.06
    zeichnen.ellipse((rand, rand, groesse - rand, groesse - rand), outline="white", width=round(groesse * 0.08))
    schrift = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", round(groesse * 0.30))
    zeichnen.text((groesse / 2, groesse / 2), "218", fill="white", font=schrift, anchor="mm")
    return bild


def schreibe_alle(emblem: Image.Image) -> None:
    ZIEL.mkdir(parents=True, exist_ok=True)
    groesse = lambda n: emblem.resize((n, n), Image.LANCZOS)
    groesse(192).save(ZIEL / "icon-192.png")
    groesse(512).save(ZIEL / "icon-512.png")
    groesse(32).save(ZIEL / "favicon-32.png")
    groesse(96).save(ZIEL / "logo-mail.png")
    groesse(256).save(ZIEL / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    badge().save(ZIEL / "badge-96.png")

    # Maskable: Android schneidet bis zu 20 % weg — Kreis deshalb auf 76 % verkleinert
    mask = Image.new("RGBA", (512, 512), HINTERGRUND_MASKABLE)
    innen = groesse(390)
    mask.alpha_composite(innen, ((512 - 390) // 2, (512 - 390) // 2))
    mask.save(ZIEL / "icon-maskable-512.png")

    # iOS mag keine Transparenz auf dem Home-Bildschirm
    apple = Image.new("RGBA", (180, 180), HINTERGRUND_MASKABLE)
    apple.alpha_composite(groesse(164), (8, 8))
    apple.convert("RGB").save(ZIEL / "apple-touch-icon.png")

    # Service-Worker-Version erhöhen, damit installierte Apps die neuen Icons holen
    sw = ZIEL / "sw.js"
    if sw.exists():
        text = sw.read_text(encoding="utf-8")
        text = re.sub(r"const VERSION = 'v(\d+)'", lambda m: f"const VERSION = 'v{int(m.group(1)) + 1}'", text)
        sw.write_text(text, encoding="utf-8")
    for f in ("favicon.ico", "favicon-32.png", "icon-192.png", "icon-512.png",
              "icon-maskable-512.png", "apple-touch-icon.png", "logo-mail.png", "badge-96.png"):
        print(f"  {f:<24}{(ZIEL / f).stat().st_size:>8} Bytes")


ROT = (143, 32, 41, 255)
WEISS = (255, 255, 255, 255)


def kurzbefehl_symbole(groesse: int = 96) -> None:
    """Symbole für die Schnellstart-Einträge (langes Drücken aufs App-Symbol): weißes
    Zeichen auf rotem Kreis, 4-fach gezeichnet und verkleinert (glatte Kanten)."""
    f = 4
    n = groesse * f
    schrift = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def leer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
        bild = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        zeichnen = ImageDraw.Draw(bild)
        zeichnen.ellipse((0, 0, n - 1, n - 1), fill=ROT)
        return bild, zeichnen

    # Karte: gefaltete Karte mit Strecke
    bild, z = leer()
    k = [(0.24, 0.30), (0.42, 0.24), (0.58, 0.30), (0.76, 0.24), (0.76, 0.70), (0.58, 0.76), (0.42, 0.70), (0.24, 0.76)]
    z.polygon([(x * n, y * n) for x, y in k], fill=WEISS)
    for x in (0.42, 0.58):
        z.line([(x * n, (0.25 if x == 0.42 else 0.31) * n), (x * n, (0.70 if x == 0.42 else 0.75) * n)], fill=ROT, width=f * 2)
    z.line([(0.30 * n, 0.64 * n), (0.45 * n, 0.46 * n), (0.56 * n, 0.55 * n), (0.70 * n, 0.36 * n)], fill=ROT, width=f * 4, joint="curve")
    bild.resize((groesse, groesse), Image.LANCZOS).save(ZIEL / "kurz-karte.png")

    # In deiner Nähe: Standort-Nadel
    bild, z = leer()
    z.ellipse((0.32 * n, 0.20 * n, 0.68 * n, 0.56 * n), fill=WEISS)
    z.polygon([(0.34 * n, 0.44 * n), (0.66 * n, 0.44 * n), (0.50 * n, 0.80 * n)], fill=WEISS)
    z.ellipse((0.43 * n, 0.31 * n, 0.57 * n, 0.45 * n), fill=ROT)
    bild.resize((groesse, groesse), Image.LANCZOS).save(ZIEL / "kurz-naehe.png")

    # Nächste 218: die Zahl
    bild, z = leer()
    z.text((n / 2, n / 2), "218", font=ImageFont.truetype(schrift, int(n * 0.34)), fill=WEISS, anchor="mm")
    bild.resize((groesse, groesse), Image.LANCZOS).save(ZIEL / "kurz-218.png")

    # Live: Punkt mit zwei Funkbögen
    bild, z = leer()
    m = n / 2
    z.ellipse((m - 0.07 * n, m - 0.07 * n + 0.08 * n, m + 0.07 * n, m + 0.07 * n + 0.08 * n), fill=WEISS)
    for r in (0.17, 0.29):
        z.arc((m - r * n, m - r * n + 0.08 * n, m + r * n, m + r * n + 0.08 * n), 215, 325, fill=WEISS, width=f * 5)
    bild.resize((groesse, groesse), Image.LANCZOS).save(ZIEL / "kurz-live.png")
    for name in ("kurz-karte.png", "kurz-naehe.png", "kurz-218.png", "kurz-live.png"):
        print(f"  {name:<24}{(ZIEL / name).stat().st_size:>8} Bytes")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    if sys.argv[1] == "--kurzbefehle":
        kurzbefehl_symbole()
        return 0
    if sys.argv[1] == "--platzhalter":
        emblem = platzhalter()
    else:
        emblem = emblem_ausschneiden(Image.open(sys.argv[1]))
    schreibe_alle(emblem)
    return 0


if __name__ == "__main__":
    sys.exit(main())

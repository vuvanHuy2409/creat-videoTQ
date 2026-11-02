#!/usr/bin/env python3
"""Developer-only refresh of bundled OFL fonts (requires requests, fonttools).

Pin --revision to the commit recorded in assets/fonts/sources.json to reproduce
an existing bundle. All derived static instances use AutoDub family names to
respect reserved font names and avoid collisions with system-installed fonts.
"""
import argparse
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONTS = [
    ("Phudu", "phudu/Phudu[wght].ttf", 900),
    ("Be Vietnam Pro", "bevietnampro/BeVietnamPro-ExtraBold.ttf", 800),
    ("Montserrat", "montserrat/Montserrat[wght].ttf", 800),
    ("Inter", "inter/Inter[opsz,wght].ttf", 700),
    ("Roboto", "roboto/Roboto[wdth,wght].ttf", 700),
    ("Roboto Condensed", "robotocondensed/RobotoCondensed[wght].ttf", 700),
    ("Oswald", "oswald/Oswald[wght].ttf", 700),
    ("Barlow Condensed", "barlowcondensed/BarlowCondensed-Bold.ttf", 700),
    ("Anton", "anton/Anton-Regular.ttf", 400),
    ("Archivo", "archivo/Archivo[wdth,wght].ttf", 900),
    ("Noto Sans", "notosans/NotoSans[wdth,wght].ttf", 700),
    ("Noto Sans JP", "notosansjp/NotoSansJP[wght].ttf", 700),
]


def fetch(url):
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    return response.content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="google/fonts Git commit; defaults to main's current commit")
    args = parser.parse_args()
    revision = args.revision
    if not revision:
        response = requests.get("https://api.github.com/repos/google/fonts/commits/main", timeout=30)
        response.raise_for_status()
        revision = response.json()["sha"]
    target = Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    (target / "ttf").mkdir(exist_ok=True)
    (target / "licenses").mkdir(exist_ok=True)
    def download(spec):
        name, path, weight = spec
        source = f"https://raw.githubusercontent.com/google/fonts/{revision}/ofl/{path}"
        binary = fetch(source)
        license_url = f"https://raw.githubusercontent.com/google/fonts/{revision}/ofl/{path.split('/')[0]}/OFL.txt"
        license_text = fetch(license_url)
        return spec, source, binary, license_url, license_text
    # Download independently, then convert and publish one family at a time.
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads = list(pool.map(download, FONTS))
    manifest = []
    for (name, path, weight), source, binary, license_url, license_text in downloads:
        font = TTFont(io.BytesIO(binary), recalcTimestamp=False)
        if "fvar" in font:
            axes = {axis.axisTag: axis.defaultValue for axis in font['fvar'].axes}
            axes['wght'] = weight
            font = instantiateVariableFont(font, axes, inplace=True)
        family = f"AutoDub {name}"
        style = {400: "Regular", 700: "Bold", 800: "ExtraBold", 900: "Black"}[weight]
        names = {1: family, 2: style, 3: f"{family}-{style}", 4: f"{family} {style}",
                 6: f"AutoDub{name.replace(' ', '')}-{style}", 16: family, 17: style}
        for record in font['name'].names:
            if record.nameID in names:
                record.string = names[record.nameID].encode(record.getEncoding())
        for key, value in names.items():
            font['name'].setName(value, key, 3, 1, 0x409)
        slug = name.replace(' ', '')
        filename = f"{slug}-{style}.ttf"
        output = target / "ttf" / filename
        font.save(output)
        font.close()
        (target / "licenses" / f"{slug}-OFL.txt").write_bytes(license_text)
        manifest.append({"name": name, "family": family, "file": filename, "weight": weight,
                         "source": source, "source_sha256": hashlib.sha256(binary).hexdigest(),
                         "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                         "license": "SIL Open Font License 1.1", "license_url": license_url})
        print(f"Bundled {name}: {output.stat().st_size:,} bytes", flush=True)
    (target / "sources.json").write_text(json.dumps({"google_fonts_revision": revision,
        "note": "Derived static instances renamed to AutoDub families; original OFL licenses included.",
        "fonts": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned google/fonts revision: {revision}", flush=True)


if __name__ == '__main__':
    main()

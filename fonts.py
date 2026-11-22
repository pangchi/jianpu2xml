"""Font discovery shared by the OMR glyph trainer and the preview renderer."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts",
             "/Library/Fonts", "/System/Library/Fonts", "C:/Windows/Fonts"]

# Latin fonts whose digits look like typical printed jianpu
LATIN_WANTED = ["dejavusans.ttf", "dejavusans-bold.ttf", "dejavuserif.ttf", "dejavuserif-bold.ttf",
                "dejavusanscondensed.ttf", "dejavusanscondensed-bold.ttf",
                "liberationsans-regular.ttf", "liberationsans-bold.ttf",
                "liberationserif-regular.ttf", "liberationserif-bold.ttf",
                "liberationsansnarrow-regular.ttf", "liberationsansnarrow-bold.ttf",
                "freesans.ttf", "freesansbold.ttf", "freeserif.ttf", "freeserifbold.ttf",
                "arial.ttf", "arialbd.ttf", "times.ttf", "timesbd.ttf", "verdana.ttf", "tahoma.ttf",
                "helvetica.ttc", "roboto-regular.ttf", "roboto-bold.ttf",
                "notosans-regular.ttf", "notosans-bold.ttf"]
CJK_HINTS = ["notosanscjk-regular", "notosanscjksc", "notosanssc", "wqy-microhei", "wqy-zenhei",
             "droidsansfallback", "msyh", "simhei", "simsun", "pingfang", "notoserifcjk-regular",
             "arphic", "ukai", "uming"]


@lru_cache(maxsize=1)
def _all_font_files():
    out = []
    for d in FONT_DIRS:
        p = Path(os.path.expanduser(d))
        if p.is_dir():
            for f in p.rglob("*"):
                if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    out.append(f)
    return out


@lru_cache(maxsize=1)
def latin_fonts():
    files = {f.name.lower(): f for f in _all_font_files()}
    return [str(files[n]) for n in LATIN_WANTED if n in files]


@lru_cache(maxsize=1)
def cjk_font():
    files = _all_font_files()
    for hint in CJK_HINTS:
        for f in files:
            if hint in f.name.lower():
                return str(f)
    return None


def ui_font(bold=False):
    names = ["dejavusans-bold.ttf", "liberationsans-bold.ttf", "arialbd.ttf"] if bold else \
            ["dejavusans.ttf", "liberationsans-regular.ttf", "arial.ttf"]
    files = {f.name.lower(): f for f in _all_font_files()}
    for n in names:
        if n in files:
            return str(files[n])
    return None

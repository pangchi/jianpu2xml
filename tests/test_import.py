"""MusicXML import: jianpu -> MusicXML -> jianpu round trips.

Run:  python tests/test_import.py   (or pytest)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jianpu_core as core  # noqa: E402
import musicxml_import as mi  # noqa: E402

ODE = (ROOT / "examples" / "ode_to_joy.txt").read_text(encoding="utf-8")


def sounding(xml):
    notes = re.findall(r"<note>.*?</note>", xml.replace("\n", ""))
    return [re.sub(r"<beam.*?</beam>|<lyric.*?</lyric>|<notations>.*?</notations>|<tie [^>]*/>", "", n)
            for n in notes]


def lyrics(text):
    return [tuple(sorted(n.lyrics.items())) for m in core.parse(text).measures for n in m.notes() if n.lyrics]


def test_round_trip_same_music_lines_and_lyrics():
    for t in (0, 2, -3, 5):
        xml = core.convert(ODE, transpose=t).xml
        back, warns, info = mi.import_bytes(xml.encode())
        assert sounding(core.convert(back).xml) == sounding(xml), t
        assert not core.convert(back).warnings
    back, _, _ = mi.import_bytes(core.convert(ODE).xml.encode())
    assert lyrics(back) == lyrics(ODE)
    # line breaks survive too (written as <print new-system> on export)
    assert len([ln for ln in back.splitlines() if "|" in ln]) == len([ln for ln in ODE.splitlines() if "|" in ln])


def test_mxl_and_headers():
    back, _, info = mi.import_bytes(core.mxl_bytes(core.convert(ODE, meta={"subtitle": "Sub"}).xml))
    assert "title: Ode to Joy" in back and "subtitle: Sub" in back and "tempo: 96" in back
    assert info["measures"] == 13


def test_triplets_ties_chords_key_change():
    src = "key: Bb\n<6m/5> 1 3{ 2_ 3_ 4_ } 5~ | 5 #4_. 5__ [key=G] <4> 6 - |]\n"
    back, _, _ = mi.import_bytes(core.convert(src).xml.encode())
    assert "3{" in back and "~" in back and "<6m/5>" in back and "[key=G]" in back and "#4_." in back
    assert sounding(core.convert(back).xml) == sounding(core.convert(src).xml)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

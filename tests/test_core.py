"""Run:  python -m pytest tests   (or just: python tests/test_core.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jianpu_core as core  # noqa: E402


def pitches(xml):
    import re
    return re.findall(r"<step>(\w)</step>(?:<alter>(-?\d)</alter>)?<octave>(\d)</octave>", xml)


def test_transpose_up_major_second():
    r = core.convert("key: D\ntime: 4/4\n1 3 5 1' |]", transpose=2)
    assert r.new_key == "E"
    assert pitches(r.xml) == [("E", "", "4"), ("G", "1", "4"), ("B", "", "4"), ("E", "", "5")]
    assert "<fifths>4</fifths>" in r.xml


def test_durations_and_measure_check():
    r = core.convert("key: C\n1_ 2_ 3. 4_ 5 |]")
    assert not r.warnings
    r = core.convert("key: C\n1 2 3 |\n1 2 3 4 |]")
    assert r.measure_count == 2 and 'implicit="yes"' in r.xml     # pickup
    r = core.convert("key: C\n1 2 3 4 | 1 2 3 | 1 2 3 4 |]")
    assert any("needs 4" in m for _, m in r.warnings)


def test_tie_slur_lyrics_chords():
    r = core.convert("key: C\n<6m/5> 1~ 1 (2 3) |]\nw: 明 天 好", transpose=2)
    assert r.xml.count('<tied type="start"/>') == 1
    assert '<slur type="start"' in r.xml and "<text>好</text>" in r.xml
    assert "<root-step>B</root-step>" in r.xml and "<bass-step>A</bass-step>" in r.xml


def test_repeats_and_endings():
    r = core.convert("key: C\n|: 1 2 3 4 |[1] 5 - - - :|[2] 1 - - - |]")
    assert '<repeat direction="forward"/>' in r.xml and '<repeat direction="backward"/>' in r.xml
    assert '<ending number="1" type="stop"/>' in r.xml
    assert '<ending number="2" type="discontinue"/>' in r.xml


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

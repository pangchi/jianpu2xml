"""Playing order (repeats, endings, D.S./D.C., coda, Fine), timing and MIDI output.

Run:  python tests/test_playback.py   (or pytest)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jianpu_core as core  # noqa: E402
import playback as pb  # noqa: E402


def order(text):
    s = core.parse("key: C\n" + text)
    return [s.measures[i].number for i in pb.order(s)]


def test_repeats_and_endings():
    assert order("|: 1 1 1 1 | 2 2 2 2 |[1] 3 3 3 3 :|[2] 4 4 4 4 ||\n5 5 5 5 |]") == list("1231245")
    assert order("1 1 1 1 | 2 2 2 2 :| 3 3 3 3 |]") == list("12123")
    assert order("|: 1 1 1 1 |[1,2] 2 2 2 2 :|[3] 3 3 3 3 |]") == list("121213")


def test_jumps():
    assert order("1 1 1 1 | [segno] 2 2 2 2 | 3 3 3 3 [tocoda] | 4 4 4 4 [dsalcoda] ||\n[coda] 5 5 5 5 |]") \
        == list("1234235")
    assert order("1 1 1 1 | 2 2 2 2 [fine] || 3 3 3 3 | 4 4 4 4 [dcalfine] |]") == list("123412")


def test_ties_timing_transpose_and_midi():
    ev = pb.events(core.parse("key: D\ntempo: 120\n1 - 3 3~ | 3 5_ 5_ <5> 1' - |]"), transpose=2)
    assert [n["midi"] for n in ev["notes"]] == [64, 68, 68, 71, 71, 76]   # E G# G# B B E; tied G# merged
    assert [n["d"] for n in ev["notes"]][:3] == [2.0, 1.0, 2.0]
    assert ev["bpm"] == 120 and ev["length"] == 8.0
    assert ev["chords"][0]["midi"][1] % 12 == 11                         # <5> in E = B
    mid = pb.midi_bytes(ev, title="t")
    assert mid[:4] == b"MThd" and mid.count(b"MTrk") == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

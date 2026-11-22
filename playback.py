"""playback - turn parsed jianpu into timed note events (repeats, endings, D.S./D.C., coda
and Fine unrolled) for the browser player, and write Standard MIDI files.

    from playback import events, midi_bytes
    ev = events(core.parse(text), transpose=2)
    open("song.mid", "wb").write(midi_bytes(ev))
"""
from __future__ import annotations

import struct
from fractions import Fraction

import jianpu_core as core
from version import __version__

DEFAULT_BPM = 90
CHORD_TONES = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "dominant": (0, 4, 7, 10), "major-seventh": (0, 4, 7, 11),
    "minor-seventh": (0, 3, 7, 10), "diminished": (0, 3, 6), "diminished-seventh": (0, 3, 6, 9),
    "augmented": (0, 4, 8), "augmented-seventh": (0, 4, 8, 10), "half-diminished": (0, 3, 6, 10),
    "suspended-fourth": (0, 5, 7), "suspended-second": (0, 2, 7), "major-sixth": (0, 4, 7, 9),
    "minor-sixth": (0, 3, 7, 9), "dominant-ninth": (0, 4, 7, 10, 14), "major-ninth": (0, 4, 7, 11, 14),
    "minor-ninth": (0, 3, 7, 10, 14), "dominant-11th": (0, 4, 7, 10, 17), "dominant-13th": (0, 4, 7, 10, 21),
    "major-minor": (0, 3, 7, 11), "power": (0, 7),
}


def _flags(m):
    words = [i.text.lower().replace(" ", "").replace(".", "") for i in m.items if isinstance(i, core.Words)]
    syms = [i.kind for i in m.items if isinstance(i, core.Symbol)]
    tocoda = "tocoda" in words
    return {
        "segno": "segno" in syms,
        "coda": "coda" in syms and not tocoda,
        "tocoda": tocoda,
        "fine": "fine" in words,
        "ds": any(w.startswith("ds") for w in words),
        "dc": any(w.startswith("dc") for w in words),
    }


def order(score):
    """Measure indices in playing order."""
    M = score.measures
    n = len(M)
    flags = [_flags(m) for m in M]
    # ending numbers covering each measure
    endings, cur = [None] * n, None
    for i, m in enumerate(M):
        if m.ending_start:
            cur = {int(x) for x in m.ending_start.replace(" ", "").split(",") if x.isdigit()}
        endings[i] = cur
        if m.ending_stop:
            cur = None
    segno = next((i for i, f in enumerate(flags) if f["segno"]), 0)
    coda = next((i for i, f in enumerate(flags) if f["coda"]), None)

    def max_pass(rs):
        nums = set()
        for j in range(rs, n):
            if j > rs and M[j].left_repeat:
                break
            if endings[j]:
                nums |= endings[j]
        return max(nums) if nums else 2

    seq, i, rs = [], 0, 0
    passes = {0: 1}
    jumped = False
    steps = 0
    while i < n and steps < 20000:
        steps += 1
        m = M[i]
        if m.left_repeat and rs != i:
            rs = i
            if jumped:
                passes.setdefault(i, 1)
            else:
                passes[i] = 1
        e = endings[i]
        if e and passes.get(rs, 1) not in e:
            i += 1
            continue
        seq.append(i)
        f = flags[i]
        if jumped and f["tocoda"] and coda is not None:
            i, jumped = coda, False
            jumped = "done"
            continue
        if jumped is True and f["fine"]:
            break
        if m.right_repeat and jumped is False and passes.get(rs, 1) < max_pass(rs):
            passes[rs] = passes.get(rs, 1) + 1
            i = rs
            continue
        if jumped is False and (f["ds"] or f["dc"]):
            jumped = True
            i = segno if f["ds"] else 0
            rs = i if M[i].left_repeat else rs
            continue
        i += 1
    return seq


def events(score, transpose=0, enharmonic="auto", octave=0, clef=None, chords=True):
    """-> dict(notes=[...], chords=[...], tempos=[(beat, bpm)], length=beats, bpm=first bpm)

    note = dict(t=start (quarter beats), d=duration, midi, line, c0, c1, measure)
    """
    shift = core.resolve_shift(score, transpose, enharmonic, octave, clef or score.clef or "treble")
    M = score.measures
    # key state at the start of every measure, in written order
    start_key, key = [], score.key
    for m in M:
        start_key.append(key)
        for it in m.items:
            if isinstance(it, core.Attr) and it.key:
                key = it.key
    notes, chord_ev, tempos = [], [], []
    t = Fraction(0)
    open_tie = None
    for idx in order(score):
        m = M[idx]
        ks = core.KeyState(start_key[idx], transpose, enharmonic, shift)
        pos = t
        for it in m.items:
            if isinstance(it, core.Attr) and it.key:
                ks = core.KeyState(it.key, transpose, enharmonic, shift)
            elif isinstance(it, core.Tempo):
                tempos.append((float(pos), it.bpm))
            elif isinstance(it, core.Harmony) and chords:
                root = ks.pitch(it.degree, it.acc)[3]
                root = 48 + (root - 48) % 12
                tones = [root + x for x in CHORD_TONES.get(it.kind, (0, 4, 7))]
                bass = ks.pitch(*it.bass)[3] if it.bass else root
                bass = 36 + (bass - 36) % 12
                chord_ev.append({"t": float(pos), "midi": [bass] + tones, "line": it.line})
            elif isinstance(it, core.Note):
                q = it.dur / core.DIV                 # quarters
                if it.degree:
                    midi = ks.pitch(it.degree, it.acc, it.octave)[3]
                    if it.tie_stop and open_tie is not None and open_tie["midi"] == midi:
                        open_tie["d"] += float(q)      # tied: extend the sounding note
                    else:
                        open_tie = {"t": float(pos), "d": float(q), "midi": midi, "line": it.line,
                                    "c0": it.col[0], "c1": it.col[1], "measure": m.number}
                        notes.append(open_tie)
                    if not it.tie_start:
                        open_tie = None
                else:
                    open_tie = None
                pos += q
        cap = Fraction(m.time[0] * 4, m.time[1])
        t += max(pos - t, cap if not m.pickup else pos - t)
    length = float(t)
    for k, c in enumerate(chord_ev):                  # each chord rings until the next one
        end = chord_ev[k + 1]["t"] if k + 1 < len(chord_ev) else length
        c["d"] = max(0.25, end - c["t"])
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0.0, DEFAULT_BPM))
    return {"notes": notes, "chords": chord_ev, "tempos": tempos, "length": length,
            "bpm": tempos[0][1]}


# --------------------------------------------------------------------------- MIDI file

def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def midi_bytes(ev, bpm=None, title="", melody_program=0, chord_program=48):
    """Standard MIDI file (format 1): tempo track, melody (ch 1), chords (ch 2)."""
    tpq = 480

    def track(events_):
        events_.sort(key=lambda e: (e[0], e[1]))
        data, last = bytearray(), 0
        for tick, _prio, msg in events_:
            data += _vlq(tick - last) + msg
            last = tick
        data += b"\x00\xff\x2f\x00"
        return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)

    tempos = [(0.0, bpm)] if bpm else ev["tempos"]
    t0 = []
    note = f"jianpu2xml {__version__}".encode()
    t0.append((0, 0, b"\xff\x01" + _vlq(len(note)) + note))
    if title:
        name = title.encode("utf-8")[:120]
        t0.append((0, 0, b"\xff\x03" + _vlq(len(name)) + name))
    for beat, b in tempos:
        us = int(60_000_000 / b)
        t0.append((int(beat * tpq), 1, b"\xff\x51\x03" + us.to_bytes(3, "big")))
    mel = [(0, 0, bytes([0xC0, melody_program]))]
    for n in ev["notes"]:
        on, off = int(n["t"] * tpq), int((n["t"] + n["d"]) * tpq) - 1
        mel.append((on, 2, bytes([0x90, n["midi"], 96])))
        mel.append((max(on + 1, off), 1, bytes([0x80, n["midi"], 0])))
    tracks = [track(t0), track(mel)]
    if ev["chords"]:
        ch = [(0, 0, bytes([0xC1, chord_program]))]
        for c in ev["chords"]:
            on, off = int(c["t"] * tpq), int((c["t"] + c["d"]) * tpq) - 1
            for k, p in enumerate(c["midi"]):
                ch.append((on, 2, bytes([0x91, p, 70 if k else 80])))
                ch.append((max(on + 1, off), 1, bytes([0x81, p, 0])))
        tracks.append(track(ch))
    head = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), tpq)
    return head + b"".join(tracks)

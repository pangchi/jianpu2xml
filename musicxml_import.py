"""musicxml_import - MusicXML (.musicxml / .xml / compressed .mxl) -> jianpu text.

Takes one melody line: the first part that has pitched notes (or the part you
choose), voice 1, and the top note of any chord. Keeps key/time (and changes),
tempo, title/credits, chords (written as degrees, e.g. <6m/5>), ties, slurs,
tuplets, fermatas, repeats, endings, segno/coda, words and lyrics (all verses).

    from musicxml_import import import_file
    text, warnings, info = import_file("song.mxl")
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import jianpu_core as core

FIFTHS_NAME = {v: k for k, v in core.KEY_FIFTHS.items()}
KIND_SUFFIX = {
    "major": "", "minor": "m", "dominant": "7", "major-seventh": "maj7", "minor-seventh": "m7",
    "diminished": "dim", "diminished-seventh": "dim7", "augmented": "aug", "augmented-seventh": "aug7",
    "half-diminished": "m7b5", "suspended-fourth": "sus4", "suspended-second": "sus2",
    "major-sixth": "6", "minor-sixth": "m6", "dominant-ninth": "9", "major-ninth": "maj9",
    "minor-ninth": "m9", "dominant-11th": "11", "dominant-13th": "13", "major-minor": "mmaj7",
    "power": "5",
}
TUPLET_OK = {2, 3, 4, 5, 6, 7, 9}


class ImportError_(Exception):
    pass


# --------------------------------------------------------------------------- reading

def read_root(data: bytes) -> ET.Element:
    """Parse MusicXML bytes (plain or .mxl zip) into the <score-partwise> root."""
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ImportError_("Not a valid .mxl file") from e
        name = None
        if "META-INF/container.xml" in z.namelist():
            c = ET.fromstring(z.read("META-INF/container.xml"))
            rf = next((e for e in c.iter() if e.tag.split("}")[-1] == "rootfile"), None)
            if rf is not None:
                name = rf.get("full-path")
        if not name:
            name = next((n for n in z.namelist() if n.lower().endswith((".xml", ".musicxml"))
                         and not n.startswith("META-INF")), None)
        if not name:
            raise ImportError_("No MusicXML file inside the .mxl")
        data = z.read(name)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ImportError_(f"Not valid XML: {e}") from e
    tag = root.tag.split("}")[-1]
    if tag == "score-timewise":
        raise ImportError_("Timewise MusicXML isn't supported - re-export as partwise (the usual kind)")
    if tag != "score-partwise":
        raise ImportError_("This doesn't look like a MusicXML score")
    return root


def import_file(path, part=None):
    return import_bytes(Path(path).read_bytes(), part)


def import_bytes(data: bytes, part=None):
    return to_jianpu(read_root(data), part)


# --------------------------------------------------------------------------- helpers

def _t(el, path, default=None):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else default


def _key_name(fifths: int) -> str:
    return FIFTHS_NAME.get(max(-7, min(7, fifths)), "C")


def _degree(step, alter, octave, key, base=0):
    """(step, alter, octave) -> jianpu (degree, acc, octave marks) in key (name)."""
    kli, kpc, _ = core.parse_key(key)
    tonic = 60 + kpc + 12 * base
    li = core.LETTERS.index(step)
    deg = (li - kli) % 7 + 1
    midi = 12 * (octave + 1) + core.LETTER_PC[li] + alter
    diff = midi - (tonic + core.MAJOR[deg - 1])
    octv = round(diff / 12)
    acc = diff - 12 * octv
    return deg, acc, octv


def _chord_degree(step, alter, key):
    kli, kpc, _ = core.parse_key(key)
    li = core.LETTERS.index(step)
    deg = (li - kli) % 7 + 1
    a = (core.LETTER_PC[li] + alter - (kpc + core.MAJOR[deg - 1])) % 12
    a = a - 12 if a > 6 else a
    return ("#" * a if a > 0 else "b" * -a) + str(deg)


def _dur_pieces(q: Fraction):
    """Length in beats (units) -> list of (underscores, dots, dashes) pieces, tied together."""
    out = []
    while q > 0:
        if q >= 1:
            n = int(q)
            f = q - n
            if f in (0, Fraction(1, 2), Fraction(3, 4)):
                out.append((0, {0: 0, Fraction(1, 2): 1, Fraction(3, 4): 2}[f], n - 1))
                return out
            out.append((0, 0, n - 1))
            q = f
            continue
        u, v = 0, q
        while v < 1 and u < 6:
            v *= 2
            u += 1
        if v in (1, Fraction(3, 2), Fraction(7, 4)):
            out.append((u, {1: 0, Fraction(3, 2): 1, Fraction(7, 4): 2}[v], 0))
            return out
        out.append((u, 0, 0))
        q -= Fraction(1, 2 ** u)
        if u >= 6:
            break
    return out


def _clean_suffix(s):
    return re.sub(r"[<>/\s]", "", s or "")


# --------------------------------------------------------------------------- converter

def to_jianpu(root: ET.Element, part=None):
    warnings = []
    # strip namespaces if any
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]

    part_names = {sp.get("id"): (_t(sp, "part-name", "") or sp.get("id"))
                  for sp in root.findall("part-list/score-part")}
    parts = root.findall("part")
    if not parts:
        raise ImportError_("The score has no parts")
    if part is None:
        chosen = next((p for p in parts if p.find("measure/note/pitch") is not None), parts[0])
    else:
        chosen = parts[int(part)] if str(part).isdigit() else next(p for p in parts if p.get("id") == part)
    if len(parts) > 1:
        warnings.append((0, f"Score has {len(parts)} parts; imported '{part_names.get(chosen.get('id'), chosen.get('id'))}'"))

    meta = {}
    meta["title"] = _t(root, "work/work-title") or _t(root, "movement-title") or ""
    for cr in root.findall("credit"):
        ctype = _t(cr, "credit-type")
        words = " ".join((w.text or "").strip() for w in cr.findall("credit-words")).strip()
        if ctype in ("title", "subtitle", "composer", "lyricist", "arranger") and words and not meta.get(ctype):
            meta[ctype] = words
    for c in root.findall("identification/creator"):
        t = c.get("type")
        if t in ("composer", "lyricist", "arranger") and c.text and not meta.get(t):
            meta[t] = c.text.strip()

    measures = chosen.findall("measure")
    use_print = any(m.find("print[@new-system='yes']") is not None or m.find("print[@new-page='yes']") is not None
                    for m in measures)

    # register: put the melody's middle near the middle of the tonic octave -> fewest dots
    fk = chosen.find("measure/attributes/key/fifths")
    first_key = _key_name(int(fk.text)) if fk is not None and fk.text else "C"
    mids = []
    for pe in chosen.iter("pitch"):
        li = core.LETTERS.index(_t(pe, "step", "C"))
        mids.append(12 * (int(_t(pe, "octave", "4")) + 1) + core.LETTER_PC[li] + int(float(_t(pe, "alter", "0"))))
    base = 0
    if mids:
        mids.sort()
        med = mids[len(mids) // 2]
        base = max(-3, min(3, round((med - (60 + core.parse_key(first_key)[1] + 5.5)) / 12)))

    divisions = 1
    key, time = None, (4, 4)
    clef = ""
    tempo = None
    voice_sel = None
    lines, cur_line = [], []          # list of (tokens, lyric_notes)
    line_notes = []                   # note dicts eligible for lyrics in the current line
    pending = []                      # harmony / words waiting for the next note
    slur_depth = 0
    tup_open = None                   # dict(actual, normal, target, acc)
    started = False
    n_measures = len(measures)
    transpose_warned = False

    def flush_line():
        nonlocal cur_line, line_notes
        if cur_line:
            lines.append((cur_line, line_notes))
        cur_line, line_notes = [], []

    for mi, m in enumerate(measures):
        if mi and ((use_print and (m.find("print[@new-system='yes']") is not None
                                   or m.find("print[@new-page='yes']") is not None))
                   or (not use_print and mi % 4 == 0)):
            flush_line()
        toks = []
        # left barline
        for bl in m.findall("barline"):
            if bl.get("location") == "left":
                if bl.find("repeat[@direction='forward']") is not None:
                    if cur_line and cur_line[-1] == "|":
                        cur_line.pop()              # "| |:" -> "|:"
                    toks.append("|:")
                end = bl.find("ending")
                if end is not None and end.get("type") == "start":
                    toks.append(f"[{end.get('number', '1').replace(' ', '')}]")
        right = None
        for el in m:
            tag = el.tag
            if tag == "attributes":
                if el.find("divisions") is not None:
                    divisions = int(_t(el, "divisions", "1"))
                if el.find("key/fifths") is not None:
                    nk = _key_name(int(_t(el, "key/fifths", "0")))
                    if key is None:
                        key = nk
                    elif nk != key:
                        toks.append(f"[key={nk}]")
                        key = nk
                if el.find("time/beats") is not None:
                    beats = _t(el, "time/beats", "4")
                    if "+" in beats:
                        beats = str(sum(int(b) for b in beats.split("+")))
                        warnings.append((0, "Additive time signature summed"))
                    nt = (int(beats), int(_t(el, "time/beat-type", "4")))
                    if started and nt != time:
                        toks.append(f"[time={nt[0]}/{nt[1]}]")
                    time = nt
                c = el.find("clef/sign")
                if c is not None and not started:
                    clef = "bass" if c.text == "F" else ""
                if el.find("transpose") is not None and not transpose_warned:
                    warnings.append((0, "Transposing instrument: imported the written pitch"))
                    transpose_warned = True
            elif tag == "direction":
                snd = el.find("sound")
                if snd is not None and snd.get("tempo") and tempo is None:
                    tempo = int(round(float(snd.get("tempo"))))
                for w in el.findall("direction-type/words"):
                    if w.text and w.text.strip():
                        txt = w.text.strip().replace('"', "'")
                        low = txt.lower().replace(" ", "").replace(".", "")
                        nav = {"tocoda": "[tocoda]", "fine": "[fine]", "dc": "[dc]", "ds": "[ds]",
                               "dcalfine": "[dcalfine]", "dsalcoda": "[dsalcoda]", "dcalcoda": "[dcalcoda]",
                               "dsalfine": "[dsalfine]"}.get(low)
                        pending.append(nav or f'"{txt}"')
                if el.find("direction-type/segno") is not None:
                    pending.append("[segno]")
                if el.find("direction-type/coda") is not None:
                    pending.append("[coda]")
                mt = el.find("direction-type/metronome/per-minute")
                if mt is not None and mt.text and tempo is None:
                    try:
                        tempo = int(round(float(mt.text)))
                    except ValueError:
                        pass
            elif tag == "harmony":
                step = _t(el, "root/root-step")
                if not step or key is None:
                    continue
                alter = int(float(_t(el, "root/root-alter", "0")))
                kind_el = el.find("kind")
                kind = kind_el.text.strip() if kind_el is not None and kind_el.text else "major"
                if kind == "none":
                    continue
                txt = kind_el.get("text") if kind_el is not None else None
                suffix = _clean_suffix(txt) if (txt or kind == "major") else KIND_SUFFIX.get(kind, "")
                chord = _chord_degree(step, alter, key) + suffix
                bstep = _t(el, "bass/bass-step")
                if bstep:
                    chord += "/" + _chord_degree(bstep, int(float(_t(el, "bass/bass-alter", "0"))), key)
                pending.append(f"<{chord}>")
            elif tag == "barline" and el.get("location", "right") == "right":
                right = el
            elif tag == "note":
                if el.find("grace") is not None or el.find("cue") is not None:
                    continue
                v = _t(el, "voice", "1")
                if voice_sel is None and (el.find("pitch") is not None or el.find("rest") is not None):
                    voice_sel = v
                if v != voice_sel or el.find("chord") is not None:
                    continue
                if key is None:
                    key = "C"
                started = True
                dur = Fraction(int(_t(el, "duration", "0") or 0), divisions)     # quarters
                if dur == 0:
                    continue
                tm = el.find("time-modification")
                nominal = dur
                actual = normal = None
                if tm is not None:
                    actual, normal = int(_t(tm, "actual-notes", "3")), int(_t(tm, "normal-notes", "2"))
                    nominal = dur * actual / normal
                unit = Fraction(4, time[1])
                q = nominal / unit
                pieces = _dur_pieces(q)

                rest = el.find("rest") is not None
                if rest:
                    digit = "0"
                else:
                    p = el.find("pitch")
                    if p is None:
                        continue                     # unpitched
                    deg, acc, octv = _degree(_t(p, "step"), int(float(_t(p, "alter", "0"))),
                                             int(_t(p, "octave", "4")), key, base)
                    if abs(acc) > 2:
                        warnings.append((0, f"Measure {m.get('number')}: unusual spelling kept as {acc:+d}"))
                    digit = ("#" * acc if acc > 0 else "b" * -acc) + str(deg) + ("'" * octv if octv > 0 else "," * -octv)

                nots = el.find("notations")
                tie_start = el.find("tie[@type='start']") is not None or \
                    (nots is not None and nots.find("tied[@type='start']") is not None)
                tie_stop = el.find("tie[@type='stop']") is not None or \
                    (nots is not None and nots.find("tied[@type='stop']") is not None)
                slur_on = len(nots.findall("slur[@type='start']")) if nots is not None else 0
                slur_off = len(nots.findall("slur[@type='stop']")) if nots is not None else 0
                ferm = nots is not None and nots.find("fermata") is not None
                t_start = nots is not None and nots.find("tuplet[@type='start']") is not None
                t_stop = nots is not None and nots.find("tuplet[@type='stop']") is not None

                toks.extend(pending)
                pending = []
                # tuplet groups (also when the file has no <tuplet> brackets)
                if tm is not None and tup_open is None:
                    if actual in TUPLET_OK:
                        toks.append(f"{actual}{{")
                        tup_open = {"target": None if t_start else nominal * actual, "acc": Fraction(0),
                                    "explicit": t_start}
                    else:
                        warnings.append((0, f"Measure {m.get('number')}: {actual}:{normal} tuplet written as plain notes"))

                lyr = {}
                if not rest and not tie_stop:
                    for ly in el.findall("lyric"):
                        txt = "".join(t.text or "" for t in ly.findall("text")).strip()
                        if not txt:
                            continue
                        num = ly.get("number", "1")
                        num = int(num) if num.isdigit() else (len(lyr) + 1)
                        syl = _t(ly, "syllabic", "single")
                        lyr[num] = txt.replace(" ", "") + ("-" if syl in ("begin", "middle") else "")
                    line_notes.append(lyr)

                if rest:
                    # jianpu writes each beat of rest as its own 0
                    for k, (u, d, dashes) in enumerate(pieces):
                        toks.append("0" + "_" * u + "." * d)
                        toks.extend(["0"] * dashes)
                else:
                    for k, (u, d, dashes) in enumerate(pieces):
                        tok = digit + "_" * u + "." * d
                        if k == 0:
                            tok = "(" * slur_on + tok
                        last = k == len(pieces) - 1
                        if not last or tie_start:
                            tok += "~"
                        if last and ferm:
                            tok += "^"
                        if last:
                            tok += ")" * min(slur_off, slur_depth + slur_on)
                        toks.append(tok)
                        toks.extend(["-"] * dashes)
                    slur_depth = max(0, slur_depth + slur_on - slur_off)
                if tup_open is not None:
                    tup_open["acc"] += nominal
                    done = t_stop if tup_open["explicit"] else (tup_open["target"] is not None
                                                                 and tup_open["acc"] >= tup_open["target"])
                    if done:
                        toks.append("}")
                        tup_open = None
            elif tag == "forward":
                pass
        if tup_open is not None:
            toks.append("}")
            tup_open = None
            warnings.append((0, f"Measure {m.get('number')}: tuplet closed at the barline"))
        # right barline
        bar = "|"
        if right is not None:
            style = _t(right, "bar-style", "regular")
            end = right.find("ending")
            if right.find("repeat[@direction='backward']") is not None:
                bar = ":|"
            elif style == "light-heavy":
                bar = "|]"
            elif style in ("light-light", "heavy-heavy"):
                bar = "||"
            elif end is not None and end.get("type") in ("stop", "discontinue"):
                toks.append("[/]")
        if mi == n_measures - 1 and bar == "|":
            bar = "|]"
        toks.extend(pending)
        pending = []
        toks.append(bar)
        cur_line.extend(toks)
    flush_line()

    # ------------------------------------------------------------- assemble text
    out = []
    for k in ("title", "subtitle", "composer", "lyricist", "arranger"):
        if meta.get(k):
            out.append(f"{k}: {meta[k]}")
    out.append(f"key: {first_key}")
    first_time = None
    for el in chosen.iter("time"):
        first_time = f"{_t(el, 'beats', '4')}/{_t(el, 'beat-type', '4')}"
        break
    out.append(f"time: {first_time or '4/4'}")
    if tempo:
        out.append(f"tempo: {tempo}")
    if clef:
        out.append(f"clef: {clef}")
    if base:
        out.append(f"octave: {base:+d}")
    out.append("")
    verses = sorted({v for _, ln in lines for d in ln for v in d})
    for toks, lnotes in lines:
        out.append(" ".join(toks))
        rows = []
        for v in verses:
            syl = [d.get(v, "*") for d in lnotes]
            while syl and syl[-1] == "*":
                syl.pop()
            rows.append(syl)
        while rows and not rows[-1]:
            rows.pop()
        for syl in rows:                    # keep empty earlier verses so verse numbers line up
            out.append("w: " + (" ".join(syl) if syl else "*"))
    info = {"part": part_names.get(chosen.get("id"), chosen.get("id")), "parts": len(parts),
            "measures": n_measures, "title": meta.get("title", "")}
    return "\n".join(out) + "\n", warnings, info

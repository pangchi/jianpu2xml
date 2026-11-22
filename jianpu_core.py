"""jianpu_core - plain-text jianpu (numbered musical notation) -> MusicXML.

Standard library only. The text format is described in SYNTAX_HELP below
and in README.md.

    import jianpu_core as core
    result = core.convert(text, transpose=2)      # up a major 2nd
    open("out.musicxml", "w", encoding="utf-8").write(result.xml)
"""
from __future__ import annotations

import datetime
import re
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from statistics import median
from xml.sax.saxutils import escape

from version import __version__

DIV = 1680                      # MusicXML <divisions> per quarter (fits 3-, 5-, 7-tuplets)
WHOLE = DIV * 4

LETTERS = "CDEFGAB"
LETTER_PC = (0, 2, 4, 5, 7, 9, 11)
MAJOR = (0, 2, 4, 5, 7, 9, 11)

KEY_FIFTHS = {"Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
              "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7}
PC_NAME = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "F#",
           7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
ENHARMONIC = {"auto": {}, "sharps": {1: "C#", 6: "F#"}, "flats": {1: "Db", 6: "Gb"}}

INTERVAL_NAMES = {0: "unison", 1: "minor 2nd", 2: "major 2nd", 3: "minor 3rd",
                  4: "major 3rd", 5: "perfect 4th", 6: "tritone", 7: "perfect 5th",
                  8: "minor 6th", 9: "major 6th", 10: "minor 7th", 11: "major 7th",
                  12: "octave"}

CHORD_KINDS = {
    "": "major", "maj": "major", "M": "major", "m": "minor", "min": "minor", "-": "minor",
    "7": "dominant", "9": "dominant-ninth", "11": "dominant-11th", "13": "dominant-13th",
    "maj7": "major-seventh", "M7": "major-seventh", "maj9": "major-ninth",
    "m7": "minor-seventh", "min7": "minor-seventh", "-7": "minor-seventh",
    "m9": "minor-ninth", "m11": "minor-11th", "mmaj7": "major-minor", "mM7": "major-minor",
    "6": "major-sixth", "m6": "minor-sixth",
    "dim": "diminished", "°": "diminished", "dim7": "diminished-seventh", "°7": "diminished-seventh",
    "m7b5": "half-diminished", "ø": "half-diminished", "ø7": "half-diminished",
    "aug": "augmented", "+": "augmented", "aug7": "augmented-seventh", "+7": "augmented-seventh",
    "sus": "suspended-fourth", "sus4": "suspended-fourth", "4": "suspended-fourth",
    "sus2": "suspended-second", "2": "suspended-second", "7sus4": "dominant",
    "add9": "major", "5": "power",
}

SYNTAX_HELP = r"""JIANPU TEXT FORMAT  (one music line per printed system)

HEADER (top of file)            key: D      or   1=D 4/4
  octave: -1   plain numbers sit an octave lower (default 0: 1 = tonic above middle C)
  title: / composer: / lyricist: / arranger:
  key: D       time: 4/4       tempo: 72      clef: treble|bass

NOTES        1-7 = scale degrees,  0 = rest
  5'  5''      octave up (dot above)       5,  5,,   octave down (dot below)
  5_  5__      eighth / 16th (underlines)  5.  5_.   dotted
  5 - - -      dash = hold one more beat   #4  b7    accidentals (n = natural)
  5~ 5         tie (same pitch; if pitches differ it becomes a slur)
  (5_ 3_)      slur                        5^        fermata
  3{ 5_ 3_ 2_ }  triplet   (5{ ... } quintuplet, etc.)

BARLINES     |   ||   |]   |:   :|   :|:
ENDINGS      [1]  [2]  at the start of the measure;  [/] ends a bracket at the next barline
CHORDS       <1> <6m> <5/7> <b7> <Em> <G7/B>   placed before the note they sit over
             numbers = chord degrees in the current key; letters are transposed too
TEXT         "rit."  "Bass"  - printed words above the staff
NAVIGATION   [segno] [coda] [tocoda] [fine] [dc] [ds] [dcalfine] [dsalcoda]
CHANGES      [key=F]  [time=2/4]  (or key:/time: lines between music lines)
LYRICS       w: 明明還說分開   (after the music line; one syllable per note;
             rests and tied continuations are skipped automatically;
             * = note with no syllable; Freu- de = hyphenated syllables;
             a second w: line under the same music line = verse 2)
COMMENTS     // anything
"""

HEADER_RE = re.compile(
    r"^\s*(title|subtitle|composer|lyricist|arranger|key|time|tempo|clef|octave)\s*[:：]\s*(.*?)\s*$", re.I)
ONE_EQ_RE = re.compile(r"^\s*1\s*=\s*([A-Ga-g][#b♯♭]?)\s*(\d+\s*/\s*\d+)?\s*$")
LYRIC_RE = re.compile(r"^\s*w\s*[:：](.*)$", re.I)
NOTE_RE = re.compile(r"(\()?([#b♯♭n]?)([0-7])([',_.~)^]*)")
BARS = (":|:", ":||", "||:", ":|", "|:", "||", "|]", "|")
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]")
LYRIC_QUOTES = set('"“”「」『』')
LYRIC_PUNCT = set("，。？！、；：,.?!;:“”\"「」『』（）()…～~")
NAV = {
    "segno": ("symbol", "segno"), "coda": ("symbol", "coda"),
    "tocoda": ("words", "To Coda"), "fine": ("words", "Fine"),
    "dc": ("words", "D.C."), "ds": ("words", "D.S."),
    "dcalfine": ("words", "D.C. al Fine"), "dcalcoda": ("words", "D.C. al Coda"),
    "dsalfine": ("words", "D.S. al Fine"), "dsalcoda": ("words", "D.S. al Coda"),
}
_NORMALISE = str.maketrans({"｜": "|", "：": ":", "‘": "'", "’": "'", "′": "'", "·": ".",
                            "“": '"', "”": '"', "＜": "<", "＞": ">", "［": "[", "］": "]",
                            "（": "(", "）": ")", "－": "-", "—": "-", "～": "~", "＃": "#"})


# --------------------------------------------------------------------------- keys

def parse_key(s: str):
    """'D', '1=Bb', 'F#' -> (letter_index, pitch_class, normalised_name) or None."""
    s = s.strip().replace("♯", "#").replace("♭", "b")
    s = re.sub(r"^1\s*=\s*", "", s)
    m = re.match(r"^([A-Ga-g])([#b]?)", s)
    if not m:
        return None
    letter, acc = m.group(1).upper(), m.group(2)
    li = LETTERS.index(letter)
    pc = (LETTER_PC[li] + {"#": 1, "b": -1, "": 0}[acc]) % 12
    return li, pc, letter + acc


def target_key_name(orig_name: str, transpose: int, enharmonic: str = "auto") -> str:
    k = parse_key(orig_name)
    if k is None:
        return orig_name
    _, pc, name = k
    if transpose % 12 == 0 and name in KEY_FIFTHS:
        return name
    npc = (pc + transpose) % 12
    return ENHARMONIC.get(enharmonic, {}).get(npc, PC_NAME[npc])


def interval_label(t: int) -> str:
    if t == 0:
        return "0  (none)"
    return f"{t:+d}  ({INTERVAL_NAMES[abs(t)]} {'up' if t > 0 else 'down'})"


def key_signature_text(name: str) -> str:
    f = KEY_FIFTHS.get(name)
    if f is None:
        return "?"
    return "no sharps/flats" if f == 0 else f"{abs(f)}{'♯' if f > 0 else '♭'}"


class KeyState:
    """Maps jianpu degrees in an original key to spelled pitches in the target key."""

    def __init__(self, orig_name, transpose, enharmonic, octave_shift):
        li, pc, name = parse_key(orig_name) or (0, 0, "C")
        self.orig_name = name
        self.orig_letter, self.orig_pc = li, pc
        self.name = target_key_name(name, transpose, enharmonic)
        self.letter = parse_key(self.name)[0]
        self.tonic = 60 + pc + transpose + 12 * octave_shift
        self.fifths = KEY_FIFTHS.get(self.name, 0)

    def pitch(self, degree, acc=0, octave=0):
        """-> (step, alter, octave, midi)"""
        li = (self.letter + degree - 1) % 7
        midi = self.tonic + MAJOR[degree - 1] + acc + 12 * octave
        lpc = LETTER_PC[li]
        o = (midi - lpc + 6) // 12 - 1
        alter = midi - (12 * (o + 1) + lpc)
        return LETTERS[li], alter, o, midi


# --------------------------------------------------------------------------- model

@dataclass(eq=False)
class Note:
    degree: int                     # 0 = rest
    acc: int
    octave: int
    nominal: Fraction               # written duration in divisions (before tuplet scaling)
    scale: Fraction = Fraction(1)
    line: int = 0
    tuplet: tuple | None = None     # (actual, normal)
    tuplet_start: bool = False
    tuplet_stop: bool = False
    tie_start: bool = False
    tie_stop: bool = False
    slur_start: list = field(default_factory=list)
    slur_stop: list = field(default_factory=list)
    fermata: bool = False
    col: tuple = (0, 0)             # (start, end) character span in its source line
    lyrics: dict = field(default_factory=dict)   # verse -> (text, syllabic)

    @property
    def dur(self) -> Fraction:
        return self.nominal * self.scale

    def same_pitch(self, o) -> bool:
        return (self.degree, self.acc, self.octave) == (o.degree, o.acc, o.octave)


@dataclass(eq=False)
class Harmony:
    degree: int
    acc: int
    suffix: str
    kind: str
    bass: tuple | None
    line: int = 0


@dataclass(eq=False)
class Words:
    text: str


@dataclass(eq=False)
class Symbol:
    kind: str


@dataclass(eq=False)
class Attr:
    key: str | None = None
    time: tuple | None = None


@dataclass(eq=False)
class Tempo:
    bpm: int


@dataclass(eq=False)
class Measure:
    items: list = field(default_factory=list)
    line: int = 0
    time: tuple = (4, 4)
    left_repeat: bool = False
    right_style: str = "regular"
    right_repeat: bool = False
    ending_start: str | None = None
    ending_stop: tuple | None = None
    number: str = "1"
    pickup: bool = False

    def notes(self):
        return [i for i in self.items if isinstance(i, Note)]

    def length(self) -> Fraction:
        return sum((n.dur for n in self.notes()), Fraction(0))


@dataclass
class Score:
    meta: dict
    key: str
    time: tuple
    clef: str
    measures: list
    warnings: list
    octave: int = 0                 # "octave:" header - register of plain numbers (0 = tonic from middle C)


# --------------------------------------------------------------------------- parser

def split_syllables(text: str):
    out = []
    for tok in text.split():
        buf = ""
        for ch in tok:
            if CJK_RE.match(ch):
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(ch)
            elif ch in LYRIC_QUOTES:
                continue
            elif ch in LYRIC_PUNCT:          # punctuation sticks to its syllable
                if buf:
                    buf += ch
                elif out:
                    out[-1] += ch
            else:
                buf += ch
        if buf:
            out.append("*" if buf == "_" else buf)
    return out


def _fmt_beats(x: Fraction) -> str:
    return str(int(x)) if x.denominator == 1 else f"{float(x):g}"


class Parser:
    def __init__(self):
        self.meta = {}
        self.key, self.time, self.clef, self.tempo = "C", (4, 4), "", None
        self.octave = 0
        self.init_key, self.init_time = None, None
        self.warnings = []
        self.measures = []
        self.cur = Measure()
        self.started = False
        self.last_note = None
        self.pending_tilde = None
        self.tuplet = None
        self.tuplet_notes = []
        self.open_volta = None
        self.close_volta_flag = False
        self.line_notes = []
        self.verse = 0

    @property
    def unit(self) -> Fraction:
        return Fraction(WHOLE, self.time[1])

    def warn(self, ln, msg):
        self.warnings.append((ln, msg))

    # ---- entry
    def parse(self, text: str) -> Score:
        for ln, raw in enumerate(text.splitlines(), 1):
            line = raw.translate(_NORMALISE)
            if "//" in line:
                line = line[:line.index("//")]
            if not line.strip():
                continue
            if line.strip().startswith("```"):
                continue
            m = LYRIC_RE.match(line)
            if m:
                self.lyrics(m.group(1), ln)
                continue
            m = ONE_EQ_RE.match(line)
            if m:
                self.header("key", m.group(1), ln)
                if m.group(2):
                    self.header("time", m.group(2), ln)
                continue
            m = HEADER_RE.match(line)
            if m:
                self.header(m.group(1).lower(), m.group(2), ln)
                continue
            self.music_line(line, ln)
        return self.finish()

    # ---- headers / attribute changes
    def header(self, k, v, ln):
        if k == "key":
            pk = parse_key(v)
            if pk is None:
                self.warn(ln, f"Unrecognised key '{v}'")
                return
            self.key = pk[2]
            if self.started:
                self.cur.items.append(Attr(key=self.key))
        elif k == "time":
            m = re.match(r"^(\d+)\s*/\s*(\d+)$", v.strip())
            if not m or int(m.group(2)) not in (1, 2, 4, 8, 16, 32):
                self.warn(ln, f"Unrecognised time signature '{v}'")
                return
            self.time = (int(m.group(1)), int(m.group(2)))
            if self.started:
                self.cur.items.append(Attr(time=self.time))
                if not self.cur.notes():
                    self.cur.time = self.time
        elif k == "tempo":
            m = re.search(r"\d+", v)
            if not m:
                self.warn(ln, f"Unrecognised tempo '{v}'")
                return
            if self.started:
                self.cur.items.append(Tempo(int(m.group())))
            else:
                self.tempo = int(m.group())
        elif k == "clef":
            self.clef = v.strip().lower()
        elif k == "octave":
            m = re.match(r"^[+-]?\d$", v.strip())
            if not m:
                self.warn(ln, f"octave: should be a number like -1 or +1, not '{v}'")
                return
            self.octave = int(v)
        else:
            self.meta[k] = v

    # ---- music
    def music_line(self, line, ln):
        if not self.started:
            self.started = True
            self.init_key, self.init_time = self.key, self.time
            self.cur.time, self.cur.line = self.time, ln
            if self.tempo:
                self.cur.items.append(Tempo(self.tempo))
        if not self.cur.items:
            self.cur.line = ln
        self.verse = 0
        self.line_notes = []
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c.isspace():
                i += 1
                continue
            if c == '"':
                j = line.find('"', i + 1)
                j = n if j < 0 else j
                self.cur.items.append(Words(line[i + 1:j].strip()))
                i = j + 1
                continue
            if c in "<[":
                close = ">" if c == "<" else "]"
                j = line.find(close, i + 1)
                if j < 0:
                    self.warn(ln, f"Missing '{close}'")
                    break
                content = line[i + 1:j].strip()
                if c == "<":
                    self.chord(content, ln)
                else:
                    self.bracket(content, ln)
                i = j + 1
                continue
            bar = next((b for b in BARS if line.startswith(b, i)), None)
            if bar:
                self.bar(bar, ln)
                i += len(bar)
                continue
            if c.isdigit() and i + 1 < n and line[i + 1] == "{":
                self.tuplet_open(int(c), ln)
                i += 2
                continue
            if c == "}":
                self.tuplet_close(ln)
                i += 1
                continue
            if c == "-":
                self.dash(ln, i)
                i += 1
                continue
            m = NOTE_RE.match(line, i)
            if m:
                self.note(m, ln)
                i = m.end()
                continue
            j = i
            while j < n and not line[j].isspace():
                j += 1
            self.warn(ln, f"Ignored unrecognised text '{line[i:j]}'")
            i = j

    def note(self, m, ln):
        acc = {"#": 1, "♯": 1, "b": -1, "♭": -1, "n": 0, "": 0}[m.group(2)]
        deg = int(m.group(3))
        suf = m.group(4)
        octv = suf.count("'") - suf.count(",")
        unders, dots = suf.count("_"), suf.count(".")
        nominal = self.unit / (2 ** unders) * (2 - Fraction(1, 2 ** dots))
        if deg == 0:
            acc = octv = 0
        n = Note(deg, acc, octv, nominal, line=ln, col=(m.start(), m.end()))
        if self.tuplet:
            k, normal = self.tuplet
            n.scale, n.tuplet = Fraction(normal, k), (k, normal)
            self.tuplet_notes.append(n)
        p = self.pending_tilde
        if p is not None:
            if deg > 0 and p.same_pitch(n):
                p.tie_start = n.tie_stop = True
            elif deg > 0:
                p.slur_start.append(2)
                n.slur_stop.append(2)
            self.pending_tilde = None
        if "~" in suf:
            self.pending_tilde = n if deg > 0 else None
        if m.group(1):
            n.slur_start.append(1)
        if ")" in suf:
            n.slur_stop.append(1)
        n.fermata = "^" in suf
        self.cur.items.append(n)
        self.last_note = n
        if deg > 0 and not n.tie_stop:
            self.line_notes.append(n)

    def dash(self, ln, col=0):
        last = self.last_note
        if last is None:
            self.warn(ln, "Dash '-' with no note before it")
            return
        if any(x is last for x in self.cur.items):
            last.nominal += self.unit
            return
        # dash at the start of a measure: continue the previous note with a tie
        n = Note(last.degree, last.acc, last.octave, self.unit, line=ln, col=(col, col + 1))
        if last.degree > 0:
            last.tie_start = n.tie_stop = True
        if self.pending_tilde is last:
            self.pending_tilde = n
        self.cur.items.append(n)
        self.last_note = n

    def tuplet_open(self, k, ln):
        if self.tuplet:
            self.warn(ln, "Nested tuplet - closing the previous one")
            self.tuplet_close(ln)
        normal = {2: 3, 3: 2, 4: 3, 5: 4, 6: 4, 7: 4, 9: 8}.get(k)
        if not normal:
            self.warn(ln, f"Unsupported tuplet {k}{{")
            return
        self.tuplet, self.tuplet_notes = (k, normal), []

    def tuplet_close(self, ln):
        if not self.tuplet:
            self.warn(ln, "'}' without an open tuplet")
            return
        ns = self.tuplet_notes
        if ns:
            ns[0].tuplet_start = True
            ns[-1].tuplet_stop = True
        self.tuplet, self.tuplet_notes = None, []

    def chord(self, s, ln):
        s = s.replace("♯", "#").replace("♭", "b").strip()
        bass = None
        if "/" in s:
            main, b = s.rsplit("/", 1)
            br = self._chord_root(b.strip())
            if br and br[2] == "":
                bass, s = (br[0], br[1]), main.strip()
        r = self._chord_root(s)
        if not r:
            self.warn(ln, f"Unrecognised chord <{s}>")
            return
        deg, acc, suffix = r
        kind = CHORD_KINDS.get(suffix)
        if kind is None:
            kind = "minor" if re.match(r"^(m|min)(?!aj)", suffix) else "major"
        self.cur.items.append(Harmony(deg, acc, suffix, kind, bass, ln))

    def _chord_root(self, s):
        m = re.match(r"^([#b]?)([1-7])(.*)$", s)
        if m:
            return int(m.group(2)), {"#": 1, "b": -1, "": 0}[m.group(1)], m.group(3)
        m = re.match(r"^([A-G])([#b]?)(.*)$", s)
        if m:
            li = LETTERS.index(m.group(1))
            pc = (LETTER_PC[li] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
            kli, kpc, _ = parse_key(self.key)
            deg = (li - kli) % 7 + 1
            a = (pc - (kpc + MAJOR[deg - 1])) % 12
            return deg, (a - 12 if a > 6 else a), m.group(3)
        return None

    def bracket(self, s, ln):
        low = s.lower().replace(" ", "").replace(".", "")
        if re.match(r"^\d+(,\d+)*$", low):
            if self.open_volta is not None and self.measures and self.measures[-1].ending_stop is None:
                self.measures[-1].ending_stop = (self.open_volta, "stop")
            if self.cur.notes():
                self.warn(ln, f"Ending [{s}] should be at the start of a measure")
            self.cur.ending_start = low.replace(",", ", ")
            self.open_volta = self.cur.ending_start
        elif low in ("/", "end"):
            self.close_volta_flag = True
        elif "=" in s:
            k, v = s.split("=", 1)
            k = k.strip().lower()
            if k in ("key", "time", "tempo"):
                self.header(k, v.strip(), ln)
            else:
                self.warn(ln, f"Unknown setting [{s}]")
        elif low in NAV:
            typ, val = NAV[low]
            self.cur.items.append(Symbol(val) if typ == "symbol" else Words(val))
            if low == "tocoda":
                self.cur.items.append(Symbol("coda"))
        else:
            self.warn(ln, f"Unknown bracket token [{s}]")

    def bar(self, b, ln):
        kind = {":||": ":|", "||:": "|:"}.get(b, b)
        if self.tuplet:
            self.warn(ln, "Tuplet not closed before barline")
            self.tuplet_close(ln)
        cur = self.cur
        if not cur.notes():
            if kind == "|":
                return
            if kind == "|:":
                cur.left_repeat = True
                return
            if self.measures:
                self._apply_right(self.measures[-1], kind)
            if kind == ":|:":
                cur.left_repeat = True
            return
        self._apply_right(cur, kind)
        self.measures.append(cur)
        self.cur = Measure(line=ln, time=self.time)
        if kind in ("|:", ":|:"):
            self.cur.left_repeat = True

    def _apply_right(self, m, kind):
        if kind in (":|", ":|:"):
            m.right_repeat, m.right_style = True, "light-heavy"
        elif kind == "||":
            m.right_style = "light-light"
        elif kind == "|]":
            m.right_style = "light-heavy"
        if self.open_volta is not None and m.ending_stop is None:
            if kind in (":|", ":|:"):
                m.ending_stop = (self.open_volta, "stop")
                self.open_volta = None
            elif kind in ("||", "|]") or self.close_volta_flag:
                m.ending_stop = (self.open_volta, "discontinue")
                self.open_volta = None
        self.close_volta_flag = False

    def lyrics(self, text, ln):
        if not self.line_notes:
            self.warn(ln, "Lyric line has no music line above it")
            return
        self.verse += 1
        notes = self.line_notes
        k, cont = 0, False
        for s in split_syllables(text):
            if k >= len(notes):
                self.warn(ln, f"More syllables than notes (extra from '{s}')")
                break
            if s == "*":
                k += 1
                cont = False
                continue
            hy = s.endswith("-") and len(s) > 1
            t = s.rstrip("-") if hy else s
            syl = ("middle" if hy else "end") if cont else ("begin" if hy else "single")
            notes[k].lyrics[self.verse] = (t, syl)
            cont = hy
            k += 1

    def finish(self) -> Score:
        if self.tuplet:
            self.tuplet_close(0)
        if self.cur.notes() or not self.measures:
            if self.cur.notes():
                self.measures.append(self.cur)
        elif self.cur.items:
            self.measures[-1].items.extend(self.cur.items)
        if self.measures:
            last = self.measures[-1]
            if last.right_style == "regular" and not last.right_repeat:
                last.right_style = "light-heavy"
            if self.open_volta is not None and last.ending_stop is None:
                last.ending_stop = (self.open_volta, "discontinue")
        # numbering + length checks
        num = 1
        for idx, m in enumerate(self.measures):
            cap = Fraction(m.time[0] * WHOLE, m.time[1])
            length = m.length()
            unit = Fraction(WHOLE, m.time[1])
            if idx == 0 and 0 < length < cap and len(self.measures) > 1:
                m.pickup, m.number = True, "0"
                continue
            m.number = str(num)
            num += 1
            last = idx == len(self.measures) - 1
            if length != cap and not (last and length < cap):
                self.warn(m.line, f"Measure {m.number} has {_fmt_beats(length / unit)} beats; "
                                  f"{m.time[0]}/{m.time[1]} needs {m.time[0]}")
        return Score(self.meta, self.init_key or self.key, self.init_time or self.time,
                     self.clef, self.measures, self.warnings, self.octave)


def parse(text: str) -> Score:
    return Parser().parse(text)


# --------------------------------------------------------------------------- writer

_BASE = [("whole", 4), ("half", 2), ("quarter", 1), ("eighth", Fraction(1, 2)),
         ("16th", Fraction(1, 4)), ("32nd", Fraction(1, 8)), ("64th", Fraction(1, 16))]
TYPE_TABLE = sorted(
    ((Fraction(q) * DIV * (2 - Fraction(1, 2 ** d)), name, d)
     for name, q in _BASE for d in (0, 1, 2)
     if (Fraction(q) * DIV * (2 - Fraction(1, 2 ** d))).denominator == 1),
    key=lambda x: -x[0])
BEAM_LEVEL = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4}


def decompose(d: Fraction):
    out, rem = [], Fraction(d)
    while rem > 0:
        for v, t, dots in TYPE_TABLE:
            if v <= rem:
                out.append((v, t, dots))
                rem -= v
                break
        else:
            return out, rem
    return out, Fraction(0)


def _beams(pieces, beat):
    groups, g, gbeat = [], [], None
    for p in pieces:
        lvl = BEAM_LEVEL.get(p["type"], 0)
        b = p["pos"] // beat
        if p["note"].degree == 0 or lvl == 0:
            if g:
                groups.append(g)
            g, gbeat = [], None
            continue
        if g and b != gbeat:
            groups.append(g)
            g = []
        g.append(p)
        gbeat = b
    if g:
        groups.append(g)
    for g in groups:
        if len(g) < 2:
            continue
        lv = [BEAM_LEVEL[p["type"]] for p in g]
        for level in range(1, max(lv) + 1):
            i = 0
            while i < len(g):
                if lv[i] < level:
                    i += 1
                    continue
                j = i
                while j + 1 < len(g) and lv[j + 1] >= level:
                    j += 1
                if j > i:
                    for k in range(i, j + 1):
                        g[k]["beams"][level] = "begin" if k == i else "end" if k == j else "continue"
                else:
                    g[i]["beams"][level] = "forward hook" if i == 0 else "backward hook"
                i = j + 1


def _iter_pitches(score, transpose, enharmonic, shift):
    ks = KeyState(score.key, transpose, enharmonic, shift)
    for m in score.measures:
        for it in m.items:
            if isinstance(it, Attr) and it.key:
                ks = KeyState(it.key, transpose, enharmonic, shift)
            elif isinstance(it, Note) and it.degree > 0:
                yield ks.pitch(it.degree, it.acc, it.octave)


@dataclass
class Result:
    xml: str
    warnings: list
    orig_key: str
    new_key: str
    octave_shift: int
    measure_count: int
    title: str
    lowest: str = ""
    highest: str = ""
    note_map: list = field(default_factory=list)   # per written <note>: (line, col_start, col_end)


def _pname(p):
    step, alter, o, _ = p
    return f"{step}{'#' * alter if alter > 0 else 'b' * -alter}{o}"


def resolve_shift(score, transpose=0, enharmonic="auto", octave=0, clef="treble"):
    """Total octave shift (header 'octave:' + the export setting, 'auto' centres on the staff)."""
    base = score.octave
    if octave == "auto":
        mids = [p[3] for p in _iter_pitches(score, transpose, enharmonic, base)]
        center = 71 if (clef or "treble") == "treble" else 50
        shift = min(range(-2, 3), key=lambda s: (abs(median(mids) + 12 * s - center), abs(s))) if mids else 0
    else:
        shift = int(octave)
    return shift + base


def build(score: Score, transpose=0, enharmonic="auto", octave=0, clef=None, meta=None) -> Result:
    if meta:                                  # header overrides, e.g. a custom title for export
        score.meta = {**score.meta, **{k: v for k, v in meta.items() if v is not None}}
    warnings = list(score.warnings)
    clef = (clef or score.clef or "treble").lower()
    if clef not in ("treble", "bass"):
        warnings.append((0, f"Unknown clef '{clef}' - using treble"))
        clef = "treble"
    base = score.octave
    shift = resolve_shift(score, transpose, enharmonic, octave, clef)
    pitches = list(_iter_pitches(score, transpose, enharmonic, shift))
    w = _Writer(score, transpose, enharmonic, shift, clef, warnings)
    xml = w.run()
    lo = _pname(min(pitches, key=lambda p: p[3])) if pitches else ""
    hi = _pname(max(pitches, key=lambda p: p[3])) if pitches else ""
    return Result(xml, warnings, parse_key(score.key)[2] if parse_key(score.key) else score.key,
                  w.first_key, shift - base, len(score.measures), score.meta.get("title", ""), lo, hi, w.note_map)


def convert(text: str, transpose=0, enharmonic="auto", octave=0, clef=None, meta=None) -> Result:
    return build(parse(text), transpose, enharmonic, octave, clef, meta)


class _Writer:
    def __init__(self, score, transpose, enharmonic, shift, clef, warnings):
        self.s, self.t, self.enh, self.shift, self.clef = score, transpose, enharmonic, shift, clef
        self.warnings = warnings
        self.first_key = ""
        self.note_map = []

    def ks(self, name):
        return KeyState(name, self.t, self.enh, self.shift)

    def run(self) -> str:
        s = self.s
        ks = self.ks(s.key)
        self.first_key = ks.name
        meta = s.meta
        o = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
             '"http://www.musicxml.org/dtds/partwise.dtd">',
             '<score-partwise version="4.0">']
        if meta.get("title"):
            o.append(f"  <work><work-title>{escape(meta['title'])}</work-title></work>")
            if meta.get("subtitle"):
                o.append(f"  <movement-title>{escape(meta['subtitle'])}</movement-title>")
        o.append("  <identification>")
        for role in ("composer", "lyricist", "arranger"):
            if meta.get(role):
                o.append(f'    <creator type="{role}">{escape(meta[role])}</creator>')
        o.append(f"    <encoding><software>jianpu2xml {__version__}</software>"
                 f"<encoding-date>{datetime.date.today().isoformat()}</encoding-date></encoding>")
        o.append("  </identification>")
        credits = [("title", meta.get("title"), "center", "top", 22), ("subtitle", meta.get("subtitle"), "center", "top", 14),
                   ("composer", meta.get("composer"), "right", "bottom", 11),
                   ("lyricist", meta.get("lyricist"), "left", "bottom", 11),
                   ("arranger", meta.get("arranger"), "right", "bottom", 11)]
        for ctype, val, just, valign, size in credits:
            if val:
                o.append(f'  <credit page="1"><credit-type>{ctype}</credit-type><credit-words justify="{just}" '
                         f'valign="{valign}" font-size="{size}">{escape(val)}</credit-words></credit>')
        o.append('  <part-list><score-part id="P1"><part-name>Melody</part-name></score-part></part-list>')
        o.append('  <part id="P1">')
        for idx, m in enumerate(s.measures):
            ks = self.measure(o, m, idx, ks)
        o.append("  </part>")
        o.append("</score-partwise>")
        return "\n".join(o) + "\n"

    # -- one measure
    def measure(self, o, m, idx, ks):
        o.append(f'    <measure number="{m.number}"' + (' implicit="yes"' if m.pickup else "") + ">")
        if idx > 0 and m.line != self.s.measures[idx - 1].line:
            o.append('      <print new-system="yes"/>')     # keep the jianpu line breaks
        if m.left_repeat or m.ending_start:
            b = ['      <barline location="left">']
            if m.left_repeat:
                b.append("<bar-style>heavy-light</bar-style>")
            if m.ending_start:
                b.append(f'<ending number="{m.ending_start}" type="start">{escape(m.ending_start)}.</ending>')
            if m.left_repeat:
                b.append('<repeat direction="forward"/>')
            b.append("</barline>")
            o.append("".join(b))
        if idx == 0:
            sign, line = ("G", 2) if self.clef == "treble" else ("F", 4)
            o.append(f"      <attributes><divisions>{DIV}</divisions>"
                     f"<key><fifths>{ks.fifths}</fifths></key>"
                     f"<time><beats>{self.s.time[0]}</beats><beat-type>{self.s.time[1]}</beat-type></time>"
                     f"<clef><sign>{sign}</sign><line>{line}</line></clef></attributes>")

        # pass 1: sequence of elements and note pieces (pitch spelled with the key in force)
        seq, pieces, pos = [], [], Fraction(0)
        time = m.time
        cap = Fraction(time[0] * WHOLE, time[1])
        only_note = len(m.notes()) == 1
        for it in m.items:
            if isinstance(it, Note):
                parts, rem = decompose(it.nominal)
                if rem:
                    self.warnings.append((it.line, "A note duration could not be notated exactly"))
                pitch = ks.pitch(it.degree, it.acc, it.octave) if it.degree else None
                for k, (v, t, dots) in enumerate(parts):
                    actual = v * it.scale
                    if actual.denominator != 1:
                        self.warnings.append((it.line, "Tuplet duration rounded"))
                        actual = Fraction(round(actual))
                    p = dict(note=it, dur=int(actual), type=t, dots=dots, pos=pos,
                             first=k == 0, last=k == len(parts) - 1, pitch=pitch, beams={},
                             mrest=(it.degree == 0 and only_note and len(parts) == 1 and it.dur == cap
                                    and not m.pickup))
                    pos += actual
                    seq.append(("p", p))
                    pieces.append(p)
            elif isinstance(it, Harmony):
                seq.append(("x", self.harmony(it, ks)))
            elif isinstance(it, Words):
                seq.append(("x", '      <direction placement="above"><direction-type>'
                                 f'<words>{escape(it.text)}</words></direction-type></direction>'))
            elif isinstance(it, Symbol):
                seq.append(("x", '      <direction placement="above"><direction-type>'
                                 f'<{it.kind}/></direction-type></direction>'))
            elif isinstance(it, Tempo):
                seq.append(("x", '      <direction placement="above"><direction-type><metronome>'
                                 f'<beat-unit>quarter</beat-unit><per-minute>{it.bpm}</per-minute>'
                                 f'</metronome></direction-type><sound tempo="{it.bpm}"/></direction>'))
            elif isinstance(it, Attr):
                a = ["      <attributes>"]
                if it.key:
                    ks = self.ks(it.key)
                    a.append(f"<key><fifths>{ks.fifths}</fifths></key>")
                if it.time:
                    time = it.time
                    a.append(f"<time><beats>{time[0]}</beats><beat-type>{time[1]}</beat-type></time>")
                a.append("</attributes>")
                seq.append(("x", "".join(a)))
        beat = Fraction(WHOLE, m.time[1])
        if m.time[1] == 8 and m.time[0] % 3 == 0 and m.time[0] >= 6:
            beat *= 3
        _beams(pieces, beat)
        for kind, v in seq:
            o.append(v if kind == "x" else self.note_xml(v))

        if m.right_style != "regular" or m.right_repeat or m.ending_stop:
            b = ['      <barline location="right">']
            b.append(f"<bar-style>{m.right_style if m.right_style != 'regular' else 'regular'}</bar-style>")
            if m.ending_stop:
                b.append(f'<ending number="{m.ending_stop[0]}" type="{m.ending_stop[1]}"/>')
            if m.right_repeat:
                b.append('<repeat direction="backward"/>')
            b.append("</barline>")
            o.append("".join(b))
        o.append("    </measure>")
        return ks

    def harmony(self, h, ks):
        step, alter, _, _ = ks.pitch(h.degree, h.acc)
        x = ['      <harmony print-frame="no"><root>', f"<root-step>{step}</root-step>"]
        if alter:
            x.append(f"<root-alter>{alter}</root-alter>")
        x.append(f'</root><kind text="{escape(h.suffix, {chr(34): "&quot;"})}">{h.kind}</kind>')
        if h.bass:
            bs, ba, _, _ = ks.pitch(h.bass[0], h.bass[1])
            x.append(f"<bass><bass-step>{bs}</bass-step>" + (f"<bass-alter>{ba}</bass-alter>" if ba else "") + "</bass>")
        x.append("</harmony>")
        return "".join(x)

    def note_xml(self, p):
        n = p["note"]
        self.note_map.append((n.line, n.col[0], n.col[1]))
        rest = n.degree == 0
        tie_in = not rest and ((p["first"] and n.tie_stop) or not p["first"])
        tie_out = not rest and ((p["last"] and n.tie_start) or not p["last"])
        x = ["      <note>"]
        if rest:
            x.append('<rest measure="yes"/>' if p["mrest"] else "<rest/>")
        else:
            step, alter, octv, _ = p["pitch"]
            x.append(f"<pitch><step>{step}</step>" + (f"<alter>{alter}</alter>" if alter else "")
                     + f"<octave>{octv}</octave></pitch>")
        x.append(f"<duration>{p['dur']}</duration>")
        if tie_in:
            x.append('<tie type="stop"/>')
        if tie_out:
            x.append('<tie type="start"/>')
        x.append("<voice>1</voice>")
        x.append(f"<type>{p['type']}</type>" + "<dot/>" * p["dots"])
        if n.tuplet:
            x.append(f"<time-modification><actual-notes>{n.tuplet[0]}</actual-notes>"
                     f"<normal-notes>{n.tuplet[1]}</normal-notes></time-modification>")
        for level in sorted(p["beams"]):
            x.append(f'<beam number="{level}">{p["beams"][level]}</beam>')
        nots = []
        if tie_in:
            nots.append('<tied type="stop"/>')
        if tie_out:
            nots.append('<tied type="start"/>')
        if p["last"] and n.slur_stop:
            nots += [f'<slur type="stop" number="{k}"/>' for k in n.slur_stop]
        if p["first"] and n.slur_start:
            nots += [f'<slur type="start" number="{k}"/>' for k in n.slur_start]
        if p["first"] and n.tuplet_start:
            nots.append('<tuplet type="start" bracket="yes"/>')
        if p["last"] and n.tuplet_stop:
            nots.append('<tuplet type="stop"/>')
        if p["last"] and n.fermata:
            nots.append('<fermata type="upright"/>')
        if nots:
            x.append("<notations>" + "".join(nots) + "</notations>")
        if p["first"] and not n.tie_stop:
            for verse in sorted(n.lyrics):
                t, syl = n.lyrics[verse]
                x.append(f'<lyric number="{verse}"><syllabic>{syl}</syllabic><text>{escape(t)}</text></lyric>')
        x.append("</note>")
        return "".join(x)


# --------------------------------------------------------------------------- output

def mxl_bytes(xml: str) -> bytes:
    """Compressed MusicXML (.mxl) container."""
    import io
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                 '<rootfile full-path="score.musicxml" media-type="application/vnd.recordare.musicxml+xml"/>'
                 '</rootfiles></container>\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.recordare.musicxml", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("score.musicxml", xml)
    return buf.getvalue()


def write_musicxml(path, xml: str):
    path = str(path)
    if path.lower().endswith(".mxl"):
        with open(path, "wb") as f:
            f.write(mxl_bytes(xml))
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)

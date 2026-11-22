"""omr - offline jianpu reader built on OpenCV (no network, no API).

Reads the melody line: digits 0-7, octave dots, underlines, augmentation
dots, dashes, accidentals, barlines/repeats, ties/slurs, triplet marks and
(optionally) large chord numbers above the melody. Lyrics, chord qualities
(m, 7, /bass) and printed words are not read - add those in the editor.

    from omr import recognize, load_pages
    res = recognize(load_pages("scan.pdf")[0], key="D", time="4/4")
    print(res.text)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

import fonts

NORM_H = 32                     # melody digits are rescaled to this height
CLASSES = "0123456789#b"
DIGITS = set("01234567")
CACHE = Path(os.environ.get("JIANPU_CACHE", Path.home() / ".cache" / "jianpu2xml"))


class OMRError(Exception):
    pass


# =========================================================================== page loading

def load_pages(path, dpi=250):
    """-> list of BGR images (numpy). PDF via PyMuPDF, falling back to pdftoppm."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        data = np.fromfile(str(path), np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise OMRError(f"Can't read image {path.name}")
        return [_to_bgr(img)]
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        pages = []
        with fitz.open(str(path)) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=dpi)
                arr = np.frombuffer(pix.tobytes("png"), np.uint8)
                pages.append(_to_bgr(cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)))
        return pages
    except ImportError:
        pass
    if not shutil.which("pdftoppm"):
        raise OMRError("PDF needs PyMuPDF (pip install pymupdf) or poppler-utils (pdftoppm)")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), f"{td}/p"], check=True,
                       capture_output=True, timeout=300)
        files = sorted(Path(td).glob("p*.png"), key=lambda f: int(f.stem.split("-")[-1]))
        return [_to_bgr(cv2.imread(str(f), cv2.IMREAD_UNCHANGED)) for f in files]


def _to_bgr(img):
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255
        rgb = img[:, :, :3].astype(np.float32)
        return (rgb * a + 255 * (1 - a)).astype(np.uint8)
    return img


# =========================================================================== glyph classifier

def _hog(img):
    """HOG for a 32x32 uint8 image: 8x8 cells, 2x2-cell blocks, 9 unsigned bins (324 values).
    Pure NumPy, so it works with any OpenCV build."""
    f = img.astype(np.float32)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:-1] = f[:, 2:] - f[:, :-2]
    gy[1:-1, :] = f[2:, :] - f[:-2, :]
    mag = np.hypot(gx, gy)
    ang = np.rad2deg(np.arctan2(gy, gx)) % 180.0
    pos = ang / 20.0 - 0.5                       # bin centres at 10, 30, ... 170 degrees
    b0 = np.floor(pos).astype(int)
    w1 = pos - b0
    b0 %= 9
    b1 = (b0 + 1) % 9
    cells = np.zeros((4, 4, 9), np.float32)
    cy, cx = np.divmod(np.arange(32 * 32), 32)
    cy, cx = (cy // 8), (cx // 8)
    m, b0f, b1f, w1f = mag.ravel(), b0.ravel(), b1.ravel(), w1.ravel()
    np.add.at(cells, (cy, cx, b0f), m * (1 - w1f))
    np.add.at(cells, (cy, cx, b1f), m * w1f)
    out = []
    for by in range(3):
        for bx in range(3):
            v = cells[by:by + 2, bx:bx + 2].ravel()
            v = v / np.sqrt((v * v).sum() + 1e-6)
            v = np.minimum(v, 0.2)
            v = v / np.sqrt((v * v).sum() + 1e-6)
            out.append(v)
    return np.concatenate(out)


def features(patch):
    """patch: uint8, ink=255, tightly cropped -> HOG + shape features"""
    h, w = patch.shape
    s = 28.0 / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    r = cv2.resize(patch, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((32, 32), np.uint8)
    y0, x0 = (32 - nh) // 2, (32 - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    hog = _hog(canvas)
    small = cv2.resize(canvas, (8, 8), interpolation=cv2.INTER_AREA).ravel().astype(np.float32) / 255
    return np.concatenate([hog, small * 0.35]).astype(np.float32)


def _crop(img):
    ys, xs = np.nonzero(img > 127)
    if len(ys) == 0:
        return None
    return ((img[ys.min():ys.max() + 1, xs.min():xs.max() + 1] > 127) * 255).astype(np.uint8)


def _template_glyphs():
    from PIL import Image, ImageDraw, ImageFont
    out = []
    hershey = [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_COMPLEX,
               cv2.FONT_HERSHEY_TRIPLEX]
    for ch in CLASSES:
        for f in hershey:
            for th in (2, 3, 5):
                img = np.zeros((90, 90), np.uint8)
                cv2.putText(img, ch, (20, 70), f, 1.8, 255, th, cv2.LINE_AA)
                c = _crop(img)
                if c is not None:
                    out.append((ch, c))
        for path in fonts.latin_fonts():
            try:
                font = ImageFont.truetype(path, 64)
            except OSError:
                continue
            im = Image.new("L", (110, 110), 0)
            ImageDraw.Draw(im).text((25, 10), ch, fill=255, font=font)
            c = _crop(np.array(im))
            if c is not None:
                out.append((ch, c))
    return out


def _augment(patch):
    yield patch
    k = np.ones((3, 3), np.uint8)
    yield cv2.dilate(patch, k)
    e = cv2.erode(patch, np.ones((2, 2), np.uint8))
    if e.any():
        yield e
    h, w = patch.shape
    for ang in (-4, 4):
        pad = cv2.copyMakeBorder(patch, 8, 8, 8, 8, cv2.BORDER_CONSTANT, 0)
        M = cv2.getRotationMatrix2D((pad.shape[1] / 2, pad.shape[0] / 2), ang, 1)
        c = _crop(cv2.warpAffine(pad, M, (pad.shape[1], pad.shape[0])))
        if c is not None:
            yield c
    for sx in (0.8, 1.2):
        yield cv2.resize(patch, (max(1, int(w * sx)), h), interpolation=cv2.INTER_LINEAR)
    small = cv2.resize(patch, (max(1, w // 3), max(1, h // 3)), interpolation=cv2.INTER_AREA)
    c = _crop(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR))
    if c is not None:
        yield c


class Classifier:
    def __init__(self):
        sig = "|".join(fonts.latin_fonts()) + "|v4-numpy"
        CACHE.mkdir(parents=True, exist_ok=True)
        cache = CACHE / "glyphs.npz"
        X = y = None
        if cache.exists():
            try:
                z = np.load(cache, allow_pickle=False)
                if str(z["sig"]) == sig:
                    X, y = z["X"], z["y"]
            except (OSError, ValueError, KeyError):
                pass
        if X is None:
            feats, labels = [], []
            for ch, patch in _template_glyphs():
                for a in _augment(patch):
                    feats.append(features(a))
                    labels.append(CLASSES.index(ch))
            X, y = np.array(feats, np.float32), np.array(labels, np.int32)
            try:
                np.savez_compressed(cache, X=X, y=y, sig=sig)
            except OSError:
                pass
        self.X, self.y = X, y
        self.Xn = (X * X).sum(axis=1)
        # rejection threshold from the spread of the training set itself
        _, d = self._nearest(X[::7], 2)
        self.thr = float(np.percentile(d[:, 1], 97)) * 2.2

    def _nearest(self, F, k):
        """k nearest training samples (squared L2), pure NumPy -> (indices, distances)"""
        d = (F * F).sum(axis=1)[:, None] + self.Xn[None, :] - 2.0 * F @ self.X.T
        np.maximum(d, 0, out=d)
        idx = np.argpartition(d, k, axis=1)[:, :k]
        dd = np.take_along_axis(d, idx, axis=1)
        order = np.argsort(dd, axis=1)
        return np.take_along_axis(idx, order, axis=1), np.take_along_axis(dd, order, axis=1)

    def classify(self, patches):
        """-> list of (label or None, distance, loose label or None)"""
        if not patches:
            return []
        F = np.array([features(p) for p in patches], np.float32)
        out = []
        for i in range(0, len(F), 512):              # chunks keep memory low on a Pi
            idx, dist = self._nearest(F[i:i + 512], 5)
            for nb_idx, ds in zip(idx, dist):
                labs = self.y[nb_idx]
                vals, counts = np.unique(labs, return_counts=True)
                best = vals[np.argmax(counts)]
                votes = int(counts.max())
                lab = CLASSES[int(best)]
                dmin = float(ds[labs == best][0])
                ok = votes >= 3 and dmin <= self.thr
                out.append((lab if ok else None, dmin, lab if votes >= 3 and dmin <= self.thr * 1.6 else None))
        return out


@lru_cache(maxsize=1)
def classifier():
    return Classifier()


# =========================================================================== geometry helpers

@dataclass(eq=False)
class Comp:
    x: int
    y: int
    w: int
    h: int
    area: int
    patch: np.ndarray = field(repr=False, default=None)
    label: str | None = None
    kind: str = ""

    @property
    def x2(self): return self.x + self.w

    @property
    def y2(self): return self.y + self.h

    @property
    def cx(self): return self.x + self.w / 2

    @property
    def cy(self): return self.y + self.h / 2

    @property
    def fill(self): return self.area / max(1, self.w * self.h)


def _components(bw, min_area=3):
    n, lab, st, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_area:
            continue
        patch = ((lab[y:y + h, x:x + w] == i) * 255).astype(np.uint8)
        out.append(Comp(int(x), int(y), int(w), int(h), int(a), patch))
    return out


def _gray(img, black_only=True):
    if img.ndim == 2:
        return img
    if black_only:
        # max channel: coloured ink and coloured highlights turn light, black stays dark
        return img.max(axis=2)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _deskew(gray):
    h, w = gray.shape
    s = 900 / max(w, h)
    small = cv2.resize(gray, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else gray
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    best, best_score = 0.0, -1
    c = (bw.shape[1] / 2, bw.shape[0] / 2)
    for ang in np.arange(-3, 3.01, 0.25):
        M = cv2.getRotationMatrix2D(c, ang, 1)
        r = cv2.warpAffine(bw, M, (bw.shape[1], bw.shape[0]))
        score = float(np.var(r.sum(axis=1)))
        if score > best_score:
            best, best_score = float(ang), score
    if abs(best) < 0.2:
        return gray, 0.0, None
    M = cv2.getRotationMatrix2D((w / 2, h / 2), best, 1)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255), best, M


def _estimate_digit_height(gray):
    f = 2 if max(gray.shape) < 1600 else 1
    g = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1 else gray
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    comps = [c for c in _components(bw, 12)
             if c.h >= 10 and 0.12 <= c.w / c.h <= 1.0 and c.h < g.shape[0] / 6 and c.fill > 0.12]
    if not comps:
        return None
    labels = classifier().classify([c.patch for c in comps])
    hs = np.array([c.h for c, (lab, _, _) in zip(comps, labels) if lab in DIGITS], np.float32)
    if len(hs) < 4:
        return None
    counts = [(np.sum(np.abs(hs - h) <= 0.12 * h), h) for h in hs]
    _, mode_h = max(counts)
    near = hs[np.abs(hs - mode_h) <= 0.12 * mode_h]
    return float(np.median(near)) / f


def _binarize(gray, H):
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    blk = int(H * 3) | 1
    ad = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, blk, 12)
    dark = (gray < min(t + 35, 235)).astype(np.uint8) * 255
    return cv2.bitwise_and(ad, dark) | otsu & dark


# =========================================================================== recognizer

@dataclass(eq=False)
class Note:
    c: Comp
    deg: int
    acc: str = ""
    up: int = 0
    down: int = 0
    under: int = 0
    dots: int = 0
    tie: bool = False
    slur_open: int = 0
    slur_close: int = 0
    tup_open: int = 0
    tup_close: bool = False
    chord: str = ""
    chord_glyph: object = None
    under_y: float = 0.0


@dataclass
class Result:
    text: str
    warnings: list
    overlay: np.ndarray
    rows: int
    notes: int
    digit_height: float
    skew: float
    notes_map: list = field(default_factory=list)   # dicts: row, k, box (overlay px), page_box (original px)


def recognize(img, key="C", time="4/4", title="", black_only=True, read_chords=True,
              header=True) -> Result:
    clf = classifier()
    warnings = []
    gray = _gray(_to_bgr(img) if img.ndim == 3 else img, black_only)
    gray, skew, M_skew = _deskew(gray)
    H0 = _estimate_digit_height(gray)
    if not H0:
        raise OMRError("No jianpu digits found. Is this a numbered-notation page? "
                       "Try a sharper or higher-resolution scan.")
    if H0 < 9:
        warnings.append((0, f"Digits are only ~{H0:.0f}px tall; a higher-resolution scan will read better"))
    scale = NORM_H / H0
    gray = cv2.resize(gray, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    H = float(NORM_H)
    bw = _binarize(gray, H)

    # 1) glyphs on the full image (digits, accidentals), splitting touching digits
    glyphs = []
    rest = bw.copy()
    cand, cpatch = [], []
    for c in _components(bw, 6):
        if 0.35 * H <= c.h <= 3.0 * H and 0.12 <= c.w / c.h <= 1.05 and c.fill > 0.12:
            cand.append(c)
            cpatch.append(c.patch)
    loose = []                             # less certain glyphs: only used as chord numbers
    for c, (lab, _, lab2) in zip(cand, clf.classify(cpatch)):
        if lab is not None and lab not in "89":
            c.label = lab
            glyphs.append(c)
        elif lab2 in set("1234567") and c.h >= 1.1 * H:
            c.label = lab2
            loose.append(c)
    for c in _components(bw, 6):           # touching digits: wide components at digit height
        if 0.8 * H <= c.h <= 1.3 * H and c.w > 1.05 * c.h and c.w < 6 * H and c.fill > 0.2:
            glyphs.extend(_split_touching(c, H, clf))
    for g in glyphs:
        sub = rest[g.y:g.y2, g.x:g.x2]
        sub[g.patch > 0] = 0

    # 2) arcs (ties/slurs) first, so line extraction doesn't chop them up
    arcs = []
    for c in _components(rest, 6):
        if c.w >= 0.55 * H and 0.2 * H <= c.h <= 1.0 * H and c.fill <= 0.45 and c.w >= 1.4 * c.h:
            arcs.append(c)
            rest[c.y:c.y2, c.x:c.x2][c.patch > 0] = 0
    # 3) separate long horizontal / vertical lines from the remainder
    hl = cv2.morphologyEx(rest, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(0.9 * H), 1)))
    vl = cv2.morphologyEx(rest, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(1.25 * H))))
    other = rest & ~hl & ~vl
    hlines = [c for c in _components(hl, 4) if c.h <= 0.3 * H]
    vlines = [c for c in _components(vl, 4) if c.w <= 0.35 * H]
    dots, small_h, misc = [], [], []
    for c in _components(other, 3):
        if c.w <= 0.36 * H and c.h <= 0.36 * H and c.w >= 0.07 * H and c.h >= 0.07 * H \
                and 0.45 <= c.w / max(1, c.h) <= 2.2 and c.fill >= 0.45:
            dots.append(c)
        elif c.h <= 0.3 * H and c.w >= 0.3 * H and c.fill >= 0.5:
            small_h.append(c)
        elif c.h >= 1.25 * H and c.w <= 0.35 * H:
            vlines.append(c)
        else:
            misc.append(c)
    hlines += small_h
    line_mask = hl.copy()
    for c in small_h:
        line_mask[c.y:c.y2, c.x:c.x2][c.patch > 0] = 255
    long_h = [c.h for c in hlines if c.w >= 1.2 * H]
    t_line = max(2.0, float(np.median(long_h))) if long_h else max(2.0, 0.08 * H)

    main = [g for g in glyphs if g.label in DIGITS and 0.78 * H <= g.h <= 1.32 * H]
    smalls = [g for g in glyphs if g.h < 0.78 * H]
    big = []                               # chord-number candidates, filled after rows are known

    # 3) rows of melody digits
    rows = []
    for g in sorted(main, key=lambda g: g.cy):
        if rows and abs(g.cy - rows[-1]["cy"]) <= 0.45 * H:
            r = rows[-1]
            r["d"].append(g)
            r["cy"] = float(np.mean([d.cy for d in r["d"]]))
        else:
            rows.append({"d": [g], "cy": g.cy})
    kept = []
    for r in rows:
        ds = r["d"]
        if len(ds) < 3:
            continue
        top = float(np.median([d.y for d in ds]))
        bot = float(np.median([d.y2 for d in ds]))
        others = [c for c in misc + [g for g in glyphs if g not in ds and g.h >= 0.5 * H]
                  if c.h >= 0.45 * H and c.cy > top and c.cy < bot]
        if len(others) > 0.6 * len(ds) + 2:
            continue                       # lyric line or text, not melody
        r.update(top=top, bot=bot, mid=(top + bot) / 2)
        kept.append(r)
    if not kept:
        raise OMRError("Found digits but no melody rows. Check the scan is upright and in focus.")
    # rows without barlines sitting just above a barred row are chord-number rows
    def has_bars(r):
        return any(v.y <= r["mid"] <= v.y2 and v.h >= 1.15 * H for v in vlines)
    barred = [has_bars(r) for r in kept]
    chord_rows = set()
    for i, r in enumerate(kept):
        if not barred[i] and i + 1 < len(kept) and barred[i + 1] and kept[i + 1]["top"] - r["bot"] <= 3.8 * H:
            chord_rows.add(i)
            for g in r["d"]:
                if g.label in "1234567":
                    big.append(g)
    kept = [r for i, r in enumerate(kept) if i not in chord_rows]
    in_rows = {id(g) for r in kept for g in r["d"]}
    big += [g for g in glyphs + loose if g.label in "1234567" and g.h >= 0.68 * H and id(g) not in in_rows
            and g not in big]

    used_dots = set()
    lines_out = []
    notes_map = []
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    note_count = 0
    for ri, r in enumerate(kept):
        T, B, M = r["top"], r["bot"], r["mid"]
        prev_bottom = kept[ri - 1]["bot"] if ri else -1e9
        notes = [Note(d, int(d.label)) for d in sorted(r["d"], key=lambda d: d.x)]
        xs = [n.c.cx for n in notes]

        def nearest_note(cx, maxd):
            if not notes:
                return None
            i = int(np.argmin([abs(x - cx) for x in xs]))
            return notes[i] if abs(xs[i] - cx) <= maxd else None

        # barlines
        bars = sorted([v for v in vlines if v.y <= M <= v.y2 and v.h >= 1.15 * H], key=lambda v: v.x)
        bar_groups = []
        for v in bars:
            if bar_groups and v.x - bar_groups[-1][-1].x2 <= 0.5 * H:
                bar_groups[-1].append(v)
            else:
                bar_groups.append([v])
        bar_tokens = []
        for grp in bar_groups:
            gx1, gx2 = grp[0].x, grp[-1].x2
            inband = [d for d in dots if id(d) not in used_dots and T - 0.1 * H <= d.cy <= B + 0.1 * H]
            left = [d for d in inband if gx1 - 0.8 * H <= d.cx < gx1]
            right = [d for d in inband if gx2 < d.cx <= gx2 + 0.8 * H]
            for d in left + right:
                used_dots.add(id(d))
            ws = [v.w for v in grp]
            thick = len(grp) >= 2 and max(ws) >= 1.5 * min(ws)
            if left and right:
                sym = ":|:"
            elif left:
                sym = ":|"
            elif right:
                sym = "|:"
            elif len(grp) >= 2:
                sym = "|]" if thick and ws[-1] >= ws[0] else "||"
            else:
                sym = "|"
            bar_tokens.append(((gx1 + gx2) / 2, sym, grp))

        # dashes and underlines
        dashes = []
        unders = []
        for hline in hlines:
            if T + 0.2 * H <= hline.cy <= B - 0.15 * H and 0.3 * H <= hline.w <= 2.5 * H:
                if not any(n.c.x - 0.05 * H < hline.cx < n.c.x2 + 0.05 * H for n in notes):
                    dashes.append(hline)
            elif B - 0.15 * H <= hline.y <= B + 1.35 * H:
                unders.append(hline)
        for n in notes:
            n.under, n.under_y = _underline_levels(line_mask, n.c, B, H, t_line)

        # dots: octave above/below, augmentation right
        for d in dots:
            if id(d) in used_dots:
                continue
            if T - 1.2 * H <= d.cy <= T + 0.05 * H and d.cy > prev_bottom + 1.4 * H:
                n = nearest_note(d.cx, 0.42 * H)
                if n:
                    n.up += 1
                    used_dots.add(id(d))
            elif B <= d.cy <= B + 1.7 * H:
                n = nearest_note(d.cx, 0.42 * H)
                if n and d.cy <= max(n.c.y2, n.under_y) + 0.6 * H + 0.3 * H * n.down:
                    n.down += 1
                    used_dots.add(id(d))
            elif T + 0.25 * H <= d.cy <= B + 0.1 * H:
                left = [n for n in notes if n.c.x2 <= d.cx + 0.05 * H and d.cx - n.c.x2 <= 0.9 * H]
                if left:
                    n = max(left, key=lambda n: n.c.x2)
                    n.dots += 1
                    used_dots.add(id(d))
        for n in notes:
            n.up, n.down = min(n.up, 3), min(n.down, 3)

        # accidentals
        for g in smalls + [g for g in glyphs if g.label in "#b"]:
            if g.label not in "#b6" or g.h > 0.95 * H:
                continue
            for n in notes:
                if -0.2 * H <= n.c.x - g.x2 <= 0.6 * H and g.cx < n.c.x and g.y2 > T and g.y < B:
                    n.acc = "#" if g.label == "#" else "b"

        # ties / slurs
        for a in arcs:
            if not (T - 2.2 * H <= a.y and a.y2 <= T + 0.35 * H):
                continue
            ln = nearest_note(a.x + 0.15 * H, 0.75 * H)
            rn = nearest_note(a.x2 - 0.15 * H, 0.75 * H)
            if not ln or not rn or ln is rn:
                continue
            i1, i2 = notes.index(ln), notes.index(rn)
            if i1 > i2:
                continue
            if i2 == i1 + 1 and (ln.deg, ln.up, ln.down, ln.acc) == (rn.deg, rn.up, rn.down, rn.acc):
                ln.tie = True
            else:
                ln.slur_open += 1
                rn.slur_close += 1
            a.kind = "arc"

        # triplets: small 3 above the row
        for g in smalls:
            if g.label != "3" or not (T - 2.2 * H <= g.y and g.y2 <= T + 0.1 * H):
                continue
            span = [a for a in arcs if a.x <= g.cx <= a.x2 and abs(a.cy - g.cy) <= 1.2 * H]
            if span:
                grp = [n for n in notes if span[0].x - 0.3 * H <= n.c.cx <= span[0].x2 + 0.3 * H]
            else:
                grp = sorted(notes, key=lambda n: abs(n.c.cx - g.cx))[:3]
                grp.sort(key=lambda n: n.c.x)
            if len(grp) >= 2:
                grp[0].tup_open = 3
                grp[-1].tup_close = True
                # the "tie/slur" drawn as the triplet bracket is not a slur
                if span:
                    grp[0].slur_open = max(0, grp[0].slur_open - 1)
                    grp[-1].slur_close = max(0, grp[-1].slur_close - 1)
                    grp[0].tie = False
                g.kind = "triplet"

        # chord numbers above the row
        if read_chords:
            for g in sorted(big, key=lambda g: g.x):
                if T - 4.0 * H <= g.y and g.y2 <= T - 0.2 * H and g.y2 > prev_bottom + 0.3 * H:
                    after = [n for n in notes if n.c.cx >= g.x - 0.4 * H]
                    if not after:
                        continue
                    # a second number just right of a chord on the same line is its bass: 6m/5, 5/7
                    host = [o for o in big if o.kind == "chord" and 0 < g.x - o.x2 <= 2.6 * H
                            and abs(o.cy - g.cy) <= 0.6 * H and _slash_between(o, g, misc + vlines)]
                    if host:
                        hn = next((n for n in notes if n.chord_glyph is host[0]), None)
                        if hn and "/" not in hn.chord:
                            hn.chord += "/" + g.label
                            g.kind = "chord"
                            continue
                    if not after[0].chord:
                        after[0].chord = g.label
                        after[0].chord_glyph = g
                        g.kind = "chord"

        # assemble tokens
        toks = [(n.c.x, "n", n) for n in notes] + [(d.x, "-", d) for d in dashes] + \
               [(x, "|", sym) for x, sym, _ in bar_tokens]
        toks.sort(key=lambda t: t[0])
        out = []
        for _, kind, obj in toks:
            if kind == "-":
                out.append("-")
            elif kind == "|":
                out.append(obj)
            else:
                n = obj
                s = ""
                if n.chord:
                    out.append(f"<{n.chord}>")
                if n.tup_open:
                    out.append(f"{n.tup_open}{{")
                s += "(" * n.slur_open + n.acc + str(n.deg) + "'" * n.up + "," * n.down
                s += "_" * n.under + "." * n.dots + ("~" if n.tie else "") + ")" * n.slur_close
                out.append(s)
                if n.tup_close:
                    out.append("}")
        lines_out.append(" ".join(out))
        note_count += len(notes)
        for k, n in enumerate(notes):
            box = (n.c.x - 0.15 * H, n.c.y - 0.7 * H, n.c.x2 + 0.15 * H, n.c.y2 + 0.9 * H)
            notes_map.append({"row": ri, "k": k, "box": [round(v, 1) for v in box],
                              "page_box": _to_page(box, scale, M_skew)})

        # overlay
        for n in notes:
            cv2.rectangle(overlay, (n.c.x, n.c.y), (n.c.x2, n.c.y2), (40, 160, 40), 1)
            lab = n.acc + str(n.deg) + "'" * n.up + "," * n.down + "_" * n.under + "." * n.dots
            cv2.putText(overlay, lab, (n.c.x, n.c.y2 + int(0.95 * H) + 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (0, 130, 0), 1, cv2.LINE_AA)
        for _, _, grp in bar_tokens:
            for v in grp:
                cv2.rectangle(overlay, (v.x, v.y), (v.x2, v.y2), (220, 90, 0), 2)
        for d in dashes:
            cv2.rectangle(overlay, (d.x, d.y - 2), (d.x2, d.y2 + 2), (200, 160, 0), 2)
        for u in [u for u in unders if any(min(u.x2, n.c.x2) - max(u.x, n.c.x) > 0 and u.cy <= n.under_y + 1
                                           and n.under for n in notes)]:
            cv2.rectangle(overlay, (u.x, u.y - 1), (u.x2, u.y2 + 1), (0, 140, 255), 1)
        cv2.line(overlay, (0, int(T)), (8, int(T)), (255, 0, 255), 2)
        cv2.line(overlay, (0, int(B)), (8, int(B)), (255, 0, 255), 2)
    for d in dots:
        if id(d) in used_dots:
            cv2.circle(overlay, (int(d.cx), int(d.cy)), max(3, d.w), (0, 0, 230), 1)
    for a in arcs:
        if a.kind == "arc":
            cv2.rectangle(overlay, (a.x, a.y), (a.x2, a.y2), (200, 0, 200), 1)
    for g in big + smalls:
        if g.kind in ("chord", "triplet"):
            cv2.rectangle(overlay, (g.x, g.y), (g.x2, g.y2), (160, 0, 160), 2)

    head = []
    if header:
        if title:
            head.append(f"title: {title}")
        head += [f"key: {key}", f"time: {time}", ""]
    if read_chords and any(g.kind == "chord" for g in big):
        warnings.append((0, "Chord numbers were read without their quality (m, 7, sus…): add those by hand"))
    text = "\n".join(head + lines_out) + "\n"
    return Result(text, warnings, overlay, len(kept), note_count, H0, skew, notes_map)


def _to_page(box, s, M):
    """Map a box from the normalised (deskewed, rescaled) image back to the original page."""
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], np.float64) / s
    if M is not None:
        inv = cv2.invertAffineTransform(M)
        pts = pts @ inv[:, :2].T + inv[:, 2]
    return [round(float(pts[:, 0].min()), 1), round(float(pts[:, 1].min()), 1),
            round(float(pts[:, 0].max()), 1), round(float(pts[:, 1].max()), 1)]


def _slash_between(a, b, comps):
    """Is there a '/' between chord glyphs a and b (6m/5, 5/7)?"""
    lo, hi = min(a.y, b.y), max(a.y2, b.y2)
    for c in comps:
        if a.x2 - 2 <= c.x and c.x2 <= b.x + 2 and c.y2 > lo and c.y < hi \
                and c.h >= 0.45 * min(a.h, b.h) and 0.2 <= c.w / max(1, c.h) <= 0.9 and c.fill <= 0.5:
            return True
    return False


def _underline_levels(mask, c, B, H, t_line):
    """Count stacked underlines under a digit from the ink profile of the line layer."""
    x1, x2 = int(c.x + 0.2 * c.w), int(c.x2 - 0.2 * c.w)
    y1, y2 = int(B - 0.1 * H), int(B + 1.3 * H)
    if x2 <= x1:
        x1, x2 = c.x, c.x2
    strip = mask[max(0, y1):y2, x1:x2]
    if strip.size == 0:
        return 0, B
    prof = (strip > 0).mean(axis=1) >= 0.5
    runs, start = [], None
    for i, v in enumerate(list(prof) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start + y1, i + y1))
            start = None
    levels, last_end = 0, None
    for a, b in runs:
        if last_end is None:
            if a > B + 0.45 * H:
                break
        elif a - last_end > 0.3 * H:
            break                          # further down = lyrics, not underlines
        thick = b - a
        if thick < 0.5 * t_line:
            continue
        levels += 2 if thick > 2.4 * t_line else 1    # two lines blurred together
        last_end = b
    return min(levels, 4), (last_end if last_end is not None else B)


def _split_touching(c, H, clf):
    """Split a wide component at digit height into separate digits."""
    k = int(round(c.w / (0.62 * H)))
    if k < 2 or k > 8:
        return []
    col = (c.patch > 0).sum(axis=0).astype(np.float32)
    cuts, step = [], c.w / k
    for i in range(1, k):
        lo, hi = int(step * i - step * 0.35), int(step * i + step * 0.35)
        lo, hi = max(1, lo), min(c.w - 1, hi)
        if hi <= lo:
            return []
        cuts.append(lo + int(np.argmin(col[lo:hi])))
    edges = [0] + cuts + [c.w]
    parts = []
    for a, b in zip(edges, edges[1:]):
        p = c.patch[:, a:b]
        ys, xs = np.nonzero(p)
        if len(ys) == 0:
            return []
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        parts.append(Comp(c.x + a + int(x0), c.y + int(y0), int(x1 - x0), int(y1 - y0),
                          int((p > 0).sum()), p[y0:y1, x0:x1].copy()))
    labels = clf.classify([p.patch for p in parts])
    if all(lab in DIGITS for lab, _, _ in labels) and all(p.h >= 0.7 * H for p in parts):
        for p, (lab, _, _) in zip(parts, labels):
            p.label = lab
        return parts
    return []


def overlay_png(result: Result) -> bytes:
    ok, buf = cv2.imencode(".png", result.overlay)
    return buf.tobytes() if ok else b""

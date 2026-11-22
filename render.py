"""render - draw jianpu text as a jianpu image (PNG) with Pillow.

Used for the web app's Preview tab (compare against the scan) and to make
synthetic test images for the OMR.
"""
from __future__ import annotations

import io
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

import fonts
import jianpu_core as core


def _font(path, size):
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    except OSError:
        return ImageFont.load_default(size)


def _split(q: Fraction):
    """beats (in units) -> (underlines, dot, dashes)"""
    if q >= 1:
        whole, frac = int(q), q - int(q)
        return 0, int(frac == Fraction(1, 2)), whole - 1
    u = 0
    while q < 1 and u < 5:
        q *= 2
        u += 1
    return u, int(q == Fraction(3, 2)), 0


def chord_text(h: core.Harmony) -> str:
    acc = "b" if h.acc < 0 else "#" if h.acc > 0 else ""
    s = f"{acc}{h.degree}{h.suffix}"
    if h.bass:
        s += "/" + ("b" if h.bass[1] < 0 else "#" if h.bass[1] > 0 else "") + str(h.bass[0])
    return s


class Renderer:
    def __init__(self, size=44, digit_font=None, page_width=None):
        self.S = size
        fpath = digit_font or fonts.ui_font(bold=True) or fonts.ui_font()
        self.f_digit = _font(fpath, size)
        self.f_small = _font(fpath, int(size * 0.55))
        self.f_chord = _font(fonts.ui_font(bold=True), int(size * 0.8))
        self.f_words = _font(fonts.ui_font(), int(size * 0.42))
        self.f_title = _font(fonts.ui_font(bold=True), int(size * 0.9))
        self.f_lyric = _font(fonts.cjk_font() or fonts.ui_font(), int(size * 0.55))
        bb = self.f_digit.getbbox("5")
        self.d_top, self.d_h, self.d_w = bb[1], bb[3] - bb[1], bb[2] - bb[0]
        self.page_width = page_width

    # ------------------------------------------------------------------ public
    def render(self, text: str) -> Image.Image:
        score = core.parse(text)
        S = self.S
        systems, verses = self._systems(score)
        self.boxes = []                          # (x0, y0, x1, y1, line, col0, col1) in page pixels
        imgs = []
        for sysm in systems:
            self._sys_boxes = []
            imgs.append((self._system(sysm, verses), self._sys_boxes))
        head = self._header(score)
        width = max([head.width] + [i.width for i, _ in imgs]) + 2 * S
        height = head.height + sum(i.height for i, _ in imgs) + S
        page = Image.new("RGB", (width, height), "white")
        page.paste(head, (S, int(S * 0.3)))
        y = head.height + int(S * 0.3)
        for im, boxes in imgs:
            page.paste(im, (S, y))
            for x0, y0, x1, y1, n in boxes:
                self.boxes.append((x0 + S, y0 + y, x1 + S, y1 + y, n.line, n.col[0], n.col[1]))
            y += im.height
        return page

    # ------------------------------------------------------------------ pages (PDF)
    def render_pages(self, text: str, meta=None, key_label=None, paper="A4", dpi=200):
        """Paginated jianpu pages (list of PIL images) with a title block and page numbers."""
        score = core.parse(text)
        meta = {**score.meta, **{k: v for k, v in (meta or {}).items() if v is not None}}
        W, H = (round(mm / 25.4 * dpi) for mm in PAPER.get(paper, PAPER["A4"]))
        margin = round(15 / 25.4 * dpi)
        content_w = W - 2 * margin
        systems, verses = self._systems(score)
        sys_imgs = []
        for sysm in systems:
            self._sys_boxes = []
            sys_imgs.append(self._system(sysm, verses))
        g = min(1.0, content_w / max([im.width for im in sys_imgs] + [1]))
        if g < 1:
            sys_imgs = [im.resize((max(1, int(im.width * g)), max(1, int(im.height * g))), Image.LANCZOS)
                        for im in sys_imgs]
        # trim the empty space under systems without lyrics
        sys_imgs = [_trim_bottom(im, pad=int(self.S * 0.5 * g)) for im in sys_imgs]

        cjk = fonts.cjk_font()
        f_title = _font(cjk or fonts.ui_font(bold=True), round(dpi * 0.26))
        f_sub = _font(cjk or fonts.ui_font(), round(dpi * 0.14))
        f_info = _font(cjk or fonts.ui_font(), round(dpi * 0.115))
        f_foot = _font(fonts.ui_font(), round(dpi * 0.07))

        pages = []

        def new_page():
            pg = Image.new("RGB", (W, H), "white")
            pages.append(pg)
            return pg, ImageDraw.Draw(pg)

        page, d = new_page()
        y = margin
        title = meta.get("title", "").strip()
        if title:
            tw = d.textlength(title, font=f_title)
            d.text(((W - tw) / 2, y), title, font=f_title, fill="black")
            y += round(f_title.size * 1.35)
        sub = meta.get("subtitle", "").strip()
        if sub:
            tw = d.textlength(sub, font=f_sub)
            d.text(((W - tw) / 2, y), sub, font=f_sub, fill="black")
            y += round(f_sub.size * 1.5)
        key = core.parse_key(score.key)
        left = [f"1={key_label or (key[2] if key else score.key)}   {score.time[0]}/{score.time[1]}"]
        if any(isinstance(i, core.Tempo) for m in score.measures[:1] for i in m.items):
            bpm = next(i.bpm for i in score.measures[0].items if isinstance(i, core.Tempo))
            left.append(f"♩ = {bpm}")
        right = [f"{lab}{meta[k]}" for k, lab in (("composer", "Music: "), ("lyricist", "Lyrics: "),
                                                   ("arranger", "Arr.: ")) if meta.get(k)]
        ly = y
        for i, line in enumerate(left):
            d.text((margin, ly + i * f_info.size * 1.4), line, font=f_info, fill="black")
        for i, line in enumerate(right):
            tw = d.textlength(line, font=f_info)
            d.text((W - margin - tw, ly + i * f_info.size * 1.4), line, font=f_info, fill="black")
        y = ly + round(max(len(left), len(right), 1) * f_info.size * 1.4) + round(dpi * 0.12)

        bottom = H - margin - round(f_foot.size * 1.5)
        for im in sys_imgs:
            if y + im.height > bottom and y > margin + 1:
                page, d = new_page()
                y = margin
            page.paste(im, (margin, y))
            y += im.height + round(dpi * 0.08)
        n = len(pages)
        if n > 1:
            for i, pg in enumerate(pages, 1):
                dd = ImageDraw.Draw(pg)
                label = f"{i} / {n}"
                tw = dd.textlength(label, font=f_foot)
                dd.text(((W - tw) / 2, H - margin + f_foot.size * 0.3), label, font=f_foot, fill="#555")
        return pages

    def png(self, text: str) -> bytes:
        buf = io.BytesIO()
        self.render(text).save(buf, "PNG", optimize=True)
        return buf.getvalue()

    @staticmethod
    def _systems(score):
        """Measures grouped by source line (one printed system per text line)."""
        systems, cur, cur_line = [], [], None
        for m in score.measures:
            if cur and m.line != cur_line:
                systems.append(cur)
                cur = []
            if not cur:
                cur_line = m.line
            cur.append(m)
        if cur:
            systems.append(cur)
        verses = max([max(n.lyrics) for m in score.measures for n in m.notes() if n.lyrics] or [0])
        return systems, verses

    # ------------------------------------------------------------------ parts
    def _header(self, score):
        S = self.S
        img = Image.new("RGB", (int(S * 20), int(S * 1.8)), "white")
        d = ImageDraw.Draw(img)
        key = core.parse_key(score.key)
        d.text((0, int(S * 0.5)), f"1={key[2] if key else score.key}  {score.time[0]}/{score.time[1]}",
               font=self.f_words, fill="black")
        title = score.meta.get("title", "")
        if title:
            d.text((int(S * 5), 0), title, font=self.f_title, fill="black")
        return img

    def _system(self, measures, verses):
        S = self.S
        cw = int(S * 0.78)                       # note cell width
        y_top = int(S * 2.3)                     # digit top
        y_bot = y_top + self.d_h
        y_lyr = y_bot + int(S * 1.15)
        height = y_lyr + int(S * 0.8) * max(verses, 0) + int(S * 0.6)
        est = sum(len(m.items) * 4 + 6 for m in measures) * cw + S * 4
        img = Image.new("RGB", (est, height), "white")
        d = ImageDraw.Draw(img)
        x = int(S * 0.2)
        r = max(2, int(S * 0.065))
        centers = []                             # (note, cx)
        pending = []                             # chord / words waiting for the next note
        volta_starts = []

        def dot(cx, cy):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="black")

        def barline(x, kind):
            top, bot = y_top - int(S * 0.35), y_bot + int(S * 0.35)
            thin = max(2, S // 22)
            thick = thin * 3
            if kind in ("|", ""):
                d.rectangle((x, top, x + thin, bot), fill="black")
                return x + thin
            if kind == "||":
                d.rectangle((x, top, x + thin, bot), fill="black")
                d.rectangle((x + thin * 3, top, x + thin * 4, bot), fill="black")
                return x + thin * 4
            if kind == "|]":
                d.rectangle((x, top, x + thin, bot), fill="black")
                d.rectangle((x + thin * 3, top, x + thin * 3 + thick, bot), fill="black")
                return x + thin * 3 + thick
            if kind == ":|":
                dot(x, y_top + self.d_h * 0.3)
                dot(x, y_top + self.d_h * 0.7)
                x += r * 3
                d.rectangle((x, top, x + thin, bot), fill="black")
                d.rectangle((x + thin * 3, top, x + thin * 3 + thick, bot), fill="black")
                return x + thin * 3 + thick
            if kind == "|:":
                d.rectangle((x, top, x + thick, bot), fill="black")
                d.rectangle((x + thick + thin * 2, top, x + thick + thin * 3, bot), fill="black")
                x2 = x + thick + thin * 3 + r * 3
                dot(x2, y_top + self.d_h * 0.3)
                dot(x2, y_top + self.d_h * 0.7)
                return x2 + r
            return x

        for mi, m in enumerate(measures):
            unit = Fraction(core.WHOLE, m.time[1])
            if m.left_repeat:
                x = barline(x, "|:") + int(S * 0.3)
            if m.ending_start:
                volta_starts.append((x, m.ending_start))
            pos = Fraction(0)
            for it in m.items:
                if isinstance(it, (core.Harmony, core.Words, core.Symbol)):
                    pending.append(it)
                    continue
                if isinstance(it, core.Attr) and it.key:
                    pending.append(core.Words(f"1={it.key}"))
                    continue
                if not isinstance(it, core.Note):
                    continue
                n = it
                u, dotted, dashes = _split(n.nominal / unit)
                if n.acc:
                    d.text((x, y_top - int(S * 0.12) - self.f_small.getbbox("#")[1]),
                           "#" if n.acc > 0 else "b", font=self.f_small, fill="black")
                    x += int(S * 0.32)
                cx = x + cw // 2
                words = []
                for p in pending:
                    if isinstance(p, core.Harmony):
                        d.text((cx - self.d_w // 2, int(S * 0.05)), chord_text(p), font=self.f_chord, fill="black")
                    else:
                        words.append(p.text if isinstance(p, core.Words) else p.kind.capitalize())
                if words:
                    d.text((cx - self.d_w // 2, int(S * 0.95)), "  ".join(words), font=self.f_words, fill="black")
                pending = []
                d.text((cx - self.d_w // 2, y_top - self.d_top), str(n.degree), font=self.f_digit, fill="black")
                for k in range(max(n.octave, 0)):
                    dot(cx, y_top - int(S * 0.2) - k * int(S * 0.2))
                # underlines
                dur = n.dur
                left_gap = pos % unit == 0
                right_gap = (pos + dur) % unit == 0 or u == 0
                for k in range(u):
                    yy = y_bot + int(S * 0.14) + k * int(S * 0.14)
                    x1 = x + (int(S * 0.08) if left_gap else 0)
                    x2 = x + cw - (int(S * 0.08) if right_gap else 0)
                    d.rectangle((x1, yy, x2, yy + max(2, S // 20)), fill="black")
                low_y = y_bot + int(S * 0.22) + u * int(S * 0.14)
                for k in range(max(-n.octave, 0)):
                    dot(cx, low_y + k * int(S * 0.2))
                if n.degree and not n.tie_stop:
                    for v, (t, _syl) in n.lyrics.items():
                        tw = self.f_lyric.getlength(t)
                        d.text((cx - tw / 2, y_lyr + (v - 1) * int(S * 0.8)), t, font=self.f_lyric, fill="black")
                centers.append((n, cx))
                self._sys_boxes.append((x, int(S * 0.9), x + cw, y_lyr, n))
                x += cw
                if dotted:
                    dot(cx + self.d_w // 2 + int(S * 0.12), y_top + int(self.d_h * 0.55))
                    x += int(S * 0.22)
                for _ in range(dashes):
                    self._sys_boxes.append((x, y_top - int(S * 0.3), x + cw, y_bot + int(S * 0.3), n))
                    yy = y_top + self.d_h // 2
                    d.rectangle((x + int(S * 0.12), yy - 1, x + cw - int(S * 0.12), yy + max(2, S // 20) - 1),
                                fill="black")
                    x += cw
                pos += dur
            # right barline
            if m.right_repeat:
                kind = ":|"
            elif m.right_style == "light-heavy":
                kind = "|]"
            elif m.right_style == "light-light":
                kind = "||"
            else:
                kind = "|"
            nxt = measures[mi + 1] if mi + 1 < len(measures) else None
            x += int(S * 0.15)
            if not (kind == "|" and nxt is not None and nxt.left_repeat):
                x = barline(x, kind) + int(S * 0.3)
            if m.ending_stop and volta_starts:
                vx, num = volta_starts.pop()
                vy = int(S * 1.45)
                d.line((vx, vy + int(S * 0.4), vx, vy, x - int(S * 0.3), vy), fill="black", width=2)
                d.text((vx + 4, vy + 2), f"{num}.", font=self.f_words, fill="black")
        # ties / slurs / triplets
        arc_y = y_top - int(S * 0.55)
        open_slurs = {}
        trip = []
        for i, (n, cx) in enumerate(centers):
            if n.tie_start and i + 1 < len(centers):
                cx2 = centers[i + 1][1]
                d.arc((cx + 2, arc_y - int(S * 0.15), cx2 - 2, arc_y + int(S * 0.3)), 190, 350, fill="black",
                      width=max(2, S // 18))
            for k in n.slur_start:
                open_slurs[k] = cx
            for k in n.slur_stop:
                if k in open_slurs:
                    x1 = open_slurs.pop(k)
                    d.arc((x1, arc_y - int(S * 0.25), cx, arc_y + int(S * 0.3)), 190, 350, fill="black",
                          width=max(2, S // 18))
            if n.tuplet_start:
                trip = [cx]
            elif trip:
                trip.append(cx)
            if n.tuplet_stop and trip:
                mid = (trip[0] + trip[-1]) / 2
                d.text((mid - S * 0.12, arc_y - int(S * 0.75)), str(n.tuplet[0]), font=self.f_small, fill="black")
                trip = []
        return img.crop((0, 0, min(img.width, x + S), img.height))


PAPER = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}


def _trim_bottom(im, pad=10):
    """Crop white space at the bottom of a system image."""
    import numpy as np
    a = np.asarray(im.convert("L"))
    rows = np.where(a.min(axis=1) < 200)[0]
    if len(rows) == 0:
        return im
    return im.crop((0, 0, im.width, min(im.height, int(rows[-1]) + 1 + pad)))


def render_pdf(text: str, path, meta=None, key_label=None, paper="A4", dpi=200, size=44):
    pages = Renderer(size).render_pages(text, meta, key_label, paper, dpi)
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=dpi)


def render_png(text: str, size=44) -> bytes:
    return Renderer(size).png(text)


def render_with_map(text: str, size=44):
    """-> (png bytes, width, height, boxes) where boxes = [(x0, y0, x1, y1, line, col0, col1), ...]"""
    r = Renderer(size)
    img = r.render(text)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), img.width, img.height, r.boxes

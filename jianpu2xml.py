#!/usr/bin/env python3
"""jianpu2xml command line - the web app is app.py.

    python jianpu2xml.py song.txt -t 2                 # text  -> song_E.musicxml
    python jianpu2xml.py scan.pdf --key D -t 2         # scan  -> scan.jianpu.txt + scan_E.musicxml
    python jianpu2xml.py song.txt --preview song.png   # draw the text as jianpu
    python jianpu2xml.py song.mxl -t 2                 # MusicXML -> song.jianpu.txt + transposed MusicXML
    python jianpu2xml.py song.txt -t 2 --pdf song.pdf --title "季候風" --subtitle "in E"   # jianpu PDF
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import jianpu_core as core
from version import __version__

SCAN_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".bmp", ".tif", ".tiff", ".webp"}
XML_EXTS = {".musicxml", ".xml", ".mxl"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jianpu2xml", description="Jianpu text / scan -> MusicXML")
    ap.add_argument("--version", action="version", version=f"jianpu2xml {__version__}")
    ap.add_argument("input", help="jianpu .txt, or a .jpg/.png/.pdf scan (read offline with OpenCV)")
    ap.add_argument("-o", "--output", help="output .musicxml or .mxl (default: <input>_<key>.musicxml)")
    ap.add_argument("-t", "--transpose", type=int, default=0, help="semitones, e.g. 2 = up a major 2nd")
    ap.add_argument("--enharmonic", choices=["auto", "sharps", "flats"], default="auto")
    ap.add_argument("--octave", default="0", help="octave shift -2..2 or 'auto'")
    ap.add_argument("--clef", choices=["treble", "bass"])
    ap.add_argument("--key", default="C", help="key of the scan (1=?), used when reading a scan")
    ap.add_argument("--time", default="4/4", help="time signature of the scan")
    ap.add_argument("--no-chords", action="store_true", help="don't read chord numbers from the scan")
    ap.add_argument("--colour", action="store_true", help="read coloured ink too (default: black only)")
    ap.add_argument("--overlay", help="save the detection overlay image (scan input)")
    ap.add_argument("--preview", help="save a PNG drawing of the notation and stop")
    ap.add_argument("--pdf", help="also save a jianpu PDF (staff-notation PDF: use the web app)")
    ap.add_argument("--paper", choices=["A4", "Letter"], default="A4")
    for k in ("title", "subtitle", "composer", "lyricist", "arranger"):
        ap.add_argument(f"--{k}", help=f"override the {k} for MusicXML/PDF output")
    a = ap.parse_args(argv)

    src = Path(a.input)
    if src.suffix.lower() in SCAN_EXTS:
        import cv2
        import omr
        texts = []
        for i, page in enumerate(omr.load_pages(src), 1):
            r = omr.recognize(page, key=a.key, time=a.time, title=src.stem, black_only=not a.colour,
                              read_chords=not a.no_chords, header=(i == 1))
            for _, m in r.warnings:
                print(f"page {i}: {m}", file=sys.stderr)
            print(f"page {i}: {r.rows} rows, {r.notes} notes", file=sys.stderr)
            texts.append(r.text if i == 1 else f"// ---- page {i} ----\n{r.text}")
            if a.overlay:
                op = Path(a.overlay)
                cv2.imwrite(str(op if i == 1 else op.with_name(f"{op.stem}-{i}{op.suffix}")), r.overlay)
        text = "\n".join(texts)
        txt = src.with_suffix(".jianpu.txt")
        txt.write_text(text, encoding="utf-8")
        print(f"Notation saved to {txt} - check it against the scan.", file=sys.stderr)
        stem = src.stem
    elif src.suffix.lower() in XML_EXTS:
        import musicxml_import
        text, warns, info = musicxml_import.import_file(src)
        for _, m in warns:
            print(f"import: {m}", file=sys.stderr)
        txt = src.with_suffix(".jianpu.txt")
        txt.write_text(text, encoding="utf-8")
        print(f"Jianpu text saved to {txt} ({info['measures']} measures from '{info['part']}')", file=sys.stderr)
        stem = src.stem
    else:
        text = src.read_text(encoding="utf-8")
        stem = src.name.split(".")[0]

    if a.preview:
        import render
        Path(a.preview).write_bytes(render.render_png(text))
        print(f"Wrote {a.preview}")
        return 0

    meta = {k: getattr(a, k) for k in ("title", "subtitle", "composer", "lyricist", "arranger")
            if getattr(a, k) is not None} or None
    r = core.convert(text, a.transpose, a.enharmonic, "auto" if a.octave == "auto" else int(a.octave), a.clef, meta)
    for ln, msg in r.warnings:
        print(f"warning: line {ln}: {msg}" if ln else f"warning: {msg}", file=sys.stderr)
    if a.pdf:
        import render
        render.render_pdf(text, a.pdf, meta=meta, key_label=r.new_key, paper=a.paper)
        print(f"Wrote {a.pdf} (jianpu, 1={r.new_key})")
    out = Path(a.output) if a.output else src.with_name(f"{stem}_{r.new_key}.musicxml")
    core.write_musicxml(out, r.xml)
    print(f"Wrote {out}  ({r.measure_count} measures, 1={r.orig_key} -> 1={r.new_key}, range {r.lowest}-{r.highest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

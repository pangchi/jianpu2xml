"""jianpu2xml web app - Flask, meant for a Raspberry Pi on the home network.

    python app.py                       # http://localhost:8080 (HOST=127.0.0.1 keeps it on this computer)
    gunicorn -w 2 -t 300 -b 0.0.0.0:8080 app:app

Environment:
    JIANPU_DATA      data folder (uploads + saved notation)   default ~/jianpu2xml-data
    JIANPU_PASSWORD  if set, the site asks for this password (HTTP basic auth, any user name)
    JIANPU_KEEP_HOURS  how long uploaded scans are kept       default 48
    HOST / PORT      where python app.py listens               default 0.0.0.0 / 8080
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import cv2
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

import jianpu_core as core
import musicxml_import
import omr
import playback
import render
from version import __version__

DATA = Path(os.environ.get("JIANPU_DATA", Path.home() / "jianpu2xml-data"))
UPLOADS = DATA / "uploads"
LIBRARY = DATA / "library"
KEEP_HOURS = float(os.environ.get("JIANPU_KEEP_HOURS", "48"))
PASSWORD = os.environ.get("JIANPU_PASSWORD", "")
SCAN_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".bmp", ".tif", ".tiff", ".webp"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
for d in (UPLOADS, LIBRARY):
    d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- helpers

@app.before_request
def _auth():
    if not PASSWORD:
        return None
    a = request.authorization
    if a and a.password and hmac.compare_digest(a.password.encode(), PASSWORD.encode()):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="jianpu2xml"'})


def _upload_dir(scan_id: str) -> Path:
    if not ID_RE.match(scan_id or ""):
        abort(400, "bad scan id")
    d = UPLOADS / scan_id
    if not d.is_dir():
        abort(404, "scan not found (uploads are removed after a while; upload it again)")
    return d


def _cleanup():
    cutoff = time.time() - KEEP_HOURS * 3600
    for d in UPLOADS.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _lib_path(name: str) -> Path:
    name = (name or "").strip()
    name = re.sub(r"\.txt$", "", name, flags=re.I)
    safe = re.sub(r"[^\w\-. ()]+", "_", name).strip(". ")[:100]
    if not safe:
        abort(400, "invalid name")
    return LIBRARY / f"{safe}.txt"


META_KEYS = ("title", "subtitle", "composer", "lyricist", "arranger")


def _meta(j):
    m = j.get("meta") or {}
    return {k: str(m[k]).strip()[:200] for k in META_KEYS if k in m} or None


def _opts(j):
    oct_ = str(j.get("octave", "0"))
    return dict(meta=_meta(j), transpose=int(j.get("transpose", 0)),
                enharmonic=j.get("enharmonic", "auto") if j.get("enharmonic") in ("auto", "sharps", "flats") else "auto",
                octave="auto" if oct_ == "auto" else int(oct_),
                clef=j.get("clef") if j.get("clef") in ("treble", "bass") else None)


def _err(msg, code=400):
    return jsonify(error=msg), code


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def _http_err(e):
    if request.path.startswith("/api/"):
        msg = e.description if e.code != 413 else "File too large (40 MB max)"
        return jsonify(error=msg), e.code
    return e


# --------------------------------------------------------------------------- pages

@app.get("/")
def index():
    return render_template("index.html", version=__version__,
                           keys=["C", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B"],
                           transposes=[(t, core.interval_label(t)) for t in range(12, -13, -1)],
                           syntax=core.SYNTAX_HELP)


@app.get("/api/version")
def version():
    return jsonify(version=__version__)


# --------------------------------------------------------------------------- scans

@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return _err("No file")
    ext = Path(f.filename).suffix.lower()
    if ext not in SCAN_EXTS:
        return _err("Upload a JPG, PNG or PDF")
    _cleanup()
    scan_id = uuid.uuid4().hex
    d = UPLOADS / scan_id
    d.mkdir(parents=True)
    src = d / f"source{ext}"
    f.save(src)
    try:
        pages = omr.load_pages(src, dpi=int(request.form.get("dpi", 250)))
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(d, ignore_errors=True)
        return _err(f"Couldn't read that file: {e}")
    for i, p in enumerate(pages, 1):
        cv2.imwrite(str(d / f"page-{i}.png"), p)
    (d / "meta.json").write_text(json.dumps({"name": f.filename, "pages": len(pages)}), encoding="utf-8")
    return jsonify(id=scan_id, pages=len(pages), name=f.filename)


@app.get("/api/scan/<scan_id>/<int:page>.png")
def scan_page(scan_id, page):
    p = _upload_dir(scan_id) / f"page-{page}.png"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/png", max_age=3600)


@app.get("/api/scan/<scan_id>/<int:page>-overlay.png")
def scan_overlay(scan_id, page):
    p = _upload_dir(scan_id) / f"overlay-{page}.png"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/png", max_age=0)


@app.get("/api/scan/<scan_id>/notes")
def scan_notes(scan_id):
    p = _upload_dir(scan_id) / "notes.json"
    return jsonify(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})


@app.post("/api/recognize")
def recognize():
    j = request.get_json(force=True)
    d = _upload_dir(j.get("id", ""))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    page = j.get("page", "all")
    pages = range(1, meta["pages"] + 1) if page == "all" else [int(page)]
    key = j.get("key", "C")
    if core.parse_key(key) is None:
        return _err("Unknown key")
    time_sig = j.get("time", "4/4").strip()
    if not re.match(r"^\d+/\d+$", time_sig):
        return _err("Time signature should look like 4/4")
    parts, warnings, stats, note_maps = [], [], [], {}
    row_offset = 0
    for i, n in enumerate(pages):
        img = cv2.imread(str(d / f"page-{n}.png"), cv2.IMREAD_COLOR)
        if img is None:
            return _err(f"Page {n} missing")
        try:
            r = omr.recognize(img, key=key, time=time_sig, title=j.get("title", "").strip(),
                              black_only=bool(j.get("black_only", True)),
                              read_chords=bool(j.get("chords", True)), header=(i == 0))
        except omr.OMRError as e:
            warnings.append((0, f"Page {n}: {e}"))
            continue
        cv2.imwrite(str(d / f"overlay-{n}.png"), r.overlay)
        parts.append(r.text if i == 0 else f"// ---- page {n} ----\n{r.text}")
        warnings += [(0, f"Page {n}: {m}") for _, m in r.warnings]
        stats.append({"page": n, "rows": r.rows, "notes": r.notes, "digit_px": round(r.digit_height, 1),
                      "skew": r.skew})
        # row = index of the music line in the generated text, counted across pages
        note_maps[n] = [dict(m, row=m["row"] + row_offset) for m in r.notes_map]
        row_offset += r.rows
    if not parts:
        return _err("; ".join(m for _, m in warnings) or "Nothing recognised")
    text = "\n".join(parts)
    res = core.convert(text)
    (d / "notes.json").write_text(json.dumps(note_maps), encoding="utf-8")
    return jsonify(text=text, stats=stats, ocr_warnings=[m for _, m in warnings],
                   warnings=res.warnings, measures=res.measure_count, notes=note_maps)


# --------------------------------------------------------------------------- notation

@app.post("/api/check")
def check():
    j = request.get_json(force=True)
    try:
        r = core.convert(j.get("text", ""), **_opts(j))
    except Exception as e:  # noqa: BLE001
        return jsonify(warnings=[(0, f"Parser error: {e}")], ok=False)
    return jsonify(ok=True, warnings=r.warnings, orig_key=r.orig_key, new_key=r.new_key,
                   key_sig=core.key_signature_text(r.new_key), measures=r.measure_count,
                   lowest=r.lowest, highest=r.highest, octave_shift=r.octave_shift, title=r.title)


@app.post("/api/export")
def export():
    j = request.get_json(force=True)
    r = core.convert(j.get("text", ""), **_opts(j))
    base = re.sub(r'[\\/:*?"<>|]+', "_", (r.title or j.get("name") or "score").strip()) or "score"
    if j.get("format") == "mxl":
        return send_file(io.BytesIO(core.mxl_bytes(r.xml)), mimetype="application/vnd.recordare.musicxml",
                         as_attachment=True, download_name=f"{base}_{r.new_key}.mxl")
    return send_file(io.BytesIO(r.xml.encode("utf-8")), mimetype="application/vnd.recordare.musicxml+xml",
                     as_attachment=True, download_name=f"{base}_{r.new_key}.musicxml")


@app.post("/api/xml")
def xml_text():
    """MusicXML for the in-browser manuscript view."""
    j = request.get_json(force=True)
    try:
        r = core.convert(j.get("text", ""), **_opts(j))
    except Exception as e:  # noqa: BLE001
        return _err(f"Parser error: {e}")
    return jsonify(xml=r.xml, map=r.note_map)


def _events(j):
    o = _opts(j)
    o.pop("meta", None)
    score = core.parse(j.get("text", ""))
    return score, playback.events(score, chords=bool(j.get("chords", True)), **o)


@app.post("/api/events")
def events():
    """Timed notes for the in-browser player (repeats and jumps unrolled)."""
    j = request.get_json(force=True)
    try:
        _, ev = _events(j)
    except Exception as e:  # noqa: BLE001
        return _err(f"Can't play this: {e}")
    return jsonify(ev)


@app.post("/api/midi")
def midi():
    j = request.get_json(force=True)
    score, ev = _events(j)
    bpm = int(j["bpm"]) if str(j.get("bpm", "")).isdigit() else None
    title = (_meta(j) or {}).get("title") or score.meta.get("title", "") or "score"
    r = core.convert(j.get("text", ""), **_opts(j))
    base = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "score"
    return send_file(io.BytesIO(playback.midi_bytes(ev, bpm=bpm, title=title)), mimetype="audio/midi",
                     as_attachment=True, download_name=f"{base}_{r.new_key}.mid")


@app.post("/api/import")
def import_musicxml():
    """MusicXML / .mxl upload -> jianpu text."""
    f = request.files.get("file")
    if not f or not f.filename:
        return _err("No file")
    if Path(f.filename).suffix.lower() not in (".musicxml", ".xml", ".mxl"):
        return _err("Upload a .musicxml, .xml or .mxl file")
    try:
        text, warnings, info = musicxml_import.import_bytes(f.read())
    except musicxml_import.ImportError_ as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"Couldn't import that file: {e}")
    res = core.convert(text)
    return jsonify(text=text, import_warnings=[m for _, m in warnings], warnings=res.warnings,
                   measures=res.measure_count, info=info)


@app.post("/api/jianpu-pages")
def jianpu_pages():
    """Paginated jianpu pages (PNG, base64) for the PDF export."""
    j = request.get_json(force=True)
    text = j.get("text", "")
    try:
        r = core.convert(text, **_opts(j))
        pages = render.Renderer(44).render_pages(text, meta=_meta(j), key_label=r.new_key,
                                                 paper=j.get("paper", "A4"), dpi=int(j.get("dpi", 200)))
    except Exception as e:  # noqa: BLE001
        return _err(f"Can't draw jianpu pages: {e}")
    out = []
    for pg in pages:
        buf = io.BytesIO()
        pg.save(buf, "PNG", optimize=True)
        out.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    return jsonify(pages=out, width=pages[0].width, height=pages[0].height)


@app.post("/api/preview")
def preview():
    j = request.get_json(force=True)
    try:
        png, w, h, boxes = render.render_with_map(j.get("text", ""), size=int(j.get("size", 40)))
    except Exception as e:  # noqa: BLE001
        return _err(f"Can't draw preview: {e}")
    return jsonify(png=base64.b64encode(png).decode("ascii"), width=w, height=h, boxes=boxes)


# --------------------------------------------------------------------------- library

@app.get("/api/library")
def lib_list():
    items = sorted(LIBRARY.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([{"name": p.stem, "modified": int(p.stat().st_mtime), "size": p.stat().st_size}
                    for p in items])


@app.get("/api/library/<path:name>")
def lib_get(name):
    p = _lib_path(name)
    if not p.exists():
        abort(404, "not found")
    return jsonify(name=p.stem, text=p.read_text(encoding="utf-8"))


@app.put("/api/library/<path:name>")
def lib_put(name):
    p = _lib_path(name)
    text = (request.get_json(force=True) or {}).get("text", "")
    p.write_text(text, encoding="utf-8")
    return jsonify(name=p.stem)


@app.delete("/api/library/<path:name>")
def lib_delete(name):
    p = _lib_path(name)
    p.unlink(missing_ok=True)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8080)),
            debug=False, threaded=True)

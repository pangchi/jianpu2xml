// jianpu2xml front end - vanilla JS, no build step.
(() => {
  const $ = (id) => document.getElementById(id);
  const ed = $("editor");
  const DRAFT_KEY = "jianpu2xml.draft";
  const STARTER = `title: \nkey: C\ntime: 4/4\n\n// Upload a scan and press "Read notes", or type jianpu here (Syntax shows the format).\n<1> 1 1 5 5 | <4> 6 6 <1> 5 - | <4> 4 4 <1> 3 3 | <5> 2 2 <1> 1 - |]\nw: twin- kle twin- kle lit- tle star how I won- der what you are\n`;

  const state = { scan: null, page: 1, zoom: null, overlay: false, libName: null, checkTimer: null };

  // ------------------------------------------------------------------ utils
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    const ct = r.headers.get("content-type") || "";
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      if (ct.includes("json")) { try { msg = (await r.json()).error || msg; } catch { } }
      throw new Error(msg);
    }
    return ct.includes("json") ? r.json() : r;
  }
  const postJSON = (path, body) => api(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  function setStatus(t) { $("status").textContent = t; }
  function options() {
    return { transpose: +$("optTranspose").value, enharmonic: $("optEnh").value,
             octave: $("optOct").value.replace("+", ""), clef: $("optClef").value };
  }
  function download(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function filenameFrom(resp, fallback) {
    const cd = resp.headers.get("content-disposition") || "";
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    return m ? decodeURIComponent(m[1]) : fallback;
  }

  // ------------------------------------------------------------------ messages
  function showMessages(list, okText) {
    const ul = $("msgList"); ul.innerHTML = "";
    if (!list.length) {
      const li = document.createElement("li"); li.className = "ok"; li.textContent = okText || "No problems found.";
      ul.appendChild(li); return;
    }
    for (const [line, msg] of list) {
      const li = document.createElement("li"); li.className = "warn";
      li.textContent = line ? `line ${line}: ${msg}` : msg;
      if (line) li.onclick = () => gotoLine(line);
      ul.appendChild(li);
    }
  }
  function gotoLine(n) {
    const lines = ed.value.split("\n");
    let start = 0;
    for (let i = 0; i < n - 1 && i < lines.length; i++) start += lines[i].length + 1;
    const end = start + (lines[n - 1] || "").length;
    ed.focus(); ed.setSelectionRange(start, end);
    const lh = parseFloat(getComputedStyle(ed).lineHeight) || 22;
    ed.scrollTop = Math.max(0, (n - 4) * lh);
  }

  // ------------------------------------------------------------------ text <-> note positions
  const HEADER_RE = /^\s*(title|subtitle|composer|lyricist|arranger|key|time|tempo|clef)\s*[:：]/i;
  function isMusicLine(line) {
    const t = line.replace(/\/\/.*$/, "").trim();
    return !!t && !t.startsWith("```") && !HEADER_RE.test(t) && !/^1\s*=/.test(t) && !/^w\s*[:：]/i.test(t);
  }
  // character spans of note tokens in one music line (mirrors the Python parser)
  function noteSpans(line) {
    const cut = line.indexOf("//"); if (cut >= 0) line = line.slice(0, cut);
    const spans = []; let i = 0; const n = line.length;
    const noteRe = /\(?[#b♯♭n]?[0-7][',_.~)^]*/y;
    while (i < n) {
      const c = line[i];
      if (/\s/.test(c)) { i++; continue; }
      if (c === '"') { const j = line.indexOf('"', i + 1); i = j < 0 ? n : j + 1; continue; }
      if (c === "<" || c === "[") { const j = line.indexOf(c === "<" ? ">" : "]", i + 1); i = j < 0 ? n : j + 1; continue; }
      if (c === "|" || c === ":") { i++; continue; }
      if (/\d/.test(c) && line[i + 1] === "{") { i += 2; continue; }
      if (c === "}" || c === "-") { i++; continue; }
      noteRe.lastIndex = i;
      const m = noteRe.exec(line);
      if (m) { spans.push([i, i + m[0].length]); i += m[0].length; continue; }
      while (i < n && !/\s/.test(line[i])) i++;
    }
    return spans;
  }
  function selectSpan(lineNo, c0, c1) {
    const lines = ed.value.split("\n");
    if (lineNo < 1 || lineNo > lines.length) return false;
    let start = 0;
    for (let i = 0; i < lineNo - 1; i++) start += lines[i].length + 1;
    ed.focus({ preventScroll: true });
    ed.setSelectionRange(start + c0, start + c1);
    // scroll the textarea so the selection is visible (it doesn't wrap)
    const cs = getComputedStyle(ed);
    const lh = parseFloat(cs.lineHeight) || 22;
    const top = (lineNo - 1) * lh;
    if (top < ed.scrollTop + lh || top > ed.scrollTop + ed.clientHeight - 2 * lh) ed.scrollTop = Math.max(0, top - ed.clientHeight / 3);
    const ctx = (selectSpan.ctx ||= document.createElement("canvas").getContext("2d"));
    ctx.font = `${cs.fontSize} ${cs.fontFamily}`;
    const x = ctx.measureText(lines[lineNo - 1].slice(0, c0)).width + parseFloat(cs.paddingLeft);
    if (x < ed.scrollLeft + 20 || x > ed.scrollLeft + ed.clientWidth - 60) ed.scrollLeft = Math.max(0, x - ed.clientWidth / 3);
    return true;
  }
  // the k-th note of the row-th music line (used for scans: survives edits on other lines)
  function selectRowNote(row, k) {
    const lines = ed.value.split("\n");
    let r = -1;
    for (let i = 0; i < lines.length; i++) {
      if (!isMusicLine(lines[i])) continue;
      if (++r === row) {
        const spans = noteSpans(lines[i]);
        if (!spans.length) return false;
        const [a, b] = spans[Math.min(k, spans.length - 1)];
        return selectSpan(i + 1, a, b);
      }
    }
    return false;
  }
  // pick the box under (x, y), or the nearest one close by
  function hitBox(list, x, y, get) {
    let best = null, bestD = Infinity;
    for (const it of list) {
      const [x0, y0, x1, y1] = get(it);
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return it;
      const dx = Math.max(x0 - x, 0, x - x1), dy = Math.max(y0 - y, 0, y - y1);
      const d = Math.hypot(dx, dy);
      if (d < bestD) { bestD = d; best = it; }
    }
    if (!best) return null;
    const [x0, , x1] = get(best);
    return bestD <= Math.max(12, (x1 - x0) * 0.8) ? best : null;
  }
  function placeMarker(container, img, box) {
    let mk = container.querySelector(".marker");
    if (!box) { if (mk) mk.remove(); return; }
    if (!mk) { mk = document.createElement("div"); mk.className = "marker"; container.appendChild(mk); }
    const z = img.clientWidth / img.naturalWidth;
    mk.style.left = `${img.offsetLeft + box[0] * z}px`; mk.style.top = `${img.offsetTop + box[1] * z}px`;
    mk.style.width = `${(box[2] - box[0]) * z}px`; mk.style.height = `${(box[3] - box[1]) * z}px`;
  }

  // ------------------------------------------------------------------ current note -> views
  function caretNote() {
    const v = ed.value, pos = ed.selectionStart;
    const before = v.slice(0, pos).split("\n");
    const lineNo = before.length, col = before[lineNo - 1].length;
    const lines = v.split("\n");
    const line = lines[lineNo - 1] || "";
    if (!isMusicLine(line)) return null;
    const spans = noteSpans(line);
    let k = spans.findIndex(([a, b]) => col >= a && col < b);
    if (k < 0) k = spans.findIndex(([, b]) => col === b);          // cursor just after a note
    if (k < 0) return null;
    let row = -1;
    for (let i = 0; i < lineNo; i++) if (isMusicLine(lines[i])) row++;
    return { line: lineNo, c0: spans[k][0], c1: spans[k][1], row, k };
  }
  function reveal(el) {
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  // scroll: bring the marked note into view (not when the click came from that view itself)
  function highlightCurrent(scroll = true) {
    const cn = caretNote();
    const tab = currentTab();
    if (tab === "scan" && state.scan && state.scan.notes) {
      let hit = null;
      if (cn) {
        for (const [pg, list] of Object.entries(state.scan.notes)) {
          const e = list.find((n) => n.row === cn.row && n.k === cn.k);
          if (e) { hit = { ...e, page: +pg }; break; }
        }
      }
      state.markNote = hit;
      if (hit && hit.page !== state.page) {                 // marker is placed when the page image loads
        state.page = hit.page; showPage();
        if (scroll) viewer.querySelector("img").addEventListener("load", () => reveal(viewer.querySelector(".marker")), { once: true });
        return;
      }
      const img = viewer.querySelector("img");
      if (img && img.naturalWidth) { placeMarker(viewer, img, scanMarkBox()); if (scroll) reveal(viewer.querySelector(".marker")); }
    } else if (tab === "preview") {
      const box = $("previewBox"), img = box.querySelector("img");
      preview.box = cn ? preview.boxes.find((b) => b[4] === cn.line && b[5] === cn.c0) || null : null;
      if (img && img.naturalWidth) { placeMarker(box, img, preview.box); if (scroll) reveal(box.querySelector(".marker")); }
    } else if (tab === "score") {
      if (!score.map) return;
      const idx = cn ? score.map.findIndex((m) => m[0] === cn.line && m[1] === cn.c0) : -1;
      const gn = idx >= 0 ? graphicalNotes()[idx] : null;
      const el = gn && gn.getSVGGElement ? gn.getSVGGElement() : null;
      markScoreNote(el);
      if (scroll && el) reveal(el);
    }
  }
  let caretTimer = null;
  const onCaret = () => { clearTimeout(caretTimer); caretTimer = setTimeout(() => highlightCurrent(true), 120); };
  for (const ev of ["click", "keyup", "select"]) ed.addEventListener(ev, onCaret);

  // ------------------------------------------------------------------ check
  async function check() {
    try {
      const r = await postJSON("/api/check", { text: ed.value, ...options() });
      if (r.ok) {
        $("keyInfo").textContent = `1=${r.orig_key} → 1=${r.new_key} (${r.key_sig})`;
        setStatus(`${r.measures} measures · range ${r.lowest || "–"}–${r.highest || "–"}` +
                  (r.octave_shift ? ` · octave ${r.octave_shift > 0 ? "+" : ""}${r.octave_shift}` : "") +
                  ` · ${r.warnings.length} warning(s)`);
      }
      showMessages(r.warnings);
      return r;
    } catch (e) { setStatus("Check failed: " + e.message); return null; }
  }
  function scheduleCheck() {
    clearTimeout(state.checkTimer);
    state.checkTimer = setTimeout(() => { check(); store.set(DRAFT_KEY, ed.value); }, 700);
  }
  ed.addEventListener("input", scheduleCheck);
  for (const id of ["optTranspose", "optEnh", "optOct", "optClef"]) $(id).addEventListener("change", check);
  ed.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const s = ed.selectionStart;
      ed.setRangeText("  ", s, ed.selectionEnd, "end"); scheduleCheck();
    }
  });

  // ------------------------------------------------------------------ export / text files
  async function exportScore(format) {
    const r = await check();
    if (r && r.warnings.length && !confirm(`${r.warnings.length} warning(s) — see the list under the editor.\nDownload anyway?`)) return;
    try {
      const resp = await api("/api/export", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: ed.value, format, name: state.libName || "", ...options() }),
      });
      download(await resp.blob(), filenameFrom(resp, `score.${format}`));
      setStatus("Downloaded " + filenameFrom(resp, format));
    } catch (e) { alert("Export failed: " + e.message); }
  }
  $("btnExport").onclick = () => exportScore("musicxml");
  $("btnExportMxl").onclick = () => exportScore("mxl");
  $("btnDownloadTxt").onclick = () => {
    const title = (/^\s*title\s*:\s*(.+)$/mi.exec(ed.value) || [])[1];
    download(new Blob([ed.value], { type: "text/plain;charset=utf-8" }),
             `${(state.libName || title || "score").trim()}.jianpu.txt`);
  };
  $("txtInput").onchange = async (e) => {
    const f = e.target.files[0]; if (f) await loadTextFile(f);
    e.target.value = "";
  };

  // ------------------------------------------------------------------ PDF export
  const META_FIELDS = ["title", "subtitle", "composer", "lyricist", "arranger"];
  const cap = (k) => k[0].toUpperCase() + k.slice(1);
  function headerValue(k) {
    const m = new RegExp(`^\\s*${k}\\s*[:：]\\s*(.*)$`, "mi").exec(ed.value);
    return m ? m[1].trim() : "";
  }
  function writeHeader(meta) {
    let lines = ed.value.split("\n");
    for (const k of META_FIELDS) {
      const re = new RegExp(`^\\s*${k}\\s*[:：]`, "i");
      const i = lines.findIndex((l) => re.test(l));
      if (meta[k]) {
        if (i >= 0) lines[i] = `${k}: ${meta[k]}`;
        else lines.splice(META_FIELDS.indexOf(k) <= 0 ? 0 : Math.max(0, lines.findIndex((l) => /^\s*(key|time|1\s*=)/i.test(l))), 0, `${k}: ${meta[k]}`);
      } else if (i >= 0) lines.splice(i, 1);
    }
    ed.value = lines.join("\n"); store.set(DRAFT_KEY, ed.value); check();
  }
  $("btnPdf").onclick = () => {
    for (const k of META_FIELDS) $("pdf" + cap(k)).value = headerValue(k);
    if (!$("pdfTitle").value) $("pdfTitle").value = state.libName || "";
    $("pdfStatus").textContent = "";
    $("pdfNote").textContent = `Uses the current settings: ${$("keyInfo").textContent || ""} · clef ${$("optClef").value}.`;
    $("dlgPdf").showModal();
  };
  $("pdfGo").onclick = async (e) => {
    e.preventDefault();
    const meta = {};
    for (const k of META_FIELDS) meta[k] = $("pdf" + cap(k)).value.trim();
    const what = document.querySelector("input[name=pdfWhat]:checked").value;
    const paper = $("pdfPaper").value;
    $("pdfGo").disabled = true;
    try {
      if ($("pdfSaveHeader").checked) writeHeader(meta);
      const r = await makePdf(meta, what, paper, (msg) => { $("pdfStatus").textContent = msg; });
      $("dlgPdf").close();
      setStatus(`Saved ${r}`);
    } catch (err) {
      $("pdfStatus").textContent = "Failed: " + err.message;
    }
    $("pdfGo").disabled = false;
  };
  function svgToPng(svg, widthPx) {
    return new Promise((resolve, reject) => {
      const clone = svg.cloneNode(true);
      const w = parseFloat(svg.getAttribute("width")) || svg.getBoundingClientRect().width;
      const h = parseFloat(svg.getAttribute("height")) || svg.getBoundingClientRect().height;
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      clone.setAttribute("width", w); clone.setAttribute("height", h);
      if (!clone.getAttribute("viewBox")) clone.setAttribute("viewBox", `0 0 ${w} ${h}`);
      const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" }));
      const img = new Image();
      img.onload = () => {
        const c = document.createElement("canvas");
        c.width = widthPx; c.height = Math.round(widthPx * h / w);
        const ctx = c.getContext("2d");
        ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve({ data: c.toDataURL("image/png"), w: c.width, h: c.height });
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("couldn't rasterise the score")); };
      img.src = url;
    });
  }
  async function makePdf(meta, what, paper, progress) {
    if (!window.jspdf) throw new Error("PDF library didn't load (static/vendor/jspdf.umd.min.js)");
    const { jsPDF } = window.jspdf;
    const fmt = paper === "Letter" ? "letter" : "a4";
    const doc = new jsPDF({ unit: "mm", format: fmt, compress: true });
    const pw = doc.internal.pageSize.getWidth(), ph = doc.internal.pageSize.getHeight();
    let first = true;
    const addPage = () => { if (!first) doc.addPage(fmt); first = false; };
    let newKey = "";

    if (what !== "jianpu") {
      progress("Drawing manuscript…");
      // the score renderer has no arranger line: show it with the composer
      const smeta = { ...meta };
      if (meta.arranger) smeta.composer = [meta.composer, `arr. ${meta.arranger}`].filter(Boolean).join(" · ");
      const r = await postJSON("/api/xml", { text: ed.value, meta: smeta, ...options() });
      if (!window.opensheetmusicdisplay) throw new Error("Score renderer didn't load");
      const stage = document.createElement("div"); stage.className = "pdf-stage"; document.body.appendChild(stage);
      try {
        const o = new opensheetmusicdisplay.OpenSheetMusicDisplay(stage, {
          autoResize: false, backend: "svg", pageFormat: paper === "Letter" ? "Letter_P" : "A4_P",
          drawTitle: true, drawSubtitle: true, drawComposer: true, drawLyricist: true, drawCredits: true,
          drawPartNames: false, drawMeasureNumbers: true, pageBackgroundColor: "#FFFFFF" });
        await o.load(r.xml);
        o.render();
        const svgs = [...stage.querySelectorAll("svg")];
        for (let i = 0; i < svgs.length; i++) {
          progress(`Manuscript page ${i + 1}/${svgs.length}…`);
          const png = await svgToPng(svgs[i], Math.round(pw / 25.4 * 220));
          addPage();
          const h = Math.min(ph, pw * png.h / png.w);
          doc.addImage(png.data, "PNG", 0, 0, pw, h, undefined, "FAST");
        }
      } finally { stage.remove(); }
    }
    if (what !== "score") {
      progress("Drawing jianpu…");
      const r = await postJSON("/api/jianpu-pages", { text: ed.value, meta, paper, ...options() });
      r.pages.forEach((b64) => {
        addPage();
        doc.addImage("data:image/png;base64," + b64, "PNG", 0, 0, pw, pw * r.height / r.width, undefined, "FAST");
      });
    }
    const c = await postJSON("/api/check", { text: ed.value, ...options() });
    newKey = c.new_key || "";
    const base = (meta.title || state.libName || "score").replace(/[\\/:*?"<>|]+/g, "_").trim() || "score";
    const name = `${base}${newKey ? "_" + newKey : ""}.pdf`;
    doc.setProperties({ title: meta.title || base, subject: meta.subtitle || "", author: meta.composer || "",
                        creator: `jianpu2xml ${window.JIANPU2XML_VERSION || ""}`.trim() });
    doc.save(name);
    return name;
  }

  // ------------------------------------------------------------------ playback (Web Audio, offline)
  const player = { ctx: null, master: null, ev: null, raf: 0, playing: false, t0: 0, starts: [], idx: -1, endSec: 0 };
  function tempoFn(ev, bpm) {
    // beats (quarters) -> seconds, honouring tempo changes; a typed bpm scales the whole map
    const scale = bpm ? bpm / ev.bpm : 1;
    const segs = ev.tempos.map(([b, t]) => [b, t * scale]).sort((a, b) => a[0] - b[0]);
    return (beat) => {
      let sec = 0;
      for (let i = 0; i < segs.length; i++) {
        const [b0, t] = segs[i];
        const b1 = i + 1 < segs.length ? segs[i + 1][0] : Infinity;
        if (beat <= b0) break;
        sec += (Math.min(beat, b1) - b0) * 60 / t;
      }
      return sec;
    };
  }
  function pianoNote(ctx, out, midi, when, dur, vel) {
    const f = 440 * Math.pow(2, (midi - 69) / 12);
    const g = ctx.createGain(); g.connect(out);
    const o1 = ctx.createOscillator(); o1.type = "triangle"; o1.frequency.value = f; o1.connect(g);
    const o2 = ctx.createOscillator(); o2.type = "sine"; o2.frequency.value = f * 2;
    const g2 = ctx.createGain(); g2.gain.value = 0.22; o2.connect(g2); g2.connect(g);
    const peak = 0.32 * vel, end = when + Math.max(0.06, dur * 0.97);
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(peak, when + 0.008);
    g.gain.setTargetAtTime(peak * 0.35, when + 0.01, 0.35);
    g.gain.setTargetAtTime(0, end, 0.05);
    o1.start(when); o2.start(when); o1.stop(end + 0.4); o2.stop(end + 0.4);
  }
  function padChord(ctx, out, midis, when, dur) {
    const end = when + Math.max(0.1, dur * 0.98);
    midis.forEach((m, k) => {
      const g = ctx.createGain(); g.connect(out);
      const o = ctx.createOscillator(); o.type = k === 0 ? "triangle" : "sine";
      o.frequency.value = 440 * Math.pow(2, (m - 69) / 12); o.connect(g);
      const peak = k === 0 ? 0.11 : 0.045;
      g.gain.setValueAtTime(0, when);
      g.gain.linearRampToValueAtTime(peak, when + 0.04);
      g.gain.setTargetAtTime(peak * 0.6, when + 0.05, 0.6);
      g.gain.setTargetAtTime(0, end, 0.08);
      o.start(when); o.stop(end + 0.5);
    });
  }
  function fmtTime(sec) { const m = Math.floor(sec / 60), s = Math.floor(sec % 60); return `${m}:${String(s).padStart(2, "0")}`; }
  async function play() {
    stopPlay();
    let ev;
    try {
      ev = await postJSON("/api/events", { text: ed.value, chords: $("playChords").checked, ...options() });
    } catch (e) { setStatus("Can't play: " + e.message); return; }
    if (!ev.notes.length) { setStatus("Nothing to play"); return; }
    $("playBpm").placeholder = ev.bpm;
    const bpm = parseInt($("playBpm").value, 10) || 0;
    const sec = tempoFn(ev, bpm >= 20 ? bpm : 0);
    // start at the note under the cursor (its first time through), else at the beginning
    const cn = caretNote();
    let startBeat = 0;
    // a cursor left behind by the last playback doesn't count: start from the top then
    const leftover = cn && player.lastFollow && cn.line === player.lastFollow.line && cn.c0 === player.lastFollow.c0;
    if (cn && !leftover) { const n = ev.notes.find((x) => x.line === cn.line && x.c0 === cn.c0); if (n) startBeat = n.t; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { setStatus("This browser can't play audio"); return; }
    player.ctx = player.ctx || new AC();
    await player.ctx.resume();
    const ctx = player.ctx;
    const comp = ctx.createDynamicsCompressor(); comp.connect(ctx.destination);
    const master = ctx.createGain(); master.gain.value = 0.9; master.connect(comp);
    player.master = master;
    const t0 = ctx.currentTime + 0.12 - sec(startBeat);
    player.t0 = t0; player.ev = ev; player.idx = -1;
    const notes = ev.notes.filter((n) => n.t + 1e-6 >= startBeat);
    for (const n of notes) pianoNote(ctx, master, n.midi, t0 + sec(n.t), sec(n.t + n.d) - sec(n.t), 1);
    for (const c of ev.chords) {
      if (c.t + c.d <= startBeat) continue;
      const s0 = Math.max(c.t, startBeat);
      padChord(ctx, master, c.midi, t0 + sec(s0), sec(c.t + c.d) - sec(s0));
    }
    player.notes = notes;
    player.starts = notes.map((n) => sec(n.t));
    player.endSec = sec(ev.length);
    player.total = fmtTime(player.endSec);
    player.playing = true;
    $("btnPlay").disabled = true; $("btnStop").disabled = false;
    const tick = () => {
      if (!player.playing) return;
      const now = ctx.currentTime - player.t0;
      if (now > player.endSec + 0.3) { stopPlay(); return; }
      // the latest note that has started
      let i = player.idx < 0 ? 0 : player.idx;
      while (i + 1 < player.starts.length && player.starts[i + 1] <= now) i++;
      if (player.starts[i] <= now && i !== player.idx) {
        player.idx = i;
        const n = player.notes[i];
        $("playPos").textContent = `bar ${n.measure} · ${fmtTime(Math.max(0, now))} / ${player.total}`;
        if ($("playFollow").checked) followNote(n);
      }
      player.raf = requestAnimationFrame(tick);
    };
    player.raf = requestAnimationFrame(tick);
  }
  function followNote(n) {
    player.lastFollow = { line: n.line, c0: n.c0 };
    const touch = !matchMedia("(pointer: fine)").matches;
    const lines = ed.value.split("\n");
    if (n.line > lines.length) return;
    if (touch) {
      let start = 0; for (let i = 0; i < n.line - 1; i++) start += lines[i].length + 1;
      ed.setSelectionRange(start + n.c0, start + n.c1);       // no focus: keeps the keyboard away
    } else {
      selectSpan(n.line, n.c0, n.c1);
    }
    highlightCurrent(true);
  }
  function stopPlay() {
    if (player.master) { try { player.master.disconnect(); } catch { } player.master = null; }
    cancelAnimationFrame(player.raf);
    player.playing = false;
    $("btnPlay").disabled = false; $("btnStop").disabled = true;
    $("playPos").textContent = "";
  }
  $("btnPlay").onclick = play;
  $("btnStop").onclick = stopPlay;
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && player.playing) stopPlay(); });
  ed.addEventListener("input", () => { if (player.playing) stopPlay(); });
  $("btnMidi").onclick = async () => {
    try {
      const bpm = parseInt($("playBpm").value, 10);
      const resp = await api("/api/midi", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: ed.value, chords: $("playChords").checked, bpm: bpm >= 20 ? String(bpm) : "", ...options() }) });
      download(await resp.blob(), filenameFrom(resp, "score.mid"));
    } catch (e) { alert("MIDI export failed: " + e.message); }
  };

  // ------------------------------------------------------------------ library
  $("btnSave").onclick = async () => {
    const title = (/^\s*title\s*:\s*(.+)$/mi.exec(ed.value) || [])[1];
    const name = prompt("Save as:", state.libName || (title || "").trim() || "untitled");
    if (!name) return;
    try {
      const r = await api(`/api/library/${encodeURIComponent(name)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: ed.value }) });
      state.libName = r.name; setStatus(`Saved “${r.name}” to the library`);
    } catch (e) { alert("Save failed: " + e.message); }
  };
  async function openLibrary() {
    const ul = $("libList"); ul.innerHTML = "<li class='muted'>Loading…</li>";
    $("dlgLibrary").showModal();
    try {
      const items = await api("/api/library");
      ul.innerHTML = items.length ? "" : "<li class='muted'>Nothing saved yet.</li>";
      for (const it of items) {
        const li = document.createElement("li");
        const nm = document.createElement("span"); nm.className = "name"; nm.textContent = it.name;
        nm.onclick = async () => {
          const r = await api(`/api/library/${encodeURIComponent(it.name)}`);
          ed.value = r.text; state.libName = r.name; $("dlgLibrary").close(); check();
        };
        const when = document.createElement("span"); when.className = "muted";
        when.textContent = new Date(it.modified * 1000).toLocaleString();
        const del = document.createElement("button"); del.className = "small ghost"; del.textContent = "Delete";
        del.onclick = async () => {
          if (!confirm(`Delete “${it.name}”?`)) return;
          await api(`/api/library/${encodeURIComponent(it.name)}`, { method: "DELETE" }); openLibrary();
        };
        li.append(nm, when, del); ul.appendChild(li);
      }
    } catch (e) { ul.innerHTML = `<li>${e.message}</li>`; }
  }
  $("btnLibrary").onclick = openLibrary;
  $("libClose").onclick = () => $("dlgLibrary").close();
  $("btnHelp").onclick = () => $("dlgHelp").showModal();
  $("helpClose").onclick = () => $("dlgHelp").close();

  // ------------------------------------------------------------------ tabs
  const TABS = ["scan", "preview", "score"];
  function currentTab() { return (document.querySelector(".tab.active") || {}).dataset?.tab || "scan"; }
  function selectTab(name) {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
    for (const t of TABS) $(`tab-${t}`).classList.toggle("hidden", t !== name);
    store.set("jianpu2xml.tab", name);
    if (name === "preview") refreshPreview();
    if (name === "score") refreshScore();
    if (name === "scan") { if (!state.zoom) applyZoom(); highlightCurrent(true); }
  }
  document.querySelectorAll(".tab").forEach((t) => t.onclick = () => {
    selectTab(t.dataset.tab);
    // keep the text selection visible (a blurred textarea hides it); skip on touch screens (keyboard pop-up)
    if (ed.selectionEnd > ed.selectionStart && matchMedia("(pointer: fine)").matches) ed.focus({ preventScroll: true });
  });

  // ------------------------------------------------------------------ resizable panes
  const layout = document.querySelector(".layout");
  const splitter = $("splitter");
  const savedLeft = store.get("jianpu2xml.left");
  if (savedLeft) layout.style.setProperty("--left", savedLeft);
  splitter.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    splitter.setPointerCapture(e.pointerId);
    splitter.classList.add("dragging"); document.body.classList.add("resizing");
    const box = layout.getBoundingClientRect();
    const pad = parseFloat(getComputedStyle(layout).paddingLeft) || 0;
    const move = (ev) => {
      const pct = Math.min(80, Math.max(20, ((ev.clientX - box.left - pad) / (box.width - 2 * pad)) * 100));
      layout.style.setProperty("--left", `${pct.toFixed(1)}%`);
      if (currentTab() === "scan" && !state.zoom) applyZoom();
    };
    const up = () => {
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", up);
      splitter.classList.remove("dragging"); document.body.classList.remove("resizing");
      store.set("jianpu2xml.left", layout.style.getPropertyValue("--left"));
      if (currentTab() === "score") rerenderScore();
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", up);
  });
  splitter.addEventListener("dblclick", () => {
    layout.style.removeProperty("--left"); store.set("jianpu2xml.left", "");
    if (currentTab() === "scan" && !state.zoom) applyZoom();
    if (currentTab() === "score") rerenderScore();
  });

  // ------------------------------------------------------------------ manuscript (OpenSheetMusicDisplay)
  const score = { osmd: null, zoom: +(store.get("jianpu2xml.scoreZoom") || 1), busy: false, again: false, timer: null };
  function osmdInstance() {
    if (score.osmd) return score.osmd;
    if (!window.opensheetmusicdisplay) throw new Error("Score renderer didn't load (static/vendor/opensheetmusicdisplay.min.js)");
    $("scoreBox").innerHTML = "";
    score.osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay($("scoreBox"), {
      autoResize: true, backend: "svg", drawTitle: true, drawComposer: true, drawLyricist: true,
      drawPartNames: false, drawMeasureNumbers: true, followCursor: false,
    });
    return score.osmd;
  }
  async function refreshScore() {
    if (score.busy) { score.again = true; return; }
    score.busy = true;
    $("scoreInfo").textContent = "Drawing…";
    try {
      const r = await postJSON("/api/xml", { text: ed.value, ...options() });
      score.map = r.map; scoreMarked = null;
      const osmd = osmdInstance();
      await osmd.load(r.xml);
      osmd.zoom = score.zoom;
      osmd.render();
      highlightCurrent(false);
      $("scoreZoomLabel").textContent = `${Math.round(score.zoom * 100)}%`;
      const t = $("optTranspose");
      $("scoreInfo").textContent = $("keyInfo").textContent + (t.value !== "0" ? ` · ${t.options[t.selectedIndex].text.trim()}` : "");
    } catch (e) {
      $("scoreInfo").textContent = "Can't draw: " + e.message;
    }
    score.busy = false;
    if (score.again) { score.again = false; refreshScore(); }
  }
  function graphicalNotes() {
    const out = [];
    const gs = score.osmd && score.osmd.GraphicSheet;
    if (!gs) return out;
    for (const measureRow of gs.MeasureList) {
      const m = measureRow && measureRow[0];
      if (!m) continue;
      for (const se of m.staffEntries) {
        for (const ve of se.graphicalVoiceEntries) {
          for (const gn of ve.notes) out.push(gn);
        }
      }
    }
    return out;
  }
  let scoreMarked = null;
  // bright yellow box drawn behind the note inside the score's SVG
  function markScoreNote(el) {
    document.querySelectorAll("#scoreBox .score-hl").forEach((r) => r.remove());
    scoreMarked = el;
    if (!el || !el.parentNode) return;
    let bb;
    try { bb = el.getBBox(); } catch { return; }
    const pad = 4;
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("class", "score-hl");
    r.setAttribute("x", bb.x - pad); r.setAttribute("y", bb.y - pad);
    r.setAttribute("width", bb.width + 2 * pad); r.setAttribute("height", bb.height + 2 * pad);
    r.setAttribute("rx", 3);
    if (el.getAttribute("transform")) r.setAttribute("transform", el.getAttribute("transform"));
    el.parentNode.insertBefore(r, el);
  }
  $("scoreBox").addEventListener("click", (e) => {
    const notes = graphicalNotes();
    if (!notes.length || !score.map) return;
    let idx = -1, bestD = Infinity;
    notes.forEach((gn, i) => {
      const el = gn.getSVGGElement && gn.getSVGGElement();
      if (!el) return;
      if (el.contains(e.target)) { idx = i; bestD = -1; return; }
      if (bestD < 0) return;
      const r = el.getBoundingClientRect();
      const dx = Math.max(r.left - e.clientX, 0, e.clientX - r.right);
      const dy = Math.max(r.top - e.clientY, 0, e.clientY - r.bottom);
      const d = Math.hypot(dx, dy);
      if (d < bestD) { bestD = d; idx = i; }
    });
    if (idx < 0 || bestD > 25) return;
    if (notes.length !== score.map.length) console.warn(`manuscript notes ${notes.length} vs map ${score.map.length}`);
    const m = score.map[idx]; if (!m) return;
    markScoreNote(notes[idx].getSVGGElement());
    selectSpan(m[0], m[1], m[2]);
  });
  function rerenderScore() {
    if (!score.osmd) return;
    try { score.osmd.render(); scoreMarked = null; highlightCurrent(false); } catch { refreshScore(); }
  }
  function scoreSoon() {
    if (currentTab() !== "score" || !$("scoreAuto").checked) return;
    clearTimeout(score.timer); score.timer = setTimeout(refreshScore, 900);
  }
  function setScoreZoom(z) {
    score.zoom = Math.min(3, Math.max(0.3, z)); store.set("jianpu2xml.scoreZoom", String(score.zoom));
    $("scoreZoomLabel").textContent = `${Math.round(score.zoom * 100)}%`;
    if (score.osmd) { score.osmd.zoom = score.zoom; rerenderScore(); }
  }
  $("btnScore").onclick = refreshScore;
  $("scoreIn").onclick = () => setScoreZoom(score.zoom * 1.15);
  $("scoreOut").onclick = () => setScoreZoom(score.zoom / 1.15);
  $("scoreZoomLabel").onclick = () => setScoreZoom(1);
  $("scorePrint").onclick = () => { if (!score.osmd) refreshScore(); setTimeout(() => window.print(), 300); };
  ed.addEventListener("input", scoreSoon);
  for (const id of ["optTranspose", "optEnh", "optOct", "optClef"]) $(id).addEventListener("change", scoreSoon);

  const preview = { boxes: [], box: null };
  async function refreshPreview() {
    const box = $("previewBox");
    try {
      const r = await postJSON("/api/preview", { text: ed.value });
      box.innerHTML = "";
      const img = new Image(); img.src = "data:image/png;base64," + r.png; img.style.maxWidth = "100%";
      img.title = "Click a note to select it in the text";
      img.onload = () => placeMarker(box, img, preview.box);
      img.onclick = (e) => {
        const z = img.naturalWidth / img.clientWidth;
        const hit = hitBox(preview.boxes, e.offsetX * z, e.offsetY * z, (b) => b);
        if (!hit) return;
        preview.box = hit; placeMarker(box, img, hit);
        selectSpan(hit[4], hit[5], hit[6]);
      };
      preview.boxes = r.boxes; preview.box = null;
      img.addEventListener("load", () => highlightCurrent(false), { once: true });
      box.appendChild(img);
    } catch (e) { box.innerHTML = `<div class="empty">${e.message}</div>`; }
  }
  window.addEventListener("resize", () => {
    const img = $("previewBox").querySelector("img"); if (img) placeMarker($("previewBox"), img, preview.box);
  });
  $("btnPreview").onclick = refreshPreview;

  // ------------------------------------------------------------------ scan viewer
  const viewer = $("viewer");
  function showPage() {
    if (!state.scan) return;
    const img = new Image();
    const suffix = state.overlay && state.scan.recognized ? "-overlay" : "";
    img.src = `/api/scan/${state.scan.id}/${state.page}${suffix}.png?t=${state.scan.stamp || 0}`;
    img.onload = applyZoom;
    viewer.innerHTML = ""; viewer.appendChild(img);
    if (state.scan.notes) img.title = "Click a note to select it in the text";
    $("pgLabel").textContent = `Page ${state.page} / ${state.scan.pages}`;
  }
  function applyZoom() {
    const img = viewer.querySelector("img"); if (!img || !img.naturalWidth) return;
    const fit = (viewer.clientWidth - 2) / img.naturalWidth;
    const z = state.zoom || fit;
    img.style.width = `${img.naturalWidth * z}px`;
    placeMarker(viewer, img, scanMarkBox());
  }
  function scanMarkBox() {
    const n = state.markNote;
    if (!n || n.page !== state.page) return null;
    return (state.overlay && state.scan && state.scan.recognized) ? n.box : n.page_box;
  }
  viewer.addEventListener("click", (e) => {
    const img = viewer.querySelector("img");
    if (!img || e.target !== img || !state.scan || !state.scan.notes) return;
    const list = state.scan.notes[String(state.page)] || [];
    const useOverlay = state.overlay && state.scan.recognized;
    const key = useOverlay ? "box" : "page_box";
    const z = img.naturalWidth / img.clientWidth;
    const hit = hitBox(list, e.offsetX * z, e.offsetY * z, (n) => n[key]);
    if (!hit) return;
    state.markNote = { ...hit, page: state.page }; placeMarker(viewer, img, scanMarkBox());
    if (!selectRowNote(hit.row, hit.k)) setStatus("That note's line isn't in the text any more");
  });
  window.addEventListener("resize", () => { if (!state.zoom) applyZoom(); });
  $("zoomIn").onclick = () => { state.zoom = curZoom() * 1.25; applyZoom(); };
  $("zoomOut").onclick = () => { state.zoom = curZoom() / 1.25; applyZoom(); };
  $("zoomFit").onclick = () => { state.zoom = null; applyZoom(); };
  function curZoom() { const img = viewer.querySelector("img"); return img ? img.clientWidth / img.naturalWidth : 1; }
  $("pgPrev").onclick = () => { if (state.scan && state.page > 1) { state.page--; showPage(); } };
  $("pgNext").onclick = () => { if (state.scan && state.page < state.scan.pages) { state.page++; showPage(); } };
  $("showOverlay").onchange = (e) => { state.overlay = e.target.checked; showPage(); };

  const SCAN_RE = /\.(png|jpe?g|pdf|bmp|tiff?|webp)$/i;
  const TEXT_RE = /\.(txt|jp|jianpu)$/i;
  const XML_RE = /\.(musicxml|xml|mxl)$/i;
  async function importMusicXML(f) {
    const cur = ed.value.trim();
    if (cur && cur !== STARTER.trim() && !confirm(`Replace the text in the editor with ${f.name} converted to jianpu?`)) return;
    const fd = new FormData(); fd.append("file", f);
    setStatus(`Converting ${f.name}…`);
    try {
      const r = await api("/api/import", { method: "POST", body: fd });
      ed.value = r.text; state.libName = f.name.replace(/\.(musicxml|xml|mxl)$/i, "");
      store.set(DRAFT_KEY, ed.value);
      const c = await check();
      showMessages([...r.import_warnings.map((m) => [0, m]), ...(c ? c.warnings : r.warnings)],
                   `Imported ${f.name}: ${r.measures} measures from “${r.info.part}”.`);
      scoreSoon();
    } catch (e) { alert("Import failed: " + e.message); setStatus("Import failed"); }
  }
  async function uploadScan(f) {
    const fd = new FormData(); fd.append("file", f);
    selectTab("scan");
    $("scanName").textContent = `Uploading ${f.name}…`;
    try {
      const r = await api("/api/upload", { method: "POST", body: fd });
      state.scan = { id: r.id, pages: r.pages, name: r.name, recognized: false }; state.page = 1; state.zoom = null;
      $("scanName").textContent = `${r.name} · ${r.pages} page(s)`;
      const sel = $("ocrPage"); sel.innerHTML = '<option value="all">All</option>';
      for (let i = 1; i <= r.pages; i++) sel.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
      $("btnRecognize").disabled = false;
      $("ocrTitle").value = r.name.replace(/\.[^.]+$/, "");
      $("showOverlay").checked = false; state.overlay = false;
      showPage();
    } catch (err) { $("scanName").textContent = "Upload failed: " + err.message; }
  }
  async function loadTextFile(f) {
    if (XML_RE.test(f.name)) return importMusicXML(f);
    const cur = ed.value.trim();
    if (cur && cur !== STARTER.trim() && !confirm(`Replace the text in the editor with ${f.name}?`)) return;
    ed.value = await f.text(); state.libName = f.name.replace(/(\.jianpu)?\.txt$/i, "");
    store.set(DRAFT_KEY, ed.value); check(); scoreSoon();
  }
  $("fileInput").onchange = async (e) => {
    const f = e.target.files[0]; if (f) await uploadScan(f);
    e.target.value = "";
  };

  // ------------------------------------------------------------------ drag & drop (anywhere on the page)
  let dragDepth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth++; document.body.classList.add("dropping");
  });
  window.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); e.dataTransfer.dropEffect = "copy";
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dropping"); }
  });
  window.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; document.body.classList.remove("dropping");
    const files = [...e.dataTransfer.files];
    const txt = files.find((f) => TEXT_RE.test(f.name) || XML_RE.test(f.name) || (f.type === "text/plain" && !SCAN_RE.test(f.name)));
    const scan = files.find((f) => SCAN_RE.test(f.name) || /^image\//.test(f.type) || f.type === "application/pdf");
    if (!txt && !scan) { setStatus(`Can't use ${files.map((f) => f.name).join(", ")}. Drop a JPG/PNG/PDF scan, a MusicXML/.mxl file or a .txt file`); return; }
    if (txt) await loadTextFile(txt);
    if (scan) await uploadScan(scan);
    if (files.length > 1 && !(txt && scan && files.length === 2)) setStatus("Only the first scan (and first .txt) were used");
  });

  $("btnRecognize").onclick = async () => {
    if (!state.scan) return;
    const cur = ed.value.trim();
    if (cur && cur !== STARTER.trim() && !confirm("Replace the text in the editor with what is read from the scan?")) return;
    $("btnRecognize").disabled = true; $("ocrStatus").textContent = "Reading… (a few seconds per page on a Pi)";
    try {
      const r = await postJSON("/api/recognize", {
        id: state.scan.id, page: $("ocrPage").value, key: $("ocrKey").value, time: $("ocrTime").value,
        title: $("ocrTitle").value, black_only: $("ocrBlack").checked, chords: $("ocrChords").checked });
      ed.value = r.text; store.set(DRAFT_KEY, ed.value);
      state.scan.recognized = true; state.scan.stamp = Date.now(); state.scan.notes = r.notes;
      $("showOverlay").checked = true; state.overlay = true; showPage();
      const notes = r.stats.reduce((a, s) => a + s.notes, 0);
      const rows = r.stats.reduce((a, s) => a + s.rows, 0);
      const bad = r.warnings.filter(([, m]) => m.includes("needs")).length;
      $("ocrStatus").textContent = `${notes} notes in ${rows} rows · ${r.measures - bad}/${r.measures} measures add up`;
      const c = await check();
      showMessages([...r.ocr_warnings.map((m) => [0, m]), ...(c ? c.warnings : r.warnings)]);
      scoreSoon();
    } catch (err) { $("ocrStatus").textContent = "Failed: " + err.message; }
    $("btnRecognize").disabled = false;
  };

  // ------------------------------------------------------------------ start
  ed.value = store.get(DRAFT_KEY) || STARTER;
  check();
  const lastTab = store.get("jianpu2xml.tab");
  if (lastTab && TABS.includes(lastTab) && lastTab !== "scan") window.addEventListener("load", () => selectTab(lastTab));
})();

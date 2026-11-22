# jianpu2xml · v2.9.1

A small web app that reads **jianpu (简谱)** scans offline with OpenCV, lets you correct the
result as text, **transposes** it, and exports **MusicXML** for MuseScore, Sibelius, Dorico and
similar programs. No cloud and no API keys.

You can **run it locally** on your own Windows, Mac or Linux computer and use it in your browser
(see [Run it locally](#run-it-locally-windows-macos-linux)). You can also **host it on a
Raspberry Pi** so every phone and PC on your home network can use it
(see [Install on the Raspberry Pi](#install-on-the-raspberry-pi)). It is the same app either way.

```
┌─ Scan │ Jianpu preview │ Manuscript ─┬─ Transpose [+2 major 2nd up] Spelling Octave Clef ─┐
│ [Upload JPG/PNG/PDF]                  │ 1=D → 1=E (4♯)                                       │
│ Read notes: Key [D] Time [4/4] …      │ ┌─────────────────────────────────────────────────┐ │
│ [Read notes]  50 notes · 13/13 ok     │ │ key: D                                          │ │
│ ☑ Show what was detected              │ │ |: <1> 3 3 4 5 | <5> 5 4 3 2 | …                │ │
│ ┌───────────────────────────────────┐ │ └─────────────────────────────────────────────────┘ │
│ │  scan with detection overlay      │ │ [Download .musicxml] [.mxl]  [Save to library] …   │
│ └───────────────────────────────────┘ │ Messages: line 7: Measure 15 has 3.75 beats …      │
└───────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

## Workflow

1. **Upload** a JPG, PNG or PDF, or drag and drop it anywhere on the page.
   Dropping a `.txt` file opens it in the editor. Set the **key** (the `1=` printed on the chart) and the
   **time signature**, then press **Read notes**.
2. Turn on **Show what was detected** and compare it with the score:
   - green boxes: digits, labelled with what was read
   - blue: barlines
   - orange: underlines
   - cyan: dashes
   - red circles: dots
   - purple: chords, ties and triplets
3. Fix the text. The **Messages** list flags every measure that doesn't add up to the
   time signature; click a message to jump to that line. This catches most misreads.
   The **Jianpu preview** tab draws your text as jianpu so you can compare it with the scan.
   The **Manuscript** tab draws it as staff notation, with transpose, clef and octave applied.
   It redraws as you type and can be printed or saved as PDF with **Print**.
   Drag the bar between the two panes to resize them; double-click it to reset.
   **Click any note** in the Scan, Jianpu preview or Manuscript view to select it in the
   text, and the clicked note gets a bright yellow highlight in the view. On the scan, a note is found by its row
   and position, so this keeps working after you edit other lines. Clicking a dash selects
   the note it holds.
   The highlight follows you between tabs: the note selected in the text is marked in
   whichever view you open, and moving the cursor in the text moves the mark (switching scan
   pages if needed).
4. Add the lyrics (`w:` lines) and chord qualities (`<6m>`), which the reader doesn't capture.
5. Pick **Transpose** (for example `+2` turns 1=D into 1=E) and click **Download .musicxml**.
   Use **Save to library** to keep the notation on the Pi.

### Play it

**▶ Play** plays the melody in the browser, with soft chords from your chord symbols if
**Chords** is ticked. It works offline, with no plugins.

- **Order:** it follows the written structure, including repeats, 1st/2nd/3rd endings,
  D.S./D.C., To Coda, Coda and Fine.
- **Settings:** it uses the current Transpose and Octave settings.
- **Starting point:** put the cursor on a note to start there; otherwise it starts from the
  beginning.
- **Tempo:** the tempo comes from the `tempo:` header (90 if there isn't one). Type a number
  in the **Tempo** box to override it for practice.
- **Highlight:** while it plays, the current note is highlighted in the text and in the open
  view (scan, jianpu preview or manuscript), which scrolls to follow. **Esc** or **■ Stop**
  stops playback, and so does editing the text.
- **Download .mid:** saves a Standard MIDI file (melody on channel 1, chords on channel 2)
  with the same structure and tempo, for MuseScore, a DAW or a keyboard.

### Import MusicXML

Open (**Open text / MusicXML**) or drop a `.musicxml`, `.xml` or compressed `.mxl` file
(MuseScore, Sibelius, Finale and Dorico all export these). It becomes jianpu text you can
edit, transpose and export again. The importer takes:

- one melody line: the first part with notes, voice 1, and the top note of any chords
- key and time signatures, including changes, and the tempo
- the title, subtitle, composer, lyricist and arranger
- chord symbols, written as degrees such as `<6m/5>`
- ties, slurs, triplets and other tuplets, and fermatas
- repeats, 1st/2nd endings, segno/coda, and printed words
- every verse of lyrics
- the line breaks

Each beat of rest is written as its own `0` (jianpu style). If the melody sits low or high,
an `octave: -1` / `+1` header is added so the numbers need as few octave dots as possible.

### Export PDF

**Export PDF…** asks for the **title, subtitle, composer, lyricist and arranger**. They are
pre-filled from the text's header, and you can tick a box to write your changes back into the text. Choose:

- **Manuscript + jianpu**, **Manuscript only** or **Jianpu only**
- **A4** or **Letter** paper

The PDF uses the current Transpose, Spelling, Octave and Clef settings, so the jianpu pages
show the new `1=` key. It is built in your browser and downloads directly, and Chinese titles
and lyrics come out correctly. Pages are high-resolution images (about 200 dpi), sharp enough
for printing. For vector staff notation, open the exported MusicXML in MuseScore.

Your draft is also kept in the browser (localStorage) between visits.

## What the OpenCV reader handles

| Reads | Doesn't read (type these in) |
|---|---|
| digits 0–7; octave dots above/below | lyrics |
| underlines (8th/16th/32nd), augmentation dots, dashes | chord qualities `m`, `7`, `sus` (the number and a `/bass` are read) |
| `#` / `b` accidentals | words: rit., Bass, To Coda… |
| barlines `\|` `\|\|` `\|]` and repeats `\|:` `:\|` | 1st/2nd ending brackets `[1]` `[2]` |
| ties vs slurs (same pitch → tie); triplet `3` | key and time signature (set them on the form) |
| big chord numbers above the melody | mid-song key changes |

How it works, briefly: the reader turns coloured ink and highlights white (*Black ink only*)
and straightens a tilted page. It then scales the page so digits are 32 px tall and splits it
into glyphs, long lines and small marks. Digits are recognised by a k-nearest-neighbour
classifier on HOG features, trained at start-up on digits rendered from the fonts on the
machine and cached in `~/.cache/jianpu2xml`. The classifier is written in NumPy, so only core
OpenCV image functions are needed and any OpenCV build works. Rows of digits are melody lines. Rows without barlines just above a
melody line are chord numbers, and rows full of non-digit marks are lyrics and get skipped.

Accuracy:

- **Clean scans:** test pages read back 100% correct, including at half size, blurred, noisy
  and tilted 1.2°.
- **Phone photos:** these do worse. On a 900 px wide photo, about 75–80% of measures added up
  on the first pass.
- **Best results:** use **200–300 dpi** scans or PDFs, with the page flat and well lit.

## Run it locally (Windows, macOS, Linux)

All you need is Python 3.10 or newer; no Pi or server is required. Everything runs on your
computer, and your scans and library stay in a folder in your home directory.

**Windows** (PowerShell or Command Prompt, in the unzipped folder):

```bat
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Then open **http://localhost:8080** in your browser. Press **Ctrl+C** in the terminal to stop it.

- **Keep it private to this computer.** By default, other devices on your Wi-Fi can open
  `http://<your-PC's-IP>:8080` too. To allow only this computer:

  | System | Command |
  |---|---|
  | macOS / Linux | `HOST=127.0.0.1 .venv/bin/python app.py` |
  | Windows PowerShell | `$env:HOST="127.0.0.1"; .venv\Scripts\python app.py` |
  | Windows Command Prompt | `set HOST=127.0.0.1` then `.venv\Scripts\python app.py` |

- **Another port:** if 8080 is busy, set `PORT=8090` the same way.
- **Where data goes:** uploads and the saved-notation library are stored in
  `~/jianpu2xml-data` (on Windows, `C:\Users\<you>\jianpu2xml-data`). Set `JIANPU_DATA` to
  use another folder.
- **PDF scans:** these work out of the box, because PyMuPDF is in `requirements.txt`.
- **Command line:** no server needed; `python jianpu2xml.py song.txt -t 2` works on its own
  (see [Command line](#command-line)).
- **Updating:** unzip the new version over the folder and run
  `pip install -r requirements.txt` again. Your data folder isn't touched.

## Install on the Raspberry Pi

Written for Raspberry Pi OS Bookworm (64-bit), but not yet run on a Pi. A page takes about 1 second on a desktop CPU, so expect a few seconds on a Pi 4 or 5.

```bash
sudo apt update
sudo apt install -y python3-venv fonts-dejavu-core fonts-noto-cjk poppler-utils
cd ~ && unzip jianpu2xml.zip && cd jianpu2xml
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                 # try it: http://<pi-address>:8080
```

On **32-bit** Pi OS, or if `opencv-python-headless` won't install, use the system OpenCV instead:

```bash
sudo apt install -y python3-opencv python3-numpy
python3 -m venv --system-site-packages .venv
.venv/bin/pip install Flask gunicorn Pillow PyMuPDF
```

`fonts-noto-cjk` is only needed for Chinese lyrics in the Preview tab. `poppler-utils` is the
PDF fallback when PyMuPDF isn't installed.

### Run it as a service

```bash
sudo cp deploy/jianpu2xml.service /etc/systemd/system/   # edit User/paths if not "pi"
sudo systemctl daemon-reload
sudo systemctl enable --now jianpu2xml
journalctl -u jianpu2xml -f                              # logs
```

| Environment variable | Default | Meaning |
|---|---|---|
| `JIANPU_DATA` | `~/jianpu2xml-data` | uploads and the saved-notation library |
| `JIANPU_PASSWORD` | *(none)* | when set, the browser asks for it (any user name) |
| `JIANPU_KEEP_HOURS` | `48` | uploaded scans older than this are deleted |
| `PORT` | `8080` | for `python app.py` only; gunicorn uses `-b` |
| `HOST` | `0.0.0.0` | for `python app.py` only; `127.0.0.1` keeps it on this computer |

The app is meant for the home network. To reach it from outside, put it behind a VPN
(Tailscale or WireGuard) rather than forwarding the port.

## Troubleshooting

- **`module 'cv2' has no attribute …`**: usually two OpenCV packages are installed side by side.
  Clean up with `pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless`,
  then `pip install opencv-python-headless`. Since v2.1 the app needs only core functions
  (threshold, morphology, connected components, resize), so most installs work as they are.
- **Running on Windows, a Mac or a Linux PC** is covered in
  [Run it locally](#run-it-locally-windows-macos-linux).
- **The first "Read notes" takes a couple of seconds longer**, because the digit classifier is
  trained once and then cached.

## Text format

One text line per printed system. Tokens are separated by spaces.

| Write | Meaning |
|---|---|
| `key: D` / `time: 4/4` / `1=D 4/4` | key and time signature (`tempo:`, `title:`, `subtitle:`, `composer:`, `lyricist:`, `arranger:`, `clef:` and `octave:` also work) |
| `1`–`7`, `0` | scale degrees; 0 is a rest |
| `5'` `5''` / `5,` `5,,` | octave up (dot above) / octave down (dot below) |
| `5_` `5__` | eighth / 16th (one or two underlines) |
| `5.` `5_.` | dotted |
| `5 - - -` | each dash holds the note one more beat |
| `#4` `b7` `n4` | sharp, flat, natural |
| `5~ 5` | tie (if the pitches differ, it's written as a slur) |
| `(6_ 5_)` | slur |
| `3{ 5_ 3_ 2_ }` | triplet (`5{…}` quintuplet, and so on) |
| `5^` | fermata |
| `\|` `\|\|` `\|]` `\|:` `:\|` `:\|:` | barlines and repeats |
| `[1]` `[2]` | 1st/2nd ending at the start of a measure; `[/]` closes one at the next barline |
| `<1>` `<6m>` `<5/7>` `<b7>` `<Em>` `<G7/B>` | chord symbols, placed before the note they sit over. Numbers are degrees in the current key. All chords are transposed. |
| `"rit."` | text above the staff |
| `[segno]` `[coda]` `[tocoda]` `[fine]` `[dc]` `[ds]` `[dcalfine]` `[dsalcoda]` | navigation marks |
| `[key=F]` `[time=2/4]` | key or time change mid-song |
| `w: 明明還說分開` | lyrics for the music line above, one syllable per note (rests and tied continuations are skipped); `*` = note with no syllable; `Freu- de` = hyphenated syllables; a second `w:` line = verse 2 |
| `// …` | comment |

A plain number is the tonic in the octave from middle C: with `1=D`, `1` is D4.
To move the register for one piece, add `octave: -1` (or `+1`) to the header. For a quick
change at export time, use **Octave** (`auto` centres the melody on the staff).
Transposing only moves the key; every pitch, chord symbol and key signature is re-spelled
in the new key.

## Command line

```bash
python jianpu2xml.py song.txt -t 2                       # → song_E.musicxml
python jianpu2xml.py scan.pdf --key D -t 2 --overlay ov.png
                                                         # → scan.jianpu.txt + scan_E.musicxml
python jianpu2xml.py song.txt --preview song.png         # draw the text as jianpu
python jianpu2xml.py song.mxl -t 2                       # MusicXML → song.jianpu.txt + song_E.musicxml
python jianpu2xml.py song.txt -t 2 --pdf song.pdf --title "季候風" --subtitle "in E"
                                                         # jianpu PDF with a custom title
```

The command line makes jianpu PDFs only. For a staff-notation PDF, use the web app.

Other options: `--enharmonic auto|sharps|flats`, `--octave -2..2|auto`,
`--clef treble|bass`, `--time 3/4`, `--no-chords`, `--colour`, `-o out.mxl`, `--paper A4|Letter`,
and `--title` / `--subtitle` / `--composer` / `--lyricist` / `--arranger` (these override the text for
MusicXML and PDF output).

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app and JSON API |
| `version.py` | the version number, the only place to change it |
| `templates/index.html`, `static/` | the web page (vanilla JS, no build step) |
| `static/vendor/opensheetmusicdisplay.min.js` | [OpenSheetMusicDisplay](https://opensheetmusicdisplay.org) 1.9.9 (BSD-3), bundled so the manuscript view works offline |
| `static/vendor/jspdf.umd.min.js` | [jsPDF](https://github.com/parallax/jsPDF) 2.5.2 (MIT), for the PDF export |
| `omr.py` | OpenCV jianpu reader |
| `musicxml_import.py` | MusicXML / .mxl → jianpu text |
| `playback.py` | playing order (repeats, endings, jumps), timed notes for the player, MIDI writer |
| `render.py` | draws jianpu text as an image (Preview tab and tests) |
| `jianpu_core.py` | text parser, transposition, MusicXML 4.0 writer (standard library only) |
| `fonts.py` | finds system fonts for training glyphs and the preview |
| `jianpu2xml.py` | command line |
| `deploy/jianpu2xml.service` | systemd unit |
| `tests/` | `python tests/test_core.py`, `test_omr.py`, `test_import.py`, `test_playback.py` (or `pytest`) |

Exported files validate against the official MusicXML 4.0 XSD.

## Limitations

- Single melody line (one voice). Grace notes, dynamics and multiple parts aren't supported yet.
  MusicXML import takes just the melody from a multi-part score.
- Write a `w:` lyric line after each music line.
- Endings close at the next `:|`, `||` or `|]`; use `[/]` to close one earlier.
- The reader expects horizontal rows of printed digits. Handwritten jianpu won't read well.

## Versioning

The version lives only in `version.py`. You can see it in:

- the web page header, and `GET /api/version`
- `python jianpu2xml.py --version`
- the `<software>` field of every exported MusicXML file
- the PDF's "creator" field and the MIDI file's text

The CSS and JS links carry `?v=<version>`, so after an update, browsers fetch the new files
instead of using cached ones. To release a new version, bump the number in `version.py` and
restart the service with `sudo systemctl restart jianpu2xml`.

## Changelog

| Version | Changes |
|---|---|
| 2.9.1 | README: running locally on Windows/macOS/Linux; `HOST` setting for `python app.py` |
| 2.9.0 | Version number in the app, CLI, MusicXML, PDF and MIDI; cache-busting for static files |
| 2.8.0 | In-browser playback that highlights notes as it plays (repeats, endings, D.S./D.C., coda, Fine); tempo override; chord accompaniment; Download .mid |
| 2.7.0 | MusicXML / .mxl import; Export PDF (manuscript and/or jianpu, custom title/subtitle/credits, A4/Letter); `octave:` header; title credits and line breaks in exported MusicXML |
| 2.6.0 | Bright yellow highlights |
| 2.5.0 | Highlight follows between tabs; moving the cursor in the text marks the note in the open view |
| 2.4.0 | Drag and drop uploads |
| 2.3.0 | Click a note in Scan, Jianpu preview or Manuscript to select it in the text |
| 2.2.0 | Resizable panes; Manuscript tab (staff notation, OpenSheetMusicDisplay bundled) |
| 2.1.0 | OMR classifier in NumPy, so any OpenCV build works |
| 2.0.0 | Flask web app for the Raspberry Pi; offline OpenCV jianpu reader; jianpu preview; library |
| 1.0.0 | Tkinter desktop app; vision-model transcription; jianpu text → transposed MusicXML |

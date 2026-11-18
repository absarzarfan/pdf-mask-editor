# PDF Mask Editor

**by @absarz software**

Hide the same areas on every page of every PDF in a folder, in one pass. Built for
engineering drawings, where a title block, client logo or ownership notice sits in the
same place on every sheet.

![PDF Mask Editor](docs/screenshot.png)

## What it does

- Draw the areas to hide on a preview of one drawing. Move and resize them with the mouse.
- Apply those areas to every page of every PDF in the folder.
- Text and images inside an area are **deleted**, not just covered, so they can't be
  copied, searched or extracted afterwards. PDF metadata is cleared too.
- Originals are never changed. Masked copies go to a separate folder.
- Save the areas as a preset and reuse them for the next batch.
- Runs entirely on your computer. Nothing is uploaded.

## Run from source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/<your-username>/pdf-mask-editor.git
cd pdf-mask-editor
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Build the Windows app

Double-click `build.bat`. It creates a virtual environment, runs the tests and builds
`dist\PDFMaskEditor.exe`, a single file that runs without Python installed.

Pushing a tag such as `v1.0.0` to GitHub builds the `.exe` automatically and attaches
it to the release (see `.github/workflows/build.yml`).

## How to use

1. **Source folder**: choose the folder with your PDFs, or drop it on the window.
2. **Save masked copies to**: defaults to a `masked` subfolder.
3. **Mark the areas to hide**: drag on the drawing. Drag an area to move it, pull its
   corner to resize, press Delete to remove it.
4. **Mask every drawing**: choose white or black cover and press the button.

Spot-check one output file: try to select or search text inside a masked area.

### Options

| Option | Default | Effect |
| --- | --- | --- |
| Skip pages turned the other way | On | Pages in a different orientation from the preview page are copied unchanged, because the areas would land in the wrong place. |
| Also delete lines that cross into an area | Off | Off keeps drawing frames and table borders that pass through an area (they are painted over). On deletes them from the file completely. |

### Shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+O | Open source folder |
| Ctrl+Z | Remove last area |
| Delete | Remove selected area |
| Ctrl+scroll, Ctrl+0 | Zoom, fit page |
| Middle-drag or Alt+drag | Pan |
| Page Up / Page Down | Change page |
| Ctrl+Enter | Mask all drawings |

## Project layout

```
pdfmask/
  core.py         redaction engine (no UI, unit-tested)
  canvas.py       preview where areas are drawn
  main_window.py  main window
  splash.py       splash screen
  theme.py        colours, type and stylesheet
  worker.py       background batch thread
assets/           icon, fonts
tests/            pytest suite
```

Run the tests with `pytest`.

## Licence

Code: MIT, see `LICENSE`.
PyMuPDF is AGPL-3.0 (or commercial from Artifex); a distributed build that bundles it
must follow those terms. IBM Plex Sans is under the SIL Open Font License.

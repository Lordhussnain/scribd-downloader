<p align="center">
  <img src="assets/scribd.svg" alt="Scribd" width="200">
</p>

<h1 align="center">Scribd Downloader</h1>

<p align="center">
  <b>Download Scribd documents as clean PDFs with a Windows desktop app or the original CLI.</b><br>
  Paste one link or queue a whole list — export runs locally in headless Chrome.
</p>

<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  </a>
  <a href="https://pypi.org/project/selenium/">
    <img src="https://img.shields.io/badge/Selenium-4-green?style=for-the-badge&logo=selenium&logoColor=white" alt="Selenium 4">
  </a>
  <img src="https://img.shields.io/badge/Windows%20GUI-v1.1.2-teal?style=for-the-badge" alt="Windows GUI v1.1.2">
  <a href="https://github.com/Lordhussnain/scribd-downloader/stargazers">
    <img src="https://img.shields.io/github/stars/Lordhussnain/scribd-downloader?style=for-the-badge&logo=github" alt="GitHub stars">
  </a>
</p>

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Using the Windows app](#using-the-windows-app)
- [Settings and file locations](#settings-and-file-locations)
- [Building the .exe and installer](#building-the-exe-and-installer)
- [Using the CLI](#using-the-cli)
- [How it works](#how-it-works)
- [Benchmarks](#benchmarks)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Development and tests](#development-and-tests)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Credits, license, and disclaimer](#credits-license-and-disclaimer)

---

## Features

### Windows GUI (recommended)

- **Multi-document queue** — add as many Scribd links as you like. Documents download **one at a time**, which keeps Chrome memory bounded and lets the queue continue after an individual failure.
- **Many ways to add links** — paste one or more links, use the **Paste** button, drag & drop links from a browser, drop link files onto the window, or use **Add from file…**. Duplicate document IDs already pending/downloading are skipped.
- **Link-file import** — reads Scribd links from `.txt`, `.text`, `.md`, `.csv`, `.url`, `.html`, and `.htm` files (up to 2 MB per file).
- **Live progress** — Connect → Load pages → Export PDF → Merge stepper, current activity (for example, `Loading pages 1-8: 5/8 ready` or `Printing page 42 of 215…`), completed-page percentage, elapsed time, and ETA.
- **Queue controls** — rename a pending PDF with **Edit**, remove rows, clear finished/all items, **Retry** failed or cancelled items, **Open** finished PDFs, and use **Skip current document** or **Stop queue** while running.
- **Restore unfinished work** — pending queue items are saved and restored on the next launch.
- **Existing-file policy** — choose **Save as copy**, **Skip existing**, or **Overwrite** once; queue runs never stop to ask per file.
- **Autosaving settings** — appearance, output folder, existing-file policy, batch size, timeouts, visible-Chrome troubleshooting mode, clipboard detection, and open-when-done are persisted. The Settings footer shows `Saved ✓` with a time.
- **Recent downloads and activity log** — quickly reopen a recent PDF or reveal its folder, and inspect/copy the activity log when something goes wrong.
- **Clean themes** — System, Light, and Dark appearance modes.
- **Built-in diagnostics** — version is shown in the app header (currently **v1.1.2**), and a detailed log is written to `%APPDATA%\ScribdDownloader\diagnostic.log`.

### Download engine

- Supports `/document/<id>/Title`, legacy `/doc/<id>/Title`, `/embeds/<id>/content`, ID-only links, and Scribd subdomains such as `de.scribd.com`.
- Converts document links to Scribd embed URLs and exports them with Selenium + Chrome DevTools Protocol.
- Loads pages directly in **bounded batches** (default: 8) instead of scrolling through the whole document or keeping every page/image in memory.
- Uses short, cancellable page-batch probes with live ready/pending-image reporting, stable-ready checks, transient renderer retries, and a clear timeout instead of an indefinite hang.
- Detects each Scribd page's rendered size and validates that it produces **exactly one PDF sheet**.
- Removes toolbars, cookie/consent overlays, headers, and footers while preserving Scribd layout classes used by text, math, and SVG content.
- Spools one-page PDFs to temporary disk storage, releases each printed batch from Chrome, then merges the final PDF with `pypdf`.
- Sanitizes automatic filenames for Windows (illegal characters, trailing dots/spaces, reserved names, and overly long names).
- Large-document friendly: the original engine benchmarks include an image-heavy document with **2,552 pages**.

---

## Requirements

### Running from source

- **Windows 10 or 11** is the supported target for the GUI and installer. The engine/CLI is Python + Selenium and can run anywhere Chrome/Selenium are available, but the packaged app is Windows-focused.
- **Python 3.10 or newer**, with Tk available for the GUI. The python.org Windows installer includes Tk; tick **“Add python.exe to PATH”** during setup, or use the `py -3` launcher in the commands below.
- **Google Chrome** installed and reasonably up to date.
- Internet access to open Scribd documents. Selenium Manager also needs internet access the first time it downloads the matching ChromeDriver.
- Enough free disk space in your temporary folder and output folder for large documents; pages are spooled individually before merging.

### Running the built app

- The portable `.exe` and installer do **not** need Python on the target PC.
- **Google Chrome is still required** — it is not bundled.
- The Inno Setup installer is for **64-bit Windows 10+**.

---

## Quick start

### Option 1: Run the Windows GUI from source

Open PowerShell:

```powershell
git clone https://github.com/Lordhussnain/scribd-downloader.git
cd scribd-downloader
python -m pip install -r requirements.txt
python gui_app.py --check
python gui_app.py
```

If `python` is not on your PATH, replace it with `py -3` in the commands above.

After dependencies are installed, you can also double-click **`run_gui.bat`** to launch without a console window.

The `--check` command should finish with output similar to:

```text
Scribd Downloader 1.1.2
...
CustomTkinter: OK
Selenium     : OK
pypdf        : OK
Engine       : OK (...)
Chrome       : C:\Program Files\Google\Chrome\Application\chrome.exe
Settings     : C:\Users\<you>\AppData\Roaming\ScribdDownloader\settings.json
Diagnostics  : C:\Users\<you>\AppData\Roaming\ScribdDownloader\diagnostic.log
```

### Option 2: Build a portable `.exe`

On Windows, from the project folder in PowerShell:

```powershell
.\build_windows.bat
```

The result is:

```text
dist\ScribdDownloader.exe
```

That single windowed executable can be copied anywhere. The target PC still needs Google Chrome installed.

### Option 3: Build an installer

Install [Inno Setup 6](https://jrsoftware.org/isdl.php) first, for example:

```powershell
winget install JRSoftware.InnoSetup
.\build_installer.bat
```

The current installer output is:

```text
dist\installer\ScribdDownloader-Setup-1.1.2.exe
```

### Option 4: Use the CLI

```bash
python -m pip install -r requirements.txt
python scribd-downloader.py
```

Then paste a Scribd URL at the prompt. See [Using the CLI](#using-the-cli) for details and tuning variables.

---

## Using the Windows app

### 1. Add documents

You can add Scribd documents in any of these ways:

- Paste one link into **Add Scribd documents**, then press **Enter** or **Add to queue**.
- Paste several links at once (including links inside surrounding text); all distinct documents are queued.
- Click **Paste** to use Scribd link(s) from the clipboard.
- Drag links from your browser onto the app window.
- Drag & drop link files, or click **Add from file…** and choose one or more files containing links.
- With clipboard detection enabled, copied Scribd links can be offered/prefilled when the app gains focus.

Accepted link forms include:

```text
https://www.scribd.com/document/123456789/Document-Title
https://www.scribd.com/doc/123456789/Document-Title
https://www.scribd.com/embeds/123456789/content
https://www.scribd.com/document/123456789
```

### 2. Review the queue

- Each row shows the title, output filename/document ID, status, and — while downloading — live activity and completed-page percentage.
- Use **Edit** on a pending row to change the PDF filename. The `.pdf` extension is added and kept automatically.
- Use **✕** to remove a row that is not currently downloading.
- **Clear finished** removes completed/failed/cancelled/skipped rows; **Clear all** also removes pending rows when the queue is not running.
- Links added while a queue is running are appended and download after the current pending items.

### 3. Choose where PDFs are saved

- Set **Save PDFs to** directly or with **Browse…**. The default is your `Downloads` folder when it exists.
- If the output filename already exists, the app follows your selected policy:
  - **Save as copy** — creates `Name (1).pdf`, `Name (2).pdf`, and so on.
  - **Skip existing** — marks the row as skipped and moves on.
  - **Overwrite** — replaces the existing PDF after a successful export.

### 4. Download

Click **Download PDF** / **Download N PDFs**, or press **Ctrl+Enter**.

While running:

- Documents are processed **sequentially**. Later rows showing “waiting” is normal.
- The percentage counts **completed exported pages**. During Chrome startup, document rendering, and batch page/image loading, watch the activity text and animated progress bar instead of expecting the percentage to move immediately.
- **Skip current document** cancels at a safe point (the current page may finish first) and moves to the next item.
- **Stop queue** (or **Esc** while running) stops after the current safe point and leaves unstarted items pending for later.
- If one document fails, its row shows a short reason, the full error is written to diagnostics, and the queue continues with the next pending document.

### 5. Open or retry results

- Finished rows have an **Open** button.
- Failed/cancelled rows have **Retry**. If the queue is idle, retry starts immediately.
- The end-of-run summary can open the latest PDF or its folder.
- **Recent downloads** lists recently saved PDFs with quick open/folder actions.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Enter` in the link box | Add the entered link(s) to the queue |
| `Ctrl+Enter` | Start downloading the queue |
| `Esc` while running | Stop the queue |
| `Esc` in Settings | Close Settings (after timeout validation) |

---

## Settings and file locations

Open **Settings** from the header. Changes save immediately; download-related changes apply from the **next document**, not to a Chrome session that is already running.

| Setting | Default | Options / range | Notes |
|---|---:|---|---|
| Appearance | `System` | `System`, `Light`, `Dark` | Also available from the header menu. Saves immediately. |
| Pages loaded at once | `8` | `4`, `8`, `16` | Lower values use less Chrome memory on image-heavy documents. |
| Page load timeout | `120` seconds | `10`–`600` | Bounds initial page detection and each page/image batch. Raise for very slow connections or documents. |
| Export timeout | `600` seconds | `30`–`3600` | ChromeDriver/CDP ceiling for export commands. Individual page printing is additionally bounded so one page cannot hold the queue for ten minutes by default. |
| Show Chrome window while downloading | Off | On/Off | Troubleshooting mode. Turn this on to see browser checks, protection pages, or rendering problems. |
| Open the PDF when a single download finishes | Off | On/Off | Only opens automatically for a clean one-document run, not after every queue item. |
| Detect Scribd links on the clipboard | On | On/Off | Offers/prefills copied Scribd links when the app gains focus. |
| Save PDFs to | `Downloads` | Any writable folder | Remembered between launches. |
| If a file already exists | `Save as copy` | `Save as copy`, `Skip existing`, `Overwrite` | Remembered between launches. |

Timeout fields save when they lose focus, when you press **Enter**, or when Settings closes with **Done**/the window **X**. Invalid values are highlighted and must be fixed before the dialog closes; valid edits are not silently discarded.

### User data on Windows

```text
%APPDATA%\ScribdDownloader\settings.json
%APPDATA%\ScribdDownloader\diagnostic.log
%APPDATA%\ScribdDownloader\diagnostic.log.old   (after 2 MB log rotation)
```

`settings.json` contains your preferences, recent downloads, output folder, existing-file policy, and unfinished pending queue. Uninstalling the app intentionally keeps this folder. To fully reset the app, close it first and then delete or rename `%APPDATA%\ScribdDownloader`.

On non-Windows source runs, settings default to `$XDG_CONFIG_HOME/ScribdDownloader` or `~/.config/ScribdDownloader`.

---

## Building the .exe and installer

### Portable executable

`build_windows.bat` performs the full build:

1. Checks that Python is available.
2. Installs/upgrades `requirements.txt` dependencies and PyInstaller.
3. Regenerates icons with `assets\make_icon.py`.
4. Builds with `ScribdDownloader.spec` (single-file, windowed, `console=False`).
5. Runs `dist\ScribdDownloader.exe --check`.

Manual equivalent in PowerShell:

```powershell
python -m pip install -r requirements.txt pyinstaller
python assets\make_icon.py
python -m PyInstaller --noconfirm ScribdDownloader.spec
.\dist\ScribdDownloader.exe --check
```

Output:

```text
dist\ScribdDownloader.exe
```

Notes:

- Build on Windows for a Windows executable. PyInstaller does not cross-compile this project from Linux/macOS.
- Chrome itself is **not** bundled. Selenium Manager downloads the matching ChromeDriver on the target machine when needed.
- Because the executable is unsigned, Windows SmartScreen may warn on first run. For a self-built copy, choose **More info → Run anyway**, or run from source instead.

### Inno Setup installer

`build_installer.bat` locates Inno Setup 6, rebuilds and self-checks the portable `.exe`, then compiles `installer\ScribdDownloader.iss`.

Output for the current version:

```text
dist\installer\ScribdDownloader-Setup-1.1.2.exe
```

Installer behaviour:

- Per-user install by default (no administrator rights required), normally under `%LOCALAPPDATA%\Programs\Scribd Downloader`.
- Start-menu shortcut, optional desktop shortcut, upgrade-in-place support, and a normal Windows uninstaller.
- Warns before installing an older version over a newer one.
- Includes this README in the install folder.
- Keeps `%APPDATA%\ScribdDownloader` settings/queue on uninstall.

### Version numbers

When bumping the version, keep these in sync:

- `APP_VERSION` in `gui_app.py`
- `MyAppVersion` in `installer/ScribdDownloader.iss`
- file/product versions in `version_info.txt`

`tests/test_packaging.py` verifies that they match.

---

## Using the CLI

The original command-line interface remains available and uses the same `download_document()` engine as the GUI.

```bash
python scribd-downloader.py
```

Paste one Scribd URL when prompted:

```text
Input link Scribd: https://www.scribd.com/document/123456789/Document-Title
```

Legacy and embed URLs also work:

```text
Input link Scribd: https://www.scribd.com/doc/123456789/Document-Title
Input link Scribd: https://www.scribd.com/embeds/123456789/content
```

The PDF is saved in the current working directory using a sanitized filename derived from the URL slug (or `scribd-<document_id>.pdf` when there is no slug).

### Example output

```text
$ python scribd-downloader.py
Input link Scribd: https://www.scribd.com/document/903361807/WorkdaySimpleIntegrations-EIB-31v2

Link embed: https://www.scribd.com/embeds/903361807/content
Output filename: WorkdaySimpleIntegrations-EIB-31v2.pdf

Starting Chrome browser...
Launching ChromeDriver/Chrome (the first launch can take a while if ChromeDriver needs downloading)...
Chrome started.
Cookie dialogs hidden.
Detected 316 pages.
Top toolbar removed.
Bottom toolbar removed.
Adjusted 1 scroll containers for print.
Print CSS injected.

Saving PDF as: WorkdaySimpleIntegrations-EIB-31v2.pdf
  Export mode: Individual document pages
  Margins: None
  Headers/Footers: Disabled
  ChromeDriver command timeout: 600s
Exporting 316 document pages in bounded batches of 8...
  Loading page batch 1-8/316...
  Loaded page batch 1-8/316.
  Page 1/316 1002x1296px -> 10.438"x13.500"
    OK: exactly 1 PDF sheet
  ...
Merging 316 disk-spooled PDF pages...
PDF saved successfully to: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf
Browser closed.
```

### CLI environment variables

PowerShell example:

```powershell
$env:SCRIBD_PAGE_LOAD_TIMEOUT = "180"
$env:SCRIBD_CDP_TIMEOUT = "900"
$env:SCRIBD_EXPORT_BATCH_SIZE = "4"
$env:SCRIBD_HEADLESS = "0"
python scribd-downloader.py
```

| Variable | Default | Meaning |
|---|---:|---|
| `SCRIBD_HEADLESS` | `1` | Set to `0`, `false`, or `no` to show Chrome while debugging. |
| `SCRIBD_PAGE_LOAD_TIMEOUT` | `120` | Seconds allowed for page detection and each exported page batch. |
| `SCRIBD_CDP_TIMEOUT` | `600` | ChromeDriver command-timeout ceiling in seconds. |
| `SCRIBD_EXPORT_BATCH_SIZE` | `8` | Maximum fully loaded pages kept in Chrome at once. Lower this if Chrome uses too much memory. |

In the GUI, prefer **Settings** over environment variables; the GUI passes its saved batch size and timeouts explicitly for each document.

---

## How it works

1. **Parse the link** — extract the document ID and optional title slug from a `/document/`, `/doc/`, or `/embeds/` URL, then open `https://www.scribd.com/embeds/<id>/content`.
2. **Start Chrome eagerly** — Selenium returns at `DOMContentLoaded` instead of waiting for ads/trackers, then the engine explicitly waits for Scribd's `.outer_page` count to become stable within the page-load timeout.
3. **Clean the page for print** — targeted scripts dismiss/remove cookie and consent overlays, remove Scribd toolbars, adjust the document scroller, and inject print CSS without stripping layout classes that math/SVG content needs.
4. **Load one bounded batch** — Python polls Chrome with short synchronous probes. Each probe asks Scribd to load/display the batch, turns page images on once per inner page element, counts ready pages and pending/unstarted images, and requires stable readiness before printing. Progress is reported during the wait, cancellation is checked between probes, and a stalled batch fails with page numbers and reasons.
5. **Print each page exactly once** — the target page is isolated with print CSS, its real pixel size is converted to inches at 96 CSS px/inch, and Chrome `Page.printToPDF` prints one sheet with zero margins, no header/footer, background graphics enabled, scale `1`, and CSS page size preferred. The result is validated as exactly one PDF page.
6. **Release and spool** — each one-page PDF is written to temporary storage. At the end of a batch, page DOM/images are released from Chrome and garbage collection is requested.
7. **Merge** — `pypdf` appends the disk-spooled pages in order and writes the final PDF only at merge time, so cancelled GUI runs do not leave a misleading partial output file created by that run.
8. **Close safely** — Chrome shutdown is time-bounded; if `driver.quit()` fails because Chrome is wedged, the ChromeDriver service process is terminated so a queue can move on.

### PDF output settings

| Setting | Value |
|---|---|
| Page size | Detected dynamically from each rendered Scribd page |
| Margins | None (`0`) |
| Headers/footers | Disabled |
| Background graphics | Enabled |
| Scale | `1` |
| Pagination | Exactly one validated PDF sheet per Scribd page |

---

## Benchmarks

Reference results from one Windows machine are shown below. Performance varies with network speed, document complexity, CPU, RAM, Chrome version, and storage speed.

| Document | Pages | Total time | Output size | Blank pages | Peak combined RAM |
|----------|------:|-----------:|------------:|------------:|------------------:|
| CSS Solved Past Papers | 359 | 58.36 seconds | 228.49 MB | 0 | 1.47 GB |
| Manual de Servicio MX-305 | 2,552 | 14 minutes 12 seconds | 237.28 MB | 0 | 2.46 GB |

The batch size was `8` in both tests. Long documents still take time because Chrome prints and validates every page individually, but fully loaded browser content is kept to one batch at a time.

---

## Troubleshooting

Start with the GUI checks below. For any stubborn failure, collect the app version from the header, the failed document URL/ID, your Windows and Chrome versions, the row's error text, and the relevant part of `%APPDATA%\ScribdDownloader\diagnostic.log`.

### First: confirm you are running the current build

The app header should show **v1.1.2** or newer, for example:

```text
Download Scribd documents as clean PDFs  ·  v1.1.2
```

If you rebuilt but the header shows an older version — or the UI has no version in the header — you are launching an old `.exe` or an old source copy. Pull/rebuild again and launch the newly produced executable. From a terminal, `.\dist\ScribdDownloader.exe --check` also prints the version.

### A queue row stays at 0%, or later rows stay “waiting”

This is often normal during the first part of a document:

- Queue items download **one at a time**. If item 1 is a 215-page book, item 2 remains waiting until item 1 finishes, fails, or is skipped.
- The percentage is based on **completed exported pages**. While Chrome starts, Scribd renders, or a page/image batch loads, use the activity text and elapsed timer as the sign of life, e.g. `Waiting for Scribd to render… · 35s`, `Loading pages 1-8: 5/8 ready · 2 image(s) loading`, or `Printing page 1 of 215…`.

If a batch truly stalls, v1.1.2 fails that document at the **Page load timeout** with the affected page numbers/reason and continues the queue. Try:

1. Retry the failed row once in case it was a transient network/Chrome hiccup.
2. Increase **Settings → Page load timeout** for very large/slow documents (remember: changes apply from the next document).
3. Lower **Pages loaded at once** to `4` for image-heavy documents or low-memory PCs.
4. Enable **Settings → Show Chrome window while downloading**, then retry and watch what Scribd displays: a browser check, protection/login page, removed document, or slow page assets.
5. Check `diagnostic.log` for lines such as `Still loading page batch …` or `Failed to load Scribd page(s) after 120s: …` to see exactly which pages/images were pending.

### Settings do not seem to save

In current builds, Settings are autosaved and the footer must show **`Saved ✓`** with a time after a change.

Check these points:

- If there is no `Saved ✓` footer, you are on an older build — update/rebuild and confirm the header version first.
- Timeout fields save on focus-out, **Enter**, **Done**, or the window **X**. If a timeout is outside `10–600` (page) or `30–3600` (export), it is highlighted and Settings will not close until it is fixed.
- Batch/timeout/browser-visibility changes do not alter a Chrome session that is already running; they apply from the next document.
- If the footer says it could not save, check permissions for `%APPDATA%\ScribdDownloader\settings.json` and see `diagnostic.log`.
- To reset everything, **close the app first**, then delete/rename `%APPDATA%\ScribdDownloader\settings.json` (or the whole `ScribdDownloader` folder) and relaunch.

### “Chrome not found” in the header

Install [Google Chrome](https://www.google.com/chrome/), restart the app, and confirm the header changes to **Chrome ready**. You can also run:

```powershell
python gui_app.py --check
# or, for the built app:
.\dist\ScribdDownloader.exe --check
```

### ChromeDriver / session errors

The app uses Selenium Manager to obtain the ChromeDriver that matches your Chrome version.

- Make sure Chrome itself launches normally and is up to date.
- Allow internet access on the first run so ChromeDriver can be downloaded.
- From source, reinstall/update dependencies:

  ```powershell
  python -m pip install --upgrade -r requirements.txt
  ```

- If you use the packaged `.exe`, rebuild it after updating dependencies, or use a newer build.
- Include the exact error and recent `diagnostic.log` lines when reporting the problem.

### A document fails, is blank, or is missing pages/images

Possible causes include a private/removed document, a Scribd browser check, a document that requires permissions you do not have, very slow image assets, or a Scribd page-structure change.

Try:

1. Open the same document normally in Chrome and confirm the embed content is actually visible.
2. Retry with **Show Chrome window while downloading** enabled.
3. Increase **Page load timeout** and lower **Pages loaded at once** to `4`.
4. Test the same single link on its own, then in a two-item queue, and note whether the failure follows the document or the queue position.
5. Preserve the Activity log (`Copy log`) and the matching section of `diagnostic.log` for a bug report.

### Drag & drop does nothing

Drag & drop is an enhancement provided by `tkinterdnd2`; the rest of the app works without it.

- Run `python gui_app.py --check` and look at the `Drag & drop` line.
- Reinstall source dependencies with `python -m pip install -r requirements.txt`.
- In the meantime, paste links or use **Add from file…**.

### PDF was not saved, or Overwrite failed

- Confirm the output folder exists and is writable.
- If using **Overwrite**, close any PDF viewer that has the destination file open; Windows may lock it. Alternatively choose **Save as copy**.
- Make sure the temporary-drive and output-drive have enough free space for large documents.
- Check the failed row's message and `diagnostic.log` for the exact path/error.

### Windows SmartScreen warning

An unsigned, locally built `.exe` may trigger SmartScreen. If you built it yourself from this source, choose **More info → Run anyway**. If you prefer not to bypass SmartScreen, run the GUI from source with `run_gui.bat` or `python gui_app.py`.

### Very large documents are slow or memory-hungry

- Lower **Pages loaded at once** to `4` (or set `SCRIBD_EXPORT_BATCH_SIZE=4` for the CLI).
- Increase **Page load timeout** for slow image assets.
- Close other Chrome windows/apps if RAM is tight.
- Keep enough temporary disk space free; every page is spooled to disk before the final merge.
- Expect long runtimes anyway: each Scribd page is printed and validated separately, and queue items are processed sequentially by design.

---

## Project structure

```text
.
├── gui_app.py                    # Windows GUI: queue, drag-&-drop, settings, --check
├── scribd-downloader.py          # Shared download engine + interactive CLI
├── requirements.txt              # Engine and GUI dependencies
├── run_gui.bat                   # Launch the GUI from source without a console
├── build_windows.bat             # Build dist\ScribdDownloader.exe
├── build_installer.bat           # Build exe + Inno Setup installer
├── ScribdDownloader.spec         # PyInstaller one-file windowed build
├── version_info.txt              # Windows version resource for the exe
├── installer/
│   └── ScribdDownloader.iss      # Inno Setup 6 script
├── assets/
│   ├── make_icon.py              # Generates app/installer icons and wizard art
│   ├── icon.ico / icon.png       # App icons
│   ├── installer/                # Generated Inno wizard bitmaps
│   └── scribd.svg                # README/header artwork
└── tests/
    ├── test_engine.py            # URL, engine, batch-loader, timeout tests
    ├── test_gui_helpers.py       # Queue worker, settings, drop parsing tests
    └── test_packaging.py         # Version sync and referenced-file tests
```

---

## Development and tests

Install dependencies, then run the headless test suite. The tests do not need a display, network access, or a real Scribd download; browser behaviour is exercised with fakes/mocks where appropriate.

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python gui_app.py --check
python -m py_compile gui_app.py scribd-downloader.py
```

Current suite: **54 tests**, covering URL parsing/extraction, filename sanitizing, stable page detection, the polling batch loader (stable readiness, deadline, cancellation, transient renderer errors, dead sessions), bounded Chrome shutdown, queue ordering/failure-continuation/skip-existing/worker finalization, settings normalization and round-trips, drag-&-drop parsing, and packaging/version consistency.

Real end-to-end verification still needs Windows + Chrome + live Scribd documents. When changing the engine, test at least:

- one short text document,
- one scanned/image-only document,
- one large document (100+ pages),
- a two-item queue containing the large document first, and
- Skip/Stop during the load and export stages.

---

## Limitations

- The queue is intentionally **sequential**; there is no parallel downloading. This avoids multiplying Chrome memory use across documents.
- The tool does not log in to Scribd. Documents that are private, removed, region/permission restricted, or blocked by a browser check may fail.
- Scribd can change its embed DOM or page-manager internals at any time; that can require engine updates.
- Google Chrome must be installed separately. The packaged app bundles Python dependencies and Selenium Manager, not Chrome itself.
- Output is a print-rendered PDF of the embed view, not Scribd's original source file. Visual fidelity depends on how the document renders in Chrome.
- Very large documents can produce very large PDFs and need substantial temporary disk space while pages are spooled and merged.

---

## Contributing

Contributions are welcome:

1. Fork the repository.
2. Create a focused feature/fix branch.
3. Make the change and add or update tests where the logic is testable headlessly.
4. Run:

   ```powershell
   python -m unittest discover -s tests -v
   python gui_app.py --check
   ```

5. For engine changes, include your Windows/Chrome versions and the document types/page counts you tested. For bug reports, include the app version and the relevant `diagnostic.log` section.
6. Open a Pull Request describing the behaviour change and any Windows verification performed.

Please keep version fields in sync when preparing a release (see [Version numbers](#version-numbers)).

---

## Credits, license, and disclaimer

This repository builds on the original Scribd Downloader engine by [Usama Nazir](https://github.com/fullstackusama). The Windows GUI, multi-document queue, drag-&-drop import, packaging scripts, Inno Setup installer, and resilience/diagnostics work were added in this repository.

If the original upstream project has helped you, consider supporting its author:

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="assets/buymeacoffee.svg" alt="Buy Me A Coffee" width="40" height="40">
  </a>
  <br>
  <a href="https://github.com/sponsors/fullstackusama">Sponsor the original author on GitHub</a>
</p>

### License

The project has historically been described as MIT-licensed, but this repository currently does not include a standalone `LICENSE` file. Do not rely on a dead license link; check with the maintainers/upstream project before redistributing substantial portions, and consider adding the canonical license text in a follow-up change.

### Disclaimer

This tool is for educational and personal use with documents you have the right to access. Respect copyright law, document licences, and Scribd's Terms of Service. Do not use it to download or redistribute material you are not permitted to access.

---

<p align="center">
  If this is useful to you, please consider giving the repository a ⭐
</p>

## Incremental Rust port

`rust-core/` is the first step toward a Windows 10 Rust version. It ports the
Scribd URL parsing, link extraction, Windows filename helpers, the queue
model, settings validation, link-file import, output conflict selection, and
browser-independent engine page/batch tracking and download orchestration into a
Rust library. The orchestration is tested with a fake backend only; browser
scripts are shared with the new Rust ChromeDriver/PDF backend. The backend and
GUI still need build, Windows and real-document verification. Settings
persistence and several GUI features are not yet ported. The original Python
app and its Windows builds remain unchanged.

To test this component on Windows 10, install the Rust stable MSVC toolchain
(using [rustup](https://rustup.rs/)) and the Visual C++ build tools, then run:

```powershell
cd rust-core
cargo test
```

The Rust tests require no Chrome, Scribd access, or Python. Cargo needs network
access on the first run to obtain the crate dependencies. The current supported
Rust minimum is 1.70; Windows execution and eventual packaging still need to be
verified on a Windows 10 machine.

### Rust desktop preview (no Python runtime)

The Rust preview uses Google Chrome and a compatible **ChromeDriver**. It does
not use the Python engine. It is under development: the Rust browser/PDF backend
and GUI have not yet been built or tested on Windows or against a live Scribd
document. Keep the original app available, and verify output before relying on
it. Only download documents you have permission to access.

On Windows 10 or 11:

1. Install the Rust stable MSVC toolchain and Visual Studio C++ build tools.
2. Install Google Chrome and a ChromeDriver version matching your Chrome.
   Put `chromedriver.exe` on `PATH`, or set the `CHROMEDRIVER` environment
   variable to its full path. The Rust preview does not download it for you.
3. Open PowerShell in the repository root and run:

   ```powershell
   cargo run --manifest-path rust-gui\Cargo.toml --release
   ```

   Cargo downloads dependencies on the first build. The executable is then at
   `rust-gui\target\release\scribd-rust-gui.exe`. Chrome and ChromeDriver
   are still required; Python is not required for this executable.

The preview includes link/file import, queue controls, progress, filename and
output selection, conflict policy, cancellation, retry, and batch/timeout
controls. It does **not** yet have full parity with the Python GUI: settings
and pending work are not persisted, and drag-and-drop, clipboard detection,
themes, recent downloads, open-when-done and installer integration are missing.
The stop command is cooperative; it may wait for a browser operation to time
out. If you close the window during a download, the GUI requests cancellation;
wait for the download to finish before closing again.

<p align="center">
  <img src="assets/scribd.svg" alt="Scribd" width="200">
</p>

<h1 align="center">Scribd Downloader</h1>

<p align="center">
  <b>Download Scribd documents as PDF for free - Fast, automated, and runs in background!</b>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  </a>
  <a href="https://pypi.org/project/selenium/">
    <img src="https://img.shields.io/badge/Selenium-4.0+-green?style=for-the-badge&logo=selenium&logoColor=white" alt="Selenium 4.0+">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-orange?style=for-the-badge" alt="MIT License">
  </a>
</p>

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee">
  </a>
  <a href="https://github.com/sponsors/fullstackusama">
    <img src="https://img.shields.io/badge/Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white" alt="GitHub Sponsors">
  </a>
  <a href="https://github.com/fullstackusama/scribd-downloader/stargazers">
    <img src="https://img.shields.io/github/stars/fullstackusama/scribd-downloader?style=for-the-badge&logo=github" alt="GitHub Stars">
  </a>
</p>

---

## Features

- **Windows desktop app** - Clean GUI with a multi-document **queue**, drag-&-drop, live progress, recent downloads, dark/light themes, and an Inno Setup installer (see below)
- **One-click download** - Just paste the Scribd URL and get your PDF
- **Supports both Scribd URL styles** - Works with `/document/...` and legacy `/doc/...` links
- **Runs in background** - Headless Chrome, no browser window pops up
- **No scrolling required** - Loads Scribd page data directly instead of simulating page-by-page scrolling
- **Clean PDFs** - No cookie banners, toolbars, or watermarks
- **Bounded-memory export** - Keeps only a configurable batch of fully loaded pages in Chrome at once
- **Disk-spooled merge** - Writes each rendered page to temporary storage before combining the final PDF with `pypdf`
- **Large document support** - Verified with image-heavy documents containing up to 2,552 pages
- **Better math rendering** - Preserves Scribd layout classes needed by equations and SVG content
- **Exact pagination** - Validates that every Scribd page produces exactly one PDF sheet
- **Dynamic page size** - Detects each rendered page's dimensions instead of forcing one fixed sheet size
- **Auto filename** - PDF named after the document URL automatically
- **No login required** - Works without Scribd account

---

## Requirements

- Python 3.10 or higher
- Google Chrome browser installed
- Chrome WebDriver (auto-managed by Selenium)

---

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/fullstackusama/scribd-downloader.git
   cd scribd-downloader
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

---

## Windows App (GUI)

A clean, single-window desktop app built on the same engine — paste a link, watch page-by-page progress with an ETA, cancel anytime, then open the PDF right from the app.

**Run from source:**

```powershell
pip install -r requirements.txt
python gui_app.py
```

…or double-click **`run_gui.bat`** (launches without a console window).

**Build a standalone `.exe`** (single file, no Python needed on the target PC):

```powershell
build_windows.bat
```

This installs dependencies, regenerates the icon (`assets/make_icon.py`), runs PyInstaller with `ScribdDownloader.spec`, and self-checks the result at `dist\ScribdDownloader.exe`. Google Chrome still needs to be installed on the target PC (the app shows a “Chrome ready / not found” status in the header).

**Build an installer** (per-user install, Start-menu shortcuts, clean uninstaller — needs [Inno Setup 6](https://jrsoftware.org/isdl.php), or `winget install JRSoftware.InnoSetup`):

```powershell
build_installer.bat
```

This builds the `.exe` first, then compiles `installer\ScribdDownloader.iss` to `dist\installer\ScribdDownloader-Setup-<version>.exe`. It installs without admin rights to `%LOCALAPPDATA%\Programs\Scribd Downloader`, upgrades in place, and warns before downgrading over a newer version. Settings and your saved queue in `%APPDATA%\ScribdDownloader` are kept on uninstall.

What you get:

- **Download queue** — add as many documents as you like; they download one at a time (keeping Chrome memory bounded) with per-document status, **Skip current / Stop queue**, per-row rename/retry/open, and unfinished items restored on next launch. Existing files follow your *Save as copy / Skip / Overwrite* policy, so batches never stop for prompts
- **Drag & drop** — drop Scribd links from your browser, or `.txt` / `.url` files full of links, anywhere on the window (a drop overlay confirms); **Add from file…** does the same via a picker. Pasting several links at once queues them all, deduplicated
- **Smart paste** — Scribd link(s) already on your clipboard are detected automatically; links are validated as you type with the document title/ID previewed
- **Live progress** — Connect → Load → Export → Merge step indicator, per-page progress bar, elapsed/ETA, and a collapsible activity log you can copy when reporting issues
- **Cancel safely** — stops between pages and discards incomplete output
- **Recent downloads** — reopen or reveal the last 5 PDFs in one click
- **Settings dialog** — appearance (System/Light/Dark), pages-per-batch, timeouts, show-browser troubleshooting mode, open-when-done. Changes save automatically (including when closing with the X) with a visible “Saved ✓” confirmation, to `%APPDATA%\ScribdDownloader\settings.json`; download-setting changes apply from the next document
- **Diagnostics** — `python gui_app.py --check` (or `ScribdDownloader.exe --check`) verifies the engine, dependencies, and Chrome detection without opening a window

---

## Usage

1. **Run the script**
   ```bash
   python scribd-downloader.py
   ```

2. **Paste the Scribd document URL** when prompted:
   ```
   Input link Scribd: https://www.scribd.com/document/123456789/Document-Title
   ```

   Legacy Scribd URLs also work:
   ```
   Input link Scribd: https://www.scribd.com/doc/123456789/Document-Title
   ```

3. **Wait for the download** - The script will:
   - Open the document in headless Chrome
   - Load document pages directly in bounded batches
   - Release each batch from Chrome after printing to control memory use
   - Remove unwanted elements (toolbars, cookie banners)
   - Spool individual pages to temporary storage and merge the final PDF
   - Save the PDF in the current directory

4. **Done!** Your PDF will be saved with the document name from the URL.

---

## Example Output

```text
$ python scribd-downloader.py
Input link Scribd: https://www.scribd.com/document/903361807/WorkdaySimpleIntegrations-EIB-31v2

Link embed: https://www.scribd.com/embeds/903361807/content
Output filename: WorkdaySimpleIntegrations-EIB-31v2.pdf

Starting Chrome browser...
Cookie dialogs hidden.
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
  Page 1/316 1002x1296px -> 10.438"x13.500"
    OK: exactly 1 PDF sheet
  ...
Merging 316 disk-spooled PDF pages...
PDF saved successfully to: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf
Browser closed.
```

---

## PDF Settings

| Setting | Value |
|---------|-------|
| Page Size | Detected dynamically from Scribd's rendered page |
| Margins | None (0) |
| Headers/Footers | Disabled |
| Background Graphics | Enabled |

---

## How It Works

1. **URL Conversion** - Converts Scribd document URL to embeddable format
2. **Headless Browser** - Opens Chrome in background (invisible)
3. **Batched Page Loading** - Loads a small group of Scribd pages and their images directly without scrolling
4. **Cleanup** - Removes toolbars, cookie banners, and overlays while preserving Scribd layout classes
5. **Per-page Export** - Detects each page's rendered size and prints exactly one PDF sheet through Chrome DevTools Protocol
6. **Memory Release** - Removes the completed batch from Chrome and requests garbage collection
7. **Disk-spooled Merge** - Combines the temporary one-page PDFs into the final document with `pypdf`
8. **Auto Close** - Browser closes automatically after saving

---

## Benchmarks

Reference results from one Windows machine are shown below. Performance varies with network speed, document complexity, CPU, RAM, Chrome version, and storage speed.

| Document | Pages | Total Time | Output Size | Blank Pages | Peak Combined RAM |
|----------|------:|-----------:|------------:|------------:|------------------:|
| CSS Solved Past Papers | 359 | 58.36 seconds | 228.49 MB | 0 | 1.47 GB |
| Manual de Servicio MX-305 | 2,552 | 14 minutes 12 seconds | 237.28 MB | 0 | 2.46 GB |

The batch size was `8` in both tests. Long documents still take time because Chrome must print every page individually, but fully loaded browser content is kept to one batch at a time.

---

## Troubleshooting

### GUI: Windows SmartScreen warning on first run
An unsigned, locally built `.exe` may trigger SmartScreen. Choose **More info → Run anyway** (you built it from this source), or run from source with `run_gui.bat`.

### GUI: “Chrome not found” in the header
Install [Google Chrome](https://www.google.com/chrome/), then restart the app. To reset all GUI settings, delete `%APPDATA%\ScribdDownloader\settings.json`.

### GUI: A download appears to stay at 0%
The percentage only advances during the **Export PDF** stage. While Chrome is starting, Scribd is rendering, or a batch of pages/images is loading, the queue row and progress card show the current activity with a live elapsed timer (for example, “Waiting for Scribd to render the document pages… · 35s” or “Loading pages 1-8: 5/8 ready · 2 image(s) loading”) and an animated bar. Queue downloads use the same sequential engine call as a one-item queue; a large first document can therefore spend a while in its first page batch while later documents correctly remain “waiting.”

Page detection and each page batch are bounded by **Settings → Page load timeout** (default 120 seconds). If a batch stalls, that document fails with the affected page numbers/reason instead of hanging indefinitely, and the queue proceeds to the next document. Individual page printing is likewise bounded so one wedged Chrome command cannot hold the whole queue at the same percentage.

If it repeatedly times out, open **Settings**, enable **Show Chrome window while downloading** (it saves immediately), then retry the document. The visible Chrome window will show whether Scribd presented a browser check, login/protection page, or another block. Full diagnostics are continuously written to `%APPDATA%\ScribdDownloader\diagnostic.log` — include the relevant section when reporting an issue. When updating from source or rebuilding the `.exe`, make sure the app reports the latest version (1.1.2 or newer); an older executable will not contain these queue/batch-loading fixes.

### GUI: Settings do not seem to save
In current builds, settings are saved as soon as each control changes; the footer must say **Saved ✓** with a time. Timeout fields save when they lose focus or when you press Done/X, and invalid values are highlighted instead of being silently discarded. If your Settings screen has no “Saved ✓” footer, you are still running an older build — pull the latest branch and rebuild (or reinstall) before retesting. If the footer reports that the file could not be saved, check permissions for `%APPDATA%\ScribdDownloader\settings.json`. Note that changing batch/timeout/browser visibility does not alter a Chrome session that is already running — it applies from the next document.

### "ChromeDriver not found" error
The script uses Selenium Manager to auto-download ChromeDriver. If you face issues:
```bash
pip install --upgrade selenium
```

### PDF not saving
- Ensure you have write permissions in the current directory
- Check if the Scribd URL is valid and accessible
- For very large documents, increase `SCRIBD_CDP_TIMEOUT` (default: `600`)

### Blank pages in PDF
- Some documents may have DRM protection
- Try increasing `SCRIBD_PAGE_LOAD_TIMEOUT` if page images load slowly
- If a document still renders incorrectly, try visible mode with `SCRIBD_HEADLESS=0`

### Very large documents
- Ensure the drive containing your temporary directory has enough free space for individual page PDFs
- Reduce `SCRIBD_EXPORT_BATCH_SIZE` if Chrome uses too much memory
- Increase `SCRIBD_PAGE_LOAD_TIMEOUT` when slow image assets time out
- Long documents still take time because each page is printed and validated separately

### Large, image-heavy, or math-heavy documents
You can tune the export with environment variables:

```powershell
$env:SCRIBD_CDP_TIMEOUT="900"
$env:SCRIBD_PAGE_LOAD_TIMEOUT="180"
$env:SCRIBD_EXPORT_BATCH_SIZE="4"
python scribd-downloader.py
```

Useful variables:

- `SCRIBD_CDP_TIMEOUT` - ChromeDriver command timeout in seconds for `Page.printToPDF`
- `SCRIBD_PAGE_LOAD_TIMEOUT` - Maximum direct page-loading time in seconds (default: `120`)
- `SCRIBD_EXPORT_BATCH_SIZE` - Maximum fully loaded pages kept in Chrome at once (default: `8`)
- `SCRIBD_HEADLESS=0` - Run with a visible browser when debugging rendering issues locally

---

## Contributing

Contributions are welcome! Feel free to:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## Support the Project

If you find this tool useful, consider supporting its development:

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="assets/buymeacoffee.svg" alt="Buy Me A Coffee" width="40" height="40">
  </a>
</p>

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Disclaimer

This tool is for educational purposes only. Please respect copyright laws and Scribd's Terms of Service. Only download documents you have the right to access.

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/fullstackusama">Usama Nazir</a>
</p>

<p align="center">
  If you find this useful, please consider giving it a ⭐
</p>

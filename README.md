# Scribd Downloader

A Windows desktop app built in Rust for exporting accessible Scribd documents to PDF. Add links to a queue, choose where to save them, and follow each document's progress from the desktop interface.

[Download Windows builds](https://github.com/Lordhussnain/scribd-downloader/releases) · [View build workflows](https://github.com/Lordhussnain/scribd-downloader/actions) · [Report an issue](https://github.com/Lordhussnain/scribd-downloader/issues)

> **Experimental software:** This Rust rewrite and its packaging are still being validated. A Windows CI build has succeeded, but the app has not been verified end-to-end against live documents on a clean Windows installation. Check exported PDFs before relying on them. Only download documents you have the right to access and save.

## Features

- **Document queue:** Add multiple links, edit filenames, retry items, skip a document, or stop the queue.
- **Flexible input:** Paste links, import a link file, or drop supported link files into the window. Clipboard detection can append newly copied links when you return to the app, without duplicating document IDs already in the input.
- **Download progress:** See the current activity, completed pages, elapsed time, and an estimated time remaining when available.
- **Save controls:** Choose an output folder and save a copy, skip, or overwrite when a file already exists.
- **Desktop preferences:** Light, dark, and system appearance; page loading and timeout settings; optional browser and ChromeDriver diagnostic windows; and an option to open the PDF when finished.
- **Local state:** Settings, recent downloads, pending queue items, and diagnostics are kept on your computer.

The app uses Chrome and ChromeDriver to render accessible document pages and merge them into a PDF. It does not guarantee access to restricted, private, removed, or protected documents.

## Download and run

1. Open the [Releases page](https://github.com/Lordhussnain/scribd-downloader/releases) and select an **experimental pre-release** for the package you want. Releases may be listed separately for each package type.
2. Download its Windows x64 ZIP and extract it.
3. Open the extracted folder and run `scribd-rust-gui.exe`. Keep the entire folder together, including `runtime` if present.

All packages target **64-bit Windows 10 or 11**. Internet access, standard Windows system components, and a compatible graphics driver are required. Rust and Python are **not** required on the target PC.

### Choose a package

| Package | What's included | What you need to install | Trade-off |
| --- | --- | --- | --- |
| **Full portable** (`ScribdDownloader-Windows-x64.zip`) | App, Chrome for Testing, and matching ChromeDriver | Nothing extra for the browser | Largest download; easiest to move to another PC |
| **Driver-only** (`ScribdDownloader-DriverOnly-Windows-x64.zip`) | App and the latest stable ChromeDriver at build time | A compatible installation of Google Chrome | Smaller; Chrome and the bundled driver must be compatible |
| **System** (`ScribdDownloader-System-Windows-x64.zip`) | App only | Chrome and a compatible ChromeDriver on `PATH`, or `CHROMEDRIVER` set to the driver's full path | Smallest; you manage both dependencies |

The driver-only build uses the installed Chrome; it does not automatically match the driver to your installed browser. The bundled browser and driver do not update themselves. Download a newer build regularly for browser security updates and compatibility. Review their licence terms before redistribution.

> The two smaller package variants and their release workflows have not yet been verified end-to-end on Windows. Package sizes depend on the build and compression; the extracted folder will be larger than the ZIP.

## Use the app

1. Paste one or more Scribd links into the input box and select **Add to queue**. You can also use **Add from file…** or drop a supported link file into the window.
2. Choose an output folder under **Save PDFs to** and select an existing-file policy.
3. Select **Download PDF**. Documents are processed one at a time.
4. Follow progress in the download card. Use **Skip current document** or **Stop queue** if needed.

To append copied links automatically, enable **Settings → Detect Scribd links from clipboard**. When you return focus to the app, new links are appended to the existing input; links for document IDs already present are skipped. The **Paste** button is also available.

Cancellation is cooperative: a stop or skip can wait for the current browser operation to finish or time out. If you close the window during a download, cancellation is requested; wait for the current item to finish before closing again.

### Settings and diagnostics

On Windows, settings, recent downloads, and pending queue items are stored in:

```text
%APPDATA%\ScribdDownloader\settings-rust.json
```

The activity log is saved as `diagnostic-rust.log` in the same folder. Use **Diagnostics folder** in the app to open it. For troubleshooting, **Settings → Show Chrome window while downloading** displays Chrome; **Show ChromeDriver console (diagnostics)** independently displays the driver console for the next download.

## Build from source

### Requirements

On the **build PC**, install:

- Rust stable with the `x86_64-pc-windows-msvc` target and Cargo.
- Visual Studio C++ build tools with the MSVC toolchain.
- Internet access to fetch Rust dependencies and, for bundled packages, Chrome for Testing components.
- Inno Setup 6 only if building the optional installer.

Clone the repository and open PowerShell in its root:

```powershell
git clone https://github.com/Lordhussnain/scribd-downloader.git
cd scribd-downloader
```

### Build the full portable package

```powershell
.\build_windows.bat
```

The script builds the app with the static MSVC runtime and downloads a matched stable Chrome for Testing and ChromeDriver pair. The resulting folder is:

```text
dist\ScribdDownloader-Portable\
  scribd-rust-gui.exe
  PACKAGE-INFO.txt
  runtime\
    package-mode.txt
    chrome-win64\
    chromedriver-win64\
```

Copy the **entire folder** to the target PC. The app prefers this bundled pair over `CHROMEDRIVER` or a system installation; an incomplete bundled runtime is reported as an error.

### Build a smaller package

```powershell
# App and ChromeDriver; Chrome is installed separately
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -Mode DriverOnly

# App only; Chrome and ChromeDriver are installed separately
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -Mode System
```

The output folders are `dist\ScribdDownloader-DriverOnly` and `dist\ScribdDownloader-System`. Keep `runtime` with the executable in the driver-only package. For system builds, the app uses `CHROMEDRIVER` if set, otherwise it looks for ChromeDriver on `PATH`.

### Build an installer

With Inno Setup 6 installed, run:

```powershell
.\build_installer.bat
```

The installer is written to `dist\installer` and includes the **full** portable package. Installer behavior has not yet been verified on a clean Windows PC.

### Run tests

```powershell
.\test_windows.bat
```

Or run the suites individually:

```powershell
cargo test --manifest-path rust-core\Cargo.toml
cargo test --manifest-path rust-browser\Cargo.toml
cargo test --manifest-path rust-gui\Cargo.toml
```

Automated tests use fakes, local loopback responses, or temporary files; they do not validate a live Scribd download or actual GUI interactions. For an end-to-end check, try a document you are allowed to download and inspect the PDF pages and images. Also test the queue, cancellation, file-conflict options, and restored settings.

## Automated builds and releases

GitHub Actions tests and builds all three variants. Successful scheduled runs, manual runs, and pushes to the default branch publish separate **experimental pre-releases**. Pull requests and feature-branch pushes upload workflow artifacts instead; artifacts expire after 14 days. Pre-releases do not replace a stable release or update installed copies.

| Workflow | Sunday schedule | Pakistan time |
| --- | --- | --- |
| Full portable | 02:00 UTC | 07:00 |
| Driver-only | 02:15 UTC | 07:15 |
| System | 02:30 UTC | 07:30 |

GitHub runs scheduled workflows from the repository's **default branch**, so each schedule begins only after its workflow is merged there. Scheduled runs may be delayed or disabled after public-repository inactivity. Automatic publishing also requires GitHub Actions to have permission to write repository contents.

## Project structure

```text
rust-core/       URL parsing, queue, download orchestration, and shared logic
rust-browser/    ChromeDriver integration and PDF printing/merging
rust-gui/        Desktop interface and settings
assets/          Application artwork and installer assets
installer/       Inno Setup configuration
.github/         Windows build and release workflows
```

## Limitations

- This is an experimental rewrite; some features and interactions may differ from the earlier desktop app. Browser-link drag-and-drop and exact feature parity are not complete.
- Scribd can change its page structure, and inaccessible documents may fail. Large documents can require substantial temporary disk space.
- Progress and remaining-time estimates are approximate. PDF merging can continue after the pages have been exported.
- Chrome/ChromeDriver version mismatches can prevent a browser session from starting, particularly in the smaller packages.

## Responsible use and licence

Use this software only for documents you have the right to access and download. Respect copyright, document licences, and Scribd's terms of service.

The project has historically been described as MIT-licensed, but it does not currently include a standalone licence file. Check with the maintainers before redistributing.

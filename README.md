# Scribd Downloader — Rust preview

A Windows-focused Rust desktop app that renders accessible Scribd documents
in Chrome and exports them to PDF.

**Status:** This is an experimental rewrite. The portable build and installer
have not yet been verified on a clean Windows 10 machine or against live
Scribd documents. Check exported PDFs before relying on them.

## Build a portable package on Windows

On the **build PC**, install Rust stable with the x86-64 MSVC toolchain and
Visual Studio C++ build tools. The first build needs internet access to download
Rust dependencies and a matching stable Chrome for Testing / ChromeDriver pair.
From PowerShell in the repository root, run:

```powershell
.\build_windows.bat
```

The package is created at:

```text
dist\ScribdDownloader-Portable\
  scribd-rust-gui.exe
  runtime\chrome-win64\          # Chrome for Testing, including its files
  runtime\chromedriver-win64\    # matching ChromeDriver
  PACKAGE-INFO.txt
```

Copy **the entire folder**, not just the `.exe`, to a 64-bit Windows 10 or 11
PC, and run `scribd-rust-gui.exe`. The target PC does not need Rust, Python,
Google Chrome, or ChromeDriver installed. Internet is still needed to access
Scribd documents. The Rust app is built with the static MSVC runtime; standard
Windows system components and a compatible graphics driver are still required.
This packaging has not yet been tested on a clean Windows installation.

The bundled Chrome for Testing is fixed at the version selected when you build;
it does **not** auto-update. Rebuild and redistribute regularly for browser
security updates. Review the bundled browser and driver licence terms before
redistribution. The app prefers the bundled pair even if `CHROMEDRIVER` is set;
a missing file in an existing `runtime` folder is reported as an error.

### Build an installer

Install Inno Setup 6 on the build PC and run:

```powershell
.\build_installer.bat
```

The installer in `dist\installer` includes the same app and matching browser
runtime. It is not yet verified on Windows. To build just the executable for
development, use `cargo run --release --manifest-path rust-gui\Cargo.toml`;
that unbundled executable requires a locally installed Chrome and compatible
ChromeDriver on PATH or configured with `CHROMEDRIVER`.

## Using the preview

Paste Scribd links into the text box and choose **Add to queue**, or use
**Add from file** to import supported link files. Choose an output folder and
an existing-file policy, then start the queue. The app processes documents
one at a time. It provides progress, retry, skip, stop, filename editing,
batch-size and timeout controls, and a visible-Chrome troubleshooting mode.
On Windows, ChromeDriver's console is hidden by default. To show it for the
next download, enable **Settings → Show ChromeDriver console (diagnostics)**.
This is independent of **Show Chrome window while downloading**.

When clipboard detection is enabled, returning to the app with a new Scribd
link copied will prefill it. If you have edited the input, the app preserves
your text and offers **Add copied link** instead. While a document is running,
the progress card shows the current activity, completed pages, elapsed time,
and an estimated remaining time when enough page information is available.
During connection and initial loading, a spinner indicates that the total is
not yet known; PDF merging can continue after all pages have been exported.

Cancellation is cooperative and may wait for a browser operation to finish or
time out. Closing the window during a download requests cancellation; wait for
the current item to finish before closing again.

## Tests on Windows 10

Install Rust stable (MSVC) and the Visual Studio C++ build tools, then open
PowerShell in the repository root. Run all automated tests with:

```powershell
.\test_windows.bat
```

Or run each suite separately:

```powershell
cargo test --manifest-path rust-core\Cargo.toml
cargo test --manifest-path rust-browser\Cargo.toml
cargo test --manifest-path rust-gui\Cargo.toml
```

Cargo downloads dependencies the first time. The tests use fakes or local
loopback responses and temporary files; they do not require a live Scribd
connection or launch Chrome. GUI tests exercise settings and theme logic, not
actual window interactions. To manually verify the whole app, build the portable package and run it as
described above. Test with a
document you have permission to download: check the PDF's pages and images,
then try a two-item queue, stop/skip, retry, file conflicts, and reopening the
app to confirm settings and pending items are restored. The GUI and browser
backend have not yet been validated on Windows against live documents.

## Settings and diagnostics

The preview saves settings, recent downloads and unfinished queue items to
`%APPDATA%\ScribdDownloader\settings-rust.json`. The activity log is written
to `diagnostic-rust.log` in the same folder. The app offers light/dark/system
appearance, clipboard detection, dropped link files, recents, and open-when-done.
These additions have not yet been verified on Windows.

## Limitations

- The GUI is not yet feature-complete compared with the earlier desktop app;
  browser-link drag-and-drop and exact feature parity remain. The ETA is an estimate.
- The GUI and browser backend have not been compiled or tested on Windows.
- The bundled browser does not update itself; rebuild to receive security updates.
- An unbundled development build requires a compatible ChromeDriver and Chrome.
- Private, removed, restricted, or protected documents may fail. Scribd can
  change its embed page structure, which may require updates.
- Very large documents may require substantial temporary disk space.

## Project structure

- `rust-core/` — shared URL, filename, queue, browser scripts and download
  orchestration logic.
- `rust-browser/` — ChromeDriver integration and PDF printing/merging.
- `rust-gui/` — desktop interface.
- `assets/` — existing project artwork.

## Disclaimer

Use this tool only for documents you have the right to access and download.
Respect copyright, document licences and Scribd's terms of service. The project
has historically been described as MIT-licensed but does not currently include
a standalone licence file; check with the maintainers before redistributing.

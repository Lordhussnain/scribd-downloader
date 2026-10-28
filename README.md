# Scribd Downloader — Rust preview

A Windows-focused desktop app that renders accessible Scribd documents in
Chrome and exports them to PDF. The app and download engine are written in
Rust; Chrome and ChromeDriver are separate runtime requirements.

**Status:** This is an experimental rewrite, not yet verified on Windows or
against live Scribd documents. The GUI and browser backend have not been built
in the development environment. Several features of the earlier app are not yet implemented. Check exported
PDFs before relying on them.

## Requirements

- Windows 10 or 11, 64-bit.
- Rust stable with the MSVC toolchain and Visual Studio C++ build tools.
- Google Chrome.
- A ChromeDriver compatible with the installed Chrome version. Put
  `chromedriver.exe` on `PATH`, or set `CHROMEDRIVER` to its full path.
  ChromeDriver is not downloaded automatically.
- Internet access for Cargo dependencies and for viewing the document.

## Build and run

From PowerShell in the repository root:

```powershell
cargo run --release --manifest-path rust-gui\Cargo.toml
```

The resulting executable is `rust-gui\target\release\scribd-rust-gui.exe`.
Chrome and ChromeDriver must also be available on the computer where it runs.
For a debug build, omit `--release`. You can also run `build_windows.bat`.
To build an installer, install Inno Setup 6 and run `build_installer.bat`;
its output is written to `dist\installer`. Chrome and ChromeDriver are not
included in the installer.

## Using the preview

Paste Scribd links into the text box and choose **Add to queue**, or use
**Add from file** to import supported link files. Choose an output folder and
an existing-file policy, then start the queue. The app processes documents
one at a time. It provides progress, retry, skip, stop, filename editing,
batch-size and timeout controls, and a visible-Chrome troubleshooting mode.

Cancellation is cooperative and may wait for a browser operation to finish or
time out. Closing the window during a download requests cancellation; wait for
the current item to finish before closing again.

## Tests

```powershell
cargo test --manifest-path rust-core\Cargo.toml
cargo test --manifest-path rust-browser\Cargo.toml
cargo check --manifest-path rust-gui\Cargo.toml
```

The core tests use fake browser operations. The browser crate includes a PDF
merge test; real end-to-end verification needs Chrome, ChromeDriver, Windows,
and documents you have permission to access. The GUI and browser backend have
not yet been validated in that environment.

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
- ChromeDriver must be installed separately and match Chrome.
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

"""
Scribd Downloader — Windows desktop GUI
=======================================

A clean, single-window interface on top of the CLI engine in
``scribd-downloader.py``. All downloading (headless Chrome, per-page PDF
export, disk-spooled merge) is done by the engine's ``download_document()``;
this file only handles presentation, settings and threading.

Run from source (Windows, Python 3.10+ with Tk from python.org):

    pip install -r requirements.txt
    python gui_app.py

Build a standalone ``ScribdDownloader.exe``:

    build_windows.bat          (or: pyinstaller ScribdDownloader.spec)

Diagnostics without opening the window:

    python gui_app.py --check
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Imported eagerly so PyInstaller bundles the engine's runtime dependencies.
# The engine itself (scribd-downloader.py) is loaded from disk below because
# its hyphenated filename cannot be imported as a normal module.
try:
    import selenium  # noqa: F401
    import pypdf  # noqa: F401
except ImportError:  # --check reports this; the GUI shows a friendly error
    selenium = None
    pypdf = None

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

APP_NAME = "Scribd Downloader"
APP_VERSION = "1.0.0"
CHROME_DOWNLOAD_URL = "https://www.google.com/chrome/"

# --- Palette: (light, dark). Teal accent in keeping with Scribd's brand. ---
ACCENT = ("#0C7A78", "#14908C")
ACCENT_HOVER = ("#095F5E", "#107471")
WINDOW_BG = ("#F2F3F5", "#131518")
CARD_BG = ("#FFFFFF", "#1C1F24")
CARD_BORDER = ("#E3E6EA", "#272C33")
FIELD_BG = ("#F7F8FA", "#14171B")
FIELD_BORDER = ("#D5DAE0", "#2E343C")
TEXT_MUTED = ("#5F6873", "#9AA3AE")
SUCCESS = ("#178A4C", "#2FBF71")
ERROR = ("#C0392B", "#E66A5E")
WARNING = ("#B7791F", "#E3B341")
FONT_FAMILY = "Segoe UI"  # native on Windows; harmless fallback elsewhere


# ---------------------------------------------------------------------------
# Engine loading
# ---------------------------------------------------------------------------
def _engine_search_paths():
    """Candidate locations of scribd-downloader.py (source & frozen .exe)."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "scribd-downloader.py")
    candidates.append(Path(__file__).resolve().parent / "scribd-downloader.py")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "scribd-downloader.py")
    candidates.append(Path.cwd() / "scribd-downloader.py")
    return candidates


def load_engine():
    """Load the hyphenated CLI module as ``scribd_engine``."""
    import importlib.util

    for path in _engine_search_paths():
        if path.is_file():
            spec = importlib.util.spec_from_file_location("scribd_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(
        "scribd-downloader.py was not found next to the application."
    )


# ---------------------------------------------------------------------------
# Settings (persisted as JSON in %APPDATA%\\ScribdDownloader on Windows)
# ---------------------------------------------------------------------------
def config_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "ScribdDownloader"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ScribdDownloader"


def default_downloads_dir():
    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


@dataclass
class AppSettings:
    output_dir: str = field(default_factory=default_downloads_dir)
    appearance: str = "System"  # System | Light | Dark
    batch_size: int = 8
    page_load_timeout: int = 120
    cdp_timeout: int = 600
    show_browser: bool = False
    open_when_done: bool = False
    clipboard_check: bool = True
    recents: list = field(default_factory=list)  # [{path, title, ts}]

    @classmethod
    def load(cls):
        path = config_dir() / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {name for name in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in data.items() if k in known})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        try:
            folder = config_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "settings.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            tmp.replace(target)
        except OSError:
            pass  # Settings are a convenience; never crash over them.

    def add_recent(self, path, title):
        self.recents = [r for r in self.recents if r.get("path") != str(path)]
        self.recents.insert(
            0, {"path": str(path), "title": title or Path(path).stem, "ts": time.time()}
        )
        self.recents = self.recents[:5]
        self.save()


# ---------------------------------------------------------------------------
# Small OS helpers
# ---------------------------------------------------------------------------
def find_chrome():
    """Return the Chrome executable path, or None when not detected."""
    if sys.platform.startswith("win"):
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value and Path(value).is_file():
                            return value
                except OSError:
                    continue
        except ImportError:
            pass
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if candidate.is_file():
                    return str(candidate)
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def open_file(path):
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - opening our own PDF
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_folder(path):
    folder = Path(path)
    folder = folder if folder.is_dir() else folder.parent
    if sys.platform.startswith("win"):
        os.startfile(str(folder))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def human_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def unique_path(path):
    """Return ``path`` or ``name (1).pdf`` … if it already exists."""
    candidate = Path(path)
    counter = 1
    while candidate.exists():
        candidate = candidate.with_name(
            f"{Path(path).stem} ({counter}){Path(path).suffix}"
        )
        counter += 1
    return candidate


def friendly_error(error):
    """Map raw Selenium/engine errors to a short, helpful sentence."""
    text = str(error)
    low = text.lower()
    if "no printable document pages" in text:
        return (
            "No pages could be read from this document. It may be private, "
            "removed, or protected."
        )
    if any(k in low for k in ("chromedriver", "session not created", "chrome not reachable", "cannot find chrome")):
        return (
            "Chrome could not be started. Make sure Google Chrome is "
            "installed and up to date, then try again."
        )
    if "timed out" in low or "timeout" in low:
        return (
            "Chrome timed out while loading pages. Check your connection, "
            "or raise the timeouts in Settings for very large documents."
        )
    if "failed to load scribd page" in low:
        return (
            "Some pages failed to load. Check your connection and try "
            "again, or increase the page timeout in Settings."
        )
    return text or "Something went wrong during the download."


def run_check():
    """Print environment diagnostics (no window). Used after building."""
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Python      : {sys.version.split()[0]} ({sys.platform})")
    print(f"Frozen (.exe): {bool(getattr(sys, 'frozen', False))}")
    print(f"CustomTkinter: {'OK' if ctk else 'MISSING - pip install customtkinter'}")
    print(f"Selenium     : {'OK' if selenium else 'MISSING - pip install selenium'}")
    print(f"pypdf        : {'OK' if pypdf else 'MISSING - pip install pypdf'}")
    try:
        engine = load_engine()
        sample = engine.parse_scribd_url(
            "https://www.scribd.com/document/123456789/Sample-Title"
        )
        print(f"Engine       : OK ({sample['filename']} <- id {sample['document_id']})")
    except Exception as error:  # noqa: BLE001 - diagnostic output
        print(f"Engine       : FAILED - {error}")
        return 1
    chrome = find_chrome()
    print(f"Chrome       : {chrome or 'not detected (Selenium may still find it)'}")
    print(f"Settings     : {config_dir() / 'settings.json'}")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
if ctk is not None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    STAGES = ["connect", "load", "export", "merge"]
    STAGE_LABELS = {
        "connect": "Connect",
        "load": "Load pages",
        "export": "Export PDF",
        "merge": "Merge",
    }

    class ScribdDownloaderApp(ctk.CTk):
        def __init__(self, engine, settings):
            super().__init__()
            self.engine = engine
            self.settings = settings

            self.title(APP_NAME)
            self.geometry("920x760")
            self.minsize(800, 660)
            self.configure(fg_color=WINDOW_BG)
            self._set_window_icon()
            self._center_on_screen()

            # State
            self.events = queue.Queue()
            self.cancel_event = threading.Event()
            self.worker = None
            self.state = "idle"  # idle | downloading | cancelling | success | error | cancelled
            self.parsed = None
            self.filename_dirty = False
            self._dirty_doc_id = None
            self._setting_filename = False
            self.started_at = None
            self.export_started_at = None
            self.last_progress = {}
            self.output_path = None
            self.output_existed_before = False
            self._last_clipboard = None
            self._validate_job = None
            self.log_visible = False

            # Tk variables
            self.url_var = tk.StringVar()
            self.filename_var = tk.StringVar()
            self.folder_var = tk.StringVar(value=settings.output_dir)

            self._build_ui()
            self._bind_events()
            self._refresh_chrome_status()
            self._refresh_recents()
            self._validate_url()
            self._update_path_preview()

            self.after(80, self._poll_events)
            self.after(600, self._check_clipboard)
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.url_entry.focus_set()

        # ---------------------------------------------------------- setup
        def _set_window_icon(self):
            assets = Path(__file__).resolve().parent / "assets"
            meipass = getattr(sys, "_MEIPASS", None)
            roots = [Path(meipass) / "assets"] if meipass else []
            roots.append(assets)
            for root in roots:
                ico = root / "icon.ico"
                if sys.platform.startswith("win") and ico.is_file():
                    try:
                        self.iconbitmap(str(ico))
                        return
                    except tk.TclError:
                        pass
                png = root / "icon.png"
                if png.is_file():
                    try:
                        self._icon_image = tk.PhotoImage(file=str(png))
                        self.iconphoto(True, self._icon_image)
                        return
                    except tk.TclError:
                        pass

        def _center_on_screen(self):
            self.update_idletasks()
            width = self.winfo_width() if self.winfo_width() > 1 else 920
            height = self.winfo_height() if self.winfo_height() > 1 else 760
            x = max(0, (self.winfo_screenwidth() - width) // 2)
            y = max(0, (self.winfo_screenheight() - height) // 2)
            self.geometry(f"+{x}+{y}")

        def _font(self, size, weight="normal"):
            return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)

        # ---------------------------------------------------------- layout
        def _build_ui(self):
            outer = ctk.CTkFrame(self, fg_color="transparent")
            outer.pack(fill="both", expand=True, padx=28, pady=(20, 14))

            self._build_header(outer)
            self._build_url_card(outer)
            self._build_save_card(outer)
            self._build_actions(outer)
            self._build_progress_card(outer)
            self._build_log(outer)
            self._build_recents(outer)

            footer = ctk.CTkLabel(
                outer,
                text="Runs locally in headless Chrome  ·  Only download documents you have the right to access.",
                font=self._font(11),
                text_color=TEXT_MUTED,
            )
            footer.pack(side="bottom", pady=(10, 0))

        def _card(self, parent):
            card = ctk.CTkFrame(
                parent,
                fg_color=CARD_BG,
                border_color=CARD_BORDER,
                border_width=1,
                corner_radius=14,
            )
            card.pack(fill="x", pady=(0, 14))
            return card

        def _build_header(self, parent):
            header = ctk.CTkFrame(parent, fg_color="transparent")
            header.pack(fill="x", pady=(0, 16))

            logo = ctk.CTkLabel(
                header,
                text="S",
                width=46,
                height=46,
                corner_radius=12,
                fg_color=ACCENT,
                text_color="white",
                font=self._font(24, "bold"),
            )
            logo.pack(side="left")

            titles = ctk.CTkFrame(header, fg_color="transparent")
            titles.pack(side="left", padx=(12, 0))
            ctk.CTkLabel(
                titles, text=APP_NAME, font=self._font(21, "bold"), anchor="w"
            ).pack(fill="x")
            ctk.CTkLabel(
                titles,
                text="Download Scribd documents as clean PDFs",
                font=self._font(12),
                text_color=TEXT_MUTED,
                anchor="w",
            ).pack(fill="x")
            self.chrome_label = ctk.CTkLabel(
                titles, text="", font=self._font(11), anchor="w", cursor="hand2"
            )
            self.chrome_label.pack(fill="x")
            self.chrome_label.bind(
                "<Button-1>",
                lambda _e: None if find_chrome() else webbrowser.open(CHROME_DOWNLOAD_URL),
            )

            appearance = ctk.CTkOptionMenu(
                header,
                values=["System", "Light", "Dark"],
                width=104,
                height=32,
                font=self._font(12),
                fg_color=CARD_BG,
                button_color=FIELD_BORDER,
                button_hover_color=ACCENT,
                text_color=("#1A1D21", "#E8EAED"),
                command=self._on_appearance_change,
            )
            appearance.set(self.settings.appearance)
            appearance.pack(side="right")
            ctk.CTkButton(
                header,
                text="Settings",
                width=92,
                height=32,
                font=self._font(12),
                fg_color=CARD_BG,
                hover_color=FIELD_BG,
                border_color=CARD_BORDER,
                border_width=1,
                text_color=("#1A1D21", "#E8EAED"),
                command=self._open_settings,
            ).pack(side="right", padx=(0, 8))

        def _build_url_card(self, parent):
            card = self._card(parent)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)

            ctk.CTkLabel(
                inner, text="Scribd document URL", font=self._font(13, "bold"), anchor="w"
            ).pack(fill="x", pady=(0, 8))

            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x")
            self.url_entry = ctk.CTkEntry(
                row,
                textvariable=self.url_var,
                placeholder_text="https://www.scribd.com/document/123456789/Document-Title",
                height=42,
                font=self._font(13),
                fg_color=FIELD_BG,
                border_color=FIELD_BORDER,
                border_width=1,
                corner_radius=10,
            )
            self.url_entry.pack(side="left", fill="x", expand=True)
            self.paste_btn = ctk.CTkButton(
                row, text="Paste", width=78, height=42, font=self._font(12),
                fg_color="transparent", hover_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=self._paste_from_clipboard,
            )
            self.paste_btn.pack(side="left", padx=(8, 0))
            self.clear_btn = ctk.CTkButton(
                row, text="Clear", width=64, height=42, font=self._font(12),
                fg_color="transparent", hover_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=self._clear_url,
            )
            self.clear_btn.pack(side="left", padx=(8, 0))

            self.url_hint = ctk.CTkLabel(
                inner, text="", font=self._font(12), text_color=TEXT_MUTED,
                anchor="w", wraplength=820,
            )
            self.url_hint.pack(fill="x", pady=(8, 0))

            self.doc_info = ctk.CTkFrame(inner, fg_color="transparent")
            self.doc_title = ctk.CTkLabel(
                self.doc_info, text="", font=self._font(14, "bold"), anchor="w",
                wraplength=820,
            )
            self.doc_title.pack(fill="x", pady=(6, 0))
            self.doc_meta = ctk.CTkLabel(
                self.doc_info, text="", font=self._font(11),
                text_color=TEXT_MUTED, anchor="w",
            )
            self.doc_meta.pack(fill="x")

        def _build_save_card(self, parent):
            card = self._card(parent)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)

            ctk.CTkLabel(
                inner, text="Save as", font=self._font(13, "bold"), anchor="w"
            ).pack(fill="x", pady=(0, 8))

            name_row = ctk.CTkFrame(inner, fg_color="transparent")
            name_row.pack(fill="x")
            self.filename_entry = ctk.CTkEntry(
                name_row, textvariable=self.filename_var,
                placeholder_text="File name", height=38, font=self._font(13),
                fg_color=FIELD_BG, border_color=FIELD_BORDER, border_width=1,
                corner_radius=10,
            )
            self.filename_entry.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                name_row, text=".pdf", font=self._font(13), text_color=TEXT_MUTED
            ).pack(side="left", padx=(6, 0))

            folder_row = ctk.CTkFrame(inner, fg_color="transparent")
            folder_row.pack(fill="x", pady=(10, 0))
            self.folder_entry = ctk.CTkEntry(
                folder_row, textvariable=self.folder_var, height=38,
                font=self._font(12), fg_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1, corner_radius=10,
            )
            self.folder_entry.pack(side="left", fill="x", expand=True)
            self.browse_btn = ctk.CTkButton(
                folder_row, text="Browse…", width=92, height=38,
                font=self._font(12), fg_color="transparent",
                hover_color=FIELD_BG, border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=self._browse_folder,
            )
            self.browse_btn.pack(side="left", padx=(8, 0))

            self.path_preview = ctk.CTkLabel(
                inner, text="", font=self._font(11), text_color=TEXT_MUTED,
                anchor="w", wraplength=820,
            )
            self.path_preview.pack(fill="x", pady=(8, 0))

        def _build_actions(self, parent):
            self.action_frame = ctk.CTkFrame(parent, fg_color="transparent")
            self.action_frame.pack(fill="x", pady=(2, 14))

            self.download_btn = ctk.CTkButton(
                self.action_frame, text="Download PDF", height=48,
                font=self._font(15, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                corner_radius=12, command=self._start_download,
            )
            self.download_btn.pack(fill="x")

            self.cancel_btn = ctk.CTkButton(
                self.action_frame, text="Cancel download", height=48,
                font=self._font(14, "bold"), fg_color="transparent",
                hover_color=FIELD_BG, border_color=ERROR, border_width=1,
                text_color=ERROR, corner_radius=12, command=self._cancel_download,
            )

            # Success / error panels (shown after a run finishes)
            self.result_panel = ctk.CTkFrame(
                parent, fg_color=CARD_BG, border_color=CARD_BORDER,
                border_width=1, corner_radius=14,
            )
            result_inner = ctk.CTkFrame(self.result_panel, fg_color="transparent")
            result_inner.pack(fill="x", padx=18, pady=14)
            self.result_title = ctk.CTkLabel(
                result_inner, text="", font=self._font(14, "bold"),
                anchor="w", wraplength=820,
            )
            self.result_title.pack(fill="x")
            self.result_detail = ctk.CTkLabel(
                result_inner, text="", font=self._font(12),
                text_color=TEXT_MUTED, anchor="w", wraplength=820,
            )
            self.result_detail.pack(fill="x", pady=(2, 10))
            btn_row = ctk.CTkFrame(result_inner, fg_color="transparent")
            btn_row.pack(fill="x")
            self.open_pdf_btn = ctk.CTkButton(
                btn_row, text="Open PDF", width=120, height=36,
                font=self._font(12, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                command=lambda: self.output_path and open_file(self.output_path),
            )
            self.open_pdf_btn.pack(side="left")
            self.open_folder_btn = ctk.CTkButton(
                btn_row, text="Open folder", width=120, height=36,
                font=self._font(12), fg_color="transparent",
                hover_color=FIELD_BG, border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"),
                command=lambda: self.output_path and open_folder(self.output_path),
            )
            self.open_folder_btn.pack(side="left", padx=(8, 0))
            self.retry_btn = ctk.CTkButton(
                btn_row, text="Try again", width=120, height=36,
                font=self._font(12, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                command=self._start_download,
            )
            self.new_download_btn = ctk.CTkButton(
                btn_row, text="New download", width=130, height=36,
                font=self._font(12), fg_color="transparent",
                hover_color=FIELD_BG, border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=self._reset_form,
            )
            self.new_download_btn.pack(side="left", padx=(8, 0))

        def _build_progress_card(self, parent):
            self.progress_card = ctk.CTkFrame(
                parent, fg_color=CARD_BG, border_color=CARD_BORDER,
                border_width=1, corner_radius=14,
            )
            inner = ctk.CTkFrame(self.progress_card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)

            steps_row = ctk.CTkFrame(inner, fg_color="transparent")
            steps_row.pack(fill="x", pady=(0, 12))
            self.step_widgets = {}
            for index, stage in enumerate(STAGES):
                steps_row.grid_columnconfigure(index, weight=1)
                cell = ctk.CTkFrame(steps_row, fg_color="transparent")
                cell.grid(row=0, column=index, sticky="ew")
                dot = ctk.CTkLabel(
                    cell, text=str(index + 1), width=24, height=24,
                    corner_radius=12, fg_color=FIELD_BORDER,
                    text_color="white", font=self._font(11, "bold"),
                )
                dot.pack(side="left")
                label = ctk.CTkLabel(
                    cell, text=STAGE_LABELS[stage], font=self._font(12),
                    text_color=TEXT_MUTED,
                )
                label.pack(side="left", padx=(7, 0))
                self.step_widgets[stage] = (dot, label)

            self.progress_bar = ctk.CTkProgressBar(
                inner, height=10, corner_radius=5, fg_color=FIELD_BG,
                progress_color=ACCENT,
            )
            self.progress_bar.pack(fill="x")
            self.progress_bar.set(0)

            status_row = ctk.CTkFrame(inner, fg_color="transparent")
            status_row.pack(fill="x", pady=(9, 0))
            self.status_label = ctk.CTkLabel(
                status_row, text="", font=self._font(12), anchor="w"
            )
            self.status_label.pack(side="left")
            self.timing_label = ctk.CTkLabel(
                status_row, text="", font=self._font(12),
                text_color=TEXT_MUTED, anchor="e",
            )
            self.timing_label.pack(side="right")

        def _build_log(self, parent):
            header = ctk.CTkFrame(parent, fg_color="transparent")
            header.pack(fill="x", pady=(0, 6))
            self.log_toggle = ctk.CTkButton(
                header, text="▸  Activity log", width=140, height=28,
                font=self._font(12), fg_color="transparent",
                hover_color=FIELD_BG, text_color=TEXT_MUTED, anchor="w",
                command=self._toggle_log,
            )
            self.log_toggle.pack(side="left")
            self.copy_log_btn = ctk.CTkButton(
                header, text="Copy log", width=84, height=28,
                font=self._font(11), fg_color="transparent",
                hover_color=FIELD_BG, text_color=TEXT_MUTED,
                command=self._copy_log,
            )
            self.clear_log_btn = ctk.CTkButton(
                header, text="Clear", width=60, height=28,
                font=self._font(11), fg_color="transparent",
                hover_color=FIELD_BG, text_color=TEXT_MUTED,
                command=self._clear_log,
            )

            self.log_box = ctk.CTkTextbox(
                parent, height=120, font=ctk.CTkFont(family="Consolas", size=11),
                fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1,
                corner_radius=10, state="disabled",
            )

        def _build_recents(self, parent):
            self.recents_frame = ctk.CTkFrame(parent, fg_color="transparent")
            self.recents_frame.pack(fill="x", pady=(10, 0))

        # ---------------------------------------------------------- events
        def _bind_events(self):
            self.url_var.trace_add("write", lambda *_: self._schedule_validate())
            self.filename_var.trace_add("write", lambda *_: self._on_filename_change())
            self.folder_var.trace_add("write", lambda *_: self._update_path_preview())
            self.url_entry.bind("<Return>", lambda _e: self._start_download())
            self.filename_entry.bind("<Return>", lambda _e: self._start_download())
            self.bind("<Escape>", lambda _e: self._cancel_download())
            self.bind("<FocusIn>", lambda _e: self.after(150, self._check_clipboard))

        def _schedule_validate(self):
            if self._validate_job:
                self.after_cancel(self._validate_job)
            self._validate_job = self.after(220, self._validate_url)

        def _on_filename_change(self):
            if not self._setting_filename:
                self.filename_dirty = True
                self._dirty_doc_id = (self.parsed or {}).get("document_id")
            self._update_path_preview()

        # ---------------------------------------------------------- header
        def _refresh_chrome_status(self):
            if find_chrome():
                self.chrome_label.configure(
                    text="●  Chrome ready", text_color=SUCCESS
                )
            else:
                self.chrome_label.configure(
                    text="●  Chrome not found — click to install Google Chrome",
                    text_color=WARNING,
                )

        def _on_appearance_change(self, value):
            self.settings.appearance = value
            self.settings.save()
            ctk.set_appearance_mode(value)

        # ---------------------------------------------------------- URL UX
        def _validate_url(self):
            self._validate_job = None
            raw = self.url_var.get().strip()
            self.parsed = self.engine.parse_scribd_url(raw) if raw else None

            if not raw:
                self.url_hint.configure(
                    text="Paste a link like  scribd.com/document/123456789/Document-Title",
                    text_color=TEXT_MUTED,
                )
                self.url_entry.configure(border_color=FIELD_BORDER)
                self.doc_info.pack_forget()
            elif not self.parsed:
                self.url_hint.configure(
                    text="That doesn't look like a Scribd document URL yet.",
                    text_color=ERROR,
                )
                self.url_entry.configure(border_color=ERROR)
                self.doc_info.pack_forget()
            else:
                self.url_hint.configure(text="✓  Valid Scribd link", text_color=SUCCESS)
                self.url_entry.configure(border_color=SUCCESS)
                title = self.parsed["title"] or f"Scribd document {self.parsed['document_id']}"
                self.doc_title.configure(text=title)
                self.doc_meta.configure(
                    text=f"Document ID {self.parsed['document_id']}  ·  Exported as a clean, toolbar-free PDF"
                )
                self.doc_info.pack(fill="x")
                if not self.filename_dirty or self._dirty_doc_id != self.parsed["document_id"]:
                    self._setting_filename = True
                    self.filename_var.set(Path(self.parsed["filename"]).stem)
                    self._setting_filename = False

            self._refresh_download_button()
            self._update_path_preview()

        def _refresh_download_button(self):
            ready = bool(self.parsed) and self.state in ("idle", "success", "error", "cancelled")
            self.download_btn.configure(state="normal" if ready else "disabled")

        def _paste_from_clipboard(self):
            try:
                text = self.clipboard_get().strip()
            except tk.TclError:
                text = ""
            if text:
                self.url_var.set(text)
                self.filename_dirty = False
                self._validate_url()
                self.url_entry.focus_set()
            else:
                self.url_hint.configure(
                    text="Clipboard is empty — copy a Scribd link first.",
                    text_color=WARNING,
                )

        def _check_clipboard(self):
            """Offer a Scribd link that is already on the clipboard."""
            if not self.settings.clipboard_check or self.state in ("downloading", "cancelling"):
                return
            try:
                text = self.clipboard_get().strip()
            except tk.TclError:
                return
            if not text or text == self._last_clipboard:
                return
            self._last_clipboard = text
            if not self.url_var.get().strip() and self.engine.parse_scribd_url(text):
                self.url_var.set(text)
                self.filename_dirty = False
                self._validate_url()
                self.url_hint.configure(
                    text="✓  Scribd link taken from your clipboard — press Download when ready.",
                    text_color=SUCCESS,
                )

        def _clear_url(self):
            self.url_var.set("")
            self.filename_dirty = False
            self._validate_url()
            self.url_entry.focus_set()

        # ---------------------------------------------------------- saving
        def _browse_folder(self):
            chosen = filedialog.askdirectory(
                title="Choose where to save PDFs",
                initialdir=self.folder_var.get() or default_downloads_dir(),
            )
            if chosen:
                self.folder_var.set(chosen)
                self.settings.output_dir = chosen
                self.settings.save()

        def _resolved_output_path(self):
            name = self.engine.sanitize_filename(self.filename_var.get().strip())
            folder = Path(self.folder_var.get().strip() or default_downloads_dir())
            return folder / f"{name}.pdf"

        def _update_path_preview(self):
            if not hasattr(self, "path_preview"):
                return
            self.path_preview.configure(text=f"Will save to:  {self._resolved_output_path()}")

        # ---------------------------------------------------------- download
        def _start_download(self):
            if self.state in ("downloading", "cancelling"):
                return
            self._validate_url()
            if not self.parsed:
                self.url_entry.focus_set()
                return

            output = self._resolved_output_path()
            if output.exists():
                choice = self._ask_overwrite(output)
                if choice == "cancel":
                    return
                if choice == "copy":
                    output = unique_path(output)

            self.output_path = output
            self.output_existed_before = output.exists()
            self.settings.output_dir = str(output.parent)
            self.settings.save()

            self.cancel_event = threading.Event()
            self.started_at = time.monotonic()
            self.export_started_at = None
            self.last_progress = {}
            self._set_state("downloading")
            self._append_log(f"\n--- {time.strftime('%H:%M:%S')}  {output.name} ---")
            self._apply_progress({"stage": "connect", "message": "Starting Chrome..."})

            url = self.url_var.get().strip()
            settings = self.settings
            cancel_event = self.cancel_event

            def work():
                try:
                    saved = self.engine.download_document(
                        url,
                        str(output),
                        on_log=lambda line: self.events.put(("log", line)),
                        on_progress=lambda info: self.events.put(("progress", info)),
                        cancel_event=cancel_event,
                        headless=not settings.show_browser,
                        batch_size=settings.batch_size,
                        page_load_timeout=settings.page_load_timeout,
                        cdp_timeout=settings.cdp_timeout,
                    )
                    self.events.put(("success", saved))
                except self.engine.DownloadCancelledError:
                    self.events.put(("cancelled", None))
                except Exception as error:  # noqa: BLE001 - shown to the user
                    self.events.put(("error", error))

            self.worker = threading.Thread(target=work, daemon=True, name="scribd-download")
            self.worker.start()
            self._tick_timing()

        def _cancel_download(self):
            if self.state != "downloading":
                return
            self.cancel_event.set()
            self.state = "cancelling"
            self.cancel_btn.configure(state="disabled", text="Cancelling…")
            self.status_label.configure(text="Cancelling — finishing the current page…")

        def _ask_overwrite(self, path):
            """Modal choice: Overwrite / Save as copy / Cancel. Returns str."""
            dialog = ctk.CTkToplevel(self)
            dialog.title("File already exists")
            dialog.geometry("460x190")
            dialog.resizable(False, False)
            dialog.configure(fg_color=WINDOW_BG)
            dialog.transient(self)
            result = {"choice": "cancel"}

            ctk.CTkLabel(
                dialog, text="A file with this name already exists:",
                font=self._font(13, "bold"), anchor="w",
            ).pack(fill="x", padx=20, pady=(18, 4))
            ctk.CTkLabel(
                dialog, text=str(path), font=self._font(12),
                text_color=TEXT_MUTED, anchor="w", wraplength=420,
            ).pack(fill="x", padx=20)

            row = ctk.CTkFrame(dialog, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=(16, 0))

            def choose(value):
                result["choice"] = value
                dialog.destroy()

            ctk.CTkButton(
                row, text="Overwrite", width=110, height=36, font=self._font(12, "bold"),
                fg_color=ERROR, hover_color=ERROR, text_color="white",
                command=lambda: choose("overwrite"),
            ).pack(side="left")
            ctk.CTkButton(
                row, text="Save as copy", width=120, height=36, font=self._font(12),
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                command=lambda: choose("copy"),
            ).pack(side="left", padx=(8, 0))
            ctk.CTkButton(
                row, text="Cancel", width=90, height=36, font=self._font(12),
                fg_color="transparent", hover_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=lambda: choose("cancel"),
            ).pack(side="left", padx=(8, 0))

            dialog.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - 460) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 190) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
            dialog.grab_set()
            self.wait_window(dialog)
            return result["choice"]

        # ---------------------------------------------------------- states
        def _set_input_enabled(self, enabled):
            state = "normal" if enabled else "disabled"
            for widget in (
                self.url_entry, self.paste_btn, self.clear_btn,
                self.filename_entry, self.folder_entry, self.browse_btn,
            ):
                widget.configure(state=state)

        def _set_state(self, state, error=None):
            self.state = state
            if state == "downloading":
                self._set_input_enabled(False)
                self.download_btn.pack_forget()
                self.cancel_btn.configure(state="normal", text="Cancel download")
                self.cancel_btn.pack(fill="x")
                self.result_panel.pack_forget()
                self.progress_card.pack(fill="x", pady=(0, 14), after=self.action_frame)
                self.progress_bar.set(0)
                self._reset_steps()
            elif state in ("success", "error", "cancelled", "idle"):
                self._set_input_enabled(True)
                self.cancel_btn.pack_forget()
                self.download_btn.pack(fill="x")
                self.progress_bar.stop()
                if state == "idle":
                    self.progress_card.pack_forget()
                    self.result_panel.pack_forget()
                else:
                    self._show_result(state, error)
            self._refresh_download_button()

        def _show_result(self, state, error=None):
            # Swap the Download button for contextual actions.
            self.download_btn.pack_forget()
            self.open_pdf_btn.pack_forget()
            self.open_folder_btn.pack_forget()
            self.retry_btn.pack_forget()

            if state == "success":
                size = ""
                try:
                    size = f"  ·  {human_size(Path(self.output_path).stat().st_size)}"
                except OSError:
                    pass
                elapsed = format_duration(time.monotonic() - (self.started_at or time.monotonic()))
                self.result_title.configure(
                    text=f"✓  Saved in {elapsed}{size}", text_color=SUCCESS
                )
                self.result_detail.configure(text=str(self.output_path))
                self.open_pdf_btn.pack(side="left", before=self.new_download_btn)
                self.open_folder_btn.pack(side="left", padx=(8, 0), before=self.new_download_btn)
                self.progress_bar.configure(mode="determinate")
                self.progress_bar.set(1)
                self._mark_stage("done")
                self.status_label.configure(text="Done — PDF saved successfully.")
            elif state == "error":
                self.result_title.configure(text="Download failed", text_color=ERROR)
                self.result_detail.configure(text=friendly_error(error))
                self.retry_btn.configure(text="Try again")
                self.retry_btn.pack(side="left", before=self.new_download_btn)
                self.status_label.configure(text="Failed — see the activity log for details.")
                if not self.log_visible:
                    self._toggle_log()
            elif state == "cancelled":
                self.result_title.configure(text="Download cancelled", text_color=WARNING)
                self.result_detail.configure(
                    text="Nothing was saved. You can adjust the link or try again."
                )
                self.retry_btn.configure(text="Download again")
                self.retry_btn.pack(side="left", before=self.new_download_btn)
                self.status_label.configure(text="Cancelled.")

            self.result_panel.pack(fill="x", pady=(0, 14), after=self.action_frame)
            # Keep the progress card directly under the result panel.
            self.progress_card.pack_forget()
            self.progress_card.pack(fill="x", pady=(0, 14), after=self.result_panel)

        def _reset_form(self):
            self.url_var.set("")
            self.filename_dirty = False
            self.retry_btn.configure(text="Try again")
            self._set_state("idle")
            self._validate_url()
            self.url_entry.focus_set()

        # ---------------------------------------------------------- progress
        def _reset_steps(self):
            for stage, (dot, label) in self.step_widgets.items():
                dot.configure(text=str(STAGES.index(stage) + 1), fg_color=FIELD_BORDER)
                label.configure(text_color=TEXT_MUTED, font=self._font(12))

        def _mark_stage(self, stage):
            if stage == "done":
                active_index = len(STAGES)
            else:
                active_index = STAGES.index(stage)
            for index, name in enumerate(STAGES):
                dot, label = self.step_widgets[name]
                if index < active_index:
                    dot.configure(text="✓", fg_color=SUCCESS)
                    label.configure(text_color=TEXT_MUTED, font=self._font(12))
                elif index == active_index:
                    dot.configure(text=str(index + 1), fg_color=ACCENT)
                    label.configure(
                        text_color=("#1A1D21", "#E8EAED"), font=self._font(12, "bold")
                    )
                else:
                    dot.configure(text=str(index + 1), fg_color=FIELD_BORDER)
                    label.configure(text_color=TEXT_MUTED, font=self._font(12))

        def _apply_progress(self, info):
            self.last_progress = info
            stage = info.get("stage") or "export"
            current, total = info.get("current"), info.get("total")
            message = info.get("message") or ""

            if stage in STAGES:
                self._mark_stage(stage)
            if stage == "export" and self.export_started_at is None:
                self.export_started_at = time.monotonic()

            if stage in ("connect", "merge") or not total:
                self.progress_bar.configure(mode="indeterminate")
                self.progress_bar.start()
            else:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
                self.progress_bar.set(current / total if total else 0)

            if stage == "export" and current is not None and total:
                percent = int(round(100 * current / total))
                self.status_label.configure(
                    text=f"Exporting page {current} of {total}  ·  {percent}%"
                )
            elif message:
                self.status_label.configure(text=message)

        def _tick_timing(self):
            if self.state not in ("downloading", "cancelling"):
                return
            elapsed = time.monotonic() - (self.started_at or time.monotonic())
            text = f"Elapsed {format_duration(elapsed)}"
            info = self.last_progress
            current, total = info.get("current"), info.get("total")
            if (
                info.get("stage") == "export"
                and current
                and total
                and current < total
                and self.export_started_at
            ):
                per_page = (time.monotonic() - self.export_started_at) / current
                text += f"  ·  about {format_duration(per_page * (total - current))} left"
            self.timing_label.configure(text=text)
            self.after(500, self._tick_timing)

        # ---------------------------------------------------------- worker events
        def _poll_events(self):
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "log":
                        self._append_log(payload)
                    elif kind == "progress":
                        if self.state in ("downloading", "cancelling"):
                            self._apply_progress(payload)
                    elif kind == "success":
                        self.output_path = Path(payload)
                        title = (self.parsed or {}).get("title") or self.output_path.stem
                        self.settings.add_recent(payload, title)
                        self._refresh_recents()
                        self._set_state("success")
                        if self.settings.open_when_done:
                            open_file(self.output_path)
                    elif kind == "cancelled":
                        self._cleanup_partial()
                        self._set_state("cancelled")
                    elif kind == "error":
                        self._append_log(f"ERROR: {payload}")
                        self._cleanup_partial()
                        self._set_state("error", error=payload)
            except queue.Empty:
                pass
            self.after(80, self._poll_events)

        def _cleanup_partial(self):
            """Remove an incomplete file created by this run (merge writes last)."""
            try:
                if (
                    self.output_path
                    and not self.output_existed_before
                    and Path(self.output_path).is_file()
                ):
                    Path(self.output_path).unlink()
            except OSError:
                pass

        # ---------------------------------------------------------- log
        def _append_log(self, line):
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"{line}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        def _toggle_log(self):
            self.log_visible = not self.log_visible
            self.log_toggle.configure(
                text="▾  Activity log" if self.log_visible else "▸  Activity log"
            )
            if self.log_visible:
                self.copy_log_btn.pack(side="right")
                self.clear_log_btn.pack(side="right")
                self.log_box.pack(fill="x", pady=(0, 6))
                # Keep recents below the log.
                self.recents_frame.pack_forget()
                self.recents_frame.pack(fill="x", pady=(10, 0))
            else:
                self.log_box.pack_forget()
                self.copy_log_btn.pack_forget()
                self.clear_log_btn.pack_forget()

        def _copy_log(self):
            text = self.log_box.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.copy_log_btn.configure(text="Copied ✓")
                self.after(1400, lambda: self.copy_log_btn.configure(text="Copy log"))

        def _clear_log(self):
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")

        # ---------------------------------------------------------- recents
        def _refresh_recents(self):
            for child in self.recents_frame.winfo_children():
                child.destroy()
            existing = [r for r in self.settings.recents if Path(r["path"]).is_file()]
            if not existing:
                return
            ctk.CTkLabel(
                self.recents_frame, text="Recent downloads",
                font=self._font(12, "bold"), text_color=TEXT_MUTED, anchor="w",
            ).pack(fill="x", pady=(0, 4))
            for item in existing[:4]:
                path = Path(item["path"])
                row = ctk.CTkFrame(self.recents_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                date_text = time.strftime("%b %d", time.localtime(item.get("ts", 0)))
                name_btn = ctk.CTkButton(
                    row, text=f"{path.name}", font=self._font(12),
                    fg_color="transparent", hover_color=FIELD_BG,
                    text_color=("#1A1D21", "#E8EAED"), anchor="w",
                    command=lambda p=path: open_file(p),
                )
                name_btn.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    row, text=date_text, font=self._font(11), text_color=TEXT_MUTED
                ).pack(side="left", padx=(0, 6))
                ctk.CTkButton(
                    row, text="Folder", width=58, height=24, font=self._font(11),
                    fg_color="transparent", hover_color=FIELD_BG,
                    text_color=TEXT_MUTED, command=lambda p=path: open_folder(p),
                ).pack(side="left")

        # ---------------------------------------------------------- settings dialog
        def _open_settings(self):
            dialog = ctk.CTkToplevel(self)
            dialog.title("Settings")
            dialog.geometry("500x560")
            dialog.resizable(False, False)
            dialog.configure(fg_color=WINDOW_BG)
            dialog.transient(self)

            body = ctk.CTkFrame(dialog, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=22, pady=20)

            def heading(text):
                ctk.CTkLabel(
                    body, text=text, font=self._font(13, "bold"), anchor="w"
                ).pack(fill="x", pady=(14, 6))

            def caption(text):
                ctk.CTkLabel(
                    body, text=text, font=self._font(11), text_color=TEXT_MUTED,
                    anchor="w", wraplength=450,
                ).pack(fill="x", pady=(4, 0))

            ctk.CTkLabel(
                body, text="Settings", font=self._font(18, "bold"), anchor="w"
            ).pack(fill="x")

            heading("Pages loaded at once")
            batch = ctk.CTkSegmentedButton(
                body, values=["4", "8", "16"], font=self._font(12),
                selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            )
            batch.set(str(self.settings.batch_size) if str(self.settings.batch_size) in ("4", "8", "16") else "8")
            batch.pack(fill="x")
            caption("Lower uses less memory on image-heavy documents. 8 is a good default.")

            heading("Page load timeout (seconds)")
            page_timeout = ctk.CTkEntry(
                body, height=36, font=self._font(12), fg_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
            )
            page_timeout.insert(0, str(self.settings.page_load_timeout))
            page_timeout.pack(fill="x")

            heading("Export timeout (seconds)")
            export_timeout = ctk.CTkEntry(
                body, height=36, font=self._font(12), fg_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
            )
            export_timeout.insert(0, str(self.settings.cdp_timeout))
            export_timeout.pack(fill="x")
            caption("Raise these only for very large or slow-loading documents.")

            heading("Behaviour")
            show_browser = ctk.CTkSwitch(
                body, text="Show Chrome window while downloading (for troubleshooting)",
                font=self._font(12), progress_color=ACCENT,
            )
            show_browser.pack(fill="x", pady=4)
            if self.settings.show_browser:
                show_browser.select()
            open_done = ctk.CTkSwitch(
                body, text="Open the PDF when the download finishes",
                font=self._font(12), progress_color=ACCENT,
            )
            open_done.pack(fill="x", pady=4)
            if self.settings.open_when_done:
                open_done.select()
            clipboard = ctk.CTkSwitch(
                body, text="Detect Scribd links on the clipboard",
                font=self._font(12), progress_color=ACCENT,
            )
            clipboard.pack(fill="x", pady=4)
            if self.settings.clipboard_check:
                clipboard.select()

            error_label = ctk.CTkLabel(
                body, text="", font=self._font(11), text_color=ERROR, anchor="w"
            )
            error_label.pack(fill="x", pady=(8, 0))

            def save():
                try:
                    page_value = int(page_timeout.get())
                    export_value = int(export_timeout.get())
                    if not (10 <= page_value <= 600 and 30 <= export_value <= 3600):
                        raise ValueError
                except ValueError:
                    error_label.configure(
                        text="Timeouts must be whole numbers (page: 10–600, export: 30–3600)."
                    )
                    return
                self.settings.batch_size = int(batch.get())
                self.settings.page_load_timeout = page_value
                self.settings.cdp_timeout = export_value
                self.settings.show_browser = bool(show_browser.get())
                self.settings.open_when_done = bool(open_done.get())
                self.settings.clipboard_check = bool(clipboard.get())
                self.settings.save()
                dialog.destroy()

            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(14, 0))
            ctk.CTkButton(
                row, text="Save", width=110, height=38, font=self._font(12, "bold"),
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
                command=save,
            ).pack(side="right")
            ctk.CTkButton(
                row, text="Cancel", width=100, height=38, font=self._font(12),
                fg_color="transparent", hover_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1,
                text_color=("#1A1D21", "#E8EAED"), command=dialog.destroy,
            ).pack(side="right", padx=(0, 8))

            dialog.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - 500) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 560) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
            dialog.grab_set()
            dialog.focus_set()

        # ---------------------------------------------------------- close
        def _on_close(self):
            if self.state in ("downloading", "cancelling"):
                if not messagebox.askyesno(
                    APP_NAME,
                    "A download is in progress. Quit anyway?\n\n"
                    "The partial download will be discarded.",
                    parent=self,
                ):
                    return
                self.cancel_event.set()
            self.settings.output_dir = self.folder_var.get() or self.settings.output_dir
            self.settings.save()
            self.destroy()


def _enable_windows_dpi_awareness():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        return run_check()
    if ctk is None:
        print(
            "CustomTkinter is not installed.\n"
            "Install dependencies with:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    if selenium is None or pypdf is None:
        print(
            "Selenium/pypdf are not installed.\n"
            "Install dependencies with:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    try:
        engine = load_engine()
    except Exception as error:  # noqa: BLE001
        print(f"Failed to load the download engine: {error}", file=sys.stderr)
        return 1

    _enable_windows_dpi_awareness()
    settings = AppSettings.load()
    ctk.set_appearance_mode(settings.appearance)
    ctk.set_default_color_theme("blue")

    app = ScribdDownloaderApp(engine, settings)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

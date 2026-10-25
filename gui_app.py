"""
Scribd Downloader — Windows desktop GUI
=======================================

A clean, single-window interface on top of the CLI engine in
``scribd-downloader.py``, with a multi-document download queue and
drag-&-drop. All downloading (headless Chrome, per-page PDF export,
disk-spooled merge) is done by the engine's ``download_document()``;
this file only handles presentation, the queue, settings and threading.

Documents are downloaded **one at a time** from the queue. The engine keeps
browser memory bounded per document, so running several Chrome exports in
parallel would defeat that; a sequential queue of unlimited length is the
reliable way to fetch many documents at once.

Run from source (Windows, Python 3.10+ with Tk from python.org):

    pip install -r requirements.txt
    python gui_app.py

Build a standalone ``ScribdDownloader.exe`` / installer:

    build_windows.bat          (portable .exe)
    build_installer.bat        (Inno Setup installer, needs Inno Setup 6)

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

# Drag-&-drop is an enhancement, never a requirement: if tkinterdnd2 (or its
# native tkdnd library) is unavailable, the app runs identically without it.
try:
    from tkinterdnd2 import COPY as _DND_COPY
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

    _DND_AVAILABLE = True
except Exception:  # ImportError or a broken native build
    TkinterDnD = None
    DND_FILES = DND_TEXT = _DND_COPY = None
    _DND_AVAILABLE = False

APP_NAME = "Scribd Downloader"
APP_VERSION = "1.1.0"
CHROME_DOWNLOAD_URL = "https://www.google.com/chrome/"
LINK_FILE_EXTENSIONS = {".txt", ".text", ".md", ".csv", ".url", ".html", ".htm"}
MAX_LINK_FILE_BYTES = 2 * 1024 * 1024

# --- Palette: (light, dark). Teal accent in keeping with Scribd's brand. ---
ACCENT = ("#0C7A78", "#14908C")
ACCENT_HOVER = ("#095F5E", "#107471")
WINDOW_BG = ("#F2F3F5", "#131518")
CARD_BG = ("#FFFFFF", "#1C1F24")
CARD_BORDER = ("#E3E6EA", "#272C33")
FIELD_BG = ("#F7F8FA", "#14171B")
FIELD_BORDER = ("#D5DAE0", "#2E343C")
TEXT_MAIN = ("#1A1D21", "#E8EAED")
TEXT_MUTED = ("#5F6873", "#9AA3AE")
SUCCESS = ("#178A4C", "#2FBF71")
ERROR = ("#C0392B", "#E66A5E")
WARNING = ("#B7791F", "#E3B341")
FONT_FAMILY = "Segoe UI"  # native on Windows; harmless fallback elsewhere

CONFLICT_POLICIES = {  # user-facing label -> internal value
    "Save as copy": "copy",
    "Skip existing": "skip",
    "Overwrite": "overwrite",
}


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
    conflict_policy: str = "copy"  # copy | skip | overwrite (existing files)
    recents: list = field(default_factory=list)  # [{path, title, ts}]
    pending_queue: list = field(default_factory=list)  # [{source_url, filename}]

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
# Queue model + drop/paste parsing (pure logic — unit-tested, no Tk needed)
# ---------------------------------------------------------------------------
@dataclass
class QueueItem:
    """One queued document. Widget updates are derived from ``status``."""

    source_url: str
    document_id: str
    title: str
    filename: str  # e.g. "My-Document.pdf" (editable stem, extension kept)
    status: str = "pending"  # pending|downloading|done|failed|cancelled|skipped
    output_path: object = None  # Path once known
    percent: int = 0
    detail: str = ""  # friendly error / note shown under the title

    @classmethod
    def from_info(cls, info):
        return cls(
            source_url=info["source_url"],
            document_id=info["document_id"],
            title=info["title"] or f"Scribd document {info['document_id']}",
            filename=info["filename"],
        )


def dedupe_infos(infos, known_ids=None):
    """Drop duplicate document ids, keeping first-seen order."""
    seen = set(known_ids or ())
    unique = []
    for info in infos:
        if info["document_id"] in seen:
            continue
        seen.add(info["document_id"])
        unique.append(info)
    return unique


def split_drop_files(data):
    """Split a tkdnd file-list string into paths.

    Paths containing spaces are brace-quoted by tkdnd, e.g.
    ``{C:/My Docs/links.txt} C:/plain.txt``. This is a small brace-aware
    splitter used as a fallback when Tk's own ``splitlist`` is unavailable
    (and in unit tests).
    """
    parts, current, depth = [], [], 0
    for char in str(data or ""):
        if char == "{":
            depth += 1
            if depth > 1:
                current.append(char)
        elif char == "}":
            depth = max(0, depth - 1)
            if depth > 0:
                current.append(char)
        elif char.isspace() and depth == 0:
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def read_urls_from_files(engine, paths):
    """Extract Scribd links from dropped/imported link files (.txt/.url/…)."""
    infos = []
    for raw_path in paths:
        path = Path(str(raw_path).strip().strip("{}"))
        try:
            if not path.is_file() or path.suffix.lower() not in LINK_FILE_EXTENSIONS:
                continue
            if path.stat().st_size > MAX_LINK_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        infos.extend(engine.extract_scribd_urls(text))
    return dedupe_infos(infos)


def parse_drop_data(engine, data, splitlist=None):
    """Turn raw drag-&-drop data into deduplicated Scribd link infos.

    Handles links dragged from a browser (plain text, sometimes with the
    page title attached), files dragged from Explorer (``splitlist`` is
    Tk's own splitter when available), and mixtures of both.
    """
    data = str(data or "")
    infos = list(engine.extract_scribd_urls(data))
    if splitlist is not None:
        try:
            parts = [str(part) for part in splitlist(data)]
        except Exception:
            parts = split_drop_files(data)
    else:
        parts = split_drop_files(data)
    files = [part for part in parts if Path(part).is_file()]
    if files:
        infos.extend(read_urls_from_files(engine, files))
    return dedupe_infos(infos)


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
    if "no printable document pages" in low:
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
    print(f"Drag & drop  : {'OK (tkinterdnd2)' if _DND_AVAILABLE else 'unavailable (app works without it)'}")
    print(f"Selenium     : {'OK' if selenium else 'MISSING - pip install selenium'}")
    print(f"pypdf        : {'OK' if pypdf else 'MISSING - pip install pypdf'}")
    try:
        engine = load_engine()
        sample = engine.parse_scribd_url(
            "https://www.scribd.com/document/123456789/Sample-Title"
        )
        found = engine.extract_scribd_urls(
            "a https://www.scribd.com/document/1/One "
            "b https://www.scribd.com/doc/2/Two"
        )
        print(f"Engine       : OK ({sample['filename']} <- id {sample['document_id']}; "
              f"multi-link extract found {len(found)})")
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
    STATUS_COLORS = {
        "pending": TEXT_MUTED,
        "downloading": ACCENT,
        "done": SUCCESS,
        "failed": ERROR,
        "cancelled": WARNING,
        "skipped": WARNING,
    }
    FINISHED_STATUSES = ("done", "failed", "cancelled", "skipped")

    # Root class with optional drag-&-drop support mixed in.
    if _DND_AVAILABLE:
        class _AppBase(ctk.CTk, TkinterDnD.DnDWrapper):
            pass
    else:
        _AppBase = ctk.CTk

    class ScribdDownloaderApp(_AppBase):
        def __init__(self, engine, settings):
            super().__init__()
            self.engine = engine
            self.settings = settings

            self.title(APP_NAME)
            self.geometry("940x860")
            self.minsize(820, 720)
            self.configure(fg_color=WINDOW_BG)
            self._set_window_icon()
            self._center_on_screen()

            # State
            self.events = queue.Queue()
            self.items = []  # list[QueueItem]; widgets rebuilt from this
            self.row_refs = {}  # document_id -> {"dot","sub","pct"} labels
            self.queue_stop = threading.Event()
            self.current_cancel = None  # threading.Event of the running item
            self.worker = None
            self.running = False
            self.current_item = None
            self.run_stats = {"done": 0, "failed": 0, "skipped": 0, "cancelled": 0}
            self.last_saved_path = None
            self.parsed = None  # parse of the URL entry (single-link preview)
            self.started_at = None
            self.export_started_at = None
            self.last_progress = {}
            self._last_clipboard = None
            self._validate_job = None
            self.log_visible = False
            self.dnd_enabled = False

            # Tk variables
            self.url_var = tk.StringVar()
            self.folder_var = tk.StringVar(value=settings.output_dir)
            self.policy_var = tk.StringVar(
                value=next(
                    (label for label, v in CONFLICT_POLICIES.items()
                     if v == settings.conflict_policy),
                    "Save as copy",
                )
            )

            self._build_ui()
            self._bind_events()
            self._init_dnd()
            self._restore_pending_queue()
            self._refresh_chrome_status()
            self._refresh_recents()
            self._validate_url()
            self._refresh_queue()
            self._refresh_actions()

            self.after(80, self._poll_events)
            self.after(600, self._check_clipboard)
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.url_entry.focus_set()

        # ---------------------------------------------------------- setup
        def _set_window_icon(self):
            roots = []
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                roots.append(Path(meipass) / "assets")
            roots.append(Path(__file__).resolve().parent / "assets")
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
            width = self.winfo_width() if self.winfo_width() > 1 else 940
            height = self.winfo_height() if self.winfo_height() > 1 else 860
            x = max(0, (self.winfo_screenwidth() - width) // 2)
            y = max(0, (self.winfo_screenheight() - height) // 2)
            self.geometry(f"+{x}+{y}")

        def _font(self, size, weight="normal"):
            return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)

        # ---------------------------------------------------------- layout
        def _build_ui(self):
            outer = ctk.CTkFrame(self, fg_color="transparent")
            outer.pack(fill="both", expand=True, padx=28, pady=(20, 14))
            self.outer = outer

            self._build_header(outer)
            self._build_url_card(outer)
            self._build_queue_card(outer)
            self._build_save_card(outer)
            self._build_actions(outer)
            self._build_progress_card(outer)
            self._build_log(outer)
            self._build_recents(outer)

            footer = ctk.CTkLabel(
                outer,
                text="Runs locally in headless Chrome  ·  Queue downloads one document at a time  ·  Only download documents you have the right to access.",
                font=self._font(11),
                text_color=TEXT_MUTED,
            )
            footer.pack(side="bottom", pady=(10, 0))

            # Drop overlay (shown while dragging over the window)
            self.drop_overlay = ctk.CTkFrame(
                self, fg_color=CARD_BG, border_color=ACCENT,
                border_width=3, corner_radius=16,
            )
            ctk.CTkLabel(
                self.drop_overlay,
                text="Drop Scribd links or .txt files\nto add them to the queue",
                font=self._font(20, "bold"), text_color=ACCENT,
            ).place(relx=0.5, rely=0.5, anchor="center")

        def _card(self, parent):
            card = ctk.CTkFrame(
                parent, fg_color=CARD_BG, border_color=CARD_BORDER,
                border_width=1, corner_radius=14,
            )
            card.pack(fill="x", pady=(0, 14))
            return card

        def _ghost_button(self, parent, text, width, command, height=38):
            return ctk.CTkButton(
                parent, text=text, width=width, height=height,
                font=self._font(12), fg_color="transparent",
                hover_color=FIELD_BG, border_color=FIELD_BORDER, border_width=1,
                text_color=TEXT_MAIN, command=command,
            )

        def _build_header(self, parent):
            header = ctk.CTkFrame(parent, fg_color="transparent")
            header.pack(fill="x", pady=(0, 16))

            ctk.CTkLabel(
                header, text="S", width=46, height=46, corner_radius=12,
                fg_color=ACCENT, text_color="white", font=self._font(24, "bold"),
            ).pack(side="left")

            titles = ctk.CTkFrame(header, fg_color="transparent")
            titles.pack(side="left", padx=(12, 0))
            ctk.CTkLabel(
                titles, text=APP_NAME, font=self._font(21, "bold"), anchor="w"
            ).pack(fill="x")
            ctk.CTkLabel(
                titles, text="Download Scribd documents as clean PDFs",
                font=self._font(12), text_color=TEXT_MUTED, anchor="w",
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
                header, values=["System", "Light", "Dark"], width=104, height=32,
                font=self._font(12), fg_color=CARD_BG, button_color=FIELD_BORDER,
                button_hover_color=ACCENT, text_color=TEXT_MAIN,
                command=self._on_appearance_change,
            )
            appearance.set(self.settings.appearance)
            appearance.pack(side="right")
            self._ghost_button(header, "Settings", 92, self._open_settings, height=32).pack(
                side="right", padx=(0, 8)
            )

        def _build_url_card(self, parent):
            card = self._card(parent)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)

            ctk.CTkLabel(
                inner, text="Add Scribd documents", font=self._font(13, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(0, 8))

            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x")
            self.url_entry = ctk.CTkEntry(
                row, textvariable=self.url_var,
                placeholder_text="Paste one or more Scribd links…",
                height=42, font=self._font(13), fg_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1, corner_radius=10,
            )
            self.url_entry.pack(side="left", fill="x", expand=True)
            self.paste_btn = self._ghost_button(
                row, "Paste", 78, self._paste_from_clipboard, height=42
            )
            self.paste_btn.pack(side="left", padx=(8, 0))
            self.add_btn = ctk.CTkButton(
                row, text="Add to queue", width=118, height=42,
                font=self._font(12, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                command=self._add_from_entry,
            )
            self.add_btn.pack(side="left", padx=(8, 0))

            self.url_hint = ctk.CTkLabel(
                inner, text="", font=self._font(12), text_color=TEXT_MUTED,
                anchor="w", wraplength=840,
            )
            self.url_hint.pack(fill="x", pady=(8, 0))

            self.doc_info = ctk.CTkFrame(inner, fg_color="transparent")
            self.doc_title = ctk.CTkLabel(
                self.doc_info, text="", font=self._font(14, "bold"),
                anchor="w", wraplength=840,
            )
            self.doc_title.pack(fill="x", pady=(6, 0))
            self.doc_meta = ctk.CTkLabel(
                self.doc_info, text="", font=self._font(11),
                text_color=TEXT_MUTED, anchor="w",
            )
            self.doc_meta.pack(fill="x")

        def _build_queue_card(self, parent):
            card = self._card(parent)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=(14, 10))

            header = ctk.CTkFrame(inner, fg_color="transparent")
            header.pack(fill="x", pady=(0, 8))
            self.queue_title = ctk.CTkLabel(
                header, text="Queue", font=self._font(13, "bold"), anchor="w"
            )
            self.queue_title.pack(side="left")
            self.clear_done_btn = self._ghost_button(
                header, "Clear finished", 116,
                lambda: self._clear_items(FINISHED_STATUSES), height=30,
            )
            self.clear_done_btn.pack(side="right")
            self.clear_all_btn = self._ghost_button(
                header, "Clear all", 84,
                lambda: self._clear_items(("pending",) + FINISHED_STATUSES),
                height=30,
            )
            self.clear_all_btn.pack(side="right", padx=(0, 8))
            self.import_btn = self._ghost_button(
                header, "Add from file…", 122, self._import_link_files, height=30
            )
            self.import_btn.pack(side="right", padx=(0, 8))

            self.queue_list = ctk.CTkScrollableFrame(
                inner, height=176, fg_color="transparent",
                scrollbar_button_color=FIELD_BORDER,
            )
            self.queue_list.pack(fill="x")

        def _build_save_card(self, parent):
            card = self._card(parent)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=14)

            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x")
            ctk.CTkLabel(
                row, text="Save PDFs to", font=self._font(13, "bold")
            ).pack(side="left", padx=(0, 10))
            self.folder_entry = ctk.CTkEntry(
                row, textvariable=self.folder_var, height=38,
                font=self._font(12), fg_color=FIELD_BG,
                border_color=FIELD_BORDER, border_width=1, corner_radius=10,
            )
            self.folder_entry.pack(side="left", fill="x", expand=True)
            self.browse_btn = self._ghost_button(row, "Browse…", 92, self._browse_folder)
            self.browse_btn.pack(side="left", padx=(8, 0))

            policy_row = ctk.CTkFrame(inner, fg_color="transparent")
            policy_row.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(
                policy_row, text="If a file already exists:",
                font=self._font(12), text_color=TEXT_MUTED,
            ).pack(side="left", padx=(0, 8))
            policy = ctk.CTkOptionMenu(
                policy_row, variable=self.policy_var,
                values=list(CONFLICT_POLICIES.keys()), width=150, height=30,
                font=self._font(12), fg_color=FIELD_BG,
                button_color=FIELD_BORDER, button_hover_color=ACCENT,
                text_color=TEXT_MAIN, command=self._on_policy_change,
            )
            policy.pack(side="left")
            ctk.CTkLabel(
                policy_row,
                text="Tip: rename any queued PDF with its Edit button.",
                font=self._font(11), text_color=TEXT_MUTED,
            ).pack(side="left", padx=(12, 0))

        def _build_actions(self, parent):
            self.action_frame = ctk.CTkFrame(parent, fg_color="transparent")
            self.action_frame.pack(fill="x", pady=(2, 14))

            self.download_btn = ctk.CTkButton(
                self.action_frame, text="Download PDF", height=48,
                font=self._font(15, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                corner_radius=12, command=self._start_queue,
            )
            self.download_btn.pack(fill="x")

            self.running_row = ctk.CTkFrame(self.action_frame, fg_color="transparent")
            self.skip_btn = self._ghost_button(
                self.running_row, "Skip current document", 200,
                self._skip_current, height=48,
            )
            self.skip_btn.pack(side="left", fill="x", expand=True)
            self.stop_btn = ctk.CTkButton(
                self.running_row, text="Stop queue", width=200, height=48,
                font=self._font(14, "bold"), fg_color="transparent",
                hover_color=FIELD_BG, border_color=ERROR, border_width=1,
                text_color=ERROR, corner_radius=12, command=self._stop_queue,
            )
            self.stop_btn.pack(side="left", fill="x", expand=True, padx=(10, 0))

            # End-of-run summary (per-document actions live in the queue rows)
            self.result_panel = ctk.CTkFrame(
                parent, fg_color=CARD_BG, border_color=CARD_BORDER,
                border_width=1, corner_radius=14,
            )
            result_inner = ctk.CTkFrame(self.result_panel, fg_color="transparent")
            result_inner.pack(fill="x", padx=18, pady=14)
            self.result_title = ctk.CTkLabel(
                result_inner, text="", font=self._font(14, "bold"),
                anchor="w", wraplength=840,
            )
            self.result_title.pack(fill="x")
            self.result_detail = ctk.CTkLabel(
                result_inner, text="", font=self._font(12),
                text_color=TEXT_MUTED, anchor="w", wraplength=840,
            )
            self.result_detail.pack(fill="x", pady=(2, 10))
            btn_row = ctk.CTkFrame(result_inner, fg_color="transparent")
            btn_row.pack(fill="x")
            self.open_last_btn = ctk.CTkButton(
                btn_row, text="Open latest PDF", width=140, height=36,
                font=self._font(12, "bold"), fg_color=ACCENT,
                hover_color=ACCENT_HOVER, text_color="white",
                command=lambda: self.last_saved_path and open_file(self.last_saved_path),
            )
            self.open_last_btn.pack(side="left")
            self._ghost_button(
                btn_row, "Open folder", 120,
                lambda: open_folder(self.folder_var.get() or default_downloads_dir()),
                height=36,
            ).pack(side="left", padx=(8, 0))
            self._ghost_button(
                btn_row, "Clear finished", 130,
                lambda: self._clear_items(FINISHED_STATUSES), height=36,
            ).pack(side="left", padx=(8, 0))

        def _build_progress_card(self, parent):
            self.progress_card = ctk.CTkFrame(
                parent, fg_color=CARD_BG, border_color=CARD_BORDER,
                border_width=1, corner_radius=14,
            )
            inner = ctk.CTkFrame(self.progress_card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)

            self.overall_label = ctk.CTkLabel(
                inner, text="", font=self._font(12, "bold"),
                text_color=TEXT_MUTED, anchor="w", wraplength=840,
            )
            self.overall_label.pack(fill="x", pady=(0, 10))

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
                parent, height=110, font=ctk.CTkFont(family="Consolas", size=11),
                fg_color=CARD_BG, border_color=CARD_BORDER, border_width=1,
                corner_radius=10, state="disabled",
            )

        def _build_recents(self, parent):
            self.recents_frame = ctk.CTkFrame(parent, fg_color="transparent")
            self.recents_frame.pack(fill="x", pady=(10, 0))

        # ---------------------------------------------------------- events
        def _bind_events(self):
            self.url_var.trace_add("write", lambda *_: self._schedule_validate())
            self.url_entry.bind("<Return>", lambda _e: self._add_from_entry())
            self.url_entry.bind("<<Paste>>", self._on_entry_paste)
            self.bind("<Control-Return>", lambda _e: self._start_queue())
            self.bind("<Escape>", lambda _e: self.running and self._stop_queue())
            self.bind("<FocusIn>", lambda _e: self.after(150, self._check_clipboard))

        def _schedule_validate(self):
            if self._validate_job:
                self.after_cancel(self._validate_job)
            self._validate_job = self.after(220, self._validate_url)

        # ---------------------------------------------------------- drag & drop
        def _init_dnd(self):
            if not _DND_AVAILABLE:
                return
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                for target in (self, self.drop_overlay, self.queue_list):
                    target.drop_target_register(DND_FILES, DND_TEXT)
                    target.dnd_bind("<<Drop>>", self._on_drop)
                    target.dnd_bind(
                        "<<DropEnter>>", lambda _e: self._show_drop_overlay(True)
                    )
                    target.dnd_bind(
                        "<<DropLeave>>", lambda _e: self._show_drop_overlay(False)
                    )
                self.dnd_enabled = True
            except Exception:
                self.dnd_enabled = False  # app is fully usable without DnD

        def _show_drop_overlay(self, show):
            if show:
                self.drop_overlay.place(
                    relx=0.5, rely=0.5, anchor="center", relwidth=0.94, relheight=0.94
                )
                self.drop_overlay.lift()
            else:
                self.drop_overlay.place_forget()

        def _on_drop(self, event):
            self._show_drop_overlay(False)
            try:
                splitlist = self.tk.splitlist
            except Exception:
                splitlist = None
            infos = parse_drop_data(self.engine, getattr(event, "data", ""), splitlist)
            if infos:
                self._add_infos(infos, source="dropped")
            else:
                self.url_hint.configure(
                    text="No Scribd links found in what you dropped. "
                         "Drop document links or a .txt file of links.",
                    text_color=WARNING,
                )
            return _DND_COPY

        # ---------------------------------------------------------- header
        def _refresh_chrome_status(self):
            if find_chrome():
                self.chrome_label.configure(text="●  Chrome ready", text_color=SUCCESS)
            else:
                self.chrome_label.configure(
                    text="●  Chrome not found — click to install Google Chrome",
                    text_color=WARNING,
                )

        def _on_appearance_change(self, value):
            self.settings.appearance = value
            self.settings.save()
            ctk.set_appearance_mode(value)

        def _on_policy_change(self, label):
            self.settings.conflict_policy = CONFLICT_POLICIES.get(label, "copy")
            self.settings.save()

        # ---------------------------------------------------------- adding links
        def _validate_url(self):
            self._validate_job = None
            raw = self.url_var.get().strip()
            infos = self.engine.extract_scribd_urls(raw) if raw else []
            self.parsed = infos[0] if infos else None

            if not raw:
                hint = "Paste links like  scribd.com/document/123456789/Document-Title"
                if self.dnd_enabled:
                    hint += "  ·  or drag & drop links / .txt files anywhere in this window"
                self.url_hint.configure(text=hint, text_color=TEXT_MUTED)
                self.url_entry.configure(border_color=FIELD_BORDER)
                self.doc_info.pack_forget()
            elif not infos:
                self.url_hint.configure(
                    text="No Scribd document link found in that text yet.",
                    text_color=ERROR,
                )
                self.url_entry.configure(border_color=ERROR)
                self.doc_info.pack_forget()
            elif len(infos) == 1:
                self.url_hint.configure(
                    text="✓  Valid Scribd link — Enter adds it to the queue",
                    text_color=SUCCESS,
                )
                self.url_entry.configure(border_color=SUCCESS)
                title = infos[0]["title"] or f"Scribd document {infos[0]['document_id']}"
                self.doc_title.configure(text=title)
                self.doc_meta.configure(
                    text=f"Document ID {infos[0]['document_id']}  ·  "
                         "Exported as a clean, toolbar-free PDF"
                )
                self.doc_info.pack(fill="x")
            else:
                self.url_hint.configure(
                    text=f"✓  Found {len(infos)} Scribd links — Enter adds them all",
                    text_color=SUCCESS,
                )
                self.url_entry.configure(border_color=SUCCESS)
                self.doc_info.pack_forget()
            self._refresh_actions()

        def _add_infos(self, infos, source="added"):
            """Add link infos to the queue (deduplicated). Returns count added."""
            known = {item.document_id for item in self.items
                     if item.status in ("pending", "downloading")}
            fresh = dedupe_infos(infos, known)
            for info in fresh:
                self.items.append(QueueItem.from_info(info))
            if fresh:
                noun = "document" if len(fresh) == 1 else "documents"
                self.url_hint.configure(
                    text=f"✓  {len(fresh)} {noun} {source} to the queue"
                         + ("." if not self.running else " — will download next."),
                    text_color=SUCCESS,
                )
                self._append_log(f"Queued {len(fresh)} {noun} ({source}).")
            elif infos:
                self.url_hint.configure(
                    text="Those documents are already in the queue.",
                    text_color=WARNING,
                )
            self._persist_pending()
            self._refresh_queue()
            self._refresh_actions()
            return len(fresh)

        def _add_from_entry(self):
            infos = self.engine.extract_scribd_urls(self.url_var.get())
            if not infos:
                self._validate_url()
                self.url_entry.focus_set()
                return
            self._add_infos(infos)
            self.url_var.set("")
            self._validate_url()
            self.url_entry.focus_set()

        def _paste_from_clipboard(self):
            try:
                text = self.clipboard_get()
            except tk.TclError:
                text = ""
            infos = self.engine.extract_scribd_urls(text)
            if len(infos) > 1:
                self._add_infos(infos, source="pasted")
            elif infos:
                self.url_var.set(text.strip())
                self._validate_url()
                self.url_entry.focus_set()
            else:
                self.url_hint.configure(
                    text="Clipboard doesn't contain a Scribd link — copy one first.",
                    text_color=WARNING,
                )

        def _on_entry_paste(self, _event):
            """Ctrl+V of several links adds them all instead of filling the box."""
            try:
                text = self.clipboard_get()
            except tk.TclError:
                return None
            infos = self.engine.extract_scribd_urls(text)
            if len(infos) > 1:
                self._add_infos(infos, source="pasted")
                return "break"
            return None

        def _check_clipboard(self):
            """Offer Scribd link(s) that are already on the clipboard."""
            if not self.settings.clipboard_check:
                return
            try:
                text = self.clipboard_get().strip()
            except tk.TclError:
                return
            if not text or text == self._last_clipboard:
                return
            self._last_clipboard = text
            infos = self.engine.extract_scribd_urls(text)
            if not infos or self.url_var.get().strip():
                return
            if len(infos) > 1:
                if not self.items:
                    self._add_infos(infos, source="taken from your clipboard")
            else:
                self.url_var.set(text)
                self._validate_url()
                self.url_hint.configure(
                    text="✓  Scribd link taken from your clipboard — Enter to queue it.",
                    text_color=SUCCESS,
                )

        def _import_link_files(self):
            paths = filedialog.askopenfilenames(
                title="Choose file(s) containing Scribd links",
                filetypes=[
                    ("Link files", "*.txt *.url *.md *.csv *.html"),
                    ("All files", "*.*"),
                ],
            )
            if not paths:
                return
            infos = read_urls_from_files(self.engine, list(paths))
            if infos:
                self._add_infos(infos, source="imported")
            else:
                self.url_hint.configure(
                    text="No Scribd links found in the selected file(s).",
                    text_color=WARNING,
                )

        # ---------------------------------------------------------- queue UI
        def _item_subtext(self, item):
            if item.status == "failed" and item.detail:
                return item.detail
            if item.status == "skipped":
                return f"{item.filename}  ·  skipped (file already exists)"
            if item.status == "done" and item.output_path:
                try:
                    size = human_size(Path(item.output_path).stat().st_size)
                    return f"{Path(item.output_path).name}  ·  {size}"
                except OSError:
                    pass
            return f"{item.filename}  ·  ID {item.document_id}"

        def _refresh_queue(self):
            """Rebuild queue rows from the model (not called on progress ticks)."""
            for child in self.queue_list.winfo_children():
                child.destroy()
            self.row_refs = {}

            pending = sum(1 for i in self.items if i.status == "pending")
            done = sum(1 for i in self.items if i.status == "done")
            self.queue_title.configure(
                text=f"Queue  ({len(self.items)})"
                     + (f"  ·  {pending} waiting" if pending else "")
                     + (f"  ·  {done} saved" if done else "")
            )

            if not self.items:
                empty_text = "No documents queued yet.\nPaste Scribd links above"
                if self.dnd_enabled:
                    empty_text += ", or drop links / .txt files anywhere in this window."
                else:
                    empty_text += ", or import a .txt file of links."
                ctk.CTkLabel(
                    self.queue_list, text=empty_text, font=self._font(12),
                    text_color=TEXT_MUTED, justify="center",
                ).pack(pady=(26, 26))
                return

            for item in self.items:
                row = ctk.CTkFrame(
                    self.queue_list, fg_color=FIELD_BG,
                    border_color=CARD_BORDER, border_width=1, corner_radius=10,
                )
                row.pack(fill="x", padx=4, pady=3)

                dot = ctk.CTkLabel(
                    row, text="●", width=20, font=self._font(13),
                    text_color=STATUS_COLORS.get(item.status, TEXT_MUTED),
                )
                dot.pack(side="left", padx=(10, 2), pady=8)

                texts = ctk.CTkFrame(row, fg_color="transparent")
                texts.pack(side="left", fill="x", expand=True, pady=6)
                ctk.CTkLabel(
                    texts, text=item.title, font=self._font(12, "bold"),
                    anchor="w", wraplength=520,
                ).pack(fill="x")
                sub = ctk.CTkLabel(
                    texts, text=self._item_subtext(item), font=self._font(11),
                    text_color=ERROR if item.status == "failed" else TEXT_MUTED,
                    anchor="w", wraplength=520,
                )
                sub.pack(fill="x")

                pct = ctk.CTkLabel(
                    row, text=f"{item.percent}%" if item.status == "downloading" else "",
                    width=46, font=self._font(12, "bold"), text_color=TEXT_MUTED,
                )
                pct.pack(side="left")
                self.row_refs[item.document_id] = {"dot": dot, "sub": sub, "pct": pct}

                if item.status == "pending":
                    self._ghost_button(
                        row, "Edit", 54,
                        lambda it=item: self._edit_item_filename(it), height=30,
                    ).pack(side="left", padx=(0, 6), pady=8)
                if item.status in ("failed", "cancelled"):
                    self._ghost_button(
                        row, "Retry", 58,
                        lambda it=item: self._retry_item(it), height=30,
                    ).pack(side="left", padx=(0, 6), pady=8)
                if item.status == "done" and item.output_path:
                    self._ghost_button(
                        row, "Open", 58,
                        lambda it=item: open_file(it.output_path), height=30,
                    ).pack(side="left", padx=(0, 6), pady=8)
                if item.status != "downloading":
                    self._ghost_button(
                        row, "✕", 34,
                        lambda it=item: self._remove_item(it), height=30,
                    ).pack(side="left", padx=(0, 8), pady=8)

        def _update_row(self, item):
            refs = self.row_refs.get(item.document_id)
            if not refs:
                self._refresh_queue()
                return
            refs["dot"].configure(
                text_color=STATUS_COLORS.get(item.status, TEXT_MUTED)
            )
            refs["sub"].configure(text=self._item_subtext(item))
            refs["pct"].configure(
                text=f"{item.percent}%" if item.status == "downloading" else ""
            )

        def _remove_item(self, item):
            if item.status == "downloading":
                return
            self.items = [i for i in self.items if i is not item]
            self._persist_pending()
            self._refresh_queue()
            self._refresh_actions()

        def _clear_items(self, statuses):
            if self.running and "pending" in statuses:
                return  # don't yank the queue mid-run; ✕ works per row instead
            self.items = [
                i for i in self.items
                if i.status not in statuses or i.status == "downloading"
            ]
            self.result_panel.pack_forget()
            self._persist_pending()
            self._refresh_queue()
            self._refresh_actions()

        def _retry_item(self, item):
            item.status = "pending"
            item.detail = ""
            item.percent = 0
            self._persist_pending()
            self._refresh_queue()
            self._refresh_actions()
            if not self.running:
                self._start_queue()

        def _edit_item_filename(self, item):
            dialog = ctk.CTkInputDialog(
                text="File name for this PDF (“.pdf” is added automatically):",
                title="Rename PDF",
            )
            value = dialog.get_input()
            if value and value.strip():
                stem = self.engine.sanitize_filename(value.strip())
                item.filename = f"{stem}.pdf"
                self._persist_pending()
                self._refresh_queue()

        def _persist_pending(self):
            self.settings.pending_queue = [
                {"source_url": i.source_url, "filename": i.filename}
                for i in self.items
                if i.status in ("pending", "downloading")
            ]
            self.settings.save()

        def _restore_pending_queue(self):
            for entry in self.settings.pending_queue:
                info = self.engine.parse_scribd_url(entry.get("source_url", ""))
                if not info:
                    continue
                item = QueueItem.from_info(info)
                if entry.get("filename"):
                    item.filename = entry["filename"]
                if item.document_id not in {i.document_id for i in self.items}:
                    self.items.append(item)
            if self.items:
                self._append_log(
                    f"Restored {len(self.items)} queued document(s) from last time."
                )

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

        # ---------------------------------------------------------- queue worker
        def _pending_count(self):
            return sum(1 for i in self.items if i.status == "pending")

        def _refresh_actions(self):
            if self.running:
                self.download_btn.pack_forget()
                self.running_row.pack(fill="x")
                return
            self.running_row.pack_forget()
            self.download_btn.pack(fill="x")
            pending = self._pending_count()
            if pending > 1:
                self.download_btn.configure(
                    text=f"Download {pending} PDFs", state="normal"
                )
            elif pending == 1:
                self.download_btn.configure(text="Download PDF", state="normal")
            elif self.parsed:
                self.download_btn.configure(text="Add & Download", state="normal")
            else:
                self.download_btn.configure(text="Download PDF", state="disabled")

        def _start_queue(self):
            if self.running:
                return
            # A valid link still in the entry joins the queue first.
            if self.url_var.get().strip():
                infos = self.engine.extract_scribd_urls(self.url_var.get())
                if infos:
                    self._add_infos(infos)
                    self.url_var.set("")
                    self._validate_url()
            if self._pending_count() == 0:
                self.url_entry.focus_set()
                return

            folder = Path(self.folder_var.get().strip() or default_downloads_dir())
            self.settings.output_dir = str(folder)
            self.settings.save()

            self.queue_stop = threading.Event()
            self.running = True
            self.run_stats = {"done": 0, "failed": 0, "skipped": 0, "cancelled": 0}
            self.result_panel.pack_forget()
            self.progress_card.pack(
                fill="x", pady=(0, 14), after=self.action_frame
            )
            self._set_input_enabled(False)
            self._refresh_actions()
            self._append_log(
                f"\n--- Queue started {time.strftime('%H:%M:%S')} · "
                f"{self._pending_count()} document(s) · saving to {folder} ---"
            )
            self.worker = threading.Thread(
                target=self._queue_worker,
                args=(folder, self.settings.conflict_policy),
                daemon=True,
                name="scribd-queue",
            )
            self.worker.start()

        def _set_input_enabled(self, enabled):
            # The URL entry stays usable while downloading so more links can
            # be queued; only destination controls lock (folder is snapshotted).
            state = "normal" if enabled else "disabled"
            for widget in (self.folder_entry, self.browse_btn, self.clear_all_btn):
                widget.configure(state=state)

        def _queue_worker(self, folder, policy):
            """Sequentially download pending items. Data-only thread: every
            widget change is posted to the main thread via ``self.events``."""
            settings = self.settings
            while not self.queue_stop.is_set():
                item = next(
                    (i for i in list(self.items) if i.status == "pending"), None
                )
                if item is None:
                    break

                item.status = "downloading"
                item.percent = 0
                item.detail = ""
                output = folder / item.filename
                existed_before = output.exists()
                if existed_before and policy == "skip":
                    item.status = "skipped"
                    item.output_path = output
                    self.events.put(("item_skipped", item))
                    continue
                if existed_before and policy == "copy":
                    output = unique_path(output)
                item.output_path = output

                cancel_event = threading.Event()
                self.current_cancel = cancel_event
                doc_id = item.document_id
                self.events.put(("item_started", item))
                try:
                    saved = self.engine.download_document(
                        item.source_url,
                        str(output),
                        on_log=lambda line: self.events.put(("log", line)),
                        on_progress=lambda info, d=doc_id: self.events.put(
                            ("progress", (d, info))
                        ),
                        cancel_event=cancel_event,
                        headless=not settings.show_browser,
                        batch_size=settings.batch_size,
                        page_load_timeout=settings.page_load_timeout,
                        cdp_timeout=settings.cdp_timeout,
                    )
                    item.status = "done"
                    item.percent = 100
                    item.output_path = Path(saved)
                    self.events.put(("item_done", item))
                except self.engine.DownloadCancelledError:
                    item.status = "cancelled"
                    self._cleanup_partial(output, existed_before)
                    self.events.put(("item_cancelled", item))
                except Exception as error:  # noqa: BLE001 - shown to the user
                    item.status = "failed"
                    item.detail = friendly_error(error)
                    self._cleanup_partial(output, existed_before)
                    self.events.put(("item_failed", (item, error)))
                finally:
                    self.current_cancel = None

            self.events.put(("queue_finished", None))

        @staticmethod
        def _cleanup_partial(output, existed_before):
            """Remove an incomplete file created by this run (merge writes last)."""
            try:
                if not existed_before and Path(output).is_file():
                    Path(output).unlink()
            except OSError:
                pass

        def _skip_current(self):
            if self.running and self.current_cancel is not None:
                self.status_label.configure(
                    text="Skipping — finishing the current page…"
                )
                self.current_cancel.set()

        def _stop_queue(self):
            if not self.running:
                return
            self.queue_stop.set()
            if self.current_cancel is not None:
                self.current_cancel.set()
            self.stop_btn.configure(state="disabled", text="Stopping…")
            self.status_label.configure(
                text="Stopping queue — finishing the current page…"
            )

        # ---------------------------------------------------------- worker events
        def _poll_events(self):
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "log":
                        self._append_log(payload)
                    elif kind == "progress":
                        doc_id, info = payload
                        if self.running:
                            self._apply_progress(doc_id, info)
                    elif kind == "item_started":
                        self.current_item = payload
                        self.started_at = time.monotonic()
                        self.export_started_at = None
                        self.last_progress = {}
                        index = self.items.index(payload) + 1 if payload in self.items else 1
                        self.overall_label.configure(
                            text=f"Document {index} of {len(self.items)} — {payload.title}"
                        )
                        self._reset_steps()
                        self.progress_bar.set(0)
                        self._update_row(payload)
                        self._tick_timing()
                    elif kind == "item_done":
                        self.run_stats["done"] += 1
                        self.last_saved_path = payload.output_path
                        self.settings.add_recent(payload.output_path, payload.title)
                        self._refresh_recents()
                        self._refresh_queue()
                        self._persist_pending()
                    elif kind == "item_skipped":
                        self.run_stats["skipped"] += 1
                        self._append_log(f"Skipped (already exists): {payload.filename}")
                        self._refresh_queue()
                        self._persist_pending()
                    elif kind == "item_cancelled":
                        self.run_stats["cancelled"] += 1
                        self._append_log(f"Cancelled: {payload.title}")
                        self._refresh_queue()
                        self._persist_pending()
                    elif kind == "item_failed":
                        item, error = payload
                        self.run_stats["failed"] += 1
                        self._append_log(f"ERROR ({item.title}): {error}")
                        self._refresh_queue()
                        self._persist_pending()
                        if not self.log_visible:
                            self._toggle_log()
                    elif kind == "queue_finished":
                        self._on_queue_finished()
            except queue.Empty:
                pass
            self.after(80, self._poll_events)

        def _on_queue_finished(self):
            self.running = False
            self.current_item = None
            self.progress_bar.stop()
            self.stop_btn.configure(state="normal", text="Stop queue")
            self._set_input_enabled(True)
            self._persist_pending()
            self._refresh_queue()
            self._refresh_actions()

            stats = self.run_stats
            parts = [f"{stats['done']} saved"] if stats["done"] else []
            if stats["failed"]:
                parts.append(f"{stats['failed']} failed")
            if stats["skipped"]:
                parts.append(f"{stats['skipped']} skipped")
            if stats["cancelled"]:
                parts.append(f"{stats['cancelled']} cancelled")
            if not parts:
                parts = ["nothing downloaded"]
            remaining = self._pending_count()
            stopped = " (stopped early)" if remaining and self.queue_stop.is_set() else ""

            all_ok = stats["done"] and not (stats["failed"] or stats["cancelled"])
            self.result_title.configure(
                text=f"{'✓' if all_ok else 'Queue finished'}  "
                     f"{', '.join(parts)}{stopped}",
                text_color=SUCCESS if all_ok else TEXT_MAIN,
            )
            self.result_detail.configure(
                text=f"Saved to {self.folder_var.get()}"
                     + (f"  ·  {remaining} still waiting — press Download to continue."
                        if remaining else
                        "  ·  Open any PDF from its queue row or Recent downloads.")
            )
            self.open_last_btn.configure(
                state="normal" if self.last_saved_path else "disabled"
            )
            self.result_panel.pack(fill="x", pady=(0, 14), after=self.action_frame)
            self.progress_card.pack_forget()
            self.progress_card.pack(fill="x", pady=(0, 14), after=self.result_panel)
            self.status_label.configure(text="Queue finished.")
            self._append_log(f"--- Queue finished: {', '.join(parts)} ---")

            # Open-when-done only makes sense for a single-document run.
            if (
                self.settings.open_when_done
                and stats["done"] == 1
                and not (stats["failed"] or stats["skipped"] or stats["cancelled"])
                and self.last_saved_path is not None
            ):
                open_file(self.last_saved_path)

        # ---------------------------------------------------------- progress
        def _reset_steps(self):
            for stage, (dot, label) in self.step_widgets.items():
                dot.configure(text=str(STAGES.index(stage) + 1), fg_color=FIELD_BORDER)
                label.configure(text_color=TEXT_MUTED, font=self._font(12))

        def _mark_stage(self, stage):
            active_index = len(STAGES) if stage == "done" else STAGES.index(stage)
            for index, name in enumerate(STAGES):
                dot, label = self.step_widgets[name]
                if index < active_index:
                    dot.configure(text="✓", fg_color=SUCCESS)
                    label.configure(text_color=TEXT_MUTED, font=self._font(12))
                elif index == active_index:
                    dot.configure(text=str(index + 1), fg_color=ACCENT)
                    label.configure(text_color=TEXT_MAIN, font=self._font(12, "bold"))
                else:
                    dot.configure(text=str(index + 1), fg_color=FIELD_BORDER)
                    label.configure(text_color=TEXT_MUTED, font=self._font(12))

        def _apply_progress(self, doc_id, info):
            self.last_progress = info
            stage = info.get("stage") or "export"
            current, total = info.get("current"), info.get("total")
            message = info.get("message") or ""

            if self.current_item is not None and self.current_item.document_id == doc_id:
                if stage == "export" and total:
                    self.current_item.percent = int(round(100 * (current or 0) / total))
                elif stage == "done":
                    self.current_item.percent = 100
                self._update_row(self.current_item)

            if stage in STAGES:
                self._mark_stage(stage)
            elif stage == "done":
                self._mark_stage("done")
            if stage == "export" and self.export_started_at is None:
                self.export_started_at = time.monotonic()

            if stage in ("connect", "merge") or not total:
                self.progress_bar.configure(mode="indeterminate")
                self.progress_bar.start()
            else:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
                self.progress_bar.set((current or 0) / total if total else 0)

            if stage == "export" and current is not None and total:
                percent = int(round(100 * current / total))
                self.status_label.configure(
                    text=f"Exporting page {current} of {total}  ·  {percent}%"
                )
            elif message:
                self.status_label.configure(text=message)

        def _tick_timing(self):
            if not self.running:
                return
            elapsed = time.monotonic() - (self.started_at or time.monotonic())
            text = f"This document: {format_duration(elapsed)}"
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
            for entry in existing[:4]:
                path = Path(entry["path"])
                row = ctk.CTkFrame(self.recents_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                date_text = time.strftime("%b %d", time.localtime(entry.get("ts", 0)))
                ctk.CTkButton(
                    row, text=path.name, font=self._font(12),
                    fg_color="transparent", hover_color=FIELD_BG,
                    text_color=TEXT_MAIN, anchor="w",
                    command=lambda p=path: open_file(p),
                ).pack(side="left", fill="x", expand=True)
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
            batch.set(
                str(self.settings.batch_size)
                if str(self.settings.batch_size) in ("4", "8", "16") else "8"
            )
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
                body, text="Open the PDF when a single download finishes",
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
            self._ghost_button(row, "Cancel", 100, dialog.destroy).pack(
                side="right", padx=(0, 8)
            )

            dialog.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - 500) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 560) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
            dialog.grab_set()
            dialog.focus_set()

        # ---------------------------------------------------------- close
        def _on_close(self):
            if self.running:
                if not messagebox.askyesno(
                    APP_NAME,
                    "The queue is downloading. Quit anyway?\n\n"
                    "Unfinished documents stay in the queue for next time.",
                    parent=self,
                ):
                    return
                self.queue_stop.set()
                if self.current_cancel is not None:
                    self.current_cancel.set()
            self.settings.output_dir = self.folder_var.get() or self.settings.output_dir
            self._persist_pending()
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

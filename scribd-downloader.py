"""
Scribd Document Downloader
==========================

A Selenium-based utility that loads a Scribd embed and saves it as a PDF.

Key behaviors:
1. Converts a Scribd document URL to the embed/content URL.
2. Opens the document in Chrome.
3. Scrolls through every page to trigger lazy loading.
4. Removes UI overlays without stripping layout classes needed for rendering.
5. Waits for fonts, images, and page geometry to settle.
6. Saves the PDF through Chrome DevTools Protocol with a larger timeout and
   stream-based PDF transfer for large documents.
"""

import base64
import os
import re
import socket
import tempfile
import threading
import time
from io import BytesIO
from urllib.parse import unquote, urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options


DEFAULT_CDP_TIMEOUT_SECONDS = int(os.getenv("SCRIBD_CDP_TIMEOUT", "600"))
DEFAULT_RENDER_SETTLE_TIMEOUT_SECONDS = int(
    os.getenv("SCRIBD_RENDER_SETTLE_TIMEOUT", "30")
)
DEFAULT_SCROLL_DELAY_SECONDS = float(os.getenv("SCRIBD_SCROLL_DELAY", "0.15"))
DEFAULT_PAGE_LOAD_CONCURRENCY = max(
    1,
    int(os.getenv("SCRIBD_PAGE_LOAD_CONCURRENCY", "8")),
)
DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS = max(
    10,
    int(os.getenv("SCRIBD_PAGE_LOAD_TIMEOUT", "120")),
)
DEFAULT_EXPORT_BATCH_SIZE = max(
    1,
    int(os.getenv("SCRIBD_EXPORT_BATCH_SIZE", "8")),
)
BATCH_PROBE_TIMEOUT_SECONDS = 20
BATCH_POLL_INTERVAL_SECONDS = 0.25
DRIVER_QUIT_TIMEOUT_SECONDS = 15
PDF_STREAM_CHUNK_SIZE = int(os.getenv("SCRIBD_PDF_STREAM_CHUNK_SIZE", str(1024 * 1024)))
HEADLESS_ENABLED = os.getenv("SCRIBD_HEADLESS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_PAPER_WIDTH_INCHES = 7.25
DEFAULT_PAPER_HEIGHT_INCHES = 10.5


class DownloadCancelledError(RuntimeError):
    """Raised when a download is cancelled through its cancel event."""


# ---------------------------------------------------------------------------
# Per-download run state (log/progress callbacks, cancellation, overrides)
#
# The CLI runs everything on the main thread and simply prints. The GUI runs
# ``download_document`` on a worker thread and registers callbacks here, so
# engine code stays UI-agnostic: it calls ``_log`` / ``_progress`` /
# ``_check_cancelled`` and never imports a GUI toolkit. State is thread-local
# so a GUI worker thread cannot leak callbacks into another download.
# ---------------------------------------------------------------------------
_run_state = threading.local()


def _log(message=""):
    """Print a message (CLI behaviour) and mirror it to the log callback."""
    text = str(message)
    try:
        # A windowed PyInstaller build may have no stdout at all. Losing the
        # console copy must never interrupt a GUI download or its callback.
        print(text, flush=True)
    except Exception:
        pass
    callback = getattr(_run_state, "on_log", None)
    if callback is not None:
        try:
            callback(text)
        except Exception:
            pass  # A broken UI callback must never break the export.


def _progress(stage, current=None, total=None, message=None):
    """Report structured progress: stage, current/total pages, message.

    Stages used by the GUI step indicator:
    ``connect`` -> ``load`` -> ``export`` -> ``merge`` -> ``done``.
    """
    callback = getattr(_run_state, "on_progress", None)
    if callback is not None:
        try:
            callback(
                {
                    "stage": stage,
                    "current": current,
                    "total": total,
                    "message": message,
                }
            )
        except Exception:
            pass


def _check_cancelled():
    """Raise :class:`DownloadCancelledError` if cancellation was requested."""
    event = getattr(_run_state, "cancel_event", None)
    if event is not None and event.is_set():
        raise DownloadCancelledError("Download cancelled by the user.")


def _override(name, default):
    """Return a per-download option override, or ``default`` when unset."""
    value = getattr(_run_state, name, None)
    return default if value is None else value


def build_chrome_options(runtime_profile_dir, headless=None):
    """Create Chrome options for reliable headless PDF generation.

    ``headless=None`` keeps the ``SCRIBD_HEADLESS`` environment default;
    pass ``True``/``False`` (the GUI does) to force a mode.
    """
    options = Options()

    if HEADLESS_ENABLED if headless is None else headless:
        options.add_argument("--headless=new")

    # Scribd embeds keep loading ads/trackers long after the document DOM is
    # usable. "eager" returns at DOMContentLoaded; download_document then
    # explicitly waits for stable Scribd page nodes with a bounded timeout.
    options.page_load_strategy = "eager"

    options.add_argument("--window-size=1600,2200")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument(f"--user-data-dir={runtime_profile_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--force-color-profile=srgb")
    options.add_argument("--hide-scrollbars")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


# Matches /document/<id>, legacy /doc/<id> and /embeds/<id> on any Scribd
# subdomain (www, de, es, ...), with or without a title slug after the id.
_SCRIBD_URL_RE = re.compile(
    r"scribd\.com/(?:document|doc|embeds)/(\d+)(?:/([^/?#\s]+))?",
    re.IGNORECASE,
)

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_filename(name, fallback="scribd-document"):
    """Make ``name`` safe to use as a file name on Windows (and elsewhere).

    Strips characters Windows forbids (``<>:"/\\|?*`` and control chars),
    trailing dots/spaces, reserved device names, and over-long names.
    The ``.pdf`` extension is *not* added here.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if cleaned.lower().endswith(".pdf"):
        cleaned = cleaned[:-4].rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned[:150].rstrip(". ") or fallback


def parse_scribd_url(url):
    """
    Parse a Scribd document URL (also finds one inside pasted text).

    Accepts ``/document/<id>/Title``, legacy ``/doc/<id>/Title`` and
    ``/embeds/<id>/content`` links on any Scribd subdomain.

    Returns:
        ``None`` when no document id can be extracted, otherwise a dict with
        ``document_id``, ``title`` (human readable, may be empty),
        ``embed_url`` and ``filename`` (sanitized, ending in ``.pdf``).
    """
    if not url:
        return None

    match = _SCRIBD_URL_RE.search(str(url).strip())
    if not match:
        return None

    document_id = match.group(1)
    slug = unquote(match.group(2) or "")
    if slug.lower() == "content":  # /embeds/<id>/content has no title slug
        slug = ""

    title = re.sub(r"[-_]+", " ", slug).strip()
    filename_slug = slug or f"scribd-{document_id}"
    return {
        "document_id": document_id,
        "title": title,
        "embed_url": f"https://www.scribd.com/embeds/{document_id}/content",
        "filename": f"{sanitize_filename(filename_slug)}.pdf",
        "source_url": (
            f"https://www.scribd.com/document/{document_id}"
            + (f"/{match.group(2)}" if match.group(2) and slug else "")
        ),
    }


def extract_scribd_urls(text):
    """Extract every distinct Scribd document found in ``text``.

    Used for multi-link paste, drag-&-drop and .txt link files. Returns a
    list of :func:`parse_scribd_url` dicts deduplicated by document id,
    in first-seen order. The ``(Scribd)`` suffix some browsers append to
    dragged link text and surrounding prose are ignored naturally.
    """
    if not text:
        return []
    found = []
    seen_ids = set()
    for match in _SCRIBD_URL_RE.finditer(str(text)):
        parsed = parse_scribd_url(match.group(0))
        if not parsed or parsed["document_id"] in seen_ids:
            continue
        seen_ids.add(parsed["document_id"])
        # Keep a canonical source URL so callers can re-download later.
        parsed["source_url"] = (
            f"https://www.scribd.com/document/{parsed['document_id']}"
            + (f"/{match.group(2)}" if match.group(2) and match.group(2).lower() != "content" else "")
        )
        found.append(parsed)
    return found


def convert_scribd_link(url):
    """
    Convert a Scribd document URL to the embed/content URL.

    Args:
        url: Standard Scribd URL such as
            https://www.scribd.com/document/123456789/Document-Title
            or https://www.scribd.com/doc/123456789/Document-Title

    Returns:
        The embeddable content URL, or "Invalid Scribd URL" if no document id
        can be extracted.
    """
    parsed = parse_scribd_url(url)
    if not parsed:
        return "Invalid Scribd URL"

    return parsed["embed_url"]


def get_filename_from_url(url):
    """
    Build an output filename from the last URL path segment.

    Args:
        url: Scribd document URL.

    Returns:
        Sanitized filename ending in ".pdf".
    """
    parsed_info = parse_scribd_url(url)
    if parsed_info:
        return parsed_info["filename"]

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    last_segment = path.split("/")[-1] if path else "scribd_document"
    return f"{sanitize_filename(unquote(last_segment))}.pdf"


def configure_command_timeout(driver, timeout_seconds):
    """
    Increase the Selenium HTTP timeout used for ChromeDriver commands.

    Large image-heavy documents can spend minutes inside Page.printToPDF before
    ChromeDriver responds, so the default 120 second timeout is too small.
    """
    executor = getattr(driver, "command_executor", None)
    if executor is None:
        return

    client_config = getattr(executor, "client_config", None)
    if client_config is None:
        client_config = getattr(executor, "_client_config", None)

    if client_config is not None:
        client_config.timeout = timeout_seconds


def close_driver(driver):
    """Close Chrome without letting a wedged driver block the next item.

    ``driver.quit()`` is itself a ChromeDriver command. If Chrome has hung,
    an unbounded quit could strand a GUI queue after the download had already
    failed or been cancelled. Bound the request and, as a last resort,
    terminate the ChromeDriver service process.
    """
    if driver is None:
        return

    configure_command_timeout(driver, DRIVER_QUIT_TIMEOUT_SECONDS)
    try:
        driver.quit()
        return
    except Exception:
        pass

    process = getattr(getattr(driver, "service", None), "process", None)
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def hide_cookie_dialogs(driver):
    """Dismiss and remove common cookie, consent, and privacy banners."""
    driver.execute_script(
        """
        const closeButtonSelectors = [
            '[class*="cookie"] [class*="close"]',
            '[class*="cookie"] [class*="dismiss"]',
            '[class*="cookie"] button[aria-label*="close"]',
            '[class*="cookie"] button[aria-label*="Close"]',
            '[class*="consent"] [class*="close"]',
            '[class*="consent"] [class*="dismiss"]',
            '[class*="banner"] [class*="close"]',
            '[class*="banner"] [class*="dismiss"]',
            '[class*="notice"] [class*="close"]',
            '[class*="notice"] [class*="dismiss"]',
            'button[class*="close"]',
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            'button[aria-label="Dismiss"]',
            '[data-dismiss]',
            '[role="button"][class*="close"]'
        ];

        closeButtonSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((button) => button.click());
            } catch (error) {}
        });

        const cookieSelectors = [
            '[class*="cookie"]',
            '[class*="Cookie"]',
            '[class*="consent"]',
            '[class*="Consent"]',
            '[class*="gdpr"]',
            '[class*="GDPR"]',
            '[id*="cookie"]',
            '[id*="Cookie"]',
            '[id*="consent"]',
            '[id*="gdpr"]',
            '[class*="privacy-notice"]',
            '[class*="Privacy"]',
            '[class*="cookie-banner"]',
            '[class*="cookie-notice"]',
            '[class*="cookie-popup"]',
            '[class*="cookie-modal"]',
            '[class*="CookieConsent"]',
            '[class*="notice-banner"]',
            '.cc-window',
            '.cc-banner',
            '#onetrust-consent-sdk',
            '#onetrust-banner-sdk',
            '.evidon-banner',
            '.truste_box_overlay',
            '[class*="osano-cm"]',
            '[id*="osano"]'
        ];

        cookieSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((element) => element.remove());
            } catch (error) {}
        });

        // Inspect only plausible overlay containers. The previous version
        // called getComputedStyle(), getBoundingClientRect() and innerText
        // on every node in the document; on a several-hundred-page Scribd
        // DOM that could keep Chrome busy long enough to look like a hang
        // before the export had printed even one page.
        const overlayCandidates = document.querySelectorAll(
            'body > *, body > * > *, [role="dialog"], [aria-modal="true"]'
        );
        overlayCandidates.forEach((element) => {
            try {
                const style = getComputedStyle(element);
                if (style.position !== 'fixed' && style.position !== 'sticky') {
                    return;
                }

                const rect = element.getBoundingClientRect();
                if (rect.top >= 100 || rect.width === 0 || rect.height === 0) {
                    return;
                }

                const text = (element.textContent || '')
                    .slice(0, 1000)
                    .toLowerCase();
                if (
                    text.includes('cookie') ||
                    text.includes('privacy') ||
                    text.includes('consent') ||
                    text.includes('analytics') ||
                    text.includes('advertising') ||
                    text.includes('personalization')
                ) {
                    element.remove();
                }
            } catch (error) {}
        });
        """
    )


def scroll_through_pages(driver, scroll_delay_seconds):
    """
    Scroll through all detected pages until the page count stabilizes.

    Scribd lazily renders more page nodes while scrolling, so a single snapshot
    of "[class*='page']" is not always enough for long documents.
    """
    scrolled_count = 0
    stable_rounds = 0
    last_total_pages = -1

    while stable_rounds < 2:
        page_elements = driver.find_elements("css selector", "[class*='page']")
        total_pages = len(page_elements)

        if total_pages == 0:
            _log("No page elements were detected.")
            return 0

        if total_pages == last_total_pages:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_total_pages = total_pages

        if scrolled_count == 0:
            _log(f"Found {total_pages} pages, scrolling...")
        elif total_pages > scrolled_count:
            _log(f"Detected {total_pages} pages after lazy loading, continuing...")

        for index in range(scrolled_count, total_pages):
            _check_cancelled()
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
                page_elements[index],
            )
            time.sleep(scroll_delay_seconds)

            if (index + 1) % 10 == 0:
                _log(f"  Scrolled {index + 1}/{total_pages} pages...")

        scrolled_count = total_pages
        time.sleep(0.5)

    _log(f"All {scrolled_count} pages loaded.")
    return scrolled_count


def load_all_pages(driver):
    """Load Scribd pages directly, without simulating user scrolling."""
    page_count = driver.execute_script(
        """
        return window.docManager && window.docManager.pages
            ? Object.values(window.docManager.pages).filter(Boolean).length
            : 0;
        """
    )
    batch_count = max(
        1,
        (page_count + DEFAULT_PAGE_LOAD_CONCURRENCY - 1)
        // DEFAULT_PAGE_LOAD_CONCURRENCY,
    )
    document_timeout_seconds = (
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * batch_count
    )
    configure_command_timeout(driver, document_timeout_seconds + 10)
    driver.set_script_timeout(document_timeout_seconds + 10)

    result = driver.execute_async_script(
        """
        const concurrency = arguments[0];
        const timeoutMs = arguments[1];
        const documentTimeoutMs = arguments[2];
        const done = arguments[arguments.length - 1];
        const manager = window.docManager;

        if (!manager || !manager.pages) {
            done({supported: false});
            return;
        }

        const pages = Object.values(manager.pages).filter(Boolean);
        const pending = pages.filter((page) => !page.innerPageElem);
        const active = new Map();
        const failed = [];
        const startedAt = Date.now();
        let nextIndex = 0;
        let completed = pages.length - pending.length;
        let finished = false;

        function finish() {
            if (finished) {
                return;
            }

            finished = true;
            clearInterval(timer);

            pages.forEach((page) => {
                if (!page.innerPageElem) {
                    return;
                }

                try {
                    page.display();
                } catch (error) {}

                try {
                    page.turnOnImages();
                } catch (error) {}
            });

            done({
                supported: true,
                total: pages.length,
                loaded: pages.filter((page) => page.innerPageElem).length,
                failed,
                elapsedMs: Date.now() - startedAt
            });
        }

        function launchMore() {
            while (active.size < concurrency && nextIndex < pending.length) {
                const page = pending[nextIndex++];

                try {
                    if (!page.loadHasStarted) {
                        page.load();
                    }

                    active.set(page.pageNum, {
                        page,
                        startedAt: Date.now()
                    });
                } catch (error) {
                    failed.push({
                        pageNum: page.pageNum,
                        reason: String(error)
                    });
                }
            }

            if (completed + failed.length >= pages.length) {
                finish();
            }
        }

        const timer = setInterval(() => {
            for (const [pageNum, state] of active) {
                if (state.page.innerPageElem) {
                    active.delete(pageNum);
                    completed += 1;
                    continue;
                }

                if (Date.now() - state.startedAt >= timeoutMs) {
                    active.delete(pageNum);
                    failed.push({
                        pageNum,
                        reason: 'page load timed out'
                    });
                }
            }

            if (Date.now() - startedAt >= documentTimeoutMs) {
                for (const [pageNum] of active) {
                    failed.push({
                        pageNum,
                        reason: 'document load timed out'
                    });
                }
                active.clear();
                finish();
                return;
            }

            launchMore();
        }, 50);

        launchMore();
        """,
        DEFAULT_PAGE_LOAD_CONCURRENCY,
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * 1000,
        document_timeout_seconds * 1000,
    )

    if not result.get("supported"):
        _log("Direct page loader unavailable; using scrolling fallback.")
        return scroll_through_pages(driver, DEFAULT_SCROLL_DELAY_SECONDS)

    total_pages = result["total"]
    loaded_pages = result["loaded"]
    elapsed_seconds = result["elapsedMs"] / 1000

    _log(
        f"Loaded {loaded_pages}/{total_pages} pages directly "
        f"in {elapsed_seconds:.2f}s "
        f"(concurrency: {DEFAULT_PAGE_LOAD_CONCURRENCY})."
    )

    if result["failed"]:
        failed_pages = ", ".join(
            str(item["pageNum"])
            for item in result["failed"]
        )
        raise RuntimeError(
            "Failed to load Scribd page(s): "
            f"{failed_pages}"
        )

    return loaded_pages


def prepare_document_for_print(driver):
    """
    Remove UI chrome and make the scroll containers printable.

    The old version removed the .document_scroller class entirely, which can
    break descendant CSS needed by math- and font-heavy documents. We keep the
    class and only override the few layout properties that interfere with print.
    """
    result = driver.execute_script(
        """
        const removed = { toolbarTop: false, toolbarBottom: false, containers: 0 };

        const toolbarTop = document.querySelector('.toolbar_top');
        if (toolbarTop) {
            toolbarTop.remove();
            removed.toolbarTop = true;
        }

        const toolbarBottom = document.querySelector('.toolbar_bottom');
        if (toolbarBottom) {
            toolbarBottom.remove();
            removed.toolbarBottom = true;
        }

        document.querySelectorAll('.document_scroller').forEach((element) => {
            element.setAttribute('data-scribd-print-root', 'true');
            element.style.position = 'static';
            element.style.top = 'auto';
            element.style.bottom = 'auto';
            element.style.left = 'auto';
            element.style.right = 'auto';
            element.style.overflow = 'visible';
            element.style.maxHeight = 'none';
            element.style.height = 'auto';
            element.style.margin = '0';
            element.style.padding = '0';
            removed.containers += 1;
        });

        return removed;
        """
    )

    if result["toolbarTop"]:
        _log("Top toolbar removed.")
    if result["toolbarBottom"]:
        _log("Bottom toolbar removed.")

    _log(f"Adjusted {result['containers']} scroll containers for print.")


def inject_print_styles(driver):
    """Install conservative print CSS without hiding Scribd document content."""
    driver.execute_script(
        """
        const existing = document.getElementById('scribd-print-styles');
        if (existing) {
            existing.remove();
        }

        const style = document.createElement('style');
        style.id = 'scribd-print-styles';
        style.textContent = `
            [class*="cookie"],
            [class*="Cookie"],
            [class*="consent"],
            [class*="Consent"],
            [class*="gdpr"],
            [class*="privacy-notice"],
            [class*="notice-banner"],
            [id*="cookie"],
            [id*="consent"],
            [class*="osano-cm"],
            [id*="osano"] {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                height: 0 !important;
                overflow: hidden !important;
            }

            [data-scribd-print-root="true"],
            .document_scroller {
                position: static !important;
                top: auto !important;
                right: auto !important;
                bottom: auto !important;
                left: auto !important;
                overflow: visible !important;
                height: auto !important;
                max-height: none !important;
                margin: 0 !important;
                padding: 0 !important;
            }

            @media print {
                html,
                body {
                    margin: 0 !important;
                    padding: 0 !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                }

                .toolbar_top,
                .toolbar_bottom {
                    display: none !important;
                }

                [data-scribd-print-root="true"],
                .document_scroller {
                    position: static !important;
                    top: auto !important;
                    right: auto !important;
                    bottom: auto !important;
                    left: auto !important;
                    overflow: visible !important;
                    height: auto !important;
                    max-height: none !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }

                mjx-container,
                .MathJax,
                .katex,
                math,
                svg {
                    visibility: visible !important;
                    overflow: visible !important;
                }
            }
        `;

        document.head.appendChild(style);
        """
    )

    _log("Print CSS injected.")


def wait_for_render_stability(driver, timeout_seconds):
    """
    Wait for fonts, images, and page dimensions to settle before printing.

    This lowers the risk of exporting before math glyphs, SVG content, or web
    fonts finish rendering.
    """
    driver.set_script_timeout(timeout_seconds + 5)

    try:
        result = driver.execute_async_script(
            """
            const settleBudgetMs = arguments[0];
            const done = arguments[arguments.length - 1];
            const start = performance.now();
            let stableTicks = 0;
            let lastSample = '';

            function sample() {
                const pages = Array.from(document.querySelectorAll("[class*='page']"));
                const heights = pages.slice(0, 12).map((element) =>
                    Math.round(element.getBoundingClientRect().height)
                );
                const pendingImages = Array.from(document.images || []).filter(
                    (image) => !image.complete
                ).length;
                return JSON.stringify({
                    pageCount: pages.length,
                    heights,
                    pendingImages
                });
            }

            function finish(timedOut) {
                done({
                    timedOut,
                    sample: lastSample || sample()
                });
            }

            function tick() {
                lastSample = sample();
                const parsed = JSON.parse(lastSample);
                const isBusy = parsed.pendingImages > 0;

                if (!isBusy && lastSample === window.__scribdLastRenderSample) {
                    stableTicks += 1;
                } else {
                    stableTicks = 0;
                }

                window.__scribdLastRenderSample = lastSample;

                if (stableTicks >= 2) {
                    finish(false);
                    return;
                }

                if (performance.now() - start >= settleBudgetMs) {
                    finish(true);
                    return;
                }

                requestAnimationFrame(() => setTimeout(tick, 200));
            }

            const fontsReady = document.fonts && document.fonts.ready
                ? document.fonts.ready.catch(() => undefined)
                : Promise.resolve();

            fontsReady.finally(() => {
                requestAnimationFrame(() => setTimeout(tick, 200));
            });
            """,
            int(timeout_seconds * 1000),
        )
    except WebDriverException as error:
        _log(f"Render settle check failed; continuing with best effort: {error}")
        return

    if result.get("timedOut"):
        _log("Render settle reached its time budget; continuing with best effort.")
    else:
        _log("Document render settled before export.")


def detect_document_paper_size(driver):
    """
    Infer a paper size from the first rendered Scribd page.

    Scribd pages often render as absolutely positioned HTML at a fixed CSS size.
    Using that page box as the print sheet size avoids splitting one Scribd page
    across multiple PDF pages.
    """
    paper_size = driver.execute_script(
        """
        const candidates = [
            '.outer_page',
            '.newpage',
            '.outer_page_container',
            "[class*='page']"
        ];

        for (const selector of candidates) {
            const element = document.querySelector(selector);
            if (!element) {
                continue;
            }

            const rect = element.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return {
                    widthInches: rect.width / 96,
                    heightInches: rect.height / 96,
                    selector
                };
            }
        }

        return null;
        """
    )

    if not paper_size:
        return {
            "widthInches": DEFAULT_PAPER_WIDTH_INCHES,
            "heightInches": DEFAULT_PAPER_HEIGHT_INCHES,
            "selector": "default",
        }

    return {
        "widthInches": max(1.0, round(paper_size["widthInches"], 3)),
        "heightInches": max(1.0, round(paper_size["heightInches"], 3)),
        "selector": paper_size["selector"],
    }


def read_pdf_stream_to_file(driver, stream_handle, filename):
    """Read a streamed CDP PDF result and write it to disk in chunks."""
    try:
        with open(filename, "wb") as file_handle:
            while True:
                chunk = driver.execute_cdp_cmd(
                    "IO.read",
                    {
                        "handle": stream_handle,
                        "size": PDF_STREAM_CHUNK_SIZE,
                    },
                )

                data = chunk.get("data", "")
                if not data and chunk.get("eof"):
                    break

                if chunk.get("base64Encoded"):
                    file_handle.write(base64.b64decode(data))
                else:
                    file_handle.write(data.encode("utf-8"))

                if chunk.get("eof"):
                    break
    finally:
        driver.execute_cdp_cmd("IO.close", {"handle": stream_handle})


def load_page_batch(
    driver,
    page_numbers,
    progress_current=None,
    progress_total=None,
):
    """Load one bounded batch of page DOM and image assets.

    Python polls Chrome with short synchronous probes instead of handing the
    whole wait to one long ``execute_async_script`` call. That distinction
    matters for queue downloads: cancellation is checked between probes, the
    GUI receives "x/y pages ready" updates while a large document loads, a
    transient renderer hiccup can be retried, and a wedged batch reaches the
    configured deadline instead of leaving the first queue item at 0%.
    """
    page_numbers = [int(page_number) for page_number in page_numbers]
    if not page_numbers:
        return

    page_load_timeout = max(
        1,
        int(_override("page_load_timeout", DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS)),
    )
    try:
        restore_timeout = max(
            30,
            int(_override("cdp_timeout", DEFAULT_CDP_TIMEOUT_SECONDS)),
        )
    except (TypeError, ValueError):
        restore_timeout = DEFAULT_CDP_TIMEOUT_SECONDS
    probe_timeout = min(
        BATCH_PROBE_TIMEOUT_SECONDS,
        max(5, page_load_timeout),
    )

    configure_command_timeout(driver, probe_timeout)
    try:
        set_script_timeout = getattr(driver, "set_script_timeout", None)
        if callable(set_script_timeout):
            try:
                set_script_timeout(probe_timeout)
            except Exception:
                pass

        started_at = time.monotonic()
        deadline = started_at + page_load_timeout
        last_status = None
        last_error = None
        stable_ready_polls = 0
        last_progress_at = 0.0
        last_progress_ready = -1
        last_log_at = started_at

        while True:
            _check_cancelled()
            now = time.monotonic()
            if now >= deadline:
                break

            try:
                status = driver.execute_script(
                    """
                    const pageNumbers = arguments[0];
                    const manager = window.docManager;

                    if (!manager || !manager.pages) {
                        return {
                            supported: false,
                            total: pageNumbers.length,
                            ready: 0,
                            totalImages: 0,
                            pendingImages: 0,
                            brokenImages: 0,
                            pages: []
                        };
                    }

                    const nowMs = Date.now();
                    const details = [];
                    let ready = 0;
                    let totalImages = 0;
                    let pendingImages = 0;
                    let brokenImages = 0;

                    function lazySource(image) {
                        return (
                            image.getAttribute('data-src') ||
                            image.getAttribute('data-lazy-src') ||
                            image.getAttribute('data-original') ||
                            ''
                        );
                    }

                    for (const pageNum of pageNumbers) {
                        const page = manager.pages[pageNum];
                        const detail = {
                            pageNum,
                            ready: false,
                            reason: '',
                            images: 0,
                            pendingImages: 0,
                            brokenImages: 0,
                            unstartedImages: 0
                        };

                        if (!page) {
                            detail.reason = 'page object missing';
                            details.push(detail);
                            continue;
                        }

                        try {
                            if (
                                !page.innerPageElem &&
                                !page.loadHasStarted &&
                                typeof page.load === 'function'
                            ) {
                                page.load();
                            }
                        } catch (error) {
                            detail.reason = `load failed: ${error}`;
                        }

                        if (!page.innerPageElem) {
                            if (!detail.reason) {
                                detail.reason = 'page content not loaded yet';
                            }
                            details.push(detail);
                            continue;
                        }

                        try {
                            // Call Scribd's render/image hooks once for each
                            // inner element. Calling them on every 50 ms tick
                            // (the old async implementation) could repeatedly
                            // reset image requests on some documents.
                            if (
                                page.__scribdDownloaderDisplayedElem !==
                                page.innerPageElem
                            ) {
                                if (typeof page.display === 'function') {
                                    page.display();
                                }
                                page.__scribdDownloaderDisplayedElem =
                                    page.innerPageElem;
                                page.__scribdDownloaderImagesElem = null;
                            }

                            if (
                                page.__scribdDownloaderImagesElem !==
                                    page.innerPageElem &&
                                !page._imagesTurnedOn &&
                                typeof page.turnOnImages === 'function'
                            ) {
                                page.turnOnImages();
                                page.__scribdDownloaderImagesElem =
                                    page.innerPageElem;
                                page.__scribdDownloaderImagesAttemptAt = nowMs;
                            }
                        } catch (error) {
                            detail.reason = `display failed: ${error}`;
                            details.push(detail);
                            continue;
                        }

                        let images = Array.from(
                            page.innerPageElem.querySelectorAll('img')
                        );
                        let unstarted = images.filter((image) => (
                            !(image.currentSrc || image.src) &&
                            lazySource(image)
                        ));

                        // If Scribd left lazy images unstarted, retry its
                        // hook occasionally (not on every poll, which could
                        // restart requests that are already in flight).
                        if (
                            unstarted.length &&
                            typeof page.turnOnImages === 'function' &&
                            nowMs -
                                (page.__scribdDownloaderImagesAttemptAt || 0) >=
                                2000
                        ) {
                            try {
                                page.turnOnImages();
                                page.__scribdDownloaderImagesElem =
                                    page.innerPageElem;
                                page.__scribdDownloaderImagesAttemptAt = nowMs;
                                images = Array.from(
                                    page.innerPageElem.querySelectorAll('img')
                                );
                                unstarted = images.filter((image) => (
                                    !(image.currentSrc || image.src) &&
                                    lazySource(image)
                                ));
                            } catch (error) {
                                detail.reason = `image load failed: ${error}`;
                            }
                        }

                        detail.images = images.length;
                        detail.unstartedImages = unstarted.length;
                        for (const image of images) {
                            if (!image.complete) {
                                detail.pendingImages += 1;
                            } else if (
                                image.naturalWidth === 0 &&
                                (image.currentSrc || image.src)
                            ) {
                                detail.brokenImages += 1;
                            }
                        }

                        totalImages += detail.images;
                        pendingImages += detail.pendingImages;
                        brokenImages += detail.brokenImages;

                        detail.ready =
                            detail.pendingImages === 0 &&
                            detail.unstartedImages === 0 &&
                            !detail.reason;
                        if (detail.ready) {
                            ready += 1;
                        } else if (!detail.reason) {
                            detail.reason = detail.unstartedImages
                                ? `${detail.unstartedImages} image(s) have not started loading`
                                : `${detail.pendingImages} image(s) still loading`;
                        }
                        details.push(detail);
                    }

                    return {
                        supported: true,
                        total: pageNumbers.length,
                        ready,
                        totalImages,
                        pendingImages,
                        brokenImages,
                        pages: details
                    };
                    """,
                    page_numbers,
                ) or {}
                last_error = None
            except WebDriverException as error:
                last_error = error
                message = str(error).lower()
                fatal_markers = (
                    "invalid session id",
                    "session deleted",
                    "chrome not reachable",
                    "target window already closed",
                    "no such window",
                    "not connected to devtools",
                )
                if any(marker in message for marker in fatal_markers):
                    raise RuntimeError(
                        "Chrome stopped responding while loading pages "
                        f"{page_numbers[0]}-{page_numbers[-1]}: {error}"
                    ) from error
                status = None

            if status is not None:
                if not status.get("supported"):
                    raise RuntimeError(
                        "Scribd direct page loader is unavailable."
                    )

                last_status = status
                try:
                    ready = int(status.get("ready") or 0)
                except (TypeError, ValueError):
                    ready = 0
                try:
                    total = int(status.get("total") or len(page_numbers))
                except (TypeError, ValueError):
                    total = len(page_numbers)
                try:
                    pending_images = int(status.get("pendingImages") or 0)
                except (TypeError, ValueError):
                    pending_images = 0
                try:
                    total_images = int(status.get("totalImages") or 0)
                except (TypeError, ValueError):
                    total_images = 0

                if ready >= total:
                    stable_ready_polls += 1
                    # Two consecutive ready probes prevent printing during
                    # the brief moment when Scribd swaps a placeholder image
                    # for the real page image. Text-only pages need no wait.
                    if total_images == 0 or stable_ready_polls >= 2:
                        return
                else:
                    stable_ready_polls = 0

                if (
                    ready != last_progress_ready
                    or now - last_progress_at >= 2
                ):
                    elapsed = int(now - started_at)
                    message = (
                        f"Loading pages {page_numbers[0]}-{page_numbers[-1]}: "
                        f"{ready}/{total} ready"
                    )
                    if pending_images:
                        message += f" · {pending_images} image(s) loading"
                    if elapsed >= 2:
                        message += f" · {elapsed}s"
                    _progress(
                        "load",
                        progress_current,
                        progress_total,
                        message,
                    )
                    last_progress_at = now
                    last_progress_ready = ready

                if ready < total and now - last_log_at >= 15:
                    _log(
                        "  Still loading page batch "
                        f"{page_numbers[0]}-{page_numbers[-1]}: "
                        f"{ready}/{total} ready, "
                        f"{pending_images} image(s) pending "
                        f"({int(now - started_at)}s)."
                    )
                    last_log_at = now

            time.sleep(BATCH_POLL_INTERVAL_SECONDS)

        details = []
        if last_status:
            for page in last_status.get("pages") or []:
                if not page.get("ready"):
                    reason = page.get("reason") or "page did not finish loading"
                    details.append(f"{page.get('pageNum')} ({reason})")
        if not details and last_error is not None:
            details.append(f"browser did not respond ({last_error})")
        if not details:
            details.append("pages did not finish loading")

        raise RuntimeError(
            "Failed to load Scribd page(s) after "
            f"{page_load_timeout}s: {', '.join(details)}"
        )
    finally:
        configure_command_timeout(driver, restore_timeout)
        set_script_timeout = getattr(driver, "set_script_timeout", None)
        if callable(set_script_timeout):
            try:
                set_script_timeout(page_load_timeout + 10)
            except Exception:
                pass


def release_page_batch(driver, page_numbers):
    """Release printed page DOM and image resources from Chrome."""
    configure_command_timeout(driver, BATCH_PROBE_TIMEOUT_SECONDS)
    driver.execute_script(
        """
        const manager = window.docManager;
        if (!manager || !manager.pages) {
            return;
        }

        for (const pageNum of arguments[0]) {
            const page = manager.pages[pageNum];
            if (!page) {
                continue;
            }

            try {
                page.remove();
            } catch (error) {
                const container = document.getElementById(`outer_page_${pageNum}`);
                if (container) {
                    const inner = container.querySelector('.newpage');
                    if (inner) {
                        inner.remove();
                    }
                }
            }
        }
        """,
        list(page_numbers),
    )

    try:
        driver.execute_cdp_cmd("HeapProfiler.collectGarbage", {})
    except WebDriverException:
        pass


def save_pdf_pages_individually(
    driver,
    filename,
    timeout_seconds=DEFAULT_CDP_TIMEOUT_SECONDS,
    batch_size=None,
):
    if batch_size is None:
        batch_size = _override("batch_size", DEFAULT_EXPORT_BATCH_SIZE)
    batch_size = max(1, int(batch_size))
    from pypdf import PdfReader, PdfWriter

    page_load_timeout = _override(
        "page_load_timeout", DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS
    )
    # The export timeout is a ceiling for the whole Chrome session's slower
    # commands, but one page print should not be allowed to sit at the same
    # percentage for ten minutes. Bound geometry/print commands to the page
    # timeout (at least a minute), unless the user configured a lower CDP
    # ceiling explicitly.
    print_timeout = max(
        30,
        min(int(timeout_seconds), max(60, int(page_load_timeout))),
    )
    configure_command_timeout(
        driver,
        print_timeout,
    )

    page_count = driver.execute_script(
        """
        return document.querySelectorAll(
            '.outer_page'
        ).length;
        """
    )

    if page_count <= 0:
        raise RuntimeError(
            "No .outer_page elements found."
        )

    _log(
        f"Exporting {page_count} "
        "document pages in bounded batches "
        f"of {batch_size}..."
    )
    _progress("export", 0, page_count, f"Exporting {page_count} pages...")

    spool = tempfile.TemporaryDirectory(
        prefix="scribd-pdf-pages-"
    )
    page_files = []

    try:
        for index in range(page_count):
            _check_cancelled()
            if index % batch_size == 0:
                batch_end = min(
                    page_count,
                    index + batch_size,
                )
                batch_page_numbers = list(
                    range(index + 1, batch_end + 1)
                )
                _log(
                    f"  Loading page batch "
                    f"{index + 1}-{batch_end}/{page_count}..."
                )
                _progress(
                    "load",
                    index,
                    page_count,
                    f"Loading pages {index + 1}-{batch_end} of {page_count}…",
                )
                load_page_batch(
                    driver,
                    batch_page_numbers,
                    progress_current=index,
                    progress_total=page_count,
                )
                configure_command_timeout(driver, print_timeout)
                _log(
                    f"  Loaded page batch "
                    f"{index + 1}-{batch_end}/{page_count}."
                )

            _progress(
                "export",
                index,
                page_count,
                f"Preparing page {index + 1} of {page_count}…",
            )
            page_info = driver.execute_script(
                """
                const targetIndex = arguments[0];

                const pages = Array.from(
                    document.querySelectorAll(
                        '.outer_page'
                    )
                );

                const target = pages[targetIndex];

                if (!target) {
                    return null;
                }

                /*
                 * Remove previous isolated-print style.
                 */
                const oldStyle = document.getElementById(
                    'isolated-page-print-style'
                );

                if (oldStyle) {
                    oldStyle.remove();
                }

                /*
                 * Restore all pages before measuring.
                 */
                pages.forEach((page) => {
                    page.style.removeProperty('display');
                    page.style.removeProperty('visibility');
                    page.style.removeProperty('position');
                    page.style.removeProperty('top');
                    page.style.removeProperty('left');
                    page.style.removeProperty('right');
                    page.style.removeProperty('bottom');
                    page.style.removeProperty('margin');
                    page.style.removeProperty('break-after');
                    page.style.removeProperty('page-break-after');
                    page.style.removeProperty('break-before');
                    page.style.removeProperty('page-break-before');
                });

                const rect = target.getBoundingClientRect();

                const width = Math.ceil(rect.width);
                const height = Math.ceil(rect.height);

                /*
                 * Mark the target instead of relying on nth-child.
                 */
                pages.forEach((page) => {
                    page.removeAttribute(
                        'data-export-target'
                    );
                });

                target.setAttribute(
                    'data-export-target',
                    'true'
                );

                const style = document.createElement(
                    'style'
                );

                style.id = 'isolated-page-print-style';

                style.textContent = `
                    @page {
                        size: ${width}px ${height}px;
                        margin: 0;
                    }

                    @media print {
                        html,
                        body {
                            width: ${width}px !important;
                            height: ${height}px !important;
                            min-width: ${width}px !important;
                            min-height: ${height}px !important;
                            max-width: ${width}px !important;
                            max-height: ${height}px !important;

                            margin: 0 !important;
                            padding: 0 !important;

                            overflow: hidden !important;

                            -webkit-print-color-adjust:
                                exact !important;

                            print-color-adjust:
                                exact !important;
                        }

                        .outer_page {
                            display: none !important;
                        }

                        .outer_page[
                            data-export-target="true"
                        ] {
                            display: block !important;
                            visibility: visible !important;

                            position: absolute !important;

                            top: 0 !important;
                            left: 0 !important;
                            right: auto !important;
                            bottom: auto !important;

                            width: ${width}px !important;
                            height: ${height}px !important;

                            min-width: 0 !important;
                            min-height: 0 !important;

                            max-width: none !important;
                            max-height: none !important;

                            margin: 0 !important;
                            padding: 0 !important;

                            transform: none !important;

                            break-before: auto !important;
                            break-after: auto !important;
                            break-inside: auto !important;

                            page-break-before:
                                auto !important;

                            page-break-after:
                                auto !important;

                            page-break-inside:
                                auto !important;

                            overflow: hidden !important;
                        }
                    }
                `;

                document.head.appendChild(style);

                return {
                    width,
                    height
                };
                """,
                index,
            )

            if not page_info:
                _log(
                    f"  Skipping page "
                    f"{index + 1}: element missing"
                )
                _progress("export", index + 1, page_count,
                          f"Page {index + 1} of {page_count} (skipped)")
                continue

            width_px = int(page_info["width"])
            height_px = int(page_info["height"])

            if width_px <= 0 or height_px <= 0:
                _log(
                    f"  Skipping page {index + 1}: "
                    f"invalid geometry "
                    f"{width_px}x{height_px}"
                )
                _progress("export", index + 1, page_count,
                          f"Page {index + 1} of {page_count} (skipped)")
                continue

            width_inches = width_px / 96.0
            height_inches = height_px / 96.0

            _log(
                f"  Page {index + 1}/{page_count} "
                f"{width_px}x{height_px}px "
                f"-> "
                f'{width_inches:.3f}"'
                f'x{height_inches:.3f}"'
            )

            _progress(
                "export",
                index,
                page_count,
                f"Printing page {index + 1} of {page_count}…",
            )
            driver.execute_cdp_cmd(
                "Emulation.setEmulatedMedia",
                {
                    "media": "print",
                },
            )

            result = driver.execute_cdp_cmd(
                "Page.printToPDF",
                {
                    "landscape": False,
                    "displayHeaderFooter": False,
                    "printBackground": True,

                    "scale": 1,

                    "paperWidth": width_inches,
                    "paperHeight": height_inches,

                    "marginTop": 0,
                    "marginBottom": 0,
                    "marginLeft": 0,
                    "marginRight": 0,

           
                    "preferCSSPageSize": True,

                
                    "pageRanges": "1",

                    "transferMode": "ReturnAsBase64",
                },
            )

            pdf_bytes = base64.b64decode(
                result["data"]
            )

            page_reader = PdfReader(BytesIO(pdf_bytes))
            if len(page_reader.pages) != 1:
                raise RuntimeError(
                    f"Document page "
                    f"{index + 1} produced "
                    f"{len(page_reader.pages)} "
                    "PDF sheets; expected exactly 1."
                )

            page_path = os.path.join(
                spool.name,
                f"page-{index + 1:08d}.pdf",
            )
            with open(page_path, "wb") as page_handle:
                page_handle.write(pdf_bytes)
            page_files.append(page_path)

            _log(
                f"    OK: exactly 1 PDF sheet"
            )
            _progress(
                "export",
                index + 1,
                page_count,
                f"Page {index + 1} of {page_count}",
            )

            is_batch_end = (
                (index + 1) % batch_size == 0
                or index + 1 == page_count
            )
            if is_batch_end:
                release_page_batch(
                    driver,
                    batch_page_numbers,
                )

        if not page_files:
            raise RuntimeError(
                "No valid document pages "
                "were exported."
            )

        _check_cancelled()
        _progress(
            "merge",
            len(page_files),
            len(page_files),
            f"Merging {len(page_files)} pages into one PDF...",
        )
        _log(
            f"Merging {len(page_files)} "
            "disk-spooled PDF pages..."
        )
        writer = PdfWriter()
        try:
            for page_path in page_files:
                writer.append(page_path)
            with open(filename, "wb") as output_handle:
                writer.write(output_handle)
        finally:
            writer.close()

    finally:
        spool.cleanup()

    return os.path.abspath(filename)

def wait_for_document_pages(driver, timeout_seconds):
    """Wait until Scribd page containers exist and their count is stable.

    Returns the page count. Raises a clear ``RuntimeError`` instead of
    leaving callers sitting at an unexplained 0% when Scribd serves a bot
    check, a removed document, or a page that never renders.
    """
    timeout_seconds = max(10, int(timeout_seconds))
    deadline = time.monotonic() + timeout_seconds
    started_at = time.monotonic()
    last_count = -1
    stable_polls = 0
    last_snapshot = {}

    while time.monotonic() < deadline:
        _check_cancelled()
        try:
            snapshot = driver.execute_script(
                """
                const managerPages = window.docManager && window.docManager.pages
                    ? Object.values(window.docManager.pages).filter(Boolean).length
                    : 0;
                return {
                    outerPages: document.querySelectorAll('.outer_page').length,
                    managerPages,
                    readyState: document.readyState,
                    title: document.title || ''
                };
                """
            ) or {}
        except WebDriverException:
            # DOMContentLoaded/eager loading can briefly leave the page
            # between execution contexts while Scribd finishes routing.
            snapshot = {}
        last_snapshot = snapshot

        try:
            count = int(snapshot.get("outerPages") or 0)
        except (TypeError, ValueError):
            count = 0
        try:
            manager_count = int(snapshot.get("managerPages") or 0)
        except (TypeError, ValueError):
            manager_count = 0

        if count > 0 and count == last_count:
            stable_polls += 1
        elif count > 0:
            stable_polls = 0
            last_count = count
        else:
            stable_polls = 0
            last_count = count

        # Two identical polls (~1 second) are enough for the initial DOM;
        # if docManager already agrees, one stable poll is sufficient.
        if count > 0 and (stable_polls >= 2 or manager_count == count):
            return count

        _progress(
            "load",
            message=(
                "Waiting for Scribd to render the document pages… "
                f"({int(time.monotonic() - started_at)}s)"
            ),
        )
        time.sleep(0.5)

    title = str(last_snapshot.get("title") or "").strip()
    ready_state = str(last_snapshot.get("readyState") or "unknown")
    suffix = f" Page title: {title}." if title else ""
    raise RuntimeError(
        "Timed out after "
        f"{timeout_seconds}s waiting for Scribd pages to appear "
        f"(browser readyState: {ready_state}).{suffix} "
        "The document may be blocked by a browser check, removed, private, "
        "or the connection is too slow. Enable “Show Chrome window” in "
        "Settings to see what Scribd is displaying."
    )


def download_document(
    url,
    output_path=None,
    *,
    on_log=None,
    on_progress=None,
    cancel_event=None,
    headless=None,
    batch_size=None,
    page_load_timeout=None,
    cdp_timeout=None,
):
    """Download one Scribd document as a PDF. Used by both CLI and GUI.

    Args:
        url: Scribd document/doc/embeds URL.
        output_path: Destination PDF path. Defaults to the auto filename
            derived from the URL, in the current directory (CLI behaviour).
        on_log: Optional ``callable(str)`` receiving every log line.
        on_progress: Optional ``callable(dict)`` receiving
            ``{"stage", "current", "total", "message"}`` updates where stage
            is one of ``connect``, ``load``, ``export``, ``merge``, ``done``.
        cancel_event: Optional ``threading.Event``; when set, the export
            stops between pages and raises :class:`DownloadCancelledError`.
        headless: ``True``/``False`` to force Chrome mode, ``None`` keeps
            the ``SCRIBD_HEADLESS`` environment default.
        batch_size: Pages kept fully loaded in Chrome at once
            (``SCRIBD_EXPORT_BATCH_SIZE`` default when ``None``).
        page_load_timeout: Per-batch page load timeout in seconds
            (``SCRIBD_PAGE_LOAD_TIMEOUT`` default when ``None``).
        cdp_timeout: ChromeDriver command timeout in seconds
            (``SCRIBD_CDP_TIMEOUT`` default when ``None``).

    Returns:
        Absolute path of the saved PDF.

    Raises:
        ValueError: The URL is not a valid Scribd document URL.
        DownloadCancelledError: ``cancel_event`` was set during export.
        RuntimeError / WebDriverException: The export failed.
    """
    parsed = parse_scribd_url(url)
    if not parsed:
        raise ValueError(
            "Please provide a valid Scribd document URL, e.g. "
            "https://www.scribd.com/document/123456789/Document-Title"
        )

    converted_url = parsed["embed_url"]
    if output_path:
        output_path = os.path.abspath(str(output_path))
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    else:
        output_path = os.path.abspath(parsed["filename"])

    # Register per-download callbacks/options on this thread (see _log etc.).
    _run_state.on_log = on_log
    _run_state.on_progress = on_progress
    _run_state.cancel_event = cancel_event
    _run_state.batch_size = batch_size
    effective_page_load_timeout = (
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS
        if page_load_timeout is None
        else max(10, int(page_load_timeout))
    )
    _run_state.page_load_timeout = effective_page_load_timeout
    effective_cdp_timeout = (
        DEFAULT_CDP_TIMEOUT_SECONDS
        if cdp_timeout is None
        else max(30, int(cdp_timeout))
    )
    _run_state.cdp_timeout = effective_cdp_timeout

    try:
        _log(f"Link embed: {converted_url}")
        _log(f"Output filename: {os.path.basename(output_path)}")

        with tempfile.TemporaryDirectory(
            prefix="scribd-chrome-profile-"
        ) as runtime_profile_dir:
            driver = None
            try:
                _check_cancelled()
                _progress("connect", message="Starting Chrome...")
                _log("\nStarting Chrome browser...")

                options = build_chrome_options(
                    runtime_profile_dir, headless=headless
                )
                _log(
                    "Launching ChromeDriver/Chrome (the first launch can "
                    "take a while if ChromeDriver needs downloading)..."
                )
                # Selenium captures socket.getdefaulttimeout() when its HTTP
                # client is created. Give session creation the same finite
                # ceiling as later CDP commands instead of allowing a
                # network/driver hiccup to block this worker forever.
                previous_socket_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(effective_cdp_timeout)
                try:
                    driver = webdriver.Chrome(options=options)
                finally:
                    socket.setdefaulttimeout(previous_socket_timeout)
                _log("Chrome started.")

                configure_command_timeout(driver, effective_cdp_timeout)
                driver.set_page_load_timeout(effective_page_load_timeout)
                driver.set_script_timeout(effective_page_load_timeout + 10)

                _check_cancelled()
                _progress("connect", message="Opening document…")
                try:
                    driver.get(converted_url)
                except TimeoutException:
                    # The DOM often becomes usable even when a tracker or ad
                    # request makes Selenium's navigation wait time out.
                    _log(
                        "Browser navigation timed out; checking whether "
                        "the document rendered anyway..."
                    )
                    try:
                        driver.execute_script("window.stop();")
                    except WebDriverException:
                        pass

                _check_cancelled()
                _progress(
                    "load",
                    message="Waiting for Scribd to render the document pages…",
                )
                total_pages = wait_for_document_pages(
                    driver, effective_page_load_timeout
                )

                hide_cookie_dialogs(driver)
                _log("Cookie dialogs hidden.")

                _log(f"Detected {total_pages} pages.")
                _progress(
                    "load",
                    0,
                    total_pages,
                    f"Preparing {total_pages} pages...",
                )

                prepare_document_for_print(driver)
                inject_print_styles(driver)

                _log(f"\nSaving PDF as: {os.path.basename(output_path)}")
                _log("  Export mode: Individual document pages")
                _log("  Margins: None")
                _log("  Headers/Footers: Disabled")
                _log(
                    "  ChromeDriver command timeout: "
                    f"{effective_cdp_timeout}s"
                )

                driver.execute_script("window.scrollTo(0, 0)")

                saved_path = save_pdf_pages_individually(
                    driver,
                    output_path,
                    timeout_seconds=effective_cdp_timeout,
                )

                if not saved_path:
                    raise RuntimeError("PDF export failed.")

                _progress(
                    "done",
                    total_pages,
                    total_pages,
                    "PDF saved successfully.",
                )
                _log(f"PDF saved successfully to: {saved_path}")
                return saved_path

            finally:
                if driver is not None:
                    close_driver(driver)
                    _log("Browser closed.")
    finally:
        # Never leak one download's callbacks into the next one.
        _run_state.on_log = None
        _run_state.on_progress = None
        _run_state.cancel_event = None
        _run_state.batch_size = None
        _run_state.page_load_timeout = None
        _run_state.cdp_timeout = None


def main():
    """Run the exporter interactively (command-line interface)."""
    input_url = input("Input link Scribd: ").strip()

    try:
        download_document(input_url)
    except ValueError:
        # Print the same friendly hints the CLI has always shown.
        print(f"Link embed: {convert_scribd_link(input_url)}")
        print(f"Output filename: {get_filename_from_url(input_url)}")
        print("Error: Please provide a valid Scribd document URL")
        print(
            "Example: "
            "https://www.scribd.com/document/"
            "123456789/Document-Title"
        )
        print(
            "Example: "
            "https://www.scribd.com/doc/"
            "123456789/Document-Title"
        )
        raise SystemExit(1)
    except (RuntimeError, WebDriverException) as error:
        print(f"Export failed: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
    

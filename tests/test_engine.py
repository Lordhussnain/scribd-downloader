"""Unit tests for the engine helpers used by the CLI and the GUI.

Run with:  python -m unittest discover -s tests -v
No browser or network is needed.
"""

import importlib.util
import sys
import threading
import unittest
from unittest import mock
from pathlib import Path

ENGINE_PATH = Path(__file__).resolve().parent.parent / "scribd-downloader.py"
spec = importlib.util.spec_from_file_location("scribd_engine", ENGINE_PATH)
engine = importlib.util.module_from_spec(spec)
sys.modules["scribd_engine"] = engine
spec.loader.exec_module(engine)


class ParseScribdUrlTests(unittest.TestCase):
    def test_document_url(self):
        info = engine.parse_scribd_url(
            "https://www.scribd.com/document/123456789/Document-Title"
        )
        self.assertEqual(info["document_id"], "123456789")
        self.assertEqual(info["title"], "Document Title")
        self.assertEqual(
            info["embed_url"], "https://www.scribd.com/embeds/123456789/content"
        )
        self.assertEqual(info["filename"], "Document-Title.pdf")

    def test_legacy_doc_url(self):
        info = engine.parse_scribd_url("https://www.scribd.com/doc/42/Old-One")
        self.assertEqual(info["document_id"], "42")

    def test_embed_url_and_id_only(self):
        for url in (
            "https://www.scribd.com/embeds/99/content",
            "https://de.scribd.com/document/99",
        ):
            info = engine.parse_scribd_url(url)
            self.assertEqual(info["document_id"], "99")
            self.assertEqual(info["filename"], "scribd-99.pdf")

    def test_url_inside_pasted_text(self):
        info = engine.parse_scribd_url(
            "read this: https://www.scribd.com/document/5/Pasted?x=1 thanks"
        )
        self.assertEqual(info["document_id"], "5")

    def test_invalid_urls(self):
        for url in ("", None, "https://example.com/document/1/x", "scribd"):
            self.assertIsNone(engine.parse_scribd_url(url))

    def test_convert_keeps_legacy_contract(self):
        self.assertEqual(
            engine.convert_scribd_link("https://www.scribd.com/document/1/A"),
            "https://www.scribd.com/embeds/1/content",
        )
        self.assertEqual(engine.convert_scribd_link("nope"), "Invalid Scribd URL")

    def test_filename_from_url(self):
        self.assertEqual(
            engine.get_filename_from_url(
                "https://www.scribd.com/document/1/My-File"
            ),
            "My-File.pdf",
        )


class ExtractScribdUrlsTests(unittest.TestCase):
    def test_extracts_multiple_and_dedupes(self):
        text = (
            "https://www.scribd.com/document/111/Alpha-Doc\n"
            "see https://de.scribd.com/doc/222/Beta and "
            "https://www.scribd.com/document/111/Alpha-Doc again\n"
            "https://www.scribd.com/embeds/333/content\n"
            "https://example.com/document/9/x"
        )
        found = engine.extract_scribd_urls(text)
        self.assertEqual([d["document_id"] for d in found], ["111", "222", "333"])
        self.assertEqual(found[0]["source_url"],
                         "https://www.scribd.com/document/111/Alpha-Doc")
        self.assertEqual(found[2]["filename"], "scribd-333.pdf")

    def test_empty_and_no_links(self):
        self.assertEqual(engine.extract_scribd_urls(""), [])
        self.assertEqual(engine.extract_scribd_urls("nothing here"), [])


class SanitizeFilenameTests(unittest.TestCase):
    def test_windows_illegal_characters(self):
        cleaned = engine.sanitize_filename('a<b>:"/\\|?*c')
        for char in '<>:"/\\|?*':
            self.assertNotIn(char, cleaned)
        self.assertTrue(cleaned.startswith("a-b-") and cleaned.endswith("-c"))

    def test_reserved_names_and_edges(self):
        self.assertEqual(engine.sanitize_filename("CON"), "CON_")
        self.assertEqual(engine.sanitize_filename("  name...  "), "name")
        self.assertEqual(engine.sanitize_filename(""), "scribd-document")
        self.assertEqual(engine.sanitize_filename("file.pdf"), "file")


class DownloadDocumentTests(unittest.TestCase):
    def test_invalid_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            engine.download_document("https://example.com/nope")

    def test_cancelled_before_start(self):
        event = threading.Event()
        event.set()
        logs, progress = [], []
        with self.assertRaises(engine.DownloadCancelledError):
            engine.download_document(
                "https://www.scribd.com/document/1/Test",
                on_log=logs.append,
                on_progress=progress.append,
                cancel_event=event,
            )
        self.assertTrue(logs)  # embed/output lines were reported first

    def test_callbacks_do_not_leak_after_run(self):
        with self.assertRaises(ValueError):
            engine.download_document("bad", on_log=lambda _line: None)
        self.assertIsNone(getattr(engine._run_state, "on_log", None))


class WaitForDocumentPagesTests(unittest.TestCase):
    def test_returns_immediately_when_doc_manager_count_matches(self):
        class Driver:
            def execute_script(self, _script):
                return {
                    "outerPages": 12,
                    "managerPages": 12,
                    "readyState": "interactive",
                    "title": "Doc",
                }

        self.assertEqual(engine.wait_for_document_pages(Driver(), 10), 12)

    def test_waits_for_stable_outer_page_count(self):
        snapshots = iter(
            [
                {"outerPages": 2, "managerPages": 0},
                {"outerPages": 4, "managerPages": 0},
                {"outerPages": 4, "managerPages": 0},
                {"outerPages": 4, "managerPages": 0},
            ]
        )

        class Driver:
            def execute_script(self, _script):
                return next(snapshots)

        with mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            self.assertEqual(engine.wait_for_document_pages(Driver(), 10), 4)

    def test_transient_navigation_errors_are_retried(self):
        calls = {"count": 0}

        class Driver:
            def execute_script(self, _script):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise engine.WebDriverException("execution context destroyed")
                return {
                    "outerPages": 5,
                    "managerPages": 5,
                    "readyState": "interactive",
                    "title": "Doc",
                }

        with mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            self.assertEqual(engine.wait_for_document_pages(Driver(), 10), 5)

    def test_cancellation_is_honoured_while_waiting(self):
        event = threading.Event()
        engine._run_state.cancel_event = event

        class Driver:
            def execute_script(self, _script):
                event.set()
                return {"outerPages": 0, "managerPages": 0}

        try:
            with mock.patch.object(engine.time, "sleep", lambda _seconds: None):
                with self.assertRaises(engine.DownloadCancelledError):
                    engine.wait_for_document_pages(Driver(), 10)
        finally:
            engine._run_state.cancel_event = None


class LoggingAndOptionsTests(unittest.TestCase):
    def test_log_callback_survives_missing_stdout(self):
        lines = []
        engine._run_state.on_log = lines.append
        try:
            with mock.patch.object(sys, "stdout", None):
                engine._log("still delivered")
        finally:
            engine._run_state.on_log = None
        self.assertEqual(lines, ["still delivered"])

    def test_chrome_uses_eager_page_load_strategy(self):
        options = engine.build_chrome_options("/tmp/profile")
        self.assertEqual(
            options.to_capabilities().get("pageLoadStrategy"), "eager"
        )


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the engine helpers used by the CLI and the GUI.

Run with:  python -m unittest discover -s tests -v
No browser or network is needed.
"""

import importlib.util
import inspect
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



class LoadPageBatchTests(unittest.TestCase):
    def setUp(self):
        self.previous_state = {
            name: getattr(engine._run_state, name, None)
            for name in (
                "cancel_event",
                "on_progress",
                "page_load_timeout",
                "cdp_timeout",
            )
        }
        engine._run_state.cancel_event = None
        engine._run_state.on_progress = None
        engine._run_state.page_load_timeout = 10
        engine._run_state.cdp_timeout = 600

    def tearDown(self):
        for name, value in self.previous_state.items():
            setattr(engine._run_state, name, value)

    @staticmethod
    def status(ready, total=2, total_images=2, pending_images=0):
        pages = []
        for index in range(1, total + 1):
            is_ready = index <= ready
            pages.append(
                {
                    "pageNum": index,
                    "ready": is_ready,
                    "reason": "" if is_ready else "3 image(s) still loading",
                }
            )
        return {
            "supported": True,
            "total": total,
            "ready": ready,
            "totalImages": total_images,
            "pendingImages": pending_images,
            "brokenImages": 0,
            "pages": pages,
        }

    def test_polls_until_images_are_stably_ready(self):
        statuses = iter(
            [
                self.status(0, pending_images=3),
                self.status(2),
                self.status(2),
            ]
        )
        progress = []
        engine._run_state.on_progress = progress.append

        class Driver:
            def __init__(self):
                self.script_timeouts = []
                self.calls = 0

            def set_script_timeout(self, value):
                self.script_timeouts.append(value)

            def execute_script(self, _script, _page_numbers):
                self.calls += 1
                return next(statuses)

            # Deliberately no execute_async_script: a regression to one long
            # browser-side wait would fail this test immediately.

        driver = Driver()
        with mock.patch.object(engine.time, "monotonic", lambda: 0.0), \
            mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            engine.load_page_batch(
                driver,
                [1, 2],
                progress_current=0,
                progress_total=215,
            )

        self.assertEqual(driver.calls, 3)
        self.assertTrue(
            any("0/2 ready" in (event.get("message") or "") for event in progress)
        )
        self.assertNotIn(
            "driver.execute_async_script",
            inspect.getsource(engine.load_page_batch),
        )

    def test_stalled_batch_fails_at_deadline_with_reasons(self):
        class Driver:
            def set_script_timeout(self, _value):
                pass

            def execute_script(self, _script, _page_numbers):
                return LoadPageBatchTests.status(0, pending_images=3)

        with mock.patch.object(
            engine.time, "monotonic", side_effect=[0.0, 0.0, 11.0]
        ), mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            with self.assertRaisesRegex(
                RuntimeError, r"after 10s.*2 \(3 image\(s\) still loading\)"
            ):
                engine.load_page_batch(Driver(), [1, 2])

    def test_cancellation_is_checked_between_polls(self):
        event = threading.Event()
        engine._run_state.cancel_event = event

        class Driver:
            def set_script_timeout(self, _value):
                pass

            def execute_script(self, _script, _page_numbers):
                event.set()
                return LoadPageBatchTests.status(0, pending_images=1)

        with mock.patch.object(engine.time, "monotonic", lambda: 0.0), \
            mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            with self.assertRaises(engine.DownloadCancelledError):
                engine.load_page_batch(Driver(), [1, 2])

    def test_transient_renderer_timeout_is_retried(self):
        calls = {"count": 0}

        class Driver:
            def set_script_timeout(self, _value):
                pass

            def execute_script(self, _script, _page_numbers):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise engine.WebDriverException(
                        "timed out receiving message from renderer"
                    )
                return LoadPageBatchTests.status(2, total_images=0)

        with mock.patch.object(engine.time, "monotonic", lambda: 0.0), \
            mock.patch.object(engine.time, "sleep", lambda _seconds: None):
            engine.load_page_batch(Driver(), [1, 2])
        self.assertEqual(calls["count"], 2)

    def test_dead_browser_session_fails_immediately(self):
        class Driver:
            def set_script_timeout(self, _value):
                pass

            def execute_script(self, _script, _page_numbers):
                raise engine.WebDriverException("invalid session id")

        with mock.patch.object(engine.time, "monotonic", lambda: 0.0):
            with self.assertRaisesRegex(RuntimeError, "Chrome stopped responding"):
                engine.load_page_batch(Driver(), [1, 2])


class CloseDriverTests(unittest.TestCase):
    def test_normal_quit(self):
        class Driver:
            quit_called = False

            def quit(self):
                self.quit_called = True

        driver = Driver()
        engine.close_driver(driver)
        self.assertTrue(driver.quit_called)

    def test_failed_quit_terminates_driver_service(self):
        class Process:
            terminated = False
            waited = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                self.waited = timeout == 5

        process = Process()

        class Driver:
            service = type("Service", (), {"process": process})()

            def quit(self):
                raise engine.WebDriverException("chrome not reachable")

        engine.close_driver(Driver())
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)

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

    def test_cookie_cleanup_does_not_scan_every_dom_node(self):
        source = inspect.getsource(engine.hide_cookie_dialogs)
        self.assertNotIn("querySelectorAll('*')", source)
        self.assertNotIn("element.innerText", source)
        self.assertIn("overlayCandidates", source)


if __name__ == "__main__":
    unittest.main()

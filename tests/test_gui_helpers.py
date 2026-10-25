"""Unit tests for queue / drag-&-drop helpers in gui_app.py.

These exercise pure logic (no window is opened), so they run headless:
    python -m unittest discover -s tests -v
"""

import json
import queue
import threading
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import gui_app  # noqa: E402  (customtkinter may be None headless — fine)

engine = gui_app.load_engine()


class SplitDropFilesTests(unittest.TestCase):
    def test_plain_and_braced_paths(self):
        data = r"{C:/My Documents/links.txt} C:/plain.txt {D:/a b/c.url}"
        self.assertEqual(
            gui_app.split_drop_files(data),
            ["C:/My Documents/links.txt", "C:/plain.txt", "D:/a b/c.url"],
        )

    def test_empty(self):
        self.assertEqual(gui_app.split_drop_files(""), [])
        self.assertEqual(gui_app.split_drop_files(None), [])


class ParseDropDataTests(unittest.TestCase):
    def test_browser_link_text(self):
        infos = gui_app.parse_drop_data(
            engine,
            "https://www.scribd.com/document/42/Dragged-Doc",
        )
        self.assertEqual([i["document_id"] for i in infos], ["42"])

    def test_text_with_title_and_prose(self):
        infos = gui_app.parse_drop_data(
            engine,
            "My Doc https://www.scribd.com/document/7/One "
            "and https://www.scribd.com/doc/8/Two",
        )
        self.assertEqual([i["document_id"] for i in infos], ["7", "8"])

    def test_dropped_link_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = Path(tmp) / "my links.txt"
            links.write_text(
                "https://www.scribd.com/document/1/First\n"
                "https://www.scribd.com/document/2/Second\n",
                encoding="utf-8",
            )
            shortcut = Path(tmp) / "page.url"
            shortcut.write_text(
                "[InternetShortcut]\nURL=https://www.scribd.com/document/3/Third\n",
                encoding="utf-8",
            )
            data = "{%s} {%s}" % (links, shortcut)
            infos = gui_app.parse_drop_data(engine, data)
            self.assertEqual(
                [i["document_id"] for i in infos], ["1", "2", "3"]
            )

    def test_non_link_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "photo.png"
            binary.write_bytes(b"https://www.scribd.com/document/5/Nope")
            infos = gui_app.parse_drop_data(engine, "{%s}" % binary)
            # The raw *data string* itself is not a URL; .png is not read.
            self.assertEqual(infos, [])

    def test_nothing_found(self):
        self.assertEqual(gui_app.parse_drop_data(engine, "hello world"), [])


class ReadUrlsFromFilesTests(unittest.TestCase):
    def test_missing_and_oversized_files_skipped(self):
        self.assertEqual(
            gui_app.read_urls_from_files(engine, ["/does/not/exist.txt"]), []
        )


class QueueModelTests(unittest.TestCase):
    def test_item_from_info(self):
        info = engine.parse_scribd_url(
            "https://www.scribd.com/document/9/Some-Doc"
        )
        item = gui_app.QueueItem.from_info(info)
        self.assertEqual(item.document_id, "9")
        self.assertEqual(item.title, "Some Doc")
        self.assertEqual(item.filename, "Some-Doc.pdf")
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.source_url, info["source_url"])

    def test_dedupe_against_queue(self):
        infos = engine.extract_scribd_urls(
            "https://www.scribd.com/document/1/A "
            "https://www.scribd.com/document/2/B"
        )
        fresh = gui_app.dedupe_infos(infos, known_ids={"1"})
        self.assertEqual([i["document_id"] for i in fresh], ["2"])



class DownloadQueueWorkerTests(unittest.TestCase):
    class FakeCancelled(RuntimeError):
        pass

    class FakeEngine:
        DownloadCancelledError = None  # replaced below (class body scope)

        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = []

        def download_document(self, url, output, **kwargs):
            self.calls.append(url)
            kwargs["on_log"](f"downloading {url}")
            kwargs["on_progress"](
                {
                    "stage": "export",
                    "current": 1,
                    "total": 1,
                    "message": "Page 1 of 1",
                }
            )
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return output

    FakeEngine.DownloadCancelledError = FakeCancelled

    @staticmethod
    def make_items():
        return [
            gui_app.QueueItem(
                "https://www.scribd.com/document/1/First",
                "1",
                "First",
                "First.pdf",
            ),
            gui_app.QueueItem(
                "https://www.scribd.com/document/2/Second",
                "2",
                "Second",
                "Second.pdf",
            ),
        ]

    def run_queue(self, items, fake_engine, folder, policy="copy", provider=None):
        events = queue.Queue()
        settings = SimpleNamespace(
            show_browser=False,
            batch_size=8,
            page_load_timeout=120,
            cdp_timeout=600,
        )
        cancel_assignments = []
        gui_app.run_download_queue(
            items_provider=provider or (lambda: items),
            engine=fake_engine,
            settings=settings,
            folder=Path(folder),
            policy=policy,
            events=events,
            stop_event=threading.Event(),
            set_current_cancel=cancel_assignments.append,
            cleanup_partial=lambda _output, _existed: None,
        )
        drained = []
        while True:
            try:
                drained.append(events.get_nowait())
            except queue.Empty:
                break
        return drained, cancel_assignments

    @staticmethod
    def meaningful_kinds(events):
        return [
            kind
            for kind, _payload in events
            if kind not in ("log", "progress")
        ]

    def test_two_items_download_sequentially_and_finish(self):
        items = self.make_items()
        fake_engine = self.FakeEngine(["ok", "ok"])
        with tempfile.TemporaryDirectory() as tmp:
            events, cancel_assignments = self.run_queue(
                items, fake_engine, tmp
            )

        self.assertEqual(
            fake_engine.calls, [item.source_url for item in items]
        )
        self.assertEqual(
            self.meaningful_kinds(events),
            [
                "item_started",
                "item_done",
                "item_started",
                "item_done",
                "queue_finished",
            ],
        )
        self.assertIsNone(events[-1][1])
        self.assertEqual([item.status for item in items], ["done", "done"])
        self.assertIsNone(cancel_assignments[-1])

    def test_first_failure_does_not_stop_second_item(self):
        items = self.make_items()
        fake_engine = self.FakeEngine([RuntimeError("boom"), "ok"])
        with tempfile.TemporaryDirectory() as tmp:
            events, _cancels = self.run_queue(items, fake_engine, tmp)

        self.assertEqual(
            self.meaningful_kinds(events),
            [
                "item_started",
                "item_failed",
                "item_started",
                "item_done",
                "queue_finished",
            ],
        )
        self.assertIsNone(events[-1][1])
        self.assertEqual([item.status for item in items], ["failed", "done"])

    def test_skip_existing_continues_to_next_item(self):
        items = self.make_items()
        fake_engine = self.FakeEngine(["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "First.pdf").write_bytes(b"existing")
            events, _cancels = self.run_queue(
                items, fake_engine, tmp, policy="skip"
            )

        self.assertEqual(fake_engine.calls, [items[1].source_url])
        self.assertEqual(
            self.meaningful_kinds(events),
            ["item_skipped", "item_started", "item_done", "queue_finished"],
        )
        self.assertEqual([item.status for item in items], ["skipped", "done"])

    def test_unexpected_worker_error_still_emits_queue_finished(self):
        items = self.make_items()[:1]
        fake_engine = self.FakeEngine(["ok"])
        calls = {"count": 0}

        def exploding_provider():
            calls["count"] += 1
            if calls["count"] > 1:
                raise RuntimeError("provider exploded")
            return items

        with tempfile.TemporaryDirectory() as tmp:
            events, cancel_assignments = self.run_queue(
                items,
                fake_engine,
                tmp,
                provider=exploding_provider,
            )

        self.assertEqual(events[-1][0], "queue_finished")
        self.assertIn("provider exploded", events[-1][1])
        self.assertEqual(items[0].status, "done")
        self.assertIsNone(cancel_assignments[-1])

class SettingsTests(unittest.TestCase):
    def test_round_trip_with_queue_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gui_app.config_dir
            gui_app.config_dir = lambda: Path(tmp)
            try:
                settings = gui_app.AppSettings()
                settings.conflict_policy = "skip"
                settings.pending_queue = [
                    {"source_url": "https://www.scribd.com/document/1/A",
                     "filename": "A.pdf"}
                ]
                settings.save()
                loaded = gui_app.AppSettings.load()
            finally:
                gui_app.config_dir = original
        self.assertEqual(loaded.conflict_policy, "skip")
        self.assertEqual(loaded.pending_queue[0]["filename"], "A.pdf")


class SettingsPersistenceRegressionTests(unittest.TestCase):
    def test_timeout_validation(self):
        self.assertEqual(
            gui_app.validate_timeout_values(" 120 ", "600"), (120, 600)
        )
        for page, export in (("x", "600"), ("9", "600"), ("120", "3601")):
            with self.assertRaises(ValueError):
                gui_app.validate_timeout_values(page, export)

    def test_load_normalizes_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "settings.json").write_text(
                json.dumps(
                    {
                        "appearance": "Purple",
                        "batch_size": 999,
                        "page_load_timeout": 1,
                        "cdp_timeout": "900",
                        "conflict_policy": "explode",
                        "show_browser": 1,
                    }
                ),
                encoding="utf-8",
            )
            original = gui_app.config_dir
            gui_app.config_dir = lambda: Path(tmp)
            try:
                loaded = gui_app.AppSettings.load()
            finally:
                gui_app.config_dir = original
        self.assertEqual(loaded.appearance, "System")
        self.assertEqual(loaded.batch_size, 16)
        self.assertEqual(loaded.page_load_timeout, 10)
        self.assertEqual(loaded.cdp_timeout, 900)
        self.assertEqual(loaded.conflict_policy, "copy")
        self.assertTrue(loaded.show_browser)

    def test_all_user_facing_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gui_app.config_dir
            gui_app.config_dir = lambda: Path(tmp)
            try:
                settings = gui_app.AppSettings()
                settings.output_dir = str(Path(tmp) / "PDFs")
                settings.appearance = "Dark"
                settings.batch_size = 16
                settings.page_load_timeout = 240
                settings.cdp_timeout = 900
                settings.show_browser = True
                settings.open_when_done = True
                settings.clipboard_check = False
                settings.conflict_policy = "overwrite"
                self.assertTrue(settings.save())
                loaded = gui_app.AppSettings.load()
            finally:
                gui_app.config_dir = original

        self.assertEqual(loaded.output_dir, str(Path(tmp) / "PDFs"))
        self.assertEqual(loaded.appearance, "Dark")
        self.assertEqual(loaded.batch_size, 16)
        self.assertEqual(loaded.page_load_timeout, 240)
        self.assertEqual(loaded.cdp_timeout, 900)
        self.assertTrue(loaded.show_browser)
        self.assertTrue(loaded.open_when_done)
        self.assertFalse(loaded.clipboard_check)
        self.assertEqual(loaded.conflict_policy, "overwrite")

    def test_save_reports_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gui_app.config_dir
            try:
                gui_app.config_dir = lambda: Path(tmp)
                self.assertTrue(gui_app.AppSettings().save())
                blocker = Path(tmp, "not-a-directory")
                blocker.write_text("x", encoding="utf-8")
                gui_app.config_dir = lambda: blocker
                self.assertFalse(gui_app.AppSettings().save())
            finally:
                gui_app.config_dir = original


class QueueTextRegressionTests(unittest.TestCase):
    def test_title_counts_downloading_separately(self):
        items = [
            gui_app.QueueItem("u1", "1", "A", "A.pdf", status="downloading"),
            gui_app.QueueItem("u2", "2", "B", "B.pdf", status="pending"),
            gui_app.QueueItem("u3", "3", "C", "C.pdf", status="done"),
        ]
        self.assertEqual(
            gui_app.queue_title_text(items),
            "Queue  (3)  ·  downloading  ·  1 waiting  ·  1 saved",
        )

    def test_downloading_subtext_shows_live_activity(self):
        item = gui_app.QueueItem(
            "u", "1", "Doc", "Doc.pdf",
            status="downloading", activity="Opening document…",
        )
        self.assertEqual(
            gui_app.queue_item_subtext(item),
            "Opening document…  ·  Doc.pdf",
        )


if __name__ == "__main__":
    unittest.main()

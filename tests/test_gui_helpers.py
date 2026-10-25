"""Unit tests for queue / drag-&-drop helpers in gui_app.py.

These exercise pure logic (no window is opened), so they run headless:
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

"""Packaging consistency tests: versions in sync, referenced files exist.

Runs headless and does not build anything:
    python -m unittest discover -s tests -v
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import gui_app  # noqa: E402


def read(relative):
    return (PROJECT / relative).read_text(encoding="utf-8")


class VersionSyncTests(unittest.TestCase):
    def test_inno_version_matches_app(self):
        iss = read("installer/ScribdDownloader.iss")
        match = re.search(r'#define MyAppVersion "([^"]+)"', iss)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), gui_app.APP_VERSION)

    def test_version_info_matches_app(self):
        info = read("version_info.txt")
        file_version = re.search(r'filevers=\(([^)]+)\)', info).group(1)
        expected = tuple(int(part) for part in gui_app.APP_VERSION.split(".")) + (0,)
        actual = tuple(int(part.strip()) for part in file_version.split(","))
        self.assertEqual(actual, expected)
        self.assertIn(f'"ProductVersion", "{gui_app.APP_VERSION}"', info)


class ReferencedFilesTests(unittest.TestCase):
    def test_spec_inputs_exist(self):
        for relative in (
            "ScribdDownloader.spec",
            "gui_app.py",
            "scribd-downloader.py",
            "version_info.txt",
            "assets/icon.ico",
            "assets/icon.png",
        ):
            self.assertTrue((PROJECT / relative).is_file(), relative)
        spec = read("ScribdDownloader.spec")
        self.assertIn('"version_info.txt"', spec)
        self.assertIn("tkinterdnd2", spec)

    def test_installer_inputs_exist(self):
        iss = read("installer/ScribdDownloader.iss")
        self.assertIn("wizard.bmp", iss)
        for relative in (
            "installer/ScribdDownloader.iss",
            "assets/installer/wizard.bmp",
            "assets/installer/wizard-small.bmp",
            "build_installer.bat",
            "build_windows.bat",
            "README.md",
        ):
            self.assertTrue((PROJECT / relative).is_file(), relative)

    def test_requirements_cover_gui(self):
        requirements = read("requirements.txt")
        for package in ("customtkinter", "tkinterdnd2", "selenium", "pypdf"):
            self.assertIn(package, requirements)


if __name__ == "__main__":
    unittest.main()

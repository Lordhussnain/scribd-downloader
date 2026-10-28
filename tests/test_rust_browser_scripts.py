"""Keep the Rust browser-script copies aligned with the current engine."""
import ast
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent


class BrowserScriptParityTests(unittest.TestCase):
    def test_scripts_match_engine(self):
        names = {
            "hide_cookie_dialogs": "hide_cookie_dialogs",
            "prepare_document_for_print": "prepare_print",
            "inject_print_styles": "print_styles",
            "load_page_batch": "probe_batch",
            "release_page_batch": "release_batch",
            "wait_for_document_pages": "page_count_snapshot",
        }
        expected = {}
        tree = ast.parse((ROOT / "scribd-downloader.py").read_text(encoding="utf-8"))
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            for call in ast.walk(function):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "execute_script" and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)):
                    continue
                script = call.args[0].value
                name = names.get(function.name)
                if function.name == "save_pdf_pages_individually" and "const targetIndex" in script:
                    name = "isolate_page"
                if name:
                    expected[name] = textwrap.dedent(script).strip() + "\n"
        self.assertEqual(len(expected), 7)
        for name, script in expected.items():
            with self.subTest(script=name):
                self.assertEqual((ROOT / "rust-core" / "src" / "browser_scripts" / f"{name}.js").read_text(encoding="utf-8"), script)

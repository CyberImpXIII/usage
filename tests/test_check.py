"""The gates (devtools/check.py): its --json keeps the schema's rules (the full
validator is tools/checks' `checks one check-json`, an audit-role run), a red
gate is red, and a gate that did not run exits 3 (the parts runner's
`unchecked`, never ok). The runner itself and the parts
file are tests/test_parts.py's."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".progress")


def run_check(root, *gates):
    r = subprocess.run([sys.executable, str(root / "devtools" / "check.py"), "--json", *gates],
                       capture_output=True, text=True, timeout=300)
    return r.returncode, json.loads(r.stdout)


class CheckJson(unittest.TestCase):
    def rules(self, code, doc):
        unrun = [c for c in doc["checks"] if c["status"] == "unchecked" and ":" not in c["name"]]
        self.assertEqual(code, 1 if not doc["ok"] else 3 if unrun else 0)
        self.assertEqual(doc["ok"], not any(c["status"] in ("fail", "error") for c in doc["checks"]))
        names = [c["name"] for c in doc["checks"]]
        self.assertEqual(len(names), len(set(names)))
        for c in doc["checks"]:
            self.assertEqual(c["counts"]["failed"], len(c["failures"]), c["name"])
            if c["status"] in ("ok", "unchecked"):
                self.assertEqual(c["failures"], [], c["name"])
            else:
                self.assertTrue(c["failures"], c["name"])
            if c["status"] == "unchecked":
                self.assertTrue(c.get("reason"), c["name"])
            for f in c["failures"]:
                self.assertTrue(f["message"] and f["role"])
                if f["line"] is not None:
                    self.assertTrue(f["file"])

    def test_green_gates_in_the_schema(self):
        code, doc = run_check(ROOT, "files")
        self.rules(code, doc)
        self.assertEqual([(c["name"], c["status"]) for c in doc["checks"]], [("files", "ok")])
        self.assertEqual(code, 0)

    def test_a_planted_fault_is_red_and_named(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-check-"))
        try:
            copy = tmp / "usage"
            shutil.copytree(ROOT, copy, ignore=IGNORE)
            (copy / "usage-stop.sh").chmod(0o644)
            (copy / "usagelib" / "calc.py").write_text("def broken(:\n")
            code, doc = run_check(copy, "files")
            self.rules(code, doc)
            self.assertEqual(code, 1)
            msgs = [f["message"] for f in doc["checks"][0]["failures"]]
            self.assertTrue(any("not executable: usage-stop.sh" in m for m in msgs), msgs)
            self.assertTrue(any(f["file"] == "usagelib/calc.py" for f in doc["checks"][0]["failures"]), msgs)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_absent_sibling_is_unchecked_and_exits_3(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-check-"))
        try:
            copy = tmp / "usage"
            shutil.copytree(ROOT, copy, ignore=IGNORE)
            code, doc = run_check(copy, "shared")
            self.rules(code, doc)
            self.assertEqual([(c["name"], c["status"]) for c in doc["checks"]], [("shared", "unchecked")])
            self.assertEqual(code, 3)
            r = subprocess.run([sys.executable, str(copy / "devtools" / "check.py"), "shared"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 3)
            self.assertIn("check: UNCHECKED (did not run: shared)", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_unknown_gate_is_a_usage_error(self):
        r = subprocess.run([sys.executable, str(ROOT / "devtools" / "check.py"), "nope"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown gate", r.stderr)

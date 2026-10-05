"""The check itself: its --json keeps the schema's rules (the full validator is
tools/checks' `checks one check-json`, an audit-role run), and a red gate is
red. Runs the cheap gates only; the suite would run inside itself otherwise."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT


def run_check(root, *gates):
    env = dict(os.environ, USAGE_CHECK_NESTED="1")
    r = subprocess.run([str(root / "dev.sh"), "check", "--json", *gates], capture_output=True, text=True,
                       env=env, timeout=300)
    return r.returncode, json.loads(r.stdout)


class CheckJson(unittest.TestCase):
    def rules(self, code, doc):
        self.assertEqual(code == 0, doc["ok"])
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
        code, doc = run_check(ROOT, "files", "test")
        self.rules(code, doc)
        self.assertEqual([c["status"] for c in doc["checks"]], ["ok", "unchecked"])

    def test_a_planted_fault_is_red_and_named(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-check-"))
        try:
            copy = tmp / "usage"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
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

    def test_an_absent_sibling_is_unchecked_not_ok(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-check-"))
        try:
            copy = tmp / "usage"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            code, doc = run_check(copy, "shared")
            self.rules(code, doc)
            self.assertEqual([(c["name"], c["status"]) for c in doc["checks"]], [("shared", "unchecked")])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_unknown_gate_is_a_usage_error(self):
        r = subprocess.run([str(ROOT / "dev.sh"), "check", "nope"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

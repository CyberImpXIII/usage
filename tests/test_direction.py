"""§4 "direction": no file here reads the workspace's delegation layer or names
a roster agent. The gate is the shared `no-roster` check (tools/checks), run
by devtools/check.py's `shared` gate; this repo keeps no copy of its patterns
or of the roster. These tests prove the gate is wired for this repo: clean
here, and red on a copy with a planted reach. Skipped in a fresh clone
without the sibling tool, where the `shared` gate says UNCHECKED instead."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

CHECKS = ROOT.parent / "checks" / "checks"
# Assembled so this file itself never holds the planted text whole.
PLANT = "../../" + ".claude/" + "agents." + "manifest.json"


def one(path, *extra):
    r = subprocess.run([str(CHECKS), "one", "no-roster", str(path), "--json", *extra],
                       capture_output=True, text=True, timeout=120)
    doc = json.loads(r.stdout)
    return doc["results"][0]["status"], doc["results"][0]["lines"]


@unittest.skipUnless(CHECKS.is_file(), "tools/checks is not beside this repo")
class Direction(unittest.TestCase):
    def test_this_repo_has_no_path_finding(self):
        status, lines = one(ROOT)
        self.assertIn(status, ("ok", "unchecked"), lines)
        self.assertTrue(any("paths half clean" in ln for ln in lines) or status == "ok", lines)

    def test_a_planted_reach_is_red(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-direction-"))
        try:
            copy = tmp / "tools" / "usage"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "tests", "__pycache__"))
            (copy / "usagelib" / "planted.py").write_text(f'ROSTER = "{PLANT}"\n')
            status, lines = one(copy, "--root", str(tmp))
            self.assertEqual(status, "fail", lines)
            self.assertTrue(any("planted.py" in ln for ln in lines), lines)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_planted_name_is_red_when_names_are_passed(self):
        tmp = Path(tempfile.mkdtemp(prefix="usage-direction-"))
        try:
            copy = tmp / "tools" / "usage"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "tests", "__pycache__"))
            (copy / "usagelib" / "planted.py").write_text('AGENT = "agent-zed"\n')
            status, lines = one(copy, "--root", str(tmp), "--names", "agent-zed,agent-yod")
            self.assertEqual(status, "fail", lines)
            status, _ = one(ROOT, "--names", "agent-zed,agent-yod")
            self.assertEqual(status, "ok")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

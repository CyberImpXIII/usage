"""`./dev.sh check` in parts (PLAN-small-tasks §3, §7): check.parts.json is
valid for the runner and lists one part per test module and one per gate of
devtools/check.py, both ways; dev.sh hands its options to the runner (check,
--resume, test <part>); a gate that did not run (exit 3) is a red part; and the
runner copy agrees with tools/checks' when that sibling is here."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

RUNNER = ROOT / "devtools" / "parts.py"
PARTS = ROOT / "check.parts.json"
SOURCE = ROOT.parent / "checks" / "checkslib" / "parts.py"


def gates():
    spec = importlib.util.spec_from_file_location("usage_check_gates", ROOT / "devtools" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.GATES)


class PartsFile(unittest.TestCase):
    def test_the_runner_accepts_it(self):
        r = subprocess.run([sys.executable, str(RUNNER), "validate", str(PARTS)], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"^ok: \d+ parts")

    def test_one_part_per_test_module_and_per_gate_both_ways(self):
        parts = json.loads(PARTS.read_text())["parts"]
        names = [p["name"] for p in parts]
        modules = {p.stem[len("test_"):] for p in (ROOT / "tests").glob("test_*.py")}
        want = {f"test.{m}" for m in modules} | gates()
        self.assertEqual(set(names), want)
        self.assertEqual(len(names), len(set(names)))
        for p in parts:
            if p["name"].startswith("test."):
                m = p["name"][len("test."):]
                self.assertEqual(p["run"], f"python3 -m unittest tests.test_{m}", p["name"])
                self.assertIn(f"tests/test_{m}.py", p["files"], p["name"])
            else:
                self.assertEqual(p["run"], f"python3 devtools/check.py {p['name']}", p["name"])

    def test_dev_sh_documents_what_it_dispatches(self):
        text = (ROOT / "dev.sh").read_text()
        usage = text.split("cat <<'EOF'", 1)[1].split("\nEOF", 1)[0]
        documented = set(re.findall(r"^  (\w+)\b", usage, re.M))
        dispatched = set(re.findall(r"^  (\w+)\) ", text, re.M))
        self.assertEqual(documented, dispatched)


@unittest.skipUnless(SOURCE.is_file(), "tools/checks is not beside this repo")
class RunnerCopy(unittest.TestCase):
    def test_it_is_the_shared_runner_byte_for_byte(self):
        """Drift (tools/checks moved on): `setup tools/usage --only parts --rebuild`."""
        self.assertEqual(RUNNER.read_bytes(), SOURCE.read_bytes())


class DevShHandsOver(unittest.TestCase):
    """A scratch suite (CHECK_PARTS) of three parts, the third exiting 3 as a gate
    that did not run does: dev.sh's options reach the runner and the run is red."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-parts-"))
        for n in ("a", "b"):
            (self.tmp / f"{n}.txt").write_text(n)
        doc = {"parts": [
            {"name": "pa", "run": "echo ran-a >> runs.log", "files": ["a.txt"], "timeout_s": 30},
            {"name": "pb", "run": "echo ran-b >> runs.log", "files": ["b.txt"], "timeout_s": 30},
            {"name": "unrun", "run": "echo 'check: UNCHECKED (did not run: guard)'; exit 3", "files": ["a.txt"],
             "timeout_s": 30}]}
        self.parts = self.tmp / "s.parts.json"
        self.parts.write_text(json.dumps(doc))
        self.env = dict(os.environ, CHECK_PARTS=str(self.parts), PROGRESS_FILE=str(self.tmp / "progress.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dev(self, *args):
        return subprocess.run([str(ROOT / "dev.sh"), *args], capture_output=True, text=True, env=self.env,
                              timeout=120)

    def runs(self):
        p = self.tmp / "runs.log"
        out = p.read_text().split() if p.exists() else []
        p.unlink(missing_ok=True)
        return out

    def test_check_names_the_red_part_and_resume_reruns_only_what_changed(self):
        r = self.dev("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("2 of 3 passed; failed: unrun", r.stdout)
        self.assertEqual(self.runs(), ["ran-a", "ran-b"])
        (self.tmp / "b.txt").write_text("changed")
        r = self.dev("check", "--resume")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.runs(), ["ran-b"])

    def test_test_part_runs_that_part_alone(self):
        r = self.dev("test", "pb")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.runs(), ["ran-b"])
        self.assertEqual(json.loads((self.tmp / "progress.json").read_text())["passed_steps"], ["pb"])

    def test_an_unknown_part_is_refused(self):
        r = self.dev("test", "nope")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.runs(), [])

"""§4 "settings are Jacob's": no command or reporter writes a settings.json.
The registrations are printed (`usage registrations`) for a proposal someone
else writes; this tool never writes a settings file of any name."""
import json
import os
import re
import subprocess
import unittest

from tests.helpers import FIXTURES, ROOT, Case, statusline_input

RUNTIME = [ROOT / "usage", *sorted(ROOT.glob("usage-*.sh")), *sorted((ROOT / "usagelib").glob("*.py"))]


class SettingsUntouched(Case):
    def test_every_command_and_reporter_leaves_both_settings_files_alone(self):
        user = self.home / ".claude" / "settings.json"
        project = self.tmp / "project" / ".claude" / "settings.json"
        for p in (user, project):
            p.parent.mkdir(parents=True)
            p.write_text('{"statusLine": {"type": "command", "command": "echo mine"}}\n')
            os.utime(p, (1_000_000_000, 1_000_000_000))
        before = {p: (p.read_bytes(), p.stat().st_mtime) for p in (user, project)}
        env = self.env("2026-10-05T10:00:30Z")
        env.pop("USAGE_STORE")  # the default store, under the fake HOME
        cwd = self.tmp / "project"

        def run(argv, stdin=""):
            return subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=120)

        self.assertEqual(run([str(ROOT / "usage"), "init"]).returncode, 0)
        transcript = self.tmp / "t.jsonl"
        transcript.write_text((FIXTURES / "transcript" / "turn1.jsonl").read_text())
        run([str(ROOT / "usage-statusline.sh")], json.dumps(statusline_input()))
        run([str(ROOT / "usage-stop.sh")], json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript)}))
        run([str(ROOT / "usage-failure.sh")], json.dumps({"hook_event_name": "StopFailure", "error": "rate_limit"}))
        for argv in (["status"], ["burn"], ["project"], ["gate", "1000"], ["hits"], ["tokens"], ["calibrate"],
                     ["registrations"]):
            run([str(ROOT / "usage"), *argv])
        store = self.home / ".claude" / "usage"
        self.assertTrue((store / "samples.tsv").exists(), "the run wrote nothing, so the test proves nothing")
        self.assertTrue((store / "tokens.tsv").exists())
        self.assertTrue((store / "hits.tsv").exists())
        after = {p: (p.read_bytes(), p.stat().st_mtime) for p in (user, project)}
        self.assertEqual(after, before)
        self.assertEqual(sorted(p.name for p in (self.home / ".claude").iterdir()), ["settings.json", "usage"])
        self.assertEqual(sorted(p.name for p in project.parent.iterdir()), ["settings.json"])

    def test_no_runtime_file_names_a_settings_file(self):
        offenders = [p.name for p in RUNTIME if re.search(r"settings(\.proposed)?\.json", p.read_text())]
        self.assertEqual(offenders, [])


class Registrations(unittest.TestCase):
    def test_registrations_name_files_that_exist_and_are_executable(self):
        r = subprocess.run([str(ROOT / "usage"), "registrations"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        doc = json.loads(r.stdout)
        cmds = [doc["statusLine"]["command"]]
        for event in ("Stop", "SubagentStop", "StopFailure"):
            for group in doc["hooks"][event]:
                cmds += [h["command"] for h in group["hooks"]]
        self.assertEqual(doc["hooks"]["StopFailure"][0]["matcher"], "rate_limit")
        for c in cmds:
            path = c.strip("'")
            self.assertTrue(os.path.isfile(path) and os.access(path, os.X_OK), c)

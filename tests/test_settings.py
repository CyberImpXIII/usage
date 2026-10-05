"""§4 "settings are Jacob's": no command or reporter writes a settings.json.
`usage registrations` prints the registrations; `usage registrations --export`
(§9) writes them only into `<file>.proposed` beside the settings file, which
Jacob copies over himself. Only usagelib/export.py may name a settings file,
and running every command, the export included, leaves both settings files
byte- and mtime-identical."""
import json
import os
import re
import subprocess
import unittest

from tests.helpers import FIXTURES, ROOT, Case, statusline_input

WRITES = re.compile(r"\.write_(?:text|bytes)\(|open\([^)]*['\"][wax+]")
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
                     ["registrations"], ["registrations", "--export"], ["registrations", "--export", str(project)]):
            run([str(ROOT / "usage"), *argv])
        self.assertTrue((self.home / ".claude" / "settings.json.proposed").is_file(), "the export wrote nothing")
        self.assertTrue((project.parent / "settings.json.proposed").is_file())
        store = self.home / ".claude" / "usage"
        self.assertTrue((store / "samples.tsv").exists(), "the run wrote nothing, so the test proves nothing")
        self.assertTrue((store / "tokens.tsv").exists())
        self.assertTrue((store / "hits.tsv").exists())
        after = {p: (p.read_bytes(), p.stat().st_mtime) for p in (user, project)}
        self.assertEqual(after, before)
        self.assertEqual(sorted(p.name for p in (self.home / ".claude").iterdir()),
                         ["settings.json", "settings.json.proposed", "usage"])
        self.assertEqual(sorted(p.name for p in project.parent.iterdir()), ["settings.json", "settings.json.proposed"])

    def test_only_the_export_names_a_settings_file(self):
        """export.py reads and proposes; cli.py names the default in its help
        and writes no file at all."""
        offenders = [p.name for p in RUNTIME if p.name not in ("export.py", "cli.py")
                     and re.search(r"settings(\.proposed)?\.json", p.read_text())]
        self.assertEqual(offenders, [])
        self.assertEqual(WRITES.findall((ROOT / "usagelib" / "cli.py").read_text()), [])

    def test_the_export_opens_for_writing_only_its_temp_file(self):
        """The one write in export.py is mkstemp's descriptor beside the
        proposal, renamed onto it; no other open for writing, write_text or
        write_bytes."""
        text = (ROOT / "usagelib" / "export.py").read_text()
        self.assertEqual(WRITES.findall(text), ['open(fd, "w'])
        self.assertIn("fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + \".\")", text)
        self.assertEqual(len(re.findall(r"os\.replace\(", text)), 1)
        self.assertIn("os.replace(tmp, target)", text)
        self.assertIn("target = path.with_name(path.name + SUFFIX)", text)


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

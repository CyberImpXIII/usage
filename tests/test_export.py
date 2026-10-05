"""PLAN-usage-reporting.md §9: `usage registrations --export [FILE]` reads a
settings file, adds the status line and the three hook registrations, and
writes the result as a static copy beside it, `<FILE>.proposed`. The live file
is never written.

The §9 gates, each a test here: the copy parses; every key of the original is
present and unchanged in the copy; the registrations are present once, so an
export of an already-registered file is byte-identical to the first (no
duplicate entries); a live file that fails to parse gives an error and no copy.
Plus: the export changes with its input, an existing proposal is replaced (and
left alone when identical), and nothing about the live file moves."""
import json
import os
import shlex
import subprocess

from tests.helpers import ROOT, Case, statusline_input

EVENTS = ("Stop", "SubagentStop", "StopFailure")
OURS = {"statusLine": "usage-statusline.sh", "Stop": "usage-stop.sh", "SubagentStop": "usage-stop.sh",
        "StopFailure": "usage-failure.sh"}

LIVE = {
    "model": "opus",
    "enabledPlugins": {"a@b": True, "c@d": False},
    "permissions": {"allow": ["Bash(ls:*)", "Read"], "deny": []},
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo theirs"}]}],
              "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x.sh"}]}]},
    "theme": "dark",
}


def commands(doc, event):
    return [h["command"] for g in doc.get("hooks", {}).get(event, []) for h in g.get("hooks", [])]


def is_ours(cmd, name):
    return shlex.split(cmd)[0] == str(ROOT / name)


def kept(orig, copy, path=""):
    """Every key of orig is in copy with the same value; lists may only grow at
    the end. Returns the paths that differ (empty: kept)."""
    if isinstance(orig, dict):
        if not isinstance(copy, dict):
            return [path or "$"]
        out = []
        for k, v in orig.items():
            out += kept(v, copy[k], f"{path}.{k}") if k in copy else [f"{path}.{k} (missing)"]
        return out
    if isinstance(orig, list):
        if not isinstance(copy, list) or len(copy) < len(orig):
            return [path]
        return [p for i, v in enumerate(orig) for p in kept(v, copy[i], f"{path}[{i}]")]
    return [] if orig == copy and type(orig) is type(copy) else [path]


class Export(Case):
    def setUp(self):
        super().setUp()
        self.live = self.tmp / "settings.json"
        self.proposed = self.tmp / "settings.json.proposed"

    def put(self, doc, text=None, mode=0o644):
        self.live.write_text(text if text is not None else json.dumps(doc, indent=4) + "\n")
        self.live.chmod(mode)
        os.utime(self.live, (1_000_000_000, 1_000_000_000))
        return (self.live.read_bytes(), self.live.stat().st_mtime, self.live.stat().st_mode)

    def export(self, *extra):
        return self.cli("registrations", "--export", *(extra or (str(self.live),)))

    def live_state(self):
        return (self.live.read_bytes(), self.live.stat().st_mtime, self.live.stat().st_mode)

    def test_adds_the_four_registrations_and_keeps_every_key(self):
        before = self.put(LIVE)
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.live_state(), before, "the live file moved")
        copy = json.loads(self.proposed.read_text())
        self.assertEqual(kept(LIVE, copy), [])
        self.assertTrue(is_ours(copy["statusLine"]["command"], OURS["statusLine"]))
        for e in EVENTS:
            self.assertEqual(sum(is_ours(c, OURS[e]) for c in commands(copy, e)), 1, e)
        self.assertEqual(commands(copy, "Stop")[0], "echo theirs", "an existing hook was not kept first")
        self.assertEqual(copy["hooks"]["StopFailure"][0]["matcher"], "rate_limit")
        self.assertIn("wrote", r.stdout)
        for line in ("statusLine: added", "hooks.Stop: added", "hooks.SubagentStop: added", "hooks.StopFailure: added"):
            self.assertIn(line, r.stdout)
        # key names only: no value of the live file is printed
        for value in ("echo theirs", "Bash(ls:*)", "opus", "dark"):
            self.assertNotIn(value, r.stdout + r.stderr)

    def test_the_export_changes_with_its_input(self):
        self.put(LIVE)
        self.assertEqual(self.export().returncode, 0)
        first = json.loads(self.proposed.read_text())
        other = {"theme": "light", "env": {"K": "v"}}
        self.put(other)
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        second = json.loads(self.proposed.read_text())
        self.assertNotEqual(first, second)
        self.assertEqual(kept(other, second), [])
        self.assertNotIn("model", second)
        self.assertNotIn("PreToolUse", second["hooks"])
        self.assertIn("replaced", r.stdout)

    def test_an_applied_proposal_exports_byte_identical(self):
        self.put(LIVE)
        self.assertEqual(self.export().returncode, 0)
        first = self.proposed.read_bytes()
        self.live.write_bytes(first)          # Jacob's step: copy the proposal over
        os.utime(self.proposed, (1_000_000_000, 1_000_000_000))
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.proposed.read_bytes(), first)
        self.assertEqual(self.proposed.stat().st_mtime, 1_000_000_000, "an identical proposal was rewritten")
        self.assertIn("unchanged", r.stdout)
        for line in ("statusLine: already present", "hooks.Stop: already present"):
            self.assertIn(line, r.stdout)

    def test_a_rerun_on_an_unchanged_file_is_idempotent(self):
        self.put(LIVE)
        self.assertEqual(self.export().returncode, 0)
        first = self.proposed.read_bytes()
        os.utime(self.proposed, (1_000_000_000, 1_000_000_000))
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.proposed.read_bytes(), first)
        self.assertEqual(self.proposed.stat().st_mtime, 1_000_000_000)
        self.assertIn("unchanged", r.stdout)

    def test_an_existing_different_proposal_is_replaced(self):
        self.put(LIVE)
        self.proposed.write_text('{"stale": true}\n')
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("replaced", r.stdout)
        self.assertNotIn("stale", json.loads(self.proposed.read_text()))
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".proposed." in p.name], [], "a temp file was left")

    def test_unparseable_live_file_gives_an_error_and_no_copy(self):
        for text in ('{"model": "opus",', "", "[1, 2]", '"text"', '{"a": 1, "a": 2}'):
            with self.subTest(text=text):
                before = self.put(None, text=text)
                r = self.export()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("no proposal written", r.stderr)
                self.assertFalse(self.proposed.exists())
                self.assertEqual(self.live_state(), before)

    def test_an_error_leaves_an_earlier_proposal_untouched(self):
        self.proposed.write_text('{"earlier": true}\n')
        self.put(None, text="{not json")
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.proposed.read_text(), '{"earlier": true}\n')
        self.assertIn("left as it was", r.stderr)

    def test_shapes_it_cannot_extend_are_refused(self):
        for doc in ({"hooks": []}, {"hooks": {"Stop": {"hooks": []}}}, {"statusLine": "echo x"},
                    {"hooks": {"Stop": [{"hooks": "x"}]}}):
            with self.subTest(doc=doc):
                self.put(doc)
                r = self.export()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertFalse(self.proposed.exists())

    def test_a_registration_from_another_location_is_refused(self):
        moved = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/elsewhere/usage-stop.sh"}]}]}}
        self.put(moved)
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("hooks.Stop", r.stderr)
        self.assertNotIn("/elsewhere", r.stderr)
        self.assertFalse(self.proposed.exists())

    def test_a_registration_present_twice_is_refused(self):
        cmd = shlex.quote(str(ROOT / "usage-stop.sh"))
        twice = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": cmd}]},
                                    {"hooks": [{"type": "command", "command": cmd}]}]}}
        self.put(twice)
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 times", r.stderr)
        self.assertFalse(self.proposed.exists())

    def test_an_existing_status_line_is_chained_not_lost(self):
        doc = {"statusLine": {"type": "command", "command": "echo 'mine here'", "padding": 2}}
        self.put(doc)
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("statusLine: chained", r.stdout)
        copy = json.loads(self.proposed.read_text())
        self.assertEqual(copy["statusLine"]["padding"], 2)
        self.assertEqual(shlex.split(copy["statusLine"]["command"]),
                         [str(ROOT / "usage-statusline.sh"), "echo 'mine here'"])
        # the chained command runs and both lines survive
        out = subprocess.run(["sh", "-c", copy["statusLine"]["command"]], input=json.dumps(statusline_input()),
                             capture_output=True, text=True, env=self.env("2026-10-05T10:00:00Z"), timeout=60)
        self.assertTrue(out.stdout.startswith("mine here | 5h 42"), out.stdout)
        # a second export of the applied copy chains nothing further
        self.live.write_bytes(self.proposed.read_bytes())
        first = self.proposed.read_bytes()
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.proposed.read_bytes(), first)

    def test_the_proposal_keeps_the_live_files_mode(self):
        self.put(LIVE, mode=0o600)
        self.assertEqual(self.export().returncode, 0)
        self.assertEqual(self.proposed.stat().st_mode & 0o777, 0o600)

    def test_a_proposal_path_that_is_a_link_is_refused(self):
        before = self.put(LIVE)
        self.proposed.symlink_to(self.live)
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.proposed.is_symlink())
        self.assertEqual(self.live_state(), before)

    def test_a_missing_live_file_is_an_error(self):
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.proposed.exists())

    def test_the_default_file_is_the_user_settings(self):
        user = self.home / ".claude" / "settings.json"
        user.parent.mkdir()
        user.write_text('{"theme": "dark"}\n')
        r = self.cli("registrations", "--export")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(user.read_text(), '{"theme": "dark"}\n')
        self.assertTrue((self.home / ".claude" / "settings.json.proposed").is_file())

    def test_bad_arguments_are_usage_errors(self):
        self.put(LIVE)
        for args in (["--export", str(self.live), "extra"], ["--merge-into", str(self.live)], ["x"]):
            with self.subTest(args=args):
                r = self.cli("registrations", *args)
                self.assertEqual(r.returncode, 2)
        self.assertFalse(self.proposed.exists())

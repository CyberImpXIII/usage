"""§4 "documented == implemented": README's command block is HELP, HELP's
commands are the dispatch table's, both ways, and every command the plan's §2
list names exists (the plan is read only when the workspace holds it)."""
import os
import re
import unittest

from tests.helpers import ROOT

PLAN = ROOT.parent.parent / "PLAN-usage-reporting.md"
CMD = re.compile(r"^\s*usage ([a-z-]+)", re.M)


def readme_block():
    text = (ROOT / "README.md").read_text()
    m = re.search(r"## Commands\n\n```\n(.*?)```", text, re.S)
    return m.group(1) if m else None


class Docs(unittest.TestCase):
    def test_readme_block_is_help(self):
        from usagelib import cli
        self.assertEqual(readme_block(), cli.HELP)

    def test_help_commands_are_the_dispatch_table_both_ways(self):
        from usagelib import cli
        documented = CMD.findall(cli.HELP)
        self.assertEqual(len(documented), len(set(documented)), "a command documented twice")
        self.assertEqual(sorted(documented), sorted(cli.COMMANDS))

    def test_cli_json_declares_the_dispatch_table_both_ways(self):
        """PLAN-usage-reporting.md §9 / PLAN-routing-tree.md §14.8: cli.json's
        verbs are the CLI's commands, and it names this CLI."""
        import json
        from usagelib import cli
        doc = json.loads((ROOT / "cli.json").read_text())
        self.assertEqual(doc["cli"], "usage")
        self.assertEqual(len(doc["verbs"]), len(set(doc["verbs"])), "a verb declared twice")
        self.assertEqual(sorted(doc["verbs"]), sorted(cli.COMMANDS))

    def test_cli_json_names_the_stores_ledgers(self):
        """The store it declares is the one usagelib/store.py writes: every
        ledger and limits.json, under the default store folder."""
        import json
        from usagelib import store
        doc = json.loads((ROOT / "cli.json").read_text())
        names = {s.rsplit("/", 1)[-1] for s in doc["store"] if s.startswith("~/.claude/usage/")}
        self.assertEqual(len(names), len(doc["store"]), "a store path outside ~/.claude/usage/")
        self.assertEqual(names - {"*"}, set(store.COLUMNS) | {store.LIMITS})
        self.assertIn("~/.claude/usage/state/*", doc["store"])

    def test_each_documented_command_answers(self):
        """Implemented means it runs: `usage <cmd> --bad` is a usage error (2) or
        the command's own answer, never an unknown-command error."""
        import subprocess
        from usagelib import cli
        for name in cli.COMMANDS:
            if name == "check":
                continue
            env = dict(os.environ, USAGE_STORE=str(ROOT / "tests" / "no-such-store"))
            r = subprocess.run([str(ROOT / "usage"), name, "--no-such-flag"], capture_output=True, text=True, env=env)
            self.assertNotIn("unknown command", r.stderr, name)
            self.assertEqual(r.returncode, 2, name)
        self.assertFalse((ROOT / "tests" / "no-such-store").exists())

    def test_the_plans_commands_exist(self):
        if not PLAN.is_file():
            self.skipTest(f"{PLAN.name} is not beside this repo (a fresh clone)")
        from usagelib import cli
        text = PLAN.read_text()
        m = re.search(r"## 2\. .*?```\n(.*?)```", text, re.S)
        self.assertIsNotNone(m, "the plan's §2 command block moved")
        planned = set(CMD.findall(m.group(1)))
        self.assertTrue(planned, "no commands read from the plan")
        self.assertEqual(sorted(planned - set(cli.COMMANDS)), [], "planned but not implemented")

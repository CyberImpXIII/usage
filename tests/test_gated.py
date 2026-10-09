"""PLAN-architecture-review.md §4 V1, "the store is gated": `usage init` writes a
cli.json into the store folder, because the store hook looks for one in each
folder above a write target and this repo's cli.json (`~/.claude/usage/...`)
is above nothing there.

The seams: the two cli.json files name the same files and verbs; every file
the reporters write is named; `status` says when the store's copy is missing or
stale; and the real hook (StoreGuard, also run by `./dev.sh check`'s `guard`
gate) refuses a write after `init` and lets the same write through before it."""
import importlib.util
import json
import os
import shutil
import subprocess
import unittest
from fnmatch import fnmatch

from tests.helpers import FIXTURES, ROOT, Case, statusline_input


def check_module():
    spec = importlib.util.spec_from_file_location("usage_check", ROOT / "devtools" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Contract(Case):
    def test_the_repo_cli_json_names_the_same_files_and_verbs(self):
        from usagelib import cli, store
        doc = json.loads((ROOT / "cli.json").read_text())
        self.assertEqual(doc["store"], ["~/.claude/usage/" + f for f in store.STORE_FILES])
        self.assertEqual(doc["verbs"], list(cli.COMMANDS))

    def test_init_writes_the_store_cli_json_once(self):
        from usagelib import cli, store
        r = self.cli("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cli.json written", r.stdout)
        doc = json.loads((self.store / "cli.json").read_text())
        self.assertEqual(doc, {"store": store.STORE_FILES, "cli": str(ROOT / "usage"), "verbs": list(cli.COMMANDS)})
        self.assertTrue(os.path.isabs(doc["cli"]) and os.access(doc["cli"], os.X_OK))
        self.assertIn("cli.json unchanged", self.cli("init").stdout)

    def test_status_says_when_the_gate_is_missing_or_stale(self):
        def gate_line():
            return [ln for ln in self.cli("status").stdout.splitlines() if ln.startswith("store gate:")]
        self.assertEqual(gate_line(), ["store gate: cli.json missing in %s -- run `usage init`" % self.store])
        self.cli("init")
        self.assertEqual(gate_line(), [], "counterfactual: a current cli.json says nothing")
        p = self.store / "cli.json"
        doc = json.loads(p.read_text())
        doc["verbs"].remove("status")
        p.write_text(json.dumps(doc))
        self.assertEqual(gate_line(), ["store gate: cli.json differs in %s -- run `usage init`" % self.store])
        self.cli("init")
        self.assertEqual(gate_line(), [])

    def test_every_file_the_reporters_write_is_named(self):
        from usagelib import store
        self.cli("init")
        self.script("statusline", statusline_input(), now="2026-10-05T10:00:00Z")
        t = self.tmp / "t.jsonl"
        shutil.copy(FIXTURES / "transcript" / "turn1.jsonl", t)
        self.script("stop", {"hook_event_name": "Stop", "transcript_path": str(t)}, now="2026-10-05T10:01:00Z")
        self.script("failure", {"hook_event_name": "StopFailure", "error": "rate_limit"}, now="2026-10-05T10:02:00Z")
        self.script("failure", "{cut", now="2026-10-05T10:03:00Z")  # a recorded failure
        written = [str(p.relative_to(self.store)) for p in self.store.rglob("*")
                   if p.is_file() and p.name not in (".lock", store.CONTRACT)]
        for f in ("samples.tsv", "tokens.tsv", "hits.tsv", "limits.json", "failures.tsv"):
            self.assertIn(f, written, "the run did not write it, so this test proves nothing about it")
        self.assertTrue(any(w.startswith("state/") for w in written))
        unnamed = [w for w in written if not any(fnmatch(w, g) for g in store.STORE_FILES)]
        self.assertEqual(unnamed, [], "a store file the store's cli.json does not name")


class StoreGuard(Case):
    """The real hook, run as Claude Code runs it, on a scratch store."""

    def setUp(self):
        super().setUp()
        self.hook = check_module().guard_hook()
        if self.hook is None:
            self.skipTest("no store hook installed in .claude/hooks/ (and no $USAGE_STORE_GUARD): "
                          "`./dev.sh check` reports the guard gate UNCHECKED")

    def guard(self, tool, target=None, command=None):
        ti = {"command": command} if tool == "Bash" else {"file_path": str(target), "content": "x\n"}
        data = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti,
                "cwd": str(self.tmp)}
        return subprocess.run(["bash", str(self.hook)], input=json.dumps(data), capture_output=True, text=True,
                              env=dict(os.environ, HOME=str(self.home)), timeout=60)

    def test_a_hand_write_to_a_ledger_is_refused_only_once_gated(self):
        targets = [self.store / n for n in ("hits.tsv", "tokens.tsv", "failures.tsv", "limits.json")]
        targets.append(self.store / "state" / "0123.json")
        for t in targets:
            r = self.guard("Write", t)
            self.assertEqual(r.returncode, 0, f"before init, nothing names {t.name}: {r.stderr}")
        self.assertEqual(self.cli("init").returncode, 0)
        for t in targets:
            r = self.guard("Write", t)
            self.assertEqual(r.returncode, 2, f"{t.name}: {r.stdout}{r.stderr}")
            self.assertIn(str(ROOT / "usage"), r.stderr, "the refusal names the CLI")
        r = self.guard("Write", self.store / "notes.txt")
        self.assertEqual(r.returncode, 0, "a file the store does not name passes: " + r.stderr)

    def test_a_shell_append_is_refused_when_the_hook_reads_shell_commands(self):
        lib = self.hook.parent.parent / "lib" / "write-targets.sh"
        if not lib.is_file():
            self.skipTest(f"no {lib}: the hook lets every Bash command through, by its own header")
        cmd = f"echo x >> {self.store}/hits.tsv"
        self.assertEqual(self.guard("Bash", command=cmd).returncode, 0, "before init")
        self.cli("init")
        self.assertEqual(self.guard("Bash", command=cmd).returncode, 2)


if __name__ == "__main__":
    unittest.main()

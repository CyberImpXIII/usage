"""`usage gate --todo ID` (PLAN-architecture-review.md item 7): the token
estimate is the todo item's `size`, read through tools/todo's CLI (`todo get`),
mapped by usage.json `size_tokens`. Unsized, L, closed or unreadable: exit 3,
"unknown, unchecked", no number.

RealTodo runs the real todo CLI on a scratch store (skipped, by name, when
tools/todo is absent); Stub holds the paths the real CLI cannot easily give
(no CLI, a failing one, output off the shape), with a script that prints a
fixed answer."""
import json
import os
import stat
import subprocess

from tests.helpers import ROOT, Case

R5 = "2026-10-05T15:00:00Z"
R7 = "2026-10-12T09:00:00Z"
TODO = ROOT.parent / "todo" / "todo"


def size_tokens():
    return json.loads((ROOT / "usage.json").read_text())["size_tokens"]


class Slope(Case):
    """5h rises 10 points per 1,000,000 turn tokens, at 20% now: 1e-5 %/token."""

    def setUp(self):
        super().setUp()
        self.put_samples([("2026-10-05T10:00:00Z", 10, 20, R5, R7),
                          ("2026-10-05T10:30:00Z", 15, 20.5, R5, R7),
                          ("2026-10-05T11:00:00Z", 20, 21, R5, R7)])
        self.put_tokens([("2026-10-05T10:20:00Z", "a", 600000, "turn"),
                         ("2026-10-05T10:50:00Z", "b", 400000, "turn")])
        self.put_limits("2026-10-05T11:00:00Z", 20, 21, R5, R7)
        self.now = "2026-10-05T11:05:00Z"

    def gate(self, item):
        return self.cli("gate", "--todo", item)

    def assert_same_as_numeric(self, r, size, item):
        n = size_tokens()[size]
        plain = self.cli("gate", str(n))
        lines = r.stdout.splitlines()
        self.assertEqual(r.returncode, plain.returncode, r.stdout)
        self.assertEqual(lines[0], plain.stdout.splitlines()[0])
        self.assertEqual(lines[1], f"  estimate: todo:{item} size {size} = {n} tokens (usage.json size_tokens)")

    def assert_unchecked(self, r, *words):
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("unknown, unchecked: "), r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)
        for w in words:
            self.assertIn(w, r.stdout)
        for n in size_tokens().values():
            self.assertNotIn(str(n), r.stdout, "an unchecked answer carries no estimate")


class RealTodo(Slope):
    def setUp(self):
        super().setUp()
        if not (TODO.is_file() and os.access(TODO, os.X_OK)):
            self.skipTest(f"no todo CLI at {TODO}: the Stub cases still run")
        self.work = self.tmp / "work"  # a repo with a store; gate runs from self.tmp, which has none
        self.work.mkdir()
        subprocess.run(["git", "init", "-q", str(self.work)], check=True, capture_output=True)
        self.todo("init", "--prefix", "ug", "--repo", "work")
        self.todo("add", "sized", "--kind", "note", "--size", "S")      # ug-1
        self.todo("add", "unsized", "--kind", "note")                   # ug-2
        self.todo("add", "large", "--kind", "note", "--size", "L")      # ug-3

    def todo_env(self):
        """No TODO_ROOT: todo finds the store from the folder it runs in, as from the workspace root."""
        e = dict(self.env(), USAGE_TODO_CLI=str(TODO), USAGE_TODO_DIR=str(self.tmp))
        e.pop("TODO_ROOT", None)
        return e

    def todo(self, *args):
        r = subprocess.run([str(TODO), "-C", str(self.work), *args], capture_output=True, text=True,
                           env=self.todo_env(), timeout=60)
        self.assertEqual(r.returncode, 0, f"todo {args}: {r.stdout}{r.stderr}")
        return r

    def gate(self, item):
        return subprocess.run([str(ROOT / "usage"), "gate", "--todo", item], capture_output=True, text=True,
                              env=self.todo_env(), timeout=120)

    def test_the_size_is_the_estimate_and_changing_it_changes_the_answer(self):
        self.assertFalse((self.tmp / "todo.json").exists(), "the folder gate runs from holds no store")
        s = self.gate("ug-1")
        self.assert_same_as_numeric(s, "S", "ug-1")
        self.todo("edit", "ug-1", "--size", "M")
        m = self.gate("ug-1")
        self.assert_same_as_numeric(m, "M", "ug-1")
        self.assertNotEqual(s.stdout.splitlines()[1], m.stdout.splitlines()[1])
        # with today's size_tokens, S fits and M does not: the size changes the verdict, not only the line
        self.assertEqual((s.returncode, m.returncode), (0, 1), s.stdout + m.stdout)

    def test_unsized_and_l_refuse_to_guess(self):
        self.assert_unchecked(self.gate("ug-2"), "todo:ug-2 is unsized")
        self.assert_unchecked(self.gate("ug-3"), "todo:ug-3 is L", "split")
        self.todo("edit", "ug-2", "--size", "S")  # counterfactual: sized, it is answered
        self.assert_same_as_numeric(self.gate("ug-2"), "S", "ug-2")

    def test_an_unknown_id_is_unchecked(self):
        self.assert_unchecked(self.gate("ug-99"), "todo get ug-99 exit 1")
        self.assert_unchecked(self.gate("zz-1"), "todo get zz-1 exit 3")

    def test_todo_s_sizes_are_the_ones_usage_reads(self):
        """The vocabulary seam: every size todo ready takes (S, M) has an
        estimate, and the size todo says to split (L) has none."""
        self.assertEqual(set(size_tokens()), {"S", "M"})
        r = subprocess.run([str(TODO), "-C", str(self.work), "add", "x", "--kind", "note", "--size", "XL"],
                           capture_output=True, text=True, env=self.todo_env(), timeout=60)
        self.assertNotEqual(r.returncode, 0, "todo accepts a size usage does not know")
        self.assertIn("choose from S, M, L", r.stderr)


class VocabSeam(Case):
    def test_size_tokens_covers_exactly_todo_s_ready_sizes(self):
        """By meaning: usage.json size_tokens has an estimate for every size
        tools/todo marks `ready` and for none it marks not ready (split first).
        todo's CLI prints no vocabulary, so this reads its vocab.json, read
        only; skipped by name when tools/todo is absent. Catches what the
        XL probe in RealTodo cannot: a size whose `ready` flag flips."""
        vocab = ROOT.parent / "todo" / "vocab.json"
        if not vocab.is_file():
            self.skipTest(f"no {vocab}")
        sizes = {k: v for k, v in json.loads(vocab.read_text())["sizes"].items() if not k.startswith("_")}
        ready = {k for k, v in sizes.items() if v.get("ready") is True}
        self.assertTrue(ready, f"todo's vocab names no ready size: {sizes}")
        self.assertEqual(set(size_tokens()), ready, "usage.json size_tokens vs todo's ready sizes")


class Workspace(Case):
    def test_the_default_folder_is_where_todo_scans_from(self):
        """todo's rule for a folder with no store: it scans from there when it
        is in no git repo. WORKSPACE must be such a folder, holding the
        workspace's CLAUDE.md, or `gate --todo` asks the wrong place."""
        from usagelib import todo
        ws = todo.WORKSPACE
        self.assertTrue((ws / "CLAUDE.md").is_file(), ws)
        self.assertIn(ws, ROOT.parents)
        r = subprocess.run(["git", "-C", str(ws), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, f"{ws} is inside a git repo: {r.stdout}")


class Stub(Slope):
    def stub(self, stdout, code=0):
        p = self.tmp / "todo-stub"
        (self.tmp / "answer").write_text(stdout)
        p.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{self.tmp}/args"\ncat "{self.tmp}/answer"\nexit {code}\n')
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def gate(self, item, cli=None):
        env = self.env()
        env["USAGE_TODO_CLI"] = str(cli or self.tmp / "absent")
        return subprocess.run([str(ROOT / "usage"), "gate", "--todo", item], capture_output=True, text=True,
                              env=env, timeout=120)

    def record(self, **fields):
        return json.dumps({"id": "todo:x-1", "closed": False, "tags": [], "record": dict({"id": "x-1"}, **fields)})

    def test_it_asks_todo_get(self):
        r = self.gate("x-1", self.stub(self.record(size="S")))
        self.assert_same_as_numeric(r, "S", "x-1")
        self.assertEqual((self.tmp / "args").read_text(), "get x-1\n")

    def test_no_size_field_is_unsized(self):
        self.assert_unchecked(self.gate("x-1", self.stub(self.record())), "unsized")
        self.assert_unchecked(self.gate("x-1", self.stub(self.record(size=None))), "unsized")

    def test_a_size_with_no_estimate(self):
        self.assert_unchecked(self.gate("x-1", self.stub(self.record(size="XL"))), "'XL'", "no estimate")

    def test_closed(self):
        doc = json.loads(self.record(size="S"))
        doc["closed"] = True
        self.assert_unchecked(self.gate("x-1", self.stub(json.dumps(doc))), "closed")

    def test_no_cli_a_failing_cli_and_off_shape_output(self):
        self.assert_unchecked(self.gate("x-1"), "no todo CLI at")
        self.assert_unchecked(self.gate("x-1", self.stub("boom", code=1)), "exit 1: boom")
        self.assert_unchecked(self.gate("x-1", self.stub("not json")), "printed no {record} object")
        self.assert_unchecked(self.gate("x-1", self.stub('{"record": "S"}')), "printed no {record} object")

    def test_bad_arguments_are_usage_errors(self):
        for args in (["--todo"], ["--todo", "--json"], ["--todo", "a", "b"], ["--toda", "x-1"]):
            self.assertEqual(self.cli("gate", *args).returncode, 2, args)

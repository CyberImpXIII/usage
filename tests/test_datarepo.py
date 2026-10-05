"""PLAN-repo-setup.md §7.11: `usage export | import | verify` and the contract in
usagelib/datarepo.py. Every data repo here is a scratch folder (helpers' env
drops the caller's DATA_REPO). The seams: CLASSES == what the store holds,
both ways; the export carries only the allowlist; verify's report is what
tools/checks' stores-exported reads (run for real when that sibling is here);
a credential-shaped cell stops export and import, named by place only."""
import json
import os
import shutil
import subprocess
import unittest

from tests.helpers import FIXTURES, ROOT, Case, statusline_input

SESSION = "0b5c3f8e-1d2a-4c6b-9e7f-0123456789ab"
CHECKS = ROOT.parent / "checks" / "checks"


def credential():
    """A credential-shaped value, assembled here so no literal sits in the source."""
    return "sk" + "-ant-" + "api03" + "Q" * 40


class DataRepo(Case):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.out = self.data / "usage"

    def fill(self):
        """A store written by the real reporters: every file kind they make."""
        self.script("statusline", statusline_input(42.0, 18.0), now="2026-10-05T10:00:00Z")
        t = self.tmp / "t.jsonl"
        shutil.copy(FIXTURES / "transcript" / "turn1.jsonl", t)
        self.script("stop", {"hook_event_name": "Stop", "transcript_path": str(t), "session_id": SESSION},
                    now="2026-10-05T10:01:00Z")
        self.script("failure", {"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": SESSION,
                                "agent_type": "general-purpose"}, now="2026-10-05T10:02:00Z")

    def verify(self, *extra, data=None):
        r = self.cli("verify", "--json", *extra, data=self.data if data is None else data)
        return r.returncode, json.loads(r.stdout)

    def statuses(self, report):
        return {i["item"]: (i["status"], i.get("detail", "")) for i in report["items"]}

    # -- the contract's classification, both ways

    def test_classes_are_what_the_store_holds_both_ways(self):
        from usagelib import datarepo, store
        self.assertEqual(set(datarepo.CLASSES), set(store.COLUMNS) | {store.LIMITS, "state/", ".lock"})
        self.fill()
        held = {p.name + ("/" if p.is_dir() else "") for p in self.store.iterdir()}
        self.assertEqual(held, set(datarepo.CLASSES), "a store file the contract does not classify, or a class "
                                                      "the reporters never write")
        self.assertEqual(sorted(datarepo.LEDGERS), sorted(store.COLUMNS))
        self.assertEqual(set(datarepo.SHAPES), set(store.COLUMNS))
        for name, cols in store.COLUMNS.items():
            self.assertEqual(list(datarepo.SHAPES[name]), cols, name)

    # -- export, verify, and their counterfactuals

    def test_round_trip_then_a_store_change_then_export(self):
        self.fill()
        code, rep = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual({s for s, _ in self.statuses(rep).values()}, {"missing"})
        r = self.cli("export", data=self.data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("4 files written", r.stdout)
        self.assertEqual(sorted(os.listdir(self.out)), ["hits.tsv", "manifest.json", "samples.tsv", "tokens.tsv"],
                         "only the allowlist reaches the data repo: never limits.json or state/")
        for n in ("samples.tsv", "tokens.tsv", "hits.tsv"):
            self.assertEqual((self.out / n).read_text(), (self.store / n).read_text(), n)
        code, rep = self.verify()
        self.assertEqual(code, 0, rep)
        self.assertEqual({s for s, _ in self.statuses(rep).values()}, {"same"})
        self.assertIn("4 unchanged", self.cli("export", data=self.data).stdout)
        self.script("statusline", statusline_input(50.0, 19.0), now="2026-10-05T10:03:00Z")  # a write, no export
        code, rep = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(rep)["ledger:samples.tsv"],
                         ("differs", "the store has 1 row(s) the export lacks (run `usage export`)"))
        self.assertEqual(self.statuses(rep)["ledger:tokens.tsv"][0], "same")
        self.cli("export", data=self.data)
        self.assertEqual(self.verify()[0], 0)

    def test_an_edited_or_extra_export_file_is_red(self):
        self.fill()
        self.cli("export", data=self.data)
        p = self.out / "hits.tsv"
        p.write_text(p.read_text().replace("2026-10-05T10:02:00Z", "2026-10-05T10:09:00Z", 1))
        (self.out / "notes.txt").write_text("x\n")
        code, rep = self.verify()
        self.assertEqual(code, 1)
        st = self.statuses(rep)
        self.assertEqual(st["ledger:hits.tsv"], ("differs", "first difference at row 1 (store 1 rows, export 1)"))
        self.assertEqual(st["file:notes.txt"][0], "missing")
        p.write_text("not\ta\tledger\n")
        self.assertIn("not as `usage export` writes it", self.statuses(self.verify()[1])["ledger:hits.tsv"][1])

    def test_a_malformed_store_line_is_named_and_not_exported(self):
        self.put_tokens([("2026-10-05T10:00:00Z", "", 100, "turn")])
        with open(self.store / "tokens.tsv", "a") as fh:
            fh.write("half\ta row\n")
        r = self.cli("export", data=self.data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tokens.tsv: 1 malformed line(s) not exported (the reader skips them too): 3", r.stdout)
        self.assertNotIn("half", (self.out / "tokens.tsv").read_text())
        self.assertEqual(self.verify()[0], 0)

    # -- refusals

    def test_data_repo_unset_or_not_a_folder_and_no_store_are_refused(self):
        for verb in ("export", "import", "verify"):
            r = self.cli(verb)
            self.assertEqual(r.returncode, 2, verb)
            self.assertIn("DATA_REPO is not set", r.stderr)
            r = self.cli(verb, data=self.tmp / "nowhere")
            self.assertEqual(r.returncode, 2, verb)
            self.assertIn("is not a folder", r.stderr)
        shutil.rmtree(self.store)
        r = self.cli("export", data=self.data)
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing to export (usage init)", r.stderr)
        self.assertFalse(self.out.exists())

    def test_a_folder_that_is_not_this_tools_export_is_not_written(self):
        self.fill()
        self.out.mkdir()
        (self.out / "theirs.txt").write_text("keep\n")
        r = self.cli("export", data=self.data)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no manifest.json naming usage", r.stderr)
        self.assertEqual(os.listdir(self.out), ["theirs.txt"])

    def test_a_credential_shaped_cell_stops_the_export_named_by_place_never_by_value(self):
        secret = credential()
        self.put_tokens([("2026-10-05T10:00:00Z", "", 1, "turn"), ("2026-10-05T10:01:00Z", secret, 1, "turn")])
        for args in ((), ("--json",)):
            r = self.cli("export", *args, data=self.data)
            self.assertEqual(r.returncode, 2, args)
            said = r.stdout + r.stderr
            self.assertIn("tokens.tsv line 3, agent_type: credential-shaped", said)
            self.assertNotIn(secret, said)
            self.assertNotIn(secret[:12], said)
            self.assertFalse(self.out.exists(), "a refused export writes nothing")
        self.put_tokens([("2026-10-05T10:00:00Z", "general-purpose", 1, "turn")])  # counterfactual: clean
        self.assertEqual(self.cli("export", data=self.data).returncode, 0)

    def test_a_misshapen_cell_stops_the_export(self):
        self.put_tokens([("2026-10-05T10:00:00Z", "has space", 1, "sideways")])
        r = self.cli("export", data=self.data)
        self.assertEqual(r.returncode, 2)
        self.assertIn("tokens.tsv line 2, agent_type: not the column's shape", r.stderr)
        self.assertIn("tokens.tsv line 2, basis: not the column's shape", r.stderr)
        self.assertNotIn("sideways", r.stderr)

    # -- import

    def test_import_recreates_the_ledgers_and_never_writes_into_a_store_with_rows(self):
        self.fill()
        self.cli("export", data=self.data)
        before = {n: (self.store / n).read_text() for n in ("samples.tsv", "tokens.tsv", "hits.tsv")}
        r = self.cli("import", data=self.data)  # the store holds rows
        self.assertEqual(r.returncode, 2)
        self.assertIn("import never writes into an existing store", r.stderr)
        self.assertEqual({n: (self.store / n).read_text() for n in before}, before)
        shutil.rmtree(self.store)
        r = self.cli("import", data=self.data)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("verify: 4 items: 4 same", r.stdout)
        self.assertEqual({n: (self.store / n).read_text() for n in before}, before)
        self.assertFalse((self.store / "limits.json").exists())
        self.assertFalse((self.store / "state").exists())
        self.assertEqual(self.verify()[0], 0)

    def test_a_bad_export_is_refused_before_the_store_is_touched(self):
        self.fill()
        self.cli("export", data=self.data)
        shutil.rmtree(self.store)
        p = self.out / "tokens.tsv"
        good = p.read_text()
        lines = good.splitlines()
        cells = lines[1].split("\t")
        cells[1] = credential()
        p.write_text("\n".join([lines[0], "\t".join(cells), *lines[2:]]) + "\n")
        r = self.cli("import", data=self.data)
        self.assertEqual(r.returncode, 2)
        self.assertIn("tokens.tsv line 2, session: credential-shaped", r.stderr)
        self.assertNotIn(credential(), r.stderr)
        self.assertFalse(self.store.exists(), "a refused import creates no store")
        p.write_text(good + "torn\n")
        r = self.cli("import", data=self.data)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not as `usage export` writes it (line", r.stderr)
        (self.out / "manifest.json").write_text(json.dumps({"tool": "usage", "format": 99}))
        self.assertIn("export format 99", self.cli("import", data=self.data).stderr)
        self.assertFalse(self.store.exists())

    # -- the seam with tools/checks

    def test_the_report_has_only_the_items_shape(self):
        self.fill()
        _, rep = self.verify()
        self.assertEqual(list(rep), ["items"])
        self.assertTrue(rep["items"])
        for i in rep["items"]:
            self.assertLessEqual(set(i), {"item", "status", "detail"})
            self.assertIn(i["status"], ("same", "differs", "missing", "unavailable"))
        self.assertEqual(len({i["item"] for i in rep["items"]}), len(rep["items"]))

    def test_stores_exported_reads_this_verify(self):
        """tools/checks' stores-exported, run for real on this repo against a
        scratch store and data repo: red before export, green after, red again
        after a write the export did not follow."""
        if not CHECKS.is_file():
            self.skipTest(f"tools/checks is not beside this repo ({CHECKS})")
        self.fill()

        def one():
            r = subprocess.run([str(CHECKS), "one", "stores-exported", str(ROOT), "--json"], capture_output=True,
                               text=True, env=self.env(data=self.data), timeout=300)
            return json.loads(r.stdout)["results"][0]

        res = one()
        self.assertEqual(res["status"], "fail", res)
        self.assertIn("ledger:samples.tsv: missing", "\n".join(res["lines"]))
        self.cli("export", data=self.data)
        self.assertEqual(one()["status"], "ok")
        self.script("failure", {"hook_event_name": "StopFailure", "error": "rate_limit"}, now="2026-10-05T10:04:00Z")
        res = one()
        self.assertEqual(res["status"], "fail", res)
        self.assertIn("ledger:hits.tsv: differs", "\n".join(res["lines"]))


class NoRealDataRepo(Case):
    def test_the_callers_data_repo_never_reaches_a_test(self):
        old = os.environ.get("DATA_REPO")
        os.environ["DATA_REPO"] = str(self.tmp / "real")
        try:
            self.assertNotIn("DATA_REPO", self.env())
            self.assertEqual(self.env(data=self.tmp / "s")["DATA_REPO"], str(self.tmp / "s"))
        finally:
            if old is None:
                os.environ.pop("DATA_REPO")
            else:
                os.environ["DATA_REPO"] = old


if __name__ == "__main__":
    unittest.main()

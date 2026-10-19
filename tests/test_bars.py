"""PLAN-routing-tree.md §27's gates for `usage bars` and bars.json: each row
changes the output, a source that does not answer is `unknown` with a reason
(never 0), a value older than refresh is `stale` with its age, `usage status`
and `usage bars` agree on the two windows, and a row naming a window, service
or verb that does not exist fails the `bars` check gate (fixtures). Each with
its counterfactual."""
import json
import re
import subprocess
import sys

from tests.helpers import ROOT, Case, epoch

NOW = "2026-10-19T12:00:00Z"
R5, R7 = "2026-10-19T15:00:00Z", "2026-10-23T09:00:00Z"
REAL = json.loads((ROOT / "bars.json").read_text())


def by_name(rows):
    return {r["name"]: r for r in rows}


class BarsCase(Case):
    def setUp(self):
        super().setUp()
        self.now = NOW
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        self.registry = None

    def env(self, now=None, data=None):
        e = super().env(now, data)
        e["USAGE_TOOLS_DIR"] = str(self.tools)
        if self.registry is not None:
            e["USAGE_BARS"] = str(self.registry)
        else:
            e.pop("USAGE_BARS", None)
        return e

    def use(self, rows):
        self.registry = self.tmp / "bars.json"
        self.registry.write_text(json.dumps({"bars": rows}))

    def bars(self, now=None):
        r = self.cli("bars", "--json", now=now)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def service(self, name="stub", verbs=("share",), out='{"pct": 12.5}', code=0, sleep=0):
        d = self.tools / name
        d.mkdir(exist_ok=True)
        (d / "cli.json").write_text(json.dumps({"cli": name, "verbs": list(verbs)}))
        (d / "out").write_text(out)
        cli = d / name
        cli.write_text(f'#!/usr/bin/env bash\nsleep {sleep}\necho "$@" > "$(dirname "$0")/argv"\n'
                       f'cat "$(dirname "$0")/out"\nexit {code}\n')
        cli.chmod(0o755)
        return d

    def check_gate(self):
        return subprocess.run([sys.executable, str(ROOT / "devtools" / "check.py"), "bars"], cwd=ROOT,
                              capture_output=True, text=True, env=self.env(), timeout=60)


def window_row(name, key, refresh=900):
    return {"name": name, "label": name, "window": "5h", "refresh": refresh, "source": {"window": key}}


def service_row(**src):
    s = {"service": "stub", "verb": "share", "key": "pct", "timeout_s": 5}
    s.update(src)
    return {"name": "svc", "label": "svc", "window": "7d", "refresh": 3600, "source": s}


class TheRegistry(BarsCase):
    def test_the_three_rows_jacob_named(self):
        self.assertEqual([r["name"] for r in REAL["bars"]], ["session", "week", "fable-week"])
        self.assertEqual(REAL["bars"][0]["source"], {"window": "five_hour"})
        self.assertEqual(REAL["bars"][1]["source"], {"window": "seven_day"})
        self.assertIn("pending", REAL["bars"][2]["source"])

    def test_each_row_changes_the_output(self):
        self.put_limits(NOW, 42.0, 18.0, R5, R7)
        base = by_name(self.bars())
        self.assertEqual(list(base), ["session", "week", "fable-week"])
        self.assertEqual((base["session"]["pct"], base["session"]["state"]), (42.0, "ok"))
        self.assertEqual((base["week"]["pct"], base["week"]["state"]), (18.0, "ok"))
        self.assertEqual(base["session"]["asof"], NOW)
        # the 5h input moves only the session row, the 7d only the week row
        self.put_limits(NOW, 55.0, 18.0, R5, R7)
        moved = by_name(self.bars())
        self.assertEqual((moved["session"]["pct"], moved["week"]["pct"]), (55.0, 18.0))
        self.put_limits(NOW, 42.0, 31.0, R5, R7)
        moved = by_name(self.bars())
        self.assertEqual((moved["session"]["pct"], moved["week"]["pct"]), (42.0, 31.0))
        # the Fable row: unknown, with the registry's reason (and it is the registry's: change it, it changes)
        f = base["fable-week"]
        self.assertEqual((f["state"], f["pct"], f["source"]), ("unknown", None, "pending"))
        self.assertIn(REAL["bars"][2]["source"]["pending"], f["reason"])
        rows = json.loads(json.dumps(REAL["bars"]))
        rows[2]["source"]["pending"] = "another reason"
        self.use(rows)
        self.assertEqual(by_name(self.bars())["fable-week"]["reason"], "no source yet: another reason")
        # a row added is a bar added; a row removed is a bar gone
        self.use(REAL["bars"][:1] + [window_row("again", "seven_day")])
        self.assertEqual([(b["name"], b["pct"]) for b in self.bars()], [("session", 42.0), ("again", 31.0)])

    def test_the_output_shape(self):
        self.put_limits(NOW, 42.0, 18.0, R5, R7)
        for b in self.bars():
            self.assertEqual(list(b), ["name", "label", "pct", "window", "asof", "age_s", "source", "state",
                                       "reason"])


class Unknown(BarsCase):
    def assert_unknown(self, bar, why):
        self.assertEqual(bar["state"], "unknown")
        self.assertIsNone(bar["pct"], "an unanswered source is never a number, 0 least of all")
        self.assertIsNone(bar["asof"])
        self.assertRegex(bar["reason"], why)

    def test_no_store(self):
        self.store.rmdir()
        for b in self.bars():
            self.assert_unknown(b, "no store|no source yet")

    def test_no_sample_then_a_sample(self):
        b = by_name(self.bars())
        self.assert_unknown(b["session"], "no status-line sample")
        self.put_limits(NOW, 42.0, 18.0, R5, R7)
        self.assertEqual(by_name(self.bars())["session"]["state"], "ok")

    def test_a_window_absent_from_the_sample(self):
        self.put_limits(NOW, None, 18.0, None, R7)
        b = by_name(self.bars())
        self.assert_unknown(b["session"], r"rate_limits\.five_hour is not in the sample")
        self.assertEqual((b["week"]["state"], b["week"]["pct"]), ("ok", 18.0))

    def test_a_zero_is_a_value_not_an_absence(self):
        self.put_limits(NOW, 0.0, 18.0, R5, R7)
        b = by_name(self.bars())["session"]
        self.assertEqual((b["state"], b["pct"], b["reason"]), ("ok", 0.0, None))

    def test_a_sample_dated_in_the_future(self):
        self.put_limits("2026-10-19T13:00:00Z", 42.0, 18.0, R5, R7)
        self.assert_unknown(by_name(self.bars())["session"], "in the future")

    def test_an_invalid_row_is_unknown_and_the_others_still_answer(self):
        self.put_limits(NOW, 42.0, 18.0, R5, R7)
        self.use([window_row("good", "five_hour"), window_row("bad", "fable_week")])
        b = by_name(self.bars())
        self.assertEqual(b["good"]["pct"], 42.0)
        self.assert_unknown(b["bad"], "bars.json: source window 'fable_week' is not one of")

    def test_no_registry_is_exit_1_and_no_list(self):
        self.registry = self.tmp / "absent.json"
        r = self.cli("bars", "--json")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("cannot read", r.stderr)


class Stale(BarsCase):
    def test_older_than_refresh_is_stale_with_its_age(self):
        self.put_limits("2026-10-19T11:44:00Z", 42.0, 18.0, R5, R7)  # 16 min: past 900 s
        b = by_name(self.bars())["session"]
        self.assertEqual((b["state"], b["pct"], b["age_s"]), ("stale", 42.0, 960))
        self.assertEqual(b["reason"], "16 min old (refresh 900 s)")
        self.put_limits("2026-10-19T11:46:00Z", 42.0, 18.0, R5, R7)  # 14 min: within
        b = by_name(self.bars())["session"]
        self.assertEqual((b["state"], b["age_s"], b["reason"]), ("ok", 840, None))

    def test_refresh_is_the_rows_own(self):
        self.put_limits("2026-10-19T11:44:00Z", 42.0, 18.0, R5, R7)
        self.use([window_row("long", "five_hour", refresh=3600)])
        self.assertEqual(self.bars()[0]["state"], "ok")


class AgreesWithStatus(BarsCase):
    """`usage status` and `usage bars` read the same sample the same way: the
    same number per window, and stale at the same age (refresh 900 s ==
    sample_stale_minutes)."""
    def status(self):
        r = self.cli("status")
        nums = dict(re.findall(r"^(5h|7d): ([\d.]+)%", r.stdout, re.M))
        return nums, "STALE" in r.stdout

    def test_same_numbers_and_same_staleness(self):
        from usagelib import config
        stale_s = config.setting("sample_stale_minutes") * 60
        for t, p5, p7 in (("2026-10-19T11:59:00Z", 42.25, 18.0), ("2026-10-19T11:44:00Z", 61.0, 33.5)):
            self.put_limits(t, p5, p7, R5, R7)
            nums, st_stale = self.status()
            b = by_name(self.bars())
            self.assertEqual(nums, {"5h": f"{b['session']['pct']:.1f}", "7d": f"{b['week']['pct']:.1f}"})
            self.assertEqual(st_stale, b["session"]["state"] == "stale", t)
            self.assertEqual(st_stale, b["week"]["state"] == "stale", t)
        for row in REAL["bars"][:2]:
            self.assertEqual(row["refresh"], stale_s, row["name"])


class ServiceSource(BarsCase):
    def test_a_service_verb_is_run_and_its_key_read(self):
        self.service(out='{"pct": 12.5, "other": 3}')
        self.use([service_row(args=["--json"])])
        b = self.bars()[0]
        self.assertEqual((b["state"], b["pct"], b["asof"], b["source"]), ("ok", 12.5, NOW, "stub share --json :pct"))
        self.assertEqual((self.tools / "stub" / "argv").read_text().strip(), "share --json")
        self.use([service_row(key="other")])
        self.assertEqual(self.bars()[0]["pct"], 3.0)

    def test_asof_key_sets_the_time_and_so_staleness(self):
        self.service(out=json.dumps({"pct": 7, "at": "2026-10-19T10:00:00Z"}))
        self.use([service_row(asof_key="at")])
        b = self.bars()[0]
        self.assertEqual((b["state"], b["asof"], b["age_s"]), ("stale", "2026-10-19T10:00:00Z", 7200))
        self.service(out=json.dumps({"pct": 7, "at": epoch("2026-10-19T11:30:00Z")}))
        self.assertEqual(self.bars()[0]["state"], "ok")

    def test_each_way_a_service_fails_to_answer(self):
        cases = [
            (dict(code=1), {}, "stub share exit 1"),
            (dict(out="not json"), {}, "printed no JSON"),
            (dict(out="[1]"), {}, "not an object"),
            (dict(out='{"x": 1}'), {}, "key 'pct' is absent"),
            (dict(out='{"pct": null}'), {}, "pct is null"),
            (dict(out='{"pct": "12"}'), {}, "pct is not a number"),
            (dict(out='{"pct": true}'), {}, "pct is not a number"),
            (dict(out='{"pct": 140}'), {}, "outside 0..100"),
            (dict(out='{"pct": 5}'), {"asof_key": "at"}, "at is absent or not a time"),
            (dict(sleep=3), {"timeout_s": 1}, "no answer within 1 s"),
        ]
        for svc, extra, why in cases:
            with self.subTest(why=why):
                self.service(**svc)
                self.use([service_row(**extra)])
                b = self.bars()[0]
                self.assertEqual((b["state"], b["pct"]), ("unknown", None))
                self.assertIn(why, b["reason"])
        self.service(out='{"pct": 5}')  # counterfactual: the same row answers
        self.use([service_row()])
        self.assertEqual(self.bars()[0]["pct"], 5.0)


class CheckGate(BarsCase):
    """A row naming a window, service or verb that does not exist fails the gate."""
    def test_the_real_registry_passes(self):
        r = self.check_gate()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"^OK +bars +\(3 rows in ")

    def assert_fails(self, rows, why):
        self.use(rows)
        r = self.check_gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(why, r.stdout)

    def test_each_bad_row_fails(self):
        self.service(verbs=("share",))
        good = [window_row("a", "five_hour"), service_row()]
        self.use(good)
        r = self.check_gate()
        self.assertEqual(r.returncode, 0, r.stdout)  # counterfactual: the fixture itself is good
        cases = [
            ([window_row("a", "fable_week")], "source window 'fable_week' is not one of"),
            ([service_row(verb="nope")], "verb 'nope' is not one of stub's verbs"),
            ([dict(window_row("a", "five_hour"), refesh=900)], "unknown key 'refesh'"),
            ([window_row("a", "five_hour"), window_row("a", "seven_day")], "name given twice"),
            ([dict(window_row("a", "five_hour"), window="week")], "window is not one of"),
            ([dict(window_row("a", "five_hour"), refresh=0)], "refresh is not"),
            ([dict(window_row("a", "five_hour"), source={"pending": " "})], "pending gives no reason"),
            ([dict(window_row("a", "five_hour"), source={"windw": "five_hour"})], "source is none of"),
            ([service_row(timeout_s=600)], "timeout_s is not"),
            ([], "no rows"),
        ]
        for rows, why in cases:
            with self.subTest(why=why):
                self.assert_fails(rows, why)
        (self.tools / "stub" / "cli.json").unlink()
        self.assert_fails([service_row()], "has no readable cli.json")

    def test_an_absent_service_is_unchecked_not_ok(self):
        self.use([service_row(service="elsewhere")])
        r = self.check_gate()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertRegex(r.stdout, r"UNCHECKED bars .*service 'elsewhere' not found")

    def test_an_unreadable_registry_fails(self):
        self.registry = self.tmp / "bars.json"
        self.registry.write_text("{cut")
        r = self.check_gate()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not JSON", r.stdout)


class TheProbe(BarsCase):
    def test_text_names_the_rate_limits_keys_no_row_names(self):
        sample = {"five_hour_pct": 42.0, "seven_day_pct": 18.0, "five_hour_resets_at": None,
                  "seven_day_resets_at": None}
        raw = {"five_hour": {"used_percentage": 42.0}, "seven_day": {"used_percentage": 18.0}}
        (self.store / "limits.json").write_text(json.dumps({"sampled_at": epoch(NOW), "sample": sample,
                                                            "rate_limits": raw}))
        r = self.cli("bars")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"rate_limits keys (sample {NOW}): five_hour, seven_day; named by no row: none", r.stdout)
        raw["seven_day_fable"] = {"used_percentage": 3.0}
        (self.store / "limits.json").write_text(json.dumps({"sampled_at": epoch(NOW), "sample": sample,
                                                            "rate_limits": raw}))
        r = self.cli("bars")
        self.assertIn("named by no row: seven_day_fable", r.stdout)
        self.assertNotIn("3.0", r.stdout, "names only: a key no row names gives no number")


if __name__ == "__main__":
    import unittest
    unittest.main()

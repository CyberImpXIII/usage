"""§4 gates for the questions: project and gate on samples with a known slope,
hits calibrate, and the token ledger's grouping."""
from tests.helpers import Case

R5 = "2026-10-05T15:00:00Z"
R7 = "2026-10-12T09:00:00Z"


class KnownSlope(Case):
    """5h rises 10 points an hour (40% at 10:00, 50% at 11:00), with 1,000,000
    turn tokens in that hour: 1e-5 % per token. 7d rises 1 point an hour."""

    def setUp(self):
        super().setUp()
        self.put_samples([("2026-10-05T10:00:00Z", 40, 20, R5, R7),
                          ("2026-10-05T10:30:00Z", 45, 20.5, R5, R7),
                          ("2026-10-05T11:00:00Z", 50, 21, R5, R7)])
        self.put_tokens([("2026-10-05T10:20:00Z", "a", 600000, "turn"),
                         ("2026-10-05T10:50:00Z", "b", 400000, "turn"),
                         ("2026-10-05T10:55:00Z", "c", 9000000, "backlog")])
        self.put_limits("2026-10-05T11:00:00Z", 50, 21, R5, R7)
        self.now = "2026-10-05T11:05:00Z"

    def test_burn_is_the_arithmetic_slope_and_ignores_backlog(self):
        out = self.cli("burn", "--window", "5h").stdout
        self.assertIn("5h: 10.00 %/h, 1,000,000 tokens/h over 3 samples", out)

    def test_project_gives_the_arithmetic_answer(self):
        r = self.cli("project", "--threshold", "75")
        self.assertEqual(r.returncode, 0)
        # 5h: 50 -> 75 at 10 %/h = 2.5 h after 11:00 = 13:30, before the 15:00 reset
        self.assertIn("5h: reaches 75% at Mon 13:30 (2026-10-05T13:30:00Z)", r.stdout)
        # default threshold is hold (90): 4 h -> 15:00, which is the reset: resets first
        self.assertIn("5h: resets first", self.cli("project").stdout)

    def test_project_changes_with_the_threshold(self):
        a = self.cli("project", "--threshold", "60").stdout.splitlines()
        b = self.cli("project", "--threshold", "70").stdout.splitlines()
        self.assertEqual(a[0], "5h: reaches 60% at Mon 12:00 (2026-10-05T12:00:00Z)")
        self.assertEqual(b[0], "5h: reaches 70% at Mon 13:00 (2026-10-05T13:00:00Z)")
        # 7d: 21% at 1 %/h -> 60% 39 h after 11:00
        self.assertEqual(a[1], "7d: reaches 60% at Wed 02:00 (2026-10-07T02:00:00Z)")

    def test_gate_passes_a_job_that_fits(self):
        # 1,000,000 tokens -> +10 points: 5h 60% < warn 75
        r = self.cli("gate", "1000000")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(r.stdout.startswith("pass\n"), r.stdout)

    def test_gate_warns_between_warn_and_hold(self):
        r = self.cli("gate", "3000000")  # 50 + 30 = 80
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(r.stdout.startswith("pass, warn: 5h"), r.stdout)

    def test_gate_holds_a_job_larger_than_the_room(self):
        r = self.cli("gate", "4500000")  # 50 + 45 = 95 >= hold 90
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(r.stdout.startswith(f"hold until {R5} (5h"), r.stdout)

    def test_a_stale_sample_holds(self):
        r = self.cli("gate", "1", now="2026-10-05T11:16:00Z")  # 16 min > 15
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("unknown, hold: the last sample is 16 min old"), r.stdout)
        self.assertEqual(self.cli("gate", "1", now="2026-10-05T11:14:00Z").returncode, 0)

    def test_no_sample_holds(self):
        (self.store / "limits.json").unlink()
        r = self.cli("gate", "1")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("unknown, hold: no status-line sample"))

    def test_no_measured_ratio_holds(self):
        (self.store / "tokens.tsv").unlink()
        r = self.cli("gate", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown, hold: 5h has no measured %/token", r.stdout)

    def test_a_reset_starts_a_new_window(self):
        self.put_samples([("2026-10-05T10:00:00Z", 40, 20, R5, R7),
                          ("2026-10-05T11:00:00Z", 50, 21, R5, R7),
                          ("2026-10-05T15:10:00Z", 2, 22, "2026-10-05T20:10:00Z", R7),
                          ("2026-10-05T16:10:00Z", 8, 23, "2026-10-05T20:10:00Z", R7)])
        out = self.cli("burn", "--window", "5h").stdout
        self.assertIn("5h: 6.00 %/h", out)

    def test_bad_arguments_are_usage_errors(self):
        self.assertEqual(self.cli("gate", "lots").returncode, 2)
        self.assertEqual(self.cli("burn", "--window", "1d").returncode, 2)
        self.assertEqual(self.cli("hits", "--since", "yesterday").returncode, 2)
        self.assertEqual(self.cli("nonsense").returncode, 2)


class HitsCalibrate(Case):
    def put_hits(self, rows):
        lines = ["time\tsession\tagent_type\tfive_hour_pct\tseven_day_pct\tsample_time"]
        for t, p5, p7, st in rows:
            lines.append("\t".join([t, "sess-1", "a", str(p5), str(p7), st]))
        (self.store / "hits.tsv").write_text("\n".join(lines) + "\n")

    def test_a_hit_below_hold_is_flagged_and_calibrate_proposes_a_lower_hold(self):
        self.put_hits([("2026-10-05T10:05:00Z", 82.4, 30, "2026-10-05T10:00:00Z")])
        out = self.cli("hits").stdout
        self.assertIn("BELOW HOLD 90%", out)
        cal = self.cli("calibrate").stdout
        self.assertIn("propose: hold 90 -> 77, warn 75 -> 62", cal)

    def test_hits_at_or_above_hold_propose_nothing(self):
        self.put_hits([("2026-10-05T10:05:00Z", 97, 30, "2026-10-05T10:00:00Z")])
        self.assertNotIn("BELOW HOLD", self.cli("hits").stdout)
        self.assertIn("no evidence to move the thresholds", self.cli("calibrate").stdout)

    def test_a_stale_sample_is_not_evidence(self):
        self.put_hits([("2026-10-05T12:00:00Z", 50, 30, "2026-10-05T10:00:00Z")])
        self.assertIn("stale sample", self.cli("hits").stdout)
        self.assertIn("no evidence", self.cli("calibrate").stdout)

    def test_since_filters(self):
        self.put_hits([("2026-10-04T10:05:00Z", 82, 30, "2026-10-04T10:00:00Z")])
        self.assertIn("no rate-limit hits", self.cli("hits", "--since", "2026-10-05").stdout)

    def test_calibrate_never_writes_usage_json(self):
        from tests.helpers import ROOT
        before = (ROOT / "usage.json").read_bytes()
        self.put_hits([("2026-10-05T10:05:00Z", 60, 30, "2026-10-05T10:00:00Z")])
        self.cli("calibrate")
        self.assertEqual((ROOT / "usage.json").read_bytes(), before)


class Tokens(Case):
    def test_by_agent_and_by_model(self):
        self.put_tokens([("2026-10-05T10:00:00Z", "a", 100, "turn"),
                         ("2026-10-05T10:01:00Z", "b", 50, "turn"),
                         ("2026-10-05T10:02:00Z", "a", 25, "backlog")])
        out = self.cli("tokens", "--by", "agent").stdout.splitlines()
        self.assertTrue(out[1].startswith("a ") and out[1].rstrip().endswith("125"), out)
        self.assertIn("claude-opus-5-5", self.cli("tokens", "--by", "model").stdout)
        self.assertIn("no token rows", self.cli("tokens", "--since", "2026-10-06").stdout)

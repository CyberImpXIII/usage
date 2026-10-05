"""§4 gates for the three reporters: the status line, the stop hook's delta and
the failure hook's filter."""
import json
import shutil

from tests.helpers import FIXTURES, Case, statusline_input

TURNS = [FIXTURES / "transcript" / f"turn{i}.jsonl" for i in (1, 2, 3)]
EXPECTED = json.loads((FIXTURES / "transcript" / "expected.json").read_text())
COLS = ("input", "output", "cache_read", "cache_write")


class StatusLine(Case):
    def test_with_rate_limits_one_row_with_the_numbers_and_the_line(self):
        r = self.script("statusline", statusline_input(42.0, 18.5), now="2026-10-05T10:00:00Z")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "5h 42% (resets 14:05) | 7d 18% (resets Mon 09:00)")
        rows = self.rows("samples.tsv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time"], "2026-10-05T10:00:00Z")
        self.assertEqual((rows[0]["five_hour_pct"], rows[0]["seven_day_pct"]), ("42.0", "18.5"))
        limits = json.loads((self.store / "limits.json").read_text())
        self.assertEqual(limits["sample"]["five_hour_pct"], 42.0)

    def test_the_raw_rate_limits_object_is_kept_whole(self):
        extra = {"seven_day_opus": {"used_percentage": 7, "resets_at": 1}}
        self.script("statusline", statusline_input(extra=extra), now="2026-10-05T10:00:00Z")
        limits = json.loads((self.store / "limits.json").read_text())
        self.assertIn("seven_day_opus", limits["rate_limits"])

    def test_without_rate_limits_no_row_no_line_exit_0(self):
        r = self.script("statusline", {"model": {"id": "claude-opus-5-5"}}, now="2026-10-05T10:00:00Z")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.store_listing(), [])

    def test_chained_command_still_runs_and_its_output_is_kept(self):
        cmd = "jq -r '\"[\" + .model.display_name + \"] chained\"'"
        r = self.script("statusline", statusline_input(), cmd, now="2026-10-05T10:00:00Z")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("[Opus] chained | 5h 42%"), r.stdout)
        self.assertEqual(len(self.rows("samples.tsv")), 1)
        r = self.script("statusline", {"model": {"display_name": "Opus"}}, cmd)
        self.assertEqual(r.stdout, "[Opus] chained\n")

    def test_marker_changes_with_the_thresholds(self):
        self.assertIn("5h 80% warn", self.script("statusline", statusline_input(80.0)).stdout)
        self.assertIn("5h 91% HOLD", self.script("statusline", statusline_input(91.0)).stdout)
        self.assertNotIn("warn", self.script("statusline", statusline_input(10.0, 10.0)).stdout)

    def test_unchanged_values_append_only_on_the_heartbeat(self):
        for t in ("2026-10-05T10:00:00Z", "2026-10-05T10:05:00Z", "2026-10-05T10:10:00Z"):
            self.script("statusline", statusline_input(), now=t)
        self.assertEqual([r["time"] for r in self.rows("samples.tsv")],
                         ["2026-10-05T10:00:00Z", "2026-10-05T10:10:00Z"])
        self.script("statusline", statusline_input(43.0), now="2026-10-05T10:11:00Z")
        self.assertEqual(len(self.rows("samples.tsv")), 3)


class StopDelta(Case):
    def run_turns(self, transcript, event="Stop"):
        data = {"session_id": "sess-1", "hook_event_name": event, "agent_type": "agent-a",
                "transcript_path": str(transcript)}
        if event == "SubagentStop":
            # the parent's transcript, which must not be the one read
            data["transcript_path"] = str(self.tmp / "main.jsonl")
            data["agent_transcript_path"] = str(transcript)
        out = []
        for turn, at in zip(TURNS, EXPECTED["run_at"]):
            with open(transcript, "a") as fh:
                fh.write(turn.read_text())
            r = self.script("stop", data, now=at)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            out.append(len(self.rows("tokens.tsv")))
        return data, out

    def test_three_runs_three_rows_summing_to_the_reader_totals(self):
        transcript = self.tmp / "t.jsonl"
        data, counts = self.run_turns(transcript)
        self.assertEqual(counts, [1, 2, 3])
        rows = self.rows("tokens.tsv")
        for row, want in zip(rows, EXPECTED["per_run"]):
            self.assertEqual({c: int(row[c]) for c in COLS}, want)
            self.assertEqual((row["basis"], row["agent_type"], row["session"]), ("turn", "agent-a", "sess-1"))
        total = {c: sum(int(r[c]) for r in rows) for c in COLS}
        self.assertEqual(total, EXPECTED["by_model"]["claude-opus-5-5"])
        before = (self.store / "tokens.tsv").read_bytes()
        r = self.script("stop", data, now="2026-10-05T10:20:00Z")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.store / "tokens.tsv").read_bytes(), before, "a fourth run on an unchanged file wrote")

    def test_subagent_stop_reads_the_agent_transcript(self):
        transcript = self.tmp / "agent.jsonl"
        _, counts = self.run_turns(transcript, event="SubagentStop")
        self.assertEqual(counts, [1, 2, 3])

    def test_a_transcript_first_seen_late_is_backlog_not_turn(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text("".join(t.read_text() for t in TURNS))
        self.script("stop", {"session_id": "s", "hook_event_name": "Stop", "transcript_path": str(transcript)},
                    now="2026-10-05T12:00:00Z")
        rows = self.rows("tokens.tsv")
        self.assertEqual([r["basis"] for r in rows], ["backlog"])
        self.assertEqual(int(rows[0]["cache_read"]), 82000)

    def test_a_partial_last_line_waits_for_its_newline(self):
        transcript = self.tmp / "t.jsonl"
        text = TURNS[0].read_text()
        cut = text.rstrip("\n").rfind("\n") + 30
        transcript.write_text(text[:cut])
        data = {"session_id": "s", "hook_event_name": "Stop", "transcript_path": str(transcript)}
        self.script("stop", data, now="2026-10-05T10:01:00Z")
        transcript.write_text(text)
        self.script("stop", data, now="2026-10-05T10:01:30Z")
        total = {c: sum(int(r[c]) for r in self.rows("tokens.tsv")) for c in COLS}
        self.assertEqual(total, EXPECTED["per_run"][0])


class FailureFilter(Case):
    def test_rate_limit_appends_a_hit_with_the_preceding_sample(self):
        self.script("statusline", statusline_input(88.0, 40.0), now="2026-10-05T10:00:00Z")
        r = self.script("failure", {"session_id": "sess-9", "hook_event_name": "StopFailure",
                                    "error": "rate_limit", "agent_type": "agent-b"},
                        now="2026-10-05T10:03:00Z")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        hits = self.rows("hits.tsv")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["five_hour_pct"], hits[0]["seven_day_pct"], hits[0]["sample_time"]),
                         ("88.0", "40.0", "2026-10-05T10:00:00Z"))
        self.assertEqual(hits[0]["agent_type"], "agent-b")

    def test_overloaded_appends_nothing(self):
        r = self.script("failure", {"session_id": "s", "hook_event_name": "StopFailure", "error": "overloaded"})
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.store / "hits.tsv").exists())


class FailsOpen(Case):
    """Truncated JSON, no store folder, a read-only store: exit 0, nothing written,
    for all three scripts."""

    def inputs(self):
        transcript = self.tmp / "t.jsonl"
        shutil.copy(TURNS[0], transcript)
        return {"statusline": statusline_input(),
                "stop": {"session_id": "s", "hook_event_name": "Stop", "transcript_path": str(transcript)},
                "failure": {"session_id": "s", "hook_event_name": "StopFailure", "error": "rate_limit"}}

    def test_truncated_json(self):
        for name, data in self.inputs().items():
            text = json.dumps(data)
            r = self.script(name, text[: len(text) // 2], now="2026-10-05T10:00:30Z")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), name)
        self.assertEqual(self.store_listing(), [])

    def test_empty_input(self):
        for name in self.inputs():
            r = self.script(name, "")
            self.assertEqual((r.returncode, r.stdout), (0, ""), name)
        self.assertEqual(self.store_listing(), [])

    def test_no_store_folder(self):
        self.store.rmdir()
        for name, data in self.inputs().items():
            r = self.script(name, data, now="2026-10-05T10:00:30Z")
            self.assertEqual(r.returncode, 0, name)
            self.assertEqual(r.stderr, "", name)
        self.assertFalse(self.store.exists(), "a reporter created the store folder")

    def test_read_only_store(self):
        self.store.chmod(0o555)
        for name, data in self.inputs().items():
            r = self.script(name, data, now="2026-10-05T10:00:30Z")
            self.assertEqual(r.returncode, 0, name)
            self.assertEqual(r.stderr, "", name)
        self.assertEqual(self.store_listing(), [])

    def test_the_counterfactual_a_good_store_is_written(self):
        """The three fail-open tests mean something only if the same inputs write here."""
        for name, data in self.inputs().items():
            self.script(name, data, now="2026-10-05T10:00:30Z")
        for f in ("samples.tsv", "tokens.tsv", "hits.tsv"):
            self.assertTrue((self.store / f).exists(), f)

"""`usage can-run <model>` (PLAN-routing-tree.md §16 step 2; usagelib/models.py):
--set, the TTL, the probe and what a reply can and cannot mean (research rs-6),
and the hold a rate-limit hit sets. Each rule is held with its counterfactual.
The probe runs a stub CLI ($USAGE_CLAUDE_CLI) that records its argv and cwd,
prints what the test gives it, and can run the StopFailure reporter mid-probe."""
import json
import os
import unittest

from tests.helpers import ROOT, SCRIPTS, Case, epoch

M = "claude-fable-5-1"
OTHER = "claude-opus-5-5"
NOW = "2026-10-19T12:00:00Z"  # a Monday
H = 3600


def stream(model, text="ok", is_error=False):
    return "\n".join(json.dumps(e) for e in (
        {"type": "system", "subtype": "init", "model": model},
        {"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": text}]}},
        {"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error, "result": text},
    )) + "\n"


class CanRun(Case):
    def setUp(self):
        super().setUp()
        self.now = NOW
        self.stubdir = self.tmp / "stub"
        self.stubdir.mkdir()
        self.claude_cli = self.stubdir / "claude"
        self.claude_cli.write_text(
            "#!/bin/bash\n"
            f"d={self.stubdir}\n"
            'printf "%s\\n" "$@" > "$d/argv"\n'
            'pwd -P > "$d/cwd"\n'
            'echo x >> "$d/calls"\n'
            f'if [ -f "$d/during.json" ]; then {SCRIPTS["failure"]} < "$d/during.json"; fi\n'
            'cat "$d/out" 2>/dev/null\n'
            'cat "$d/err" >&2 2>/dev/null\n'
            'exit "$(cat "$d/code" 2>/dev/null || echo 0)"\n')
        self.claude_cli.chmod(0o755)
        self.answer(stream(M))

    # -- plumbing --

    def answer(self, out, err="", code=0):
        (self.stubdir / "out").write_text(out)
        (self.stubdir / "err").write_text(err)
        (self.stubdir / "code").write_text(str(code))

    def calls(self):
        p = self.stubdir / "calls"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def can_run(self, model=M, *flags, now=None):
        r = self.cli("can-run", model, *flags, now=now)
        self.assertEqual(r.stderr, "")
        return r.returncode, r.stdout.splitlines()[0], r.stdout

    def set(self, verdict, model=M, reason="the plan includes it", now=None):
        r = self.cli("can-run", "--set", model, verdict, reason, now=now)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def records(self):
        p = self.store / "models.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def put(self, source, result, at, model=M, **extra):
        recs = self.records()
        recs.setdefault(model, {})[source] = {"model": model, "checked_at": at, "result": result,
                                              "evidence": "by hand", "source": source, **extra}
        (self.store / "models.json").write_text(json.dumps(recs))

    def transcript(self, model=M):
        p = self.tmp / f"t-{model}.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in (
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"model": model, "content": []}},
            {"type": "assistant", "message": {"model": "<synthetic>", "content": []}},  # skipped
        )) + "\n")
        return p

    def hit(self, text, model=M, error="rate_limit", now=None, transcript=None):
        data = {"hook_event_name": "StopFailure", "session_id": "s1", "error": error,
                "last_assistant_message": text,
                "transcript_path": str(transcript or self.transcript(model))}
        r = self.script("failure", data, now=now)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        return r


class Answer(CanRun):
    def test_no_answer_from_jacob_is_unknown_and_runs_no_probe(self):
        code, head, out = self.can_run()
        self.assertEqual((code, head), (3, "unknown"))
        self.assertIn("--set <model> ok", out)
        self.assertEqual(self.calls(), 0, "a probe without a set ok can only spend credits (rs-6)")
        self.set("ok")
        code, head, _ = self.can_run()
        self.assertEqual((code, head, self.calls()), (0, "ok", 1), "counterfactual: a set ok lets the probe run")

    def test_a_probe_reply_alone_is_not_ok(self):
        self.put("probe", "reply", NOW)
        code, head, out = self.can_run()
        self.assertEqual((code, head), (3, "unknown"))
        self.assertIn("ok or usage credits (rs-6)", out)
        self.put("set", "ok", NOW)
        code, head, _ = self.can_run()
        self.assertEqual((code, head), (0, "ok"), "counterfactual: with a set ok the same reply is ok")
        self.assertEqual(self.calls(), 0, "a fresh probe record is used, not re-probed")

    def test_set_ok_alone_is_not_ok_without_a_probe(self):
        self.set("ok")
        code, head, out = self.can_run(M, "--no-probe")
        self.assertEqual((code, head, self.calls()), (3, "unknown", 0))
        self.assertIn("no fresh probe: --no-probe", out)

    def test_set_no_is_refused_and_beats_a_reply(self):
        self.set("ok")
        self.assertEqual(self.can_run()[:2], (0, "ok"))
        r = self.set("no", reason="no Fable on this plan")
        self.assertIn(f"set: {M} refused at {NOW}, good until 2026-10-20T12:00:00Z", r.stdout)
        code, head, out = self.can_run()
        self.assertEqual((code, head), (1, "refused"))
        self.assertIn("set: no Fable on this plan", out)
        rec = self.records()[M]["set"]
        self.assertEqual((rec["checked_at"], rec["result"], rec["evidence"]),
                         (NOW, "refused", "set: no Fable on this plan"))

    def test_set_needs_ok_or_no_and_a_reason(self):
        for args in (["--set", M, "ok"], ["--set", M, "ok", "  "], ["--set", M, "yes", "r"],
                     ["--set", "not a model", "ok", "r"], ["--set", "fable", "ok", "r"], ["fable"],
                     [M, "--probe"], []):
            r = self.cli("can-run", *args)
            self.assertEqual(r.returncode, 2, args)
        self.assertFalse((self.store / "models.json").exists())
        self.assertEqual(self.cli("can-run", "--set", M, "ok", "r").returncode, 0, "counterfactual")

    def test_set_without_a_store_says_so(self):
        os.rmdir(self.store)
        r = self.cli("can-run", "--set", M, "ok", "r")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not set: no store", r.stdout)
        code, head, out = self.can_run()
        self.assertEqual((code, head, self.calls()), (3, "unknown", 0))
        self.assertIn("no store at", out)


class TTL(CanRun):
    def test_each_record_lasts_the_ttl(self):
        self.set("ok")
        self.assertEqual(self.can_run()[:2], (0, "ok"))
        later = epoch(NOW) + 23 * H
        self.assertEqual(self.can_run(M, "--no-probe", now=later)[:2], (0, "ok"), "within the TTL")
        self.assertEqual(self.calls(), 1)
        past = epoch(NOW) + 25 * H
        code, head, out = self.can_run(M, "--no-probe", now=past)
        self.assertEqual((code, head), (3, "unknown"))
        self.assertIn("past the TTL (probes.model_ttl_hours 24)", out)
        self.assertEqual(self.can_run(M, now=past)[:2], (3, "unknown"), "a stale set ok runs no probe")
        self.assertEqual(self.calls(), 1)
        self.set("ok", now=past)
        self.assertEqual(self.can_run(M, now=past)[:2], (0, "ok"), "counterfactual: a fresh set ok re-probes")
        self.assertEqual(self.calls(), 2)

    def test_a_stale_or_future_hand_edit_is_not_trusted(self):
        for at, why in (("2026-10-17T12:00:00Z", "past the TTL"), ("2026-10-19T13:00:00Z", "in the future"),
                        ("2026-10-19 12:00", "not shaped")):
            with self.subTest(at=at):
                self.put("set", "ok", at)
                self.put("probe", "reply", at)
                code, head, out = self.can_run(M, "--no-probe")
                self.assertEqual((code, head), (3, "unknown"))
                self.assertIn(why, out)
        self.put("set", "ok", NOW)
        self.put("probe", "reply", NOW)
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (0, "ok"), "counterfactual: dated now")

    def test_a_misfiled_record_is_not_used(self):
        self.put("set", "reply", NOW)  # not a set result
        self.put("probe", "reply", NOW)
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (3, "unknown"))
        recs = self.records()
        recs[M]["set"]["result"] = "ok"
        recs[M]["probe"]["model"] = OTHER  # filed under the wrong model
        (self.store / "models.json").write_text(json.dumps(recs))
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (3, "unknown"))
        recs[M]["probe"]["model"] = M
        (self.store / "models.json").write_text(json.dumps(recs))
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (0, "ok"), "counterfactual")


class Probe(CanRun):
    def test_the_probe_names_the_model_and_one_turn(self):
        for model in (M, OTHER):
            self.set("ok", model)
            self.answer(stream(model))
            self.assertEqual(self.can_run(model)[:2], (0, "ok"))
            self.assertEqual((self.stubdir / "argv").read_text().splitlines(),
                             ["-p", "--model", model, "--max-turns", "1", "--output-format", "stream-json",
                              "--verbose", "ok"])
            self.assertEqual((self.stubdir / "cwd").read_text().strip(), str(self.store.resolve()))

    def test_what_a_probe_reads(self):
        reset = "You've hit your session limit · resets 3:45pm"
        cases = [
            ("a reply", stream(M), "", 0, "ok", "replied as claude-fable-5-1"),
            ("a fallback model answering", stream(OTHER), "", 0, "refused", "answered by claude-opus-5-5"),
            ("an error result", stream(M, "boom", is_error=True), "", 0, "refused", "unverified"),
            ("no access, on stdout", stream(M, f"There's an issue with the selected model ({M}). Run --model",
                                            is_error=True), "", 1, "refused", "no access, or no such model"),
            ("no access, on stderr", "", f"There's an issue with the selected model ({M}).", 1, "refused",
             "no access, or no such model"),
            ("credits spent", "", "You've hit your monthly spend limit", 1, "credits", "a spend limit"),
            ("a limit with a reset", "", reset, 1, "hold 2026-10-19T15:45:00Z", "a limit with a reset"),
            ("nothing", "", "", 7, "refused", "exit 7"),
        ]
        for name, out, err, code, head, evidence in cases:
            with self.subTest(name):
                (self.store / "models.json").unlink(missing_ok=True)
                self.set("ok")
                self.answer(out, err, code)
                got_code, got_head, text = self.can_run()
                self.assertEqual((got_head, got_code), (head, 0 if head == "ok" else 1))
                self.assertIn(evidence, self.records()[M]["probe"]["evidence"])
                self.assertIn(evidence, text)

    def test_a_probe_that_cannot_run_records_nothing(self):
        self.set("ok")
        self.claude_cli = self.tmp / "nowhere" / "claude"
        code, head, out = self.can_run()
        self.assertEqual((code, head), (3, "unknown"))
        self.assertIn("no claude CLI to probe with", out)
        self.assertNotIn("probe", self.records()[M])
        self.claude_cli = self.stubdir / "claude"
        self.assertEqual(self.can_run()[:2], (0, "ok"), "counterfactual: with a CLI the probe is recorded")
        self.assertIn("probe", self.records()[M])

    def test_a_hold_written_during_the_probe_is_kept(self):
        self.set("ok")
        (self.stubdir / "during.json").write_text(json.dumps({
            "hook_event_name": "StopFailure", "error": "rate_limit", "transcript_path": str(self.transcript()),
            "last_assistant_message": "You've hit your session limit · resets 3:45pm"}))
        code, head, _ = self.can_run()
        self.assertEqual((code, head), (1, "hold 2026-10-19T15:45:00Z"))
        self.assertEqual(set(self.records()[M]), {"set", "probe", "hit"})
        self.assertEqual(self.records()[M]["probe"]["result"], "reply")


class Hold(CanRun):
    def ok_now(self):
        self.set("ok")
        self.assertEqual(self.can_run()[:2], (0, "ok"))

    def test_a_rate_limit_hit_holds_the_sessions_model_until_the_reset(self):
        self.ok_now()
        self.hit("You've hit your session limit · resets 3:45pm")
        code, head, out = self.can_run(M, "--no-probe")
        self.assertEqual((code, head), (1, "hold 2026-10-19T15:45:00Z"))
        self.assertIn("held by hit", out)
        self.assertEqual(self.rows("hits.tsv")[0]["time"], NOW, "the hits row is written too")
        after = epoch("2026-10-19T15:46:00Z")
        self.assertEqual(self.can_run(M, "--no-probe", now=after)[:2], (0, "ok"),
                         "counterfactual: past the reset, the set and the probe answer again")

    def test_only_a_rate_limit_hit_holds_and_only_its_model(self):
        self.ok_now()
        self.hit("Overloaded", error="overloaded")
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (0, "ok"), "another error holds nothing")
        self.hit("You've hit your session limit · resets 3:45pm", model=OTHER)
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (0, "ok"), "an opus session does not hold fable")
        self.assertEqual(self.can_run(OTHER, "--no-probe")[:2], (1, "hold 2026-10-19T15:45:00Z"), "counterfactual")

    def test_a_hold_with_no_reset_lasts_the_ttl(self):
        for text in ("You've hit your limit", "resets 2026-11-30 00:00 UTC"):  # none; past 8 days
            with self.subTest(text):
                (self.store / "models.json").unlink(missing_ok=True)
                self.hit(text)
                code, head, out = self.can_run(M, "--no-probe")
                self.assertEqual((code, head), (1, "hold unknown"))
                self.assertIn("no reset named, until 2026-10-20T12:00:00Z", out)
                self.assertEqual(self.can_run(M, "--no-probe", now=epoch(NOW) + 25 * H)[:2], (3, "unknown"),
                                 "counterfactual: past the TTL the hold is gone")

    def test_a_hold_beyond_eight_days_is_a_misread(self):
        self.put("set", "ok", NOW)
        self.put("probe", "reply", NOW)
        self.put("hit", "hold", NOW, until="2026-10-29T12:00:00Z")
        code, head, out = self.can_run(M, "--no-probe")
        self.assertEqual((code, head), (0, "ok"))
        self.assertIn("not within 8 days", out)
        self.put("hit", "hold", NOW, until="2026-10-26T12:00:00Z")
        self.assertEqual(self.can_run(M, "--no-probe")[:2], (1, "hold 2026-10-26T12:00:00Z"), "counterfactual")

    def test_a_hit_that_cannot_find_the_model_is_traced_and_the_hit_kept(self):
        self.hit("resets 3:45pm", transcript=self.tmp / "missing.jsonl")
        self.assertEqual(len(self.rows("hits.tsv")), 1)
        self.assertFalse((self.store / "models.json").exists())
        self.assertIn("FileNotFoundError", (self.store / "failures.tsv").read_text())
        self.hit("resets 3:45pm")
        self.assertIn("hit", self.records()[M], "counterfactual: with the transcript the hold is written")

    def test_a_hit_never_replaces_an_unreadable_models_json_but_set_does(self):
        (self.store / "models.json").write_text("{cut")
        self.hit("resets 3:45pm")
        self.assertEqual((self.store / "models.json").read_text(), "{cut")
        self.assertIn("Unreadable", (self.store / "failures.tsv").read_text())
        self.assertEqual(len(self.rows("hits.tsv")), 1)
        r = self.set("ok")
        self.assertIn("models.json was unreadable: replaced", r.stdout)
        self.assertEqual(set(self.records()[M]), {"set"})


class ResetTimes(unittest.TestCase):
    """parse_reset on its own, at a Monday noon UTC. The forms without a zone
    (local time) are held through the reporter above, run with TZ=UTC."""

    def test_each_form(self):
        from usagelib import models, store
        t = epoch(NOW)
        cases = [
            (f"limit|{int(t) + 3600}", "2026-10-19T13:00:00Z"),
            ("resets 2026-10-20 00:00 UTC", "2026-10-20T00:00:00Z"),
            ("resets Wed 9am (America/New_York)", "2026-10-21T13:00:00Z"),
            ("resets 3:45pm (Europe/London)", "2026-10-19T14:45:00Z"),
            ("resets 11am (Asia/Tokyo)", "2026-10-20T02:00:00Z"),  # 21:00 there: tomorrow's
            ("resets 3:45pm (Mars/Olympus)", None),  # a zone it does not know is never read as local
            ("resets 2026-11-30 00:00 UTC", None),  # more than 8 days off
            ("resets 2026-10-18 00:00 UTC", None),  # already past
            ("resets 13:00pm (UTC)", None),
            ("no time here", None),
        ]
        for text, want in cases:
            with self.subTest(text):
                got = models.parse_reset(text, t)
                self.assertEqual(store.iso(got) if got is not None else None, want)


if __name__ == "__main__":
    unittest.main()

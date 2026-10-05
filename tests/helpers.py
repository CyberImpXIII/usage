"""Shared test plumbing: a throwaway store, the scripts run as subprocesses
exactly as Claude Code runs them, the clock pinned (USAGE_NOW), TZ=UTC."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SCRIPTS = {"statusline": ROOT / "usage-statusline.sh", "stop": ROOT / "usage-stop.sh",
           "failure": ROOT / "usage-failure.sh"}
CLI = ROOT / "usage"


def epoch(iso):
    from usagelib import store
    return store.epoch(iso)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-test-"))
        self.store = self.tmp / "store"
        self.store.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.now = None

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        self.tmp.chmod(0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, now=None, data=None):
        """The caller's DATA_REPO never reaches a test: only `data` (a scratch
        folder) is passed on, so no test can write into a real data repo."""
        e = dict(os.environ, USAGE_STORE=str(self.store), HOME=str(self.home), TZ="UTC")
        e.pop("DATA_REPO", None)
        if data is not None:
            e["DATA_REPO"] = str(data)
        t = now if now is not None else self.now
        if t is not None:
            e["USAGE_NOW"] = str(epoch(t) if isinstance(t, str) else t)
        else:
            e.pop("USAGE_NOW", None)
        return e

    def script(self, name, stdin, *args, now=None):
        data = stdin if isinstance(stdin, str) else json.dumps(stdin)
        return subprocess.run([str(SCRIPTS[name]), *args], input=data, capture_output=True,
                              text=True, env=self.env(now), timeout=60)

    def cli(self, *args, now=None, data=None):
        return subprocess.run([str(CLI), *args], capture_output=True, text=True,
                              env=self.env(now, data), timeout=120)

    def rows(self, name):
        p = self.store / name
        if not p.exists():
            return []
        lines = p.read_text().splitlines()
        head = lines[0].split("\t")
        return [dict(zip(head, ln.split("\t"))) for ln in lines[1:]]

    def store_listing(self):
        return sorted(str(p.relative_to(self.store)) for p in self.store.rglob("*"))

    def put_samples(self, rows):
        """rows: (iso time, 5h pct, 7d pct, 5h resets iso|None, 7d resets iso|None)"""
        lines = ["time\tfive_hour_pct\tseven_day_pct\tfive_hour_resets_at\tseven_day_resets_at"]
        for t, p5, p7, r5, r7 in rows:
            lines.append("\t".join([t, str(p5), str(p7), str(int(epoch(r5))) if r5 else "",
                                    str(int(epoch(r7))) if r7 else ""]))
        (self.store / "samples.tsv").write_text("\n".join(lines) + "\n")

    def put_limits(self, t, p5, p7, r5=None, r7=None):
        sample = {"five_hour_pct": p5, "seven_day_pct": p7,
                  "five_hour_resets_at": int(epoch(r5)) if r5 else None,
                  "seven_day_resets_at": int(epoch(r7)) if r7 else None}
        (self.store / "limits.json").write_text(json.dumps({"sampled_at": epoch(t), "sample": sample}))

    def put_tokens(self, rows):
        """rows: (iso time, agent, total as input tokens, basis)"""
        lines = ["time\tsession\tagent_type\tmodel\tinput\toutput\tcache_read\tcache_write\tbasis"]
        for t, agent, total, basis in rows:
            lines.append("\t".join([t, "s1", agent, "claude-opus-5-5", str(total), "0", "0", "0", basis]))
        (self.store / "tokens.tsv").write_text("\n".join(lines) + "\n")


def statusline_input(p5=42.0, p7=18.0, r5="2026-10-05T14:05:00Z", r7="2026-10-12T09:00:00Z", extra=None):
    rl = {"five_hour": {"used_percentage": p5, "resets_at": int(epoch(r5))},
          "seven_day": {"used_percentage": p7, "resets_at": int(epoch(r7))}}
    if extra:
        rl.update(extra)
    return {"model": {"id": "claude-opus-5-5", "display_name": "Opus"}, "rate_limits": rl}

#!/usr/bin/env python3
"""This repo's gates: `./dev.sh check [--json] [GATE...]` and `usage check`.

  test    the unittest suite (tests/), the §4 gates of PLAN-usage-reporting.md
  files   every script the registrations and the CLI need: present, executable,
          parses; usage.json parses
  hooks   the shared hook copies in .claude/hooks/ pass their own tests
  shared  tools/checks' generic gates (`checks run . --json`), one entry each as
          shared:<name>; UNCHECKED, never ok, when that sibling is absent.
          Roster names for no-roster's second half come from the caller
          (CHECKS_ROSTER_NAMES), never from here.

Text by default; --json prints the one schema (tools/checks'
schema/check-json.schema.json): exit 0 iff ok, ok false iff a check is fail or
error. USAGE_CHECK_NESTED=1 (set for the suite) skips the `test` gate, so a
test that runs this file does not run the suite inside itself.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT.parent / "checks" / "checks"
EXECUTABLES = ["usage", "usage-statusline.sh", "usage-stop.sh", "usage-failure.sh", "usagelib/hook.py",
               "dev.sh", "devtools/check.py"]
UNITTEST = re.compile(r"^(FAIL|ERROR): (\S+) \(([\w.]+)\)")
NESTED = "USAGE_CHECK_NESTED"


def result(name, failures=(), reason=None, status=None):
    failures = list(failures)
    st = status or ("fail" if failures else "ok")
    out = {"name": name, "status": st, "counts": {"failed": len(failures)}, "failures": failures}
    if reason:
        out["reason"] = reason
    return out


def failure(message, file=None, line=None, role="code"):
    return {"message": message, "file": file, "line": line if file else None, "role": role}


def _module_file(dotted):
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if (ROOT / rel).is_file():
            return rel
    return None


def gate_test():
    if os.environ.get(NESTED):
        return result("test", status="unchecked", reason=f"nested run ({NESTED} set): the suite is already running")
    env = dict(os.environ, **{NESTED: "1"})
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    found = []
    for ln in r.stderr.splitlines():
        m = UNITTEST.match(ln)
        if m:
            found.append(failure(f"{m.group(1)}: {m.group(2)} ({m.group(3)})", _module_file(m.group(3)), role="tests"))
    if r.returncode != 0 and not found:
        tail = " / ".join(r.stderr.strip().splitlines()[-3:]) or f"exit {r.returncode}"
        found.append(failure(f"the suite failed with no FAIL/ERROR line: {tail}; re-run "
                             f"python3 -m unittest discover -s tests -t . -v", role="tests"))
    if r.returncode == 0 and found:  # cannot happen; never let it read as green
        found.append(failure("unittest exited 0 while printing FAIL/ERROR lines", role="tests"))
    res = result("test", found)
    m = re.search(r"^Ran (\d+) tests?", r.stderr, re.M)
    res["_note"] = (m.group(0) if m else "Ran ? tests") + (", " + r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "")
    return res


def gate_files():
    found = []
    for rel in EXECUTABLES:
        p = ROOT / rel
        if not p.is_file():
            found.append(failure(f"missing: {rel}", rel))
        elif not os.access(p, os.X_OK):
            found.append(failure(f"not executable: {rel}", rel))
    for p in sorted(ROOT.glob("*.sh")):
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        if r.returncode:
            found.append(failure(f"bash -n: {r.stderr.strip()}", p.name))
    for p in [ROOT / "usage", *sorted(ROOT.glob("usagelib/*.py")), *sorted(ROOT.glob("devtools/*.py"))]:
        try:
            compile(p.read_text(), str(p), "exec")
        except SyntaxError as e:
            found.append(failure(f"does not compile: {e.msg}", str(p.relative_to(ROOT)), e.lineno))
    try:
        json.loads((ROOT / "usage.json").read_text())
    except (OSError, ValueError) as e:
        found.append(failure(f"usage.json: {e}", "usage.json"))
    return result("files", found)


def gate_hooks():
    tests = sorted((ROOT / ".claude" / "hooks").glob("test-*.sh"))
    if not tests:
        return result("hooks", [failure("no shared hook copies with tests in .claude/hooks/", ".claude/hooks")])
    found = []
    for t in tests:
        r = subprocess.run(["bash", str(t)], cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            tail = " / ".join((r.stdout + r.stderr).strip().splitlines()[-2:])
            found.append(failure(f"{t.name} exit {r.returncode}: {tail}", str(t.relative_to(ROOT)), role="audit"))
    return result("hooks", found)


def gate_shared():
    if not CHECKS.is_file():
        return [result("shared", status="unchecked",
                       reason=f"tools/checks not found at {CHECKS}: the generic gates (no-roster, no-secrets, ...) did not run")]
    r = subprocess.run([str(CHECKS), "run", str(ROOT), "--json"], capture_output=True, text=True)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        tail = " / ".join(r.stderr.strip().splitlines()[-2:]) or f"exit {r.returncode}"
        return [result("shared", [failure(f"checks run printed no JSON: {tail}", role="audit")], status="error")]
    out = []
    for c in doc.get("results", []):
        st = c.get("status")
        lines = c.get("lines") or []
        if st in ("fail", "error"):
            fs = [failure(ln, role="audit") for ln in lines] or [failure(f"{c['check']} is {st}; run "
                                                                        f"../checks/checks one {c['check']} .", role="audit")]
            out.append(result(f"shared:{c['check']}", fs, status=st))
        elif st == "unchecked":
            out.append(result(f"shared:{c['check']}", status="unchecked", reason=" / ".join(lines) or "unchecked"))
        elif st == "ok":
            out.append(result(f"shared:{c['check']}"))
        else:
            out.append(result(f"shared:{c.get('check')}", [failure(f"unknown status {st!r}", role="audit")],
                              status="error"))
    for f in doc.get("faults", []):
        out.append(result(f"shared:fault-{len(out)}", [failure(str(f), role="audit")], status="error"))
    if not out:
        out.append(result("shared", [failure("checks run returned no results", role="audit")], status="error"))
    return out


GATES = {"test": gate_test, "files": gate_files, "hooks": gate_hooks, "shared": gate_shared}


def main(argv):
    as_json = "--json" in argv
    names = [a for a in argv if a != "--json"]
    bad = [n for n in names if n not in GATES]
    if bad:
        sys.stderr.write(f"unknown gate(s): {', '.join(bad)}; gates: {', '.join(GATES)}\n")
        return 2
    checks = []
    for n in names or list(GATES):
        r = GATES[n]()
        checks.extend(r if isinstance(r, list) else [r])
    ok = not any(c["status"] in ("fail", "error") for c in checks)
    if as_json:
        for c in checks:
            c.pop("_note", None)
        print(json.dumps({"ok": ok, "checks": checks}, indent=1))
    else:
        for c in checks:
            note = c.pop("_note", "")
            extra = f"  ({note})" if note else (f"  ({c['reason']})" if c.get("reason") else "")
            print(f"{c['status'].upper():9} {c['name']}{extra}")
            for f in c["failures"]:
                at = f"{f['file']}:{f['line'] or ''}" if f["file"] else ""
                print(f"  FAIL {at} {f['message']}".rstrip())
        print("check: " + ("ok" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

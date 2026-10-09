#!/usr/bin/env python3
"""Entry point for the three reporter scripts (usage-statusline.sh, usage-stop.sh,
usage-failure.sh): reads the JSON on stdin and hands it to usagelib.reporters.

  hook.py statusline|stop|failure  < input.json

Fails open, always: malformed or absent input, a missing store, a read-only
or full disk -- every one exits 0 with nothing on stderr. A reporter that
blocks a turn costs more than the limit it watches (PLAN-usage-reporting.md §2).
Only `statusline` prints, and only its one line.

But a failure is never swallowed: everything except "no store folder" (not set
up, so nothing was lost) is recorded by store.record_failure() as one line in
failures.tsv, or beside the store when the store refuses the write. Input that
is not a JSON object counts: a StopFailure whose input was cut is a lost hit.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BadInput(Exception):
    pass


def main(argv):
    kind = argv[0] if argv else ""
    try:
        from usagelib import reporters, store
    except BaseException:  # noqa: BLE001 -- without the package there is nowhere known to record
        return 0
    event = ""
    try:
        try:
            data = json.loads(sys.stdin.read())
        except ValueError:
            raise BadInput("input is not JSON (empty or cut)") from None
        if not isinstance(data, dict):
            raise BadInput(f"input is a JSON {type(data).__name__}, not an object")
        if isinstance(data.get("hook_event_name"), str):
            event = data["hook_event_name"]
        if kind == "statusline":
            line = reporters.statusline(data)
            if line:
                sys.stdout.write(line)
        elif kind == "stop":
            reporters.stop(data)
        elif kind == "failure":
            reporters.failure(data)
    except store.NoStore:
        pass
    except BaseException as e:  # noqa: BLE001 -- fail open is the contract; the trace is the point
        store.record_failure(kind, event, e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

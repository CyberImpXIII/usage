#!/usr/bin/env python3
"""Entry point for the three reporter scripts (usage-statusline.sh, usage-stop.sh,
usage-failure.sh): reads the JSON on stdin and hands it to usagelib.reporters.

  hook.py statusline|stop|failure  < input.json

Fails open, always: malformed or absent input, a missing store, a read-only
or full disk -- every one exits 0 having written nothing. A reporter that
blocks a turn costs more than the limit it watches (PLAN-usage-reporting.md §2).
Only `statusline` prints, and only its one line.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv):
    kind = argv[0] if argv else ""
    try:
        from usagelib import reporters
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            return 0
        if kind == "statusline":
            line = reporters.statusline(data)
            if line:
                sys.stdout.write(line)
        elif kind == "stop":
            reporters.stop(data)
        elif kind == "failure":
            reporters.failure(data)
    except BaseException:  # noqa: BLE001 -- fail open is the contract
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""The one way to read usage.json, the store's location and the clock.

setting(name) is the only accessor for usage.json: an unknown name raises
(a missing threshold is a failure, never a default), and tests/test_config.py
compares the names read here, by that call, with the keys in the file.
"""
import json
import os
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
CONFIG_FILE = TOOL / "usage.json"

_cache = None


def load():
    data = json.loads(CONFIG_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def setting(name):
    global _cache
    if _cache is None:
        _cache = load()
    return _cache[name]


def store_dir():
    """~/.claude/usage/ (Claude Code's home, not the workspace's delegation layer);
    USAGE_STORE points elsewhere, which is how the tests run."""
    env = os.environ.get("USAGE_STORE")
    return Path(env) if env else Path.home() / ".claude" / "usage"


def now():
    """Epoch seconds. USAGE_NOW pins it (tests only)."""
    v = os.environ.get("USAGE_NOW")
    return float(v) if v else time.time()

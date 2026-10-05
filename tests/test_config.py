"""§4 "thresholds are a vocabulary": every key in usage.json is read by the
code through config.setting(), and every name the code reads is in the file.
Plus the accessor audit: nothing but config.py reads usage.json."""
import ast
import json
import re
import unittest

from tests.helpers import ROOT

CODE = sorted((ROOT / "usagelib").glob("*.py"))


def keys_in_file():
    return {k for k in json.loads((ROOT / "usage.json").read_text()) if not k.startswith("_")}


def setting_calls(source, filename="<src>"):
    """(names read by a literal, [line of each call by a computed name])."""
    names, dynamic = set(), []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if fname != "setting":
            continue
        arg = node.args[0] if len(node.args) == 1 and not node.keywords else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.add(arg.value)
        else:
            dynamic.append(f"{filename}:{node.lineno}")
    return names, dynamic


def names_read():
    names, dynamic = set(), []
    for p in CODE:
        n, d = setting_calls(p.read_text(), p.name)
        names |= n
        dynamic += d
    return names, dynamic


class Vocabulary(unittest.TestCase):
    def test_every_key_is_read_and_every_read_key_exists(self):
        names, _ = names_read()
        keys = keys_in_file()
        self.assertEqual(sorted(keys - names), [], "keys in usage.json that no code reads")
        self.assertEqual(sorted(names - keys), [], "names the code reads that usage.json lacks")

    def test_no_setting_is_read_by_a_computed_name(self):
        """A computed name would escape the comparison above."""
        self.assertEqual(names_read()[1], [])

    def test_the_scan_sees_literal_and_computed_reads(self):
        """Counterfactual for both tests above: the scan finds reads in either
        call form and flags a computed name."""
        names, dynamic = setting_calls("config.setting('warn') + setting(\"hold\") + setting(k)")
        self.assertEqual((names, dynamic), ({"warn", "hold"}, ["<src>:1"]))

    def test_only_config_reads_usage_json(self):
        offenders = [p.name for p in CODE if p.name != "config.py"
                     and re.search(r"CONFIG_FILE|config\.load\(|['\"]usage\.json['\"]", p.read_text())]
        self.assertEqual(offenders, [])

    def test_an_unknown_setting_raises(self):
        from usagelib import config
        with self.assertRaises(KeyError):
            config.setting("no_such_threshold")

    def test_the_approved_thresholds(self):
        """PLAN-usage-reporting.md §8, approved 2026-10-05: a change is a reviewed commit."""
        from usagelib import config
        self.assertEqual((config.setting("warn"), config.setting("hold"), config.setting("sample_stale_minutes")),
                         (75, 90, 15))

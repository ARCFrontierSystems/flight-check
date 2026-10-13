"""Usage efficiency: settings, the spending check, quick checks, and inventory reuse."""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402,F401
from flight_check_lib import cli, settings  # noqa: E402
from test_freshness import T2, ProjectTestCase  # noqa: E402
from test_state import run_cli  # noqa: E402


class EfficiencyTests(ProjectTestCase):
    def init_run(self, *extra, base=None):
        return run_cli("init-run", "--base", base or os.path.join(self.sd, "runs"), "--root", self.root, "--now", T2, *extra)

    def test_settings_and_spending_check(self):
        self.assertEqual(settings.load(self.root), {"max_audit_usd": 5.0, "models": "balanced"})
        code, out = run_cli("settings", "--root", self.root, "--max-audit-usd", "0.5", "--models", "thorough")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.sd, ".gitignore")) as fh:
            self.assertIn("settings.json", fh.read())
        code, out = self.init_run("--passes", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["tier"], out["models"]["domain"], out["over_budget"]), ("thorough", "opus", True))
        adds = [o["add"] for o in out["cheaper_options"]]
        self.assertEqual(adds, ["--passes 1", "--models economy", "--quick"])
        self.assertTrue(all(o["usd_high"] <= out["estimate"]["usd_high"] for o in out["cheaper_options"]))
        self.assertEqual(run_cli("settings", "--root", self.root, "--max-audit-usd", "-1")[0], 1)
        code, out = self.init_run("--models", "economy", "--suffix", "e")
        self.assertEqual((out["tier"], out["models"]["domain"], out["models"]["verifier"]), ("economy", "haiku", "sonnet"))
        run_cli("settings", "--root", self.root, "--max-audit-usd", "0", "--models", "balanced")
        code, out = self.init_run("--suffix", "b")
        self.assertEqual((out["models"], out["over_budget"]), (None, False))

    def test_quick_check_looks_only_at_changed_security_areas_and_records_nothing(self):
        code, out = self.init_run("--quick")
        self.assertTrue(out.get("nothing_to_check"), out)
        self.edit("src/store.py")
        code, out = self.init_run("--quick")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["run_id"].endswith("-quick"))
        self.assertIn("appsec", out["quick"]["agents"])
        self.assertTrue(set(out["quick"]["domains"]) <= set(cli.QUICK_DOMAINS))
        self.assertTrue(out["estimate"]["quick"])
        self.assertFalse(os.path.exists(os.path.join(out["run_dir"], "scope.json")))
        # The runs listing never offers a quick check as the latest audit.
        os.makedirs(os.path.join(self.sd, "runs"), exist_ok=True)
        self.assertIsNone(run_cli("runs", "--base", os.path.join(self.sd, "runs"))[1]["latest_finalized"])

    def test_inventory_is_reused_only_when_nothing_it_describes_changed(self):
        base = os.path.join(self.sd, "runs")
        shutil.copytree(os.path.join(self.tmp, "runs", "20261013T100000Z"), os.path.join(base, "20261013T100000Z"))
        inv = {"entry_point_files": ["src/app.py"], "items": [{"path": "./src/store.py"}]}
        with open(os.path.join(base, "20261013T100000Z", "inventory.json"), "w") as fh:
            json.dump(inv, fh)
        scope = {"mode": "changed", "baseline_run_id": "20261013T100000Z", "changed_files": 1,
                 "files": [{"path": "docs/guide.md", "change": "modified"}]}
        self.assertEqual(cli._reusable_inventory(base, scope), inv)
        for files in ([{"path": "src/store.py", "change": "modified"}], [{"path": "docs/new.md", "change": "added"}],
                      [{"path": "package.json", "change": "modified"}], [{"path": ".github/workflows/ci.yml", "change": "modified"}]):
            self.assertIsNone(cli._reusable_inventory(base, dict(scope, files=files)), files)
        self.assertIsNone(cli._reusable_inventory(base, dict(scope, changed_files=80)))
        self.edit("docs/PRIVACY.md")
        code, out = self.init_run("--changed")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["scope"]["inventory_reused_from"], "20261013T100000Z")
        with open(os.path.join(out["run_dir"], "inventory.json")) as fh:
            self.assertEqual(json.load(fh), inv)


if __name__ == "__main__":
    unittest.main()

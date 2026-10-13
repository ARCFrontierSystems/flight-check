"""Watch mode: free checks after edits, suggestions of paid checks, session reminders, and `later`."""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402,F401
from flight_check_lib import state, watch  # noqa: E402
from test_freshness import T1, ProjectTestCase  # noqa: E402
from test_state import run_cli  # noqa: E402

NOW = datetime.datetime(2026, 10, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)
FAKE_KEY = "sk_" + "live_" + "a1B2c3D4e5" * 2  # built at runtime: never a credential-like literal


def hook(event, payload, now=NOW):
    return watch.run_hook(event, json.dumps(payload), now)


class InstantCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="flight-check-watch-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "src"))
        os.makedirs(os.path.join(self.root, "tests"))

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w") as fh:
            fh.write(text)
        return os.path.join(self.root, rel)

    def test_checks_find_common_mistakes_in_plain_english(self):
        path = self.write("src/app.py", 'KEY = "%s"\nrequests.get(url, verify=False)\napp.run(debug=True)\n'
                                        'TOKEN = "${API_TOKEN}"\nPASSWORD = "changeme"\n' % FAKE_KEY)
        got = watch.check_file(self.root, path)
        self.assertEqual([(i["line"], i["kind"]) for i in got], [(1, "secret"), (2, "tls-off"), (3, "debug-on")])
        self.assertNotIn(FAKE_KEY, json.dumps(got))  # never repeats the secret
        self.assertIn("environment variable", got[0]["fix"])
        self.assertEqual(watch.check_file(self.root, self.write("tests/test_app.py", 'KEY = "%s"\n' % FAKE_KEY)), [])
        self.assertEqual([i["kind"] for i in watch.check_file(self.root, self.write(".env", "DB_PASSWORD=x\n"))], ["env-not-ignored"])
        self.write(".gitignore", ".env\n")
        self.assertEqual(watch.check_file(self.root, os.path.join(self.root, ".env")), [])

    def test_post_edit_hook_is_silent_unless_watching_and_warns_once(self):
        path = self.write("src/app.py", 'KEY = "%s"\n' % FAKE_KEY)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": self.root}
        self.assertIsNone(hook("post-edit", payload))
        code, out = run_cli("watch", "on", "--root", self.root)
        self.assertEqual((code, out["watching"]), (0, True))
        got = hook("post-edit", payload)
        self.assertEqual(got["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("Line 1 of src/app.py", got["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(hook("post-edit", payload))  # the same warning is not repeated
        with open(os.path.join(self.root, ".flight-check", ".gitignore")) as fh:
            self.assertIn("watch.json", fh.read())
        self.assertIsNone(watch.run_hook("post-edit", "not json", NOW))
        self.assertEqual(run_cli("watch", "off", "--root", self.root)[1]["watching"], False)
        self.write("src/app.py", 'OTHER = "%s"\n' % FAKE_KEY)
        self.assertIsNone(hook("post-edit", payload))


class WatchProjectTests(ProjectTestCase):
    def test_stop_suggests_a_paid_check_once_per_set_of_changes(self):
        run_cli("watch", "on", "--root", self.root, "--now", T1)
        payload = {"cwd": self.root, "stop_hook_active": False}
        self.assertIsNone(hook("stop", payload, NOW - datetime.timedelta(minutes=30)))  # nothing changed since the audit
        self.edit("src/store.py")
        self.assertIsNone(hook("stop", payload, NOW - datetime.timedelta(minutes=27)))  # scanned 3 minutes ago: waits
        got = hook("stop", payload)
        self.assertEqual(got["decision"], "block")
        self.assertIn("/flight-check:audit", got["reason"])
        self.assertIn("costs about $", got["reason"])
        later = NOW + datetime.timedelta(hours=1)
        self.assertIsNone(hook("stop", payload, later))  # same changes: not suggested again
        self.edit("README.md", "\nmore\n")  # another file changed: suggested again, but not within 20 minutes
        self.assertIsNone(hook("stop", payload, NOW + datetime.timedelta(minutes=5)))
        self.assertIsNotNone(hook("stop", payload, later + datetime.timedelta(minutes=6)))
        self.assertIsNone(hook("stop", payload, later + datetime.timedelta(hours=1)))  # same files, within 2 hours
        self.assertIsNotNone(hook("stop", payload, later + datetime.timedelta(hours=3)))
        self.assertIsNone(hook("stop", dict(payload, stop_hook_active=True), later + datetime.timedelta(hours=6)))

    def test_session_start_and_reminders(self):
        self.assertIsNone(hook("session-start", {"cwd": self.root}))
        run_cli("watch", "on", "--root", self.root)
        code, out = run_cli("later", "--root", self.root, "--id", "FC-0001", "--note", "after the demo")
        self.assertEqual(code, 0, out)
        run_cli("later", "--root", self.root, "--id", "FC-0002", "--on", "2099-01-01")
        self.assertEqual(run_cli("later", "--root", self.root, "--id", "FC-0003", "--on", "soon")[0], 1)
        ctx = hook("session-start", {"cwd": self.root, "source": "startup"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Not ready yet", ctx)
        self.assertIn("FC-0001: after the demo", ctx)
        self.assertIn("waiting for the user to sort", ctx)  # the fixture's legal question
        self.assertNotIn("FC-0002", ctx.split("Reminders")[1])  # not due yet
        self.assertEqual([r["id"] for r in run_cli("later", "--root", self.root, "--done", "FC-0001")[1]["reminders"]], ["FC-0002"])
        self.assertIn("watch.json", state.GITIGNORE)


if __name__ == "__main__":
    unittest.main()

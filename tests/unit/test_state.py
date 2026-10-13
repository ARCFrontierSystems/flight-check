"""Project State persistence (docs/design/project-state.md, Phase 3)."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import cli, constants, ledger, lockfile, state  # noqa: E402

T1, T2, T3 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z"


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    try:
        return code, json.loads(buf.getvalue())
    except ValueError:
        return code, buf.getvalue()


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def write_run(base, run_id):
    return helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=base,
                             run=dict(helpers.base_run(), run_id=run_id))


def make_repo(tmp, remote="https://user:s3cret-token@github.com/example/shop.git"):
    """A directory that looks like a git checkout (no git needed): .git/config with an origin remote."""
    os.makedirs(os.path.join(tmp, ".git"))
    with open(os.path.join(tmp, ".git", "config"), "w") as fh:
        fh.write('[core]\n\tbare = false\n[remote "origin"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n' % remote)
    return tmp


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-state-")
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        make_repo(self.root)
        self.sd = state.default_state_dir(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def init(self, **kw):
        return run_cli("state", "init", "--root", self.root, "--now", T1, *sum((["--" + k, v] for k, v in kw.items()), []))

    def load(self, **kw):
        return state.load(self.sd, root=self.root, **kw)


class InitAndLoadTests(StateTestCase):
    def test_init_creates_valid_state_with_identity_and_log(self):
        code, out = self.init(name="Shop")
        self.assertEqual(code, 0, out)
        st = self.load()
        ident = st.project["identity"]
        self.assertRegex(ident["project_id"], r"^[0-9a-f]{32}$")
        self.assertEqual((ident["name"], ident["root"], ident["vcs"]), ("Shop", ".", "git"))
        self.assertEqual(ident["repository"], "github.com/example/shop")  # credentials removed
        self.assertNotIn("s3cret", read(os.path.join(self.sd, "project.json")).decode())
        self.assertEqual(st.project["revision"], 1)
        self.assertEqual([e["seq"] for e in st.events], [1])
        self.assertEqual(st.registers, state.new_registers())
        self.assertIn("backups/", read(os.path.join(self.sd, ".gitignore")).decode())
        self.assertEqual(os.stat(os.path.join(self.sd, "project.json")).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.sd, "pending")))
        code, out = self.init()
        self.assertEqual(code, 1)
        self.assertIn("already exists", out["error"])

    def test_monorepo_apps_get_separate_identities(self):
        a, b = os.path.join(self.root, "apps", "api"), os.path.join(self.root, "apps", "web")
        os.makedirs(a)
        os.makedirs(b)
        for app in (a, b):
            self.assertEqual(run_cli("state", "init", "--root", app, "--now", T1)[0], 0)
        pa = state.load(state.default_state_dir(a)).project["identity"]
        pb = state.load(state.default_state_dir(b)).project["identity"]
        self.assertEqual((pa["root"], pb["root"]), ("apps/api", "apps/web"))
        self.assertNotEqual(pa["project_id"], pb["project_id"])

    def test_project_without_git(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertEqual(run_cli("state", "init", "--root", plain, "--now", T1)[0], 0)
        ident = state.load(state.default_state_dir(plain)).project["identity"]
        self.assertEqual((ident["root"], ident["vcs"]), (".", "none"))
        self.assertNotIn("repository", ident)

    def test_repository_url_cleaning(self):
        for raw, want in (("git@github.com:owner/repo.git", "github.com/owner/repo"),
                          ("https://x-access-token:abc123@github.com/owner/repo", "github.com/owner/repo"),
                          ("ssh://git@gitlab.example.com:2222/group/sub/repo.git", "gitlab.example.com/group/sub/repo"),
                          ("file:///etc/passwd", None), ("not a url", None)):
            self.assertEqual(state.clean_repository_url(raw), want, raw)


class MigrationTests(StateTestCase):
    def test_v1_ledger_migrates_with_legal_items_and_a_backup(self):
        os.makedirs(self.sd)
        src = os.path.join(helpers.REPO, ".flight-check", "ledger.json")
        shutil.copy(src, os.path.join(self.sd, "ledger.json"))
        before = read(os.path.join(self.sd, "ledger.json"))
        st = self.load()
        self.assertFalse(st.initialized)
        self.assertTrue(st.ledger["entries"])
        code, out = self.init()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["migrated_from_1_0"])
        old = json.loads(before)
        st = self.load()
        self.assertEqual(st.ledger["schema_version"], "2.0")
        self.assertEqual(sorted(e["id"] for e in st.ledger["entries"].values()), sorted(e["id"] for e in old["entries"].values()))
        self.assertEqual(st.project["identity"]["name"], old["project"]["name"])
        self.assertIn("1.0 ledger with %d finding" % len(st.ledger["entries"]), st.events[0]["reason"])
        self.assertEqual(st.events[1]["change"], {"ledger_schema": ["1.0", "2.0"]})
        # Every 1.0 legal status became a legal item with its history, linked from the finding.
        with_legal = {e["id"]: e["legal"] for e in old["entries"].values() if e.get("legal")}
        self.assertEqual(len(st.registers["legal"]), len(with_legal))
        items = {i["id"]: i for i in st.registers["legal"]}
        for e in st.ledger["entries"].values():
            if e["id"] in with_legal:
                item = items[e["legal"]["item"]]
                self.assertEqual(item["status"], constants.V1_LEGAL_TO_ITEM[with_legal[e["id"]]["status"]])
                self.assertEqual(len(item["history"]), len(with_legal[e["id"]]["history"]) + 1)
                self.assertNotIn("status", e["legal"])
        backups = state.list_backups(self.sd)
        self.assertEqual(len(backups), 1)
        self.assertEqual(read(os.path.join(backups[0]["backup"], "ledger.json")), before)
        self.assertEqual(run_cli("state", "check", "--root", self.root)[0], 0)

    def test_first_v1_ledger_command_migrates_and_logs(self):
        os.makedirs(self.sd)
        led = ledger.new_ledger("shop")
        ledger.save(os.path.join(self.sd, "ledger.json"), led)
        code, out = run_cli("ledger", "accept", "--ledger", os.path.join(self.sd, "ledger.json"), "--ref", "APPSEC-SECRETS",
                            "--risk", "No secret scanning in CI", "--reason", "Single developer, manual review",
                            "--owner", "owner", "--date", "2026-10-13", "--scope", "this release",
                            "--compensating-controls", "manual review", "--now", T2)
        self.assertEqual(code, 0, out)
        st = self.load()
        self.assertEqual([e["command"] for e in st.events], ["ledger accept", "ledger accept"])
        self.assertEqual(st.events[0]["record"], "state")
        self.assertEqual(st.events[1]["record"], "control:APPSEC-SECRETS")
        self.assertEqual(st.events[1]["change"], {"accepted": ["no", "yes"]})
        self.assertEqual(st.events[1]["actor"], "user")
        self.assertEqual(st.project["identity"]["root"], ".")


class FinalizeIntegrationTests(unittest.TestCase):
    def test_finalize_logs_runs_and_stamps_readiness(self):
        tmp = tempfile.mkdtemp(prefix="flight-check-state-")
        self.addCleanup(shutil.rmtree, tmp, True)
        led = os.path.join(tmp, "ledger.json")
        for n, now in enumerate((T1, T2), 1):
            run_dir = write_run(tmp, "20261013T1%d0000Z" % n)
            code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", led, "--now", now)
            self.assertEqual(code, 0, out)
        st = state.load(tmp)
        runs = [e for e in st.events if "gate" in e["change"]]
        self.assertEqual(st.events[0]["record"], "state")  # state created by the first finalize
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["change"]["gate"][0], None)
        self.assertEqual(runs[1]["change"]["gate"][0], runs[0]["change"]["gate"][1])
        stamps = st.project["readiness"]
        self.assertEqual(len(stamps), 2)
        self.assertLess(stamps[0]["revision"], stamps[1]["revision"])
        self.assertEqual(stamps[-1]["revision"], st.project["revision"])
        self.assertEqual(stamps[-1]["decision"], out["gate"])

    def test_run_from_another_project_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="flight-check-state-")
        self.addCleanup(shutil.rmtree, tmp, True)
        led = os.path.join(tmp, "ledger.json")
        run1 = write_run(tmp, "20261013T100000Z")
        self.assertEqual(run_cli("finalize", run1, "--root", helpers.FIXTURE_ROOT, "--ledger", led, "--now", T1)[0], 0)
        revision = state.load(tmp).project["revision"]
        run2 = write_run(tmp, "20261013T110000Z")
        with open(os.path.join(run2, "run-ref.json"), "w") as fh:
            json.dump({"project_id": "f" * 32, "revision": 1, "run_id": "20261013T110000Z"}, fh)
        code, out = run_cli("finalize", run2, "--root", helpers.FIXTURE_ROOT, "--ledger", led, "--now", T2)
        self.assertEqual(code, 1)
        self.assertIn("will not be merged", out["error"])
        self.assertFalse(os.path.exists(os.path.join(run2, "audit.final.json")))
        self.assertEqual(state.load(tmp).project["revision"], revision)

    def test_init_run_records_the_project(self):
        tmp = tempfile.mkdtemp(prefix="flight-check-state-")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "proj")
        os.makedirs(root)
        run_cli("state", "init", "--root", root, "--now", T1)
        code, out = run_cli("init-run", "--base", os.path.join(root, ".flight-check", "runs"), "--root", root, "--now", T2)
        self.assertEqual(code, 0, out)
        with open(os.path.join(out["run_dir"], "run-ref.json")) as fh:
            ref = json.load(fh)
        self.assertEqual(ref["project_id"], state.load(os.path.join(root, ".flight-check")).project["identity"]["project_id"])


class IntegrityTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init()[0], 0)

    def check(self):
        return run_cli("state", "check", "--root", self.root)

    def test_clean_state_checks_ok(self):
        code, out = self.check()
        self.assertEqual((code, out["ok"], out["problems"]), (0, True, []))

    def test_edited_history_is_detected(self):
        path = os.path.join(self.sd, "history.jsonl")
        text = read(path).decode().replace("Created project state.", "Nothing happened.")
        with open(path, "w") as fh:
            fh.write(text)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any("does not match its content" in p for p in out["problems"]), out)
        with self.assertRaises(state.StateError):
            self.load()

    def test_removed_event_breaks_the_chain(self):
        run_cli("state", "backup", "--root", self.root, "--reason", "x", "--now", T2)  # does not log
        self.assertEqual(run_cli("state", "adopt", "--root", self.root, "--reason", "same project", "--now", T2)[0], 0)
        path = os.path.join(self.sd, "history.jsonl")
        lines = read(path).decode().splitlines(True)
        with open(path, "w") as fh:
            fh.write(lines[1])
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(out["problems"])

    def test_revision_out_of_step_with_log(self):
        path = os.path.join(self.sd, "project.json")
        doc = json.loads(read(path))
        doc["revision"] = 7
        with open(path, "w") as fh:
            json.dump(doc, fh)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any("revision 7" in p for p in out["problems"]), out)

    def test_corrupt_and_malicious_files(self):
        path = os.path.join(self.sd, "project.json")
        good = read(path)
        cases = [b'{"schema_version": "2.0", "revision": 1', b"[]", b'{"schema_version": "9.0"}',
                 good.replace(b'"profile"', b'"profile_x"')]
        for bad in cases:
            with open(path, "wb") as fh:
                fh.write(bad)
            with self.assertRaises(state.StateError):
                self.load()
            code, out = self.check()
            self.assertEqual(code, 1, bad)
        with open(path, "wb") as fh:
            fh.write(b'{"schema_version": "9.0"}')
        with self.assertRaises(state.StateError) as ctx:
            self.load()
        self.assertIn("newer Flight Check", str(ctx.exception))
        with open(path, "wb") as fh:
            fh.write(good)

    def test_generated_fact_cannot_claim_to_be_declared(self):
        with self.assertRaises(state.StateError):
            with state.transaction(self.sd, "flight-check", "test", T2, root=self.root) as tx:
                tx.project["profile"]["facts"].append({
                    "id": "PF-0001", "aspect": "hosting", "value": "on-premises", "origin": "declared",
                    "source": "flight-check", "status": "current", "evidence": [], "first_observed": T2,
                    "last_observed": T2, "history": []})
                tx.project["profile"]["next_id"] = 2
        self.assertEqual(self.load().project["revision"], 1)  # nothing written

    def test_symlinked_state_files_are_refused(self):
        outside = os.path.join(self.tmp, "outside.json")
        shutil.copy(os.path.join(self.sd, "registers.json"), outside)
        os.unlink(os.path.join(self.sd, "registers.json"))
        os.symlink(outside, os.path.join(self.sd, "registers.json"))
        with self.assertRaises(state.StateError) as ctx:
            self.load()
        self.assertIn("symbolic link", str(ctx.exception))

    def test_symlinked_state_directory_is_refused(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        make_repo(other)
        os.symlink(self.sd, os.path.join(other, ".flight-check"))
        code, out = run_cli("state", "show", "--root", other)
        self.assertEqual(code, 1)
        self.assertIn("symbolic link", out["error"])


class RecoveryTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init()[0], 0)
        self.before = {n: read(os.path.join(self.sd, n)) for n in ("project.json", "registers.json", "history.jsonl")}

    def change(self):
        with state.transaction(self.sd, "user", "test change", T2, root=self.root) as tx:
            tx.project["identity"]["name"] = "Renamed"

    def test_interrupted_before_logging_is_rolled_back(self):
        with mock.patch.object(state, "_append_events", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.change()
        self.assertTrue(os.path.isdir(os.path.join(self.sd, "pending")))
        with self.assertRaises(state.StateError) as ctx:
            self.change()
        self.assertIn("state recover", str(ctx.exception))
        code, out = run_cli("state", "recover", "--root", self.root)
        self.assertEqual((code, out["action"]), (0, "rolled_back"), out)
        for name, data in self.before.items():
            self.assertEqual(read(os.path.join(self.sd, name)), data, name)
        self.change()
        self.assertEqual(self.load().project["identity"]["name"], "Renamed")

    def test_partial_log_line_is_rolled_back(self):
        def half_write(path, events):
            with open(path, "ab") as fh:
                fh.write(b'{"seq": 2, "at"')
            raise OSError("power loss")
        with mock.patch.object(state, "_append_events", side_effect=half_write):
            with self.assertRaises(OSError):
                self.change()
        self.assertEqual(run_cli("state", "recover", "--root", self.root)[1]["action"], "rolled_back")
        self.assertEqual(read(os.path.join(self.sd, "history.jsonl")), self.before["history.jsonl"])
        self.assertEqual(run_cli("state", "check", "--root", self.root)[0], 0)

    def test_interrupted_after_logging_is_completed(self):
        with mock.patch.object(state, "_finish", side_effect=OSError("killed")):
            with self.assertRaises(OSError):
                self.change()
        code, out = run_cli("state", "recover", "--root", self.root)
        self.assertEqual(out["action"], "completed")
        st = self.load()
        self.assertEqual((st.project["identity"]["name"], st.project["revision"]), ("Renamed", 2))

    def test_nothing_is_written_when_the_command_fails(self):
        with self.assertRaises(RuntimeError):
            with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
                tx.project["identity"]["name"] = "Half done"
                raise RuntimeError("validation failed")
        for name, data in self.before.items():
            self.assertEqual(read(os.path.join(self.sd, name)), data)


class IsolationTests(StateTestCase):
    def test_state_copied_to_another_app_is_refused_until_adopted(self):
        self.assertEqual(self.init()[0], 0)
        app = os.path.join(self.root, "apps", "web")
        os.makedirs(app)
        shutil.copytree(self.sd, os.path.join(app, ".flight-check"))
        with self.assertRaises(state.StateError) as ctx:
            with state.transaction(os.path.join(app, ".flight-check"), "user", "test", T2, root=app):
                pass
        self.assertIn("state adopt", str(ctx.exception))
        code, out = run_cli("state", "check", "--root", app)
        self.assertEqual(code, 1)
        code, out = run_cli("state", "adopt", "--root", app, "--reason", "moved the app", "--now", T2)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["root"], [".", "apps/web"])
        self.assertEqual(run_cli("state", "check", "--root", app)[0], 0)

    def test_moving_the_whole_repository_keeps_identity(self):
        self.assertEqual(self.init()[0], 0)
        pid = self.load().project["identity"]["project_id"]
        moved = os.path.join(self.tmp, "moved")
        shutil.move(self.root, moved)
        self.assertEqual(run_cli("state", "check", "--root", moved)[0], 0)
        self.assertEqual(state.load(state.default_state_dir(moved)).project["identity"]["project_id"], pid)


class ExportImportBackupTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init()[0], 0)
        self.bundle = os.path.join(self.tmp, "export.json")

    def test_export_import_round_trip(self):
        self.assertEqual(run_cli("state", "export", "--root", self.root, "--out", self.bundle, "--now", T2)[0], 0)
        with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
            tx.project["identity"]["name"] = "Changed later"
        code, out = run_cli("state", "import", "--root", self.root, "--from", self.bundle, "--now", T3)
        self.assertEqual(code, 0, out)
        st = self.load()
        self.assertNotEqual(st.project["identity"]["name"], "Changed later")
        self.assertEqual(st.events[-1]["command"], "state import")
        self.assertIn("previous state", st.events[-1]["reason"])
        self.assertEqual(run_cli("state", "check", "--root", self.root)[0], 0)

    def test_import_from_another_project_needs_adopt(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        run_cli("state", "init", "--root", other, "--now", T1)
        run_cli("state", "export", "--root", other, "--out", self.bundle, "--now", T2)
        code, out = run_cli("state", "import", "--root", self.root, "--from", self.bundle, "--now", T3)
        self.assertEqual(code, 1)
        self.assertIn("--adopt", out["error"])
        self.assertEqual(run_cli("state", "import", "--root", self.root, "--from", self.bundle, "--adopt", "--now", T3)[0], 0)

    def test_tampered_export_is_rejected(self):
        run_cli("state", "export", "--root", self.root, "--out", self.bundle, "--now", T2)
        doc = json.loads(read(self.bundle))
        doc["files"]["project.json"]["identity"]["name"] = "Tampered"
        with open(self.bundle, "w") as fh:
            json.dump(doc, fh)
        code, out = run_cli("state", "import", "--root", self.root, "--from", self.bundle, "--now", T3)
        self.assertEqual(code, 1)
        self.assertIn("does not match its recorded hash", out["error"])

    def test_backup_and_restore_keep_the_log(self):
        code, out = run_cli("state", "backup", "--root", self.root, "--reason", "before experiment", "--now", T2)
        self.assertEqual(code, 0, out)
        saved = out["backup"]
        with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
            tx.project["identity"]["name"] = "Experiment"
        code, out = run_cli("state", "restore", "--root", self.root, "--from", saved, "--now", T3)
        self.assertEqual(code, 0, out)
        st = self.load()
        self.assertNotEqual(st.project["identity"]["name"], "Experiment")
        self.assertEqual([e["command"] for e in st.events][-2:], ["test", "state restore"])
        self.assertEqual(len(state.list_backups(self.sd)), 2)  # the manual one and the one before restore

    def test_restore_refuses_paths_outside_backups_and_damaged_backups(self):
        code, out = run_cli("state", "restore", "--root", self.root, "--from", self.tmp, "--now", T3)
        self.assertEqual(code, 1)
        saved = run_cli("state", "backup", "--root", self.root, "--reason", "b", "--now", T2)[1]["backup"]
        with open(os.path.join(saved, "registers.json"), "a") as fh:
            fh.write(" ")
        code, out = run_cli("state", "restore", "--root", self.root, "--from", saved, "--now", T3)
        self.assertEqual(code, 1)
        self.assertIn("recorded hash", out["error"])


class LockTests(StateTestCase):
    def test_concurrent_writer_waits_then_fails_clearly(self):
        self.assertEqual(self.init()[0], 0)
        with mock.patch.object(lockfile, "WAIT_SECONDS", 0.2):
            with lockfile.locked(self.sd):
                with self.assertRaises(state.StateError) as ctx:
                    with state.transaction(self.sd, "user", "test", T2, root=self.root):
                        pass
        self.assertIn("in use by another Flight Check command", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

"""End-to-end: run directory -> finalize -> report.md -> Attorney Review Packet PDF, through the CLI."""

import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, cli, constants, minischema, render_md  # noqa: E402
from flight_check_lib import packet  # noqa: E402

NOW_DT = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=datetime.timezone.utc)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def read_text(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    text = buf.getvalue()
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text


def finalized_run(verification=None, parts=None):
    tmp = tempfile.mkdtemp(prefix="flight-check-e2e-")
    part = helpers.all_controls_resolved(helpers.base_part())
    run_dir = helpers.write_run(parts=parts or [part], verification=verification, base=tmp)
    ledger_path = os.path.join(tmp, "ledger.json")
    code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", ledger_path, "--now", helpers.NOW)
    return tmp, run_dir, ledger_path, code, out


def summary_for(final):
    ids = [f["id"] for f in final["findings"]]
    return {
        "executive_summary": "The fixture has one confirmed injection issue that blocks release and a deletion behavior that contradicts the privacy policy.",
        "legal_review_summary": "One finding is a policy/implementation contradiction and is prepared for counsel review.",
        "remediation_priority": [{"finding_id": i, "rationale": "ordered by severity"} for i in ids],
        "accepted_risk_candidates": [],
        "regression_recommendations": ["Add a test that search_notes binds the term as a parameter."],
    }


def pdf_text(path):
    if not shutil.which("pdftotext"):
        return None
    return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout


def check_pdf_structure(data):
    """Verify header, EOF, xref offsets, and that every content stream inflates."""
    assert data.startswith(b"%PDF-1.4"), "missing header"
    assert data.rstrip().endswith(b"%%EOF"), "missing EOF"
    startxref = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
    assert data[startxref:startxref + 4] == b"xref"
    m = re.search(rb"xref\n0 (\d+)\n", data[startxref:])
    count = int(m.group(1))
    table = data[startxref + m.end():].split(b"\n")[:count]
    for i, row in enumerate(table[1:], 1):
        off = int(row[:10])
        assert data[off:].startswith(("%d 0 obj" % i).encode()), "bad offset for object %d" % i
    for sm in re.finditer(rb"/Length (\d+) /Filter /FlateDecode >>\nstream\n", data):
        length = int(sm.group(1))
        zlib.decompress(data[sm.end():sm.end() + length])
    return count


class SecretMaskingTests(unittest.TestCase):
    def test_secrets_in_agent_output_never_reach_json_or_ledger(self):
        # Built at runtime so this repository never contains strings that look like real credentials.
        key = "AKIA" + "ABCDEFGHIJKLMNOP"
        tmp = tempfile.mkdtemp(prefix="flight-check-mask-")
        root = os.path.join(tmp, "root")
        shutil.copytree(helpers.FIXTURE_ROOT, root)
        with open(os.path.join(root, "settings.py"), "w", encoding="utf-8") as fh:
            fh.write("DEBUG = False\nAWS_KEY = '%s'\n" % key)
        leak = helpers.sql_finding(
            local_id="F3", rule="application-security.hardcoded-credential", title="Hard-coded cloud key %s in settings" % key,
            explanation="settings.py assigns the cloud key %s directly in source." % key, control_ids=["APPSEC-SECRETS"],
            evidence=[{"kind": "code", "path": "settings.py", "start_line": 2, "end_line": 2, "quote": "AWS_KEY = '%s'" % key}])
        part = helpers.all_controls_resolved(helpers.base_part([helpers.sql_finding(), helpers.deletion_finding(), leak]))
        run_dir = helpers.write_run(parts=[part], base=tmp)
        ledger_path = os.path.join(tmp, "ledger.json")
        code, out = run_cli("finalize", run_dir, "--root", root, "--ledger", ledger_path, "--now", helpers.NOW)
        self.assertEqual(code, 0, out)
        final_text = read_text(os.path.join(run_dir, "audit.final.json"))
        self.assertNotIn(key, final_text)
        self.assertNotIn(key, read_text(ledger_path))
        self.assertIn("[REDACTED", final_text)
        self.assertTrue(any("Masked" in w for w in out["warnings"]), out["warnings"])
        masked = [f for f in read_json(os.path.join(run_dir, "audit.final.json"))["findings"] if f["title"].startswith("Hard-coded")]
        self.assertEqual(masked[0]["evidence_check"], "PASSED")  # the redaction marker acts as a gap


class PdfLanguageTests(unittest.TestCase):
    def test_language_tag_is_validated(self):
        from flight_check_lib import pdf
        self.assertIn(b"/Lang (de-CH)", pdf.Document("t", lang="de-CH").to_bytes())
        for bad in ("en) /OpenAction", "", "english language"):
            with self.assertRaises(ValueError):
                pdf.Document("t", lang=bad)


class EndToEndTests(unittest.TestCase):
    def test_finalize_report_and_gate(self):
        verification = {"verdicts": [
            {"local_id": "appsec.F1", "verdict": "CONFIRMED", "notes": "Re-read lines 11-12; concatenation confirmed and no escaping upstream."},
            {"local_id": "appsec.F2", "verdict": "CONFIRMED", "notes": "Flag-only deletion confirmed against policy line 3."},
        ]}
        tmp, run_dir, ledger_path, code, out = finalized_run(verification)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        self.assertEqual(minischema.validate_def(final, catalog.schema(), "auditFinal"), [])
        ids = {f["rule"]: f["id"] for f in final["findings"]}
        self.assertEqual(sorted(ids.values()), ["FC-0001", "FC-0002"])
        sql = next(f for f in final["findings"] if f["rule"] == "application-security.sql-injection")
        self.assertEqual(sql["evidence_check"], "PASSED")
        self.assertTrue(sql["release_blocking_effective"])
        # Other domains are not applicable per inventory; supply-chain etc. controls become NOT_APPLICABLE.
        self.assertEqual(final["gate"]["decision"], constants.GATE_NOT_READY)
        self.assertEqual(len(final["positive_controls"]), 1)
        self.assertTrue(final["evidence_index"])

        write_json(os.path.join(run_dir, "summary.json"), summary_for(final))
        code, out = run_cli("render", run_dir)
        self.assertEqual(code, 0, out)
        report = read_text(os.path.join(run_dir, "report.md"))
        for num, title, _ in render_md.SPEC_SECTIONS:
            self.assertIn("## %s. %s" % (num, title), report)
        self.assertIn(constants.GATE_NOT_READY, report)
        self.assertIn("Questions for qualified counsel", report)

        code, out = run_cli("gate", os.path.join(run_dir, "audit.final.json"), "--exit-code", "--json")
        self.assertEqual(code, constants.GATE_EXIT_CODES[constants.GATE_NOT_READY])

        # finalizing twice is refused (ledger integrity)
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", ledger_path)
        self.assertEqual(code, 1)

    def test_verifier_rejection_and_fabricated_evidence(self):
        fake = helpers.sql_finding(local_id="F3", rule="application-security.command-injection",
                                   title="Shell command built from user input in store module",
                                   evidence=[{"kind": "code", "path": "src/store.py", "start_line": 5, "end_line": 6,
                                              "quote": "os.system('rm ' + user_input)"}])
        part = helpers.all_controls_resolved(helpers.base_part([helpers.sql_finding(), helpers.deletion_finding(), fake]))
        verification = {"verdicts": [{"local_id": "appsec.F1", "verdict": "REJECTED",
                                      "notes": "Callers validate the term against an allowlist before calling search_notes."}]}
        tmp, run_dir, ledger_path, code, out = finalized_run(verification, parts=[part])
        self.assertEqual(code, 0, out)
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        self.assertEqual([f["local_id"] for f in final["rejected_findings"]], ["appsec.F1"])
        fab = next(f for f in final["findings"] if f["local_id"] == "appsec.F3")
        self.assertEqual(fab["evidence_check"], "FAILED")
        self.assertEqual(fab["effective_confidence"], "UNVERIFIED")
        # fabricated HIGH finding cannot be trusted -> insufficient evidence, not a confident block
        self.assertEqual(final["gate"]["decision"], constants.GATE_INSUFFICIENT)
        inj = next(c for c in final["controls"] if c["id"] == "APPSEC-INJECTION")
        self.assertEqual(inj["state"], "UNVERIFIED")

    def test_duplicate_findings_are_merged(self):
        main = helpers.all_controls_resolved(helpers.base_part())
        dup = helpers.deletion_finding(local_id="D1", legal=dict(helpers.deletion_finding()["legal"],
                                       questions=["Should the backup retention schedule be disclosed separately from the 30-day deletion commitment?"]))
        second = {"agent": "data", "coverage": [], "controls": [], "findings": [dup], "positive_controls": [], "unverified_areas": []}
        verification = {"verdicts": [
            {"local_id": "appsec.F1", "verdict": "CONFIRMED", "notes": "Concatenation confirmed on lines 11-12."},
            {"local_id": "appsec.F2", "verdict": "CONFIRMED", "notes": "Flag-only deletion confirmed against the policy."},
            {"local_id": "data.D1", "verdict": "DUPLICATE", "duplicate_of": "appsec.F2", "notes": "Same deletion contradiction, same lines."},
        ]}
        tmp, run_dir, ledger_path, code, out = finalized_run(verification, parts=[main, second])
        self.assertEqual(code, 0, out)
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        self.assertEqual(len(final["findings"]), 2)
        self.assertEqual([f["local_id"] for f in final["duplicate_findings"]], ["data.D1"])
        kept = next(f for f in final["findings"] if f["local_id"] == "appsec.F2")
        self.assertEqual(kept["also_reported_by"][0]["local_id"], "data.D1")
        self.assertEqual(len(kept["legal"]["questions"]), 3)

    def test_near_duplicate_questions_not_merged_twice(self):
        main = helpers.all_controls_resolved(helpers.base_part())
        base_q = helpers.deletion_finding()["legal"]["questions"][1]
        dup = helpers.deletion_finding(local_id="D1", legal=dict(helpers.deletion_finding()["legal"],
                                       questions=["What retention exceptions, if any, should apply to the deleted user data and to backups?"]))
        second = {"agent": "data", "coverage": [], "controls": [], "findings": [dup], "positive_controls": [], "unverified_areas": []}
        verification = {"verdicts": [{"local_id": "data.D1", "verdict": "DUPLICATE", "duplicate_of": "appsec.F2", "notes": "Same deletion contradiction."}]}
        tmp, run_dir, ledger_path, code, out = finalized_run(verification, parts=[main, second])
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        kept = next(f for f in final["findings"] if f["local_id"] == "appsec.F2")
        self.assertIn(base_q, kept["legal"]["questions"])
        self.assertEqual(len(kept["legal"]["questions"]), 2)

    def test_agent_field_must_match_file_name(self):
        part = helpers.all_controls_resolved(helpers.base_part())
        part["agent"] = "appsec-auditor"
        tmp = tempfile.mkdtemp()
        run = helpers.base_run()
        run_dir = os.path.join(tmp, run["run_id"])
        os.makedirs(run_dir)
        write_json(os.path.join(run_dir, "run.json"), run)
        write_json(os.path.join(run_dir, "inventory.json"), helpers.base_inventory())
        write_json(os.path.join(run_dir, "part-appsec.json"), part)
        code, out = run_cli("validate", run_dir)
        self.assertEqual(code, 1)
        self.assertTrue(any("file name says 'appsec'" in e for e in out["errors"]), out["errors"])

    def test_bad_duplicate_reference_is_rejected(self):
        main = helpers.all_controls_resolved(helpers.base_part())
        verification = {"verdicts": [{"local_id": "appsec.F1", "verdict": "DUPLICATE", "duplicate_of": "appsec.F1", "notes": "points at itself, invalid"}]}
        tmp = tempfile.mkdtemp()
        run_dir = helpers.write_run(parts=[main], verification=verification, base=tmp)
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", os.path.join(tmp, "l.json"))
        self.assertEqual(code, 1)
        self.assertTrue(any("duplicate_of" in e for e in out["errors"]))

    def test_invalid_run_writes_nothing(self):
        bad = helpers.base_part([helpers.sql_finding(counterevidence=[])])
        tmp = tempfile.mkdtemp()
        run_dir = helpers.write_run(parts=[bad], base=tmp)
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", os.path.join(tmp, "ledger.json"))
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertFalse(os.path.exists(os.path.join(run_dir, "audit.final.json")))
        self.assertFalse(os.path.exists(os.path.join(tmp, "ledger.json")))

    def test_attorney_review_packet(self):
        tmp, run_dir, ledger_path, code, out = finalized_run()
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        legal_id = next(f["id"] for f in final["findings"] if f.get("legal"))
        sql_id = next(f["id"] for f in final["findings"] if not f.get("legal"))
        req = {
            "project_identifier": "mini-target (fixture) — édition ☃",
            "executive_summary": "This packet collects the finding where the privacy policy's deletion commitment differs from implemented behavior.",
            "overall_summary": "One policy/implementation contradiction about account deletion needs counsel's input before the deletion approach is finalized.",
            "finding_ids": [legal_id],
            "per_finding": {legal_id: {"relevant_decisions": ["Whether to keep a 30-day deletion commitment."],
                                       "follow_up": ["Record counsel's decision with /flight-check:track."]}},
        }
        req_path = os.path.join(tmp, "req.json")
        write_json(req_path, req)
        pdf_path = os.path.join(tmp, "packet.pdf")
        code, out = run_cli("packet", run_dir, "--request", req_path, "--out", pdf_path, "--ledger", ledger_path, "--now", helpers.NOW)
        self.assertEqual(code, 0, out)
        data = read_text(pdf_path, "rb")
        check_pdf_structure(data)
        self.assertIn(b"/Lang (en)", data)
        self.assertIn(b"/DisplayDocTitle true", data)
        self.assertGreaterEqual(out["pages"], 3)
        self.assertEqual(out["replaced_characters"], 1)  # the snowman cannot be represented
        md = read_text(out["markdown"])
        self.assertIn(constants.PACKET_DISCLAIMER, md)
        text = pdf_text(pdf_path)
        if text is not None:
            flat = " ".join(text.split())
            self.assertIn("FLIGHT CHECK — ATTORNEY REVIEW PACKET", flat)
            self.assertIn(" ".join(constants.PACKET_DISCLAIMER.split()), flat)
            self.assertIn(legal_id, flat)
            self.assertIn("Questions for qualified counsel", flat)
            self.assertIn("Attorney notes", flat)
            self.assertIn("Jurisdiction-specific legal research: UNVERIFIED", flat)
            self.assertIn("POLICY/IMPLEMENTATION CONTRADICTION", flat)
        led = read_json(ledger_path)
        entry = next(e for e in led["entries"].values() if e["id"] == legal_id)
        self.assertIn("attorney review packet", entry["legal"]["history"][-1]["note"])

        # non-legal finding and legal conclusions are refused
        bad = dict(req, finding_ids=[sql_id], per_finding={})
        write_json(req_path, bad)
        code, out = run_cli("packet", run_dir, "--request", req_path, "--out", pdf_path)
        self.assertEqual(code, 1)
        bad = dict(req, overall_summary="Counsel will confirm the service is GDPR compliant once deletion is fixed, so this is low priority overall.")
        write_json(req_path, bad)
        code, out = run_cli("packet", run_dir, "--request", req_path, "--out", pdf_path)
        self.assertEqual(code, 1)
        self.assertTrue(any("prohibited legal conclusion" in e for e in out["errors"]))

    def test_init_run_and_ledger_cli(self):
        tmp = tempfile.mkdtemp()
        code, out = run_cli("init-run", "--base", os.path.join(tmp, "runs"), "--root", helpers.REPO, "--now", helpers.NOW)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["run_id"], "20261004T120000Z")
        self.assertTrue(os.path.isdir(out["run_dir"]))
        self.assertTrue(os.path.exists(os.path.join(tmp, "runs", ".gitignore")))
        tmp2, run_dir, ledger_path, code, out = finalized_run()
        code, out = run_cli("ledger", "accept", "--ledger", ledger_path, "--ref", "FC-0001", "--risk", "r risk", "--reason", "",
                            "--owner", "o", "--date", "2026-10-01", "--scope", "scope", "--compensating-controls", "none")
        self.assertEqual(code, 1)
        code, out = run_cli("ledger", "legal", "--ledger", ledger_path, "--id", "FC-0002", "--status", "DECISION_RECEIVED",
                            "--note", "", "--source", "user")
        self.assertEqual(code, 1)
        code, out = run_cli("ledger", "show", "--ledger", ledger_path)
        self.assertEqual(code, 0)
        self.assertEqual(len(out["entries"]), 2)


class HandlerReviewTests(unittest.TestCase):
    """A handler reviewer reports findings for its files only; its NOT_MET controls outrank the main agent's."""

    def _parts(self, reviewer_controls):
        main = helpers.all_controls_resolved(helpers.base_part([helpers.deletion_finding()]))
        main["controls"] = [c if c["id"] != "APPSEC-INJECTION" else
                            {"id": "APPSEC-INJECTION", "state": "VERIFIED", "rationale": "Queries outside the delegated files are bound.",
                             "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 11, "end_line": 12,
                                           "quote": helpers.sql_finding()["evidence"][0]["quote"]}]}
                            for c in main["controls"]]
        reviewer = {"agent": "appsec-h1", "coverage": [
            {"domain": "application-security", "status": "ASSESSED", "rationale": "Handler review: read src/store.py end to end.",
             "searches": ["Read src/store.py"]}],
            "controls": reviewer_controls, "findings": [helpers.sql_finding()], "positive_controls": [],
            "unverified_areas": [], "notes": [], "truncation": []}
        return [main, reviewer]

    def test_reviewer_not_met_outranks_main_verified(self):
        parts = self._parts([{"id": "APPSEC-INJECTION", "state": "NOT_MET", "related_findings": ["F1"]}])
        tmp, run_dir, ledger_path, code, out = finalized_run(parts=parts)
        self.assertEqual(code, 0, out)
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        ctl = next(c for c in final["controls"] if c["id"] == "APPSEC-INJECTION")
        self.assertEqual(ctl["state"], "NOT_MET")
        self.assertEqual(sorted(ctl["reported_by"]), ["appsec", "appsec-h1"])
        cov = next(c for c in final["coverage"] if c["domain"] == "application-security")
        self.assertEqual(cov["status"], "ASSESSED")
        self.assertIn("appsec-h1", cov["reported_by"])
        self.assertTrue(any(f["source_agent"] == "appsec-h1" for f in final["findings"]))

    def test_failed_reviewer_keeps_the_gate_from_passing(self):
        # The skill replaces a reviewer that failed validation with a part that reports its controls UNVERIFIED.
        parts = self._parts([{"id": "APPSEC-INJECTION", "state": "UNVERIFIED",
                              "missing_evidence": "Handler review of src/store.py failed validation."}])
        parts[1]["findings"] = []
        parts[1]["coverage"][0] = {"domain": "application-security", "status": "NOT_ASSESSED",
                                   "rationale": "Agent output failed validation after two attempts"}
        tmp, run_dir, ledger_path, code, out = finalized_run(parts=parts)
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        ctl = next(c for c in final["controls"] if c["id"] == "APPSEC-INJECTION")
        self.assertEqual(ctl["state"], "UNVERIFIED")
        self.assertEqual(final["gate"]["decision"], constants.GATE_INSUFFICIENT)


class PacketQuestionTests(unittest.TestCase):
    def _final_and_id(self):
        tmp, run_dir, ledger_path, code, out = finalized_run()
        final = read_json(os.path.join(run_dir, "audit.final.json"))
        f = next(f for f in final["findings"] if f.get("legal"))
        f["legal"]["questions"] = ["First question about the deletion period?", "Second question about backups?",
                                   "First question about the deletion period, asked again in other words?"]
        return final, f

    def _request(self, fid, per=None):
        return {"project_identifier": "p", "finding_ids": [fid], "per_finding": per or {},
                "executive_summary": "This packet collects one finding about the account deletion commitment.",
                "overall_summary": "One contradiction about account deletion needs counsel's input before a decision."}

    def _pdf_flat_text(self, data):
        path = os.path.join(tempfile.mkdtemp(), "p.pdf")
        with open(path, "wb") as fh:
            fh.write(data)
        check_pdf_structure(data)
        text = pdf_text(path)
        return " ".join(text.split()) if text is not None else None

    def test_request_can_choose_and_order_audit_questions(self):
        final, f = self._final_and_id()
        data, md_text, _ = packet.build(final, self._request(f["id"], {f["id"]: {"questions": [2, 1]}}), None, NOW_DT)
        flat = self._pdf_flat_text(data)
        if flat is not None:
            self.assertIn("1. Second question about backups?", flat)
            self.assertIn("2. First question about the deletion period?", flat)
            self.assertNotIn("asked again", flat)
            self.assertIn("were left out as repeats", flat)
        section = md_text.split("### Questions for qualified counsel", 1)[1].split("### Follow-up items", 1)[0]
        self.assertIn("1. Second question about backups?", section)
        self.assertIn("2. First question about the deletion period?", section)
        self.assertNotIn("asked again", section)
        self.assertIn("1 further question(s) from the audit were left out as repeats", section)

    def test_request_cannot_select_missing_or_repeated_questions(self):
        final, f = self._final_and_id()
        for chosen, message in (([4], "has 4, but the finding has 3"), ([1, 1], "contains duplicates")):
            with self.assertRaises(packet.PacketError) as ctx:
                packet.build(final, self._request(f["id"], {f["id"]: {"questions": chosen}}), None, NOW_DT)
            self.assertIn(message, str(ctx.exception))

    def test_all_questions_kept_when_none_chosen(self):
        final, f = self._final_and_id()
        _, md_text, _ = packet.build(final, self._request(f["id"]), None, NOW_DT)
        self.assertIn("3. First question about the deletion period, asked again", md_text)
        self.assertNotIn("further question(s)", md_text)

    def test_business_decision_without_questions_says_so(self):
        final, f = self._final_and_id()
        f["legal"] = {"classification": "BUSINESS DECISION REQUIRED", "decisions_needed": ["Which deletion period to promise."]}
        data, md_text, _ = packet.build(final, self._request(f["id"]), None, NOW_DT)
        flat = self._pdf_flat_text(data)
        if flat is not None:
            self.assertIn(" ".join(packet.NO_QUESTIONS_BUSINESS.split()), flat)
        self.assertIn(packet.NO_QUESTIONS_BUSINESS, md_text)
        self.assertNotIn("No specific questions were generated", md_text)
        with self.assertRaises(packet.PacketError):
            packet.build(final, self._request(f["id"], {f["id"]: {"questions": [1]}}), None, NOW_DT)


class ListingCommandTests(unittest.TestCase):
    def test_runs_findings_and_inventory_reuse(self):
        tmp, run_dir, ledger_path, code, out = finalized_run()
        base = os.path.dirname(run_dir)
        code, out = run_cli("runs", "--base", base)
        self.assertEqual(code, 0)
        self.assertEqual(out["latest_finalized"], run_dir)
        self.assertEqual(out["runs"][0]["gate"], constants.GATE_NOT_READY)
        code, out = run_cli("findings", run_dir, "--legal")
        self.assertEqual(code, 0)
        self.assertEqual([f["legal"]["classification"] for f in out["findings"]], ["POLICY/IMPLEMENTATION CONTRADICTION"])
        code, out = run_cli("findings", run_dir, "--ids", "FC-0001,FC-9999", "--detail")
        self.assertEqual(code, 1)
        self.assertEqual(out["not_found"], ["FC-9999"])
        self.assertIn("quotes", out["findings"][0])
        code, out = run_cli("init-run", "--base", base, "--suffix", "reaudit", "--inventory-from", run_dir, "--now", "2026-10-05T09:00:00Z")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(os.path.join(out["run_dir"], "inventory.json")))


if __name__ == "__main__":
    unittest.main()

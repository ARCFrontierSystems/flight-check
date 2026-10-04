import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from fairtide_lib import evidence, render_md, textsafety  # noqa: E402


class TextSafetyTests(unittest.TestCase):
    def test_sanitize_removes_bidi_zero_width_ansi_and_controls(self):
        dirty = "safe\u202eevil\u200b text\x1b[31mred\x1b[0m\x07 end"
        self.assertEqual(textsafety.sanitize(dirty), "safeevil textred end")
        self.assertTrue(textsafety.find_invisible(dirty))

    def test_redacts_known_secret_formats(self):
        # Built at runtime so this repository never contains strings that look like real credentials.
        samples = [
            "aws = " + "AKIA" + "ABCDEFGHIJKLMNOP",
            "token: " + "ghp" + "_" + "a1B2" * 9,
            "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQ\n-----END " + "PRIVATE KEY-----",
            "postgres://admin:" + "hunter2pass" + "@db.internal/app",
            "API_KEY = \"" + "q8Zr2LmN" + "4vPx7TbK9wYs" + "\"",
        ]
        for s in samples:
            red, n = textsafety.redact(s)
            self.assertGreater(n, 0, s)
            self.assertIn("[REDACTED", red)

    def test_placeholders_are_not_redacted(self):
        for s in ('password = "${DB_PASSWORD}"', "api_key: process.env.API_KEY", 'secret = "changeme"', "token = <your-token>"):
            red, n = textsafety.redact(s)
            self.assertEqual(n, 0, s)
            self.assertEqual(red, s)

    def test_markdown_neutralizes_links_images_and_html(self):
        out = render_md.md("![x](http://evil/beacon.png) <script>alert(1)</script> [click](http://evil)")
        self.assertNotIn("![", out)
        self.assertNotIn("<script>", out)
        self.assertNotIn("](", out)

    def test_code_block_fence_longer_than_content(self):
        block = render_md.code_block("```\ninjected\n```")
        self.assertTrue(block.startswith("````"))


class EvidenceCheckTests(unittest.TestCase):
    root = helpers.FIXTURE_ROOT

    def check(self, **ev):
        base = {"kind": "code", "path": "src/store.py", "start_line": 11, "end_line": 11, "quote": "query = \"SELECT body FROM notes"}
        base.update(ev)
        return evidence.check_item(base, self.root, evidence.FileCache())

    def test_ok_and_whitespace_insensitive(self):
        self.assertEqual(self.check(), "OK")
        self.assertEqual(self.check(quote="query   =   \"SELECT   body FROM notes"), "OK")

    def test_line_tolerance_and_range(self):
        self.assertEqual(self.check(start_line=12, end_line=12), "OK")  # within 2-line tolerance
        self.assertEqual(self.check(start_line=900, end_line=901), "LINES_OUT_OF_RANGE")

    def test_quote_mismatch(self):
        self.assertEqual(self.check(quote="DROP TABLE users"), "QUOTE_MISMATCH")

    def test_gaps_for_redaction_and_ellipsis(self):
        self.assertEqual(self.check(start_line=11, end_line=12, quote="query = ... fetchall()"), "OK")
        self.assertEqual(self.check(quote="[REDACTED:credential]"), "UNCHECKABLE_QUOTE")

    def test_missing_file_and_out_of_root(self):
        self.assertEqual(self.check(path="src/nope.py"), "FILE_MISSING")
        self.assertEqual(self.check(path="../../README.md"), "OUT_OF_ROOT")

    def test_symlink_escaping_root_is_refused(self):
        d = tempfile.mkdtemp()
        outside = tempfile.mkdtemp()
        with open(os.path.join(outside, "secret.txt"), "w") as fh:
            fh.write("outside data\n")
        os.symlink(os.path.join(outside, "secret.txt"), os.path.join(d, "link.txt"))
        ev = {"kind": "code", "path": "link.txt", "start_line": 1, "end_line": 1, "quote": "outside data"}
        self.assertEqual(evidence.check_item(ev, d, evidence.FileCache()), "OUT_OF_ROOT")

    def test_summary_values(self):
        self.assertEqual(evidence.summarize(["OK", "NOT_CHECKED"]), "PASSED")
        self.assertEqual(evidence.summarize(["OK", "QUOTE_MISMATCH"]), "FAILED")
        self.assertEqual(evidence.summarize(["NOT_CHECKED"]), "NOT_APPLICABLE")
        self.assertEqual(evidence.summarize(["OK", "UNCHECKABLE_QUOTE"]), "PARTIAL")


if __name__ == "__main__":
    unittest.main()

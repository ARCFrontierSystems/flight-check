import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402

sys.path.insert(0, os.path.join(helpers.REPO, "tools", "blindtest"))
import owasp_subset  # noqa: E402
import score  # noqa: E402


def fake_benchmark():
    """A tiny stand-in with the benchmark's layout; no benchmark code is used."""
    root = tempfile.mkdtemp(prefix="fake-bench-")
    pkg = os.path.join(root, owasp_subset.PKG)
    os.makedirs(os.path.join(pkg, "helpers"))
    os.makedirs(os.path.join(pkg, "testcode"))
    with open(os.path.join(pkg, "helpers", "Util.java"), "w") as fh:
        fh.write("class Util {}\n")
    with open(os.path.join(root, "LICENSE"), "w") as fh:
        fh.write("license text\n")
    rows = ["# test name, category, real vulnerability, cwe, version"]
    n = 0
    for cat in sorted(owasp_subset.CATEGORIES):
        for real in ("true", "false"):
            for _ in range(3):
                n += 1
                name = "Case%05d" % n
                rows.append("%s,%s,%s,1" % (name, cat, real))
                with open(os.path.join(pkg, "testcode", name + ".java"), "w") as fh:
                    fh.write("class %s {\n  // %s\n}\n" % (name, cat))
    with open(os.path.join(root, "expectedresults-0.1.csv"), "w") as fh:
        fh.write("\n".join(rows) + "\n")
    return root


class OwaspSubsetTests(unittest.TestCase):
    def test_balanced_deterministic_subset_with_valid_manifest(self):
        bench = fake_benchmark()
        outs = []
        for i in range(2):
            out = os.path.join(tempfile.mkdtemp(), "fixture")
            self.assertEqual(owasp_subset.main(["--benchmark", bench, "--out", out, "--per-class", "2", "--seed", "s"]), 0)
            outs.append(out)
        manifests = [json.load(open(os.path.join(o, "ground-truth", "manifest.json"))) for o in outs]
        m = manifests[0]
        self.assertEqual(score.validate_manifest(m), [])
        self.assertEqual(len(m["issues"]), 2 * len(owasp_subset.CATEGORIES))
        self.assertEqual(len(m["secure_controls"]), 2 * len(owasp_subset.CATEGORIES))
        self.assertEqual([i["id"] for i in m["issues"]], [i["id"] for i in manifests[1]["issues"]])  # same seed, same cases
        app = os.path.join(outs[0], "app")
        self.assertFalse(os.path.exists(os.path.join(app, "ground-truth")))  # answers stay outside the audited app
        loc = m["issues"][0]["locations"][0]
        self.assertTrue(os.path.isfile(os.path.join(app, loc["path"])))
        self.assertEqual(loc["end_line"], 3)

    def test_refuses_to_write_inside_this_repository(self):
        with self.assertRaises(SystemExit):
            owasp_subset.main(["--benchmark", fake_benchmark(), "--out", os.path.join(helpers.REPO, "never-here")])
        self.assertFalse(os.path.exists(os.path.join(helpers.REPO, "never-here")))


if __name__ == "__main__":
    unittest.main()

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


HEADER = "/**\n * Fake suite header naming OWASP Benchmark, see https://owasp.org/x\n */\n"


def fake_benchmark():
    """A tiny stand-in with the benchmark's layout; no benchmark code is used."""
    root = tempfile.mkdtemp(prefix="fake-bench-")
    pkg = os.path.join(root, owasp_subset.PKG)
    os.makedirs(os.path.join(pkg, "helpers"))
    os.makedirs(os.path.join(pkg, "testcode"))
    with open(os.path.join(pkg, "helpers", "Util.java"), "w") as fh:
        fh.write("class Util {}\n")
    with open(os.path.join(pkg, "helpers", "ThingFactory.java"), "w") as fh:
        fh.write(HEADER + "package org.owasp.benchmark.helpers;\nclass ThingFactory { Object createThing() { return null; } }\n")
    with open(os.path.join(root, "LICENSE"), "w") as fh:
        fh.write("license text\n")
    rows = ["# test name, category, real vulnerability, cwe, version"]
    n = 0
    for cat in sorted(owasp_subset.CATEGORIES):
        for real in ("true", "false"):
            for _ in range(3):
                n += 1
                name = "BenchmarkTest%05d" % n
                rows.append("%s,%s,%s,1" % (name, cat, real))
                with open(os.path.join(pkg, "testcode", name + ".java"), "w") as fh:
                    fh.write(HEADER + "package org.owasp.benchmark.testcode;\nimport org.owasp.esapi.ESAPI;\n"
                             "@WebServlet(value = \"/%s-00/%s\")\nclass %s {\n  // %s\n"
                             "  String page = \"/%s-00/%s.html\"; String m = \"Problem executing %s - TestCase\";\n}\n"
                             % (cat, name, name, cat, cat, name, cat))
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
        manifests = []
        for o in outs:
            with open(os.path.join(o, "ground-truth", "manifest.json"), encoding="utf-8") as fh:
                manifests.append(json.load(fh))
        m = manifests[0]
        self.assertEqual(score.validate_manifest(m), [])
        self.assertEqual(len(m["issues"]), 2 * len(owasp_subset.CATEGORIES))
        self.assertEqual(len(m["secure_controls"]), 2 * len(owasp_subset.CATEGORIES))
        self.assertEqual([i["id"] for i in m["issues"]], [i["id"] for i in manifests[1]["issues"]])  # same seed, same cases
        app = os.path.join(outs[0], "app")
        self.assertFalse(os.path.exists(os.path.join(app, "ground-truth")))  # answers stay outside the audited app
        loc = m["issues"][0]["locations"][0]
        self.assertTrue(os.path.isfile(os.path.join(app, loc["path"])))
        self.assertEqual(loc["end_line"], 10)

    def test_deidentified_copy_hides_the_benchmark_and_keeps_answers_aligned(self):
        bench = fake_benchmark()
        out = os.path.join(tempfile.mkdtemp(), "fixture")
        self.assertEqual(owasp_subset.main(["--benchmark", bench, "--out", out, "--per-class", "1", "--seed", "s", "--deidentify"]), 0)
        app = os.path.join(out, "app")
        texts = []
        for dirpath, _, files in os.walk(app):
            for fn in files:
                with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                    texts.append((os.path.join(dirpath, fn), fh.read()))
        for path, text in texts:
            for word in ("benchmark", "testcode", "Thing", "testcase", "test case"):
                self.assertNotIn(word.lower(), (path + text).lower() if word != "Thing" else path + text, path)
            self.assertNotIn("owasp", text.replace("org.owasp.esapi", "").lower(), path)
            for cat in owasp_subset.CATEGORIES:
                self.assertNotIn("/%s-" % cat, text, path)  # servlet paths no longer name the category
        self.assertTrue(any("import org.owasp.esapi.ESAPI;" in t for _, t in texts))  # library imports are untouched
        self.assertFalse(os.path.exists(os.path.join(app, "LICENSE")))
        self.assertTrue(os.path.exists(os.path.join(out, "LICENSE")))
        self.assertTrue(os.path.exists(os.path.join(out, "NOTICE")))
        with open(os.path.join(out, "ground-truth", "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        with open(os.path.join(out, "ground-truth", "name-map.json"), encoding="utf-8") as fh:
            names = json.load(fh)
        self.assertEqual(score.validate_manifest(m), [])
        for item in m["issues"] + m["secure_controls"]:
            loc = item["locations"][0]
            self.assertTrue(os.path.isfile(os.path.join(app, loc["path"])), loc["path"])
            self.assertTrue(names[loc["path"]].endswith(item["id"][len("OB-"):] + ".java"))
            self.assertEqual(loc["end_line"], 7)  # the identifying header is gone

    def test_refuses_to_write_inside_this_repository(self):
        with self.assertRaises(SystemExit):
            owasp_subset.main(["--benchmark", fake_benchmark(), "--out", os.path.join(helpers.REPO, "never-here")])
        self.assertFalse(os.path.exists(os.path.join(helpers.REPO, "never-here")))


if __name__ == "__main__":
    unittest.main()

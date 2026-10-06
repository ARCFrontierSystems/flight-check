#!/usr/bin/env python3
"""Build a small, balanced OWASP Benchmark (Java) fixture and a Flight Check ground-truth manifest.

The OWASP Benchmark is a public test suite for application-security scanners. Each test case
is labeled as a real vulnerability or a false positive, which makes the false positives ready-made
decoys. Flight Check's own blind-test fixtures cover the other domains; this subset covers application
security. Models may have seen the benchmark during training, so treat scores on it as optimistic.

The benchmark is licensed under GPL-2.0. This tool copies files from a local checkout into a
directory outside this repository (with the benchmark's LICENSE) and never vendors them here.

Usage:
    python3 tools/blindtest/owasp_subset.py --benchmark /path/to/benchmarkjava \\
        --out /path/to/fixture --per-class 2 --seed flight-check-1

This writes <out>/app/ (the code to audit) and <out>/ground-truth/manifest.json (outside the app).

With --deidentify, the local copy is changed so the auditor cannot recognize the benchmark: package and
class names, servlet paths (which name the vulnerability category), and the identifying header comments.
The benchmark's LICENSE and a NOTICE describing the changes are written next to app/, not inside it, and
ground-truth/name-map.json records the original name of every file. The copy is for local testing only
and must never be distributed or committed.
Development-only; never shipped in the plugin.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys

PKG = os.path.join("src", "main", "java", "org", "owasp", "benchmark")
NEW_PKG = os.path.join("src", "main", "java", "org", "example", "portal")
HEADER = re.compile(r"\A\s*/\*.*?\*/\s*", re.S)
SERVLET_PATH = re.compile(r'"/[A-Za-z]+-\d+/(BenchmarkTest\d{5})(\.html)?"')
CASE_MESSAGE = re.compile(r'"[^"\n]*(?:TestCase|Test Case)[^"\n]*"')
TEST_NAME = re.compile(r"BenchmarkTest\d{5}")
URL = re.compile(r"https?://[^\s\"<>]*owasp[^\s\"<>]*", re.I)
NOTICE = ("This directory's app/ is a modified local copy of part of the OWASP Benchmark (Java), which is\n"
          "licensed under GPL-2.0 (see LICENSE). It was changed for blind testing: package, class, and file\n"
          "names, servlet paths, and header comments. ground-truth/name-map.json maps every file to its\n"
          "original. It is for local testing only and must not be distributed.\n")

# Category -> (severity, severity_tolerance, tier, domains). Severities are Flight Check's judgment of
# typical impact, not part of the benchmark, so the tolerance is generous.
CATEGORIES = {
    "sqli": ("HIGH", 1, "must", ["application-security"]),
    "cmdi": ("HIGH", 1, "must", ["application-security"]),
    "ldapi": ("HIGH", 1, "must", ["application-security"]),
    "xpathi": ("HIGH", 1, "must", ["application-security"]),
    "pathtraver": ("HIGH", 1, "must", ["application-security"]),
    "xss": ("MEDIUM", 1, "must", ["application-security"]),
    "crypto": ("MEDIUM", 1, "stretch", ["application-security", "data-protection"]),
    "hash": ("MEDIUM", 1, "stretch", ["application-security", "data-protection"]),
    "weakrand": ("MEDIUM", 1, "stretch", ["application-security", "data-protection"]),
    "securecookie": ("LOW", 1, "stretch", ["application-security", "authentication"]),
    "trustbound": ("LOW", 1, "stretch", ["application-security", "authentication", "authorization"]),
}


def load_expected(benchmark):
    names = [n for n in os.listdir(benchmark) if n.startswith("expectedresults-") and n.endswith(".csv")]
    if len(names) != 1:
        sys.exit("expected exactly one expectedresults-*.csv in %s, found %s" % (benchmark, names))
    rows = []
    with open(os.path.join(benchmark, names[0]), newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].startswith("#"):
                continue
            rows.append({"name": row[0].strip(), "category": row[1].strip(), "real": row[2].strip() == "true", "cwe": row[3].strip()})
    return names[0], rows


def select(rows, per_class, seed):
    def order(r):
        return hashlib.sha256((seed + ":" + r["name"]).encode("utf-8")).hexdigest()

    chosen = []
    for cat in sorted(CATEGORIES):
        for real in (True, False):
            pool = sorted((r for r in rows if r["category"] == cat and r["real"] == real), key=order)
            if len(pool) < per_class:
                sys.exit("category %s has only %d %s cases" % (cat, len(pool), "real" if real else "false-positive"))
            chosen.extend(pool[:per_class])
    return chosen


def new_name(name, seed):
    return "Endpoint" + hashlib.sha256(("%s:%s" % (seed, name)).encode("utf-8")).hexdigest()[:8]


def deidentify_text(text, seed):
    """Remove what identifies the benchmark from one source file; library imports are left alone."""
    head = HEADER.match(text)
    if head and re.search(r"owasp|benchmark", head.group(0), re.I):
        text = text[head.end():]
    text = SERVLET_PATH.sub(lambda m: '"/api/%s%s"' % (new_name(m.group(1), seed).lower(), m.group(2) or ""), text)
    text = CASE_MESSAGE.sub('"Request failed"', text)
    for old, new in (("TestCase", "Handler"), ("Test Cases", "Handlers"), ("Test Case", "Handler"),
                     ("test cases", "handlers"), ("test case", "handler")):
        text = text.replace(old, new)
    text = TEST_NAME.sub(lambda m: new_name(m.group(0), seed), text)
    text = text.replace("org.owasp.benchmark.testcode", "org.example.portal.web").replace("org.owasp.benchmark", "org.example.portal")
    text = re.sub(r"(?<![a-z])Thing", "Processor", text).replace("createThing", "createProcessor")
    text = URL.sub("https://example.org/", text)
    for old, new in (("OWASP Benchmark", "Portal"), ("Benchmark", "Portal"), ("benchmark", "portal"), ("BENCHMARK", "PORTAL")):
        text = text.replace(old, new)
    return re.sub(r"(?<!org\.)OWASP ?|(?<!org\.)owasp(?!\.esapi)", "", text)


def deidentify(out, app, seed):
    """Rewrite app/ in place and return {new relative path: original relative path}."""
    mapping = {}
    old_root = os.path.join(app, PKG)
    for dirpath, _, files in os.walk(old_root):
        for fn in sorted(files):
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, old_root)
            parts = rel.split(os.sep)
            if parts[0] == "testcode":
                parts[0] = "web"
            stem, ext = os.path.splitext(parts[-1])
            if TEST_NAME.fullmatch(stem):
                parts[-1] = new_name(stem, seed) + ext
            elif stem.startswith("Thing"):
                parts[-1] = "Processor" + stem[len("Thing"):] + ext
            dst = os.path.join(app, NEW_PKG, *parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(deidentify_text(text, seed))
            mapping[os.path.relpath(dst, app).replace(os.sep, "/")] = os.path.relpath(src, app).replace(os.sep, "/")
    shutil.rmtree(os.path.join(app, "src", "main", "java", "org", "owasp"))
    if os.path.exists(os.path.join(app, "LICENSE")):
        shutil.move(os.path.join(app, "LICENSE"), os.path.join(out, "LICENSE"))
    with open(os.path.join(out, "NOTICE"), "w", encoding="utf-8") as fh:
        fh.write(NOTICE)
    return mapping


def line_count(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", required=True, help="local checkout of OWASP-Benchmark/BenchmarkJava")
    p.add_argument("--out", required=True, help="new directory outside this repository")
    p.add_argument("--per-class", type=int, default=2, help="real and false-positive cases per category")
    p.add_argument("--seed", default="flight-check-1")
    p.add_argument("--deidentify", action="store_true", help="remove what identifies the benchmark from the local copy")
    args = p.parse_args(argv)

    bench = os.path.abspath(args.benchmark)
    out = os.path.abspath(args.out)
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if out == repo or out.startswith(repo + os.sep):
        sys.exit("refusing: --out must be outside this repository (the benchmark is GPL-2.0 and must not be vendored here)")
    if os.path.exists(out):
        sys.exit("refusing: %s already exists" % out)

    csv_name, rows = load_expected(bench)
    chosen = select(rows, args.per_class, args.seed)
    app = os.path.join(out, "app")
    shutil.copytree(os.path.join(bench, PKG, "helpers"), os.path.join(app, PKG, "helpers"))
    os.makedirs(os.path.join(app, PKG, "testcode"))
    for lic in ("LICENSE",):
        if os.path.exists(os.path.join(bench, lic)):
            shutil.copy(os.path.join(bench, lic), os.path.join(app, lic))

    for r in chosen:
        rel = os.path.join(PKG, "testcode", r["name"] + ".java")
        shutil.copy(os.path.join(bench, rel), os.path.join(app, rel))
    mapping = deidentify(out, app, args.seed) if args.deidentify else None
    original_to_new = {v: k for k, v in (mapping or {}).items()}

    issues, decoys = [], []
    for r in chosen:
        rel = os.path.join(PKG, "testcode", r["name"] + ".java").replace(os.sep, "/")
        rel = original_to_new.get(rel, rel)
        loc = [{"path": rel, "start_line": 1, "end_line": line_count(os.path.join(app, rel))}]
        sev, tol, tier, domains = CATEGORIES[r["category"]]
        if r["real"]:
            issues.append({"id": "OB-%s" % r["name"], "domains": domains, "severity": sev, "severity_tolerance": tol,
                           "tier": tier, "locations": loc, "standards": ["CWE-%s" % r["cwe"]],
                           "description": "OWASP Benchmark %s case (real vulnerability, CWE-%s)." % (r["category"], r["cwe"])})
        else:
            decoys.append({"id": "OB-%s" % r["name"], "domains": domains, "locations": loc,
                           "description": "OWASP Benchmark %s case labeled a false positive." % r["category"]})

    manifest = {
        "manifest_version": "1.0",
        "fixture": {"name": "owasp-benchmark-subset", "revision": "%s per-class=%d seed=%s" % (csv_name, args.per_class, args.seed),
                    "author": "OWASP Benchmark project (subset selected by tools/blindtest/owasp_subset.py)", "held_out": False},
        "expected_gate": "NOT READY — REMEDIATION REQUIRED",
        "issues": issues,
        "secure_controls": decoys,
        "expected_not_applicable_domains": ["payments", "ai-llm"],
    }
    if mapping is not None:
        manifest["fixture"]["revision"] += " deidentified"
    os.makedirs(os.path.join(out, "ground-truth"))
    with open(os.path.join(out, "ground-truth", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    if mapping is not None:
        with open(os.path.join(out, "ground-truth", "name-map.json"), "w", encoding="utf-8") as fh:
            json.dump(mapping, fh, indent=1, sort_keys=True)
    print(json.dumps({"app": app, "manifest": os.path.join(out, "ground-truth", "manifest.json"),
                      "real_cases": len(issues), "false_positive_cases": len(decoys)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

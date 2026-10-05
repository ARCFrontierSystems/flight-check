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
Development-only; never shipped in the plugin.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys

PKG = os.path.join("src", "main", "java", "org", "owasp", "benchmark")

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


def line_count(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", required=True, help="local checkout of OWASP-Benchmark/BenchmarkJava")
    p.add_argument("--out", required=True, help="new directory outside this repository")
    p.add_argument("--per-class", type=int, default=2, help="real and false-positive cases per category")
    p.add_argument("--seed", default="flight-check-1")
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

    issues, decoys = [], []
    for r in chosen:
        rel = os.path.join(PKG, "testcode", r["name"] + ".java")
        shutil.copy(os.path.join(bench, rel), os.path.join(app, rel))
        loc = [{"path": rel.replace(os.sep, "/"), "start_line": 1, "end_line": line_count(os.path.join(app, rel))}]
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
    os.makedirs(os.path.join(out, "ground-truth"))
    with open(os.path.join(out, "ground-truth", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(json.dumps({"app": app, "manifest": os.path.join(out, "ground-truth", "manifest.json"),
                      "real_cases": len(issues), "false_positive_cases": len(decoys)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

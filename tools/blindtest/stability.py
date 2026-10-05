#!/usr/bin/env python3
"""Summarize several blind-test scores of the same fixture: run-to-run variation.

Audits are not deterministic, so a single run says little. This combines the score.json files
that tools/blindtest/score.py wrote for repeated runs of one fixture and reports the spread of
each metric, how often each seeded issue was detected, and how much the detected sets overlap.

Usage:
    python3 tools/blindtest/stability.py results/run1/score.json results/run2/score.json ... \\
        [--json stability.json] [--markdown stability.md]

Development-only; never shipped in the plugin.
"""

import argparse
import itertools
import json
import sys

METRICS = ("recall", "must_recall", "recall_location_only", "must_recall_location_only", "precision_lower_bound",
           "precision_excluding_unlisted", "decoy_false_positive_rate", "severity_within_tolerance")


def _spread(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"mean": round(sum(values) / len(values), 3), "min": min(values), "max": max(values)}


def summarize(scores):
    if not scores:
        raise ValueError("no scores given")
    fixtures = {json.dumps(s.get("fixture", {}), sort_keys=True) for s in scores}
    if len(fixtures) != 1:
        raise ValueError("scores come from different fixtures")
    n = len(scores)
    detected_sets = [{r["issue"] for r in s["per_issue"] if r["detected"]} for s in scores]
    issues = [(r["issue"], r["tier"]) for r in scores[0]["per_issue"]]
    freq = {iid: sum(iid in d for d in detected_sets) for iid, _ in issues}
    pairs = list(itertools.combinations(detected_sets, 2))
    jaccard = [len(a & b) / float(len(a | b)) if (a | b) else 1.0 for a, b in pairs]
    by_domain = {}
    for s in scores:
        for dom, slot in s["by_domain"].items():
            if slot["issues"]:
                by_domain.setdefault(dom, []).append(slot["detected"] / float(slot["issues"]))
    return {
        "fixture": scores[0].get("fixture", {}),
        "runs": n,
        "run_ids": [s.get("run_id") for s in scores],
        "gates": [s.get("gate") for s in scores],
        "expected_gate": scores[0].get("expected_gate"),
        "metrics": {m: _spread([s.get(m) for s in scores]) for m in METRICS},
        "findings_per_run": _spread([s["counts"]["findings"] for s in scores]),
        "issues": len(issues),
        "detected_in_every_run": sum(1 for v in freq.values() if v == n),
        "detected_in_some_runs": sum(1 for v in freq.values() if 0 < v < n),
        "never_detected": sum(1 for v in freq.values() if v == 0),
        "must_never_detected": sum(1 for iid, tier in issues if tier == "must" and freq[iid] == 0),
        "detected_by_any_run_recall": round(sum(1 for v in freq.values() if v) / float(len(issues)), 3) if issues else None,
        "mean_pairwise_jaccard_of_detected_sets": round(sum(jaccard) / len(jaccard), 3) if jaccard else None,
        "fabricated_evidence_findings_total": sum(len(s.get("fabricated_evidence_findings") or []) for s in scores),
        "by_domain_recall": {d: _spread(v) for d, v in sorted(by_domain.items())},
        "detection_frequency": {iid: freq[iid] for iid, _ in issues},
    }


def to_markdown(r):
    lines = ["# Stability: %s (%d runs)" % (r["fixture"].get("name", "?"), r["runs"]), ""]
    lines.append("Gates: %s (expected: %s)" % (", ".join(str(g) for g in r["gates"]), r["expected_gate"]))
    lines.append("")
    lines.append("| Metric | Mean | Min | Max |")
    lines.append("|---|---|---|---|")
    for name, s in list(r["metrics"].items()) + [("findings_per_run", r["findings_per_run"])]:
        if s:
            lines.append("| %s | %s | %s | %s |" % (name, s["mean"], s["min"], s["max"]))
    lines.append("")
    lines.append("- Seeded issues: %d. Detected in every run: %d. In some runs: %d. Never: %d (must-tier never: %d)."
                 % (r["issues"], r["detected_in_every_run"], r["detected_in_some_runs"], r["never_detected"], r["must_never_detected"]))
    lines.append("- Recall if any run's finding counted: %s. Mean pairwise overlap (Jaccard) of detected sets: %s."
                 % (r["detected_by_any_run_recall"], r["mean_pairwise_jaccard_of_detected_sets"]))
    lines.append("- Findings whose evidence failed the mechanical check, all runs: %d." % r["fabricated_evidence_findings_total"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("scores", nargs="+")
    p.add_argument("--json")
    p.add_argument("--markdown")
    args = p.parse_args(argv)
    scores = []
    for path in args.scores:
        with open(path, encoding="utf-8") as fh:
            scores.append(json.load(fh))
    result = summarize(scores)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
    md = to_markdown(result)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

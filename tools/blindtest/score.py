#!/usr/bin/env python3
"""Score a finalized Fairtide audit against a hidden ground-truth manifest.

Usage:
    python3 tools/blindtest/score.py --final RUN_DIR/audit.final.json --manifest manifest.json [--json out.json] [--tolerance 5]

Matching (one-to-one, greedy by overlap):
  A finding matches a ground-truth issue when they share a domain and at least one of the
  finding's located evidence items overlaps one of the issue's locations or alternate
  locations (same path, line ranges within +/- tolerance lines).
Unmatched findings:
  - overlapping a declared secure control (decoy)  -> decoy false positive
  - otherwise                                       -> unlisted; needs human adjudication
                                                       (a real but unseeded issue is not a false positive)

Paths in the manifest are relative to the audited application root.
This tool is development-only and is never shipped in the plugin.
"""

import argparse
import json
import math
import os
import sys

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _norm(path):
    path = (path or "").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def overlap(ev, loc, tol):
    if _norm(ev.get("path")) != _norm(loc["path"]):
        return 0
    a1, a2 = ev["start_line"], ev["end_line"]
    b1, b2 = loc["start_line"] - tol, loc["end_line"] + tol
    return max(0, min(a2, b2) - max(a1, b1) + 1)


def located(finding):
    return [ev for ev in finding.get("evidence", []) if ev.get("path") and ev.get("start_line")]


def best_overlap(finding, locations, tol):
    return max([overlap(ev, loc, tol) for ev in located(finding) for loc in locations] or [0])


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def validate_manifest(m):
    problems = []
    ids = set()
    for key in ("issues", "secure_controls"):
        for item in m.get(key, []):
            if item["id"] in ids:
                problems.append("duplicate id %s" % item["id"])
            ids.add(item["id"])
            if not item.get("locations"):
                problems.append("%s has no locations" % item["id"])
            if not item.get("domains"):
                problems.append("%s has no domains" % item["id"])
    for issue in m.get("issues", []):
        if issue.get("severity") not in SEVERITIES:
            problems.append("%s has invalid severity" % issue["id"])
        if issue.get("tier", "must") not in ("must", "stretch"):
            problems.append("%s has invalid tier" % issue["id"])
    return problems


def score(final, manifest, tol=5):
    findings = final["findings"]
    issues = manifest.get("issues", [])
    decoys = manifest.get("secure_controls", [])

    candidates = []
    for fi, f in enumerate(findings):
        for ii, issue in enumerate(issues):
            if f["domain"] not in issue["domains"]:
                continue
            ov = best_overlap(f, issue["locations"] + issue.get("alternate_locations", []), tol)
            if ov > 0:
                candidates.append((ov, fi, ii))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_f, used_i, matches = set(), set(), {}
    for ov, fi, ii in candidates:
        if fi in used_f or ii in used_i:
            continue
        used_f.add(fi)
        used_i.add(ii)
        matches[ii] = fi

    decoy_fp, unlisted = [], []
    for fi, f in enumerate(findings):
        if fi in used_f:
            continue
        hit = [d["id"] for d in decoys if f["domain"] in d["domains"] and best_overlap(f, d["locations"], tol) > 0]
        (decoy_fp if hit else unlisted).append({"finding": f["id"], "title": f["title"], "domain": f["domain"],
                                                "severity": f["severity"], "decoys": hit})

    per_issue = []
    sev_exact = sev_within = 0
    legal_total = legal_class_ok = legal_questions_ok = 0
    for ii, issue in enumerate(issues):
        row = {"issue": issue["id"], "tier": issue.get("tier", "must"), "domains": issue["domains"], "severity": issue["severity"]}
        if ii in matches:
            f = findings[matches[ii]]
            diff = abs(SEVERITIES.index(f["severity"]) - SEVERITIES.index(issue["severity"]))
            sev_exact += diff == 0
            sev_within += diff <= issue.get("severity_tolerance", 1)
            row.update({"detected": True, "finding": f["id"], "found_severity": f["severity"], "confidence": f["effective_confidence"],
                        "evidence_check": f.get("evidence_check"), "severity_delta": diff})
        else:
            row["detected"] = False
        if issue.get("legal"):
            legal_total += 1
            if ii in matches:
                f = findings[matches[ii]]
                got = (f.get("legal") or {}).get("classification")
                expected = issue["legal"].get("classification")
                accepted = issue["legal"].get("accepted_classifications") or ([expected] if expected else [])
                row["legal_classification"] = got
                if got in accepted:
                    legal_class_ok += 1
                if (f.get("legal") or {}).get("questions"):
                    legal_questions_ok += 1
        per_issue.append(row)

    detected = [r for r in per_issue if r["detected"]]
    must = [r for r in per_issue if r["tier"] == "must"]
    must_hit = [r for r in must if r["detected"]]
    by_domain = {}
    for r in per_issue:
        for d in r["domains"]:
            slot = by_domain.setdefault(d, {"issues": 0, "detected": 0})
            slot["issues"] += 1
            slot["detected"] += r["detected"]
    tp = len(detected)
    fp_known = len(decoy_fp)
    decoy_n = len(decoys)
    coverage = {c["domain"]: c["status"] for c in final["coverage"]}
    na_expected = manifest.get("expected_not_applicable_domains", [])
    na_wrong = [d for d in na_expected if coverage.get(d) != "NOT_APPLICABLE"]
    na_findings = [f["id"] for f in findings if f["domain"] in na_expected]
    fabricated = [f["id"] for f in findings if f.get("evidence_check") == "FAILED"]
    n_findings = len(findings)
    result = {
        "fixture": manifest.get("fixture", {}),
        "run_id": final["run"]["run_id"],
        "gate": final["gate"]["decision"],
        "expected_gate": manifest.get("expected_gate"),
        "counts": {"issues": len(issues), "must_issues": len(must), "decoys": decoy_n, "findings": n_findings,
                   "true_positives": tp, "decoy_false_positives": fp_known, "unlisted_findings": len(unlisted)},
        "recall": round(tp / len(issues), 3) if issues else None,
        "recall_ci95": wilson(tp, len(issues)),
        "must_recall": round(len(must_hit) / len(must), 3) if must else None,
        "precision_lower_bound": round(tp / (tp + fp_known + len(unlisted)), 3) if (tp + fp_known + len(unlisted)) else None,
        "precision_excluding_unlisted": round(tp / (tp + fp_known), 3) if (tp + fp_known) else None,
        "decoy_false_positive_rate": round(fp_known / decoy_n, 3) if decoy_n else None,
        "youden_j_on_seeded_sites": round(tp / len(issues) - fp_known / decoy_n, 3) if issues and decoy_n else None,
        "severity_exact": round(sev_exact / tp, 3) if tp else None,
        "severity_within_tolerance": round(sev_within / tp, 3) if tp else None,
        "fabricated_evidence_findings": fabricated,
        "fabricated_evidence_upper_bound_95": round(3.0 / n_findings, 3) if n_findings and not fabricated else None,
        "legal": {"issues": legal_total, "classification_correct": legal_class_ok, "with_questions": legal_questions_ok},
        "not_applicable_errors": {"expected_na_not_marked": na_wrong, "findings_in_na_domains": na_findings},
        "by_domain": by_domain,
        "per_issue": per_issue,
        "decoy_false_positives": decoy_fp,
        "unlisted_for_adjudication": unlisted,
        "confidence_table": confidence_table(findings, matches, decoy_fp),
    }
    return result


def confidence_table(findings, matches, decoy_fp):
    tp_ids = {findings[fi]["id"] for fi in matches.values()}
    fp_ids = {d["finding"] for d in decoy_fp}
    table = {}
    for f in findings:
        row = table.setdefault(f["effective_confidence"], {"true_positive": 0, "decoy_false_positive": 0, "unlisted": 0})
        if f["id"] in tp_ids:
            row["true_positive"] += 1
        elif f["id"] in fp_ids:
            row["decoy_false_positive"] += 1
        else:
            row["unlisted"] += 1
    return table


def to_markdown(r):
    lines = ["# Fairtide blind-test score", "",
             "- Fixture: %s" % r["fixture"].get("name", "?"),
             "- Run: %s; gate: %s%s" % (r["run_id"], r["gate"], (" (expected %s)" % r["expected_gate"]) if r.get("expected_gate") else ""),
             "- Recall: %s (95%% CI %s); must-tier recall: %s" % (r["recall"], r["recall_ci95"], r["must_recall"]),
             "- Precision (unlisted counted as false): %s; excluding unlisted: %s" % (r["precision_lower_bound"], r["precision_excluding_unlisted"]),
             "- Decoy false-positive rate: %s; Youden J on seeded sites: %s" % (r["decoy_false_positive_rate"], r["youden_j_on_seeded_sites"]),
             "- Severity exact: %s; within tolerance: %s" % (r["severity_exact"], r["severity_within_tolerance"]),
             "- Findings with fabricated or mismatched evidence: %s" % (", ".join(r["fabricated_evidence_findings"]) or "none"),
             "- Legal issues: %(issues)d; classification correct: %(classification_correct)d; with counsel questions: %(with_questions)d" % r["legal"],
             "- Not-applicable errors: %s" % r["not_applicable_errors"], "",
             "| Issue | Tier | Domains | Expected severity | Detected | Finding | Found severity |", "|---|---|---|---|---|---|---|"]
    for row in r["per_issue"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (row["issue"], row["tier"], ", ".join(row["domains"]), row["severity"],
                                                          "yes" if row["detected"] else "NO", row.get("finding", ""), row.get("found_severity", "")))
    if r["decoy_false_positives"]:
        lines += ["", "## Decoy false positives", ""] + ["- %(finding)s %(title)s (decoys %(decoys)s)" % d for d in r["decoy_false_positives"]]
    if r["unlisted_for_adjudication"]:
        lines += ["", "## Unlisted findings (adjudicate: real unseeded issue or false positive?)", ""] + \
                 ["- %(finding)s [%(severity)s/%(domain)s] %(title)s" % d for d in r["unlisted_for_adjudication"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--final", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--tolerance", type=int, default=5)
    p.add_argument("--json")
    p.add_argument("--markdown")
    args = p.parse_args(argv)
    manifest = load(args.manifest)
    problems = validate_manifest(manifest)
    if problems:
        print("manifest problems:\n  " + "\n  ".join(problems))
        return 2
    result = score(load(args.final), manifest, args.tolerance)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    md = to_markdown(result)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

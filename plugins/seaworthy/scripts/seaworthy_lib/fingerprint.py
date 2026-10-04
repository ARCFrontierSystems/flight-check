"""Stable finding fingerprints.

A fingerprint identifies "the same problem" across audits even when line numbers
shift: it hashes the rule, the primary file path, and an anchor taken from the
first meaningful quoted line. When the quoted code changes, a secondary match key
(rule + path) lets the ledger keep the same finding ID if the match is unambiguous.
"""

import hashlib
import re

from . import constants, textsafety

_WS = re.compile(r"\s+")
_GAP = re.compile(r"\[REDACTED(?::[^\]\n]{0,40})?\]")


def normalize_path(path):
    path = (path or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def primary_path(finding):
    for ev in finding.get("evidence", []):
        if ev.get("kind") in constants.LOCATED_EVIDENCE_KINDS and ev.get("path"):
            return normalize_path(ev["path"])
    comps = finding.get("affected_components") or [""]
    return normalize_path(comps[0]).lower()


def anchor(finding):
    for ev in finding.get("evidence", []):
        if ev.get("kind") not in constants.LOCATED_EVIDENCE_KINDS:
            continue
        for line in (ev.get("quote") or "").splitlines():
            text = _WS.sub(" ", _GAP.sub(" ", textsafety.sanitize(line))).strip().lower()
            if len(re.sub(r"\W", "", text)) >= 3:
                return text[:200]
    return ""


def match_key(finding):
    return "%s|%s" % (finding["rule"], primary_path(finding))


def fingerprint(finding, occurrence=1):
    material = "v1|%s|%s|%s" % (finding["rule"], primary_path(finding), anchor(finding))
    if occurrence > 1:
        material += "#%d" % occurrence
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def assign(findings):
    """Return fingerprints for findings in order, disambiguating exact duplicates within one run."""
    counts = {}
    out = []
    for f in findings:
        base = fingerprint(f)
        counts[base] = counts.get(base, 0) + 1
        out.append(base if counts[base] == 1 else fingerprint(f, counts[base]))
    return out

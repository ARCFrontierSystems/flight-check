"""SARIF 2.1.0 export of the findings that count against release, for GitHub code scanning and other SARIF readers.

Built from the committed ledger only, so a CI job can produce it without Claude. Findings the owner accepted or
dismissed, and findings no longer observed, are left out; a finding whose location is not a file in the checkout is
counted but not exported, because code scanning needs a file to point at.
"""

import os

from . import VERSION, ledger

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/ARCFrontierSystems/flight-check"
EXPORTED = ("OPEN", "REGRESSION", "UNDER_REVIEW", "REMEDIATION_PLANNED", "REMEDIATED")
LEVEL = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note", "INFORMATIONAL": "note"}
# GitHub ranks security alerts by this rule property (critical >= 9.0, high >= 7.0, medium >= 4.0, low > 0).
SECURITY_SEVERITY = {"CRITICAL": "9.5", "HIGH": "8.0", "MEDIUM": "5.5", "LOW": "3.0", "INFORMATIONAL": "0.1"}
SECURITY_DOMAINS = {"application-security", "authentication", "authorization", "data-protection", "privacy", "payments",
                    "supply-chain", "infrastructure", "ai-llm"}
_SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"]
_SAFE = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/")


def _uri(path):
    """Percent-encode a relative path as a URI reference."""
    return "".join(chr(b) if b in _SAFE else "%%%02X" % b for b in path.encode("utf-8"))


def build(st, root):
    """(sarif document, {"exported": n, "without_file": [ids]}) for the ledger's findings that count against release."""
    prefix = (st.project or {}).get("identity", {}).get("root", ".") if st.project else "."
    rules, results, without_file = {}, [], []
    entries = sorted(((st.ledger or {}).get("entries") or {}).values(), key=lambda e: e["id"])
    for e in entries:
        if e["status"] not in EXPORTED:
            continue
        path = ledger._entry_path(e)
        full = os.path.join(root, *path.split("/")) if path else None
        if not path or path.startswith("/") or ".." in path.split("/") or not os.path.isfile(full) or os.path.islink(full):
            without_file.append(e["id"])
            continue
        sev = e["severity"]
        rule = rules.setdefault(e["rule"], {"id": e["rule"], "name": e["rule"], "shortDescription": {"text": e["title"][:200]},
                                            "helpUri": INFORMATION_URI, "properties": {"tags": [e["domain"]], "severity": sev}})
        if _SEV_ORDER.index(sev) < _SEV_ORDER.index(rule["properties"]["severity"]):
            rule["properties"]["severity"] = sev
        results.append({
            "ruleId": e["rule"],
            "level": LEVEL[sev],
            "message": {"text": "%s: %s (%s, %s). See Flight Check's report for the evidence, impact, and remediation."
                                % (e["id"], e["title"], sev, e["status"])},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": _uri(path if prefix in (".", "") else prefix.rstrip("/") + "/" + path)},
                "region": {"startLine": e.get("start_line") or 1}}}],
            "partialFingerprints": {"flightCheckFingerprint/v1": e["fingerprint"]},
            "properties": {"flight_check_id": e["id"], "severity": sev, "status": e["status"], "domain": e["domain"],
                           "confidence": e.get("confidence", "unknown")},
        })
    for rule in rules.values():
        props = rule["properties"]
        if any(t in SECURITY_DOMAINS for t in props["tags"]):
            props["tags"] = props["tags"] + ["security"]
            props["security-severity"] = SECURITY_SEVERITY[props["severity"]]
    doc = {"$schema": SCHEMA, "version": "2.1.0", "runs": [{
        "tool": {"driver": {"name": "Flight Check", "version": VERSION, "informationUri": INFORMATION_URI,
                            "rules": sorted(rules.values(), key=lambda r: r["id"])}},
        "results": results}]}
    return doc, {"exported": len(results), "without_file": without_file}

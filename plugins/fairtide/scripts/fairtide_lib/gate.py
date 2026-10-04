"""Deterministic ship gate.

Precedence (first match wins; every applicable condition is still reported):
  1. BLOCKED - CRITICAL RISK         an open CRITICAL finding with CONFIRMED or LIKELY effective confidence
  2. BLOCKED - INSUFFICIENT EVIDENCE an unaccepted release-critical control is UNVERIFIED; a domain was not
                                     assessed; an open CRITICAL finding is POTENTIAL/UNVERIFIED; an open HIGH
                                     finding is UNVERIFIED; or a re-audit contradicted a recorded remediation
  3. NOT READY - REMEDIATION REQUIRED an open finding is release-blocking
  4. READY WITH ACCEPTED RISKS       valid user-accepted risks are in effect
  5. READY FOR RELEASE

"Effective confidence" is UNVERIFIED when mechanical evidence checking failed, so a
finding whose quotes cannot be found in the cited files cannot drive a CRITICAL
block, but it does prevent a READY decision until a human resolves it.
"""

from . import constants
from .ledger import acceptance_problems

SCOPE_STATEMENT = (
    "This decision reflects only the evidence Fairtide could examine in the audited scope at this "
    "revision. It is not a certification, an audit opinion, legal advice, or a guarantee that no "
    "problems exist."
)


def effective_confidence(finding):
    if finding.get("evidence_check") == "FAILED":
        return "UNVERIFIED"
    return finding["confidence"]


def release_blocking_effective(finding):
    sev = finding["severity"]
    conf = effective_confidence(finding)
    if sev == "CRITICAL":
        return True
    if sev == "HIGH" and conf in ("CONFIRMED", "LIKELY"):
        return True
    return bool(finding.get("release_blocking")) and sev != "INFORMATIONAL"


def decide(audit, ledger, today, lifecycle=None):
    lifecycle = lifecycle or {}
    open_findings, accepted_findings = [], []
    for f in audit["findings"]:
        if f.get("status") == "ACCEPTED_RISK" and not acceptance_problems(f.get("accepted_risk"), today):
            accepted_findings.append(f)
        else:
            open_findings.append(f)

    critical = [f["id"] for f in open_findings
                if f["severity"] == "CRITICAL" and effective_confidence(f) in ("CONFIRMED", "LIKELY")]

    insufficient = []
    acceptances = (ledger or {}).get("control_acceptances", {})
    accepted_controls = []
    for c in audit["controls"]:
        if not c["release_critical"] or c["state"] != "UNVERIFIED":
            continue
        acc = acceptances.get(c["id"])
        if acc and not acceptance_problems(acc, today):
            accepted_controls.append(c["id"])
            continue
        insufficient.append("Release-critical control %s is UNVERIFIED: %s" % (c["id"], c.get("missing_evidence") or "no evidence"))
    for cov in audit["coverage"]:
        if cov["status"] == "NOT_ASSESSED":
            insufficient.append("Domain %s was not assessed: %s" % (cov["domain"], cov["rationale"]))
    for f in open_findings:
        conf = effective_confidence(f)
        if f["severity"] == "CRITICAL" and conf in ("POTENTIAL", "UNVERIFIED"):
            insufficient.append("%s is a CRITICAL finding with %s confidence; it must be confirmed or ruled out" % (f["id"], conf))
        elif f["severity"] == "HIGH" and conf == "UNVERIFIED":
            insufficient.append("%s is a HIGH finding with UNVERIFIED confidence; it must be confirmed or ruled out" % f["id"])
    for fid in lifecycle.get("inconsistent_remediation", []):
        insufficient.append("%s: the re-audit says the recorded fix is not effective but the finding was not re-observed; resolve manually" % fid)

    blocking = [f["id"] for f in open_findings if release_blocking_effective(f)]

    reasons = []
    if critical:
        decision = constants.GATE_CRITICAL
        reasons.append("Open CRITICAL finding(s) with confirmed or likely confidence: %s." % ", ".join(critical))
    elif insufficient:
        decision = constants.GATE_INSUFFICIENT
    elif blocking:
        decision = constants.GATE_NOT_READY
        reasons.append("Open release-blocking finding(s): %s." % ", ".join(blocking))
    elif accepted_findings or accepted_controls:
        decision = constants.GATE_READY_ACCEPTED
    else:
        decision = constants.GATE_READY

    if insufficient:
        if decision == constants.GATE_INSUFFICIENT:
            reasons.append("Evidence is insufficient to support a release decision:")
        else:
            reasons.append("Also, evidence is insufficient in these areas:")
        reasons.extend("- " + r for r in insufficient)
    if blocking and decision != constants.GATE_NOT_READY:
        reasons.append("Release-blocking findings that also need remediation: %s." % ", ".join(blocking))
    if accepted_findings or accepted_controls:
        refs = [f["id"] for f in accepted_findings] + accepted_controls
        reasons.append("User-accepted risks in effect: %s." % ", ".join(refs))
    if decision == constants.GATE_READY:
        reasons.append("No open release-blocking findings, no unverified release-critical controls, and every domain was assessed or shown not applicable.")

    return {
        "decision": decision,
        "reasons": reasons,
        "critical_findings": critical,
        "blocking_findings": blocking,
        "insufficient_evidence": insufficient,
        "accepted_findings": [f["id"] for f in accepted_findings],
        "accepted_controls": accepted_controls,
        "scope_statement": SCOPE_STATEMENT,
        "exit_code": constants.GATE_EXIT_CODES[decision],
    }

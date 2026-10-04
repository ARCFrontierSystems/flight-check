"""Deterministic ship gate.

Precedence (first match wins; every applicable condition is still reported):
  1. BLOCKED - CRITICAL RISK         an open CRITICAL finding with CONFIRMED or LIKELY effective confidence
  2. BLOCKED - INSUFFICIENT EVIDENCE an unaccepted release-critical control is UNVERIFIED; a domain was not
                                     assessed; an open CRITICAL finding is POTENTIAL/UNVERIFIED; an open HIGH
                                     finding is UNVERIFIED; or a re-audit contradicted a recorded remediation
  3. NOT READY - REMEDIATION REQUIRED an open finding is release-blocking
  4. READY WITH ACCEPTED RISKS       valid user-accepted risks are in effect
  5. READY FOR RELEASE

Accepted risks are counted only when they were recorded before the run started, so nothing
recorded while an audit is in progress can change that audit's decision. Accepted CRITICAL
findings never lift a block: they are treated exactly like open CRITICAL findings.

"Effective confidence" is UNVERIFIED when mechanical evidence checking failed, so a
finding whose quotes cannot be found in the cited files cannot drive a CRITICAL
block, but it does prevent a READY decision until a human resolves it.
"""

import datetime
import re

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


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def run_started_at(run):
    """Earliest credible start time of a run, as YYYY-MM-DDTHH:MM:SSZ, or None.

    The run ID is created by init-run from the clock; run.json's started_at is copied from
    init-run's output. Taking the earlier of the two keeps a later-looking run.json from
    widening the window in which acceptances count.
    """
    candidates = []
    m = re.match(r"^(\d{8}T\d{6}Z)", (run or {}).get("run_id", ""))
    if m:
        dt = datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ")
        candidates.append(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    started = (run or {}).get("started_at")
    if isinstance(started, str) and _ISO.match(started):
        candidates.append(started)
    return min(candidates) if candidates else None


def _acceptance_timing_problem(acc, started):
    if started is None:
        return None
    recorded = (acc or {}).get("recorded_at")
    if not isinstance(recorded, str) or not _ISO.match(recorded):
        return "has no valid recording time"
    if recorded >= started:
        return "was recorded at %s, after this run started (%s); it counts from the next run" % (recorded, started)
    return None


def decide(audit, ledger, today, lifecycle=None):
    lifecycle = lifecycle or {}
    started = run_started_at(audit.get("run"))
    open_findings, accepted_findings, accepted_critical, not_counted = [], [], [], []
    for f in audit["findings"]:
        if f.get("status") == "ACCEPTED_RISK" and not acceptance_problems(f.get("accepted_risk"), today):
            timing = _acceptance_timing_problem(f.get("accepted_risk"), started)
            if timing:
                not_counted.append("Acceptance of %s %s." % (f["id"], timing))
                open_findings.append(f)
            elif f["severity"] == "CRITICAL":
                accepted_critical.append(f["id"])
                open_findings.append(f)
            else:
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
            timing = _acceptance_timing_problem(acc, started)
            if not timing:
                accepted_controls.append(c["id"])
                continue
            not_counted.append("Acceptance of control %s %s." % (c["id"], timing))
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
    if accepted_critical:
        reasons.append("Accepted CRITICAL finding(s) still block release: %s. Accepting a CRITICAL risk records the decision but never lifts the block." % ", ".join(accepted_critical))
    for note in not_counted:
        reasons.append(note)
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
        "accepted_critical_findings": accepted_critical,
        "acceptances_not_counted": not_counted,
        "run_started_at": started,
        "scope_statement": SCOPE_STATEMENT,
        "exit_code": constants.GATE_EXIT_CODES[decision],
    }

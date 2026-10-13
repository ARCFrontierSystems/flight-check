"""Deterministic ship gate.

Precedence (first match wins; every applicable condition is still reported):
  1. BLOCKED - CRITICAL RISK         an open CRITICAL finding with CONFIRMED or LIKELY effective confidence
  2. BLOCKED - INSUFFICIENT EVIDENCE an unaccepted release-critical control is UNVERIFIED; a domain was not
                                     assessed; an open CRITICAL finding is POTENTIAL/UNVERIFIED; an open HIGH
                                     finding is UNVERIFIED; or a re-audit contradicted a recorded remediation
  3. NOT READY - REMEDIATION REQUIRED an open finding is release-blocking
  4. READY WITH ACCEPTED RISKS       valid user-accepted risks are in effect
  5. READY FOR RELEASE

Findings carried forward by an incremental run (domains that no change affects) count exactly like findings
observed in this run; a carried-forward release-critical control whose cited files changed is UNVERIFIED.

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
    "This decision reflects only the evidence Flight Check could examine in the audited scope at this "
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


_OPEN_RISK = ("OPEN", "REOPENED", "UNDER_REVIEW", "MITIGATION_PLANNED")


def _register_acceptance_problem(acc, today, started):
    acc = acc or {}
    if today is not None:
        if acc.get("date") and acc["date"] > today.isoformat():
            return "is dated in the future"
        if acc.get("review_date") and acc["review_date"] < today.isoformat():
            return "is past its review date %s" % acc["review_date"]
    if started and acc.get("recorded_at") and acc["recorded_at"] >= started:
        return "was recorded at %s, after this run started (%s); it counts from the next run" % (acc["recorded_at"], started)
    return None


def _register_inputs(registers, gate_findings=None, today=None, started=None):
    """Gate inputs from the project's records (1.5): (kind, id, effect, explanation). gate_findings maps the IDs of
    this run's gate findings to their severity: a risk counts through its findings only when one of them is in the
    gate at least as severe as the risk."""
    out = []
    if not registers:
        return out
    gate_findings = gate_findings or {}
    for rec in registers.get("requirements", []):
        if rec["criticality"] == "release-critical" and rec["status"] in ("ACCEPTED", "IMPLEMENTED", "VERIFIED"):
            current = rec["status"] == "VERIFIED" and (rec.get("verification") or {}).get("state") == "current"
            if not current:
                how = ("link it to controls that an audit can verify, or change its criticality"
                       if not rec.get("controls") else "its linked controls were not all verified with checked evidence")
                out.append(("requirement", rec["id"], "insufficient",
                            "Release-critical requirement %s (%s) is not verified by evidence: %s." % (rec["id"], rec["title"], how)))
    for item in registers.get("legal", []):
        if item["status"] == "ACTION_REQUIRED":
            out.append(("legal", item["id"], "blocking",
                        "Legal review item %s (%s) needs action before release, as recorded by the owner." % (item["id"], item["title"])))
    for risk in registers.get("risks", []):
        status = risk["status"]
        if status == "ACCEPTED":
            problem = _register_acceptance_problem(risk.get("acceptance"), today, started)
            if problem:
                out.append(("risk", risk["id"], "not_counted", "Acceptance of %s %s." % (risk["id"], problem)))
                status = "OPEN"
        linked = (risk.get("links") or {}).get("findings") or []
        rank = constants.SEVERITY_RANK.get(risk["severity"], 0)
        represented = any(f in gate_findings and constants.SEVERITY_RANK[gate_findings[f]] <= rank for f in linked)
        if represented and not (status == "ACCEPTED" and risk["severity"] == "CRITICAL"):
            continue  # its findings carry it into the decision
        if status in _OPEN_RISK:
            if risk["severity"] == "CRITICAL":
                out.append(("risk", risk["id"], "critical", "Open CRITICAL risk %s (%s) in the risk register." % (risk["id"], risk["title"])))
            elif risk["severity"] == "HIGH":
                out.append(("risk", risk["id"], "blocking", "Open HIGH risk %s (%s) in the risk register." % (risk["id"], risk["title"])))
        elif status == "ACCEPTED":
            if risk["severity"] == "CRITICAL":
                out.append(("risk", risk["id"], "accepted_critical",
                            "Accepted CRITICAL risk %s still blocks release; accepting it records the decision but never lifts the block." % risk["id"]))
            else:
                out.append(("risk", risk["id"], "accepted", "Accepted risk %s." % risk["id"]))
    return out


def _dismissal_time(entry, status):
    """When the user marked the finding false positive or superseded (the latest such history event)."""
    if not entry or status not in constants.DISMISSED_STATUSES:
        return None
    times = [ev.get("at") for ev in entry.get("history") or [] if ev.get("status") == status and ev.get("source") == "user"]
    return max(t for t in times if t) if any(times) else None


def decide(audit, ledger, today, lifecycle=None, registers=None):
    lifecycle = lifecycle or {}
    started = run_started_at(audit.get("run"))
    open_findings, accepted_findings, accepted_critical, not_counted, dismissed = [], [], [], [], []
    carried_findings = audit.get("carried_forward_findings") or []
    entries = {e["id"]: e for e in ((ledger or {}).get("entries") or {}).values()}
    for f in audit["findings"] + carried_findings:
        mark = _dismissal_time(entries.get(f["id"]), f.get("status"))
        if f.get("status") in constants.DISMISSED_STATUSES and started and mark and mark >= started:
            not_counted.append("%s was marked %s at %s, after this run started; it counts from the next run."
                               % (f["id"], f["status"].replace("_", " ").lower(), mark))
            open_findings.append(f)
            continue
        if f.get("status") in constants.DISMISSED_STATUSES:
            # The owner marked it a false positive or superseded, yet this run still reports it. That counts like an
            # acceptance: visible, never READY without qualification, and a CRITICAL one still blocks.
            if f["severity"] == "CRITICAL":
                accepted_critical.append(f["id"])
                open_findings.append(f)
            else:
                dismissed.append(f)
            continue
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
    accepted_risks = []
    gate_findings = {f["id"]: f["severity"] for f in open_findings + accepted_findings}
    register_inputs = _register_inputs(registers, gate_findings, today, started)
    for item in register_inputs:
        kind, rid, effect, text = item
        if effect == "critical":
            critical.append(rid)
        elif effect == "accepted_critical":
            accepted_critical.append(rid)
            critical.append(rid)
        elif effect == "insufficient":
            insufficient.append(text)
        elif effect == "blocking":
            blocking.append(rid)
        elif effect == "accepted":
            accepted_risks.append(rid)
        elif effect == "not_counted":
            not_counted.append(text)

    reasons = []
    if critical:
        decision = constants.GATE_CRITICAL
        reasons.append("Open CRITICAL finding(s) with confirmed or likely confidence: %s." % ", ".join(critical))
    elif insufficient:
        decision = constants.GATE_INSUFFICIENT
    elif blocking:
        decision = constants.GATE_NOT_READY
        reasons.append("Open release-blocking finding(s): %s." % ", ".join(blocking))
    elif accepted_findings or accepted_controls or dismissed or accepted_risks:
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
    if accepted_findings or accepted_controls or accepted_risks:
        refs = [f["id"] for f in accepted_findings] + accepted_controls + accepted_risks
        reasons.append("User-accepted risks in effect: %s." % ", ".join(refs))
    if dismissed:
        reasons.append("Still reported by this audit, but marked false positive or superseded by the owner: %s." %
                       ", ".join(f["id"] for f in dismissed))
    if accepted_critical:
        reasons.append("Accepted CRITICAL finding(s) still block release: %s. Accepting a CRITICAL risk records the decision but never lifts the block." % ", ".join(accepted_critical))
    for note in not_counted:
        reasons.append(note)
    carried_domains = [c["domain"] for c in audit["coverage"] if c["status"] == constants.CARRIED_FORWARD]
    if decision == constants.GATE_READY:
        reasons.append("No open release-blocking findings, no unverified release-critical controls, and every domain was "
                       "assessed%s or shown not applicable." % (" in this run, carried forward from an earlier assessment that "
                                                               "no change affects," if carried_domains else ""))
    unassessed = [f["id"] for f in carried_findings if f.get("basis") == "not_applicable"]
    if unassessed:
        reasons.append("Open finding(s) in a domain reported not applicable in this run still count, because the "
                       "earlier evidence was never resolved: %s." % ", ".join(unassessed))
    if carried_domains:
        reasons.append("Carried forward, not reassessed in this run (no change since the last run affects them): %s. "
                       "Their earlier results count, re-checked only against the hashes of the files they cited%s." % (
                           ", ".join(carried_domains),
                           "; carried-forward findings: " + ", ".join(f["id"] for f in carried_findings) if carried_findings else ""))
    for item in register_inputs:
        if item[2] in ("critical", "blocking", "accepted_critical"):
            reasons.append(item[3])

    return {
        "decision": decision,
        "reasons": reasons,
        "critical_findings": critical,
        "blocking_findings": blocking,
        "insufficient_evidence": insufficient,
        "accepted_findings": [f["id"] for f in accepted_findings],
        "dismissed_findings": [f["id"] for f in dismissed],
        "accepted_register_risks": accepted_risks,
        "accepted_controls": accepted_controls,
        "carried_forward_domains": carried_domains,
        "carried_forward_findings": [f["id"] for f in carried_findings],
        "accepted_critical_findings": accepted_critical,
        "acceptances_not_counted": not_counted,
        "run_started_at": started,
        "scope_statement": SCOPE_STATEMENT,
        "exit_code": constants.GATE_EXIT_CODES[decision],
    }

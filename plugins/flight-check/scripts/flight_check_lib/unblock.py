"""What stands between the project and a READY decision, with the next step for each (docs/usage.md, "Getting unblocked").

Built from the records only and mirroring the ship gate's inputs (gate.py) without deciding anything: the recorded
decision stays the one the last audit made. Each item names a concrete action: a command to run, evidence to import,
or a decision only the user can make.
"""

import os

from . import catalog, constants, gate, ledger

EVIDENCE_DIR = ".flight-check/evidence"
# A file at the project root that marks an ecosystem, and the scanner command that writes JSON Flight Check can import.
SCANNERS = [
    ("package-lock.json", "npm", "npm audit --json > %s/npm-audit.json"),
    ("pnpm-lock.yaml", "pnpm", "pnpm audit --json > %s/pnpm-audit.json"),
    ("yarn.lock", "Yarn", "yarn npm audit --all --recursive --json > %s/yarn-audit.json (Yarn 1: yarn audit --json)"),
    ("requirements.txt", "Python", "pip-audit -r requirements.txt -f json -o %s/pip-audit.json"),
    ("poetry.lock", "Python", "pip-audit -f json -o %s/pip-audit.json (inside the project's virtual environment)"),
    ("Pipfile.lock", "Python", "pip-audit -f json -o %s/pip-audit.json (inside the project's virtual environment)"),
    ("go.mod", "Go", "govulncheck -json ./... > %s/govulncheck.json"),
    ("Cargo.lock", "Rust", "cargo audit --json > %s/cargo-audit.json"),
    ("Gemfile.lock", "Ruby", "bundle-audit check --update --format json --output %s/bundle-audit.json"),
    ("composer.lock", "PHP", "composer audit --format=json > %s/composer-audit.json"),
]
GENERIC_SCANNER = "osv-scanner --format json -r . > %s/osv-scanner.json"
_ACTIVE = ("OPEN", "REGRESSION", "UNDER_REVIEW", "REMEDIATION_PLANNED")
_RANK = {"critical": 0, "insufficient": 1, "blocking": 2, "refresh": 3}


def decision_inputs_changed(st, today):
    """What can change the ship decision without any file changing: records changed since the latest decision, and
    acceptances whose review date has passed."""
    out = []
    stamps = st.project.get("readiness") or []
    if stamps and stamps[-1]["revision"] != st.project["revision"]:
        out.append("the project's records changed after the latest decision (revision %d, now %d)"
                   % (stamps[-1]["revision"], st.project["revision"]))
    led = st.ledger or {}
    for e in (led.get("entries") or {}).values():
        if e["status"] == "ACCEPTED_RISK" and ledger.acceptance_problems(e.get("accepted_risk"), today):
            out.append("the acceptance of %s no longer counts" % e["id"])
    for cid, acc in (led.get("control_acceptances") or {}).items():
        if ledger.acceptance_problems(acc, today):
            out.append("the acceptance of control %s no longer counts" % cid)
    for risk in (st.registers or {}).get("risks", []):
        review = (risk.get("acceptance") or {}).get("review_date")
        if risk["status"] == "ACCEPTED" and review and review < today.isoformat():
            out.append("the acceptance of %s is past its review date" % risk["id"])
    return out[:20]


def scanner_commands(root):
    """Scanner commands for the ecosystems whose lock or manifest files are at the project root, then a generic one."""
    try:
        names = set(os.listdir(root)) if root else set()
    except OSError:
        names = set()
    cmds = ["mkdir -p %s" % EVIDENCE_DIR]
    seen = set()
    for marker, ecosystem, cmd in SCANNERS:
        if marker in names and (ecosystem, cmd) not in seen:
            seen.add((ecosystem, cmd))
            cmds.append("%s: %s" % (ecosystem, cmd % EVIDENCE_DIR))
    if any(n.endswith((".csproj", ".sln")) for n in names):
        cmds.append(".NET: dotnet list package --vulnerable --include-transitive --format json > %s/dotnet-vulnerable.json"
                    % EVIDENCE_DIR)
    cmds.append("%s: %s" % ("Any ecosystem" if len(cmds) == 1 else "Or, for every ecosystem at once", GENERIC_SCANNER % EVIDENCE_DIR))
    return cmds


def _control_item(cid, rec, cat, root):
    title = (cat.get("title") or cid).rstrip(".")
    if rec["state"] == "NOT_MET":
        return {"kind": "control", "id": cid, "effect": "blocking", "problem": "%s is not met: %s." % (cid, title),
                "action": "Fix the findings linked to it (/flight-check:remediate), then run /flight-check:audit --changed."}
    item = {"kind": "control", "id": cid, "effect": "insufficient",
            "problem": "No positive evidence was recorded for %s: %s." % (cid, title)}
    if cid == "SUPPLY-KNOWN-VULNS":
        item["action"] = ("Run a vulnerability scanner, then import its output: /flight-check:audit --recheck %s "
                          "--evidence <the JSON file>." % cid)
        item["commands"] = scanner_commands(root)
    elif cid == "REL-BACKUP-RESTORE":
        item["action"] = ("Write down when a restore from backup was last tested and with what result, save it as a file, "
                          "and import it: /flight-check:audit --recheck %s --evidence <that file>." % cid)
    else:
        item["action"] = ("If the project meets it, re-check just this control: /flight-check:audit --recheck %s (add "
                          "--context FILE naming where it is implemented). If it does not, fix it. If you accept the gap, "
                          "record that with /flight-check:track." % cid)
    if cat.get("verified_when"):
        item["needs"] = cat["verified_when"]
    return item


def _finding_item(e):
    sev, conf = e["severity"], e.get("confidence", "LIKELY")
    blocking = e.get("release_blocking")
    if blocking is None:  # recorded before ledger entries kept the flag: apply the gate's rule to what is known
        blocking = gate.release_blocking_effective({"severity": sev, "confidence": conf})
    ref = "%s (%s, %s): %s" % (e["id"], sev, e["status"], e["title"])
    if e["status"] == "REMEDIATED":
        if not blocking:
            return None
        return {"kind": "finding", "id": e["id"], "effect": "blocking", "problem": "%s has a recorded fix that no audit has "
                "verified yet." % ref, "action": "Run /flight-check:audit --changed to verify the fix."}
    if (sev == "CRITICAL" and conf in ("POTENTIAL", "UNVERIFIED")) or (sev == "HIGH" and conf == "UNVERIFIED"):
        return {"kind": "finding", "id": e["id"], "effect": "insufficient",
                "problem": "%s is not confirmed (confidence %s)." % (ref, conf),
                "action": "Have a person check it. If it is real, fix it (/flight-check:remediate %s); if it is not, mark it a "
                          "false positive with /flight-check:track." % e["id"]}
    if sev == "CRITICAL":
        return {"kind": "finding", "id": e["id"], "effect": "critical", "problem": "%s." % ref,
                "action": "Fix it (/flight-check:remediate %s), then run /flight-check:audit --changed. Accepting a CRITICAL "
                          "risk does not lift the block." % e["id"]}
    if blocking:
        return {"kind": "finding", "id": e["id"], "effect": "blocking", "problem": "%s." % ref,
                "action": "Fix it (/flight-check:remediate %s), then run /flight-check:audit --changed; or, if the risk is "
                          "acceptable for this release, accept it with /flight-check:track." % e["id"]}
    return None


_REGISTER_ACTIONS = {
    "requirement": "Link the requirement to controls an audit can verify (/flight-check:track), then run /flight-check:audit --changed.",
    "legal": "Complete the action with counsel, then record the outcome with /flight-check:track.",
    "risk": "Mitigate the risk and record it with /flight-check:track; a non-CRITICAL risk may instead be accepted there.",
}


def items(st, root, today):
    """Everything that keeps the latest decision from READY FOR RELEASE, most serious first. Empty when it is READY."""
    stamps = st.project.get("readiness") or []
    latest = stamps[-1] if stamps else None
    out = []
    if not latest:
        return [{"kind": "audit", "id": None, "effect": "refresh", "problem": "No audit has recorded a ship decision yet.",
                 "action": "Run /flight-check:audit."}]
    inputs = decision_inputs_changed(st, today)
    if inputs:
        out.append({"kind": "records", "id": None, "effect": "refresh",
                    "problem": "The decision is out of date: %s." % "; ".join(inputs),
                    "action": "Run /flight-check:audit --changed for a new decision; it reassesses only what changed."})
    if latest["decision"] == constants.GATE_READY and not inputs:
        return out
    led = st.ledger or {}
    accepted = {cid for cid, acc in (led.get("control_acceptances") or {}).items() if not ledger.acceptance_problems(acc, today)}
    cat = {c["id"]: c for c in catalog.controls()}
    for cid, rec in sorted((st.project.get("controls") or {}).items()):
        if cat.get(cid, {}).get("release_critical") and rec.get("state") in ("UNVERIFIED", "NOT_MET") and cid not in accepted:
            out.append(_control_item(cid, rec, cat.get(cid, {}), root))
    for d, rec in sorted((st.project.get("domains") or {}).items()):
        if rec.get("status") == "NOT_ASSESSED":
            out.append({"kind": "domain", "id": d, "effect": "insufficient", "problem": "The %s domain was not assessed." % d,
                        "action": "Run /flight-check:audit (a full audit), or /flight-check:audit --domains %s." % d})
    entries = sorted((led.get("entries") or {}).values(), key=lambda e: e["id"])
    for e in entries:
        if e["status"] in _ACTIVE + ("REMEDIATED",) or (e["status"] == "ACCEPTED_RISK" and e["severity"] == "CRITICAL"):
            item = _finding_item(e)
            if item:
                out.append(item)
    gate_findings = {e["id"]: e["severity"] for e in entries if e["status"] in _ACTIVE + ("ACCEPTED_RISK",)}
    for kind, rid, effect, explanation in gate._register_inputs(st.registers, gate_findings, today):
        if effect in ("insufficient", "blocking", "critical", "accepted_critical", "not_counted"):
            out.append({"kind": kind, "id": rid, "effect": {"accepted_critical": "critical", "not_counted": "refresh"}.get(effect, effect),
                        "problem": explanation, "action": _REGISTER_ACTIONS[kind]})
    out.sort(key=lambda i: _RANK.get(i["effect"], 9))
    return out

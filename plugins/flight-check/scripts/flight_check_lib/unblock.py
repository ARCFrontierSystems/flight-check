"""What stands between the project and a READY decision, with the next step for each (docs/usage.md, "Getting unblocked").

Built from the records only and mirroring the ship gate's inputs (gate.py) without deciding anything: the recorded
decision stays the one the last audit made. Each item names a concrete action: a command to run, evidence to import,
or a decision only the user can make.
"""

import os

from . import catalog, constants, gate, ledger, plain

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
        out.append("your Flight Check records changed after the latest decision (revision %d, now %d), for example "
                   "a risk you accepted or a requirement you added" % (stamps[-1]["revision"], st.project["revision"]))
    led = st.ledger or {}
    for e in (led.get("entries") or {}).values():
        if e["status"] == "ACCEPTED_RISK" and ledger.acceptance_problems(e.get("accepted_risk"), today):
            out.append("the acceptance of %s no longer counts (it is incomplete or past its review date)" % e["id"])
    for cid, acc in (led.get("control_acceptances") or {}).items():
        if ledger.acceptance_problems(acc, today):
            out.append("the acceptance of check %s no longer counts (it is incomplete or past its review date)" % cid)
    for risk in (st.registers or {}).get("risks", []):
        review = (risk.get("acceptance") or {}).get("review_date")
        if risk["status"] == "ACCEPTED" and review and review < today.isoformat():
            out.append("the review date for the accepted risk %s has passed" % risk["id"])
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


def _sev(e):
    return plain.SEVERITY.get(e["severity"], e["severity"].lower())


def _where(e):
    path = ledger._entry_path(e)
    return (" (in %s)" % path) if path else ""


def _control_item(cid, rec, cat, root):
    what = plain.control(cid, cat.get("title"))
    if rec["state"] == "NOT_MET":
        return {"kind": "control", "id": cid, "effect": "blocking", "who": "claude",
                "problem": "This is not the case yet: %s." % what,
                "action": "I can fix the problems behind it. Say \"fix it\" and I will show you the changes first."}
    item = {"kind": "control", "id": cid, "effect": "insufficient", "who": "claude",
            "problem": "Flight Check could not confirm that %s." % what}
    if cid == "SUPPLY-KNOWN-VULNS":
        item["action"] = ("I can run a vulnerability scanner for you and save its results (say \"fix it\"). Then type "
                          "/flight-check:audit --recheck %s --evidence <the saved file> so Flight Check reads them." % cid)
        item["commands"] = scanner_commands(root)
    elif cid == "REL-BACKUP-RESTORE":
        item["who"] = "you"
        item["action"] = ("Test restoring your production data from a backup. Write down when you did it and whether it "
                          "worked, save that as a file, and type /flight-check:audit --recheck %s --evidence <that file>." % cid)
    else:
        item["action"] = ("If the project already does this, type /flight-check:audit --recheck %s (add --context FILE "
                          "saying where it happens). If it does not, I can add it (say \"fix it\"). If it is not needed "
                          "for this release, record that decision with /flight-check:track." % cid)
    if cat.get("verified_when"):
        item["needs"] = cat["verified_when"]
    return item


def _finding_item(e):
    sev, conf = e["severity"], e.get("confidence", "LIKELY")
    blocking = e.get("release_blocking")
    if blocking is None:  # recorded before ledger entries kept the flag: apply the gate's rule to what is known
        blocking = gate.release_blocking_effective({"severity": sev, "confidence": conf})
    title, fid = e["title"], e["id"]
    legal_matter = e.get("domain") == "legal-business"
    if e["status"] == "REMEDIATED":
        if not blocking:
            return None
        return {"kind": "finding", "id": fid, "effect": "blocking", "who": "you",
                "problem": "A fix was made for \"%s\" but has not been confirmed yet." % title,
                "action": "Type /flight-check:audit --changed to confirm the fix; it only re-checks what changed."}
    if (sev == "CRITICAL" and conf in ("POTENTIAL", "UNVERIFIED")) or (sev == "HIGH" and conf == "UNVERIFIED"):
        return {"kind": "finding", "id": fid, "effect": "insufficient", "who": "you",
                "problem": "Possible %s problem that needs a person to look at it: \"%s\"%s." % (_sev(e), title, _where(e)),
                "action": "Take a look (I can walk you through it). If it is real, I can fix it (say \"fix it\"). If it is "
                          "not, record that with /flight-check:track %s not a real problem, because <why>." % fid}
    if sev == "CRITICAL":
        return {"kind": "finding", "id": fid, "effect": "critical", "who": "you" if legal_matter else "claude",
                "problem": "Serious problem: \"%s\"%s." % (title, _where(e)),
                "action": ("This needs your decision, possibly with a lawyer; I can prepare a summary for them with "
                           "/flight-check:legal-packet." if legal_matter else
                           "I can fix this. Say \"fix it\" and I will show you the change first.")
                          + " A serious problem blocks release until it is fixed; accepting the risk does not unblock it."}
    if blocking:
        return {"kind": "finding", "id": fid, "effect": "blocking", "who": "you" if legal_matter else "claude",
                "problem": "%s problem: \"%s\"%s." % (_sev(e).capitalize(), title, _where(e)),
                "action": ("This needs your decision, possibly with a lawyer (/flight-check:legal-packet prepares a "
                           "summary for them)." if legal_matter else
                           "I can fix this. Say \"fix it\" and I will show you the change first.")
                          + " If it is intentional and acceptable for this release, record that with "
                            "/flight-check:track accept %s." % fid}
    return None


def _register_item(kind, rid, effect, rec):
    title = (rec or {}).get("title", rid)
    if kind == "requirement":
        return {"kind": kind, "id": rid, "effect": effect, "who": "you",
                "problem": "Your must-have requirement \"%s\" (%s) is not confirmed by the code yet." % (title, rid),
                "action": "Link it to checks an audit can confirm, or lower how critical it is, with /flight-check:track."}
    if kind == "legal":
        return {"kind": kind, "id": rid, "effect": effect, "who": "you",
                "problem": "Your legal question \"%s\" (%s) is marked as needing action before release." % (title, rid),
                "action": "Complete it, with your lawyer if needed; I can make the technical changes it calls for (say "
                          "\"fix it\"). Then record the outcome with /flight-check:track."}
    if effect == "refresh":
        return {"kind": kind, "id": rid, "effect": effect, "who": "you",
                "problem": "Your acceptance of the risk \"%s\" (%s) no longer counts: it is incomplete or past its review date." % (title, rid),
                "action": "Renew or update it with /flight-check:track, then type /flight-check:audit --changed."}
    serious = (rec or {}).get("severity") == "CRITICAL"
    return {"kind": kind, "id": rid, "effect": effect, "who": "you",
            "problem": "%s risk in your risk list is still %s: \"%s\" (%s)." % (
                "A serious" if serious else "An important", "accepted, and serious risks still block release" if effect == "critical"
                and (rec or {}).get("status") == "ACCEPTED" else "open", title, rid),
            "action": "Reduce it (I can help with code changes) and record that with /flight-check:track."
                      + ("" if serious else " If it is acceptable for this release, you can accept it there instead.")}


def items(st, root, today):
    """Everything that keeps the latest decision from READY FOR RELEASE, most serious first. Empty when it is READY."""
    stamps = st.project.get("readiness") or []
    latest = stamps[-1] if stamps else None
    out = []
    if not latest:
        return [{"kind": "audit", "id": None, "effect": "refresh", "who": "you",
                 "problem": "Flight Check has not checked this project yet.",
                 "action": "Type /flight-check:audit for the first check; it tells you the expected cost before it starts."}]
    inputs = decision_inputs_changed(st, today)
    if inputs:
        out.append({"kind": "records", "id": None, "effect": "refresh", "who": "you",
                    "problem": "The last release decision is out of date: %s." % "; ".join(inputs),
                    "action": "Type /flight-check:audit --changed to update it; it only re-checks what changed."})
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
            out.append({"kind": "domain", "id": d, "effect": "insufficient", "who": "you",
                        "problem": "One area of the project was not checked: %s." % d.replace("-", " "),
                        "action": "Type /flight-check:audit --recheck with a check from that area, or run a full "
                                  "/flight-check:audit."})
    entries = sorted((led.get("entries") or {}).values(), key=lambda e: e["id"])
    for e in entries:
        if e["status"] in _ACTIVE + ("REMEDIATED",) or (e["status"] == "ACCEPTED_RISK" and e["severity"] == "CRITICAL"):
            item = _finding_item(e)
            if item:
                out.append(item)
    gate_findings = {e["id"]: e["severity"] for e in entries if e["status"] in _ACTIVE + ("ACCEPTED_RISK",)}
    regs = st.registers or {}
    by_id = {r["id"]: r for key in ("requirements", "legal", "risks") for r in regs.get(key, [])}
    for kind, rid, effect, _ in gate._register_inputs(st.registers, gate_findings, today):
        if effect in ("insufficient", "blocking", "critical", "accepted_critical", "not_counted"):
            effect = {"accepted_critical": "critical", "not_counted": "refresh"}.get(effect, effect)
            out.append(_register_item(kind, rid, effect, by_id.get(rid)))
    out.sort(key=lambda i: _RANK.get(i["effect"], 9))
    return out

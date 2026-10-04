"""Render the human-readable Seaworthy report (report.md) from audit.final.json and summary.json."""

import re

from . import catalog, constants, textsafety

SPEC_SECTIONS = [
    # (number, title, domain or None)
    ("1", "Executive Summary", None),
    ("2", "Ship Readiness Decision", None),
    ("3", "Critical Findings", None),
    ("4", "High Findings", None),
    ("5", "Medium Findings", None),
    ("6", "Low Findings", None),
    ("7", "Informational Findings", None),
    ("8", "Legal / Business Review", "legal-business"),
    ("9", "Privacy Review", "privacy"),
    ("10", "Compliance Readiness", "compliance-readiness"),
    ("11", "Authentication", "authentication"),
    ("12", "Authorization", "authorization"),
    ("13", "Data Protection", "data-protection"),
    ("13a", "Application Security", "application-security"),
    ("14", "Payments / Subscriptions", "payments"),
    ("15", "Infrastructure / Deployment", "infrastructure"),
    ("15a", "Production Readiness", "production-readiness"),
    ("15b", "Operational Readiness", "operational-readiness"),
    ("16", "Reliability / Recovery", "reliability"),
    ("17", "Accessibility", "accessibility"),
    ("18", "Dependencies / Supply Chain", "supply-chain"),
    ("19", "Third-Party Services", "third-party"),
    ("20", "AI / LLM Risks", "ai-llm"),
    ("21", "IP / Asset Risks", "ip-assets"),
    ("22", "Documentation Consistency", "documentation"),
    ("23", "Testing", "testing"),
    ("24", "Positive Controls", None),
    ("25", "Unverified Areas", None),
    ("26", "Missing Evidence", None),
    ("27", "Accepted-Risk Candidates", None),
    ("28", "Recommended Remediation Priority", None),
    ("29", "Regression Recommendations", None),
    ("30", "Legal Review Assistant Summary", None),
    ("31", "Attorney Review Packet Availability", None),
    ("32", "Final Ship Decision", None),
    ("33", "Evidence Index", None),
]


def md(text):
    """Inline-safe Markdown: sanitized, secrets masked, HTML/link/image syntax neutralized."""
    text, _ = textsafety.redact(textsafety.sanitize(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("![", "!\\[").replace("](", "]\\(")
    text = re.sub(r"(?m)^(\s*)([#>|])", r"\1\\\2", text)
    return text.replace("|", "\\|")


def md_block(text):
    return "\n".join(md(line) for line in (text or "").splitlines())


def code_block(text, lang=""):
    text, _ = textsafety.redact(textsafety.sanitize(text or ""))
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    fence = "`" * max(3, longest + 1)
    return "%s%s\n%s\n%s" % (fence, lang, text.rstrip("\n"), fence)


def _loc(ev):
    if ev.get("kind") == "absence":
        return "absence of evidence"
    if ev.get("kind") == "user-provided":
        return "user-provided: %s" % ev.get("path", "")
    return "%s:%s-%s" % (ev.get("path"), ev.get("start_line"), ev.get("end_line"))


def _evidence_lines(evidence):
    out = []
    for ev in evidence:
        ref = ev.get("ref", "")
        check = ev.get("check")
        check_txt = " (mechanical check: %s)" % check if check and check != "NOT_CHECKED" else ""
        out.append("- **%s** `%s` [%s]%s" % (ref, md(_loc(ev)), ev.get("kind"), check_txt))
        if ev.get("kind") == "absence":
            out.append("  - Searched: %s" % md(ev.get("searched", "")))
        if ev.get("quote"):
            out.append("")
            out.append("  " + code_block(ev["quote"]).replace("\n", "\n  "))
            out.append("")
        if ev.get("note"):
            out.append("  - Note: %s" % md(ev["note"]))
    return out


def _finding_block(f):
    lines = ["### %s — %s" % (f["id"], md(f["title"])), ""]
    legal = f.get("legal") or {}
    hr = f.get("human_review") or {}
    rows = [
        ("Domain", catalog.domain_title(f["domain"])),
        ("Severity", f["severity"]),
        ("Confidence", f["effective_confidence"] + (" (reported %s; evidence check failed)" % f["confidence"] if f["effective_confidence"] != f["confidence"] else "")),
        ("Status", f["status"]),
        ("Blocks release", "Yes" if f["release_blocking_effective"] else "No"),
        ("Legal / business review", legal.get("classification", "Not indicated") + (" — status %s" % f["legal_status"] if f.get("legal_status") else "")),
        ("Human review", ("Recommended: " + ", ".join(hr.get("types") or [])) if hr.get("required") else "Not indicated"),
        ("Verifier", f["verification"]["verifier_verdict"]),
        ("Evidence check", f.get("evidence_check", "NOT_APPLICABLE")),
        ("Rule", f["rule"]),
        ("First seen", f.get("first_seen_run", "")),
    ]
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for k, v in rows:
        lines.append("| %s | %s |" % (k, md(str(v))))
    lines.append("")
    lines.append("**Affected components:** " + ", ".join("`%s`" % md(c) for c in f["affected_components"]))
    lines.append("")
    lines.append("**Evidence**")
    lines.append("")
    lines.extend(_evidence_lines(f["evidence"]))
    lines.append("")
    lines.append("**Explanation.** " + md_block(f["explanation"]))
    lines.append("")
    lines.append("**Potential impact.** " + md_block(f["impact"]))
    lines.append("")
    lines.append("**Why this severity.** " + md_block(f["severity_rationale"]))
    if f.get("confidence_rationale"):
        lines.append("")
        lines.append("**Why this confidence.** " + md_block(f["confidence_rationale"]))
    lines.append("")
    lines.append("**Counter-evidence checked (false-positive defense)**")
    for ce in f["counterevidence"]:
        lines.append("- Searched: %s — Result: %s" % (md(ce["searched"]), md(ce["result"])))
    lines.append("")
    lines.append("**Recommended remediation direction.** " + md_block(f["remediation"]))
    if f.get("release_blocking_rationale"):
        lines.append("")
        lines.append("**Release-blocking rationale.** " + md_block(f["release_blocking_rationale"]))
    if legal:
        lines.append("")
        lines.append("**Legal Review Assistant.** Classification: %s." % legal["classification"])
        if legal.get("why_review"):
            lines.append("Why review is recommended: " + md_block(legal["why_review"]))
        if legal.get("questions"):
            lines.append("")
            lines.append("Questions for qualified counsel:")
            for i, q in enumerate(legal["questions"], 1):
                lines.append("%d. %s" % (i, md(q)))
        if legal.get("decisions_needed"):
            lines.append("")
            lines.append("Business decisions needed:")
            for d in legal["decisions_needed"]:
                lines.append("- %s" % md(d))
    if f.get("standards"):
        lines.append("")
        lines.append("**Related standards (by identifier):** " + ", ".join(md(s["id"] + (" " + s["version"] if s.get("version") else "")) for s in f["standards"]))
    if f["verification"].get("notes"):
        lines.append("")
        lines.append("**Verifier notes.** " + md_block(f["verification"]["notes"]))
    if f.get("also_reported_by"):
        lines.append("")
        lines.append("**Also reported (merged duplicates):** " + "; ".join("%s (%s): %s" % (d["local_id"], catalog.domain_title(d["domain"]), md(d["title"])) for d in f["also_reported_by"]))
    if f.get("accepted_risk"):
        a = f["accepted_risk"]
        lines.append("")
        lines.append("**Accepted risk (user decision).** Owner: %s; date: %s; scope: %s; reason: %s; compensating controls: %s%s." % (
            md(a["owner"]), md(a["date"]), md(a["scope"]), md(a["reason"]), md(a["compensating_controls"]),
            ("; review by " + md(a["review_date"])) if a.get("review_date") else ""))
    lines.append("")
    return lines


def _domain_section(final, domain):
    cov = next(c for c in final["coverage"] if c["domain"] == domain)
    out = ["**Coverage:** %s — %s" % (cov["status"], md(cov["rationale"])), ""]
    if cov.get("conflicts"):
        out.append("Coverage notes: " + md("; ".join(cov["conflicts"])))
        out.append("")
    searches = cov.get("searches") or []
    if searches:
        out.append("<details><summary>Searches and files examined (%d)</summary>" % len(searches))
        out.append("")
        for s in searches[:60]:
            out.append("- %s" % md(s))
        out.append("")
        out.append("</details>")
        out.append("")
    controls = [c for c in final["controls"] if c["domain"] == domain]
    if controls:
        out.append("| Control | Release-critical | State | Basis |")
        out.append("|---|---|---|---|")
        for c in controls:
            basis = c.get("missing_evidence") if c["state"] == "UNVERIFIED" else c.get("rationale") or ""
            if c["state"] == "NOT_MET" and c.get("related_findings"):
                basis = "See " + ", ".join(c["related_findings"])
            if c["state"] == "VERIFIED" and not basis:
                basis = "Evidence " + ", ".join(ev.get("ref", "") for ev in c.get("evidence") or [])
            out.append("| %s: %s | %s | %s | %s |" % (c["id"], md(c["title"]), "Yes" if c["release_critical"] else "No", c["state"], md(basis or "")))
        out.append("")
    fs = [f for f in final["findings"] if f["domain"] == domain]
    if fs:
        out.append("Findings in this domain:")
        for f in fs:
            out.append("- %s [%s / %s / %s] %s" % (f["id"], f["severity"], f["effective_confidence"], f["status"], md(f["title"])))
    else:
        out.append("No findings were reported in this domain for the assessed scope." if cov["status"] == "ASSESSED" else "No findings: this domain was %s." % cov["status"].replace("_", " ").lower())
    pcs = [p for p in final["positive_controls"] if p["domain"] == domain]
    if pcs:
        out.append("")
        out.append("Positive controls observed:")
        for p in pcs:
            out.append("- %s (evidence %s)" % (md(p["title"]), ", ".join(ev.get("ref", "") for ev in p["evidence"])))
    out.append("")
    return out


def render(final, summary):
    run = final["run"]
    g = final["gate"]
    sev_groups = {s: [f for f in final["findings"] if f["severity"] == s] for s in constants.SEVERITIES}
    out = []
    out.append("# Seaworthy Report — %s" % md(run["target"]["name"]))
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    for k, v in (("Seaworthy version", final["tool"]["version"]), ("Run", run["run_id"]), ("Started", run["started_at"]),
                 ("Mode", run["mode"]), ("Trust tier", run["trust_tier"]), ("Revision", run["target"].get("commit", "not recorded")),
                 ("Scope", ", ".join(run["target"].get("scope") or ["entire project"])), ("Ship decision", g["decision"])):
        out.append("| %s | %s |" % (k, md(str(v))))
    out.append("")
    out.append("> " + md(constants.REPORT_DISCLAIMER))
    out.append("")

    for num, title, domain in SPEC_SECTIONS:
        out.append("## %s. %s" % (num, title))
        out.append("")
        if num == "1":
            out.append(md_block(summary["executive_summary"]) if summary else "_Executive summary not provided._")
        elif num in ("2", "32"):
            out.append("**%s**" % g["decision"])
            out.append("")
            for r in g["reasons"]:
                out.append(md(r) if r.startswith("- ") else md(r))
            out.append("")
            out.append("_%s_" % md(g["scope_statement"]))
        elif num in ("3", "4", "5", "6", "7"):
            sev = constants.SEVERITIES[int(num) - 3]
            group = sev_groups[sev]
            if not group:
                out.append("No %s findings in the assessed scope." % sev.lower())
            for f in group:
                out.extend(_finding_block(f))
        elif domain:
            if domain == "legal-business":
                legal = [f for f in final["findings"] if f.get("legal")]
                if legal:
                    out.append("Findings from any domain that warrant legal or business review:")
                    for cls in constants.LEGAL_CLASSES:
                        group = [f for f in legal if f["legal"]["classification"] == cls]
                        if group:
                            out.append("- **%s:** %s" % (cls, ", ".join("%s (%s)" % (f["id"], md(f["title"])) for f in group)))
                    out.append("")
            out.extend(_domain_section(final, domain))
        elif num == "24":
            if not final["positive_controls"]:
                out.append("No positive controls were recorded with supporting evidence.")
            for p in final["positive_controls"]:
                out.append("- **%s** [%s] %s — evidence %s" % (p.get("id", p["local_id"]), catalog.domain_title(p["domain"]), md(p["title"]),
                                                                 ", ".join(ev.get("ref", "") for ev in p["evidence"])))
            if final.get("unconfirmed_positive_controls"):
                out.append("")
                out.append("Positive controls reported but not counted because their evidence could not be confirmed mechanically:")
                for p in final["unconfirmed_positive_controls"]:
                    out.append("- %s" % md(p["title"]))
        elif num == "25":
            uv = [c for c in final["controls"] if c["state"] == "UNVERIFIED"]
            if not uv and not final["unverified_areas"]:
                out.append("No unverified areas were recorded.")
            for c in uv:
                out.append("- **%s%s** %s — %s" % (c["id"], " (release-critical)" if c["release_critical"] else "", md(c["title"]), md(c.get("missing_evidence") or "")))
            for ua in final["unverified_areas"]:
                out.append("- [%s] %s — missing: %s; how to verify: %s" % (catalog.domain_title(ua["domain"]), md(ua["area"]), md(ua["missing_evidence"]), md(ua["how_to_verify"])))
            not_assessed = [c for c in final["coverage"] if c["status"] == "NOT_ASSESSED"]
            for c in not_assessed:
                out.append("- Domain **%s** was not assessed: %s" % (catalog.domain_title(c["domain"]), md(c["rationale"])))
        elif num == "26":
            failed = [f for f in final["findings"] if f.get("evidence_check") == "FAILED"]
            items = 0
            for c in final["controls"]:
                if c["state"] == "UNVERIFIED":
                    cat = catalog.control_by_id().get(c["id"], {})
                    out.append("- **%s**: %s Evidence that would establish it: %s" % (c["id"], md(c.get("missing_evidence") or ""), md(cat.get("verified_when", ""))))
                    items += 1
            for f in failed:
                bad = [ev for ev in f["evidence"] if ev.get("check") not in ("OK", "NOT_CHECKED")]
                out.append("- **%s**: cited evidence could not be confirmed in the files (%s); treat as unverified until a human checks it." % (
                    f["id"], ", ".join("%s %s" % (ev.get("ref", ""), ev.get("check")) for ev in bad)))
                items += 1
            if not items:
                out.append("No missing evidence was recorded.")
        elif num == "27":
            cands = (summary or {}).get("accepted_risk_candidates") or []
            if cands:
                out.append("Candidates only. Seaworthy never accepts a risk; acceptance requires an explicit human decision recorded with `/seaworthy:track`.")
                for c in cands:
                    out.append("- %s: %s" % (c["ref"], md(c["rationale"])))
            else:
                out.append("No accepted-risk candidates were identified.")
            acc = g.get("accepted_findings", []) + g.get("accepted_controls", [])
            if acc:
                out.append("")
                out.append("Risks currently accepted by a user decision: %s." % ", ".join(acc))
        elif num == "28":
            prio = (summary or {}).get("remediation_priority") or []
            if prio:
                for i, p in enumerate(prio, 1):
                    out.append("%d. %s — %s" % (i, p["finding_id"], md(p["rationale"])))
            else:
                out.append("No remediation priority list was provided.")
        elif num == "29":
            lc = final["lifecycle"]
            rows = [("Regressions (previously resolved, observed again)", lc.get("regressions")),
                    ("Not reproduced (previously open, not observed; confirm resolution)", lc.get("not_reproduced")),
                    ("Remediated, pending verification", lc.get("remediated_pending")),
                    ("Verified fixed in this run", lc.get("verified_this_run")),
                    ("Reopened (remediation ineffective or finding returned)", lc.get("reopened")),
                    ("Open in ledger but domain not assessed this run", lc.get("not_assessed_open")),
                    ("Accepted risks that expired and reopened", lc.get("expired_acceptances"))]
            for label, ids in rows:
                out.append("- %s: %s" % (label, ", ".join(ids) if ids else "none"))
            recs = (summary or {}).get("regression_recommendations") or []
            if recs:
                out.append("")
                out.append("Recommended regression coverage:")
                for r in recs:
                    out.append("- %s" % md(r))
        elif num == "30":
            legal = [f for f in final["findings"] if f.get("legal")]
            out.append(md_block((summary or {}).get("legal_review_summary") or "No legal review summary was provided."))
            out.append("")
            out.append("The Legal Review Assistant organizes technical evidence and questions for qualified counsel. It does not answer legal questions or determine compliance.")
            out.append("")
            out.append("| Finding | Classification | Severity | Review status | Questions |")
            out.append("|---|---|---|---|---|")
            for f in legal:
                out.append("| %s | %s | %s | %s | %d |" % (f["id"], f["legal"]["classification"], f["severity"], f.get("legal_status") or "OPEN", len(f["legal"].get("questions") or [])))
            if not legal:
                out.append("| — | No findings were classified for legal or business review | | | |")
        elif num == "31":
            needing = [f for f in final["findings"] if f.get("legal") and f["legal"]["classification"] != "INFORMATIONAL"]
            if needing:
                out.append("An Attorney Review Packet can be generated for %d finding(s): %s." % (len(needing), ", ".join(f["id"] for f in needing)))
                out.append("Run `/seaworthy:legal-packet` to produce `SEAWORTHY — ATTORNEY REVIEW PACKET` as a printable PDF.")
                if len(needing) > 1:
                    out.append("Multiple findings warrant review; combining them into one packet is recommended.")
            else:
                out.append("No findings currently warrant an Attorney Review Packet.")
        elif num == "33":
            out.append("| Ref | Belongs to | Location | Kind | Mechanical check |")
            out.append("|---|---|---|---|---|")
            for e in final["evidence_index"]:
                out.append("| %s | %s | %s | %s | %s |" % (e["ref"], e["owner"], md(e["location"]), e["kind"], e["check"]))
        out.append("")

    out.append("## Appendix A. Inventory")
    out.append("")
    for item in final["inventory"].get("items", []):
        out.append("- **%s** (%s): %s" % (md(item["aspect"]), item["state"], md(item["value"])))
    out.append("")
    out.append("## Appendix B. Findings rejected by the verifier")
    out.append("")
    if final["rejected_findings"]:
        out.append("Kept for transparency; these did not survive the false-positive defense and do not affect the ship decision.")
        for f in final["rejected_findings"]:
            out.append("- %s: %s — %s" % (f["local_id"], md(f["title"]), md(f["verification"]["notes"])))
    else:
        out.append("None.")
    out.append("")
    if final.get("duplicate_findings"):
        out.append("## Appendix B2. Findings merged as duplicates")
        out.append("")
        for f in final["duplicate_findings"]:
            out.append("- %s merged into %s: %s" % (f["local_id"], f["verification"].get("duplicate_of", "?"), md(f["title"])))
        out.append("")
    if final.get("truncation") or final["validation"].get("warnings"):
        out.append("## Appendix C. Coverage limits and warnings")
        out.append("")
        for t in final.get("truncation") or []:
            out.append("- %s" % md(t))
        for w in final["validation"].get("warnings") or []:
            out.append("- %s" % md(w))
        out.append("")
    out.append("---")
    out.append("")
    out.append("_Generated by Seaworthy %s. %s_" % (final["tool"]["version"], md(constants.REPORT_DISCLAIMER)))
    out.append("")
    return "\n".join(out)

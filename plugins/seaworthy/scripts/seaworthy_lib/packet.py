"""SEAWORTHY - ATTORNEY REVIEW PACKET: assembly, validation, PDF and Markdown rendering.

The packet organizes technical evidence and questions for qualified counsel. Evidence,
severities, classifications, and questions are copied from the finalized audit; the
request file only supplies summaries, the project identifier, and per-finding context.
Nothing here answers a legal question, cites legal authority, or determines compliance.
"""

import datetime

from . import VERSION, catalog, constants, minischema, pdf, textsafety
from .render_md import code_block, md, md_block

TITLE = "SEAWORTHY — ATTORNEY REVIEW PACKET"
JURISDICTION_NOTE = (
    "Jurisdiction-specific legal research: UNVERIFIED. Seaworthy did not research or apply the law of any "
    "jurisdiction. Whether and how any law, regulation, or contract applies is for qualified counsel to determine."
)
HOW_TO_USE = (
    "Each section separates what Seaworthy observed in the project (technical evidence) from the questions "
    "it suggests putting to qualified counsel. Seaworthy does not answer those questions. Space is provided "
    "for counsel's notes and for recording decisions. Record decisions you receive with /seaworthy:track so "
    "Seaworthy can track the resulting implementation work."
)


class PacketError(Exception):
    pass


def _clean(text):
    text, _ = textsafety.redact(textsafety.sanitize(text or ""))
    return text


def check_request(request, final):
    errors = ["packet request: %s" % e for e in minischema.validate_def(request, catalog.schema(), "packetRequest")]
    if errors:
        return errors
    by_id = {f["id"]: f for f in final["findings"]}
    for fid in request["finding_ids"]:
        f = by_id.get(fid)
        if f is None:
            errors.append("packet request: %s is not a current finding in this run" % fid)
        elif not f.get("legal"):
            errors.append("packet request: %s has no legal/business classification; the packet is for findings that warrant legal or business review" % fid)
    if len(set(request["finding_ids"])) != len(request["finding_ids"]):
        errors.append("packet request: finding_ids contains duplicates")
    for key in (request.get("per_finding") or {}):
        if key not in request["finding_ids"]:
            errors.append("packet request: per_finding has %s, which is not in finding_ids" % key)
    texts = [("executive_summary", request["executive_summary"]), ("overall_summary", request["overall_summary"])]
    for fid, extra in (request.get("per_finding") or {}).items():
        if extra.get("technical_context"):
            texts.append(("per_finding.%s.technical_context" % fid, extra["technical_context"]))
        for i, t in enumerate(extra.get("relevant_decisions") or []):
            texts.append(("per_finding.%s.relevant_decisions[%d]" % (fid, i), t))
        for i, t in enumerate(extra.get("follow_up") or []):
            texts.append(("per_finding.%s.follow_up[%d]" % (fid, i), t))
    for where, text in texts:
        for reason in textsafety.overclaims(text):
            errors.append("packet request %s: prohibited legal conclusion (%s)" % (where, reason))
    return errors


def _status_checklist(current):
    marks = []
    reached = constants.LEGAL_STATUSES.index(current) if current in constants.LEGAL_STATUSES else 0
    for i, s in enumerate(constants.LEGAL_STATUSES):
        marks.append("[%s] %s" % ("x" if i <= reached else " ", s.replace("_", " ")))
    return marks


def _evidence_label(ev):
    check = ev.get("check")
    suffix = "" if not check or check == "NOT_CHECKED" else "  (mechanical check: %s)" % check
    if ev.get("kind") == "absence":
        return "%s  absence of evidence%s" % (ev.get("ref", ""), suffix)
    if ev.get("kind") == "user-provided":
        return "%s  user-provided: %s%s" % (ev.get("ref", ""), ev.get("path", ""), suffix)
    return "%s  %s:%s-%s  [%s]%s" % (ev.get("ref", ""), ev.get("path"), ev.get("start_line"), ev.get("end_line"), ev.get("kind"), suffix)


def _finding_parts(f, extra, ledger_entry):
    legal = f["legal"]
    decisions = list(legal.get("decisions_needed") or []) + list(extra.get("relevant_decisions") or [])
    policy_refs = list(legal.get("policy_refs") or [])
    policy_refs += ["%s:%s-%s" % (ev["path"], ev["start_line"], ev["end_line"]) for ev in f["evidence"] if ev.get("kind") == "documentation"]
    follow_up = list(extra.get("follow_up") or [])
    if not follow_up:
        follow_up = ["Share this section with counsel and record the outcome with /seaworthy:track (legal status for %s)." % f["id"],
                     "After a decision is received, implement any required changes and re-run /seaworthy:audit to verify them."]
    history = (ledger_entry or {}).get("legal", {}).get("history", [])
    status = f.get("legal_status") or "OPEN"
    context = [f["explanation"], "Potential impact: " + f["impact"]]
    if extra.get("technical_context"):
        context.append(extra["technical_context"])
    return {
        "why": legal.get("why_review") or "Seaworthy classified this finding as %s. The technical evidence is summarized below for counsel's assessment." % legal["classification"],
        "decisions": decisions,
        "policy_refs": policy_refs,
        "follow_up": follow_up,
        "context": context,
        "status": status,
        "history": history,
        "questions": legal.get("questions") or [],
    }


def build(final, request, ledger=None, generated_at=None, page_size="letter"):
    errors = check_request(request, final)
    if errors:
        raise PacketError("\n".join(errors))
    generated_at = generated_at or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    date_text = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    by_id = {f["id"]: f for f in final["findings"]}
    entries = {e["id"]: e for e in (ledger or {}).get("entries", {}).values()}
    findings = [by_id[fid] for fid in request["finding_ids"]]
    per = request.get("per_finding") or {}
    project = _clean(request["project_identifier"])

    doc = pdf.Document(TITLE, page_size=page_size, header="Seaworthy — Attorney Review Packet — %s" % project,
                       footer="Not legal advice. Organizes technical findings and questions for qualified counsel.",
                       creation=generated_at)
    doc.heading(TITLE, level=0)
    rows = [("Seaworthy version", VERSION), ("Date generated", date_text), ("Project identifier", project)]
    if request.get("prepared_for"):
        rows.append(("Prepared for", _clean(request["prepared_for"])))
    rows += [("Source audit run", final["run"]["run_id"]), ("Audit ship decision", final["gate"]["decision"]),
             ("Findings in this packet", str(len(findings)))]
    doc.key_values(rows)
    doc.boxed(constants.PACKET_DISCLAIMER, title="Disclaimer")
    doc.paragraph(HOW_TO_USE, font="F3", size=9.5)
    doc.heading("Review summary", level=2)
    doc.table(["Finding", "Classification", "Severity", "Confidence", "Review status"],
              [[f["id"], f["legal"]["classification"], f["severity"], f["effective_confidence"], (f.get("legal_status") or "OPEN").replace("_", " ")] for f in findings],
              [0.13, 0.33, 0.15, 0.17, 0.22])
    doc.heading("Executive summary", level=1)
    doc.paragraph(_clean(request["executive_summary"]))
    doc.heading("Overall legal / business review summary", level=1)
    doc.paragraph(_clean(request["overall_summary"]))
    doc.boxed(JURISDICTION_NOTE, title="Jurisdiction")

    for f in findings:
        parts = _finding_parts(f, per.get(f["id"], {}), entries.get(f["id"]))
        doc.page_break()
        doc.heading("%s — %s" % (f["id"], _clean(f["title"])), level=1)
        hr = f.get("human_review") or {}
        doc.key_values([
            ("Category", f["legal"]["classification"]),
            ("Domain", catalog.domain_title(f["domain"])),
            ("Severity", f["severity"]),
            ("Confidence", f["effective_confidence"]),
            ("Blocks release", "Yes" if f["release_blocking_effective"] else "No"),
            ("Review status", parts["status"].replace("_", " ")),
            ("Human review", ", ".join(hr.get("types") or []) or "legal"),
        ])
        doc.heading("Why legal review is recommended", level=2)
        doc.paragraph(_clean(parts["why"]))
        doc.heading("Technical evidence (observed by Seaworthy)", level=2)
        for ev in f["evidence"]:
            doc.paragraph(_clean(_evidence_label(ev)), font="F2", size=9, space_after=2)
            if ev.get("kind") == "absence":
                doc.paragraph("Searched: " + _clean(ev.get("searched")), size=9)
            if ev.get("quote"):
                doc.code(_clean(ev["quote"]))
            if ev.get("note"):
                doc.paragraph("Note: " + _clean(ev["note"]), size=9)
        doc.heading("Affected files / components", level=2)
        doc.bullets([_clean(c) for c in f["affected_components"]])
        doc.heading("Technical context", level=2)
        for para in parts["context"]:
            doc.paragraph(_clean(para))
        doc.heading("Relevant project decisions", level=2)
        doc.bullets([_clean(d) for d in parts["decisions"]] or ["None recorded."])
        doc.heading("Relevant policy / documentation references", level=2)
        doc.bullets([_clean(p) for p in parts["policy_refs"]] or ["None identified in the audited scope."])
        doc.heading("Questions for qualified counsel", level=2)
        doc.bullets([_clean(q) for q in parts["questions"]] or ["No specific questions were generated; counsel may identify the relevant questions."], numbered=bool(parts["questions"]))
        doc.heading("Follow-up items", level=2)
        doc.bullets([_clean(x) for x in parts["follow_up"]])
        doc.heading("Review status", level=2)
        doc.paragraph("Current status: %s" % parts["status"].replace("_", " "))
        doc.paragraph("   ".join(_status_checklist(parts["status"])), size=8.5, gray=0.2)
        for ev in parts["history"][-6:]:
            doc.paragraph("%s  %s  (%s)%s" % (ev["at"], ev["status"].replace("_", " "), ev["source"], (": " + _clean(ev["note"])) if ev.get("note") else ""), size=8.5, gray=0.3, space_after=1)
        doc.notes_box("Attorney notes", lines=8)
        doc.notes_box("Decisions / recommendations", lines=6)

    doc.page_break()
    doc.heading("Disclaimer", level=1)
    doc.boxed(constants.PACKET_DISCLAIMER, title="Not legal advice")
    doc.paragraph("Standards, if mentioned, are referenced by identifier only. Seaworthy does not cite statutes, regulations, or cases, and it does not determine whether any requirement applies.", size=9.5)
    doc.paragraph(JURISDICTION_NOTE, size=9.5)
    replaced = len(doc.stats["replaced"])
    if replaced:
        doc.paragraph("Note: %d distinct character(s) could not be represented in this PDF's standard fonts and were replaced with '?'. The Markdown version of this packet preserves the original text." % replaced, font="F3", size=9)
    data = doc.to_bytes()

    md_text = render_markdown(final, request, findings, per, entries, date_text, project)
    return data, md_text, {"pages": len(doc.pages), "findings": [f["id"] for f in findings], "replaced_characters": replaced}


def render_markdown(final, request, findings, per, entries, date_text, project):
    out = ["# %s" % TITLE, "",
           "| | |", "|---|---|",
           "| Seaworthy version | %s |" % VERSION,
           "| Date generated | %s |" % date_text,
           "| Project identifier | %s |" % md(project),
           "| Source audit run | %s |" % final["run"]["run_id"],
           "| Audit ship decision | %s |" % final["gate"]["decision"],
           "", "> **Disclaimer.** " + constants.PACKET_DISCLAIMER, "", HOW_TO_USE, "",
           "## Review summary", "", "| Finding | Classification | Severity | Confidence | Review status |", "|---|---|---|---|---|"]
    for f in findings:
        out.append("| %s | %s | %s | %s | %s |" % (f["id"], f["legal"]["classification"], f["severity"], f["effective_confidence"], f.get("legal_status") or "OPEN"))
    out += ["", "## Executive summary", "", md_block(request["executive_summary"]), "",
            "## Overall legal / business review summary", "", md_block(request["overall_summary"]), "", "> " + JURISDICTION_NOTE, ""]
    for f in findings:
        parts = _finding_parts(f, per.get(f["id"], {}), entries.get(f["id"]))
        out += ["## %s — %s" % (f["id"], md(f["title"])), "",
                "- Category: %s" % f["legal"]["classification"],
                "- Domain: %s" % catalog.domain_title(f["domain"]),
                "- Severity: %s; Confidence: %s; Blocks release: %s" % (f["severity"], f["effective_confidence"], "Yes" if f["release_blocking_effective"] else "No"),
                "- Review status: %s" % parts["status"], "",
                "### Why legal review is recommended", "", md_block(parts["why"]), "",
                "### Technical evidence (observed by Seaworthy)", ""]
        for ev in f["evidence"]:
            out.append("- " + md(_evidence_label(ev)))
            if ev.get("kind") == "absence":
                out.append("  - Searched: " + md(ev.get("searched", "")))
            if ev.get("quote"):
                out += ["", code_block(ev["quote"]), ""]
        out += ["", "### Affected files / components", ""] + ["- `%s`" % md(c) for c in f["affected_components"]]
        out += ["", "### Technical context", ""] + [md_block(p) + "\n" for p in parts["context"]]
        out += ["### Relevant project decisions", ""] + (["- " + md(d) for d in parts["decisions"]] or ["- None recorded."])
        out += ["", "### Relevant policy / documentation references", ""] + (["- " + md(p) for p in parts["policy_refs"]] or ["- None identified in the audited scope."])
        out += ["", "### Questions for qualified counsel", ""] + (["%d. %s" % (i, md(q)) for i, q in enumerate(parts["questions"], 1)] or ["- No specific questions were generated."])
        out += ["", "### Follow-up items", ""] + ["- " + md(x) for x in parts["follow_up"]]
        out += ["", "### Review status", "", " ".join(_status_checklist(parts["status"])), "",
                "### Attorney notes", "", "_(space for counsel's notes)_", "", "### Decisions / recommendations", "", "_(space for decisions)_", ""]
    out += ["---", "", "**Disclaimer.** " + constants.PACKET_DISCLAIMER, ""]
    return "\n".join(out)

"""FLIGHT CHECK - ATTORNEY REVIEW PACKET: assembly, validation, PDF and Markdown rendering.

The packet organizes technical evidence and questions for qualified counsel. Evidence,
severities, classifications, and questions are copied from the finalized audit; the
request file only supplies summaries, the project identifier, and per-finding context.
It may choose and order a finding's audit questions (by number) to leave out repeats,
but it can never add or reword a question.
Nothing here answers a legal question, cites legal authority, or determines compliance.
"""

import datetime

from . import VERSION, catalog, constants, legal, minischema, pdf, textsafety
from .render_md import code_block, md, md_block

TITLE = "FLIGHT CHECK — ATTORNEY REVIEW PACKET"
JURISDICTION_NOTE = (
    "Jurisdiction-specific legal research: UNVERIFIED. Flight Check did not research or apply the law of any "
    "jurisdiction. Whether and how any law, regulation, or contract applies is for qualified counsel to determine."
)
HOW_TO_USE = (
    "Each section separates what Flight Check observed in the project (technical evidence) from the questions "
    "it suggests putting to qualified counsel. Flight Check does not answer those questions. Space is provided "
    "for counsel's notes and for recording decisions. Record decisions you receive with /flight-check:track so "
    "Flight Check can track the resulting implementation work."
)


NO_QUESTIONS = "No specific questions were generated; counsel may identify the relevant questions."
NO_QUESTIONS_BUSINESS = (
    "None. Flight Check classified this as a business decision for the project's owners; the decisions are listed "
    "under Relevant project decisions. Ask counsel about it if any of those choices depends on legal requirements."
)


class PacketError(Exception):
    pass


def _clean(text):
    text, _ = textsafety.redact(textsafety.sanitize(text or ""))
    return text


def check_request(request, final, regs=None):
    errors = ["packet request: %s" % e for e in minischema.validate_def(request, catalog.schema(), "packetRequest")]
    if errors:
        return errors
    item_ids = request.get("item_ids") or []
    if not request["finding_ids"] and not item_ids:
        return ["packet request: there is nothing to put in a packet. " + legal.NO_ISSUES]
    known_items = {i["id"] for i in (regs or {}).get("legal", [])}
    for iid in item_ids:
        if iid not in known_items:
            errors.append("packet request: %s is not a legal review item in this project's state" % iid)
    if len(set(item_ids)) != len(item_ids):
        errors.append("packet request: item_ids contains duplicates")
    by_id = {f["id"]: f for f in final["findings"]}
    for fid in request["finding_ids"]:
        f = by_id.get(fid)
        if f is None:
            errors.append("packet request: %s is not a current finding in this run" % fid)
        elif not f.get("legal"):
            errors.append("packet request: %s has no legal/business classification; the packet is for findings that warrant legal or business review" % fid)
    if len(set(request["finding_ids"])) != len(request["finding_ids"]):
        errors.append("packet request: finding_ids contains duplicates")
    for key, extra in (request.get("per_finding") or {}).items():
        if key not in request["finding_ids"]:
            errors.append("packet request: per_finding has %s, which is not in finding_ids" % key)
            continue
        chosen = extra.get("questions")
        if chosen is None or key not in by_id or not by_id[key].get("legal"):
            continue
        available = len(by_id[key]["legal"].get("questions") or [])
        if not available:
            errors.append("packet request: per_finding.%s.questions selects questions, but the finding has none" % key)
        for n in chosen:
            if n > available:
                errors.append("packet request: per_finding.%s.questions has %d, but the finding has %d question(s)" % (key, n, available))
        if len(set(chosen)) != len(chosen):
            errors.append("packet request: per_finding.%s.questions contains duplicates" % key)
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
    if current == "NOT_APPLICABLE":
        return ["[x] NOT APPLICABLE (recorded by the project owner)"]
    flow = constants.LEGAL_ITEM_FLOW
    position = "AWAITING_COUNSEL" if current == "AWAITING_BUSINESS_DECISION" else current
    reached = flow.index(position) if position in flow else 0
    marks = []
    for i, s in enumerate(flow):
        label = "AWAITING BUSINESS DECISION" if (s == "AWAITING_COUNSEL" and current == "AWAITING_BUSINESS_DECISION") else s.replace("_", " ")
        marks.append("[%s] %s" % ("x" if i <= reached else " ", label))
    return marks


def _evidence_label(ev):
    check = ev.get("check")
    suffix = "" if not check or check == "NOT_CHECKED" else "  (mechanical check: %s)" % check
    if ev.get("kind") == "absence":
        return "%s  absence of evidence%s" % (ev.get("ref", ""), suffix)
    if ev.get("kind") == "user-provided":
        return "%s  user-provided: %s%s" % (ev.get("ref", ""), ev.get("path", ""), suffix)
    return "%s  %s:%s-%s  [%s]%s" % (ev.get("ref", ""), ev.get("path"), ev.get("start_line"), ev.get("end_line"), ev.get("kind"), suffix)


def _finding_parts(f, extra, item):
    legal = f["legal"]
    decisions = list(legal.get("decisions_needed") or []) + list(extra.get("relevant_decisions") or [])
    policy_refs = list(legal.get("policy_refs") or [])
    policy_refs += ["%s:%s-%s" % (ev["path"], ev["start_line"], ev["end_line"]) for ev in f["evidence"] if ev.get("kind") == "documentation"]
    follow_up = list(extra.get("follow_up") or [])
    if not follow_up:
        follow_up = ["Share this section with counsel and record the outcome with /flight-check:track (legal status for %s)." % f["id"],
                     "After a decision is received, implement any required changes and re-run /flight-check:audit to verify them."]
    history = (item or {}).get("history", [])
    questions = list(legal.get("questions") or [])
    omitted = 0
    if extra.get("questions"):
        omitted = len(questions) - len(extra["questions"])
        questions = [questions[n - 1] for n in extra["questions"]]
    status = f.get("legal_status") or (item or {}).get("status") or "NEEDS_REVIEW"
    context = [f["explanation"], "Potential impact: " + f["impact"]]
    if extra.get("technical_context"):
        context.append(extra["technical_context"])
    return {
        "why": legal.get("why_review") or "Flight Check classified this finding as %s. The technical evidence is summarized below for counsel's assessment." % legal["classification"],
        "decisions": decisions,
        "policy_refs": policy_refs,
        "follow_up": follow_up,
        "context": context,
        "status": status,
        "history": history,
        "questions": questions,
        "omitted_questions": omitted,
        "no_questions": NO_QUESTIONS_BUSINESS if legal["classification"] == "BUSINESS DECISION REQUIRED" else NO_QUESTIONS,
    }


def _omitted_note(n):
    return ("%d further question(s) from the audit were left out as repeats of the ones above when this packet "
            "was prepared; the audit report lists all of them." % n)


def _item_evidence(item):
    out = []
    for ev in item.get("evidence") or []:
        if ev.get("kind") == "absence":
            out.append("absence of evidence; searched: %s" % ev.get("searched", ""))
        elif ev.get("path"):
            lines = (":%s-%s" % (ev["start_line"], ev.get("end_line", ev["start_line"]))) if ev.get("start_line") else ""
            out.append("%s%s  [%s]" % (ev["path"], lines, ev.get("kind")))
    return out


def build(final, request, ledger=None, generated_at=None, page_size="letter", regs=None):
    errors = check_request(request, final, regs)
    if errors:
        raise PacketError("\n".join(errors))
    generated_at = generated_at or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    date_text = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    by_id = {f["id"]: f for f in final["findings"]}
    items_by_id = {i["id"]: i for i in (regs or {}).get("legal", [])}
    finding_items = {e["id"]: items_by_id.get((e.get("legal") or {}).get("item"))
                     for e in (ledger or {}).get("entries", {}).values()}
    findings = [by_id[fid] for fid in request["finding_ids"]]
    items = [items_by_id[i] for i in request.get("item_ids") or []]
    per = request.get("per_finding") or {}
    project = _clean(request["project_identifier"])

    doc = pdf.Document(TITLE, page_size=page_size, header="Flight Check — Attorney Review Packet — %s" % project,
                       footer="Not legal advice. Organizes technical findings and questions for qualified counsel.",
                       creation=generated_at, lang=request.get("language") or "en")
    doc.heading(TITLE, level=0)
    rows = [("Flight Check version", VERSION), ("Date generated", date_text), ("Project identifier", project)]
    if request.get("prepared_for"):
        rows.append(("Prepared for", _clean(request["prepared_for"])))
    rows += [("Source audit run", final["run"]["run_id"]), ("Audit ship decision", final["gate"]["decision"]),
             ("Findings in this packet", str(len(findings)))]
    if items:
        rows.append(("Other legal review items", str(len(items))))
    doc.key_values(rows)
    doc.boxed(constants.PACKET_DISCLAIMER, title="Disclaimer")
    doc.paragraph(HOW_TO_USE, font="F3", size=9.5)
    doc.heading("Review summary", level=2)
    doc.table(["Finding", "Classification", "Severity", "Confidence", "Review status"],
              [[f["id"], f["legal"]["classification"], f["severity"], f["effective_confidence"],
                (f.get("legal_status") or "NEEDS_REVIEW").replace("_", " ")] for f in findings]
              + [[i["id"], i.get("classification") or "Not classified", "—", "—", i["status"].replace("_", " ")] for i in items],
              [0.11, 0.35, 0.18, 0.15, 0.21])
    doc.heading("Executive summary", level=1)
    doc.paragraph(_clean(request["executive_summary"]))
    doc.heading("Overall legal / business review summary", level=1)
    doc.paragraph(_clean(request["overall_summary"]))
    doc.boxed(JURISDICTION_NOTE, title="Jurisdiction")

    for f in findings:
        parts = _finding_parts(f, per.get(f["id"], {}), finding_items.get(f["id"]))
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
        doc.heading("Technical evidence (observed by Flight Check)", level=2)
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
        doc.bullets([_clean(q) for q in parts["questions"]] or [parts["no_questions"]], numbered=bool(parts["questions"]))
        if parts["omitted_questions"]:
            doc.paragraph(_omitted_note(parts["omitted_questions"]), font="F3", size=9)
        doc.heading("Follow-up items", level=2)
        doc.bullets([_clean(x) for x in parts["follow_up"]])
        doc.heading("Review status", level=2)
        doc.paragraph("Current status: %s" % parts["status"].replace("_", " "))
        doc.paragraph("   ".join(_status_checklist(parts["status"])), size=8.5, gray=0.2)
        for ev in parts["history"][-6:]:
            doc.paragraph("%s  %s  (%s)%s" % (ev["at"], ev["status"].replace("_", " "), ev["source"], (": " + _clean(ev["note"])) if ev.get("note") else ""), size=8.5, gray=0.3, space_after=1)
        doc.notes_box("Attorney notes", lines=8)
        doc.notes_box("Decisions / recommendations", lines=6)

    for item in items:
        doc.page_break()
        doc.heading("%s — %s" % (item["id"], _clean(item["title"])), level=1)
        doc.key_values([
            ("Category", _clean(item["category"])),
            ("Classification", item.get("classification") or "Not classified"),
            ("Review status", item["status"].replace("_", " ")),
            ("Raised by", _clean(item["trigger"])),
            ("Jurisdiction", _clean(item.get("jurisdiction")) or "Not determined; counsel should identify the relevant jurisdictions"),
        ])
        doc.heading("Why legal review is recommended", level=2)
        doc.paragraph(_clean(item["description"]))
        doc.heading("Evidence references", level=2)
        doc.bullets([_clean(x) for x in _item_evidence(item)] or ["No file evidence was recorded for this item."])
        doc.heading("Questions for qualified counsel", level=2)
        questions = [_clean(q) for q in item.get("questions") or []]
        doc.bullets(questions or [NO_QUESTIONS], numbered=bool(questions))
        doc.heading("Review status", level=2)
        doc.paragraph("Current status: %s" % item["status"].replace("_", " "))
        doc.paragraph("   ".join(_status_checklist(item["status"])), size=8.5, gray=0.2)
        for ev in item.get("history", [])[-6:]:
            doc.paragraph("%s  %s  (%s)%s" % (ev["at"], ev["status"].replace("_", " "), ev["source"], (": " + _clean(ev["note"])) if ev.get("note") else ""), size=8.5, gray=0.3, space_after=1)
        doc.notes_box("Attorney notes", lines=8)
        doc.notes_box("Decisions / recommendations", lines=6)

    doc.page_break()
    doc.heading("Disclaimer", level=1)
    doc.boxed(constants.PACKET_DISCLAIMER, title="Not legal advice")
    doc.paragraph("Standards, if mentioned, are referenced by identifier only. Flight Check does not cite statutes, regulations, or cases, and it does not determine whether any requirement applies.", size=9.5)
    doc.paragraph(JURISDICTION_NOTE, size=9.5)
    replaced = len(doc.stats["replaced"])
    if replaced:
        doc.paragraph("Note: %d distinct character(s) could not be represented in this PDF's standard fonts and were replaced with '?'. The Markdown version of this packet preserves the original text." % replaced, font="F3", size=9)
    data = doc.to_bytes()

    md_text = render_markdown(final, request, findings, per, finding_items, date_text, project, items)
    return data, md_text, {"pages": len(doc.pages), "findings": [f["id"] for f in findings], "items": [i["id"] for i in items],
                           "item_for_finding": {f["id"]: (finding_items.get(f["id"]) or {}).get("id") for f in findings},
                           "replaced_characters": replaced}


def render_markdown(final, request, findings, per, finding_items, date_text, project, items=()):
    out = ["# %s" % TITLE, "",
           "| | |", "|---|---|",
           "| Flight Check version | %s |" % VERSION,
           "| Date generated | %s |" % date_text,
           "| Project identifier | %s |" % md(project),
           "| Source audit run | %s |" % final["run"]["run_id"],
           "| Audit ship decision | %s |" % final["gate"]["decision"],
           "", "> **Disclaimer.** " + constants.PACKET_DISCLAIMER, "", HOW_TO_USE, "",
           "## Review summary", "", "| Finding | Classification | Severity | Confidence | Review status |", "|---|---|---|---|---|"]
    for f in findings:
        out.append("| %s | %s | %s | %s | %s |" % (f["id"], f["legal"]["classification"], f["severity"], f["effective_confidence"], f.get("legal_status") or "NEEDS_REVIEW"))
    for i in items:
        out.append("| %s | %s | — | — | %s |" % (i["id"], i.get("classification") or "Not classified", i["status"]))
    out += ["", "## Executive summary", "", md_block(request["executive_summary"]), "",
            "## Overall legal / business review summary", "", md_block(request["overall_summary"]), "", "> " + JURISDICTION_NOTE, ""]
    for f in findings:
        parts = _finding_parts(f, per.get(f["id"], {}), finding_items.get(f["id"]))
        out += ["## %s — %s" % (f["id"], md(f["title"])), "",
                "- Category: %s" % f["legal"]["classification"],
                "- Domain: %s" % catalog.domain_title(f["domain"]),
                "- Severity: %s; Confidence: %s; Blocks release: %s" % (f["severity"], f["effective_confidence"], "Yes" if f["release_blocking_effective"] else "No"),
                "- Review status: %s" % parts["status"], "",
                "### Why legal review is recommended", "", md_block(parts["why"]), "",
                "### Technical evidence (observed by Flight Check)", ""]
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
        out += ["", "### Questions for qualified counsel", ""] + (["%d. %s" % (i, md(q)) for i, q in enumerate(parts["questions"], 1)] or ["- " + parts["no_questions"]])
        if parts["omitted_questions"]:
            out += ["", "_%s_" % _omitted_note(parts["omitted_questions"])]
        out += ["", "### Follow-up items", ""] + ["- " + md(x) for x in parts["follow_up"]]
        out += ["", "### Review status", "", " ".join(_status_checklist(parts["status"])), "",
                "### Attorney notes", "", "_(space for counsel's notes)_", "", "### Decisions / recommendations", "", "_(space for decisions)_", ""]
    for i in items:
        out += ["## %s — %s" % (i["id"], md(i["title"])), "",
                "- Category: %s" % md(i["category"]),
                "- Classification: %s" % (i.get("classification") or "Not classified"),
                "- Review status: %s" % i["status"],
                "- Raised by: %s" % md(i["trigger"]),
                "- Jurisdiction: %s" % (md(i.get("jurisdiction") or "") or "Not determined; counsel should identify the relevant jurisdictions"), "",
                "### Why legal review is recommended", "", md_block(i["description"]), "",
                "### Evidence references", ""] + (["- " + md(x) for x in _item_evidence(i)] or ["- No file evidence was recorded for this item."])
        out += ["", "### Questions for qualified counsel", ""] + (["%d. %s" % (n, md(q)) for n, q in enumerate(i.get("questions") or [], 1)] or ["- " + NO_QUESTIONS])
        out += ["", "### Review status", "", " ".join(_status_checklist(i["status"])), "",
                "### Attorney notes", "", "_(space for counsel's notes)_", "", "### Decisions / recommendations", "", "_(space for decisions)_", ""]
    out += ["---", "", "**Disclaimer.** " + constants.PACKET_DISCLAIMER, ""]
    return "\n".join(out)

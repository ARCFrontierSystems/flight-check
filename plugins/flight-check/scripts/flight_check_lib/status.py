"""Project status and report views (docs/design/project-state.md, section 14).

Every view is built from the recorded state, the ledger, and (when the audit root is given) the current files. Nothing
here infers, guesses, or changes anything: a question the records cannot answer is reported as unknown.
"""

import datetime

from . import constants, freshness, legal, plain, profile, registers, unblock, watch
from .render_md import md

REPORT_VERSION = "1.0"
VIEWS = ("status", "unblock", "profile", "requirements", "decisions", "risks", "legal", "findings", "evidence", "changes", "readiness")
_SEV = {s: i for i, s in enumerate(constants.SEVERITIES)}
_OPEN_RISK = ("OPEN", "REOPENED", "UNDER_REVIEW", "MITIGATION_PLANNED")


def _entries(st):
    return sorted((st.ledger or {}).get("entries", {}).values(), key=lambda e: e["id"])


def _changes(st, root):
    if not root:
        return {"available": False, "reason": "give --root to compare the current files with the last audit"}
    out = freshness.changes(root, st)
    out["available"] = True
    return out


def _readiness(st, ch):
    stamps = st.project.get("readiness") or []
    latest = stamps[-1] if stamps else None
    if not latest:
        note = "No finalized audit has produced a release-readiness decision yet."
    elif not ch["available"]:
        note = "Whether files changed since this decision is unknown here (%s)." % ch["reason"]
    elif ch["changed_files"]:
        note = ("Historical: %d file(s) changed since run %s, so this decision no longer describes the current files."
                % (ch["changed_files"], latest["run_id"]))
    else:
        note = "No file changed since run %s." % latest["run_id"]
    return {"latest": latest, "historical": bool(latest and ch["available"] and ch["changed_files"]), "note": note}


def _requirements_attention(regs):
    out = []
    for r in regs["requirements"]:
        if r["status"] in ("SUPERSEDED", "REJECTED"):
            continue
        current = r["status"] == "VERIFIED" and (r.get("verification") or {}).get("state") == "current"
        if not current:
            out.append({"id": r["id"], "title": r["title"], "criticality": r["criticality"], "status": r["status"],
                        "verification": (r.get("verification") or {}).get("state", "unverified")})
    out.sort(key=lambda r: (r["criticality"] != "release-critical", r["status"] != "BLOCKED", r["id"]))
    return out


def build_status(st, root, now_iso):
    ch = _changes(st, root)
    p, regs = st.project, st.registers or {}
    facts = [f for f in p["profile"]["facts"] if f["status"] == "current"]
    known = [{"id": f["id"], "aspect": f["aspect"], "value": f["value"], "origin": f["origin"]}
             for f in facts if f.get("presence", "present") == "present"]
    unknown = sorted({f["aspect"] for f in facts if f.get("presence") == "unclear"})
    apps = {"applicable": [], "not_applicable": [], "uncertain": []}
    for a in p["applicability"]:
        apps.setdefault(profile.effective(a), []).append(a["domain"])
    entries = _entries(st)
    open_f = [e for e in entries if e["status"] in constants.OPEN_STATUSES]
    by_sev = {s: sum(1 for e in open_f if e["severity"] == s) for s in constants.SEVERITIES}
    top = sorted((e for e in open_f if e["severity"] in ("CRITICAL", "HIGH")), key=lambda e: (_SEV[e["severity"]], e["id"]))
    risks = sorted((r for r in regs.get("risks", []) if r["status"] in _OPEN_RISK),
                   key=lambda r: (_SEV.get(r["severity"], 9), r["id"]))
    decisions = [{"id": d["id"], "title": d["title"], "status": d["status"]}
                 for d in regs.get("decisions", []) if d["status"] in ("NEEDS_REVIEW", "PROPOSED")]
    items = [{"id": i["id"], "title": i["title"], "status": i["status"], "classification": i.get("classification")}
             for i in legal.open_items(regs)] if regs else []
    attention = [{"kind": "profile", "id": f["id"], "aspect": f["aspect"], "value": f["value"],
                  "correction": next((c for c in reversed(f.get("corrections") or []) if c["active"]), {}).get("assertion")}
                 for f in facts if f.get("contradiction")]
    attention += [{"kind": "applicability", "id": "domain:" + a["domain"], "evidence": a["classification"],
                   "override": (a.get("override") or {}).get("classification")}
                  for a in p["applicability"] if a.get("contradiction")]
    out = {
        "project": {"name": p["identity"]["name"], "project_id": p["identity"]["project_id"], "revision": p["revision"]},
        "contradictions": attention,
        "profile": {"known": known[:15], "known_total": len(known), "unknown_aspects": unknown},
        "applicability": apps,
        "readiness": _readiness(st, ch),
        "changes": ({"available": True, "changed_files": ch["changed_files"], "reassess_domains": ch["reassess_domains"],
                     "carried_forward_domains": ch["carried_forward_domains"], "widened": ch["widened"],
                     "stale_evidence": len(ch["stale_evidence"])} if ch["available"] else ch),
        "findings": {"open_by_severity": by_sev, "open_total": len(open_f),
                     "top": [{"id": e["id"], "severity": e["severity"], "status": e["status"], "domain": e["domain"],
                              "title": e["title"]} for e in top[:8]],
                     "accepted": [e["id"] for e in entries if e["status"] == "ACCEPTED_RISK"],
                     "regressions": [e["id"] for e in open_f if e["status"] == "REGRESSION"]},
        "risks": [{"id": r["id"], "severity": r["severity"], "status": r["status"], "title": r["title"]} for r in risks[:8]],
        "requirements": _requirements_attention(regs)[:10] if regs else [],
        "decisions": {"needs_attention": decisions,
                      "triggered_by_changes": ch.get("decisions_to_review", []) if ch["available"] else []},
        "legal": {"open": items, "message": None if items else legal.NO_ISSUES},
    }
    out["unblock"] = unblock.items(st, root, _date(now_iso))
    out["reminders"] = watch.load_reminders(root) if root else []
    out["next_steps"] = _next_steps(out)
    return out


def _date(now_iso):
    return datetime.date(int(now_iso[0:4]), int(now_iso[5:7]), int(now_iso[8:10]))


def _next_steps(s):
    steps = []
    if not s["readiness"]["latest"]:
        steps.append("Run /flight-check:audit to establish a baseline.")
    elif s["readiness"]["historical"]:
        steps.append("Run /flight-check:audit --changed to reassess the areas affected by the changes.")
    if s["findings"]["top"]:
        steps.append("Fix or decide on the open CRITICAL and HIGH findings (/flight-check:remediate FC-..., or "
                     "/flight-check:track to accept a risk).")
    if s["legal"]["open"]:
        steps.append("Review the open legal questions; /flight-check:legal-packet prepares a packet for counsel.")
    if s.get("contradictions"):
        steps.append("Review the corrections and overrides that new evidence contradicts (/flight-check:track).")
    if s["decisions"]["needs_attention"] or s["decisions"]["triggered_by_changes"]:
        steps.append("Review the decisions marked for review (/flight-check:track).")
    if any(r["criticality"] == "release-critical" for r in s["requirements"]):
        steps.append("Link release-critical requirements to controls an audit can verify, then reassess.")
    return steps


def build(view, st, root, now_iso, record_id=None):
    if view == "status":
        data = build_status(st, root, now_iso)
    elif view == "unblock":
        latest = (st.project.get("readiness") or [None])[-1]
        data = {"decision": latest, "items": unblock.items(st, root, _date(now_iso))}
    elif view == "profile":
        p = st.project
        data = {"facts": [{k: f.get(k) for k in ("id", "aspect", "value", "origin", "source", "presence", "last_observed")}
                          | {"evidence": [r.get("path") or ("searched: " + r.get("searched", "")) for r in f["evidence"]],
                             "corrected": any(c["active"] for c in f.get("corrections") or []),
                             "contradiction": bool(f.get("contradiction"))}
                          for f in p["profile"]["facts"] if f["status"] == "current"],
                "applicability": [{"domain": a["domain"], "classification": a["classification"], "effective": profile.effective(a),
                                   "override": (a.get("override") or {}).get("classification"), "reasoning": a["reasoning"]}
                                  for a in p["applicability"]]}
    elif view in ("requirements", "decisions", "risks", "legal"):
        kind = {"requirements": "requirement", "decisions": "decision", "risks": "risk", "legal": "legal"}[view]
        recs = (st.registers or {}).get(registers.KINDS[kind]["key"], [])
        if record_id:
            recs = [r for r in recs if r["id"] == record_id]
            if not recs:
                raise registers.RegisterError("no %s record %s" % (kind, record_id))
            data = {"record": recs[0]}
        else:
            data = {"records": [registers.summary_row(kind, r) for r in recs]}
            if view == "legal" and not legal.open_items(st.registers or {"legal": []}):
                data["message"] = legal.NO_ISSUES
    elif view == "findings":
        entries = _entries(st)
        if record_id:
            match = [e for e in entries if e["id"] == record_id]
            if not match:
                raise registers.RegisterError("no finding %s" % record_id)
            e = match[0]
            data = {"finding": {k: e.get(k) for k in ("id", "rule", "domain", "title", "severity", "status", "first_seen_run",
                                                      "last_seen_run", "regression", "superseded_by", "accepted_risk")},
                    "history": e["history"], "evidence_files": sorted(e.get("evidence_files") or {})}
        else:
            data = {"findings": [{"id": e["id"], "severity": e["severity"], "status": e["status"], "domain": e["domain"],
                                  "title": e["title"], "first_seen_run": e["first_seen_run"], "last_seen_run": e["last_seen_run"],
                                  "changes": len(e["history"]), "regression": bool(e.get("regression"))} for e in entries]}
    elif view == "evidence":
        ch = _changes(st, root)
        if not ch["available"]:
            raise registers.RegisterError(ch["reason"])
        data = {"baseline": ch["baseline"], "changed_files": ch["changed_files"], "stale_or_missing": ch["stale_evidence"]}
    elif view == "changes":
        ch = _changes(st, root)
        if not ch["available"]:
            raise registers.RegisterError(ch["reason"])
        data = ch
    elif view == "readiness":
        ch = _changes(st, root)
        data = dict(_readiness(st, ch), history=(st.project.get("readiness") or [])[-20:])
    else:
        raise ValueError(view)
    return {"report_version": REPORT_VERSION, "view": view, "generated_at": now_iso,
            "project": {"name": st.project["identity"]["name"], "project_id": st.project["identity"]["project_id"],
                        "revision": st.project["revision"]},
            "scope_note": "Built from Flight Check's records and evidence; it is not a certification or legal advice.",
            "data": data}


# --- Markdown -----------------------------------------------------------------------------------------------------

def _lines(items, fmt, empty):
    return [fmt(i) for i in items] if items else [empty]


def render_status(doc):
    s, proj = doc["data"], doc["project"]
    r = s["readiness"]
    out = ["# Flight Check status: %s" % md(proj["name"]), "",
           "_Revision %d, generated %s. %s_" % (proj["revision"], doc["generated_at"], doc["scope_note"]), ""]
    out.append("**Release readiness:** %s" % (("%s _(Flight Check decision: %s, run %s, %s)_" % (
        plain.decision(r["latest"]["decision"]), r["latest"]["decision"], r["latest"]["run_id"], r["latest"]["at"]))
        if r["latest"] else "not checked yet. Type /flight-check:audit for the first check."))
    out += ["", md(r["note"]), "", "## What this project is"]
    out += _lines(s["profile"]["known"], lambda f: "- %s: %s (%s)" % (md(f["aspect"]), md(f["value"]), f["origin"]),
                  "- No observations recorded yet.")
    if s["profile"]["known_total"] > len(s["profile"]["known"]):
        out.append("- ... %d more (see the profile report)" % (s["profile"]["known_total"] - len(s["profile"]["known"])))
    out.append("- **Unknown:** %s" % (", ".join(md(a) for a in s["profile"]["unknown_aspects"]) or "nothing recorded as unclear"))
    apps = s["applicability"]
    out.append("- **Domains:** %d applicable, %d not applicable, %d uncertain%s" % (
        len(apps.get("applicable", [])), len(apps.get("not_applicable", [])), len(apps.get("uncertain", [])),
        (" (uncertain: %s)" % ", ".join(apps["uncertain"])) if apps.get("uncertain") else ""))
    out += ["", "## What changed"]
    c = s["changes"]
    if c["available"]:
        out.append("- %d file(s) changed since the last audit." % c["changed_files"])
        if c["changed_files"] or c["reassess_domains"]:
            out.append("- Needs reassessment: %s%s" % (", ".join(c["reassess_domains"]) or "nothing",
                                                       " (widened: the effect of a change could not be traced)" if c["widened"] else ""))
        out.append("- Stored conclusions resting on stale or missing evidence: %d" % c["stale_evidence"])
    else:
        out.append("- Unknown: %s." % md(c["reason"]))
    f = s["findings"]
    out += ["", "## Most important open issues", "",
            "Open findings: %d (%s)." % (f["open_total"], ", ".join("%s %d" % (k, v) for k, v in f["open_by_severity"].items() if v) or "none"), ""]
    out += _lines(f["top"], lambda e: "- **%s** [%s, %s] %s" % (e["id"], e["severity"], e["status"], md(e["title"])),
                  "- No open CRITICAL or HIGH findings.")
    if f["regressions"]:
        out.append("- Regressions: %s" % ", ".join(f["regressions"]))
    if s["risks"]:
        out += ["", "Open risks in the register:"]
        out += ["- **%s** [%s, %s] %s" % (x["id"], x["severity"], x["status"], md(x["title"])) for x in s["risks"]]
    if f["accepted"]:
        out += ["", "Accepted risks: %s" % ", ".join(f["accepted"])]
    if s.get("contradictions"):
        out += ["", "## Your corrections contradicted by new evidence"]
        for c in s["contradictions"]:
            if c["kind"] == "profile":
                out.append("- **%s** %s: you recorded \"%s\", but the latest audit observed %s" % (
                    c["id"], md(c["aspect"]), c.get("correction") or "a correction", md(c["value"])))
            else:
                out.append("- **%s**: you declared it %s, but the evidence says %s" % (
                    md(c["id"]), (c.get("override") or "").replace("_", " "), c["evidence"].replace("_", " ")))
    out += ["", "## Requirements not verified"]
    out += _lines(s["requirements"], lambda x: "- **%s** (%s, %s) %s" % (x["id"], x["criticality"], x["status"], md(x["title"])),
                  "- None.")
    out += ["", "## Decisions needing attention"]
    d = s["decisions"]
    out += _lines(d["needs_attention"], lambda x: "- **%s** (%s) %s" % (x["id"], x["status"], md(x["title"])), "- None marked for review.")
    for t in d["triggered_by_changes"]:
        out.append("- **%s** %s: review triggers matched %s" % (t["decision"], md(t["title"]), ", ".join(md(p) for p in t["triggered_by"])))
    out += ["", "## Legal questions"]
    out += _lines(s["legal"]["open"], lambda x: "- **%s** (%s) %s" % (x["id"], x["status"], md(x["title"])),
                  md(s["legal"]["message"] or ""))
    if s.get("unblock"):
        out += ["", "## What stands between this project and release", ""] + _unblock_lines(s["unblock"])
    if s.get("reminders"):
        out += ["", "## Reminders you set", ""]
        out += ["- %s%s%s" % (md(r["id"]), (" (on %s)" % r["on"]) if r.get("on") else " (next time)",
                              (": " + md(r["note"])) if r.get("note") else "") for r in s["reminders"]]
    out += ["", "## Next steps"]
    out += _lines(s["next_steps"], lambda x: "- " + x, "- Nothing pending in Flight Check's records.")
    out.append("")
    return "\n".join(out)


LATER_NOTE = ("Not now? Say \"remind me later\" and Flight Check will bring it up again when you come back. You can keep "
              "working in the meantime; until an item is handled, it still counts against a release.")


def _unblock_lines(items):
    out = []
    groups = [("I can fix these for you (I will ask once before changing anything)", [i for i in items if i.get("who") == "claude"]),
              ("These need you", [i for i in items if i.get("who") != "claude"])]
    for heading, group in groups:
        if not group:
            continue
        out += ["**%s:**" % heading, ""]
        for i in group:
            out.append("- %s %s%s" % (md(i["problem"]), md(i["action"]), (" _(%s)_" % md(i["id"])) if i.get("id") else ""))
            if i.get("needs"):
                out.append("  - What counts as proof: %s" % md(i["needs"]))
            for c in i.get("commands") or []:
                label, _, cmd = c.partition(": ") if ": " in c and not c.startswith("mkdir") else ("", "", c)
                out.append("  - %s`%s`" % ((md(label) + ": ") if label else "", cmd.replace("`", "'")))
        out.append("")
    return out + [LATER_NOTE]


def _table(rows, cols):
    out = ["| " + " | ".join(c for c, _ in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(md(str(fn(r))) for _, fn in cols) + " |")
    return out


def render_markdown(doc):
    if doc["view"] == "status":
        return render_status(doc)
    d, title = doc["data"], doc["view"].replace("-", " ").capitalize()
    out = ["# Flight Check %s report: %s" % (title.lower(), md(doc["project"]["name"])), "",
           "_Revision %d, generated %s. %s_" % (doc["project"]["revision"], doc["generated_at"], doc["scope_note"]), ""]
    if "record" in d or "finding" in d:
        rec = d.get("record") or d["finding"]
        for k, v in sorted(rec.items()):
            if k != "history" and v not in (None, [], {}):
                out.append("- **%s:** %s" % (k, md(v if isinstance(v, str) else str(v))))
        out += ["", "## History"]
        for ev in d.get("history") or rec.get("history") or []:
            out.append("- %s %s (%s)%s" % (ev.get("at", ""), ev.get("status", ev.get("event", "")), ev.get("source", ""),
                                           (": " + md(ev["note"])) if ev.get("note") else ""))
    elif doc["view"] == "profile":
        out += _table(d["facts"], [("ID", lambda f: f["id"]), ("Aspect", lambda f: f["aspect"]), ("Value", lambda f: f["value"]),
                                   ("Origin", lambda f: f["origin"] + (" (corrected)" if f["corrected"] else "")
                                    + (", contradicted by new evidence" if f["contradiction"] else "")),
                                   ("Evidence", lambda f: ", ".join(f["evidence"][:3]))])
        out += ["", "## Applicability", ""]
        out += _table(d["applicability"], [("Domain", lambda a: a["domain"]), ("Evidence says", lambda a: a["classification"]),
                                           ("Owner override", lambda a: a["override"] or ""), ("Used", lambda a: a["effective"])])
    elif "records" in d:
        out += _table(d["records"], [("ID", lambda r: r["id"]), ("Status", lambda r: r["status"]), ("Title", lambda r: r["title"]),
                                     ("Source", lambda r: r["source"]),
                                     ("Links", lambda r: ", ".join(i for v in r["links"].values() for i in v))])
        if d.get("message"):
            out += ["", md(d["message"])]
    elif doc["view"] == "findings":
        out += _table(d["findings"], [("ID", lambda e: e["id"]), ("Severity", lambda e: e["severity"]), ("Status", lambda e: e["status"]),
                                      ("Title", lambda e: e["title"]), ("First seen", lambda e: e["first_seen_run"]),
                                      ("Last seen", lambda e: e["last_seen_run"])])
    elif doc["view"] in ("evidence", "changes"):
        out.append("Compared with run %s (%s). Changed files: %d." % (d["baseline"].get("run_id") or "none", d["baseline"]["kind"], d["changed_files"]))
        out += ["", "## Conclusions resting on stale or missing evidence", ""]
        out += _lines(d.get("stale_or_missing", d.get("stale_evidence")) or [],
                      lambda s: "- %s %s: %s%s" % (s["kind"], s["id"], s["state"], (" (via %s)" % ", ".join(s["because"])) if s.get("because") else ""),
                      "- None.")
        if doc["view"] == "changes":
            out += ["", "## Files", ""] + _lines(d["files"], lambda f: "- %s (%s)" % (md(f["path"]), f["change"]), "- None.")
            out += ["", "Reassess: %s. Carried forward: %s." % (", ".join(d["reassess_domains"]) or "nothing",
                                                                ", ".join(d["carried_forward_domains"]) or "nothing")]
            out += ["- " + md(r) for r in d["reasons"]]
    elif doc["view"] == "unblock":
        dec = d["decision"]
        out.append("%s" % (("%s _(Flight Check decision: %s, run %s, %s)_" % (plain.decision(dec["decision"]), dec["decision"],
                                                                            dec["run_id"], dec["at"])) if dec else "Not checked yet."))
        out += [""] + (_unblock_lines(d["items"]) if d["items"] else ["Nothing in Flight Check's records stands between this project and release."])
    elif doc["view"] == "readiness":
        out += [md(d["note"]), ""]
        out += _table(list(reversed(d["history"])), [("At", lambda s: s["at"]), ("Decision", lambda s: s["decision"]),
                                                     ("Run", lambda s: s["run_id"]), ("Commit", lambda s: s.get("commit", ""))])
    out.append("")
    return "\n".join(out)

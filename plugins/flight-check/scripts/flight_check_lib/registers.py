"""Requirements, decisions, and risks (docs/design/project-state.md, section 7).

Records live in registers.json. Flight Check may propose; only the user accepts, decides, accepts a risk, or closes
a record. Status changes that rest on evidence (a requirement becoming VERIFIED, a risk becoming MITIGATED or
RESOLVED because its findings were verified fixed) are made from finalized audits, never from a user's say-so alone.
"""

import re

from . import catalog, constants, textsafety

KINDS = {
    "requirement": {"key": "requirements", "prefix": "REQ"},
    "decision": {"key": "decisions", "prefix": "DEC"},
    "risk": {"key": "risks", "prefix": "RISK"},
    "legal": {"key": "legal", "prefix": "LR"},
}

# Who may move a record into each status. "evidence" means only a finalized audit (see apply_assessment).
TRANSITIONS = {
    "requirement": {"PROPOSED": {"user", "flight-check"}, "ACCEPTED": {"user"}, "IMPLEMENTED": {"user", "flight-check"},
                    "VERIFIED": {"evidence"}, "BLOCKED": {"user"}, "SUPERSEDED": {"user"}, "REJECTED": {"user"}},
    "decision": {"PROPOSED": {"user", "flight-check"}, "ACCEPTED": {"user"}, "SUPERSEDED": {"user"},
                 "REVERSED": {"user"}, "NEEDS_REVIEW": {"user", "flight-check"}},
    "risk": {"OPEN": {"user", "flight-check"}, "REOPENED": {"user", "flight-check"}, "UNDER_REVIEW": {"user"},
             "MITIGATION_PLANNED": {"user"}, "ACCEPTED": {"user"}, "MITIGATED": {"user", "evidence"},
             "RESOLVED": {"user", "evidence"}},
    # Flight Check never marks a legal question reviewed, resolved, or not applicable.
    "legal": {"NEEDS_REVIEW": {"user", "flight-check"}, "AWAITING_BUSINESS_DECISION": {"user"},
              "AWAITING_COUNSEL": {"user"}, "ACTION_REQUIRED": {"user"}, "REVIEWED": {"user"}, "RESOLVED": {"user"},
              "NOT_APPLICABLE": {"user"}},
}
INITIAL = {"requirement": {"user": "ACCEPTED", "flight-check": "PROPOSED"},
           "decision": {"user": "ACCEPTED", "flight-check": "PROPOSED"},
           "risk": {"user": "OPEN", "flight-check": "OPEN"},
           "legal": {"user": "NEEDS_REVIEW", "flight-check": "NEEDS_REVIEW"}}

LINK_PATTERNS = {"findings": r"FC-[0-9]{4,}", "requirements": r"REQ-[0-9]{4,}", "decisions": r"DEC-[0-9]{4,}",
                 "risks": r"RISK-[0-9]{4,}", "legal": r"LR-[0-9]{4,}", "profile": r"PF-[0-9]{4,}"}


class RegisterError(Exception):
    pass


def _clean(text, limit):
    return textsafety.redact(text)[0][:limit] if text else text


def _event(now_iso, status, source, note=None, by=None, run_id=None):
    ev = {"at": now_iso, "status": status, "source": source}
    if note:
        ev["note"] = _clean(note, 4000)
    if by:
        ev["by"] = by[:200]
    if run_id:
        ev["run_id"] = run_id
    return ev


def find(registers, record_id):
    for kind, spec in KINDS.items():
        if record_id.startswith(spec["prefix"] + "-"):
            for rec in registers[spec["key"]]:
                if rec["id"] == record_id:
                    return kind, rec
    raise RegisterError("no record %s" % record_id)


def _known_ids(project, registers, led):
    ids = set()
    for spec in KINDS.values():
        ids.update(r["id"] for r in registers[spec["key"]])
    ids.update(r["id"] for r in registers.get("legal") or [])
    ids.update(f["id"] for f in project["profile"]["facts"])
    ids.update(e["id"] for e in (led or {}).get("entries", {}).values())
    return ids


def parse_links(values, project, registers, led):
    """['FC-0003', 'DEC-0001', 'control:PRIV-DELETION'] -> links dict; every target must exist."""
    links, controls = {}, []
    known = _known_ids(project, registers, led)
    control_ids = set(catalog.control_by_id())
    for raw in values or []:
        for value in [v.strip() for v in raw.split(",") if v.strip()]:
            if value.startswith("control:"):
                cid = value[len("control:"):]
                if cid not in control_ids:
                    raise RegisterError("unknown control %s" % cid)
                controls.append(cid)
                continue
            for key, pattern in LINK_PATTERNS.items():
                if re.fullmatch(pattern, value):
                    if value not in known:
                        raise RegisterError("cannot link to %s: no such record" % value)
                    links.setdefault(key, []).append(value)
                    break
            else:
                raise RegisterError("cannot link to %r (use FC-, REQ-, DEC-, RISK-, LR-, PF- ids or control:<id>)" % value)
    return links, controls


def _merge_links(rec, links, controls):
    current = rec.setdefault("links", {})
    for key, values in links.items():
        current[key] = sorted(set(current.get(key) or []) | set(values))
    if controls:
        rec["controls"] = sorted(set(rec.get("controls") or []) | set(controls))


FIELDS = {
    "requirement": {"required": ("title", "description", "category", "criticality"),
                    "optional": ("applicability", "components")},
    "decision": {"required": ("title", "context", "decision"),
                 "optional": ("rationale", "alternatives", "owner", "date", "review_triggers", "assertions")},
    "risk": {"required": ("title", "description", "domain", "severity"),
             "optional": ("confidence", "impact", "likelihood", "owner", "mitigation_plan", "next_review")},
    "legal": {"required": ("title", "category", "description", "trigger"),
              "optional": ("classification", "confidence", "jurisdiction", "jurisdiction_unknown", "questions", "owner",
                           "review_date", "resolution_notes", "references")},
}
LIMITS = {"title": 300, "description": 4000, "context": 4000, "decision": 4000, "rationale": 4000, "alternatives": 4000,
          "owner": 200, "impact": 2000, "likelihood": 1000, "mitigation_plan": 4000, "applicability": 300,
          "next_review": 300, "category": 120, "trigger": 1000, "jurisdiction": 300, "resolution_notes": 4000}


def _apply_fields(kind, rec, fields):
    for name, value in fields.items():
        if value is None:
            continue
        if name not in FIELDS[kind]["required"] + FIELDS[kind]["optional"]:
            raise RegisterError("%s records have no field %s" % (kind, name))
        if isinstance(value, bool):
            rec[name] = value
        elif isinstance(value, list):
            rec[name] = [_clean(v, 1000 if name == "questions" else 500) for v in value][:20]
        elif name in LIMITS:
            rec[name] = _clean(value, LIMITS[name])
        else:
            rec[name] = value


def add(kind, project, registers, led, fields, source, now_iso, links=None, by=None, note=None):
    """Create a record. User records start ACCEPTED (requirements, decisions) or OPEN (risks); Flight Check's
    suggestions start PROPOSED (requirements, decisions) and are labelled inferred."""
    spec = KINDS[kind]
    missing = [f for f in FIELDS[kind]["required"] if not fields.get(f)]
    if missing:
        raise RegisterError("a %s needs %s" % (kind, ", ".join(missing)))
    rid = "%s-%04d" % (spec["prefix"], registers["next_ids"][spec["prefix"]])
    status = INITIAL[kind][source]
    rec = {"id": rid, "status": status, "origin": "declared" if source == "user" else "inferred", "source": source,
           "created_at": now_iso, "updated_at": now_iso, "links": {}, "evidence": [],
           "history": [_event(now_iso, status, source, note or ("suggested by Flight Check" if source != "user" else None), by)]}
    if kind == "requirement":
        rec["verification"] = {"state": "unverified"}
    if kind == "risk":
        rec["first_observed"] = now_iso
    if kind == "legal" and not fields.get("jurisdiction"):
        rec["jurisdiction_unknown"] = True
    _apply_fields(kind, rec, fields)
    if links:
        link_map, controls = parse_links(links, project, registers, led)
        _merge_links(rec, link_map, controls)
    registers["next_ids"][spec["prefix"]] += 1
    registers[spec["key"]].append(rec)
    return rec


def update(project, registers, led, record_id, source, now_iso, status=None, fields=None, links=None, note=None,
           by=None, superseded_by=None):
    kind, rec = find(registers, record_id)
    old_status = rec["status"]
    if status and status != old_status:
        allowed = TRANSITIONS[kind].get(status)
        if allowed is None:
            raise RegisterError("%s is not a %s status" % (status, kind))
        if source not in allowed:
            who = "a finalized audit's evidence" if allowed == {"evidence"} else " or ".join(sorted(allowed))
            raise RegisterError("only %s can set %s to %s" % (who, record_id, status))
        if kind == "risk" and status == "ACCEPTED":
            raise RegisterError("use `risk accept` with a complete acceptance record to accept a risk")
        if status in ("MITIGATED", "RESOLVED") and source == "user" and not (note and len(note.strip()) >= 5):
            raise RegisterError("say what resolved or mitigated %s (--note)" % record_id)
        if kind == "legal" and status in ("REVIEWED", "NOT_APPLICABLE") and not (note and len(note.strip()) >= 5):
            raise RegisterError("record what you were told or decided for %s (--note); Flight Check never fills this in" % record_id)
        if kind == "decision" and status == "SUPERSEDED":
            if not superseded_by:
                raise RegisterError("give --superseded-by DEC-xxxx")
            find(registers, superseded_by)
            if superseded_by == record_id:
                raise RegisterError("a decision cannot supersede itself")
            rec["superseded_by"] = superseded_by
        rec["status"] = status
    if fields:
        _apply_fields(kind, rec, fields)
    if links:
        link_map, controls = parse_links(links, project, registers, led)
        _merge_links(rec, link_map, controls)
    if kind == "requirement" and rec["status"] in ("IMPLEMENTED",) and old_status != rec["status"]:
        rec["verification"] = {"state": "unverified"}  # implemented is not verified
    rec["updated_at"] = now_iso
    rec["history"].append(_event(now_iso, rec["status"], source, note, by))
    return kind, rec, old_status


def accept_risk(registers, record_id, acceptance, now_iso, by=None):
    """Record the user's acceptance. Severity is unchanged: acceptance does not make a risk go away."""
    kind, rec = find(registers, record_id)
    if kind != "risk":
        raise RegisterError("%s is not a risk" % record_id)
    for name in ("reason", "owner", "date", "scope", "compensating_controls"):
        if not (acceptance.get(name) or "").strip():
            raise RegisterError("a risk acceptance needs %s" % name.replace("_", " "))
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", acceptance["date"]):
        raise RegisterError("acceptance date must be YYYY-MM-DD")
    old = rec["status"]
    rec["acceptance"] = {k: _clean(v, 2000) for k, v in acceptance.items() if v}
    rec["status"] = "ACCEPTED"
    rec["updated_at"] = now_iso
    rec["history"].append(_event(now_iso, "ACCEPTED", "user", "accepted by %s: %s" % (acceptance["owner"], acceptance["reason"]), by))
    return rec, old


def active_assertions(registers):
    """Statements from ACCEPTED decisions for agents to check against the implementation."""
    out = []
    for rec in registers["decisions"]:
        if rec["status"] == "ACCEPTED":
            for text in rec.get("assertions") or []:
                out.append({"decision": rec["id"], "title": rec["title"], "assertion": text})
    return out


def reverse_links(registers, record_id):
    """Records that link to record_id (links are stored on one side only)."""
    out = []
    for spec in KINDS.values():
        for rec in registers.get(spec["key"]) or []:
            if any(record_id in values for values in (rec.get("links") or {}).values()):
                out.append(rec["id"])
    return out


def entries_by_id(led):
    return {e["id"]: e for e in (led or {}).get("entries", {}).values()}


def apply_assessment(registers, final, ledger_entries, now_iso, run_id):
    """Evidence-based updates after a finalized audit. ledger_entries maps finding id -> ledger entry.
    Returns a list of (record_id, change, note)."""
    changes = []
    controls = {c["id"]: c for c in final.get("controls") or []}
    for rec in registers["requirements"]:
        linked = rec.get("controls") or []
        if not linked or rec["status"] not in ("ACCEPTED", "IMPLEMENTED", "VERIFIED"):
            continue
        states = [controls.get(c, {}).get("state") for c in linked]
        checks = [controls.get(c, {}).get("evidence_check") for c in linked]
        open_findings = [f for f in (rec.get("links") or {}).get("findings") or []
                         if ledger_entries.get(f, {}).get("status") in ("OPEN", "REGRESSION", "REMEDIATED", "NOT_REPRODUCED")]
        verified = (all(s == "VERIFIED" for s in states) and all(c in ("PASSED", None) for c in checks)
                    and not open_findings)
        if verified and any(controls.get(c, {}).get("carried_forward") for c in linked):
            # Remembered, not re-verified: an earlier verification stands while its evidence is unchanged, but a
            # carried-forward control never verifies a requirement for the first time.
            continue
        old_state = (rec.get("verification") or {}).get("state")
        if verified:
            rec["verification"] = {"state": "current", "at": now_iso, "run_id": run_id}
            if rec["status"] != "VERIFIED":
                changes.append((rec["id"], {"status": [rec["status"], "VERIFIED"]},
                                "Linked controls %s were verified with checked evidence in run %s." % (", ".join(linked), run_id)))
                rec["status"] = "VERIFIED"
                rec["history"].append(_event(now_iso, "VERIFIED", "flight-check", changes[-1][2], run_id=run_id))
                rec["updated_at"] = now_iso
        else:
            reason = ("controls not verified: %s" % ", ".join(c for c, s in zip(linked, states) if s != "VERIFIED")
                      if any(s != "VERIFIED" for s in states) else "open findings: %s" % ", ".join(open_findings)
                      if open_findings else "evidence check did not pass")
            if rec["status"] == "VERIFIED":
                changes.append((rec["id"], {"status": ["VERIFIED", "IMPLEMENTED"]},
                                "No longer verified in run %s (%s)." % (run_id, reason)))
                rec["status"] = "IMPLEMENTED"
                rec["history"].append(_event(now_iso, "IMPLEMENTED", "flight-check", changes[-1][2], run_id=run_id))
                rec["updated_at"] = now_iso
            rec["verification"] = {"state": "unverified", "at": now_iso, "run_id": run_id}
        if old_state != rec["verification"]["state"] and not (changes and changes[-1][0] == rec["id"]):
            changes.append((rec["id"], {"verification": [old_state, rec["verification"]["state"]]},
                            "Verification in run %s." % run_id))
    for rec in registers["risks"]:
        findings = (rec.get("links") or {}).get("findings") or []
        if not findings:
            continue
        statuses = [ledger_entries.get(f, {}).get("status") for f in findings]
        if rec["status"] in ("OPEN", "UNDER_REVIEW", "MITIGATION_PLANNED", "REOPENED") and all(s in constants.RESOLVED_STATUSES for s in statuses):
            new = "MITIGATED"
            note = "All linked findings (%s) were verified fixed or closed by run %s." % (", ".join(findings), run_id)
            changes.append((rec["id"], {"status": [rec["status"], new]}, note))
            rec["status"] = new
            rec["history"].append(_event(now_iso, new, "flight-check", note, run_id=run_id))
            rec["updated_at"] = now_iso
        elif rec["status"] in ("MITIGATED", "RESOLVED") and any(s == "REGRESSION" for s in statuses):
            note = "A linked finding came back in run %s." % run_id
            changes.append((rec["id"], {"status": [rec["status"], "REOPENED"]}, note))
            rec["status"] = "REOPENED"
            rec["history"].append(_event(now_iso, "REOPENED", "flight-check", note, run_id=run_id))
            rec["updated_at"] = now_iso
    return changes


def summary_row(kind, rec):
    row = {"id": rec["id"], "title": rec["title"], "status": rec["status"], "source": rec["source"]}
    if kind == "requirement":
        row.update(criticality=rec["criticality"], verification=(rec.get("verification") or {}).get("state"),
                   controls=rec.get("controls") or [])
    elif kind == "risk":
        row.update(severity=rec["severity"], domain=rec["domain"], accepted=bool(rec.get("acceptance")))
    elif kind == "decision":
        row.update(superseded_by=rec.get("superseded_by"), assertions=len(rec.get("assertions") or []))
    elif kind == "legal":
        row.update(category=rec["category"], classification=rec.get("classification"), trigger=rec["trigger"],
                   jurisdiction=rec.get("jurisdiction") or "unknown", questions=len(rec.get("questions") or []))
    row["links"] = rec.get("links") or {}
    return row

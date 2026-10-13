"""Legal review queue (docs/design/project-state.md, section 7.4).

Each legal or business question is one LR- item in registers.json. Items come from findings that carry a legal or
business classification (other than INFORMATIONAL) or from the user. Flight Check opens and re-opens items; only the
user records that counsel reviewed one, that it is resolved, or that it does not apply.
"""

from . import catalog, constants, profile, registers

NO_ISSUES = ("Based on the project evidence currently available, no specific legal-review issue has been identified. "
             "This is not a guarantee that no legal issues exist.")


class LegalError(Exception):
    pass


def _history_from_v1(events):
    out = []
    for h in events or []:
        ev = {"at": h["at"], "status": constants.V1_LEGAL_TO_ITEM.get(h["status"], "NEEDS_REVIEW"), "source": h["source"],
              "note": ("1.0 status %s" % h["status"]) + ((": " + h["note"]) if h.get("note") else "")}
        if h.get("run_id"):
            ev["run_id"] = h["run_id"]
        out.append(ev)
    return out


def _new_item_from_entry(regs, project, led, entry, now_iso, status, history=None, questions=None, evidence=None,
                         source="flight-check"):
    fields = {"title": entry["title"], "category": catalog.domain_title(entry["domain"]),
              "description": "Raised by finding %s: %s" % (entry["id"], entry["title"]),
              "trigger": "finding %s" % entry["id"], "classification": entry["legal"]["classification"]}
    if questions:
        fields["questions"] = list(questions)[:20]
    item = registers.add("legal", project, regs, led, fields, source, now_iso, links=[entry["id"]],
                         note="opened for finding %s" % entry["id"])
    item["origin"] = "observed" if source == "flight-check" else "declared"
    if evidence:
        item["evidence"] = evidence
    if status != item["status"]:
        item["status"] = status
    if history is not None:
        item["history"] = history + [{"at": now_iso, "status": status, "source": "flight-check",
                                      "note": "moved from the 1.0 ledger into the legal review queue"}]
    entry["legal"] = {"classification": entry["legal"]["classification"], "item": item["id"]}
    return item


def migrate_ledger(led, regs, project, now_iso):
    """Ledger 1.0 -> 2.0: each finding's legal status and history become a legal review item. Returns new item ids."""
    moved = []
    for entry in sorted(led["entries"].values(), key=lambda e: e["id"]):
        legal = entry.get("legal")
        if not legal or "status" not in legal:
            continue
        status = constants.V1_LEGAL_TO_ITEM[legal["status"]]
        item = _new_item_from_entry(regs, project, led, entry, now_iso, status, history=_history_from_v1(legal.get("history")))
        moved.append(item["id"])
    led["schema_version"] = constants.LEDGER_SCHEMA_VERSION
    return moved


def sync_findings(regs, project, led, findings, now_iso, run_id, root=None):
    """After a finalized audit: give every finding with a legal or business classification its queue item, keep the
    item current, and set each finding's legal_status. Returns (record, change, note) tuples for the history log."""
    changes = []
    items = {i["id"]: i for i in regs["legal"]}
    entries = registers.entries_by_id(led)
    for f in findings:
        legal = f.get("legal")
        f["legal_status"] = None
        if not legal:
            continue
        entry = entries.get(f["id"])
        if entry is None:
            continue
        item = items.get((entry.get("legal") or {}).get("item"))
        if item is None:
            if legal["classification"] == "INFORMATIONAL":
                continue
            refs = profile.evidence_refs(f.get("evidence"), root, now_iso, run_id) if root else []
            item = _new_item_from_entry(regs, project, led, entry, now_iso, "NEEDS_REVIEW",
                                        questions=legal.get("questions"), evidence=refs)
            item["history"][-1]["run_id"] = run_id
            items[item["id"]] = item
            changes.append((item["id"], {"status": [None, "NEEDS_REVIEW"]},
                            "Opened for finding %s (%s) in run %s." % (f["id"], legal["classification"], run_id)))
        else:
            notes = []
            if item.get("classification") != legal["classification"]:
                notes.append("classification changed from %s to %s" % (item.get("classification"), legal["classification"]))
                item["classification"] = legal["classification"]
            known = set(item.get("questions") or [])
            new_q = [q for q in legal.get("questions") or [] if q not in known]
            if new_q and len(item.get("questions") or []) < 20:
                item["questions"] = (list(item.get("questions") or []) + new_q)[:20]
                notes.append("%d new question(s) from the audit" % len(new_q))
            if f.get("status") == "REGRESSION" and item["status"] in ("REVIEWED", "RESOLVED", "NOT_APPLICABLE"):
                old = item["status"]
                item["status"] = "NEEDS_REVIEW"
                notes.append("finding %s came back; check whether the earlier outcome (%s) still holds" % (f["id"], old))
                changes.append((item["id"], {"status": [old, "NEEDS_REVIEW"]}, "Reopened: finding %s regressed in run %s." % (f["id"], run_id)))
            if notes:
                item["history"].append({"at": now_iso, "status": item["status"], "source": "flight-check",
                                        "note": "; ".join(notes), "run_id": run_id})
                item["updated_at"] = now_iso
        f["legal_status"] = item["status"]
    return changes


def item_for_finding(regs, project, led, finding_id, now_iso, create=False):
    entry = registers.entries_by_id(led).get(finding_id)
    if entry is None:
        raise LegalError("no finding %s in the ledger" % finding_id)
    if "legal" not in entry:
        raise LegalError("%s has no legal or business classification; add a legal item with `legal add` instead" % finding_id)
    item_id = entry["legal"].get("item")
    if item_id:
        return registers.find(regs, item_id)[1]
    if not create:
        raise LegalError("%s has no legal review item yet" % finding_id)
    return _new_item_from_entry(regs, project, led, entry, now_iso, "NEEDS_REVIEW", source="user")


def set_status(regs, project, led, ref, status, source, now_iso, note=None, by=None):
    """Change a legal item's status. ref is an LR- id or the FC- id of a finding with a legal classification."""
    if ref.startswith("FC-"):
        item = item_for_finding(regs, project, led, ref, now_iso, create=True)
    else:
        item = registers.find(regs, ref)[1]
    try:
        _, rec, old = registers.update(project, regs, led, item["id"], source, now_iso, status=status, note=note, by=by)
    except registers.RegisterError as exc:
        raise LegalError(str(exc))
    return rec, old


def open_items(regs):
    return [i for i in regs["legal"] if i["status"] not in constants.LEGAL_ITEM_CLOSED]

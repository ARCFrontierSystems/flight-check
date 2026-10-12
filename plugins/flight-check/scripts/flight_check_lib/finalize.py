"""finalize: validate -> assemble -> check evidence -> ledger merge -> gate -> audit.final.json."""

import datetime
import os

from . import VERSION, assemble, catalog, constants, evidence, gate, ledger, minischema, profile, state, validate
from .jsonio import dump_json, load_json


def _now(now_iso=None):
    if now_iso:
        dt = datetime.datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt.date()


def _index_evidence(final):
    index = []
    n = 0

    def add(owner, items):
        nonlocal n
        for ev in items or []:
            n += 1
            ev["ref"] = "E-%04d" % n
            if ev.get("kind") == "absence":
                loc = "absence: " + (ev.get("searched") or "")[:120]
            elif ev.get("kind") == "user-provided":
                loc = "user-provided: " + ev.get("path", "")
            else:
                loc = "%s:%s-%s" % (ev.get("path"), ev.get("start_line"), ev.get("end_line"))
            index.append({"ref": ev["ref"], "owner": owner, "location": loc, "kind": ev.get("kind"), "check": ev.get("check", "NOT_CHECKED")})

    for f in final["findings"]:
        add(f["id"], f["evidence"])
    for c in final["controls"]:
        add("control " + c["id"], c.get("evidence"))
    for p in final["positive_controls"]:
        add("positive " + p["id"], p["evidence"])
    return index


def _run_ref(run_dir):
    path = os.path.join(run_dir, "run-ref.json")
    if not os.path.isfile(path):
        return None
    ref = load_json(path)
    if not isinstance(ref, dict) or not isinstance(ref.get("project_id"), str):
        raise state.StateError("%s is not a valid run reference" % path)
    return ref


def finalize(run_dir, root, ledger_path=None, now_iso=None):
    """Returns a result dict. On validation failure nothing is written and ok is False."""
    errors, warnings = validate.validate_run_dir(run_dir)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    final_path = os.path.join(run_dir, "audit.final.json")
    if os.path.exists(final_path):
        return {"ok": False, "errors": ["%s already exists; this run was finalized. Use 'render' to regenerate the report." % final_path], "warnings": warnings}

    overrides = {}
    if ledger_path:
        # Owner overrides of applicability (read before the merge; the merge re-checks them against this run).
        prior = state.load(os.path.dirname(os.path.abspath(ledger_path)), strict=False)
        if prior.initialized and not prior.problems:
            overrides = {a["domain"]: a["override"] for a in prior.project["applicability"] if a.get("override")}
    audit, more = assemble.assemble(run_dir, overrides)
    warnings.extend(more)
    now, today = _now(now_iso)
    cache = evidence.FileCache()

    for f in audit["findings"] + audit["rejected_findings"] + audit["duplicate_findings"]:
        f["evidence_check"] = evidence.annotate(f["evidence"], root, cache)
    kept_positive, unconfirmed_positive = [], []
    for p in audit["positive_controls"]:
        p["evidence_check"] = evidence.annotate(p["evidence"], root, cache)
        (unconfirmed_positive if p["evidence_check"] == "FAILED" else kept_positive).append(p)
    audit["positive_controls"] = kept_positive
    for c in audit["controls"]:
        if c.get("evidence"):
            c["evidence_check"] = evidence.annotate(c["evidence"], root, cache)
            if c["state"] == "VERIFIED" and c["evidence_check"] == "FAILED":
                c["state"] = "UNVERIFIED"
                c["missing_evidence"] = "The evidence cited to verify this control could not be confirmed in the files; it needs to be re-established."
                warnings.append("control %s downgraded to UNVERIFIED: cited evidence did not match the files" % c["id"])
    for item in audit["inventory"].get("items", []):
        if item.get("evidence"):
            evidence.annotate(item["evidence"], root, cache)

    def merge_and_gate(led):
        lifecycle = ledger.merge(led, audit, now, today)
        local_to_id = {f["local_id"]: f["id"] for f in audit["findings"]}
        for f in audit["findings"]:
            f["effective_confidence"] = gate.effective_confidence(f)
            f["release_blocking_effective"] = gate.release_blocking_effective(f)
        for c in audit["controls"]:
            if c.get("related_findings"):
                c["related_findings"] = [local_to_id.get(r, r + " (rejected)") for r in c["related_findings"]]
        for i, p in enumerate(audit["positive_controls"], 1):
            p["id"] = "PC-%03d" % i
        audit["findings"].sort(key=lambda f: (constants.SEVERITY_RANK[f["severity"]], f["id"]))
        decision = gate.decide(audit, led, today, lifecycle)
        ledger.record_run(led, audit["run"]["run_id"], now, decision["decision"], audit["run"]["mode"])
        return lifecycle, decision

    run_id = audit["run"]["run_id"]
    if ledger_path:
        state_dir = os.path.dirname(os.path.abspath(ledger_path))
        with state.transaction(state_dir, "flight-check", "finalize", now, root=root, run_id=run_id,
                               ledger_name=os.path.basename(ledger_path), create_ledger=True,
                               project_name=audit["run"]["target"]["name"]) as tx:
            ref = _run_ref(run_dir)
            if ref and ref["project_id"] != tx.project["identity"]["project_id"]:
                raise state.StateError("run %s was started for project %s, but this state belongs to project %s; "
                                       "it will not be merged" % (run_id, ref["project_id"], tx.project["identity"]["project_id"]))
            prev = (tx.ledger["runs"][-1]["gate"] if tx.ledger.get("runs") else None)
            lifecycle, decision = merge_and_gate(tx.ledger)
            led = tx.ledger
            counts = {k: len(v) for k, v in lifecycle.items() if isinstance(v, list) and v and k != "ledger_entries"}
            profile_changes = profile.merge(tx.project, audit["inventory"], root, now, run_id)
            kinds = {}
            for change in profile_changes:
                kinds[change[0]] = kinds.get(change[0], 0) + 1
            tx.event("state", {"gate": [prev, decision["decision"]]},
                     "Audit run %s finalized. Findings: %s. Profile: %s." % (
                         run_id, ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "no lifecycle changes",
                         ", ".join("%s %d" % kv for kv in sorted(kinds.items())) or "unchanged"))
            for change in profile_changes:
                if change[0] == "applicability":
                    tx.event("domain:" + change[1], {"applicability": change[2]}, "Evaluated by the inventory in run %s." % run_id)
                elif change[0] == "override_contradiction":
                    tx.event("domain:" + change[1], {"contradiction": [False, True]},
                             "The owner's override (%s) contradicts the evidence in run %s; the evidence is used." % (change[2], run_id))
                elif change[0] == "contradiction":
                    tx.event(change[1], {"contradiction": [False, True]},
                             "The latest observation of %s contradicts an active correction." % change[2])
            tx.readiness = (decision["decision"], run_id, audit["run"]["target"].get("commit"))
    else:
        led = ledger.new_ledger(audit["run"]["target"]["name"])
        lifecycle, decision = merge_and_gate(led)

    final = {
        "schema_version": constants.SCHEMA_VERSION,
        "tool": {"name": "flight-check", "version": VERSION},
        "finalized_at": now,
        "run": audit["run"],
        "inventory": audit["inventory"],
        "coverage": audit["coverage"],
        "controls": audit["controls"],
        "findings": audit["findings"],
        "rejected_findings": audit["rejected_findings"],
        "duplicate_findings": audit["duplicate_findings"],
        "positive_controls": audit["positive_controls"],
        "unconfirmed_positive_controls": unconfirmed_positive,
        "unverified_areas": audit["unverified_areas"],
        "remediation_checks": audit["remediation_checks"],
        "regressions": lifecycle.get("regressions", []),
        "not_reproduced": lifecycle.get("not_reproduced", []),
        "remediated_pending": lifecycle.get("remediated_pending", []),
        "lifecycle": {k: v for k, v in lifecycle.items() if k != "ledger_entries"},
        "gate": decision,
        "notes": audit["notes"],
        "truncation": audit["truncation"],
        "validation": {"errors": [], "warnings": warnings},
        "ledger": os.path.abspath(ledger_path) if ledger_path else None,
    }
    final["evidence_index"] = _index_evidence(final)
    schema_errors = minischema.validate_def(final, catalog.schema(), "auditFinal")
    if schema_errors:
        raise RuntimeError("internal error: final document failed its schema: %s" % "; ".join(schema_errors[:5]))
    dump_json(final_path, final)
    return {
        "ok": True,
        "final": final_path,
        "gate": decision["decision"],
        "reasons": decision["reasons"],
        "counts": {s: sum(1 for f in final["findings"] if f["severity"] == s) for s in constants.SEVERITIES},
        "findings": [{"id": f["id"], "severity": f["severity"], "confidence": f["effective_confidence"], "status": f["status"],
                      "domain": f["domain"], "title": f["title"], "legal": (f.get("legal") or {}).get("classification")}
                     for f in final["findings"]],
        "controls_unverified": [c["id"] for c in final["controls"] if c["state"] == "UNVERIFIED"],
        "rejected": len(final["rejected_findings"]),
        "evidence_failures": [f["id"] for f in final["findings"] if f.get("evidence_check") == "FAILED"],
        "lifecycle": final["lifecycle"],
        "warnings": warnings,
    }


def load_final(run_dir):
    return load_json(os.path.join(run_dir, "audit.final.json"))

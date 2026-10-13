"""finalize: validate -> assemble -> check evidence -> ledger merge -> gate -> audit.final.json."""

import datetime
import os

from . import VERSION, assemble, catalog, constants, evidence, freshness, gate, ledger, legal, minischema, profile, registers, state, validate
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


def _scope(run_dir):
    """scope.json, written by init-run --changed: which domains this run reassesses and which it carries forward."""
    path = os.path.join(run_dir, "scope.json")
    if not os.path.isfile(path):
        return None
    doc = load_json(path)
    if not isinstance(doc, dict) or doc.get("mode") not in ("changed", "full") or \
            not isinstance(doc.get("carried_forward_domains"), list) or \
            not all(d in catalog.domain_ids() for d in doc["carried_forward_domains"]):
        raise state.StateError("%s is not a valid run scope" % path)
    return doc


_STILL_OPEN = ("OPEN", "REGRESSION", "REMEDIATED", "UNDER_REVIEW", "REMEDIATION_PLANNED", "ACCEPTED_RISK")


def _carried_findings(audit, entries, files):
    """Gate inputs this run did not observe but must not drop: findings in carried-forward domains that counted at
    those domains' last assessment, and open findings in a domain this run reports not applicable (an owner override
    or an inventory answer narrows attention; it never hides recorded evidence)."""
    coverage = {c["domain"]: c for c in audit["coverage"]}
    seen = {f["id"] for f in audit["findings"]}
    out = []
    for fid, e in sorted(entries.items()):
        cov = coverage.get(e["domain"])
        if not cov or fid in seen:
            continue
        if cov["status"] == constants.CARRIED_FORWARD:
            # Seen at the domain's last assessment or later (an agent of another domain may have re-reported it in a
            # run that carried this domain). Run IDs start with their UTC time, so they sort in time order.
            if e["last_seen_run"] < cov["carried_forward"]["run_id"]:
                continue
            if e["status"] in constants.RESOLVED_STATUSES or e["status"] == "NOT_REPRODUCED":
                continue
            basis = "carried_forward"
        elif cov["status"] == "NOT_APPLICABLE" and e["status"] in _STILL_OPEN:
            basis = "not_applicable"
        else:
            continue
        ev = freshness.evidence_state(e.get("evidence_files") or {}, files)
        item = {"id": fid, "domain": e["domain"], "rule": e["rule"], "title": e["title"], "severity": e["severity"],
                "status": e["status"], "confidence": e.get("confidence", "UNVERIFIED"),
                "release_blocking": e.get("release_blocking", e["severity"] != "INFORMATIONAL"),
                "last_seen_run": e["last_seen_run"], "evidence_state": ev, "basis": basis}
        if ev in (freshness.STALE, freshness.MISSING):
            item["evidence_check"] = "FAILED"
        if e.get("accepted_risk"):
            item["accepted_risk"] = e["accepted_risk"]
        if (e.get("legal") or {}).get("classification"):
            item["legal_classification"] = e["legal"]["classification"]
        item["effective_confidence"] = gate.effective_confidence(item)
        item["release_blocking_effective"] = gate.release_blocking_effective(item)
        out.append(item)
    return out


def finalize(run_dir, root, ledger_path=None, now_iso=None):
    """Returns a result dict. On validation failure nothing is written and ok is False."""
    errors, warnings = validate.validate_run_dir(run_dir)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    final_path = os.path.join(run_dir, "audit.final.json")
    if os.path.exists(final_path):
        return {"ok": False, "errors": ["%s already exists; this run was finalized. Use 'render' to regenerate the report." % final_path], "warnings": warnings}

    overrides, carried, scanned, prior_revision = {}, None, None, None
    scope = _scope(run_dir)
    if ledger_path:
        # Owner overrides of applicability (read before the merge; the merge re-checks them against this run).
        prior = state.load(os.path.dirname(os.path.abspath(ledger_path)), strict=False)
        if prior.initialized and not prior.problems:
            overrides = {a["domain"]: a["override"] for a in prior.project["applicability"] if a.get("override")}
            if scope and scope["mode"] == "changed":
                # Re-check the scope against the files now: a domain stays carried forward only if nothing that
                # changed since the last run, including while this run was in progress, affects it.
                scanned = freshness.scan(root)
                fresh = freshness.changes(root, prior, scanned=scanned)
                keep = [d for d in scope["carried_forward_domains"] if d in fresh["carried_forward_domains"]]
                dropped = sorted(set(scope["carried_forward_domains"]) - set(keep))
                if dropped:
                    warnings.append("Files changed after this run started, so these domains were not carried forward and "
                                    "count as not assessed unless an agent assessed them: %s" % ", ".join(dropped))
                carried = {"domains": {d: fresh["carried_forward_from"][d] for d in keep},
                           "controls": prior.project.get("controls") or {}, "files": scanned[0]}
                prior_revision = prior.project["revision"]
    audit, more = assemble.assemble(run_dir, overrides, carried)
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

    def merge_ledger(led):
        lifecycle = ledger.merge(led, audit, now, today)
        for rid in lifecycle.get("unknown_recorded_ids") or []:
            warnings.append("verification.json: recorded_id %s is not in the ledger; the finding was matched as usual" % rid)
        local_to_id = {f["local_id"]: f["id"] for f in audit["findings"]}
        for f in audit["findings"]:
            f["effective_confidence"] = gate.effective_confidence(f)
            f["release_blocking_effective"] = gate.release_blocking_effective(f)
        for c in audit["controls"]:
            if c.get("related_findings"):
                c["related_findings"] = [local_to_id.get(r, r + " (rejected)") for r in c["related_findings"]]
        audit["findings"].sort(key=lambda f: (constants.SEVERITY_RANK[f["severity"]], f["id"]))
        return lifecycle

    def decide(led, lifecycle, regs=None):
        decision = gate.decide(audit, led, today, lifecycle, regs)
        ledger.record_run(led, audit["run"]["run_id"], now, decision["decision"], audit["run"]["mode"])
        return decision

    run_id = audit["run"]["run_id"]
    if ledger_path:
        state_dir = os.path.dirname(os.path.abspath(ledger_path))
        with state.transaction(state_dir, "flight-check", "finalize", now, root=root, run_id=run_id,
                               ledger_name=os.path.basename(ledger_path), create_ledger=True,
                               project_name=audit["run"]["target"]["name"]) as tx:
            if prior_revision is not None and tx.project["revision"] != prior_revision:
                raise state.StateError("the project state changed while run %s was being finalized; run finalize again" % run_id)
            ref = _run_ref(run_dir)
            if ref and ref["project_id"] != tx.project["identity"]["project_id"]:
                raise state.StateError("run %s was started for project %s, but this state belongs to project %s; "
                                       "it will not be merged" % (run_id, ref["project_id"], tx.project["identity"]["project_id"]))
            prev = (tx.ledger["runs"][-1]["gate"] if tx.ledger.get("runs") else None)
            led = tx.ledger
            lifecycle = merge_ledger(led)
            # Hashes of the files each conclusion cites, so later changes can be traced to it (freshness.changes).
            hashes = {}
            entries = registers.entries_by_id(led)
            for f in audit["findings"]:
                entry = entries[f["id"]]
                entry["evidence_files"] = freshness.entry_evidence_files(f, root, hashes)
                # Kept so a later incremental run can count this finding without re-observing it.
                entry["confidence"] = f["effective_confidence"]
                entry["release_blocking"] = f["release_blocking_effective"]
            audit["carried_forward_findings"] = _carried_findings(audit, entries, scanned[0] if scanned else {})
            ledger.merge_positive_controls(led, audit["positive_controls"], run_id, now,
                                           lambda p: freshness.entry_evidence_files(p, root, hashes),
                                           {c["domain"] for c in audit["coverage"] if c["status"] == "ASSESSED"})
            controls = tx.project.setdefault("controls", {})
            coverage = {c["domain"]: c for c in audit["coverage"]}
            for c in audit["controls"]:
                if "carried-forward" in (c.get("reported_by") or []):
                    continue  # keeps the record of the run that actually assessed it
                if c.get("reported_by") or coverage[c["domain"]]["status"] in ("ASSESSED", "NOT_APPLICABLE") or \
                        c["id"] not in controls:
                    controls[c["id"]] = {"state": c["state"], "run_id": run_id, "at": now,
                                         "evidence_files": freshness.entry_evidence_files(c, root, hashes)}
            assessed = tx.project.setdefault("domains", {})
            for d, cov in coverage.items():
                rec = {"status": cov["status"], "run_id": run_id, "at": now}
                if cov["status"] in ("ASSESSED", "NOT_APPLICABLE"):
                    rec.update(assessed_run_id=run_id, assessed_at=now)
                elif cov["status"] == constants.CARRIED_FORWARD:
                    rec.update(assessed_run_id=cov["carried_forward"]["run_id"], assessed_at=cov["carried_forward"]["at"])
                elif (assessed.get(d) or {}).get("assessed_run_id"):
                    rec.update(assessed_run_id=assessed[d]["assessed_run_id"], assessed_at=assessed[d]["assessed_at"])
                assessed[d] = rec
            profile_changes = profile.merge(tx.project, audit["inventory"], root, now, run_id)
            legal_changes = legal.sync_findings(tx.registers, tx.project, led, audit["findings"], now, run_id, root)
            decision_changes, unknown = registers.apply_decision_conflicts(tx.registers, audit["findings"], now, run_id)
            warnings.extend("decision_ids: %s, which is not a recorded decision; the link was dropped" % u for u in unknown)
            register_changes = decision_changes + registers.apply_assessment(tx.registers, audit, registers.entries_by_id(led),
                                                                             now, run_id)
            decision = decide(led, lifecycle, tx.registers)
            counts = {k: len(v) for k, v in lifecycle.items() if isinstance(v, list) and v and k != "ledger_entries"}
            kinds = {}
            for change in profile_changes:
                kinds[change[0]] = kinds.get(change[0], 0) + 1
            n_carried = len(decision["carried_forward_domains"])
            tx.event("state", {"gate": [prev, decision["decision"]]},
                     "Audit run %s finalized%s. Findings: %s. Profile: %s." % (
                         run_id, (" (%d domain(s) carried forward, not reassessed)" % n_carried) if n_carried else "",
                         ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "no lifecycle changes",
                         ", ".join("%s %d" % kv for kv in sorted(kinds.items())) or "unchanged"))
            for record, change, note in legal_changes + register_changes:
                tx.event(record, change, note)
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
        # The files as this run started (init-run's snapshot) become the next baseline when available.
        freshness.write_manifest(state_dir, root, run_id, now, freshness.load_snapshot(run_dir) or scanned)
    else:
        led = ledger.new_ledger(audit["run"]["target"]["name"])
        lifecycle = merge_ledger(led)
        for i, p in enumerate(audit["positive_controls"], 1):
            p["id"] = "PC-%04d" % i
        decision = decide(led, lifecycle)

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
        "carried_forward_findings": audit.get("carried_forward_findings", []),
        "lifecycle": {k: v for k, v in lifecycle.items() if k != "ledger_entries"},
        "gate": decision,
        "notes": audit["notes"],
        "truncation": audit["truncation"],
        "validation": {"errors": [], "warnings": warnings},
        "ledger": os.path.abspath(ledger_path) if ledger_path else None,
    }
    if scope and scope["mode"] == "changed":
        final["incremental"] = {"baseline_run_id": scope.get("baseline_run_id"), "changed_files": scope.get("changed_files", 0),
                                "reassessed_domains": [c["domain"] for c in audit["coverage"] if c["status"] == "ASSESSED"],
                                "carried_forward_domains": decision.get("carried_forward_domains", [])}
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
        "carried_forward_domains": decision.get("carried_forward_domains", []),
        "carried_forward_findings": [{"id": f["id"], "severity": f["severity"], "status": f["status"], "domain": f["domain"],
                                      "title": f["title"]} for f in final["carried_forward_findings"]],
        "warnings": warnings,
    }


def load_final(run_dir):
    return load_json(os.path.join(run_dir, "audit.final.json"))

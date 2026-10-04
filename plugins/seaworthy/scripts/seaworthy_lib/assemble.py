"""Combine a run directory's agent outputs into one audit document (before ledger merge)."""

import copy
import os

from . import catalog, constants, validate
from .jsonio import load_json


def _remap_part_ids(part, taken):
    """Prefix colliding local IDs with the agent name and rewrite references inside the part."""
    mapping = {}
    for item in part["findings"] + part["positive_controls"]:
        lid = item["local_id"]
        if lid in taken or lid in mapping.values():
            new = "%s.%s" % (part["agent"], lid)
            n = 2
            while new in taken:
                new = "%s.%s.%d" % (part["agent"], lid, n)
                n += 1
            mapping[lid] = new
            item["local_id"] = new
        taken.add(item["local_id"])
    if mapping:
        for c in part["controls"]:
            c["related_findings"] = [mapping.get(r, r) for r in c.get("related_findings") or []]
    return mapping


def _merge_control(existing, incoming, agent):
    existing.setdefault("reported_by", [])
    existing["reported_by"].append(agent)
    old, new = existing["state"], incoming["state"]
    if old != new:
        existing.setdefault("conflicts", []).append("%s reported %s" % (agent, new))
        if constants.CONTROL_STATE_CAUTION[new] > constants.CONTROL_STATE_CAUTION[old]:
            existing["state"] = new
            for field in ("rationale", "missing_evidence"):
                if incoming.get(field):
                    existing[field] = incoming[field]
    existing["evidence"] = (existing.get("evidence") or []) + (incoming.get("evidence") or [])
    existing["related_findings"] = sorted(set((existing.get("related_findings") or []) + (incoming.get("related_findings") or [])))


def assemble(run_dir):
    """Return (audit_dict, warnings). Assumes validate.validate_run_dir passed."""
    warnings = []
    run = load_json(os.path.join(run_dir, "run.json"))
    inventory = load_json(os.path.join(run_dir, "inventory.json"))
    parts = [load_json(p) for p in validate.part_files(run_dir)]
    ver_path = os.path.join(run_dir, "verification.json")
    verification = load_json(ver_path) if os.path.exists(ver_path) else {"verdicts": [], "remediation_checks": []}

    taken = set()
    findings, positives, unverified = [], [], []
    controls = {}
    coverage = {}
    notes, truncation = [], list(inventory.get("truncation") or [])
    for part in parts:
        part = copy.deepcopy(part)
        _remap_part_ids(part, taken)
        agent = part["agent"]
        for f in part["findings"]:
            f["source_agent"] = agent
            findings.append(f)
        for pc in part["positive_controls"]:
            pc["source_agent"] = agent
            positives.append(pc)
        for ua in part["unverified_areas"]:
            ua["source_agent"] = agent
            unverified.append(ua)
        for c in part["controls"]:
            if c["id"] in controls:
                _merge_control(controls[c["id"]], c, agent)
            else:
                entry = copy.deepcopy(c)
                entry["reported_by"] = [agent]
                controls[c["id"]] = entry
        for cov in part["coverage"]:
            d = cov["domain"]
            cov = dict(cov, reported_by=[agent])
            if d not in coverage:
                coverage[d] = cov
            else:
                prev = coverage[d]
                prev["reported_by"].append(agent)
                if cov["status"] == "ASSESSED" and prev["status"] != "ASSESSED":
                    cov["reported_by"] = prev["reported_by"]
                    cov.setdefault("conflicts", []).append("an earlier report said %s" % prev["status"])
                    coverage[d] = cov
                else:
                    prev["searches"] = (prev.get("searches") or []) + (cov.get("searches") or [])
                    if cov["status"] != prev["status"]:
                        prev.setdefault("conflicts", []).append("%s reported %s" % (agent, cov["status"]))
        notes.extend("%s: %s" % (agent, n) for n in part.get("notes") or [])
        truncation.extend("%s: %s" % (agent, t) for t in part.get("truncation") or [])

    applicability = {a["domain"]: a for a in inventory.get("applicability", [])}
    for d in catalog.domain_ids():
        if d in coverage:
            continue
        app = applicability.get(d)
        if app and app["applicable"] == "no":
            coverage[d] = {
                "domain": d, "status": "NOT_APPLICABLE",
                "rationale": "Inventory: %s" % app["rationale"],
                "evidence": app.get("evidence") or [], "searches": [], "reported_by": ["inventory"],
            }
        else:
            coverage[d] = {
                "domain": d, "status": "NOT_ASSESSED",
                "rationale": "No agent reported on this domain in this run.",
                "searches": [], "reported_by": [],
            }

    by_id = {f["local_id"]: f for f in findings}
    verdicts = {}
    for v in verification.get("verdicts", []):
        if v["local_id"] not in by_id:
            warnings.append("verification.json: verdict for unknown finding %s ignored" % v["local_id"])
            continue
        verdicts[v["local_id"]] = v

    kept, rejected = [], []
    for f in findings:
        v = verdicts.get(f["local_id"])
        f["verification"] = {"verifier_verdict": v["verdict"] if v else "NOT_REVIEWED", "notes": v["notes"] if v else ""}
        if v and v.get("counterevidence"):
            f["counterevidence"] = f["counterevidence"] + v["counterevidence"]
        if v and v["verdict"] == "REJECTED":
            rejected.append(f)
            continue
        if v and v["verdict"] == "DOWNGRADED":
            original = {"severity": f["severity"], "confidence": f["confidence"]}
            if v.get("adjusted_severity"):
                f["severity"] = v["adjusted_severity"]
            if v.get("adjusted_confidence"):
                f["confidence"] = v["adjusted_confidence"]
            f["verification"]["original"] = original
            if f["severity"] == "INFORMATIONAL":
                f["release_blocking"] = False
        if v and v["verdict"] == "NEEDS_HUMAN":
            f["human_review"]["required"] = True
        kept.append(f)
    if not verification.get("verdicts"):
        warnings.append("No verifier verdicts were recorded; findings are marked NOT_REVIEWED.")

    rejected_ids = {f["local_id"] for f in rejected}
    known = catalog.control_by_id()
    final_controls = []
    for c in catalog.controls():
        entry = controls.get(c["id"])
        if entry is None:
            cov = coverage.get(c["domain"])
            if cov and cov["status"] == "NOT_APPLICABLE":
                entry = {"id": c["id"], "state": "NOT_APPLICABLE",
                         "rationale": "Domain not applicable: %s" % cov["rationale"],
                         "evidence": cov.get("evidence") or [], "reported_by": []}
            else:
                entry = {"id": c["id"], "state": "UNVERIFIED",
                         "missing_evidence": "No agent reported on this control in this run.",
                         "reported_by": []}
        if entry["state"] == "NOT_MET":
            live = [r for r in entry.get("related_findings") or [] if r not in rejected_ids]
            if not live:
                entry["state"] = "UNVERIFIED"
                entry["missing_evidence"] = "The finding(s) that showed this gap were rejected by the verifier; the control needs confirmation."
        entry["domain"] = c["domain"]
        entry["title"] = c["title"]
        entry["release_critical"] = c["release_critical"]
        final_controls.append(entry)
    for cid in controls:
        if cid not in known:
            warnings.append("control %s is not in the catalog and was ignored" % cid)

    audit = {
        "schema_version": constants.SCHEMA_VERSION,
        "run": run,
        "inventory": inventory,
        "coverage": [coverage[d] for d in catalog.domain_ids()],
        "controls": final_controls,
        "findings": kept,
        "rejected_findings": rejected,
        "positive_controls": positives,
        "unverified_areas": unverified,
        "remediation_checks": verification.get("remediation_checks", []),
        "notes": notes,
        "truncation": truncation,
    }
    return audit, warnings

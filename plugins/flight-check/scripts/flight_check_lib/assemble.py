"""Combine a run directory's agent outputs into one audit document (before ledger merge)."""

import copy
import os
import re

from . import catalog, constants, freshness, textsafety, validate
from .jsonio import load_json

# Structural values (identifiers, enums, paths) are never rewritten by secret masking.
_UNMASKED_KEYS = {"agent", "local_id", "id", "rule", "domain", "kind", "path", "state", "severity", "confidence",
                  "classification", "verdict", "result", "finding_id", "duplicate_of", "status", "applicable", "types",
                  "related_findings", "control_ids"}


def mask_secrets(obj, key=None):
    """Mask likely secrets in every free-text string of agent output, in place. Returns the count.

    Agents are told to mask secrets, but this does it mechanically before anything is written to
    audit.final.json or the ledger. Quotes keep matching their files: redaction markers act as gaps
    in the evidence check.
    """
    count = 0
    if isinstance(obj, dict):
        for k in list(obj):
            v = obj[k]
            if isinstance(v, str):
                if k not in _UNMASKED_KEYS:
                    obj[k], n = textsafety.redact(v)
                    count += n
            else:
                count += mask_secrets(v, k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                if key not in _UNMASKED_KEYS:
                    obj[i], n = textsafety.redact(v)
                    count += n
            else:
                count += mask_secrets(v, key)
    return count


def _namespace_part_ids(part, taken, errors):
    """Prefix every local ID with the agent name ("appsec.A1") and rewrite references inside the part.

    Verifier verdicts always use the namespaced form, so IDs never depend on merge order.
    """
    agent = part["agent"]
    mapping = {}
    for item in part["findings"] + part["positive_controls"]:
        new = "%s.%s" % (agent, item["local_id"])
        if new in taken:
            errors.append("duplicate local_id %s across part files" % new)
        mapping[item["local_id"]] = new
        item["local_id"] = new
        taken.add(new)
    for c in part["controls"]:
        c["related_findings"] = [mapping.get(r, r) for r in c.get("related_findings") or []]


def require_legal_review(finding):
    """A legal or business classification other than INFORMATIONAL always means human review.

    Agents sometimes set the classification but tag only their own review type (for example
    privacy). The review requirement follows from the classification, so it is added here; this
    can only add review, never remove it. Returns True when the finding was changed.
    """
    cls = (finding.get("legal") or {}).get("classification")
    if not cls or cls == "INFORMATIONAL":
        return False
    hr = finding.setdefault("human_review", {"required": True})
    wanted = "business" if cls == "BUSINESS DECISION REQUIRED" else "legal"
    types = list(hr.get("types") or [])
    changed = not hr.get("required") or not ({"legal", "business"} & set(types))
    if changed:
        hr["required"] = True
        if wanted not in types:
            types.append(wanted)
        hr["types"] = types
        if not hr.get("reason"):
            hr["reason"] = "Classified %s, which needs review by the owner or counsel." % cls
    return changed


def _words(text):
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _similar(a, b, threshold=0.6):
    """True when two questions substantially repeat each other (word-set Jaccard similarity)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / float(len(wa | wb)) >= threshold


_PASS_SUFFIX = re.compile(r"-p[2-9]$")


def _base_agent(agent):
    return _PASS_SUFFIX.sub("", agent)


def _merge_control(existing, incoming, agent):
    existing.setdefault("reported_by", [])
    # Repeat passes of one agent (appsec, appsec-p2) are independent attempts at the same work, so
    # evidence that any pass found counts: VERIFIED with evidence beats UNVERIFIED (the evidence is
    # still checked against the files). NOT_MET from any pass or any other agent still wins.
    passes_only = all(_base_agent(a) == _base_agent(agent) for a in existing["reported_by"])
    existing["reported_by"].append(agent)
    old, new = existing["state"], incoming["state"]
    if old != new:
        existing.setdefault("conflicts", []).append("%s reported %s" % (agent, new))
        verified = existing if old == "VERIFIED" else incoming
        if passes_only and {old, new} == {"VERIFIED", "UNVERIFIED"} and verified.get("evidence"):
            take_new = new == "VERIFIED"
        else:
            take_new = constants.CONTROL_STATE_CAUTION[new] > constants.CONTROL_STATE_CAUTION[old]
        if take_new:
            if new == "VERIFIED":
                existing.pop("missing_evidence", None)
            existing["state"] = new
            for field in ("rationale", "missing_evidence"):
                if incoming.get(field):
                    existing[field] = incoming[field]
    existing["evidence"] = (existing.get("evidence") or []) + (incoming.get("evidence") or [])
    existing["related_findings"] = sorted(set((existing.get("related_findings") or []) + (incoming.get("related_findings") or [])))


def _carried_control(c, cov, prior, files):
    """A control in a domain carried forward from an earlier assessment: its last recorded state, re-checked against
    the files it cited. Stale or missing evidence makes it UNVERIFIED; it is never newly verified."""
    origin = cov["carried_forward"]
    base = {"id": c["id"], "reported_by": ["carried-forward"]}
    if not prior:
        return dict(base, state="UNVERIFIED", missing_evidence="Its domain was carried forward from run %s, but no earlier "
                    "result for this control was recorded; reassess the domain." % origin["run_id"])
    ev = freshness.evidence_state(prior.get("evidence_files") or {}, files)
    base["carried_forward"] = {"run_id": prior["run_id"], "at": prior["at"], "evidence": ev}
    if ev in (freshness.STALE, freshness.MISSING):
        return dict(base, state="UNVERIFIED", missing_evidence="Carried forward from run %s, but a file it cited is now %s; "
                    "reassess the domain." % (prior["run_id"], ev))
    note = ("Carried forward, not reassessed: %s in run %s (%s); %s." % (
        prior["state"], prior["run_id"], prior["at"][:10],
        "the files it cited are unchanged" if ev == freshness.CURRENT else
        "it cites no files, and no change since then affects its domain"))
    if prior["state"] == "UNVERIFIED":
        return dict(base, state="UNVERIFIED", missing_evidence=note)
    return dict(base, state=prior["state"], rationale=note)


def assemble(run_dir, overrides=None, carried=None):
    """Return (audit_dict, warnings). Assumes validate.validate_run_dir passed.

    carried, for an incremental run: {"domains": {domain: {"run_id", "at"}}, "controls": project controls,
    "files": current file hashes}. Those domains, when no agent reported on them, are carried forward."""
    warnings = []
    run = load_json(os.path.join(run_dir, "run.json"))
    inventory = load_json(os.path.join(run_dir, "inventory.json"))
    parts = [load_json(p) for p in validate.part_files(run_dir)]
    ver_path = os.path.join(run_dir, "verification.json")
    verification = load_json(ver_path) if os.path.exists(ver_path) else {"verdicts": [], "remediation_checks": []}
    masked = sum(mask_secrets(doc) for doc in [inventory, verification] + parts)
    if masked:
        warnings.append("Masked %d likely secret(s) in agent output before writing results." % masked)

    taken = set()
    agents_seen = set()
    findings, positives, unverified = [], [], []
    controls = {}
    coverage = {}
    notes, truncation = [], list(inventory.get("truncation") or [])
    for part in parts:
        part = copy.deepcopy(part)
        agent = part["agent"]
        if agent in agents_seen:
            raise ValueError("two part files declare agent %r; each agent's results must be saved once" % agent)
        agents_seen.add(agent)
        dup_errors = []
        _namespace_part_ids(part, taken, dup_errors)
        if dup_errors:
            raise ValueError("; ".join(dup_errors))
        for f in part["findings"]:
            f["source_agent"] = agent
            if require_legal_review(f):
                warnings.append("%s: added the legal or business review flag that its classification requires" % f["local_id"])
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
        override = (overrides or {}).get(d)
        # "Not applicable" needs evidence of what was searched; without it the domain counts as uncertain.
        inventory_no = bool(app and app["applicable"] == "no"
                            and any(ev.get("kind") == "absence" for ev in app.get("evidence") or []))
        uncertain = not app or app["applicable"] == "unknown" or (app["applicable"] == "no" and not inventory_no)
        if carried and d in carried["domains"] and inventory_no:
            # Nothing the domain's earlier results rest on changed, so a sudden "not applicable" from the inventory is
            # not evidence enough to drop those results: carry them forward and say so.
            warnings.append("The inventory now marks %s not applicable, but nothing its earlier results rest on changed; "
                            "it is carried forward. Reassess it with a full audit if the project really dropped it." % d)
        if inventory_no and not (override and override["classification"] == "applicable") \
                and not (carried and d in carried["domains"]):
            coverage[d] = {
                "domain": d, "status": "NOT_APPLICABLE",
                "rationale": "Inventory: %s" % app["rationale"],
                "evidence": app.get("evidence") or [], "searches": [], "reported_by": ["inventory"],
            }
        elif uncertain and override and override["classification"] == "not_applicable":
            coverage[d] = {
                "domain": d, "status": "NOT_APPLICABLE",
                "rationale": "Declared not applicable by the project owner%s on %s: %s" % (
                    (" (%s)" % override["by"]) if override.get("by") else "", override["at"][:10], override["reason"]),
                "evidence": [], "searches": [], "reported_by": ["owner"],
            }
        elif carried and d in carried["domains"]:
            origin = carried["domains"][d]
            coverage[d] = {
                "domain": d, "status": constants.CARRIED_FORWARD,
                "rationale": "Carried forward, not reassessed: no change since the last run affects this domain. "
                             "Last assessed in run %s (%s)." % (origin["run_id"], origin["at"][:10]),
                "searches": [], "reported_by": [], "carried_forward": dict(origin),
            }
        else:
            rationale = "No agent reported on this domain in this run."
            if override and override["classification"] == "applicable":
                rationale = "Declared applicable by the project owner, but no agent assessed it in this run."
            elif override:
                rationale += " The owner declared it not applicable, but the inventory found it applies."
            elif app and app["applicable"] == "no":
                rationale += " The inventory marked it not applicable without saying what it searched."
            coverage[d] = {
                "domain": d, "status": "NOT_ASSESSED", "rationale": rationale,
                "searches": [], "reported_by": [],
            }

    by_id = {f["local_id"]: f for f in findings}
    verdicts = {}
    for v in verification.get("verdicts", []):
        if v["local_id"] not in by_id:
            warnings.append("verification.json: verdict for unknown finding %s ignored" % v["local_id"])
            continue
        verdicts[v["local_id"]] = v

    kept, rejected, duplicates = [], [], []
    dup_map = {lid: v["duplicate_of"] for lid, v in verdicts.items() if v["verdict"] == "DUPLICATE" and v.get("duplicate_of") in by_id}
    # The verifier's identity judgment across audits; a duplicate's passes to the finding it was merged into.
    recorded = {lid: v["recorded_id"] for lid, v in verdicts.items() if v.get("recorded_id") and v["verdict"] != "DUPLICATE"}
    for lid, target in dup_map.items():
        dup, keep = by_id[lid], by_id[target]
        if verdicts[lid].get("recorded_id"):
            recorded.setdefault(target, verdicts[lid]["recorded_id"])
        keep.setdefault("also_reported_by", []).append({"local_id": lid, "domain": dup["domain"], "title": dup["title"]})
        if dup.get("legal") and not keep.get("legal"):
            keep["legal"] = dup["legal"]
            keep["human_review"] = dup["human_review"]
        elif dup.get("legal") and keep.get("legal"):
            qs = keep["legal"].setdefault("questions", [])
            for q in dup["legal"].get("questions") or []:
                if not any(_similar(q, existing) for existing in qs):
                    qs.append(q)
        for ce in dup["counterevidence"]:
            if ce not in keep["counterevidence"]:
                keep["counterevidence"].append(ce)
    for f in findings:
        if f["local_id"] in dup_map:
            f["verification"] = {"verifier_verdict": "DUPLICATE", "notes": verdicts[f["local_id"]]["notes"], "duplicate_of": dup_map[f["local_id"]]}
            duplicates.append(f)
            continue
        v = verdicts.get(f["local_id"])
        f["verification"] = {"verifier_verdict": v["verdict"] if v else "NOT_REVIEWED", "notes": v["notes"] if v else ""}
        if v and v.get("counterevidence"):
            f["counterevidence"] = f["counterevidence"] + v["counterevidence"]
        if recorded.get(f["local_id"]) and not (v and v["verdict"] == "REJECTED"):
            f["recorded_id"] = recorded[f["local_id"]]
        if v and v["verdict"] == "REJECTED":
            rejected.append(f)
            continue
        if v and v.get("domain") and v["domain"] != f["domain"] and v["domain"] in catalog.domain_ids():
            # The verifier sees every finding together and re-files one whose harm belongs to another domain.
            f["verification"]["original_domain"] = f["domain"]
            f["domain"] = v["domain"]
            f["rule"] = v["domain"] + "." + f["rule"].split(".", 1)[-1]
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
    for c in controls.values():
        if c.get("related_findings"):
            c["related_findings"] = sorted({dup_map.get(r, r) for r in c["related_findings"]})
    known = catalog.control_by_id()
    final_controls = []
    for c in catalog.controls():
        entry = controls.get(c["id"])
        if entry is None:
            cov = coverage.get(c["domain"])
            if cov and cov["status"] == constants.CARRIED_FORWARD:
                entry = _carried_control(c, cov, carried["controls"].get(c["id"]), carried["files"])
            elif cov and cov["status"] == "NOT_APPLICABLE":
                entry = {"id": c["id"], "state": "NOT_APPLICABLE",
                         "rationale": "Domain not applicable: %s" % cov["rationale"],
                         "evidence": cov.get("evidence") or [], "reported_by": []}
            else:
                entry = {"id": c["id"], "state": "UNVERIFIED",
                         "missing_evidence": "No agent reported on this control in this run.",
                         "reported_by": []}
        if entry["state"] == "NOT_MET" and not entry.get("carried_forward"):
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
        "duplicate_findings": duplicates,
        "positive_controls": positives,
        "unverified_areas": unverified,
        "remediation_checks": verification.get("remediation_checks", []),
        "notes": notes,
        "truncation": truncation,
    }
    return audit, warnings

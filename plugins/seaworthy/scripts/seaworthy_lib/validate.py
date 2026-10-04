"""Schema and semantic validation for the files agents and the orchestrator write into a run directory."""

import glob
import os
import re

from . import catalog, constants, minischema, textsafety
from .jsonio import InputError, load_json

_GENERIC_QUESTION = re.compile(
    r"(?i)^\s*(?:is|are|would|does)\s+(?:this|it|that|we|the\s+(?:app|project|service))\s+"
    r"(?:be\s+)?(?:legal|illegal|compliant|allowed|ok|okay|fine|lawful|permitted)\s*\??\s*$"
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")

FINDING_TEXT_FIELDS = (
    "title", "severity_rationale", "confidence_rationale", "explanation", "impact",
    "remediation", "release_blocking_rationale",
)


def _rel_path_problem(path):
    if path.startswith("/") or path.startswith("\\") or _WINDOWS_DRIVE.match(path):
        return "path must be relative to the audit root"
    parts = re.split(r"[\\/]", path)
    if ".." in parts:
        return "path must not contain '..'"
    return None


def check_evidence(ev, where, errors):
    kind = ev.get("kind")
    if kind in constants.LOCATED_EVIDENCE_KINDS:
        missing = [k for k in ("path", "start_line", "end_line", "quote") if k not in ev]
        if missing:
            errors.append("%s: %s evidence needs %s" % (where, kind, ", ".join(missing)))
            return
        problem = _rel_path_problem(ev["path"])
        if problem:
            errors.append("%s: %s (%r)" % (where, problem, ev["path"]))
        if ev["end_line"] < ev["start_line"]:
            errors.append("%s: end_line is before start_line" % where)
        elif ev["end_line"] - ev["start_line"] > 60:
            errors.append("%s: cite at most 60 lines per evidence item; split it" % where)
    elif kind == "absence":
        if not ev.get("searched"):
            errors.append("%s: absence evidence must say what was searched ('searched')" % where)
    elif kind == "user-provided":
        if not ev.get("path"):
            errors.append("%s: user-provided evidence must name the imported evidence file or ID ('path')" % where)


def _guard_text(text, where, errors, warnings, check_vague=False):
    for reason in textsafety.overclaims(text):
        errors.append("%s: prohibited claim (%s). Rephrase as risk, readiness, or a question for review; quote project text in double quotes if attributing a claim." % (where, reason))
    if check_vague and textsafety.vague(text):
        errors.append("%s: vague finding text; name the specific concern and its evidence" % where)
    if textsafety.find_invisible(text):
        warnings.append("%s: contains invisible or control characters (they will be removed when rendered)" % where)


def check_finding(f, where, errors, warnings):
    rule_domain = f["rule"].split(".", 1)[0]
    if rule_domain != f["domain"]:
        errors.append("%s: rule %r must start with its domain %r" % (where, f["rule"], f["domain"]))
    for i, ev in enumerate(f["evidence"]):
        check_evidence(ev, "%s.evidence[%d]" % (where, i), errors)
    for field in FINDING_TEXT_FIELDS:
        if f.get(field):
            _guard_text(f[field], "%s.%s" % (where, field), errors, warnings, check_vague=(field == "title"))
    if f["severity"] == "INFORMATIONAL" and f["release_blocking"]:
        errors.append("%s: an INFORMATIONAL finding cannot block release" % where)
    if f["release_blocking"] and not f.get("release_blocking_rationale") and f["severity"] not in ("CRITICAL", "HIGH"):
        errors.append("%s: release_blocking on a %s finding needs release_blocking_rationale" % (where, f["severity"]))
    hr = f["human_review"]
    if hr["required"] and not hr.get("types"):
        errors.append("%s.human_review: list the review types (legal, security, privacy, accessibility, compliance, business)" % where)
    legal = f.get("legal")
    if legal:
        cls = legal["classification"]
        if cls in constants.LEGAL_CLASSES_NEEDING_QUESTIONS:
            if not legal.get("why_review"):
                errors.append("%s.legal: %s needs why_review" % (where, cls))
            if not legal.get("questions"):
                errors.append("%s.legal: %s needs at least one specific question for counsel" % (where, cls))
        if cls == "BUSINESS DECISION REQUIRED" and not legal.get("decisions_needed"):
            errors.append("%s.legal: BUSINESS DECISION REQUIRED needs decisions_needed" % where)
        if cls != "INFORMATIONAL":
            types = hr.get("types") or []
            if not hr["required"] or not ({"legal", "business"} & set(types)):
                errors.append("%s: a %s finding must set human_review.required with type legal or business" % (where, cls))
        for i, q in enumerate(legal.get("questions") or []):
            qw = "%s.legal.questions[%d]" % (where, i)
            if not q.rstrip().endswith("?"):
                errors.append("%s: phrase each item as a question for counsel ending in '?'" % qw)
            if _GENERIC_QUESTION.match(q):
                errors.append("%s: question is too generic; make it specific to this finding" % qw)
            _guard_text(q, qw, errors, warnings)
        if legal.get("why_review"):
            _guard_text(legal["why_review"], "%s.legal.why_review" % where, errors, warnings)
        for i, d in enumerate(legal.get("decisions_needed") or []):
            _guard_text(d, "%s.legal.decisions_needed[%d]" % (where, i), errors, warnings)
    known = catalog.control_by_id()
    for cid in f.get("control_ids") or []:
        if cid not in known:
            errors.append("%s.control_ids: unknown control %r" % (where, cid))


def check_control(c, where, errors, warnings, part_finding_ids=None):
    known = catalog.control_by_id()
    if c["id"] not in known:
        errors.append("%s: unknown control id %r (see references/required-controls.json)" % (where, c["id"]))
        return
    state = c["state"]
    evidence = c.get("evidence") or []
    for i, ev in enumerate(evidence):
        check_evidence(ev, "%s.evidence[%d]" % (where, i), errors)
    if state == "VERIFIED" and not any(ev["kind"] != "absence" for ev in evidence):
        errors.append("%s: VERIFIED needs at least one piece of positive evidence (not absence)" % where)
    if state == "NOT_MET" and not c.get("related_findings"):
        errors.append("%s: NOT_MET must reference the finding(s) that show the gap (related_findings)" % where)
    if state == "UNVERIFIED" and not c.get("missing_evidence"):
        errors.append("%s: UNVERIFIED must say what evidence is missing (missing_evidence)" % where)
    if state == "NOT_APPLICABLE":
        if not c.get("rationale"):
            errors.append("%s: NOT_APPLICABLE needs a rationale" % where)
        if not evidence:
            errors.append("%s: NOT_APPLICABLE needs evidence (for example absence evidence describing the searches)" % where)
    if part_finding_ids is not None:
        for ref in c.get("related_findings") or []:
            if ref not in part_finding_ids:
                errors.append("%s.related_findings: %r is not a finding in this file" % (where, ref))
    for field in ("rationale", "missing_evidence"):
        if c.get(field):
            _guard_text(c[field], "%s.%s" % (where, field), errors, warnings)


def check_part(part, name, errors, warnings):
    schema = catalog.schema()
    for e in minischema.validate_def(part, schema, "part"):
        errors.append("%s: %s" % (name, e))
    if errors:
        return
    ids = [f["local_id"] for f in part["findings"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    for d in dupes:
        errors.append("%s: duplicate finding local_id %r" % (name, d))
    id_set = set(ids)
    for i, f in enumerate(part["findings"]):
        check_finding(f, "%s: findings[%d] (%s)" % (name, i, f["local_id"]), errors, warnings)
    seen_controls = set()
    for i, c in enumerate(part["controls"]):
        if c["id"] in seen_controls:
            errors.append("%s: control %s reported twice" % (name, c["id"]))
        seen_controls.add(c["id"])
        check_control(c, "%s: controls[%d] (%s)" % (name, i, c["id"]), errors, warnings, id_set)
    for i, cov in enumerate(part["coverage"]):
        where = "%s: coverage[%d] (%s)" % (name, i, cov["domain"])
        if cov["status"] == "ASSESSED" and not cov.get("searches"):
            errors.append("%s: ASSESSED must list the searches and files examined (searches)" % where)
        if cov["status"] == "NOT_APPLICABLE" and not (cov.get("searches") or cov.get("evidence")):
            errors.append("%s: NOT_APPLICABLE must show what was searched to conclude that" % where)
        for j, ev in enumerate(cov.get("evidence") or []):
            check_evidence(ev, "%s.evidence[%d]" % (where, j), errors)
        _guard_text(cov["rationale"], where + ".rationale", errors, warnings)
    for i, pc in enumerate(part["positive_controls"]):
        where = "%s: positive_controls[%d] (%s)" % (name, i, pc["local_id"])
        if pc["local_id"] in id_set:
            errors.append("%s: local_id collides with a finding" % where)
        for j, ev in enumerate(pc["evidence"]):
            check_evidence(ev, "%s.evidence[%d]" % (where, j), errors)
            if ev["kind"] == "absence":
                errors.append("%s.evidence[%d]: a positive control needs positive evidence, not absence" % (where, j))
        if pc.get("control_id") and pc["control_id"] not in catalog.control_by_id():
            errors.append("%s: unknown control_id %r" % (where, pc["control_id"]))
        _guard_text(pc["title"], where + ".title", errors, warnings)
    for i, ua in enumerate(part["unverified_areas"]):
        where = "%s: unverified_areas[%d]" % (name, i)
        for field in ("area", "missing_evidence", "how_to_verify"):
            _guard_text(ua[field], "%s.%s" % (where, field), errors, warnings)


def part_files(run_dir):
    return sorted(glob.glob(os.path.join(run_dir, "part-*.json")))


def validate_run_dir(run_dir):
    """Validate everything an audit run produced before finalization. Returns (errors, warnings)."""
    errors, warnings = [], []
    schema = catalog.schema()

    def load(name, required=True):
        path = os.path.join(run_dir, name)
        if not os.path.exists(path):
            if required:
                errors.append("%s: missing" % name)
            return None
        try:
            return load_json(path)
        except InputError as exc:
            errors.append(str(exc))
            return None

    run = load("run.json")
    if run is not None:
        for e in minischema.validate_def(run, schema, "run"):
            errors.append("run.json: %s" % e)
    inventory = load("inventory.json")
    if inventory is not None:
        for e in minischema.validate_def(inventory, schema, "inventory"):
            errors.append("inventory.json: %s" % e)
        if not any(e.startswith("inventory.json") for e in errors):
            for i, item in enumerate(inventory["items"]):
                for j, ev in enumerate(item.get("evidence") or []):
                    check_evidence(ev, "inventory.json: items[%d].evidence[%d]" % (i, j), errors)
    files = part_files(run_dir)
    if not files:
        errors.append("no part-*.json files found; domain agents' results must be saved before finalizing")
    agents = {}
    namespaced = set()
    for path in files:
        name = os.path.basename(path)
        try:
            part = load_json(path)
        except InputError as exc:
            errors.append(str(exc))
            continue
        before = len(errors)
        check_part(part, name, errors, warnings)
        if len(errors) == before:
            if part["agent"] in agents:
                errors.append("%s: agent %r already reported in %s" % (name, part["agent"], agents[part["agent"]]))
            agents[part["agent"]] = name
            for item in part["findings"]:
                namespaced.add("%s.%s" % (part["agent"], item["local_id"]))
    verification = load("verification.json", required=False)
    if verification is not None:
        ver_errors = minischema.validate_def(verification, schema, "verification")
        errors.extend("verification.json: %s" % e for e in ver_errors)
        if not ver_errors and namespaced:
            dup_targets = {}
            for v in verification["verdicts"]:
                if v["local_id"] not in namespaced:
                    errors.append("verification.json: verdict for %r matches no finding; use <agent>.<local_id>, e.g. appsec.A1" % v["local_id"])
                if v["verdict"] == "DUPLICATE":
                    target = v.get("duplicate_of")
                    if not target or target not in namespaced or target == v["local_id"]:
                        errors.append("verification.json: DUPLICATE verdict for %r needs duplicate_of naming another finding" % v["local_id"])
                    else:
                        dup_targets[v["local_id"]] = target
                elif v.get("duplicate_of"):
                    errors.append("verification.json: duplicate_of is only allowed with a DUPLICATE verdict (%r)" % v["local_id"])
            for src, target in dup_targets.items():
                if target in dup_targets:
                    errors.append("verification.json: %r is marked duplicate of %r, which is itself a duplicate" % (src, target))
            if v_missing(verification, namespaced):
                warnings.append("verification.json: no verdict for %s" % ", ".join(sorted(v_missing(verification, namespaced))))
    return errors, warnings


def v_missing(verification, namespaced):
    return namespaced - {v["local_id"] for v in verification["verdicts"]}


def validate_summary(summary, final):
    errors, warnings = [], []
    for e in minischema.validate_def(summary, catalog.schema(), "summary"):
        errors.append("summary.json: %s" % e)
    if errors:
        return errors, warnings
    ids = {f["id"] for f in final["findings"]}
    controls = {c["id"] for c in final["controls"]}
    for i, item in enumerate(summary["remediation_priority"]):
        if item["finding_id"] not in ids:
            errors.append("summary.json: remediation_priority[%d] references unknown finding %s" % (i, item["finding_id"]))
    for i, item in enumerate(summary["accepted_risk_candidates"]):
        if item["ref"] not in ids and item["ref"] not in controls:
            errors.append("summary.json: accepted_risk_candidates[%d] references unknown finding or control %s" % (i, item["ref"]))
    for field in ("executive_summary", "legal_review_summary"):
        _guard_text(summary[field], "summary.json: " + field, errors, warnings)
    for i, text in enumerate(summary["regression_recommendations"]):
        _guard_text(text, "summary.json: regression_recommendations[%d]" % i, errors, warnings)
    return errors, warnings

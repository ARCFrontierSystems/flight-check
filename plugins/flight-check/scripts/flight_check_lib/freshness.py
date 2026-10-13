"""Change detection, evidence freshness, and impact (docs/design/project-state.md, section 9).

Content hashes decide what changed; timestamps never do. Every stored conclusion keeps the hashes of the files it
cited, so its evidence can be classified as current, stale, missing, or unverified without re-running an audit.
When the effect of a change cannot be traced, reassessment widens instead of narrowing.
"""

import fnmatch
import hashlib
import os
import stat

from . import catalog, constants, registers
from .jsonio import InputError, dump_json, load_json

MANIFEST = "manifest.json"
# Skipped wherever they appear: version control, Flight Check's own state, installed dependencies, and caches.
SKIP_DIRS = {".git", ".hg", ".svn", ".flight-check", "node_modules", ".venv", "venv", "site-packages", "__pycache__",
             ".tox", ".mypy_cache", ".pytest_cache", ".next", ".nuxt"}
# Skipped only at the top of the project, where they are build output or vendored dependencies; a directory with one
# of these names deeper in the tree (src/coverage/, tools/build/) is first-party code and is scanned.
SKIP_TOP_DIRS = {"vendor", "dist", "build", "target", "coverage"}
MAX_FILES = 200000
MAX_LIST_BYTES = 100 * 1024 * 1024  # a file list for MAX_FILES long paths

CURRENT, STALE, MISSING, UNVERIFIED = "current", "stale", "missing", "unverified"
# Conclusions no longer in force: their old evidence is expected to change (that is often how they were resolved), so
# it is not reported as stale. Changes to the files they cite still steer reassessment.
RETIRED = {"finding": ("NOT_REPRODUCED", "VERIFIED", "CLOSED"), "positive_control": ("not_observed",),
           "requirement": ("SUPERSEDED", "REJECTED"), "decision": ("SUPERSEDED", "REVERSED"), "risk": ("RESOLVED",)}


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root, max_files=MAX_FILES):
    """Hash the project's files. Never follows symbolic links; skips VCS, Flight Check, dependency, and build
    directories. Returns (files, notes) where files maps relative path -> sha256 and notes lists what was skipped."""
    root = os.path.realpath(root)
    files, notes = {}, []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        top = dirpath == root
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (top and d in SKIP_TOP_DIRS)
                             and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if st.st_size > constants.MAX_HASHED_FILE_BYTES:
                files[rel] = "size:%d" % st.st_size
                notes.append("%s is larger than %d bytes; only its size is compared" % (rel, constants.MAX_HASHED_FILE_BYTES))
                continue
            try:
                files[rel] = _hash_file(full)
            except OSError as exc:
                notes.append("%s could not be read (%s)" % (rel, exc.__class__.__name__))
            if len(files) >= max_files:
                notes.insert(0, "stopped after %d files; changes beyond that are not detected, so reassessment is widened" % max_files)
                return files, notes
    return files, notes


def write_manifest(state_dir, root, run_id, now_iso, scanned=None):
    files, notes = scanned if scanned is not None else scan(root)
    dump_json(os.path.join(state_dir, MANIFEST), {"schema_version": "1.0", "created_at": now_iso, "run_id": run_id,
                                                  "files": files, "notes": notes[:200],
                                                  "partial": any("stopped after" in n for n in notes)}, indent=None)
    return len(files)


def load_manifest(state_dir, name=MANIFEST):
    path = os.path.join(state_dir, name)
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        doc = load_json(path, MAX_LIST_BYTES)
    except InputError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in doc["files"].items()):
        return None
    return doc


SNAPSHOT = "files.json"


def write_snapshot(run_dir, scanned, now_iso):
    """The project's files as the run started. finalize records them as the next baseline, so a file that changes
    while agents are reading is reported as changed next time instead of being absorbed unassessed."""
    files, notes = scanned
    dump_json(os.path.join(run_dir, SNAPSHOT), {"schema_version": "1.0", "created_at": now_iso, "files": files,
                                                "notes": notes[:200], "partial": any("stopped after" in n for n in notes)},
              indent=None)


def load_snapshot(run_dir):
    doc = load_manifest(run_dir, SNAPSHOT)
    return (doc["files"], list(doc.get("notes") or [])) if doc else None


def file_hashes(evidence_refs):
    """{path: sha256} from stored evidence references (absence and user-provided items have none)."""
    return {r["path"]: r["sha256"] for r in evidence_refs or [] if r.get("path") and r.get("sha256")}


def evidence_state(hashes, current):
    """Classify stored evidence against the project's files now. current maps path -> sha256 (from scan())."""
    if not hashes:
        return UNVERIFIED
    states = []
    for path, digest in hashes.items():
        now = current.get(path)
        states.append(MISSING if now is None else (CURRENT if now == digest else STALE))
    if MISSING in states:
        return MISSING
    if STALE in states:
        return STALE
    return CURRENT


def _rules():
    return catalog.impact_rules()


def domains_for_path(path):
    """Domains a change to this path can affect, by rule. Empty when no rule matches. Case does not matter:
    CheckoutForm.tsx matches *checkout* like checkout_form.tsx does."""
    path = path.lower()
    name = path.rsplit("/", 1)[-1]
    out = set()
    for rule in _rules()["rules"]:
        for pattern in rule["patterns"]:
            pattern = pattern.lower()
            if fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern) or \
                    (pattern.endswith("/*") and ("/" + path).find("/" + pattern[:-1]) >= 0):
                out.update(rule["domains"])
                break
    return out


def is_source_code(path):
    return any(path.lower().endswith(s) for s in _rules()["source_code_suffixes"])


def normalize_path(path):
    """The form scan() uses: forward slashes, no leading './', no '.' segments."""
    import posixpath
    path = (path or "").replace("\\", "/").strip()
    norm = posixpath.normpath(path) if path else path
    return "" if norm == "." else norm


def _conclusions(st):
    """Every stored conclusion with the hashes of the files it relies on: (kind, id, domain or None, hashes, status)."""
    out = []
    led = st.ledger or {}
    for e in led.get("entries", {}).values():
        out.append(("finding", e["id"], e["domain"], e.get("evidence_files") or {}, e["status"]))
    for pc in (led.get("positive_controls") or {}).values():
        out.append(("positive_control", pc["id"], pc["domain"], pc.get("evidence_files") or {}, pc.get("status")))
    if st.project:
        controls = catalog.control_by_id()
        for cid, c in (st.project.get("controls") or {}).items():
            out.append(("control", cid, controls.get(cid, {}).get("domain"), c.get("evidence_files") or {}, c["state"]))
        for f in st.project["profile"]["facts"]:
            if f["status"] == "current":
                out.append(("profile", f["id"], None, file_hashes(f["evidence"]), f["status"]))
        for a in st.project["applicability"]:
            out.append(("applicability", "domain:" + a["domain"], a["domain"], file_hashes(a["evidence"]), a["classification"]))
    if st.registers:
        for kind, spec in registers.KINDS.items():
            for rec in st.registers[spec["key"]]:
                out.append((kind, rec["id"], rec.get("domain"), file_hashes(rec.get("evidence")), rec["status"]))
    return out


CARRY_ELIGIBLE = ("ASSESSED", "NOT_APPLICABLE", "CARRIED_FORWARD")
_OPEN_FINDING = ("OPEN", "REGRESSION", "REMEDIATED", "UNDER_REVIEW", "REMEDIATION_PLANNED", "ACCEPTED_RISK")


def _carry_from(rec, baseline_run):
    """The earlier assessment a domain's results can be carried forward from, or None. Only a domain that the run
    which wrote the baseline file list assessed (or itself carried forward) qualifies, so the chain of unchanged
    files is never broken by a run that skipped the domain. Only applicable domains are asked about, so a domain
    last recorded as not applicable (the owner has since said it applies, or withdrew a not-applicable override)
    has nothing to carry and is reassessed."""
    if not rec or not baseline_run or rec.get("status") not in CARRY_ELIGIBLE or rec.get("run_id") != baseline_run:
        return None
    if rec.get("status") == "NOT_APPLICABLE":
        return None
    if not rec.get("assessed_run_id"):
        return None
    return {"run_id": rec["assessed_run_id"], "at": rec["assessed_at"]}


def changes(root, st, max_files=MAX_FILES, scanned=None):
    """What changed since the last finalized assessment, what that affects, and which domains to reassess."""
    current, scan_notes = scanned if scanned is not None else scan(root, max_files)
    manifest = load_manifest(st.state_dir)
    big = sum(1 for n in scan_notes if "only its size is compared" in n)
    reasons = [n for n in scan_notes if "only its size is compared" not in n][:20]
    if big:
        reasons.append("%d file(s) over %d bytes are compared by size only" % (big, constants.MAX_HASHED_FILE_BYTES))
    widen = any("stopped after" in n for n in scan_notes)
    if manifest:
        base = manifest["files"]
        baseline = {"run_id": manifest.get("run_id"), "at": manifest.get("created_at"), "kind": "manifest"}
        if manifest.get("partial"):
            widen = True
            reasons.append("the last assessment's file list was incomplete, so reassessment is widened")
        changed = {p: ("modified" if p in base else "added") for p, d in current.items() if base.get(p) != d}
        changed.update({p: "removed" for p in base if p not in current})
    else:
        # No manifest on this machine (for example a fresh clone): compare the files that stored conclusions cite,
        # and treat every other file as possibly changed.
        cited = {}
        for _, _, _, hashes, _ in _conclusions(st):
            cited.update(hashes)
        changed = {p: ("modified" if p in current else "removed") for p, d in cited.items() if current.get(p) != d}
        baseline = {"run_id": None, "at": None, "kind": "cited files only"}
        widen = True
        reasons.append("no file list from the last assessment is available here, so files that no conclusion cites "
                       "are treated as possibly changed and reassessment is widened")

    affected, domains, unexplained = [], set(), []
    conclusions = _conclusions(st)
    by_path = {}
    for kind, cid, domain, hashes, status in conclusions:
        for path in hashes:
            by_path.setdefault(path, []).append((kind, cid, domain, status))
    for path, how in sorted(changed.items()):
        cited = by_path.get(path, [])
        rule_domains = domains_for_path(path)
        cited_domains = set()
        for kind, cid, domain, status in cited:
            affected.append({"kind": kind, "id": cid, "status": status, "path": path, "change": how})
            if domain:
                cited_domains.add(domain)
        domains.update(cited_domains)
        domains.update(rule_domains)
        if is_source_code(path):
            domains.update(_rules()["source_code_domains"])
        # A citation explains a change only through a domain; a profile fact or a register record without a domain
        # names no area to reassess, so such a file still widens reassessment.
        if not cited_domains and not rule_domains and not is_source_code(path):
            unexplained.append(path)
    if unexplained:
        widen = True
        reasons.append("%d changed file(s) match no impact rule and no stored conclusion cites them (for example %s), "
                       "so reassessment is widened" % (len(unexplained), ", ".join(unexplained[:3])))

    applicable = _applicable_domains(st)
    decision_reviews = _decision_reviews(st, changed)
    recorded = (st.project or {}).get("domains") or {}
    baseline_run = manifest.get("run_id") if manifest else None
    carry_from = {d: _carry_from(recorded.get(d), baseline_run) for d in applicable}
    never = sorted(d for d, c in carry_from.items() if c is None)
    # A domain with an open finding that cites a changed file is reassessed even if it is recorded as not
    # applicable: recorded evidence is never dropped because of an applicability answer.
    open_hit = {domain for a in affected for _, cid, domain, _, status in conclusions
                if a["id"] == cid and a["kind"] == "finding" and domain and status in _OPEN_FINDING}
    if widen:
        reassess = sorted(set(applicable) | open_hit)
    else:
        reassess = sorted(set(d for d in domains if d in applicable) | set(never) | open_hit)
        if never:
            reasons.append("%d applicable domain(s) were not assessed in the last run, so there is nothing to carry "
                           "forward for them: %s" % (len(never), ", ".join(never)))
    carried = sorted(d for d in applicable if d not in reassess)
    stale = [{"kind": k, "id": i, "state": evidence_state(h, current)} for k, i, _, h, s in conclusions
             if h and s not in RETIRED.get(k, ()) and evidence_state(h, current) in (STALE, MISSING)]
    stale_controls = {s["id"] for s in stale if s["kind"] == "control"}
    for rec in (st.registers or {}).get("requirements", []):
        linked = [c for c in rec.get("controls") or [] if c in stale_controls]
        if rec["status"] == "VERIFIED" and linked:
            stale.append({"kind": "requirement", "id": rec["id"], "state": STALE, "because": linked})
    latest = (st.project or {}).get("readiness") or []
    return {
        "baseline": baseline,
        "changed_files": len(changed),
        "files": [{"path": p, "change": c} for p, c in sorted(changed.items())][:300],
        "affected": affected[:500],
        "stale_evidence": stale[:500],
        "decisions_to_review": decision_reviews,
        "reassess_domains": reassess,
        "carried_forward_domains": carried,
        "carried_forward_from": {d: carry_from[d] for d in carried},
        "widened": bool(widen and (changed or not manifest)),
        "reasons": reasons,
        "latest_readiness": latest[-1] if latest else None,
        "readiness_is_historical": bool(latest and changed),
    }


def _applicable_domains(st):
    from . import profile
    ids = catalog.domain_ids()
    if not st.project or not st.project["applicability"]:
        return set(ids)
    entries = {a["domain"]: a for a in st.project["applicability"]}
    return {d for d in ids if d not in entries or profile.effective(entries[d]) != "not_applicable"}


def _decision_reviews(st, changed):
    """Accepted decisions whose review triggers (path prefixes, file patterns, or domain names) match the changes."""
    out = []
    if not st.registers:
        return out
    changed_domains = set()
    for path in changed:
        changed_domains.update(domains_for_path(path))
    for rec in st.registers["decisions"]:
        if rec["status"] not in ("ACCEPTED", "NEEDS_REVIEW"):
            continue
        hits = []
        for trigger in rec.get("review_triggers") or []:
            t = trigger.strip()
            if t in changed_domains:
                hits.append(t)
                continue
            for path in changed:
                if path == t or path.startswith(t.rstrip("/") + "/") or fnmatch.fnmatch(path, t):
                    hits.append(path)
                    break
        if hits:
            out.append({"decision": rec["id"], "title": rec["title"], "triggered_by": sorted(set(hits))[:10]})
    return out


def _evidence_hash(root, path, profile):
    """The value scan() would record for this file, so stored evidence and the file list compare alike."""
    from . import evidence as evidence_mod
    full, problem = evidence_mod.resolve_within(root, path)
    if problem or not full or not os.path.isfile(full) or os.path.islink(full):
        return None
    size = os.path.getsize(full)
    return ("size:%d" % size) if size > constants.MAX_HASHED_FILE_BYTES else profile.file_sha256(root, path)


def entry_evidence_files(finding, root, cache=None):
    """{path: sha256} for the files a finding (or control, or positive control) cites, read now."""
    from . import profile
    cache = {} if cache is None else cache
    out = {}
    for ev in finding.get("evidence") or []:
        path = normalize_path(ev.get("path"))
        if not path or ev.get("kind") not in ("code", "config", "documentation", "dependency"):
            continue
        if path not in cache:
            cache[path] = _evidence_hash(root, path, profile)
        if cache[path]:
            out[path] = cache[path]
    return out

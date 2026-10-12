"""Project profile and applicability (docs/design/project-state.md, sections 5 and 6).

The inventory agent describes the project in every audit. merge() turns that description into profile facts and
applicability entries in project.json. Observations are Flight Check's (source flight-check); declarations,
corrections, and overrides are the user's. Neither side erases the other: a later observation that contradicts an
active correction or override is flagged, not resolved silently.
"""

import hashlib
import os
import re

from . import catalog, textsafety
from . import evidence as evidence_mod


def aspect_key(label):
    key = {lab.lower(): a["key"] for a in catalog.profile_aspects() for lab in a["labels"]}.get(label.strip().lower())
    if key:
        return key
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60] or "unnamed"
    return "other." + slug


def file_sha256(root, rel_path):
    path, problem = evidence_mod.resolve_within(root, rel_path)
    if problem or not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_refs(items, root, now_iso, run_id, cache=None):
    """Turn agent evidence (with quotes) into stored references (file hashes, no quotes)."""
    cache = {} if cache is None else cache
    refs = []
    for ev in (items or [])[:20]:
        kind = ev.get("kind")
        ref = {"kind": kind, "observed_at": now_iso, "run_id": run_id}
        if kind == "absence":
            ref["searched"] = textsafety.redact(ev.get("searched") or "")[0][:1000]
        elif ev.get("path"):
            path = ev["path"]
            if kind == "user-provided":
                ref["path"] = path
            else:
                _, problem = evidence_mod.resolve_within(root, path) if root else (None, "NO_ROOT")
                if problem == "OUT_OF_ROOT":
                    continue
                ref["path"] = path
                for k in ("start_line", "end_line"):
                    if isinstance(ev.get(k), int) and ev[k] >= 1:
                        ref[k] = ev[k]
                if root:
                    if path not in cache:
                        cache[path] = file_sha256(root, path)
                    if cache[path]:
                        ref["sha256"] = cache[path]
        refs.append(ref)
    return refs


def _event(now_iso, status, source, note=None, run_id=None, by=None):
    ev = {"at": now_iso, "status": status, "source": source}
    if note:
        ev["note"] = textsafety.redact(note)[0][:4000]
    if run_id:
        ev["run_id"] = run_id
    if by:
        ev["by"] = by[:200]
    return ev


_PRESENCE = {"IDENTIFIED": "present", "NOT_FOUND": "absent", "UNVERIFIED": "unclear"}


def _origin(item, refs):
    if item["state"] in ("IDENTIFIED", "NOT_FOUND"):
        return "observed"
    return "inferred" if any(r["kind"] != "absence" for r in refs) else "unknown"


def _norm(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def _contradicts(fact):
    """An observation that disagrees with an active user correction."""
    if fact["source"] != "flight-check" or fact.get("presence") != "present":
        return False
    for c in fact.get("corrections") or []:
        if c["active"] and c["assertion"] in ("not_used", "excluded"):
            return True
    return False


def _new_fact(project, aspect, value, origin, source, presence, refs, now_iso, run_id, status_note, by=None):
    pid = "PF-%04d" % project["profile"]["next_id"]
    project["profile"]["next_id"] += 1
    fact = {"id": pid, "aspect": aspect, "value": value[:2000], "origin": origin, "source": source,
            "status": "current", "evidence": refs, "first_observed": now_iso, "last_observed": now_iso,
            "corrections": [], "contradiction": False,
            "history": [_event(now_iso, "current", source, status_note, run_id, by)]}
    if presence:
        fact["presence"] = presence
    if run_id:
        fact["run_id"] = run_id
    project["profile"]["facts"].append(fact)
    return fact


def merge(project, inventory, root, now_iso, run_id):
    """Fold one finalized run's inventory into the profile and applicability. Returns a list of changes."""
    changes = []
    cache = {}
    facts = project["profile"]["facts"]
    current = {f["aspect"]: f for f in facts if f["source"] == "flight-check" and f["status"] == "current"}
    seen = set()
    for item in inventory.get("items") or []:
        aspect = aspect_key(item["aspect"])
        if aspect in seen:
            continue
        seen.add(aspect)
        refs = evidence_refs(item.get("evidence"), root, now_iso, run_id, cache)
        presence = _PRESENCE.get(item["state"])
        origin = _origin(item, refs)
        value = textsafety.redact(item["value"])[0]
        old = current.get(aspect)
        if old and _norm(old["value"]) == _norm(value) and old.get("presence") == presence:
            old.update(last_observed=now_iso, run_id=run_id, evidence=refs, origin=origin)
        else:
            fact = _new_fact(project, aspect, value, origin, "flight-check", presence, refs, now_iso, run_id,
                             "observed by the inventory agent")
            if old:
                old["status"] = "superseded"
                old["history"].append(_event(now_iso, "superseded", "flight-check", "replaced by %s" % fact["id"], run_id))
                # Corrections are about the aspect, so they carry over to the new observation.
                fact["corrections"] = [dict(c) for c in old.get("corrections") or [] if c["active"]]
                changes.append(("superseded", old["id"], fact["id"]))
            else:
                changes.append(("new", fact["id"], aspect))
            old = fact
        flagged = _contradicts(old)
        if flagged and not old.get("contradiction"):
            old["history"].append(_event(now_iso, "current", "flight-check",
                                         "the latest observation contradicts an active correction", run_id))
            changes.append(("contradiction", old["id"], aspect))
        old["contradiction"] = flagged
    for aspect, fact in current.items():
        if aspect not in seen:
            fact["status"] = "historical"
            fact["history"].append(_event(now_iso, "historical", "flight-check",
                                          "not reported by the latest inventory", run_id))
            changes.append(("historical", fact["id"], aspect))
    changes.extend(_merge_applicability(project, inventory, root, now_iso, run_id, cache))
    return changes


_CLASS = {"yes": "applicable", "no": "not_applicable", "unknown": "uncertain"}


def _merge_applicability(project, inventory, root, now_iso, run_id, cache):
    changes = []
    entries = {a["domain"]: a for a in project["applicability"]}
    for app in inventory.get("applicability") or []:
        domain = app["domain"]
        refs = evidence_refs(app.get("evidence"), root, now_iso, run_id, cache)
        classification = _CLASS[app["applicable"]]
        reasoning = textsafety.redact(app.get("rationale") or "")[0][:2000] or "No rationale given."
        if classification == "not_applicable" and not any(r["kind"] == "absence" for r in refs):
            classification = "uncertain"
            reasoning = ("Marked not applicable without evidence of what was searched, so treated as uncertain. "
                         + reasoning)[:2000]
        old = entries.get(domain)
        entry = {"domain": domain, "classification": classification, "reasoning": reasoning, "evidence": refs,
                 "evaluated_at": now_iso, "run_id": run_id,
                 "override": old.get("override") if old else None}
        entry["contradiction"] = _override_conflict(entry)
        if old is None:
            changes.append(("applicability_first", domain, classification))
        elif old["classification"] != classification:
            changes.append(("applicability", domain, [old["classification"], classification]))
        if entry["contradiction"] and not (old and old.get("contradiction")):
            changes.append(("override_contradiction", domain, entry["override"]["classification"]))
        entries[domain] = entry
    project["applicability"] = [entries[d] for d in sorted(entries)]
    return changes


def _override_conflict(entry):
    override = entry.get("override")
    if not override:
        return False
    return {override["classification"], entry["classification"]} == {"applicable", "not_applicable"}


def effective(entry):
    """How the audit should treat a domain: the evidence wins over an override it contradicts."""
    override = entry.get("override")
    if override and entry["classification"] == "uncertain":
        return override["classification"]
    if override and override["classification"] == "applicable":
        return "applicable"
    return entry["classification"]


# --- user changes ----------------------------------------------------------------------------------------------

class ProfileError(Exception):
    pass


def _find_fact(project, fact_id):
    for f in project["profile"]["facts"]:
        if f["id"] == fact_id:
            return f
    raise ProfileError("no profile fact %s" % fact_id)


def correct(project, fact_id, assertion, reason, now_iso, value=None, by=None):
    fact = _find_fact(project, fact_id)
    if fact["source"] != "flight-check":
        raise ProfileError("%s is your own declaration; declare a new value instead of correcting it" % fact_id)
    if assertion == "replace" and not value:
        raise ProfileError("a 'replace' correction needs --value")
    corr = {"at": now_iso, "source": "user", "assertion": assertion, "reason": reason[:2000], "active": True}
    if value:
        corr["value"] = value[:2000]
    if by:
        corr["by"] = by[:200]
    fact.setdefault("corrections", []).append(corr)
    fact["history"].append(_event(now_iso, fact["status"], "user", "correction (%s): %s" % (assertion, reason), by=by))
    fact["contradiction"] = _contradicts(fact)
    return fact


def withdraw_corrections(project, fact_id, reason, now_iso, by=None):
    fact = _find_fact(project, fact_id)
    active = [c for c in fact.get("corrections") or [] if c["active"]]
    if not active:
        raise ProfileError("%s has no active correction" % fact_id)
    for c in active:
        c["active"] = False
    fact["history"].append(_event(now_iso, fact["status"], "user", "corrections withdrawn: %s" % reason, by=by))
    fact["contradiction"] = False
    return fact


def declare(project, aspect, value, reason, now_iso, by=None):
    """Record what the user states about the project. It sits beside observations and never replaces them."""
    key = aspect if re.fullmatch(r"[a-z][a-z0-9-]*(\.[a-z0-9-]+)*", aspect) else aspect_key(aspect)
    for f in project["profile"]["facts"]:
        if f["aspect"] == key and f["source"] == "user" and f["status"] == "current":
            f["status"] = "superseded"
            f["history"].append(_event(now_iso, "superseded", "user", "replaced by a new declaration", by=by))
    return _new_fact(project, key, value, "declared", "user", None, [], now_iso, None, "declared: %s" % reason, by)


def override(project, domain, classification, reason, now_iso, by=None):
    entries = {a["domain"]: a for a in project["applicability"]}
    entry = entries.get(domain)
    if entry is None:
        entry = {"domain": domain, "classification": "uncertain", "evidence": [], "evaluated_at": now_iso,
                 "reasoning": "Not evaluated by an audit yet."}
        project["applicability"].append(entry)
        project["applicability"].sort(key=lambda a: a["domain"])
    old = entry.get("override")
    if classification is None:
        if not old:
            raise ProfileError("%s has no override to clear" % domain)
        entry["override"] = None
    else:
        entry["override"] = {"classification": classification, "source": "user", "at": now_iso, "reason": reason[:2000]}
        if by:
            entry["override"]["by"] = by[:200]
    entry["contradiction"] = _override_conflict(entry)
    return entry, (old or {}).get("classification")

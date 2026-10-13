"""Watch mode: Flight Check alongside development (docs/usage.md, "Watch mode").

Off unless the user turns it on for a project (`watch on`, stored in the local `.flight-check/watch.json`). When on, the
plugin's hooks call into this module:
- after each file edit, instant free checks of that file (secrets written into code, HTTPS certificate checks turned
  off, debug mode left on), reported to Claude so it can tell the user in plain English and offer a fix;
- when Claude finishes a reply, a suggestion of a quick paid check of the changes, at most once per set of changes and
  at most every 20 minutes (every 2 hours for the same files), with its estimated cost; it never starts an audit
  itself;
- at session start, a short status with the reminders that are due.

Everything here reads local files only, never runs another program, and never fails a session: any error means no output.
"""

import datetime
import fnmatch
import hashlib
import json
import os
import re

from . import catalog, estimate, freshness, legal, plain, settings, state, textsafety, unblock
from .jsonio import InputError, dump_json, load_json

WATCH = "watch.json"
QUICK = ("application-security", "authentication", "authorization", "data-protection", "privacy", "payments",
         "supply-chain", "ai-llm")  # the areas a quick check covers (cli.QUICK_DOMAINS)
REMINDERS = "reminders.json"
SUGGEST_GAP_SECONDS = 20 * 60  # between suggestions
REPEAT_SAME_SECONDS = 2 * 60 * 60  # before suggesting again for the same changed files
SCAN_GAP_SECONDS = 5 * 60  # between scans of the project when Claude stops
MAX_CHECK_BYTES = 1024 * 1024
MAX_WARNED = 300
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "fixtures", "testdata"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Instant checks: (kind, pattern, file-name filter or None, what is wrong, what to do).
_TLS_OFF = [
    re.compile(r"\bverify\s*=\s*False\b"),
    re.compile(r"\brejectUnauthorized\s*:\s*false\b"),
    re.compile(r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0"),
    re.compile(r"\bInsecureSkipVerify\s*:\s*true\b"),
    re.compile(r"CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)\b"),
    re.compile(r"\bssl\._create_unverified_context\b"),
]
_DEBUG_ON = [
    (re.compile(r"\.run\([^)]*\bdebug\s*=\s*True"), None),
    (re.compile(r"^\s*DEBUG\s*=\s*True\b"), re.compile(r"(^|/)(settings|production|prod)\.py$")),
]


# --- settings ------------------------------------------------------------------------------------------------------

def _state_dir(root):
    return state.default_state_dir(root)


def load_config(root):
    path = os.path.join(_state_dir(root), WATCH)
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        doc = load_json(path, 1024 * 1024)
    except InputError:
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("enabled"), bool) else None


def _save(root, name, doc):
    sd = _state_dir(root)
    if os.path.islink(sd) or os.path.islink(os.path.join(sd, name)):
        raise state.StateError("%s is a symbolic link; refusing to write through it" % os.path.join(sd, name))
    os.makedirs(sd, exist_ok=True)
    state._ensure_gitignore(sd)  # watch.json and reminders.json stay on this machine
    dump_json(os.path.join(sd, name), doc)


def set_enabled(root, enabled, now_iso):
    doc = load_config(root) or {"schema_version": "1.0"}
    doc.update(enabled=enabled, changed_at=now_iso)
    _save(root, WATCH, doc)
    return doc


def find_root(start):
    """The nearest directory at or above start whose project has watch mode turned on, or None."""
    current = os.path.realpath(start or ".")
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        cfg = load_config(current)
        if cfg is not None:
            return current if cfg.get("enabled") else None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


# --- reminders -----------------------------------------------------------------------------------------------------

def load_reminders(root):
    path = os.path.join(_state_dir(root), REMINDERS)
    try:
        doc = load_json(path, 1024 * 1024) if os.path.isfile(path) and not os.path.islink(path) else None
    except InputError:
        doc = None
    items = (doc or {}).get("reminders") if isinstance(doc, dict) else None
    return [r for r in items or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]


def remind_later(root, item_id, on, note, now_iso):
    if not _ID.match(item_id or ""):
        raise state.StateError("--id takes a Flight Check ID such as FC-0003, SUPPLY-KNOWN-VULNS, or LR-0002")
    if on and not _DATE.match(on):
        raise state.StateError("--on takes a date as YYYY-MM-DD")
    items = [r for r in load_reminders(root) if r["id"] != item_id]
    items.append({"id": item_id, "on": on, "note": (note or "")[:500], "created_at": now_iso})
    _save(root, REMINDERS, {"schema_version": "1.0", "reminders": items})
    return items


def done(root, item_id):
    items = [r for r in load_reminders(root) if r["id"] != item_id]
    _save(root, REMINDERS, {"schema_version": "1.0", "reminders": items})
    return items


def due(root, today):
    return [r for r in load_reminders(root) if not r.get("on") or r["on"] <= today.isoformat()]


# --- instant checks ------------------------------------------------------------------------------------------------

def _gitignored(root, name):
    path = os.path.join(root, ".gitignore")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            patterns = [ln.strip().lstrip("/") for ln in fh if ln.strip() and not ln.startswith(("#", "!"))]
    except OSError:
        return False
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, p.rstrip("/")) for p in patterns)


def check_file(root, path):
    """Plain-English problems in one file: [{"line", "kind", "problem", "fix"}]. Free and local; no audit."""
    given = path if os.path.isabs(path) else os.path.join(root, path)
    if os.path.islink(given):
        return []
    full = os.path.realpath(given)
    rel = os.path.relpath(full, os.path.realpath(root)).replace(os.sep, "/")
    if rel.startswith("../") or not freshness.scanned_path(rel) or not os.path.isfile(full):
        return []
    name = rel.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))):
        if os.path.isdir(os.path.join(root, ".git")) and not _gitignored(root, name):
            return [{"line": None, "kind": "env-not-ignored",
                     "problem": "%s holds settings such as passwords, but git is not told to ignore it, so it could be "
                                "committed and shared." % rel,
                     "fix": "Add %s to .gitignore. I can do that for you." % name}]
        return []
    if set(rel.split("/")[:-1]) & _TEST_DIRS:
        return []  # test data often holds made-up keys; the full audit still looks at tests
    try:
        if os.path.getsize(full) > MAX_CHECK_BYTES:
            return []
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    if b"\0" in data[:8192]:
        return []
    out = []
    for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        if len(out) >= 10:
            break
        if _secret_kind(line):
            out.append({"line": n, "kind": "secret",
                        "problem": "Line %d of %s looks like a real %s written into the code. Anyone who can see the code "
                                   "could use it." % (n, rel, _secret_kind(line)),
                        "fix": "Move it into an environment variable or your platform's secret settings, and read it from "
                               "there. If it was ever committed or shared, also replace it with a new one at the "
                               "provider, because the old one may already be exposed. I can make the code change."})
        elif any(p.search(line) for p in _TLS_OFF):
            out.append({"line": n, "kind": "tls-off",
                        "problem": "Line %d of %s turns off the check that a server you connect to is genuine (its "
                                   "HTTPS certificate). Someone on the network could then read or change that "
                                   "traffic." % (n, rel),
                        "fix": "Remove it so the normal check applies; if you need a private certificate, point to it "
                               "instead. I can change it."})
        elif any(p.search(line) and (f is None or f.search(rel)) for p, f in _DEBUG_ON):
            out.append({"line": n, "kind": "debug-on",
                        "problem": "Line %d of %s turns on debug mode. If this reaches production, error pages can show "
                                   "your code and settings to anyone." % (n, rel),
                        "fix": "Make it depend on a setting that is off in production. I can change it."})
    return out


def _secret_kind(line):
    for kind, pattern in textsafety._SECRET_PATTERNS:
        if pattern.search(line):
            return kind.replace("-", " ")
    for m in textsafety._ASSIGNMENT.finditer(line):
        value = m.group(4)
        if m.group(3) and len(value) >= 8 and not textsafety._PLACEHOLDER.match(value) and "${" not in value:
            return "password or key"
    return None


# --- hooks ---------------------------------------------------------------------------------------------------------

def _key(*parts):
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]


def post_edit(payload, now_iso):
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path, str) or not path:
        return None
    root = find_root(os.path.dirname(path) if os.path.isabs(path) else payload.get("cwd") or ".")
    if not root:
        return None
    issues = check_file(root, path)
    cfg = load_config(root) or {}
    warned = list(cfg.get("warned") or [])
    rel = os.path.relpath(os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path)),
                          os.path.realpath(root)).replace(os.sep, "/")
    fresh = []
    for i in issues:
        k = _key(rel, i["kind"], str(i["line"]))
        if k not in warned:
            fresh.append(i)
            warned.append(k)
    if not fresh:
        return None
    cfg["warned"] = warned[-MAX_WARNED:]
    _save(root, WATCH, cfg)
    lines = ["Flight Check watch mode checked %s after this edit (automated note, not from the user):" % rel]
    lines += ["- %s Fix: %s" % (i["problem"], i["fix"]) for i in fresh]
    lines.append("Tell the user right away, in plain English and in a sentence or two per item. Never repeat a secret's "
                 "value. Offer to fix what you can and ask once before changing anything.")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(lines)}}


def _load_state(root):
    try:
        st = state.load(_state_dir(root), root=root, strict=False)
    except (state.StateError, InputError):
        return None
    return st if st.initialized and not st.problems else None


def stop(payload, now):
    if payload.get("stop_hook_active"):
        return None
    root = find_root(payload.get("cwd") or ".")
    if not root:
        return None
    cfg = load_config(root) or {}
    last = cfg.get("last_suggested_at")
    elapsed = (now - datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
               ).total_seconds() if last else None
    if elapsed is not None and elapsed < SUGGEST_GAP_SECONDS:
        return None
    st = _load_state(root)
    if st is None or not (st.project.get("readiness") or []):
        if cfg.get("suggested_first_audit"):
            return None
        cfg["suggested_first_audit"] = True
        message = ("Flight Check has not checked this project yet. When you are ready, type /flight-check:audit; it "
                   "tells you the expected cost before it starts.")
        key = "first"
    else:
        checked = cfg.get("last_checked_at")
        if checked and (now - datetime.datetime.strptime(checked, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc)).total_seconds() < SCAN_GAP_SECONDS:
            return None
        cfg["last_checked_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _save(root, WATCH, cfg)
        scanned = freshness.scan(root)
        ch = freshness.changes(root, st, scanned=scanned)
        if not ch["changed_files"] or not ch["reassess_domains"]:
            return None
        key = _key(*sorted(f["path"] + ":" + f["change"] for f in ch["files"]))
        if key == cfg.get("last_suggested_key") and elapsed is not None and elapsed < REPEAT_SAME_SECONDS:
            return None
        prefs = settings.load(root)
        agents = {d["agent"] for d in catalog.domains() if d["id"] in ch["reassess_domains"]}
        quick_agents = {d["agent"] for d in catalog.domains() if d["id"] in ch["reassess_domains"] and d["id"] in QUICK}
        full = estimate.estimate(len(scanned[0]), len(agents), 1, prefs["models"])
        areas = ", ".join(d.replace("-", " ") for d in ch["reassess_domains"][:5])
        message = "Flight Check: %d file(s) changed since the last check, touching %s." % (ch["changed_files"], areas)
        quick = estimate.estimate(len(scanned[0]), len(quick_agents), 1, prefs["models"], True) if quick_agents else None
        if quick and quick["usd_high"] < full["usd_high"]:
            message += (" A quick security check of these changes costs about $%.2f to $%.2f (/flight-check:audit --quick);"
                        " a full check of them, with a release decision, about $%.2f to $%.2f (/flight-check:audit)."
                        % (quick["usd_low"], quick["usd_high"], full["usd_low"], full["usd_high"]))
        else:
            message += (" Checking them costs about $%.2f to $%.2f (/flight-check:audit; it only re-checks what changed)."
                        % (full["usd_low"], full["usd_high"]))
        message += " Run it when you reach a good stopping point."
    cfg.update(last_suggested_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), last_suggested_key=key)
    _save(root, WATCH, cfg)
    return {"decision": "block",
            "reason": "Flight Check watch mode (automated note, not from the user): in one or two short plain sentences, "
                      "tell the user: %s Do not run the audit or any other command yourself; then stop." % message}


def session_start(payload, now):
    root = find_root(payload.get("cwd") or ".")
    if not root:
        return None
    lines = ["Flight Check is watching this project (watch mode is on; the user turns it off with /flight-check:watch off)."]
    st = _load_state(root)
    if st is None or not (st.project.get("readiness") or []):
        lines.append("It has not been checked yet: suggest /flight-check:audit when the user is ready.")
    else:
        latest = st.project["readiness"][-1]
        lines.append("Latest release decision: %s (%s)." % (plain.decision(latest["decision"]), latest["at"][:10]))
        items = unblock.items(st, root, now.date())
        mine = [i for i in items if i.get("who") == "claude"]
        theirs = [i for i in items if i.get("who") != "claude"]
        if items:
            lines.append("Open items: %d Claude can fix after asking once (/flight-check:fix), %d need the user. "
                         "Details: /flight-check:status." % (len(mine), len(theirs)))
            lines += ["- %s" % i["problem"] for i in items[:5]]
    if st is not None:
        open_legal = legal.open_items(st.registers or {"legal": []})
        to_sort = [i for i in open_legal if i["status"] == "NEEDS_REVIEW"]
        with_lawyer = [i for i in open_legal if i["status"] == "AWAITING_COUNSEL"]
        if to_sort:
            lines.append("%d legal or business question(s) are waiting for the user to sort (ask a lawyer, their own "
                         "decision, not relevant, or later); offer /flight-check:legal-packet once. They do not block a "
                         "release unless an answer says something must change." % len(to_sort))
        if with_lawyer:
            lines.append("%d question(s) are with the user's lawyer (%s): remind the user to tell Claude the answers when "
                         "they arrive, and to record them with /flight-check:track." % (
                             len(with_lawyer), ", ".join(i["id"] for i in with_lawyer[:5])))
    reminders = due(root, now.date())
    if reminders:
        lines.append("Reminders the user asked for that are due now:")
        lines += ["- %s%s" % (r["id"], (": " + r["note"]) if r.get("note") else "") for r in reminders[:10]]
    lines.append("In your first reply this session, tell the user this in one or two plain sentences, then continue "
                 "with what they asked.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}


HANDLERS = {"post-edit": "post_edit", "stop": "stop", "session-start": "session_start"}


def run_hook(event, raw, now):
    """The JSON a hook prints for this event, or None. Never raises: a broken check must not break the session."""
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return None
        if event == "post-edit":
            return post_edit(payload, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if event == "stop":
            return stop(payload, now)
        if event == "session-start":
            return session_start(payload, now)
    except Exception:  # noqa: BLE001 - a hook must never fail the user's session
        return None
    return None

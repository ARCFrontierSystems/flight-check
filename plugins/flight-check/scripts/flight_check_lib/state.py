"""Project State: the persistent files under .flight-check/ (design: docs/design/project-state.md).

Committed files: project.json, registers.json, ledger.json (1.0 format, unchanged in this phase), history.jsonl.
Local files: backups/, pending/ (an interrupted command's journal), state.lock.

Every change goes through transaction(): it takes the state lock, loads and validates every file, journals the
files it will replace, writes them atomically, appends one hash-chained event per change to history.jsonl, and
only then removes the journal. A command interrupted part-way leaves the journal behind; write commands refuse
to run until `state recover` rolls the files back to their state before that command.
"""

import contextlib
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import stat

from . import catalog, legal, ledger, lockfile, minischema, textsafety
from .jsonio import InputError, dump_json, load_json, write_text

SCHEMA_VERSION = "2.0"
STATE_DIR_NAME = ".flight-check"
PROJECT = "project.json"
REGISTERS = "registers.json"
LEDGER = "ledger.json"
HISTORY = "history.jsonl"
BACKUPS = "backups"
PENDING = "pending"
JSON_FILES = (PROJECT, REGISTERS, LEDGER)
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_HISTORY_BYTES = 100 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
READINESS_KEPT = 200
EXPORT_FORMAT = "flight-check-state-export"

GITIGNORE = """# Flight Check: files that stay on this machine (see docs/design/project-state.md).
runs/
packets/
backups/
pending/
reports/
manifest.json
*.lock
"""


class StateError(Exception):
    pass


# --- small helpers -------------------------------------------------------------------------------------------

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso(now_iso=None):
    if now_iso:
        return now_iso
    return _stamp(datetime.datetime.now(datetime.timezone.utc))


def default_state_dir(root):
    return os.path.join(os.path.abspath(root), STATE_DIR_NAME)


def _regular_or_missing(path):
    """Refuse symlinks and special files: a planted link could make Flight Check read or replace a file elsewhere."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise StateError("%s is a symbolic link; Flight Check state files must be regular files" % path)
    if not stat.S_ISREG(st.st_mode):
        raise StateError("%s is not a regular file" % path)
    return True


def _check_state_dir(state_dir, root=None):
    try:
        st = os.lstat(state_dir)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise StateError("%s is a symbolic link; Flight Check will not use a linked state directory" % state_dir)
    if not stat.S_ISDIR(st.st_mode):
        raise StateError("%s is not a directory" % state_dir)
    if root and os.path.abspath(state_dir) == default_state_dir(root):
        real_root = os.path.realpath(root)
        real_dir = os.path.realpath(state_dir)
        if os.path.commonpath([real_root, real_dir]) != real_root:
            raise StateError("%s resolves outside the audit root" % state_dir)
    return True


def _load_json_file(path, definition):
    if not _regular_or_missing(path):
        return None
    if os.path.getsize(path) > MAX_JSON_BYTES:
        raise StateError("%s is larger than %d bytes; refusing to load it" % (path, MAX_JSON_BYTES))
    try:
        doc = load_json(path)
    except InputError as exc:
        raise StateError(str(exc))
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    if isinstance(version, str) and version != SCHEMA_VERSION:
        raise StateError("%s has schema version %s, which this version of Flight Check does not support (it supports %s); "
                         "it may have been written by a newer Flight Check, and it will not be modified"
                         % (path, version, SCHEMA_VERSION))
    errors = minischema.validate_def(doc, catalog.schema(), definition)
    if errors:
        raise StateError("%s is invalid: %s" % (path, "; ".join(errors[:8])))
    return doc


# --- identity -------------------------------------------------------------------------------------------------

def _find_repo_root(root):
    current = os.path.realpath(root)
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _git_config_path(repo_root):
    dotgit = os.path.join(repo_root, ".git")
    if os.path.isdir(dotgit) and not os.path.islink(dotgit):
        return os.path.join(dotgit, "config")
    if os.path.isfile(dotgit) and not os.path.islink(dotgit):
        # Worktrees and submodules: ".git" is a file that points at the real git directory.
        try:
            with open(dotgit, encoding="utf-8") as fh:
                line = fh.read(4096).strip()
        except (OSError, UnicodeDecodeError):
            return None
        if line.startswith("gitdir:"):
            gitdir = os.path.join(repo_root, line[len("gitdir:"):].strip())
            common = os.path.join(gitdir, "commondir")
            if os.path.isfile(common):
                try:
                    with open(common, encoding="utf-8") as fh:
                        gitdir = os.path.join(gitdir, fh.read(4096).strip())
                except (OSError, UnicodeDecodeError):
                    return None
            return os.path.join(gitdir, "config")
    return None


_REMOTE = re.compile(r'^\s*\[remote\s+"origin"\]\s*$')
_SECTION = re.compile(r"^\s*\[")
_URL = re.compile(r"^\s*url\s*=\s*(\S+)\s*$")


def clean_repository_url(url):
    """Reduce a remote URL to host/path, dropping any user name, password, or token."""
    url = url.strip()
    if "://" in url:
        m = re.match(r"^(?:https?|ssh|git)://(?:[^@/]*@)?([^/:]+)(?::[0-9]+)?/(.+)$", url)
    else:
        m = re.match(r"^(?:[^@]+@)?([^:/]+):(.+)$", url)  # scp-like: git@host:owner/repo.git
    if not m or "." not in m.group(1):
        return None
    host, path = m.group(1), m.group(2)
    if path.endswith(".git"):
        path = path[:-4]
    cleaned = "%s/%s" % (host.lower(), path.strip("/"))
    if not re.fullmatch(r"[A-Za-z0-9.-]+/[A-Za-z0-9._/~-]+", cleaned) or len(cleaned) > 300:
        return None
    return cleaned


def _read_repository(repo_root):
    path = _git_config_path(repo_root)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read(256 * 1024).splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    in_origin = False
    for line in lines:
        if _SECTION.match(line):
            in_origin = bool(_REMOTE.match(line))
            continue
        m = _URL.match(line)
        if in_origin and m:
            return clean_repository_url(m.group(1))
    return None


def locate(root):
    """Where the audit root sits: (relative root, vcs, repository) without running git. Uses the real path, so the
    same directory reached through a symbolic link is the same project."""
    root = os.path.realpath(root)
    repo_root = _find_repo_root(root)
    if repo_root is None:
        return ".", "none", None
    rel = os.path.relpath(root, repo_root).replace(os.sep, "/")
    return rel, "git", _read_repository(repo_root)


def new_identity(root, name, now_iso):
    rel, vcs, repository = locate(root) if root else (".", "none", None)
    identity = {"project_id": os.urandom(16).hex(), "created_at": now_iso,
                "name": (name or os.path.basename(os.path.abspath(root or ".")) or "project")[:200],
                "root": rel, "vcs": vcs}
    if repository:
        identity["repository"] = repository
    return identity


# --- documents -----------------------------------------------------------------------------------------------

def new_project(identity):
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "identity": identity,
            "profile": {"next_id": 1, "facts": []}, "applicability": [], "readiness": []}


def new_registers():
    return {"schema_version": SCHEMA_VERSION, "next_ids": {"REQ": 1, "DEC": 1, "RISK": 1, "LR": 1},
            "requirements": [], "decisions": [], "risks": [], "legal": []}


_ORIGIN_SOURCE = {"declared": {"user"}, "observed": {"flight-check"}, "inferred": {"flight-check"},
                  "unknown": {"user", "flight-check"}, "historical": {"user", "flight-check"}}


def _id_number(record_id):
    return int(record_id.split("-")[-1])


def semantic_errors(project, registers):
    """Rules the schema cannot express."""
    errors = []

    def origin_source(rec, where):
        if rec["source"] not in _ORIGIN_SOURCE[rec["origin"]]:
            errors.append("%s: origin %r cannot come from source %r" % (where, rec["origin"], rec["source"]))

    if project is not None:
        facts = project["profile"]["facts"]
        ids = [f["id"] for f in facts]
        if len(set(ids)) != len(ids):
            errors.append("project.json: duplicate profile fact ids")
        if ids and max(_id_number(i) for i in ids) >= project["profile"]["next_id"]:
            errors.append("project.json: profile.next_id must be greater than every fact id")
        for f in facts:
            origin_source(f, "project.json: %s" % f["id"])
        domains = [a["domain"] for a in project["applicability"]]
        if len(set(domains)) != len(domains):
            errors.append("project.json: a domain appears twice in applicability")
        unknown = sorted(d for d in project.get("domains") or {} if d not in catalog.domain_ids())
        if unknown:
            errors.append("project.json: unknown domain %s in domains" % ", ".join(unknown[:5]))
        for r in project.get("readiness") or []:
            if r["revision"] > project["revision"]:
                errors.append("project.json: a readiness stamp is newer than the state revision")
    if registers is not None:
        for key, prefix in (("requirements", "REQ"), ("decisions", "DEC"), ("risks", "RISK"), ("legal", "LR")):
            ids = [r["id"] for r in registers[key]]
            if len(set(ids)) != len(ids):
                errors.append("registers.json: duplicate %s ids" % key)
            if ids and max(_id_number(i) for i in ids) >= registers["next_ids"][prefix]:
                errors.append("registers.json: next_ids.%s must be greater than every %s id" % (prefix, key))
            for rec in registers[key]:
                origin_source(rec, "registers.json: %s" % rec["id"])
    return errors


# --- history log ----------------------------------------------------------------------------------------------

def event_hash(event):
    body = {k: v for k, v in event.items() if k != "hash"}
    return _sha256(_canonical(body))


def read_history(path, tolerate_partial_tail=False):
    """Return (events, problems). Verifies JSON, schema, sequence, and the hash chain."""
    events, problems = [], []
    if not _regular_or_missing(path):
        return events, problems
    if os.path.getsize(path) > MAX_HISTORY_BYTES:
        raise StateError("%s is larger than %d bytes; refusing to load it" % (path, MAX_HISTORY_BYTES))
    with open(path, "rb") as fh:
        data = fh.read()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    elif lines and not tolerate_partial_tail:
        problems.append("history.jsonl: the last line is incomplete (an interrupted write)")
        lines.pop()
    elif lines:
        lines.pop()
    prev = ""
    schema = catalog.schema()
    for n, raw in enumerate(lines, 1):
        if len(raw) > MAX_EVENT_BYTES:
            problems.append("history.jsonl line %d is too large" % n)
            break
        try:
            event = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            problems.append("history.jsonl line %d is not valid JSON" % n)
            break
        errs = minischema.validate_def(event, schema, "logEvent")
        if errs:
            problems.append("history.jsonl line %d: %s" % (n, "; ".join(errs[:3])))
            break
        if event["seq"] != n:
            problems.append("history.jsonl line %d has seq %d; events must be numbered 1, 2, 3, ..." % (n, event["seq"]))
            break
        if event["prev"] != prev:
            problems.append("history.jsonl line %d does not chain to the previous event (edited or reordered)" % n)
            break
        if event_hash(event) != event["hash"]:
            problems.append("history.jsonl line %d has a hash that does not match its content (edited)" % n)
            break
        events.append(event)
        prev = event["hash"]
    return events, problems


def make_event(prev_event, now_iso, actor, command, record, change=None, reason=None, run_id=None, by=None):
    seq = (prev_event["seq"] + 1) if prev_event else 1
    event = {"seq": seq, "at": now_iso, "actor": actor, "command": command[:80], "record": record,
             "change": change or {}, "run_id": run_id, "revision": seq,
             "prev": prev_event["hash"] if prev_event else ""}
    if by:
        event["by"] = textsafety.redact(by)[0][:200]
    if reason:
        event["reason"] = textsafety.redact(reason)[0][:4000]
    errors = minischema.validate_def(dict(event, hash="0" * 64), catalog.schema(), "logEvent")
    if errors:
        raise StateError("internal error: history event is invalid: %s" % "; ".join(errors[:5]))
    event["hash"] = event_hash(event)
    return event


def _append_events(path, events):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as fh:
        for event in events:
            fh.write(_canonical(event) + b"\n")
        fh.flush()
        os.fsync(fh.fileno())


# --- loading -------------------------------------------------------------------------------------------------

class State(object):
    """The loaded state files. project is None when the directory holds only a 1.0 ledger (or nothing)."""

    def __init__(self, state_dir, ledger_name=LEDGER):
        self.state_dir = os.path.abspath(state_dir)
        self.ledger_name = ledger_name
        self.project = None
        self.registers = None
        self.ledger = None
        self.events = []
        self.problems = []
        self.pending = False

    def path(self, name):
        return os.path.join(self.state_dir, name)

    @property
    def initialized(self):
        return self.project is not None

    @property
    def head(self):
        return self.events[-1] if self.events else None


def load(state_dir, root=None, ledger_name=LEDGER, strict=True):
    """Load and validate the state. strict=False collects problems instead of raising (for `state check`)."""
    st = State(state_dir, ledger_name)
    if not _check_state_dir(st.state_dir, root):
        return st
    st.pending = os.path.lexists(st.path(PENDING))
    if st.pending:
        msg = ("an earlier Flight Check command was interrupted while changing state (%s exists); run "
               "`flight_check.py state recover` to roll back its partial changes" % st.path(PENDING))
        if strict:
            raise StateError(msg)
        st.problems.append(msg)

    def guarded(fn):
        try:
            return fn()
        except (StateError, ledger.LedgerError) as exc:
            if strict:
                raise StateError(str(exc))
            st.problems.append(str(exc))
            return None

    st.project = guarded(lambda: _load_json_file(st.path(PROJECT), "projectState"))
    st.registers = guarded(lambda: _load_json_file(st.path(REGISTERS), "registers"))
    if _regular_or_missing(st.path(ledger_name)):
        st.ledger = guarded(lambda: ledger.load(st.path(ledger_name), create=False))
    events, problems = read_history(st.path(HISTORY))
    st.events = events
    st.problems.extend(problems)
    if problems and strict:
        raise StateError("; ".join(problems))
    for e in semantic_errors(st.project, st.registers):
        if strict:
            raise StateError(e)
        st.problems.append(e)
    if st.project is not None:
        if st.registers is None and os.path.lexists(st.path(REGISTERS)) is False:
            msg = "registers.json is missing although project.json exists"
            if strict:
                raise StateError(msg)
            st.problems.append(msg)
        head_rev = st.head["revision"] if st.head else 0
        if st.project["revision"] != head_rev:
            msg = ("project.json is at revision %d but history.jsonl ends at revision %d; the state was changed "
                   "outside Flight Check or a command was interrupted" % (st.project["revision"], head_rev))
            if strict:
                raise StateError(msg)
            st.problems.append(msg)
    elif os.path.lexists(st.path(REGISTERS)) or st.events:
        msg = "project.json is missing although other state files exist"
        if strict:
            raise StateError(msg)
        st.problems.append(msg)
    return st


def check_identity(st, root):
    """Problems that suggest this state belongs to another project or location. Empty when it matches."""
    if not st.initialized or not root:
        return []
    if os.path.abspath(st.state_dir) != default_state_dir(root):
        return []  # state kept outside the project (--out); runs carry the project id instead
    rel, _, _ = locate(root)
    recorded = st.project["identity"]["root"]
    if rel != recorded:
        return ["this state was created for the audit root %r of its repository, but it is now at %r. If this is "
                "the same project (for example after moving it inside the repository), confirm with `state adopt`; "
                "if the state was copied from another project, remove it" % (recorded, rel)]
    return []


# --- writing -------------------------------------------------------------------------------------------------

def _ensure_gitignore(state_dir):
    path = os.path.join(state_dir, ".gitignore")
    if not os.path.lexists(path):
        write_text(path, GITIGNORE)
    elif os.path.isfile(path) and not os.path.islink(path):
        # Bring an earlier Flight Check-written file up to date; never touch one the user has edited.
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = set(fh.read().splitlines())
        if lines < set(GITIGNORE.splitlines()):
            write_text(path, GITIGNORE)


_STATE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.jsonl?$")


def _journal(st, names, new_head_seq, new_head_hash):
    """Copy the files a command is about to replace into pending/, then write journal.json last."""
    pending = st.path(PENDING)
    os.makedirs(pending, mode=0o700)
    existed = []
    for name in names:
        src = st.path(name)
        if os.path.lexists(src):
            shutil.copy2(src, os.path.join(pending, name))
            existed.append(name)
    dump_json(os.path.join(pending, "journal.json"),
              {"files": list(names), "existed": existed, "head_seq": st.head["seq"] if st.head else 0,
               "new_head_seq": new_head_seq, "new_head_hash": new_head_hash})


def _finish(st):
    shutil.rmtree(st.path(PENDING))


def _write_history(path, events):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        for event in events:
            fh.write(_canonical(event) + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def recover(state_dir):
    """Finish or roll back a command that was interrupted. Returns what was done."""
    state_dir = os.path.abspath(state_dir)
    _check_state_dir(state_dir)
    pending = os.path.join(state_dir, PENDING)
    if not os.path.lexists(pending):
        return {"action": "none", "detail": "no interrupted command was found"}
    with lockfile.locked(state_dir):
        if not os.path.lexists(pending):
            return {"action": "none", "detail": "the command that was in progress has finished"}
        if os.path.islink(pending) or not os.path.isdir(pending):
            raise StateError("%s is not a directory Flight Check created; check it and remove it by hand" % pending)
        journal_path = os.path.join(pending, "journal.json")
        if not os.path.lexists(journal_path):
            # Interrupted while journaling: no state file had been replaced yet.
            shutil.rmtree(pending)
            return {"action": "discarded", "detail": "the interrupted command had not changed any file"}
        journal = _checked_journal(state_dir, pending, journal_path)
        history = os.path.join(state_dir, HISTORY)
        events, _ = read_history(history, tolerate_partial_tail=True)
        head = events[-1]["seq"] if events else 0
        finished = head == journal["new_head_seq"] and journal.get("new_head_hash") in (None, events[-1]["hash"] if events else None)
        if finished:
            # Every file and every event was written; only removing the journal was left.
            _write_history(history, events)
            shutil.rmtree(pending)
            return {"action": "completed", "detail": "the command had finished; its journal was removed"}
        for name in journal["files"]:
            target = os.path.join(state_dir, name)
            if name in journal["existed"]:
                tmp = target + ".recover"
                shutil.copy2(os.path.join(pending, name), tmp)
                os.replace(tmp, target)
            elif os.path.lexists(target):
                os.unlink(target)
        shutil.rmtree(pending)
        return {"action": "rolled_back", "detail": "files restored to their state before the interrupted command: %s"
                % ", ".join(journal["files"])}


def _checked_journal(state_dir, pending, journal_path):
    """The journal of an interrupted command, after checking that it can only touch Flight Check's own files.
    The state directory can be committed, so a planted journal must never reach a file anywhere else."""
    _regular_or_missing(journal_path)
    try:
        journal = load_json(journal_path)
    except InputError as exc:
        raise StateError("the journal of the interrupted command is unreadable (%s); check %s by hand" % (exc, pending))
    files, existed = (journal.get("files"), journal.get("existed")) if isinstance(journal, dict) else (None, None)
    valid = (isinstance(files, list) and isinstance(existed, list) and isinstance(journal.get("new_head_seq"), int)
             and all(isinstance(n, str) and _STATE_FILE.match(n) and os.path.basename(n) == n for n in files)
             and all(isinstance(n, str) for n in existed) and set(existed) <= set(files)
             and (journal.get("new_head_hash") is None or isinstance(journal.get("new_head_hash"), str)))
    if not valid:
        raise StateError("%s is not a valid Flight Check journal; nothing was changed. Check %s and remove it by hand"
                         % (journal_path, pending))
    for name in existed:
        if not _regular_or_missing(os.path.join(pending, name)):
            raise StateError("the journal lists %s, but its saved copy is missing from %s" % (name, pending))
    for name in files:
        _regular_or_missing(os.path.join(state_dir, name))
    return journal


def backup(st, reason, now_iso):
    """Copy the committed state files to backups/<time>-<reason>/. Flight Check never deletes backups."""
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:40] or "backup"
    base = st.path(BACKUPS)
    if os.path.islink(base) or (os.path.lexists(base) and not os.path.isdir(base)):
        raise StateError("%s is not a directory Flight Check created (a symbolic link?); backups are never written "
                         "through it" % base)
    os.makedirs(base, mode=0o700, exist_ok=True)
    name = "%s-%s" % (now_iso.replace("-", "").replace(":", ""), slug)
    target = os.path.join(base, name)
    n = 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(base, "%s-%d" % (name, n))
    os.makedirs(target, mode=0o700)
    files = {}
    for fname in (PROJECT, REGISTERS, st.ledger_name, HISTORY):
        src = st.path(fname)
        if _regular_or_missing(src):
            shutil.copy2(src, os.path.join(target, fname))
            with open(src, "rb") as fh:
                files[fname] = _sha256(fh.read())
    dump_json(os.path.join(target, "backup.json"),
              {"created_at": now_iso, "reason": reason[:300], "revision": st.project["revision"] if st.project else 0,
               "files": files})
    return target


class Transaction(object):
    """Changes to the state, applied on commit. Callers edit .project, .registers, and .ledger in place."""

    def __init__(self, st, actor, command, now_iso, run_id=None, by=None):
        self.st = st
        self.actor = actor
        self.command = command
        self.now_iso = now_iso
        self.run_id = run_id
        self.by = by
        self.events = []  # (record, change, reason, actor)
        self.created = []
        self.readiness = None  # (decision, run_id, commit): stamped with the new revision on commit
        self.original = {PROJECT: copy.deepcopy(st.project), REGISTERS: copy.deepcopy(st.registers),
                         st.ledger_name: copy.deepcopy(st.ledger)}
        self.project = st.project
        self.registers = st.registers
        self.ledger = st.ledger

    def event(self, record, change=None, reason=None, actor=None):
        self.events.append((record, change or {}, reason, actor or self.actor))

    def changed_files(self):
        current = {PROJECT: self.project, REGISTERS: self.registers, self.st.ledger_name: self.ledger}
        return [name for name, doc in current.items() if doc is not None and doc != self.original[name]]


def _validate_before_write(tx):
    schema = catalog.schema()
    for doc, definition, name in ((tx.project, "projectState", PROJECT), (tx.registers, "registers", REGISTERS)):
        errors = minischema.validate_def(doc, schema, definition)
        if errors:
            raise StateError("refusing to write an invalid %s: %s" % (name, "; ".join(errors[:5])))
    errors = semantic_errors(tx.project, tx.registers)
    if errors:
        raise StateError("refusing to write inconsistent state: %s" % "; ".join(errors[:5]))
    if tx.ledger is not None:
        errors = minischema.validate_def(tx.ledger, schema, "ledger")
        if errors:
            raise StateError("refusing to write an invalid ledger: %s" % "; ".join(errors[:5]))


def _initialize(tx, root, project_name, from_ledger):
    """Create project.json and registers.json, recording a migration when a 1.0 ledger already exists."""
    st = tx.st
    name = project_name or ((st.ledger or {}).get("project") or {}).get("name")
    tx.project = new_project(new_identity(root, name, tx.now_iso))
    tx.registers = new_registers()
    if from_ledger:
        where = backup(st, "migrate-from-1.0", tx.now_iso)
        findings = len(st.ledger["entries"])
        tx.events.insert(0, ("state", {"format": [None, "1.5"]},
                             "Created project state from a Flight Check 1.0 ledger with %d finding(s); the ledger "
                             "itself is unchanged. Backup: %s" % (findings, os.path.relpath(where, st.state_dir)),
                             "flight-check"))
    else:
        tx.events.insert(0, ("state", {"format": [None, "1.5"]}, "Created project state.", "flight-check"))
    tx.created = [PROJECT, REGISTERS]


def _migrate_ledger(tx):
    """Ledger 1.0 -> 2.0: legal review status moves from findings into legal review items (design section 13)."""
    where = None if tx.created else backup(tx.st, "migrate-ledger-2.0", tx.now_iso)
    moved = legal.migrate_ledger(tx.ledger, tx.registers, tx.project, tx.now_iso)
    reason = "Ledger moved to schema 2.0: the legal review status of %d finding(s) is now kept in legal review items%s." % (
        len(moved), (" " + ", ".join(moved)) if moved else "")
    if where:
        reason += " Backup: %s" % os.path.relpath(where, tx.st.state_dir)
    tx.events.append(("state", {"ledger_schema": ["1.0", "2.0"]}, reason, "flight-check"))


@contextlib.contextmanager
def transaction(state_dir, actor, command, now_iso=None, root=None, run_id=None, by=None, ledger_name=LEDGER,
                require_ledger=False, create_ledger=False, project_name=None, initialize=True):
    """Lock, load, yield a Transaction, then journal, write, and log. Nothing is written if the body raises."""
    state_dir = os.path.abspath(state_dir)
    now_iso = _now_iso(now_iso)
    if root is None and os.path.basename(state_dir) == STATE_DIR_NAME:
        root = os.path.dirname(state_dir)  # state kept in the project: the project root is its parent
    _check_state_dir(state_dir, root)
    if require_ledger and not os.path.lexists(os.path.join(state_dir, ledger_name)):
        raise StateError("ledger %s does not exist" % os.path.join(state_dir, ledger_name))
    os.makedirs(state_dir, exist_ok=True)
    try:
        lock = lockfile.locked(state_dir)
        lock.__enter__()
    except lockfile.LockError as exc:
        raise StateError(str(exc))
    try:
        st = load(state_dir, root=root, ledger_name=ledger_name)
        problems = check_identity(st, root)
        if problems:
            raise StateError(problems[0])
        ledger_on_disk = st.ledger is not None
        if st.ledger is None:
            if require_ledger:
                raise StateError("ledger %s does not exist" % st.path(ledger_name))
            if create_ledger:
                st.ledger = ledger.new_ledger(project_name)
        tx = Transaction(st, actor, command, now_iso, run_id=run_id, by=by)
        if not st.initialized:
            if not initialize:
                raise StateError("Flight Check state is not initialized in %s; run `state init`" % state_dir)
            _initialize(tx, root, project_name, ledger_on_disk)
        if tx.ledger is not None and tx.ledger.get("schema_version") == "1.0":
            _migrate_ledger(tx)
        yield tx
        _commit(tx)
    finally:
        lock.__exit__(None, None, None)


def _commit(tx):
    st = tx.st
    names = sorted(set(tx.created) | set(tx.changed_files()))
    if not names and not tx.events and tx.readiness is None:
        return
    if not tx.events:
        tx.event("state", {}, None)
    events, prev = [], st.head
    for record, change, reason, actor in tx.events:
        ev = make_event(prev, tx.now_iso, actor, tx.command, record, change, reason, tx.run_id, tx.by)
        events.append(ev)
        prev = ev
    tx.project["revision"] = events[-1]["revision"]
    if tx.readiness is not None:
        decision, run_id, commit = tx.readiness
        stamp_readiness(tx.project, decision, run_id, tx.project["revision"], commit, tx.now_iso)
    _validate_before_write(tx)
    if PROJECT not in names:
        names.append(PROJECT)
    if tx.ledger is not None and tx.original[st.ledger_name] is None and st.ledger_name not in names:
        names.append(st.ledger_name)
    _journal(st, names + [HISTORY], events[-1]["seq"], events[-1]["hash"])
    docs = {PROJECT: tx.project, REGISTERS: tx.registers, st.ledger_name: tx.ledger}
    for name in names:
        if docs.get(name) is not None:
            dump_json(st.path(name), docs[name])
    _append_events(st.path(HISTORY), events)
    _ensure_gitignore(st.state_dir)
    _finish(st)
    st.project, st.registers, st.ledger = tx.project, tx.registers, tx.ledger
    st.events.extend(events)


# --- commands used by the CLI --------------------------------------------------------------------------------

def init(state_dir, root, now_iso=None, project_name=None):
    st = load(state_dir, root=root)
    if st.initialized:
        raise StateError("Flight Check state already exists in %s (project %s)"
                         % (st.state_dir, st.project["identity"]["project_id"]))
    with transaction(state_dir, "user", "state init", now_iso, root=root, project_name=project_name) as tx:
        pass
    return tx


def adopt(state_dir, root, reason, now_iso=None, by=None):
    """Record that this state belongs to the audit root at its current location."""
    state_dir = os.path.abspath(state_dir)
    rel, vcs, repository = locate(root)
    with lockfile.locked(state_dir):
        st = load(state_dir, root=root)
        if not st.initialized:
            raise StateError("no Flight Check state to adopt in %s" % state_dir)
        tx = Transaction(st, "user", "state adopt", _now_iso(now_iso), by=by)
        old = st.project["identity"]["root"]
        tx.project["identity"]["root"] = rel
        tx.project["identity"]["vcs"] = vcs
        if repository:
            tx.project["identity"]["repository"] = repository
        tx.event("state", {"root": [old, rel]}, reason)
        _commit(tx)
    return {"root": [old, rel]}


def export_bundle(state_dir, out, now_iso=None):
    _check_state_dir(os.path.abspath(state_dir))
    with lockfile.locked(state_dir):  # one consistent snapshot, never one torn by a concurrent write
        st = load(state_dir)
    if not st.initialized:
        raise StateError("no Flight Check state to export in %s" % st.state_dir)
    files = {PROJECT: st.project, REGISTERS: st.registers, LEDGER: st.ledger, HISTORY: st.events}
    bundle = {"format": EXPORT_FORMAT, "format_version": 1, "exported_at": _now_iso(now_iso),
              "project_id": st.project["identity"]["project_id"], "revision": st.project["revision"],
              "files": files, "sha256": {k: _sha256(_canonical(v)) for k, v in files.items()}}
    dump_json(out, bundle)
    return {"out": os.path.abspath(out), "revision": st.project["revision"], "events": len(st.events)}


def _validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("format") != EXPORT_FORMAT or bundle.get("format_version") != 1:
        raise StateError("not a Flight Check state export (format %r)" % (bundle.get("format") if isinstance(bundle, dict) else None))
    files = bundle.get("files")
    if not isinstance(files, dict) or set(files) - {PROJECT, REGISTERS, LEDGER, HISTORY} or PROJECT not in files:
        raise StateError("the export has an unexpected set of files")
    for name, doc in files.items():
        if _sha256(_canonical(doc)) != (bundle.get("sha256") or {}).get(name):
            raise StateError("the export's %s does not match its recorded hash (damaged or edited)" % name)
    schema = catalog.schema()
    for name, definition in ((PROJECT, "projectState"), (REGISTERS, "registers"), (LEDGER, "ledger")):
        if files.get(name) is not None:
            errors = minischema.validate_def(files[name], schema, definition)
            if errors:
                raise StateError("the export's %s is invalid: %s" % (name, "; ".join(errors[:5])))
    errors = semantic_errors(files[PROJECT], files.get(REGISTERS))
    if errors:
        raise StateError("the export is inconsistent: %s" % "; ".join(errors[:5]))
    prev = ""
    for n, event in enumerate(files.get(HISTORY) or [], 1):
        if minischema.validate_def(event, schema, "logEvent") or event["seq"] != n or event["prev"] != prev \
                or event_hash(event) != event["hash"]:
            raise StateError("the export's history log is damaged at event %d" % n)
        prev = event["hash"]
    if files[PROJECT]["revision"] != (files[HISTORY][-1]["revision"] if files.get(HISTORY) else 0):
        raise StateError("the export's project revision does not match its history log")
    # The top-level fields are not covered by the hashes, so they must agree with the hashed project file.
    if bundle.get("project_id") != files[PROJECT]["identity"]["project_id"] or \
            bundle.get("revision") != files[PROJECT]["revision"] or not isinstance(bundle.get("exported_at"), str):
        raise StateError("the export's summary fields do not match its project file (damaged or edited)")


def import_bundle(state_dir, source, root=None, adopt_other=False, now_iso=None, by=None):
    """Replace the state with an exported bundle, after backing up the current state (history log included)."""
    state_dir = os.path.abspath(state_dir)
    now_iso = _now_iso(now_iso)
    try:
        bundle = load_json(source)
    except InputError as exc:
        raise StateError(str(exc))
    _validate_bundle(bundle)
    files = bundle["files"]
    _check_state_dir(state_dir, root)
    os.makedirs(state_dir, exist_ok=True)
    with lockfile.locked(state_dir):
        st = load(state_dir, root=root)
        incoming = files[PROJECT]["identity"]["project_id"]
        if st.initialized and st.project["identity"]["project_id"] != incoming and not adopt_other:
            raise StateError("the export belongs to project %s, but this state belongs to project %s; pass --adopt "
                             "to replace it anyway" % (incoming, st.project["identity"]["project_id"]))
        where = backup(st, "before-import", now_iso) if (st.initialized or st.ledger is not None) else None
        project = copy.deepcopy(files[PROJECT])
        registers = copy.deepcopy(files.get(REGISTERS)) or new_registers()
        led = copy.deepcopy(files.get(LEDGER))
        events = list(files.get(HISTORY) or [])
        note = "Imported the export of %s (revision %d)." % (bundle["exported_at"], bundle["revision"])
        if where:
            note += " The previous state, including its history log, is in %s." % os.path.relpath(where, state_dir)
        ev = make_event(events[-1] if events else None, now_iso, "user", "state import", "state",
                        {"revision": [st.project["revision"] if st.initialized else None, bundle["revision"]]},
                        note, None, by)
        project["revision"] = ev["revision"]
        tx = Transaction(st, "user", "state import", now_iso)
        tx.project, tx.registers, tx.ledger = project, registers, led
        _validate_before_write(tx)
        _journal(st, [PROJECT, REGISTERS, st.ledger_name, HISTORY], ev["seq"], ev["hash"])
        dump_json(st.path(PROJECT), project)
        dump_json(st.path(REGISTERS), registers)
        if led is not None:
            dump_json(st.path(st.ledger_name), led)
        elif os.path.lexists(st.path(st.ledger_name)):
            os.unlink(st.path(st.ledger_name))
        _write_history(st.path(HISTORY), events + [ev])
        _ensure_gitignore(state_dir)
        _finish(st)
    return {"backup": where, "revision": ev["revision"], "project_id": bundle["project_id"]}


def list_backups(state_dir):
    base = os.path.join(os.path.abspath(state_dir), BACKUPS)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        meta = os.path.join(base, name, "backup.json")
        if os.path.isfile(meta):
            try:
                info = load_json(meta)
            except InputError:
                continue
            out.append({"backup": os.path.join(base, name), "created_at": info.get("created_at"),
                        "reason": info.get("reason"), "revision": info.get("revision")})
    return out


def _keep_counters(tx, st):
    """A restore never lowers an ID counter: IDs used since the backup appear in the history log (and perhaps in
    packets already sent), so they are never given to a different record."""
    for key, value in (st.registers or new_registers())["next_ids"].items():
        tx.registers["next_ids"][key] = max(value, tx.registers["next_ids"].get(key, 1))
    tx.project["profile"]["next_id"] = max(st.project["profile"]["next_id"], tx.project["profile"]["next_id"])
    if tx.ledger is not None and st.ledger is not None and tx.ledger is not st.ledger:
        for key in ("next_id", "next_pc_id"):
            if key in st.ledger:
                tx.ledger[key] = max(st.ledger[key], tx.ledger.get(key, 1))


def restore(state_dir, backup_dir, root=None, now_iso=None, by=None, adopt_other=False):
    """Restore project, registers, and ledger from a backup. The history log is kept and records the restore."""
    state_dir = os.path.abspath(state_dir)
    now_iso = _now_iso(now_iso)
    backup_dir = os.path.abspath(backup_dir)
    base = os.path.join(state_dir, BACKUPS)
    if os.path.commonpath([base, backup_dir]) != base or not os.path.isfile(os.path.join(backup_dir, "backup.json")):
        raise StateError("%s is not a Flight Check backup in %s" % (backup_dir, base))
    try:
        meta = load_json(os.path.join(backup_dir, "backup.json"))
    except InputError as exc:
        raise StateError("the backup is damaged: %s" % exc)
    listed = meta.get("files") if isinstance(meta, dict) else None
    if not isinstance(listed, dict) or not all(isinstance(k, str) and _STATE_FILE.match(k) and isinstance(v, str)
                                               for k, v in listed.items()):
        raise StateError("the backup is damaged: backup.json does not list its files")
    for fname, digest in listed.items():
        path = os.path.join(backup_dir, fname)
        if not _regular_or_missing(path):
            raise StateError("backup file %s is missing" % fname)
        with open(path, "rb") as fh:
            if _sha256(fh.read()) != digest:
                raise StateError("backup file %s does not match its recorded hash" % fname)
    unlisted = sorted(n for n in os.listdir(backup_dir) if n != "backup.json" and n not in listed)
    if unlisted:
        raise StateError("the backup has files its backup.json does not list (%s); it was changed after it was made"
                         % ", ".join(unlisted))
    with lockfile.locked(state_dir):
        st = load(state_dir, root=root)
        if not st.initialized:
            raise StateError("no Flight Check state in %s" % state_dir)
        restored_project = _load_json_file(os.path.join(backup_dir, PROJECT), "projectState")
        restored_registers = _load_json_file(os.path.join(backup_dir, REGISTERS), "registers")
        restored_ledger = None
        if os.path.isfile(os.path.join(backup_dir, st.ledger_name)):
            restored_ledger = ledger.load(os.path.join(backup_dir, st.ledger_name), create=False)
        if restored_project is None:
            raise StateError("the backup has no project.json (it was taken before the state existed); restore its "
                             "ledger by hand if you need it")
        if restored_project["identity"]["project_id"] != st.project["identity"]["project_id"] and not adopt_other:
            raise StateError("the backup belongs to another project (for example it was taken before importing "
                             "another project's export); pass --adopt to restore it anyway")
        where = backup(st, "before-restore", now_iso)
        tx = Transaction(st, "user", "state restore", now_iso, by=by)
        tx.project = restored_project
        tx.registers = restored_registers or new_registers()
        if restored_ledger is not None:
            tx.ledger = restored_ledger
        _keep_counters(tx, st)
        tx.event("state", {"revision": [st.project["revision"], meta.get("revision")]},
                 "Restored from backup %s; the state before the restore is in %s."
                 % (os.path.basename(backup_dir), os.path.relpath(where, state_dir)))
        _commit(tx)
    return {"restored_from": backup_dir, "backup_of_previous": where, "revision": tx.project["revision"]}


def summary(st):
    """A compact description of the state for `state show`."""
    out = {"state_dir": st.state_dir, "initialized": st.initialized, "pending_recovery": st.pending,
           "problems": st.problems}
    if st.ledger is not None:
        counts = {}
        for e in st.ledger["entries"].values():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        out["findings_by_status"] = counts
    if not st.initialized:
        out["note"] = ("no project state yet; it is created by `state init` or by the next finalized audit"
                       + (" from the existing 1.0 ledger" if st.ledger is not None else ""))
        return out
    p, r = st.project, st.registers or new_registers()
    out.update({
        "identity": p["identity"], "revision": p["revision"], "events": len(st.events),
        "history_head": st.head["hash"] if st.head else None,
        "profile_facts": len(p["profile"]["facts"]), "applicability_entries": len(p["applicability"]),
        "requirements": len(r["requirements"]), "decisions": len(r["decisions"]), "risks": len(r["risks"]),
        "legal_items": len(r["legal"]),
        "latest_readiness": p["readiness"][-1] if p.get("readiness") else None,
    })
    return out


def stamp_readiness(project, decision, run_id, revision, commit, now_iso):
    stamp = {"decision": decision, "at": now_iso, "run_id": run_id, "revision": revision}
    if commit:
        stamp["commit"] = commit[:80]
    project.setdefault("readiness", []).append(stamp)
    del project["readiness"][:-READINESS_KEPT]


def run_ref(state_dir):
    """What init-run records in a run directory so finalize can refuse a run from another project."""
    st = load(state_dir, strict=False)
    if not st.initialized:
        return None
    return {"project_id": st.project["identity"]["project_id"], "revision": st.project["revision"]}

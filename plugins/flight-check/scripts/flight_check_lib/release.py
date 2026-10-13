"""Release check: whether the latest recorded ship decision still applies to the files about to be released.

Each finalized audit records the files it assessed in `.flight-check/audited-files.json`: every file git tracks
(read from git's index file, without running git), with a hash of its content as the run started. The release check
compares that record with a checkout, such as the one a CI job makes. It fails when the decision is not ready, when the
project's records changed after it, or when a tracked file was added, changed, or deleted since the audit.

It is a process control, not a security barrier: anyone who can commit can also edit these records. Flight Check's
hash-chained history makes such an edit visible afterwards, but cannot prevent it.
"""

import hashlib
import os
import re
import stat
import struct

from . import constants, freshness, state
from .jsonio import InputError, dump_json, load_json

AUDITED_FILES = "audited-files.json"  # in the state directory (committed) and in each run directory (local)
MAX_INDEX_BYTES = freshness.MAX_LIST_BYTES


def git_dir(root):
    """The repository's git directory: .git itself, or where a worktree's or submodule's .git file points."""
    git = os.path.join(root, ".git")
    if os.path.islink(git):
        return None
    if os.path.isfile(git):
        try:
            with open(git, encoding="utf-8") as fh:
                m = re.match(r"gitdir:\s*(.+)", fh.read(4096).strip())
        except (OSError, UnicodeDecodeError):
            return None
        return os.path.join(root, m.group(1).strip()) if m else None
    return git if os.path.isdir(git) else None


def _varint(data, pos):
    """git's offset encoding for index version 4 path prefixes."""
    byte = data[pos]
    pos += 1
    value = byte & 0x7F
    while byte & 0x80:
        byte = data[pos]
        pos += 1
        value = ((value + 1) << 7) | (byte & 0x7F)
    return value, pos


def _parse_index(data):
    if data[:4] != b"DIRC":
        return None
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        return None
    pos, prev, out = 12, b"", set()
    for _ in range(count):
        mode = struct.unpack(">I", data[pos + 24:pos + 28])[0]
        flags = struct.unpack(">H", data[pos + 60:pos + 62])[0]
        start = pos + (64 if flags & 0x4000 else 62)  # extended flags (version 3 and later) add two bytes
        if version == 4:
            strip, start = _varint(data, start)
            end = data.index(b"\0", start)
            if strip > len(prev):
                return None
            name = prev[:len(prev) - strip] + data[start:end]
            pos = end + 1
        else:
            end = data.index(b"\0", start)
            name = data[start:end]
            pos += (start - pos + len(name) + 8) & ~7  # entries are NUL-padded to a multiple of 8 bytes
        prev = name
        kind = mode & 0o170000
        if kind == 0o040000:
            return None  # a sparse index lists whole directories, so the files in them are unknown
        if kind == 0o100000:  # regular files; symbolic links and submodules have no content to compare
            out.add(os.fsdecode(name))
    return out


def tracked_paths(root):
    """The files git's index lists under root (tracked and staged files), relative to root, or None without a
    readable index. root may be a folder inside the repository."""
    top = state._find_repo_root(root)
    gd = git_dir(top) if top else None
    if not gd:
        return None
    path = os.path.join(gd, "index")
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_INDEX_BYTES:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
        paths = _parse_index(data)
    except (OSError, IndexError, ValueError, struct.error):
        return None
    prefix = os.path.relpath(os.path.realpath(root), top).replace(os.sep, "/")
    if paths is None or prefix == ".":
        return paths
    return {p[len(prefix) + 1:] for p in paths if p.startswith(prefix + "/")}


def _content_hash(path):
    """sha256 of the content with CRLF line ends read as LF, so a Windows checkout and a CI checkout of the same commit
    agree. Files over the hashing limit are compared by size."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size > constants.MAX_HASHED_FILE_BYTES:
        return "size:%d" % st.st_size
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def current_files(root, max_files=freshness.MAX_FILES):
    """({path: content hash}, None) for the tracked files under root in the directories an audit covers, or
    (None, reason)."""
    tracked = tracked_paths(root)
    if tracked is None:
        return None, "no readable git index under %s; the release check compares the files git tracks" % root
    files = {}
    for rel in sorted(tracked):
        parts = rel.split("/")
        if rel.startswith("/") or ".." in parts or not freshness.scanned_path(rel):
            continue
        if len(files) >= max_files:
            return None, "more than %d tracked files" % max_files
        try:
            digest = _content_hash(os.path.join(root, *parts))
        except FileNotFoundError:
            continue  # deleted from the working tree, not yet from the index: not part of what was audited
        except OSError:
            digest = "unreadable"
        if digest:
            files[rel] = digest
    return files, None


def write_run_record(run_dir, root, now_iso):
    """At init-run: the tracked files as the run starts, before any agent reads them."""
    files, reason = current_files(root)
    doc = {"schema_version": "1.0", "created_at": now_iso, "available": files is not None}
    doc.update({"files": files} if files is not None else {"reason": reason})
    dump_json(os.path.join(run_dir, AUDITED_FILES), doc, indent=0)


def _load(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        doc = load_json(path, MAX_INDEX_BYTES)
    except InputError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("available"), bool):
        return None
    if doc["available"] and not (isinstance(doc.get("files"), dict)
                                 and all(isinstance(k, str) and isinstance(v, str) for k, v in doc["files"].items())):
        return None
    return doc


def publish(state_dir, root, run_dir, run_id, decision, now_iso):
    """At finalize: record which files the new decision describes. Uses the run's record from init-run; a run without
    one (started without --root) records the files as they are now."""
    rec = _load(os.path.join(run_dir, AUDITED_FILES)) if run_dir else None
    if rec is None:
        files, reason = current_files(root)
        rec = {"created_at": now_iso, "available": files is not None, "files": files, "reason": reason}
    doc = {"schema_version": "1.0", "run_id": run_id, "decision": decision, "files_as_of": rec["created_at"],
           "available": rec["available"]}
    doc.update({"files": rec["files"]} if rec["available"] else {"reason": rec.get("reason") or "unknown"})
    dump_json(os.path.join(state_dir, AUDITED_FILES), doc, indent=0)


def check(st, root, inputs_changed, identity_problems, allow_accepted_risks=False, max_listed=20):
    """Whether the latest ship decision applies to the tracked files under root now. Returns a dict with ok, problems,
    and the files that differ from the audited ones."""
    problems = list(st.problems) + list(identity_problems)
    stamps = st.project.get("readiness") or []
    latest = stamps[-1] if stamps else None
    if not latest:
        problems.append("No finalized audit has recorded a ship decision.")
    elif latest["decision"] == constants.GATE_READY_ACCEPTED and not allow_accepted_risks:
        problems.append("The latest ship decision is %s (run %s). Releasing with accepted risks needs "
                        "--allow-accepted-risks." % (latest["decision"], latest["run_id"]))
    elif latest["decision"] not in (constants.GATE_READY, constants.GATE_READY_ACCEPTED):
        problems.append("The latest ship decision is %s (run %s)." % (latest["decision"], latest["run_id"]))
    problems.extend("After that decision, %s." % i for i in inputs_changed)

    diff = {"added": [], "changed": [], "deleted": []}
    rec = _load(os.path.join(st.state_dir, AUDITED_FILES))
    if rec is None:
        problems.append("There is no record of the audited files (%s). Audits finalized with Flight Check 1.5 or "
                        "later write it; commit it with the other state files." % AUDITED_FILES)
    elif latest and rec.get("run_id") != latest["run_id"]:
        problems.append("The record of audited files is from run %s, but the latest decision is from run %s."
                        % (rec.get("run_id"), latest["run_id"]))
    elif not rec["available"]:
        problems.append("The audit could not record its files: %s." % rec.get("reason", "unknown"))
    else:
        files, reason = current_files(root)
        if files is None:
            problems.append("Cannot list the files to release: %s." % reason)
        else:
            old = rec["files"]
            diff = {"added": sorted(set(files) - set(old)), "deleted": sorted(set(old) - set(files)),
                    "changed": sorted(p for p in files if p in old and files[p] != old[p])}
            n = sum(len(v) for v in diff.values())
            if n:
                problems.append("%d tracked file(s) differ from the files the decision was made on (%d added, %d "
                                "changed, %d deleted). A file added before the audit but not yet tracked by git counts "
                                "as added." % (n, len(diff["added"]), len(diff["changed"]), len(diff["deleted"])))
    listed = {k: v[:max_listed] for k, v in diff.items()}
    return {"ok": not problems, "decision": latest, "problems": problems, "files": listed,
            "files_not_listed": sum(len(v) for v in diff.values()) - sum(len(v) for v in listed.values())}

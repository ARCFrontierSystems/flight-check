"""Mechanical evidence verification.

For every located evidence item (code, config, documentation, dependency) this
checks, without executing anything, that:
  * the cited path resolves inside the audit root (symlinks are resolved first),
  * the file exists and the cited lines exist,
  * the quoted text appears within the cited lines (whitespace-insensitive;
    redaction markers and "..." act as gaps).
"""

import os
import re

from . import constants, textsafety

MAX_FILE_BYTES = 5 * 1024 * 1024
LINE_TOLERANCE = 2

OK = "OK"
FAILURES = ("OUT_OF_ROOT", "FILE_MISSING", "LINES_OUT_OF_RANGE", "QUOTE_MISMATCH")
UNCHECKED = ("NOT_CHECKED", "UNCHECKABLE_QUOTE", "FILE_TOO_LARGE", "NOT_TEXT")

_GAP = re.compile(r"\[REDACTED(?::[^\]\n]{0,40})?\]|\.\.\.|…")
_WS = re.compile(r"\s+")


def _norm(text):
    return _WS.sub(" ", textsafety.sanitize(text)).strip()


def resolve_within(root, rel_path):
    """Return (absolute_path, None) when rel_path stays inside root, else (None, reason)."""
    root_real = os.path.realpath(root)
    if os.path.isabs(rel_path) or re.match(r"^[A-Za-z]:", rel_path):
        return None, "OUT_OF_ROOT"
    candidate = os.path.realpath(os.path.join(root_real, rel_path))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        return None, "OUT_OF_ROOT"
    return candidate, None


def quote_matches(quote, window_text):
    segments = [_norm(s) for s in _GAP.split(quote)]
    segments = [s for s in segments if len(s) >= 3]
    if not segments:
        return None  # nothing checkable, e.g. the quote is only a redaction marker
    haystack = _norm(window_text)
    pos = 0
    for seg in segments:
        idx = haystack.find(seg, pos)
        if idx < 0:
            return False
        pos = idx + len(seg)
    return True


class FileCache(object):
    def __init__(self):
        self._lines = {}

    def lines(self, path):
        if path not in self._lines:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                self._lines[path] = "TOO_LARGE"
            else:
                with open(path, "rb") as fh:
                    raw = fh.read()
                if b"\x00" in raw[:8192]:
                    self._lines[path] = "NOT_TEXT"
                else:
                    self._lines[path] = raw.decode("utf-8", errors="replace").splitlines()
        return self._lines[path]


def check_item(ev, root, cache):
    kind = ev.get("kind")
    if kind not in constants.LOCATED_EVIDENCE_KINDS:
        return "NOT_CHECKED"
    path, problem = resolve_within(root, ev["path"])
    if problem:
        return problem
    if not os.path.isfile(path):
        return "FILE_MISSING"
    lines = cache.lines(path)
    if lines == "TOO_LARGE":
        return "FILE_TOO_LARGE"
    if lines == "NOT_TEXT":
        return "NOT_TEXT"
    start, end = ev["start_line"], ev["end_line"]
    if start > len(lines) or end > len(lines) + LINE_TOLERANCE:
        return "LINES_OUT_OF_RANGE"
    lo = max(0, start - 1 - LINE_TOLERANCE)
    hi = min(len(lines), end + LINE_TOLERANCE)
    result = quote_matches(ev["quote"], "\n".join(lines[lo:hi]))
    if result is None:
        return "UNCHECKABLE_QUOTE"
    return OK if result else "QUOTE_MISMATCH"


def summarize(results):
    """Collapse per-item results into PASSED / FAILED / PARTIAL / NOT_APPLICABLE."""
    located = [r for r in results if r != "NOT_CHECKED"]
    if not located:
        return "NOT_APPLICABLE"
    if any(r in FAILURES for r in located):
        return "FAILED"
    if all(r == OK for r in located):
        return "PASSED"
    return "PARTIAL"


def annotate(items, root, cache):
    """Add a 'check' field to each evidence dict in items; return the summary."""
    results = []
    for ev in items:
        ev["check"] = check_item(ev, root, cache)
        results.append(ev["check"])
    return summarize(results)

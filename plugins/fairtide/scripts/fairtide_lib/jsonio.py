"""JSON file helpers with size limits and atomic writes."""

import json
import os
import tempfile

MAX_JSON_BYTES = 20 * 1024 * 1024


class InputError(Exception):
    """Raised for unreadable or invalid input files."""


def load_json(path):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise InputError("cannot read %s: %s" % (path, exc.strerror or exc))
    if size > MAX_JSON_BYTES:
        raise InputError("%s is larger than %d bytes" % (path, MAX_JSON_BYTES))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError("%s is not valid JSON: line %d column %d: %s" % (path, exc.lineno, exc.colno, exc.msg))
    except UnicodeDecodeError:
        raise InputError("%s is not UTF-8 text" % path)


def dump_json(path, data):
    """Write JSON atomically so a crash never leaves a half-written ledger or report."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fairtide-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fairtide-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_bytes(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fairtide-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

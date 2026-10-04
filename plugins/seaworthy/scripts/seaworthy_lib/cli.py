"""Command-line interface. Every command prints JSON (or text for `gate`) and exits non-zero on failure."""

import argparse
import datetime
import json
import os
import re
import sys

from . import VERSION, catalog, constants, finalize, gate, ledger, minischema, packet, render_md, validate
from .jsonio import InputError, dump_json, load_json, write_bytes, write_text


def _print(obj):
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _fail(message, code=1, **extra):
    out = {"ok": False, "error": message}
    out.update(extra)
    _print(out)
    return code


def _now(args):
    if getattr(args, "now", None):
        dt = datetime.datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return dt


def _read_git_commit(root):
    """Read the checked-out commit from .git files without running git."""
    git = os.path.join(root, ".git")
    try:
        if os.path.isfile(git):
            with open(git, "r", encoding="utf-8") as fh:
                m = re.match(r"gitdir:\s*(.+)", fh.read().strip())
            if not m:
                return None
            git = os.path.join(root, m.group(1))
        with open(os.path.join(git, "HEAD"), "r", encoding="utf-8") as fh:
            head = fh.read().strip()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            return head
        m = re.match(r"ref:\s*(refs/[\w./-]+)$", head)
        if not m or ".." in m.group(1):
            return None
        ref_path = os.path.join(git, m.group(1))
        if os.path.isfile(ref_path):
            with open(ref_path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                return value if re.fullmatch(r"[0-9a-f]{40}", value) else None
        packed = os.path.join(git, "packed-refs")
        if os.path.isfile(packed):
            with open(packed, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split(" ")
                    if len(parts) == 2 and parts[1] == m.group(1) and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                        return parts[0]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def cmd_version(args):
    _print({"ok": True, "version": VERSION, "schema_version": constants.SCHEMA_VERSION})
    return 0


def cmd_init_run(args):
    now = _now(args)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    if args.suffix:
        if not re.fullmatch(r"[a-z0-9]{1,12}", args.suffix):
            return _fail("--suffix must be 1-12 lowercase letters or digits")
        run_id += "-" + args.suffix
    base = os.path.abspath(args.base)
    run_dir = os.path.join(base, run_id)
    if os.path.exists(run_dir):
        return _fail("run directory already exists: %s" % run_dir)
    os.makedirs(run_dir)
    marker = os.path.join(base, ".gitignore")
    if not os.path.exists(marker):
        write_text(marker, "# Seaworthy run output can quote project files; do not commit it.\n*\n")
    _print({"ok": True, "run_id": run_id, "run_dir": run_dir, "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": _read_git_commit(os.path.abspath(args.root)) if args.root else None,
            "seaworthy_version": VERSION})
    return 0


def cmd_validate(args):
    errors, warnings = validate.validate_run_dir(args.run_dir)
    _print({"ok": not errors, "errors": errors, "warnings": warnings})
    return 0 if not errors else 1


def cmd_finalize(args):
    led_path = None if args.no_ledger else (args.ledger or os.path.join(os.path.abspath(args.root), ".seaworthy", "ledger.json"))
    try:
        result = finalize.finalize(args.run_dir, args.root, led_path, args.now)
    except (InputError, ledger.LedgerError) as exc:
        return _fail(str(exc))
    _print(result)
    return 0 if result["ok"] else 1


def cmd_render(args):
    try:
        final = finalize.load_final(args.run_dir)
    except InputError as exc:
        return _fail(str(exc))
    summary_path = os.path.join(args.run_dir, "summary.json")
    summary = None
    if os.path.exists(summary_path):
        summary = load_json(summary_path)
        errors, warnings = validate.validate_summary(summary, final)
        if errors:
            _print({"ok": False, "errors": errors, "warnings": warnings})
            return 1
    elif not args.allow_missing_summary:
        return _fail("summary.json is missing; write it (see the audit skill) or pass --allow-missing-summary")
    out = os.path.join(args.run_dir, "report.md")
    write_text(out, render_md.render(final, summary))
    _print({"ok": True, "report": out, "gate": final["gate"]["decision"]})
    return 0


def cmd_gate(args):
    try:
        final = load_json(args.final)
    except InputError as exc:
        return _fail(str(exc))
    g = final["gate"]
    if args.json:
        _print(g)
    else:
        sys.stdout.write("Seaworthy ship decision: %s\n" % g["decision"])
        for r in g["reasons"]:
            sys.stdout.write("  %s\n" % r)
        sys.stdout.write("%s\n" % g["scope_statement"])
    if not args.exit_code:
        return 0
    code = g["exit_code"]
    if code == constants.GATE_EXIT_CODES[constants.GATE_READY_ACCEPTED] and args.allow_accepted_risks:
        return 0
    return code


def _load_ledger(args):
    return ledger.load(args.ledger, create=False)


def cmd_ledger_show(args):
    led = _load_ledger(args)
    if args.id:
        _, entry = ledger.find_entry(led, args.id)
        _print({"ok": True, "entry": entry})
    else:
        _print({"ok": True, "entries": sorted(({"id": e["id"], "status": e["status"], "severity": e["severity"], "domain": e["domain"],
                                                   "title": e["title"], "legal_status": e.get("legal", {}).get("status")}
                                                  for e in led["entries"].values()), key=lambda e: e["id"]),
                "control_acceptances": led["control_acceptances"], "runs": led["runs"][-10:]})
    return 0


def _ledger_write(args, fn):
    try:
        led = _load_ledger(args)
        fn(led)
        ledger.save(args.ledger, led)
    except (InputError, ledger.LedgerError) as exc:
        return _fail(str(exc))
    _print({"ok": True})
    return 0


def cmd_ledger_accept(args):
    now = _now(args)
    fields = {"risk": args.risk, "reason": args.reason, "owner": args.owner, "date": args.date, "scope": args.scope,
              "compensating_controls": args.compensating_controls, "review_date": args.review_date}
    return _ledger_write(args, lambda led: ledger.accept(led, args.ref, fields, now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.date()))


def cmd_ledger_revoke(args):
    now = _now(args)
    return _ledger_write(args, lambda led: ledger.revoke_acceptance(led, args.ref, args.note, now.strftime("%Y-%m-%dT%H:%M:%SZ")))


def cmd_ledger_legal(args):
    now = _now(args)
    return _ledger_write(args, lambda led: ledger.set_legal_status(led, args.id, args.status, args.note, args.source,
                                                                 now.strftime("%Y-%m-%dT%H:%M:%SZ"), args.reason))


def cmd_ledger_remediate(args):
    now = _now(args)
    return _ledger_write(args, lambda led: ledger.record_remediation(led, args.id, args.note, args.file or [], now.strftime("%Y-%m-%dT%H:%M:%SZ")))


def cmd_ledger_close(args):
    now = _now(args)
    return _ledger_write(args, lambda led: ledger.close(led, args.id, args.note, now.strftime("%Y-%m-%dT%H:%M:%SZ")))


def cmd_packet(args):
    try:
        final = finalize.load_final(args.run_dir)
        request = load_json(args.request)
        led = ledger.load(args.ledger, create=False) if args.ledger else None
    except (InputError, ledger.LedgerError) as exc:
        return _fail(str(exc))
    now = _now(args)
    try:
        data, md_text, meta = packet.build(final, request, led, now, args.page_size)
    except packet.PacketError as exc:
        return _fail("packet request rejected", errors=str(exc).split("\n"))
    out = os.path.abspath(args.out)
    write_bytes(out, data)
    md_out = os.path.splitext(out)[0] + ".md"
    write_text(md_out, md_text)
    if led is not None:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for fid in meta["findings"]:
            _, entry = ledger.find_entry(led, fid)
            if "legal" in entry:
                entry["legal"]["history"].append({"at": stamp, "status": entry["legal"]["status"], "source": "seaworthy",
                                                  "note": "included in attorney review packet %s" % os.path.basename(out)})
        ledger.save(args.ledger, led)
    _print({"ok": True, "pdf": out, "markdown": md_out, "pages": meta["pages"], "findings": meta["findings"],
            "replaced_characters": meta["replaced_characters"]})
    return 0


def cmd_schema_check(args):
    """Validate an arbitrary JSON document against a named definition (useful for CI consumers)."""
    try:
        doc = load_json(args.file)
    except InputError as exc:
        return _fail(str(exc))
    errors = minischema.validate_def(doc, catalog.schema(), args.definition)
    _print({"ok": not errors, "errors": errors})
    return 0 if not errors else 1


def build_parser():
    p = argparse.ArgumentParser(prog="seaworthy", description="Seaworthy deterministic tooling (no network, no project code execution).")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    s = sub.add_parser("version")
    s.set_defaults(func=cmd_version)

    s = sub.add_parser("init-run", help="create a run directory and print its id")
    s.add_argument("--base", required=True, help="directory that holds run directories")
    s.add_argument("--root", help="audit root, used to read the current git commit from .git files")
    s.add_argument("--suffix")
    s.add_argument("--now")
    s.set_defaults(func=cmd_init_run)

    s = sub.add_parser("validate", help="validate agent outputs in a run directory")
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("finalize", help="validate, assemble, check evidence, merge ledger, compute the ship gate")
    s.add_argument("run_dir")
    s.add_argument("--root", required=True, help="audit root; evidence paths are resolved inside it")
    s.add_argument("--ledger", help="ledger path (default <root>/.seaworthy/ledger.json)")
    s.add_argument("--no-ledger", action="store_true", help="do not read or write a ledger")
    s.add_argument("--now")
    s.set_defaults(func=cmd_finalize)

    s = sub.add_parser("render", help="write report.md from audit.final.json and summary.json")
    s.add_argument("run_dir")
    s.add_argument("--allow-missing-summary", action="store_true")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("gate", help="print the ship decision; with --exit-code, exit non-zero unless ready")
    s.add_argument("final", help="path to audit.final.json")
    s.add_argument("--exit-code", action="store_true")
    s.add_argument("--allow-accepted-risks", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_gate)

    s = sub.add_parser("packet", help="build the Attorney Review Packet (PDF and Markdown)")
    s.add_argument("run_dir")
    s.add_argument("--request", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--ledger")
    s.add_argument("--page-size", choices=sorted(["letter", "a4"]), default="letter")
    s.add_argument("--now")
    s.set_defaults(func=cmd_packet)

    s = sub.add_parser("schema-check", help="validate a JSON file against a schema definition")
    s.add_argument("file")
    s.add_argument("--definition", required=True)
    s.set_defaults(func=cmd_schema_check)

    lp = sub.add_parser("ledger", help="inspect or update the finding ledger")
    lsub = lp.add_subparsers(dest="ledger_command")
    lsub.required = True

    s = lsub.add_parser("show")
    s.add_argument("--ledger", required=True)
    s.add_argument("--id")
    s.set_defaults(func=cmd_ledger_show)

    s = lsub.add_parser("accept", help="record a user's explicit risk acceptance")
    s.add_argument("--ledger", required=True)
    s.add_argument("--ref", required=True, help="finding ID (SW-0001) or control ID")
    for name in ("risk", "reason", "owner", "date", "scope", "compensating-controls"):
        s.add_argument("--" + name, required=True)
    s.add_argument("--review-date")
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_accept)

    s = lsub.add_parser("revoke", help="revoke a risk acceptance")
    s.add_argument("--ledger", required=True)
    s.add_argument("--ref", required=True)
    s.add_argument("--note", required=True)
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_revoke)

    s = lsub.add_parser("legal", help="update a finding's legal review status")
    s.add_argument("--ledger", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--status", required=True, choices=list(constants.LEGAL_STATUSES))
    s.add_argument("--note", default="")
    s.add_argument("--source", required=True, choices=["user", "seaworthy"])
    s.add_argument("--reason")
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_legal)

    s = lsub.add_parser("remediate", help="record a remediation (pending verification by re-audit)")
    s.add_argument("--ledger", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--note", required=True)
    s.add_argument("--file", action="append")
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_remediate)

    s = lsub.add_parser("close", help="close a VERIFIED or NOT_REPRODUCED finding on the user's confirmation")
    s.add_argument("--ledger", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--note", required=True)
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_close)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ledger.LedgerError as exc:
        return _fail(str(exc))
    except InputError as exc:
        return _fail(str(exc))

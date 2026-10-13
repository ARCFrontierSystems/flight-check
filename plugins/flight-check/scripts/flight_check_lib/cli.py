"""Command-line interface. Every command prints JSON (or text for `gate`) and exits non-zero on failure."""

import argparse
import datetime
import json
import os
import re
import sys

from . import VERSION, catalog, constants, estimate, finalize, freshness, gate, ledger, legal, lockfile, minischema, packet, profile, registers, release, render_md, sarif, state, status, unblock, validate
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


def _decision_inputs_changed(st, today):
    return unblock.decision_inputs_changed(st, today)


def _incremental_scope(state_dir, root, scanned, today):
    """init-run --changed: which domains to reassess and which to carry forward, or a full scope with the reason."""
    full = {"mode": "full", "reassess_domains": catalog.domain_ids(), "carried_forward_domains": [],
            "agents": sorted({d["agent"] for d in catalog.domains()})}
    try:
        st = state.load(state_dir, root=root, strict=False)
    except (state.StateError, InputError) as exc:
        return dict(full, reasons=["the project state could not be read (%s), so every domain is assessed" % exc])
    if not st.initialized or st.problems:
        return dict(full, reasons=["there is no usable project state from an earlier audit, so every domain is assessed"])
    ch = freshness.changes(root, st, scanned=scanned)
    inputs = _decision_inputs_changed(st, today)
    if inputs and not ch["changed_files"]:
        ch["reasons"].append("no file changed, but inputs to the ship decision did, so a new decision is needed: %s"
                             % "; ".join(inputs))
    return {"mode": "changed", "baseline_run_id": ch["baseline"]["run_id"], "changed_files": ch["changed_files"],
            "decision_inputs_changed": inputs,
            "files": ch["files"][:50], "widened": ch["widened"], "reasons": ch["reasons"],
            "reassess_domains": ch["reassess_domains"], "carried_forward_domains": ch["carried_forward_domains"],
            "carried_forward_from": ch["carried_forward_from"],
            "agents": sorted({d["agent"] for d in catalog.domains() if d["id"] in ch["reassess_domains"]}),
            "decisions_to_review": ch["decisions_to_review"], "latest_readiness": ch["latest_readiness"]}


def _with_recheck(scope, controls):
    """Add the domains of the controls the user asked to re-check to the run, so they are reassessed instead of carried
    forward. Re-checking only ever reassesses more."""
    cat = {c["id"]: c["domain"] for c in catalog.controls()}
    domains = sorted({cat[c] for c in controls})
    scope = dict(scope, recheck={"controls": controls, "domains": domains})
    if scope["mode"] == "changed":
        order = catalog.domain_ids()
        scope["reassess_domains"] = [d for d in order if d in set(scope["reassess_domains"]) | set(domains)]
        scope["carried_forward_domains"] = [d for d in scope["carried_forward_domains"] if d not in domains]
        scope["carried_forward_from"] = {d: r for d, r in (scope.get("carried_forward_from") or {}).items() if d not in domains}
        scope["agents"] = sorted({d["agent"] for d in catalog.domains() if d["id"] in scope["reassess_domains"]})
        scope["reasons"] = list(scope.get("reasons") or []) + [
            "re-check requested for %s, so %s %s reassessed" % (", ".join(controls), ", ".join(domains),
                                                                "is" if len(domains) == 1 else "are")]
    return scope


def _project_records(state_dir):
    """What the audit skill passes to agents from the project's records: accepted decisions' assertions to check, and
    the owner's applicability overrides. Empty when there is no usable state."""
    try:
        st = state.load(state_dir, strict=False)
    except (state.StateError, InputError):
        return None
    if not st.initialized or st.problems:
        return None
    recorded = sorted(((st.ledger or {}).get("entries") or {}).values(), key=lambda e: e["id"], reverse=True)[:200]
    return {"decision_assertions": registers.active_assertions(st.registers)[:40],
            "recorded_findings": [{"id": e["id"], "domain": e["domain"], "rule": e["rule"], "title": e["title"][:160],
                                   "file": ledger._entry_path(e), "status": e["status"]} for e in recorded],
            "applicability_overrides": {a["domain"]: {"classification": a["override"]["classification"],
                                                      "reason": a["override"]["reason"]}
                                        for a in st.project["applicability"] if a.get("override")}}


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
    root = os.path.abspath(args.root) if args.root else None
    if args.changed and not root:
        return _fail("--changed needs --root")
    recheck = [c.strip().upper() for c in (args.recheck or "").split(",") if c.strip()]
    if recheck:
        known = {c["id"] for c in catalog.controls()}
        unknown = [c for c in recheck if c not in known]
        if unknown:
            return _fail("--recheck: unknown control ID(s): %s" % ", ".join(unknown))
        if not root:
            return _fail("--recheck needs --root")
    scanned = freshness.scan(root) if root else None
    scope = _incremental_scope(os.path.dirname(base), root, scanned, now.date()) if args.changed or recheck else None
    if scope and recheck:
        scope = _with_recheck(scope, recheck)
    if scope and scope["mode"] == "changed" and not scope["changed_files"] and not scope["reassess_domains"] \
            and not scope.get("decision_inputs_changed"):
        _print({"ok": True, "nothing_to_reassess": True, "scope": scope,
                "message": "No file changed since run %s and every applicable domain was assessed then, so its "
                           "ship decision still describes these files. No run was started." % scope["baseline_run_id"]})
        return 0
    new_base = not os.path.isdir(base)
    # Run directories hold raw agent replies that quote the project, so only the owner may read them.
    os.makedirs(run_dir, mode=0o700)
    os.chmod(run_dir, 0o700)
    if new_base:
        os.chmod(base, 0o700)
    if args.inventory_from:
        src = os.path.join(os.path.abspath(args.inventory_from), "inventory.json")
        try:
            dump_json(os.path.join(run_dir, "inventory.json"), load_json(src))
        except InputError as exc:
            return _fail("cannot reuse inventory: %s" % exc)
    try:
        ref = state.run_ref(os.path.dirname(base))
    except (state.StateError, InputError):
        ref = None
    if ref:
        # Lets finalize refuse to merge this run into another project's state.
        dump_json(os.path.join(run_dir, "run-ref.json"), dict(ref, run_id=run_id))
    if scanned is not None:
        freshness.write_snapshot(run_dir, scanned, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        release.write_run_record(run_dir, root, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    records = _project_records(os.path.dirname(base))
    if scope:
        dump_json(os.path.join(run_dir, "scope.json"), dict(scope, schema_version="1.0", run_id=run_id))
    marker = os.path.join(base, ".gitignore")
    if not os.path.exists(marker):
        write_text(marker, "# Flight Check run output can quote project files; do not commit it.\n*\n")
    _print({"ok": True, "run_id": run_id, "run_dir": run_dir, "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": _read_git_commit(os.path.abspath(args.root)) if args.root else None,
            "flight_check_version": VERSION, **({"scope": scope} if scope else {}),
            **({"estimate": estimate.estimate(len(scanned[0]), len(scope["agents"]) if scope and scope["mode"] == "changed" else None,
                                              args.passes)} if scanned is not None else {}),
            **({"project_records": records} if records else {})})
    return 0


def cmd_validate(args):
    errors, warnings = validate.validate_run_dir(args.run_dir)
    _print({"ok": not errors, "errors": errors, "warnings": warnings})
    return 0 if not errors else 1


def cmd_finalize(args):
    led_path = None if args.no_ledger else (args.ledger or os.path.join(os.path.abspath(args.root), ".flight-check", "ledger.json"))
    try:
        result = finalize.finalize(args.run_dir, args.root, led_path, args.now)
    except (InputError, ledger.LedgerError, state.StateError, lockfile.LockError) as exc:
        return _fail(str(exc))
    _print(result)
    return 0 if result["ok"] else 1


REPLY_NAME = re.compile(r"(inventory|verification|summary|part-[a-z0-9-]{2,40})\.json")
MAX_REPLY_BYTES = 5 * 1024 * 1024


def _transcript_reply(raw):
    """The final reply in an agent transcript (JSON Lines, one event per line, as in a background agent's output
    file): the text blocks of the last assistant message that has text. None when raw is not such a transcript."""
    final_id, texts, seen = None, [], False
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        seen = True
        parts = [b.get("text") for b in message["content"]
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        if not parts:
            continue
        if message.get("id") is None or message.get("id") != final_id:
            final_id, texts = message.get("id"), []
        texts.extend(parts)
    return "\n".join(texts) if seen and texts else None


def _reply_text(raw):
    """Agent replies that Claude Code stored in a file: a JSON list of text blocks, an agent transcript (JSON Lines),
    or plain text."""
    try:
        data = json.loads(raw)
    except ValueError:
        reply = _transcript_reply(raw)
        return raw if reply is None else reply
    blocks = data if isinstance(data, list) else [data]
    texts = [b.get("text") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return "\n".join(texts) if texts else raw


def extract_reply_json(text):
    """Return the JSON object an agent returned: its last fenced json block that parses, else the first object."""
    for block in reversed(re.findall(r"```json[ \t]*\n(.*?)\n```", text, re.S)):
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and len(obj) > 1:
            return obj
    return None


def cmd_save_reply(args):
    """Save an agent reply that was too large to come back inline, exactly as returned, without retyping it."""
    name = args.as_name
    if not REPLY_NAME.fullmatch(name):
        return _fail("--as must be inventory.json, verification.json, summary.json, or part-<agent>.json")
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isfile(os.path.join(run_dir, "run.json")):
        return _fail("%s is not a Flight Check run directory (no run.json)" % run_dir)
    try:
        if os.path.getsize(args.source) > MAX_REPLY_BYTES:
            return _fail("the saved reply is larger than 5 MB")
        with open(args.source, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        return _fail("cannot read %s: %s" % (args.source, exc))
    obj = extract_reply_json(_reply_text(raw))
    if obj is None:
        return _fail("no JSON object found in %s" % args.source)
    if name.startswith("part-"):
        obj["agent"] = name[len("part-"):-len(".json")]
    out = os.path.join(run_dir, name)
    dump_json(out, obj)
    _print({"ok": True, "path": out, "keys": sorted(obj)})
    return 0


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
        sys.stdout.write("Flight Check ship decision: %s\n" % g["decision"])
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


def _legal_status(args, entry, _cache={}):
    legal_block = entry.get("legal") or {}
    if "status" in legal_block:
        return legal_block["status"]
    item_id = legal_block.get("item")
    if not item_id:
        return None
    key = os.path.abspath(args.ledger)
    if key not in _cache:
        st = state.load(os.path.dirname(key), strict=False)
        _cache[key] = {i["id"]: i["status"] for i in (st.registers or {}).get("legal", [])}
    return _cache[key].get(item_id)


def cmd_ledger_show(args):
    led = _load_ledger(args)
    if args.id:
        _, entry = ledger.find_entry(led, args.id)
        _print({"ok": True, "entry": entry})
    else:
        _print({"ok": True, "entries": sorted(({"id": e["id"], "status": e["status"], "severity": e["severity"], "domain": e["domain"],
                                                   "title": e["title"], "legal_status": _legal_status(args, e)}
                                                  for e in led["entries"].values()), key=lambda e: e["id"]),
                "control_acceptances": led["control_acceptances"], "runs": led["runs"][-10:]})
    return 0


_FINDING_ID = re.compile(r"FC-[0-9]{4,}")
_CONTROL_ID = re.compile(r"[A-Z0-9]+(-[A-Z0-9]+)+")


def _record_of(ref):
    if _FINDING_ID.fullmatch(ref or ""):
        return ref
    if _CONTROL_ID.fullmatch(ref or ""):
        return "control:" + ref
    return "state"


def _status_of(led, ref):
    entry = registers.entries_by_id(led).get(ref)
    if entry is not None:
        return {"status": entry["status"], "legal": (entry.get("legal") or {}).get("status")}
    acc = led["control_acceptances"].get(ref)
    return {"accepted": "yes" if acc else "no"}


def _ledger_write(args, fn, command, ref, actor="user", reason=None):
    now = _now(args)
    try:
        with state.transaction(os.path.dirname(os.path.abspath(args.ledger)), actor, command,
                               now.strftime("%Y-%m-%dT%H:%M:%SZ"), ledger_name=os.path.basename(args.ledger),
                               require_ledger=True) as tx:
            before = _status_of(tx.ledger, ref)
            fn(tx.ledger, now)
            after = _status_of(tx.ledger, ref)
            change = {k: [before.get(k), after.get(k)] for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
            tx.event(_record_of(ref), change, reason)
    except (InputError, ledger.LedgerError, state.StateError) as exc:
        return _fail(str(exc))
    _print({"ok": True})
    return 0


def cmd_ledger_accept(args):
    fields = {"risk": args.risk, "reason": args.reason, "owner": args.owner, "date": args.date, "scope": args.scope,
              "compensating_controls": args.compensating_controls, "review_date": args.review_date}
    return _ledger_write(args, lambda led, now: ledger.accept(led, args.ref, fields, now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.date()),
                         "ledger accept", args.ref, reason=args.reason)


def cmd_ledger_revoke(args):
    return _ledger_write(args, lambda led, now: ledger.revoke_acceptance(led, args.ref, args.note, now.strftime("%Y-%m-%dT%H:%M:%SZ")),
                         "ledger revoke", args.ref, reason=args.note)


def _has_item(tx, finding_id):
    entry = registers.entries_by_id(tx.ledger).get(finding_id)
    return bool(entry and (entry.get("legal") or {}).get("item"))


def cmd_ledger_legal(args):
    """1.0 command: maps the 1.0 legal statuses onto the finding's legal review item."""
    status = constants.V1_LEGAL_TO_ITEM[args.status]
    note = " | ".join(x for x in (args.note, ("reason: " + args.reason) if args.reason else None) if x) or None
    try:
        with state.transaction(os.path.dirname(os.path.abspath(args.ledger)), args.source, "ledger legal",
                               _now(args).strftime("%Y-%m-%dT%H:%M:%SZ"), ledger_name=os.path.basename(args.ledger),
                               require_ledger=True) as tx:
            current = legal.item_for_finding(tx.registers, tx.project, tx.ledger, args.id, tx.now_iso, create=False)["status"] \
                if _has_item(tx, args.id) else "NEEDS_REVIEW"
            flow = constants.LEGAL_ITEM_FLOW
            backward = current in flow and status in flow and flow.index(status) < flow.index(current)
            early_close = status == "RESOLVED" and current not in ("ACTION_REQUIRED", "REVIEWED", "RESOLVED")
            if (backward or early_close) and not args.reason:
                raise legal.LegalError("moving %s from %s to %s needs --reason (as in 1.0)" % (args.id, current, status))
            rec, old = legal.set_status(tx.registers, tx.project, tx.ledger, args.id, status, args.source, tx.now_iso, note)
            tx.event(rec["id"], {"status": [old, rec["status"]]}, note)
    except (InputError, ledger.LedgerError, state.StateError, legal.LegalError) as exc:
        return _fail(str(exc))
    _print({"ok": True, "finding": args.id, "item": rec["id"], "status": rec["status"]})
    return 0


def cmd_ledger_remediate(args):
    return _ledger_write(args, lambda led, now: ledger.record_remediation(led, args.id, args.note, args.file or [], now.strftime("%Y-%m-%dT%H:%M:%SZ")),
                         "ledger remediate", args.id, reason=args.note)


def cmd_ledger_close(args):
    return _ledger_write(args, lambda led, now: ledger.close(led, args.id, args.note, now.strftime("%Y-%m-%dT%H:%M:%SZ")),
                         "ledger close", args.id, reason=args.note)


def cmd_packet(args):
    if not args.ledger:
        return _packet(args, None)
    try:
        with state.transaction(os.path.dirname(os.path.abspath(args.ledger)), "flight-check", "packet",
                               _now(args).strftime("%Y-%m-%dT%H:%M:%SZ"), ledger_name=os.path.basename(args.ledger),
                               require_ledger=True) as tx:
            return _packet(args, tx)
    except (InputError, ledger.LedgerError, state.StateError) as exc:
        return _fail(str(exc))


def _packet_out_problem(out, md_out, run_dir):
    """packet is pre-approved, so where it writes is limited: a .pdf (and its .md beside it), never through a link,
    and outside the run directory never over an existing file."""
    if not out.lower().endswith(".pdf"):
        return "--out must name a .pdf file"
    inside = os.path.commonpath([run_dir, out]) == run_dir
    for path in (out, md_out):
        if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
            return "%s is or is inside a symbolic link; refusing to write through it" % path
        if os.path.exists(path) and not (inside and os.path.isfile(path)):
            return "%s already exists; choose a new file name (outside the run directory a packet never replaces a file)" % path
    return None


def _packet(args, tx):
    try:
        final = finalize.load_final(args.run_dir)
        request = load_json(args.request)
    except (InputError, ledger.LedgerError) as exc:
        return _fail(str(exc))
    led = tx.ledger if tx else None
    now = _now(args)
    final = dict(final, findings=_with_carried(final, args.run_dir))  # carried-forward findings can go to counsel too
    try:
        data, md_text, meta = packet.build(final, request, led, now, args.page_size, regs=tx.registers if tx else None)
    except packet.PacketError as exc:
        return _fail("packet request rejected", errors=str(exc).split("\n"))
    out = os.path.abspath(args.out)
    md_out = os.path.splitext(out)[0] + ".md"
    problem = _packet_out_problem(out, md_out, os.path.abspath(args.run_dir))
    if problem:
        return _fail(problem)
    write_bytes(out, data)
    write_text(md_out, md_text)
    if tx is not None:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        included = [i for i in list(meta["item_for_finding"].values()) + meta["items"] if i]
        for item in tx.registers["legal"]:
            if item["id"] in included:
                item["history"].append({"at": stamp, "status": item["status"], "source": "flight-check",
                                        "note": "included in attorney review packet %s" % os.path.basename(out)})
        tx.event("state", {}, "Attorney Review Packet %s covers %s." % (
            os.path.basename(out), ", ".join(meta["findings"] + meta["items"])))
    _print({"ok": True, "pdf": out, "markdown": md_out, "pages": meta["pages"], "findings": meta["findings"],
            "replaced_characters": meta["replaced_characters"]})
    return 0


def cmd_runs(args):
    base = os.path.abspath(args.base)
    runs = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            run_dir = os.path.join(base, name)
            if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(-[a-z0-9]{1,12})?", name) or not os.path.isdir(run_dir):
                continue
            entry = {"run_id": name, "run_dir": run_dir, "finalized": False}
            final_path = os.path.join(run_dir, "audit.final.json")
            if os.path.exists(final_path):
                try:
                    final = load_json(final_path)
                    entry.update({"finalized": True, "mode": final["run"]["mode"], "trust_tier": final["run"]["trust_tier"],
                                  "gate": final["gate"]["decision"], "findings": len(final["findings"]),
                                  "report": os.path.exists(os.path.join(run_dir, "report.md"))})
                except (InputError, KeyError):
                    entry["error"] = "unreadable audit.final.json"
            runs.append(entry)
    latest = next((r for r in reversed(runs) if r["finalized"]), None)
    _print({"ok": True, "runs": runs, "latest_finalized": latest["run_dir"] if latest else None})
    return 0


def _with_carried(final, run_dir, summaries=False):
    """This run's findings plus those it carried forward, each taken from the run that last observed it (a sibling
    run directory), so skills that read a run see every finding its ship decision counted."""
    findings = list(final["findings"])
    base = os.path.dirname(os.path.abspath(run_dir))
    cache = {}
    for cf in final.get("carried_forward_findings") or []:
        src = cache.get(cf["last_seen_run"])
        if src is None:
            path = os.path.join(base, cf["last_seen_run"])
            try:
                src = cache[cf["last_seen_run"]] = finalize.load_final(path) \
                    if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(-[a-z0-9]{1,12})?", cf["last_seen_run"]) else {}
            except InputError:
                src = cache[cf["last_seen_run"]] = {}
        match = next((f for f in src.get("findings") or [] if f["id"] == cf["id"]), None)
        if match is not None:
            findings.append(dict(match, status=cf["status"], carried_forward_from=cf["last_seen_run"]))
        elif summaries:
            # The run that observed it is not next to this one: list what this run recorded about it.
            findings.append({"id": cf["id"], "title": cf["title"], "domain": cf["domain"], "rule": cf.get("rule", ""),
                             "severity": cf["severity"], "effective_confidence": cf["effective_confidence"],
                             "status": cf["status"], "release_blocking_effective": cf["release_blocking_effective"],
                             "evidence": [], "affected_components": [], "remediation": "", "explanation": "", "impact": "",
                             "carried_forward_from": cf["last_seen_run"]})
    return findings


def cmd_findings(args):
    try:
        final = finalize.load_final(args.run_dir)
    except InputError as exc:
        return _fail(str(exc))
    wanted = set(args.ids.split(",")) if args.ids else None
    out = []
    for f in _with_carried(final, args.run_dir, summaries=True):
        if wanted is not None and f["id"] not in wanted:
            continue
        if args.legal and not f.get("legal"):
            continue
        item = {"id": f["id"], "title": f["title"], "domain": f["domain"], "severity": f["severity"],
                "confidence": f["effective_confidence"], "status": f["status"], "release_blocking": f["release_blocking_effective"],
                "evidence": [{"path": ev.get("path"), "lines": "%s-%s" % (ev.get("start_line"), ev.get("end_line")) if ev.get("start_line") else None,
                              "kind": ev.get("kind"), "check": ev.get("check")} for ev in f["evidence"]],
                "affected_components": f["affected_components"], "remediation": f["remediation"]}
        if f.get("carried_forward_from"):
            item["carried_forward_from"] = f["carried_forward_from"]
        if args.detail:
            item.update({"explanation": f["explanation"], "impact": f["impact"], "rule": f["rule"],
                         "quotes": [ev.get("quote") for ev in f["evidence"] if ev.get("quote")]})
        if f.get("legal"):
            item["legal"] = {"classification": f["legal"]["classification"], "status": f.get("legal_status"),
                             "questions": f["legal"].get("questions") or [], "decisions_needed": f["legal"].get("decisions_needed") or []}
        out.append(item)
    missing = sorted(wanted - {i["id"] for i in out}) if wanted else []
    _print({"ok": not missing, "run_id": final["run"]["run_id"], "gate": final["gate"]["decision"], "findings": out,
            "not_found": missing})
    return 0 if not missing else 1


def cmd_schema_check(args):
    """Validate an arbitrary JSON document against a named definition (useful for CI consumers)."""
    try:
        doc = load_json(args.file)
    except InputError as exc:
        return _fail(str(exc))
    errors = minischema.validate_def(doc, catalog.schema(), args.definition)
    _print({"ok": not errors, "errors": errors})
    return 0 if not errors else 1


def _state_dir(args):
    if getattr(args, "state_dir", None):
        return os.path.abspath(args.state_dir)
    if getattr(args, "root", None):
        return state.default_state_dir(args.root)
    raise state.StateError("give --root (the audit root) or --state-dir")


def _stamp(args):
    return _now(args).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_cmd(fn):
    def run(args):
        try:
            result = fn(args)
        except (InputError, ledger.LedgerError, state.StateError, lockfile.LockError) as exc:
            return _fail(str(exc))
        if isinstance(result, int):
            return result
        _print(dict({"ok": True}, **result))
        return 0
    return run


@_state_cmd
def cmd_state_init(args):
    tx = state.init(_state_dir(args), args.root, _stamp(args), args.name)
    return {"state_dir": tx.st.state_dir, "project_id": tx.project["identity"]["project_id"],
            "migrated_from_1_0": tx.st.ledger is not None, "revision": tx.project["revision"]}


@_state_cmd
def cmd_state_show(args):
    st = state.load(_state_dir(args), root=args.root, strict=False)
    return {"state": state.summary(st)}


@_state_cmd
def cmd_state_check(args):
    st = state.load(_state_dir(args), root=args.root, strict=False)
    problems = list(st.problems) + state.check_identity(st, args.root)
    _print({"ok": not problems, "state_dir": st.state_dir, "initialized": st.initialized, "events": len(st.events),
            "revision": st.project["revision"] if st.initialized else None, "problems": problems})
    return 0 if not problems else 1


@_state_cmd
def cmd_state_recover(args):
    return state.recover(_state_dir(args))


@_state_cmd
def cmd_state_backup(args):
    state_dir = _state_dir(args)
    with lockfile.locked(state_dir):
        st = state.load(state_dir, root=args.root)
        if not st.initialized and st.ledger is None:
            raise state.StateError("there is no Flight Check state to back up in %s" % state_dir)
        return {"backup": state.backup(st, args.reason, _stamp(args))}


@_state_cmd
def cmd_state_backups(args):
    return {"backups": state.list_backups(_state_dir(args))}


@_state_cmd
def cmd_state_restore(args):
    return state.restore(_state_dir(args), args.source, root=args.root, now_iso=_stamp(args), by=args.by,
                         adopt_other=args.adopt)


@_state_cmd
def cmd_state_export(args):
    return state.export_bundle(_state_dir(args), args.out, _stamp(args))


@_state_cmd
def cmd_state_import(args):
    return state.import_bundle(_state_dir(args), args.source, root=args.root, adopt_other=args.adopt,
                               now_iso=_stamp(args), by=args.by)


@_state_cmd
def cmd_state_adopt(args):
    return state.adopt(_state_dir(args), args.root, args.reason, _stamp(args), by=args.by)


def _user_change(args, command, fn):
    """Run fn(tx) in a state transaction on behalf of the user; fn returns (record, change, reason, result)."""
    with state.transaction(_state_dir(args), "user", command, _stamp(args), root=args.root, by=args.by,
                           initialize=False) as tx:
        try:
            record, change, reason, result = fn(tx)
        except profile.ProfileError as exc:
            raise state.StateError(str(exc))
        tx.event(record, change, reason)
    return result


@_state_cmd
def cmd_profile_show(args):
    st = state.load(_state_dir(args), root=args.root)
    if not st.initialized:
        raise state.StateError("no project state yet; run an audit or `state init` first")
    facts = st.project["profile"]["facts"]
    if not args.all:
        facts = [f for f in facts if f["status"] == "current"]
    return {"facts": [{k: f.get(k) for k in ("id", "aspect", "value", "origin", "source", "presence", "status",
                                             "last_observed", "contradiction")}
                      | {"evidence": [r.get("path") or ("searched: " + r.get("searched", "")) for r in f["evidence"]],
                         "active_corrections": [c for c in f.get("corrections") or [] if c["active"]]}
                      for f in facts]}


@_state_cmd
def cmd_profile_correct(args):
    def fn(tx):
        if args.withdraw:
            fact = profile.withdraw_corrections(tx.project, args.id, args.reason, tx.now_iso, args.by)
            return fact["id"], {"correction": ["active", "withdrawn"]}, args.reason, {"fact": fact["id"]}
        fact = profile.correct(tx.project, args.id, args.assertion, args.reason, tx.now_iso, args.value, args.by)
        return fact["id"], {"correction": [None, args.assertion]}, args.reason, {
            "fact": fact["id"], "contradiction": fact["contradiction"]}
    if not args.withdraw and not args.assertion:
        raise state.StateError("give --assertion (not_used, excluded, replace, scope) or --withdraw")
    return _user_change(args, "profile correct", fn)


@_state_cmd
def cmd_profile_declare(args):
    def fn(tx):
        fact = profile.declare(tx.project, args.aspect, args.value, args.reason, tx.now_iso, args.by)
        return fact["id"], {"declared": [None, fact["aspect"]]}, args.reason, {"fact": fact["id"], "aspect": fact["aspect"]}
    return _user_change(args, "profile declare", fn)


@_state_cmd
def cmd_applicability_show(args):
    st = state.load(_state_dir(args), root=args.root)
    if not st.initialized:
        raise state.StateError("no project state yet; run an audit or `state init` first")
    return {"domains": [{"domain": a["domain"], "classification": a["classification"], "effective": profile.effective(a),
                         "override": a.get("override"), "contradiction": a.get("contradiction", False),
                         "reasoning": a["reasoning"], "evaluated_at": a["evaluated_at"]}
                        for a in st.project["applicability"]]}


@_state_cmd
def cmd_applicability_override(args):
    if args.domain not in catalog.domain_ids():
        raise state.StateError("unknown domain %r" % args.domain)

    def fn(tx):
        new = None if args.clear else args.classification
        entry, old = profile.override(tx.project, args.domain, new, args.reason, tx.now_iso, args.by)
        return "domain:" + args.domain, {"override": [old, new]}, args.reason, {
            "domain": args.domain, "effective": profile.effective(entry), "contradiction": entry["contradiction"]}
    if not args.clear and not args.classification:
        raise state.StateError("give --classification applicable|not_applicable, or --clear")
    return _user_change(args, "applicability override", fn)


def _add_profile_parsers(sub):
    def common(s, func):
        s.add_argument("--root")
        s.add_argument("--state-dir")
        s.add_argument("--now")
        s.add_argument("--by", help="who made this change")
        s.set_defaults(func=func)

    pp = sub.add_parser("profile", help="show the project profile, or record your corrections and declarations")
    psub = pp.add_subparsers(dest="profile_command")
    psub.required = True
    s = psub.add_parser("show")
    common(s, cmd_profile_show)
    s.add_argument("--all", action="store_true", help="include superseded and historical facts")
    s = psub.add_parser("correct", help="correct an observed fact (never deletes it)")
    common(s, cmd_profile_correct)
    s.add_argument("--id", required=True)
    s.add_argument("--assertion", choices=["not_used", "excluded", "replace", "scope"])
    s.add_argument("--value")
    s.add_argument("--reason", required=True)
    s.add_argument("--withdraw", action="store_true", help="withdraw the fact's active corrections")
    s = psub.add_parser("declare", help="state a fact about the project yourself")
    common(s, cmd_profile_declare)
    s.add_argument("--aspect", required=True)
    s.add_argument("--value", required=True)
    s.add_argument("--reason", required=True)

    ap = sub.add_parser("applicability", help="show which audit domains apply, or record your override")
    asub = ap.add_subparsers(dest="applicability_command")
    asub.required = True
    s = asub.add_parser("show")
    common(s, cmd_applicability_show)
    s = asub.add_parser("override", help="declare a domain applicable or not applicable (evidence still wins)")
    common(s, cmd_applicability_override)
    s.add_argument("--domain", required=True)
    s.add_argument("--classification", choices=["applicable", "not_applicable"])
    s.add_argument("--clear", action="store_true")
    s.add_argument("--reason", required=True)


REGISTER_FIELDS = {
    "requirement": [("title", None), ("description", None), ("category", None), ("criticality", None),
                    ("applicability", None), ("components", "component")],
    "decision": [("title", None), ("context", None), ("decision", None), ("rationale", None), ("alternatives", None),
                 ("owner", None), ("date", None), ("review_triggers", "review-trigger"), ("assertions", "assertion")],
    "risk": [("title", None), ("description", None), ("domain", None), ("severity", None), ("confidence", None),
             ("impact", None), ("likelihood", None), ("owner", None), ("mitigation_plan", None), ("next_review", None)],
    "legal": [("title", None), ("category", None), ("description", None), ("trigger", None), ("classification", None),
              ("jurisdiction", None), ("questions", "question"), ("owner", None), ("review_date", None),
              ("resolution_notes", None), ("references", "reference")],
}


def _register_fields(kind, args):
    return {name: getattr(args, name, None) for name, _ in REGISTER_FIELDS[kind]}


def _register_change(args, command, fn):
    actor = getattr(args, "source", None) or "user"
    # A 1.0 project (a ledger but no 1.5 state yet) is migrated by its first recording command, as by finalize.
    has_ledger = os.path.isfile(os.path.join(_state_dir(args), state.LEDGER))
    with state.transaction(_state_dir(args), actor, command, _stamp(args), root=args.root, by=args.by,
                           initialize=has_ledger) as tx:
        try:
            record, change, reason, result = fn(tx)
        except (registers.RegisterError, legal.LegalError) as exc:
            raise state.StateError(str(exc))
        tx.event(record, change, reason)
    return result


def _make_register_commands(kind):
    @_state_cmd
    def add(args):
        def fn(tx):
            fields = _register_fields(kind, args)
            links = list(args.link or [])
            if kind == "risk" and args.from_finding:
                entry = registers.entries_by_id(tx.ledger).get(args.from_finding)
                if entry is None:
                    raise registers.RegisterError("no finding %s in the ledger" % args.from_finding)
                fields["title"] = fields["title"] or entry["title"]
                fields["description"] = fields["description"] or "Tracked from finding %s: %s" % (entry["id"], entry["title"])
                fields["domain"] = fields["domain"] or entry["domain"]
                fields["severity"] = fields["severity"] or entry["severity"]
                links.append(args.from_finding)
            rec = registers.add(kind, tx.project, tx.registers, tx.ledger, fields, args.source, tx.now_iso, links,
                                args.by, args.note)
            return rec["id"], {"status": [None, rec["status"]]}, args.note, {"id": rec["id"], "status": rec["status"]}
        return _register_change(args, "%s add" % kind, fn)

    @_state_cmd
    def update(args):
        def fn(tx):
            if kind == "legal" and args.id.startswith("FC-"):
                args.id = legal.item_for_finding(tx.registers, tx.project, tx.ledger, args.id, tx.now_iso, create=True)["id"]
            _, rec, old = registers.update(tx.project, tx.registers, tx.ledger, args.id, args.source, tx.now_iso,
                                           status=args.status, fields=_register_fields(kind, args), links=args.link,
                                           note=args.note, by=args.by, superseded_by=getattr(args, "superseded_by", None))
            change = {"status": [old, rec["status"]]} if old != rec["status"] else {}
            return rec["id"], change, args.note, {"id": rec["id"], "status": rec["status"]}
        return _register_change(args, "%s update" % kind, fn)

    @_state_cmd
    def show(args):
        st = state.load(_state_dir(args), root=args.root)
        if not st.initialized:
            raise state.StateError("no project state yet; run an audit or `state init` first")
        key = registers.KINDS[kind]["key"]
        if getattr(args, "id", None):
            _, rec = registers.find(st.registers, args.id)
            return {"record": rec, "linked_from": registers.reverse_links(st.registers, args.id)}
        rows = [registers.summary_row(kind, r) for r in st.registers[key]
                if not args.status or r["status"] == args.status]
        result = {key: rows}
        if kind == "legal" and not legal.open_items(st.registers):
            result["message"] = legal.NO_ISSUES
        return result
    return add, update, show


@_state_cmd
def cmd_risk_accept(args):
    def fn(tx):
        acceptance = {"reason": args.reason, "owner": args.owner, "date": args.date, "scope": args.scope,
                      "compensating_controls": args.compensating_controls, "conditions": args.conditions,
                      "review_date": args.review_date, "review_trigger": args.review_trigger}
        rec, old = registers.accept_risk(tx.registers, args.id, acceptance, tx.now_iso, args.by)
        return rec["id"], {"status": [old, "ACCEPTED"]}, args.reason, {
            "id": rec["id"], "status": "ACCEPTED", "severity": rec["severity"],
            "note": "Acceptance does not lower severity; a CRITICAL risk still blocks release."}
    args.source = "user"
    return _register_change(args, "risk accept", fn)


@_state_cmd
def cmd_decision_assertions(args):
    st = state.load(_state_dir(args), root=args.root)
    if not st.initialized:
        return {"assertions": []}
    return {"assertions": registers.active_assertions(st.registers)}


def _add_register_parsers(sub):
    choices = {"category": ["security", "privacy", "compliance-readiness", "accessibility", "reliability", "data-lifecycle",
                            "authentication", "authorization", "infrastructure", "deployment", "legal-business",
                            "product-behavior", "release"],
               "criticality": ["release-critical", "high", "normal"], "severity": list(constants.SEVERITIES),
               "confidence": list(constants.CONFIDENCES), "domain": catalog.domain_ids(),
               "classification": list(constants.LEGAL_CLASSES)}
    names = {"requirement": "req", "decision": "decision", "risk": "risk", "legal": "legal"}
    for kind, command in names.items():
        add, update, show = _make_register_commands(kind)
        parser = sub.add_parser(command, help="the %s register" % kind)
        rsub = parser.add_subparsers(dest="%s_command" % command)
        rsub.required = True

        def common(s, func, source=True):
            s.add_argument("--root")
            s.add_argument("--state-dir")
            s.add_argument("--now")
            s.add_argument("--by", help="who made this change")
            if source:
                s.add_argument("--source", choices=["user", "flight-check"], default="user",
                               help="flight-check only for Flight Check's own suggestions")
            s.set_defaults(func=func)

        def fields(s):
            for name, flag in REGISTER_FIELDS[kind]:
                opt = "--" + (flag or name.replace("_", "-"))
                if flag:
                    s.add_argument(opt, dest=name, action="append")
                elif choices.get(name) and not (kind == "legal" and name == "category"):
                    s.add_argument(opt, dest=name, choices=choices[name])
                else:
                    s.add_argument(opt, dest=name)
            s.add_argument("--link", action="append", help="FC-, REQ-, DEC-, RISK-, LR-, PF- ids or control:<id>")
            s.add_argument("--note")

        s = rsub.add_parser("add")
        common(s, add)
        fields(s)
        if kind == "risk":
            s.add_argument("--from-finding", help="start from a ledger finding (FC-xxxx)")
        s = rsub.add_parser("update")
        common(s, update)
        fields(s)
        s.add_argument("--id", required=True)
        s.add_argument("--status", choices=sorted(registers.TRANSITIONS[kind]))
        if kind == "decision":
            s.add_argument("--superseded-by")
        s = rsub.add_parser("list")
        common(s, show, source=False)
        s.add_argument("--status")
        s = rsub.add_parser("show")
        common(s, show, source=False)
        s.add_argument("--id", required=True)
        s.add_argument("--status")
        if kind == "risk":
            s = rsub.add_parser("accept", help="record your acceptance of a risk (severity is unchanged)")
            common(s, cmd_risk_accept, source=False)
            s.add_argument("--id", required=True)
            for name in ("reason", "owner", "date", "scope", "compensating-controls"):
                s.add_argument("--" + name, required=True)
            for name in ("conditions", "review-date", "review-trigger"):
                s.add_argument("--" + name)
        if kind == "decision":
            s = rsub.add_parser("assertions", help="statements from accepted decisions, for agents to check")
            common(s, cmd_decision_assertions, source=False)


@_state_cmd
def cmd_changes(args):
    root = args.root or (os.path.dirname(_state_dir(args)) if os.path.basename(_state_dir(args)) == state.STATE_DIR_NAME else None)
    if not root:
        raise state.StateError("give --root (the audit root) so the current files can be compared")
    st = state.load(_state_dir(args), root=root)
    if not st.initialized:
        raise state.StateError("no project state yet; run an audit first")
    return freshness.changes(root, st)


def _view_state(args):
    root = args.root or (os.path.dirname(_state_dir(args)) if os.path.basename(_state_dir(args)) == state.STATE_DIR_NAME else None)
    st = state.load(_state_dir(args), root=root)
    if not st.initialized:
        raise state.StateError("no project state yet; run /flight-check:audit (or `state init`) first")
    return st, root


def _report_file(state_dir, name, suffix):
    """(path, error): where --out writes. Reports go only to <state dir>/reports/, so a pre-approved command can never
    overwrite another file."""
    folder = os.path.join(state_dir, "reports")
    if os.path.basename(name) != name or name.startswith(".") or not name.endswith(suffix):
        return None, "--out takes a file name ending in %s, without a directory; reports are written to %s" % (suffix, folder)
    if os.path.islink(folder):
        return None, "%s is a symbolic link; refusing to write through it" % folder
    os.makedirs(folder, mode=0o700, exist_ok=True)
    out = os.path.join(folder, name)
    if os.path.islink(out):
        return None, "%s is a symbolic link; refusing to write through it" % out
    return out, None


def _emit_view(args, doc, state_dir):
    text = status.render_markdown(doc) if args.format == "md" else None
    if getattr(args, "out", None):
        out, error = _report_file(state_dir, args.out, ".md" if text is not None else ".json")
        if error:
            return _fail(error)
        if text is None:
            dump_json(out, doc)
        else:
            write_text(out, text)
        _print({"ok": True, "view": doc["view"], "written": out})
    elif text is None:
        _print(dict({"ok": True}, **doc))
    else:
        sys.stdout.write(text)
    return 0


def cmd_status(args):
    try:
        st, root = _view_state(args)
        doc = status.build("status", st, root, _stamp(args))
    except (InputError, state.StateError, registers.RegisterError) as exc:
        return _fail(str(exc))
    return _emit_view(args, doc, st.state_dir)


def cmd_report(args):
    try:
        st, root = _view_state(args)
        doc = status.build(args.view, st, root, _stamp(args), args.id)
    except (InputError, state.StateError, registers.RegisterError) as exc:
        return _fail(str(exc))
    return _emit_view(args, doc, st.state_dir)


def cmd_sarif(args):
    """Findings that count against release, as SARIF 2.1.0 (for GitHub code scanning)."""
    try:
        st, root = _view_state(args)
        doc, counts = sarif.build(st, root)
    except (InputError, state.StateError, registers.RegisterError) as exc:
        return _fail(str(exc))
    if not args.out:
        _print(doc)
        return 0
    out, error = _report_file(st.state_dir, args.out, ".sarif")
    if error:
        return _fail(error)
    dump_json(out, doc)
    _print(dict({"ok": True, "written": out}, **counts))
    return 0


def cmd_release_check(args):
    """Exit 0 only when the latest ship decision is ready and still describes the tracked files under --root."""
    root = os.path.abspath(args.root)
    try:
        st = state.load(_state_dir(args), root=root, strict=False)
        if not st.initialized:
            raise state.StateError("no project state under %s; run /flight-check:audit and commit .flight-check/ first"
                                   % st.state_dir)
        result = release.check(st, root, _decision_inputs_changed(st, _now(args).date()), state.check_identity(st, root),
                               args.allow_accepted_risks)
    except (InputError, state.StateError, ledger.LedgerError) as exc:
        result = {"ok": False, "decision": None, "problems": [str(exc)], "files": {}, "files_not_listed": 0}
    result["mode"] = args.mode
    if args.json:
        _print(result)
    else:
        latest = result["decision"]
        lines = ["Flight Check release check: %s%s" % ("PASSED" if result["ok"] else "FAILED",
                                                       "" if result["ok"] or args.mode == "require" else " (warn mode: not blocking)")]
        if latest:
            lines.append("Latest ship decision: %s (run %s, %s)" % (latest["decision"], latest["run_id"], latest["at"]))
        lines += ["- " + p for p in result["problems"]]
        for kind in ("added", "changed", "deleted"):
            lines += ["  %s: %s" % (kind, path) for path in result["files"].get(kind, [])]
        if result["files_not_listed"]:
            lines.append("  ... and %d more" % result["files_not_listed"])
        lines.append("This check confirms only that the recorded decision applies to these files. It is not a "
                     "certification, and the records it reads can be edited by anyone who can commit.")
        sys.stdout.write("\n".join(lines) + "\n")
        if os.environ.get("GITHUB_ACTIONS") == "true" and not result["ok"]:
            level = "error" if args.mode == "require" else "warning"
            for p in result["problems"]:
                text = p.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")  # GitHub's workflow-command escaping
                sys.stdout.write("::%s title=Flight Check release check::%s\n" % (level, text))
    return 0 if result["ok"] or args.mode == "warn" else 1


@_state_cmd
def cmd_ledger_mark(args):
    def fn(tx):
        old = ledger.mark(tx.ledger, args.id, args.status, args.reason, tx.now_iso, args.superseded_by, args.by)
        return args.id, {"status": [old, args.status]}, args.reason, {"id": args.id, "status": args.status}
    return _register_change(args, "ledger mark", fn)


def _add_state_parsers(sub):
    sp = sub.add_parser("state", help="inspect, check, back up, and recover Flight Check project state")
    ssub = sp.add_subparsers(dest="state_command")
    ssub.required = True

    def add(name, func, help_text, root_required=False):
        s = ssub.add_parser(name, help=help_text)
        s.add_argument("--root", required=root_required, help="audit root (state defaults to <root>/.flight-check)")
        s.add_argument("--state-dir", help="state directory, for state kept outside the project (--out)")
        s.add_argument("--now")
        s.set_defaults(func=func)
        return s

    s = add("init", cmd_state_init, "create project state (from an existing 1.0 ledger if there is one)", True)
    s.add_argument("--name", help="display name (default: the ledger's project name or the directory name)")
    add("show", cmd_state_show, "summarize the state")
    add("check", cmd_state_check, "verify schemas, the history chain, revisions, and the project's location")
    add("recover", cmd_state_recover, "finish or roll back a command that was interrupted")
    s = add("backup", cmd_state_backup, "copy the state files to backups/")
    s.add_argument("--reason", required=True)
    add("backups", cmd_state_backups, "list backups")
    s = add("restore", cmd_state_restore, "restore project, registers, and ledger from a backup (keeps the history log)")
    s.add_argument("--from", dest="source", required=True, help="a directory under .flight-check/backups/")
    s.add_argument("--by", help="who asked for the restore")
    s.add_argument("--adopt", action="store_true", help="restore a backup that belongs to another project (for example "
                                                       "the one taken before importing another project's export)")
    s = add("export", cmd_state_export, "write the committed state files to one JSON bundle")
    s.add_argument("--out", required=True)
    s = add("import", cmd_state_import, "replace the state with an exported bundle (backs up first)")
    s.add_argument("--from", dest="source", required=True)
    s.add_argument("--adopt", action="store_true", help="allow a bundle from a different project")
    s.add_argument("--by")
    s = add("adopt", cmd_state_adopt, "confirm that this state belongs to the project at its current location", True)
    s.add_argument("--reason", required=True)
    s.add_argument("--by")


def build_parser():
    p = argparse.ArgumentParser(prog="flight-check", description="Flight Check deterministic tooling (no network, no project code execution).")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    s = sub.add_parser("version")
    s.set_defaults(func=cmd_version)

    s = sub.add_parser("init-run", help="create a run directory and print its id")
    s.add_argument("--base", required=True, help="directory that holds run directories")
    s.add_argument("--root", help="audit root, used to read the current git commit from .git files")
    s.add_argument("--suffix")
    s.add_argument("--inventory-from", help="copy inventory.json from this earlier run directory (targeted re-audits)")
    s.add_argument("--passes", type=int, choices=(1, 2, 3), default=1, help="passes the audit will run (for the usage estimate)")
    s.add_argument("--recheck", help="comma-separated control IDs to re-examine; implies --changed and reassesses their domains")
    s.add_argument("--changed", action="store_true",
                   help="reassess only the domains that changes since the last finalized run can affect; carry the rest forward")
    s.add_argument("--now")
    s.set_defaults(func=cmd_init_run)

    s = sub.add_parser("runs", help="list run directories and their ship decisions")
    s.add_argument("--base", required=True)
    s.set_defaults(func=cmd_runs)

    s = sub.add_parser("findings", help="print a compact list of findings from a finalized run")
    s.add_argument("run_dir")
    s.add_argument("--ids", help="comma-separated finding IDs")
    s.add_argument("--legal", action="store_true", help="only findings with a legal/business classification")
    s.add_argument("--detail", action="store_true", help="include explanation, impact, and quotes")
    s.set_defaults(func=cmd_findings)

    s = sub.add_parser("validate", help="validate agent outputs in a run directory")
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("finalize", help="validate, assemble, check evidence, merge ledger, compute the ship gate")
    s.add_argument("run_dir")
    s.add_argument("--root", required=True, help="audit root; evidence paths are resolved inside it")
    s.add_argument("--ledger", help="ledger path (default <root>/.flight-check/ledger.json)")
    s.add_argument("--no-ledger", action="store_true", help="do not read or write a ledger")
    s.add_argument("--now")
    s.set_defaults(func=cmd_finalize)

    s = sub.add_parser("save-reply", help="save an agent reply that Claude Code stored in a file into the run directory")
    s.add_argument("run_dir")
    s.add_argument("--from", dest="source", required=True, help="the file the reply was saved to")
    s.add_argument("--as", dest="as_name", required=True, help="inventory.json, verification.json, summary.json, or part-<agent>.json")
    s.set_defaults(func=cmd_save_reply)

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

    _add_state_parsers(sub)
    _add_profile_parsers(sub)
    _add_register_parsers(sub)

    s = sub.add_parser("changes", help="what changed since the last finalized audit, what it affects, and what to reassess")
    s.add_argument("--root")
    s.add_argument("--state-dir")
    s.add_argument("--now")
    s.set_defaults(func=cmd_changes)

    s = sub.add_parser("status", help="what the project needs now: readiness, open issues, changes, records needing attention")
    s.add_argument("--root")
    s.add_argument("--state-dir")
    s.add_argument("--format", choices=("md", "json"), default="md")
    s.add_argument("--out", help="file name (.md, or .json with --format json) to write under <state dir>/reports/ instead of printing")
    s.add_argument("--now")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("report", help="a Markdown or JSON view of the project's records")
    s.add_argument("--view", required=True, choices=status.VIEWS)
    s.add_argument("--id", help="one record (REQ-, DEC-, RISK-, LR-, or FC- with the matching view)")
    s.add_argument("--root")
    s.add_argument("--state-dir")
    s.add_argument("--format", choices=("md", "json"), default="md")
    s.add_argument("--out", help="file name (.md, or .json with --format json) to write under <state dir>/reports/ instead of printing")
    s.add_argument("--now")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("sarif", help="open findings as SARIF 2.1.0, for GitHub code scanning")
    s.add_argument("--root", required=True, help="the audit root (finding paths are checked against it)")
    s.add_argument("--state-dir")
    s.add_argument("--out", help="file name ending in .sarif, written under <state dir>/reports/; default: print it")
    s.set_defaults(func=cmd_sarif)

    s = sub.add_parser("release-check", help="for CI: pass only when the latest ship decision is ready and still describes the tracked files")
    s.add_argument("--root", required=True, help="the checkout to release (the audit root)")
    s.add_argument("--state-dir")
    s.add_argument("--mode", choices=("require", "warn"), default="require",
                   help="require: exit 1 when the check fails; warn: report and exit 0")
    s.add_argument("--allow-accepted-risks", action="store_true", help="also pass READY WITH ACCEPTED RISKS")
    s.add_argument("--json", action="store_true")
    s.add_argument("--now")
    s.set_defaults(func=cmd_release_check)

    lp = sub.add_parser("ledger", help="inspect or update the finding ledger")
    lsub = lp.add_subparsers(dest="ledger_command")
    lsub.required = True

    s = lsub.add_parser("show")
    s.add_argument("--ledger", required=True)
    s.add_argument("--id")
    s.set_defaults(func=cmd_ledger_show)

    s = lsub.add_parser("accept", help="record a user's explicit risk acceptance")
    s.add_argument("--ledger", required=True)
    s.add_argument("--ref", required=True, help="finding ID (FC-0001) or control ID")
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
    s.add_argument("--source", required=True, choices=["user", "flight-check"])
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

    s = lsub.add_parser("mark", help="record your triage of a finding: under review, remediation planned, false positive, superseded, or open")
    s.add_argument("--root")
    s.add_argument("--state-dir")
    s.add_argument("--id", required=True)
    s.add_argument("--status", required=True, choices=list(constants.USER_MARK_STATUSES))
    s.add_argument("--reason", required=True)
    s.add_argument("--superseded-by")
    s.add_argument("--by")
    s.add_argument("--now")
    s.set_defaults(func=cmd_ledger_mark, source="user")

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
    except (state.StateError, lockfile.LockError) as exc:
        return _fail(str(exc))

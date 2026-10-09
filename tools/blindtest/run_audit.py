#!/usr/bin/env python3
"""Run a blind Flight Check audit of a test application in a disposable copy.

The application directory is copied to a fresh temporary directory, so the audit can
never see a ground-truth manifest, scoring scripts, or anything else stored next to the
application. The audit runs headless with only Read/Grep/Glob for the agents, writes
restricted to the copy's .flight-check/runs/ directory, and only the script subcommands an audit
needs allowed (never the ledger commands that record user decisions). Results are copied to --out.

Usage:
    python3 tools/blindtest/run_audit.py --app path/to/app --out results/run1 [--budget 40] [--evidence f.json ...]

Development-only; never shipped in the plugin.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PLUGIN = os.path.join(REPO, "plugins", "flight-check")
EXCLUDE = {".git", "ground-truth", "manifest.json", ".flight-check"}


def summarize_stream(path):
    """Final result, result count, and every denied tool call (with its target) from a stream-json log.

    Headless runs can emit an interim result while background agents work; the last result is final.
    """
    results, uses, denied = [], {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                results.append({k: event.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd", "result")})
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    inp = block.get("input") or {}
                    uses[block.get("id")] = {"tool": block.get("name"), "target": inp.get("file_path") or inp.get("command") or inp.get("subagent_type"),
                                             "subagent": bool(event.get("parent_tool_use_id"))}
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    text = block.get("content")
                    text = text if isinstance(text, str) else json.dumps(text)
                    if "denied" in text.lower() or "permission" in text.lower():
                        denied.append(dict(uses.get(block.get("tool_use_id"), {}), error=text[:300]))
    return {"results": len(results), "final": results[-1] if results else None,
            "final_result": results[-1]["result"] if results else None, "denied_tool_calls": denied}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--app", required=True, help="application directory to audit (must not contain the answer key)")
    p.add_argument("--out", required=True)
    p.add_argument("--budget", type=float, default=40.0, help="--max-budget-usd for the headless run")
    p.add_argument("--evidence", action="append", default=[], help="imported evidence file(s), copied into the target")
    p.add_argument("--model")
    p.add_argument("--keep", action="store_true", help="keep the temporary copy")
    args = p.parse_args(argv)

    app = os.path.abspath(args.app)
    for name in os.listdir(app):
        if name.lower() in ("manifest.json", "ground-truth", "ground_truth", "answers", "expected"):
            sys.exit("refusing: %s looks like ground truth inside the application directory" % name)

    # The copy is the temporary directory itself, not a subdirectory of it. In Phase 5, agents sometimes
    # dropped a trailing subdirectory from the audit root; with no subdirectory there is nothing to drop.
    # Settings live in a separate temporary directory outside the audited copy.
    work = tempfile.mkdtemp(prefix="flight-check-settings-")
    target = tempfile.mkdtemp(prefix="flight-check-blind-")
    shutil.copytree(app, target, ignore=lambda d, names: [n for n in names if n in EXCLUDE], dirs_exist_ok=True)
    evidence_args = []
    if args.evidence:
        ev_dir = os.path.join(target, ".flight-check", "evidence")
        os.makedirs(ev_dir)
        for ev in args.evidence:
            shutil.copy(ev, ev_dir)
            evidence_args.append(os.path.join(".flight-check", "evidence", os.path.basename(ev)))

    script = os.path.join(PLUGIN, "scripts", "flight_check.py")
    # Only the four subcommands an audit needs; never the ledger commands that record user decisions.
    # The ledger is written by the script, so the session itself may write only run directories.
    settings = {"permissions": {
        "allow": ["Edit(/%s/.flight-check/runs/**)" % target]
        + ["Bash(python3 %s %s *)" % (script, sub) for sub in ("init-run", "validate", "finalize", "render")],
        "deny": ["Edit(/%s/.flight-check/ledger.json)" % target, "Bash(python3 %s ledger *)" % script],
    }}
    settings_path = os.path.join(work, "settings.json")
    with open(settings_path, "w") as fh:
        json.dump(settings, fh)
    prompt = "/flight-check:audit"
    if evidence_args:
        prompt += " --evidence " + " ".join(evidence_args)
    # Stream every event to a log so a failed run can be diagnosed (which tool call was denied, and why).
    cmd = ["claude", "-p", prompt, "--plugin-dir", PLUGIN, "--tools", "Read,Write,Agent,Bash,Glob,Grep",
           "--permission-mode", "dontAsk", "--settings", settings_path, "--strict-mcp-config",
           "--output-format", "stream-json", "--verbose", "--max-budget-usd", str(args.budget)]
    if args.model:
        cmd += ["--model", args.model]
    print("auditing copy at %s" % target)
    os.makedirs(args.out, exist_ok=True)
    stream_path = os.path.join(args.out, "claude-stream.jsonl")
    with open(stream_path, "w") as out_fh, open(os.path.join(args.out, "claude-stderr.txt"), "w") as err_fh:
        proc = subprocess.run(cmd, cwd=target, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh, text=True)
    summary = summarize_stream(stream_path)
    with open(os.path.join(args.out, "claude-output.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    runs = os.path.join(target, ".flight-check", "runs")
    copied = []
    if os.path.isdir(runs):
        for name in sorted(os.listdir(runs)):
            src = os.path.join(runs, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(args.out, name), dirs_exist_ok=True)
                copied.append(name)
    ledger = os.path.join(target, ".flight-check", "ledger.json")
    if os.path.exists(ledger):
        shutil.copy(ledger, os.path.join(args.out, "ledger.json"))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)
    # A run directory alone is not success: the session can stop (for example at a usage limit) after init-run.
    finalized = [n for n in copied if os.path.exists(os.path.join(args.out, n, "audit.final.json"))]
    print(json.dumps({"exit_code": proc.returncode, "runs": copied, "finalized": finalized, "out": os.path.abspath(args.out),
                      "denied_tool_calls": len(summary["denied_tool_calls"]), "final_result": (summary["final_result"] or "")[:300]}, indent=2))
    return 0 if proc.returncode == 0 and finalized else 1


if __name__ == "__main__":
    sys.exit(main())

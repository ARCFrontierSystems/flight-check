#!/usr/bin/env python3
"""Repository lint for Flight Check. Run from the repository root: python3 tools/lint_plugin.py

Checks (all must pass in CI):
  frontmatter   skills and agents use allowed keys; skills are manual-only; auditing agents are read-only
  surface       the shipped plugin has no hooks, MCP servers, bin/, package.json, symlinks, or oversized files
  injection     skill and agent bodies contain no shell-injection blocks or @file imports
  scripts       bundled Python never imports network/process modules or calls eval/exec/os.system
  unicode       no invisible, bidirectional, or control characters in any text file
  consistency   catalogs, schema enums, Python constants, report sections, and versions agree
  agents        generated agent blocks are in sync (tools/sync_agents.py --check)
  leak          no private project names or test-fixture identifiers inside the shipped plugin
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLUGIN = os.path.join(REPO, "plugins", "flight-check")
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))

from flight_check_lib import VERSION, catalog, constants, render_md  # noqa: E402

SKILL_KEYS = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
              "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "license", "metadata", "compatibility"}
AGENT_KEYS = {"name", "description", "tools", "disallowedTools", "model", "effort", "maxTurns", "omitClaudeMd", "skills", "color"}
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}
FORBIDDEN_MODULES = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "telnetlib", "poplib", "imaplib", "xmlrpc",
                     "requests", "subprocess", "asyncio", "multiprocessing", "ctypes", "pickle", "marshal", "shelve",
                     "webbrowser", "pty", "socketserver"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint"}
FORBIDDEN_OS = re.compile(r"^(system|popen|exec\w*|spawn\w*|fork\w*|kill\w*|startfile|posix_spawn\w*)$")
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 512
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".toml", ".cfg", ".ini", ""}

errors = []
notes = []


def err(check, msg):
    errors.append("[%s] %s" % (check, msg))


def rel(path):
    return os.path.relpath(path, REPO)


def walk(base, skip_dirs=(".git",)):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and d != "__pycache__"]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


# Subcommands a skill may pre-approve. Commands that record the user's own decisions
# (ledger accept, revoke, legal, close) are deliberately absent: they must always prompt.
PREAPPROVABLE_SUBCOMMANDS = {"version", "init-run", "validate", "finalize", "render", "gate", "runs", "findings",
                             "packet", "schema-check", "ledger show", "ledger remediate"}
PREAPPROVAL = re.compile(r"Bash\(python3 \$\{CLAUDE_PLUGIN_ROOT\}/scripts/flight_check\.py ((?:ledger )?[a-z-]+) \*\)")


def parse_frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    meta = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not m:
            meta.setdefault("__invalid__", []).append(line)
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[m.group(1)] = value
    return meta, text[end + 5:]


def check_frontmatter_and_bodies():
    skills_dir = os.path.join(PLUGIN, "skills")
    for name in sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []:
        path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(path):
            err("frontmatter", "%s has no SKILL.md" % rel(os.path.join(skills_dir, name)))
            continue
        meta, body = parse_frontmatter(path)
        if meta is None:
            err("frontmatter", "%s: missing frontmatter" % rel(path))
            continue
        for key in meta:
            if key not in SKILL_KEYS:
                err("frontmatter", "%s: unexpected key %r" % (rel(path), key))
        if meta.get("name") != name:
            err("frontmatter", "%s: name must equal directory name %r" % (rel(path), name))
        desc = meta.get("description", "")
        if not desc or len(desc) > 1024:
            err("frontmatter", "%s: description must be 1-1024 characters" % rel(path))
        if meta.get("disable-model-invocation") != "true":
            err("frontmatter", "%s: skills must set disable-model-invocation: true (manual invocation only)" % rel(path))
        if body.count("\n") > 500:
            err("frontmatter", "%s: SKILL.md body exceeds 500 lines" % rel(path))
        check_body(path, body)
        allowed = meta.get("allowed-tools", "")
        for tool in [t.strip() for t in re.split(r",(?![^()]*\))", allowed) if t.strip()]:
            if tool.startswith("Bash(") and "${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py" not in tool:
                err("frontmatter", "%s: allowed-tools may pre-approve only Flight Check's own script, found %r" % (rel(path), tool))
            elif tool in ("Bash", "Bash(*)", "WebFetch", "WebSearch") or tool.startswith("mcp__"):
                err("frontmatter", "%s: allowed-tools must not pre-approve %r" % (rel(path), tool))
            elif tool.startswith("Bash("):
                m = PREAPPROVAL.fullmatch(tool)
                if not m:
                    err("frontmatter", "%s: pre-approve one named subcommand per entry, as "
                        "Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py <subcommand> *); found %r" % (rel(path), tool))
                elif m.group(1) not in PREAPPROVABLE_SUBCOMMANDS:
                    err("frontmatter", "%s: %r must not be pre-approved; commands that record the user's decisions "
                        "must always raise a permission prompt" % (rel(path), m.group(1)))
    agents_dir = os.path.join(PLUGIN, "agents")
    for fn in sorted(os.listdir(agents_dir)) if os.path.isdir(agents_dir) else []:
        path = os.path.join(agents_dir, fn)
        if not fn.endswith(".md"):
            err("frontmatter", "%s: only .md files belong in agents/" % rel(path))
            continue
        meta, body = parse_frontmatter(path)
        if meta is None:
            err("frontmatter", "%s: missing frontmatter" % rel(path))
            continue
        for key in meta:
            if key not in AGENT_KEYS:
                err("frontmatter", "%s: unexpected key %r" % (rel(path), key))
        if meta.get("name") != fn[:-3]:
            err("frontmatter", "%s: name must equal file name" % rel(path))
        if not meta.get("description") or len(meta["description"]) > 300:
            err("frontmatter", "%s: description must be 1-300 characters (it is listed in every session)" % rel(path))
        tools = {t.strip() for t in meta.get("tools", "").split(",") if t.strip()}
        if not tools or not tools <= READ_ONLY_TOOLS:
            err("frontmatter", "%s: agents must use tools: Read, Grep, Glob only (got %s)" % (rel(path), sorted(tools)))
        if meta.get("omitClaudeMd") != "true":
            err("frontmatter", "%s: agents must set omitClaudeMd: true" % rel(path))
        check_body(path, body)


def check_body(path, body):
    for i, line in enumerate(body.splitlines(), 1):
        if "!`" in line or re.match(r"^\s*```!", line):
            err("injection", "%s:%d: shell-injection syntax is not allowed in Flight Check skills or agents" % (rel(path), i))
        if re.match(r"^\s*@[\w./~-]+", line):
            err("injection", "%s:%d: @file imports are not allowed" % (rel(path), i))


def check_surface():
    for forbidden in ("hooks", "bin", ".mcp.json", "package.json", "node_modules", ".lsp.json"):
        if os.path.exists(os.path.join(PLUGIN, forbidden)):
            err("surface", "plugin must not contain %s" % forbidden)
    manifest = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8"))
    for key in ("hooks", "mcpServers", "lspServers", "outputStyles", "channels"):
        if key in manifest:
            err("surface", "plugin.json must not declare %s" % key)
    files = []
    for dirpath, dirnames, filenames in os.walk(PLUGIN):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                err("surface", "symlinked directory %s" % rel(os.path.join(dirpath, d)))
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                err("surface", "symlink %s" % rel(p))
                continue
            files.append(p)
            if os.path.getsize(p) > MAX_FILE_BYTES:
                err("surface", "%s is larger than 256 KiB" % rel(p))
    if len(files) > MAX_FILES:
        err("surface", "plugin has %d files (limit %d)" % (len(files), MAX_FILES))


def check_scripts():
    for path in walk(os.path.join(PLUGIN, "scripts")):
        if not path.endswith(".py"):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                        err("scripts", "%s:%d imports %s" % (rel(path), node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.split(".")[0] in FORBIDDEN_MODULES:
                    err("scripts", "%s:%d imports from %s" % (rel(path), node.lineno, node.module))
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                    err("scripts", "%s:%d calls %s()" % (rel(path), node.lineno, fn.id))
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "os" and FORBIDDEN_OS.match(fn.attr):
                    err("scripts", "%s:%d calls os.%s()" % (rel(path), node.lineno, fn.attr))


def is_invisible(ch):
    if ch in "\n\t\r":
        return False
    return unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs")


def check_unicode():
    for path in walk(REPO):
        if os.path.splitext(path)[1].lower() not in TEXT_SUFFIXES or os.path.getsize(path) > 2 * 1024 * 1024:
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except UnicodeDecodeError:
            if path.endswith((".md", ".py", ".json", ".yml", ".yaml")):
                err("unicode", "%s is not valid UTF-8" % rel(path))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for col, ch in enumerate(line, 1):
                if is_invisible(ch):
                    err("unicode", "%s:%d:%d contains invisible/control character U+%04X" % (rel(path), lineno, col, ord(ch)))


def check_json():
    for path in walk(REPO):
        if path.endswith(".json"):
            try:
                json.load(open(path, encoding="utf-8"))
            except ValueError as exc:
                err("consistency", "%s is not valid JSON: %s" % (rel(path), exc))


def check_consistency():
    schema = catalog.schema()
    defs = schema["$defs"]
    ids = catalog.domain_ids()
    if sorted(ids) != sorted(defs["domainId"]["enum"]):
        err("consistency", "domains.json and schema domainId enum differ")
    if len(set(ids)) != len(ids):
        err("consistency", "duplicate domain ids")
    for c in catalog.controls():
        if c["domain"] not in ids:
            err("consistency", "control %s has unknown domain %s" % (c["id"], c["domain"]))
        if not re.fullmatch(defs["controlId"]["pattern"].strip("^$"), c["id"]):
            err("consistency", "control id %s does not match the schema pattern" % c["id"])
    ctrl_ids = [c["id"] for c in catalog.controls()]
    if len(set(ctrl_ids)) != len(ctrl_ids):
        err("consistency", "duplicate control ids")
    pairs = [(constants.SEVERITIES, defs["severity"]["enum"], "severities"),
             (constants.CONFIDENCES, defs["confidence"]["enum"], "confidences"),
             (constants.LEGAL_CLASSES, defs["legal"]["properties"]["classification"]["enum"], "legal classes"),
             (constants.GATES, defs["auditFinal"]["properties"]["gate"]["properties"]["decision"]["enum"], "gates"),
             (constants.LEGAL_STATUSES, defs["ledgerEntry"]["properties"]["legal"]["properties"]["status"]["enum"], "legal statuses"),
             (constants.FINDING_STATUSES, defs["ledgerEntry"]["properties"]["status"]["enum"], "finding statuses")]
    for code_vals, schema_vals, label in pairs:
        if list(code_vals) != list(schema_vals):
            err("consistency", "%s differ between constants.py and the schema" % label)
    section_domains = {d for _, _, d in render_md.SPEC_SECTIONS if d}
    if section_domains != set(ids):
        err("consistency", "report sections do not cover exactly the catalog domains: %s" % sorted(set(ids) ^ section_domains))
    numbered = [n for n, _, _ in render_md.SPEC_SECTIONS if n.isdigit()]
    if numbered != [str(i) for i in range(1, 34)]:
        err("consistency", "report must contain the 33 numbered sections in order")
    manifest = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8"))
    if manifest.get("version") != VERSION:
        err("consistency", "plugin.json version %s != flight_check_lib.VERSION %s" % (manifest.get("version"), VERSION))
    market = json.load(open(os.path.join(REPO, ".claude-plugin", "marketplace.json"), encoding="utf-8"))
    for entry in market["plugins"]:
        src = entry["source"]
        if isinstance(src, str) and not os.path.isdir(os.path.join(REPO, src)):
            err("consistency", "marketplace source %s does not exist" % src)
    agents_dir = os.path.join(PLUGIN, "agents")
    if os.path.isdir(agents_dir) and os.listdir(agents_dir):
        for agent in {d["agent"] for d in catalog.domains()} | {"inventory", "verifier"}:
            if not os.path.isfile(os.path.join(agents_dir, agent + ".md")):
                err("consistency", "missing agent file agents/%s.md" % agent)
    for path in (os.path.join(REPO, "LICENSE"), os.path.join(PLUGIN, "LICENSE")):
        if not os.path.isfile(path):
            err("consistency", "missing %s" % rel(path))


def check_agents_sync():
    script = os.path.join(REPO, "tools", "sync_agents.py")
    result = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
    if result.returncode != 0:
        err("agents", (result.stdout + result.stderr).strip() or "sync_agents.py --check failed")


def _tokens(text):
    return {t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}", text)}


def leak_scan_files():
    """Every file that is or could next be committed: tracked files plus untracked files that are not ignored.

    The leak rules cover the whole repository, not only the shipped plugin. Ignored paths (for
    example .flight-check/runs/, which can quote audited projects) are local and never committed.
    """
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout.decode("utf-8", "replace")
        files = [os.path.join(REPO, f) for f in out.split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = list(walk(REPO, skip_dirs=(".git", "runs")))
    hashes_file = os.path.join(REPO, "tools", "leak-hashes.txt")
    return [p for p in files if os.path.isfile(p) and os.path.splitext(p)[1].lower() in TEXT_SUFFIXES
            and os.path.abspath(p) != hashes_file]


def check_leaks():
    texts = {}
    for p in leak_scan_files():
        try:
            texts[p] = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
    hashes_file = os.path.join(REPO, "tools", "leak-hashes.txt")
    hashes = set()
    if os.path.isfile(hashes_file):
        for line in open(hashes_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                hashes.add(line.lower())
    if not hashes:
        notes.append("fixture canary list is empty (tools/leak-hashes.txt); add hashes when blind-test fixtures exist")
    else:
        for p, text in texts.items():
            for tok in _tokens(text):
                if hashlib.sha256(tok.encode("utf-8")).hexdigest() in hashes:
                    err("leak", "%s contains a token from the protected fixture list (hash match)" % rel(p))
    deny_path = os.environ.get("FLIGHT_CHECK_LEAK_DENYLIST")
    if deny_path and os.path.isfile(deny_path):
        terms = [l.strip() for l in open(deny_path, encoding="utf-8") if l.strip() and not l.startswith("#")]
        for p, text in texts.items():
            for term in terms:
                if re.search(r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(term), text):
                    err("leak", "%s contains a private denylisted term (term #%d)" % (rel(p), terms.index(term) + 1))
        notes.append("private denylist applied (%d terms) to %d files" % (len(terms), len(texts)))
    else:
        notes.append("private denylist not applied (set FLIGHT_CHECK_LEAK_DENYLIST to a local file; never commit it)")


def main():
    check_frontmatter_and_bodies()
    check_surface()
    check_scripts()
    check_unicode()
    check_json()
    check_consistency()
    check_agents_sync()
    check_leaks()
    for n in notes:
        print("note: " + n)
    if errors:
        for e in errors:
            print("ERROR " + e)
        print("%d problem(s) found" % len(errors))
        return 1
    print("lint passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Keep shared agent instructions in one place.

Agent files contain generated blocks delimited by
    <!-- BEGIN GENERATED: <block> -->  ...  <!-- END GENERATED: <block> -->
Block sources:
    untrusted-data            tools/agent-blocks/untrusted-data.md (all agents)
    domain-output-contract    tools/agent-blocks/domain-output-contract.md (domain agents)
    controls                  generated per domain agent from references/required-controls.json

Usage: python3 tools/sync_agents.py          rewrite agent files
       python3 tools/sync_agents.py --check  exit 1 if any agent file is out of date
"""

import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLUGIN = os.path.join(REPO, "plugins", "flight-check")
AGENTS = os.path.join(PLUGIN, "agents")
BLOCKS = os.path.join(REPO, "tools", "agent-blocks")
BLOCK_RE = re.compile(r"(<!-- BEGIN GENERATED: ([a-z-]+) -->\n)(.*?)(<!-- END GENERATED: \2 -->)", re.S)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def domain_agents():
    domains = json.loads(load(os.path.join(PLUGIN, "references", "domains.json")))["domains"]
    mapping = {}
    for d in domains:
        mapping.setdefault(d["agent"], []).append(d)
    return mapping


def controls_block(domains):
    controls = json.loads(load(os.path.join(PLUGIN, "references", "required-controls.json")))["controls"]
    lines = ["Report every control below in `controls`, each with exactly one state.", ""]
    for d in domains:
        lines.append("Domain `%s` (%s):" % (d["id"], d["title"]))
        for c in controls:
            if c["domain"] != d["id"]:
                continue
            lines.append("- `%s`%s: %s" % (c["id"], " (release-critical)" if c["release_critical"] else "", c["title"]))
            lines.append("  - Applies when: %s" % c["applies_when"])
            lines.append("  - VERIFIED when: %s" % c["verified_when"])
            lines.append("  - Static limits: %s" % c["static_limits"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def expected_blocks(agent_name, agents_map):
    blocks = {"untrusted-data": load(os.path.join(BLOCKS, "untrusted-data.md"))}
    if agent_name in agents_map:
        blocks["domain-output-contract"] = load(os.path.join(BLOCKS, "domain-output-contract.md"))
        blocks["controls"] = controls_block(agents_map[agent_name])
    return blocks


def sync(check):
    if not os.path.isdir(AGENTS):
        return 0
    agents_map = domain_agents()
    stale = []
    for fn in sorted(os.listdir(AGENTS)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(AGENTS, fn)
        text = load(path)
        blocks = expected_blocks(fn[:-3], agents_map)
        found = {m.group(2) for m in BLOCK_RE.finditer(text)}
        missing = set(blocks) - found
        unknown = found - set(blocks)
        if missing or unknown:
            stale.append("%s: missing blocks %s, unexpected blocks %s" % (fn, sorted(missing), sorted(unknown)))
            continue
        new = BLOCK_RE.sub(lambda m: m.group(1) + blocks[m.group(2)] + m.group(4), text)
        if new != text:
            stale.append("%s: generated blocks are out of date" % fn)
            if not check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
    if check and stale:
        print("\n".join(stale))
        print("Run: python3 tools/sync_agents.py")
        return 1
    if not check:
        for s in stale:
            print("updated " + s.split(":")[0] if "out of date" in s else "ERROR " + s)
        return 1 if any("missing blocks" in s for s in stale) else 0
    return 0


if __name__ == "__main__":
    sys.exit(sync("--check" in sys.argv))

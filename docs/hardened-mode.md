# Auditing untrusted code

Use this when you audit code you did not write or do not trust: a vendor drop, an acquisition target, a third-party repository, or anything that might be adversarial.

## Why it needs care

When Claude Code starts inside a project, that project's own Claude configuration can load. It can include `CLAUDE.md` and `AGENTS.md` instructions, `.claude/settings.json` (hooks, environment, permission defaults), nested `.claude/skills`, and `.mcp.json` servers. A malicious repository can use these to inject instructions or run commands before Fairtide does anything.

Inside the audit, Fairtide's agents can only Read, Grep, and Glob. They treat all project content as untrusted data and report instructions aimed at AI tools as findings. These are strong mitigations, but **prompt injection cannot be fully prevented by any current tool**. The safeguards below reduce risk; they do not eliminate it.

## Three tiers

| Tier | When | How |
|---|---|---|
| Own code | You wrote it, or trust its contents | Run `/fairtide:audit` inside the project. |
| Untrusted code | Not yours; probably benign | Use the neutral-workspace procedure below. |
| Hostile code | Possibly adversarial | Use the neutral-workspace procedure inside a disposable container or VM with no credentials, and network access limited to your model provider. |

## Neutral-workspace procedure

1. **Create an empty workspace and an output directory, both outside the target:**

   ```bash
   mkdir -p ~/fairtide-work ~/fairtide-out
   cd ~/fairtide-work
   ```

2. **Create `~/fairtide-work/audit-settings.json`.** Replace `/abs/target`, `/abs/out`, and `/abs/plugin` with absolute paths. For `/abs/plugin`, use the plugin directory from a clone or from your Claude Code plugin cache.

   ```json
   {
     "permissions": {
       "additionalDirectories": ["/abs/target", "/abs/out"],
       "allow": [
         "Bash(python3 /abs/plugin/scripts/fairtide.py *)",
         "Edit(//abs/out/**)"
       ],
       "deny": [
         "WebFetch",
         "WebSearch",
         "Edit(//abs/target/**)"
       ]
     },
     "claudeMdExcludes": [
       "/abs/target/**/CLAUDE.md",
       "/abs/target/**/CLAUDE.local.md",
       "/abs/target/**/AGENTS.md"
     ],
     "disableSkillShellExecution": true,
     "disableAllHooks": true,
     "autoMemoryEnabled": false
   }
   ```

   What each part does:
   - **`additionalDirectories`** in a settings file grants file access without loading that directory's Claude configuration. Do not use `--add-dir` for the target; it loads the target's skills and other configuration.
   - **`Edit(...)` rules** govern all file-writing tools: the allow rule permits writing only to the output directory, and the deny rule blocks writes into the target.
   - **`claudeMdExcludes`** keeps the target's instruction files out of context. The patterns must be absolute.
   - **The remaining settings** stop target skills from running shell commands, disable hooks, and keep auto memory from persisting anything learned from the target.

3. **Start Claude Code in the neutral workspace and run the audit:**

   ```bash
   claude --plugin-dir /abs/plugin --settings ~/fairtide-work/audit-settings.json --strict-mcp-config
   ```

   Then, in the session:

   ```
   /fairtide:audit /abs/target --untrusted --out /abs/out
   ```

   For a non-interactive run:

   ```bash
   claude -p "/fairtide:audit /abs/target --untrusted --out /abs/out" \
     --plugin-dir /abs/plugin --settings ~/fairtide-work/audit-settings.json \
     --permission-mode dontAsk --tools "Read,Write,Agent,Bash,Glob,Grep" \
     --strict-mcp-config --no-session-persistence --max-budget-usd 30
   ```

   With `--permission-mode dontAsk`, anything not explicitly allowed is denied. Prefer the default permission mode for interactive runs: plan mode and auto mode can approve actions without asking you.

4. **Start a fresh session before acting on the findings.** The target's content is in the audit conversation's context, and Fairtide's per-skill tool restrictions end when you send your next message.

## Known limits

- **Version-dependent settings.** Settings names and behavior can change between Claude Code versions. This procedure was written for Claude Code 2.1.289; check the Claude Code documentation for your version.
- **Nested instruction files.** Whether nested instruction files inside an additional directory load on demand is not documented. `claudeMdExcludes` covers the common file names; a container adds protection beyond that.
- **`--restricted`.** Claude Code's `--restricted` flag removes command-running tools, but it cannot be enforced in cloud or remote sessions. Fairtide needs Bash for its own script, so the procedure above relies on allow and deny rules instead.
- **Remediation.** Fairtide refuses to remediate untrusted projects.

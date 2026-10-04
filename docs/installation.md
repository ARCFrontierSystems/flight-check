# Installation

## Requirements

- Claude Code with plugin support. Fairtide was developed and tested with Claude Code 2.1.289 and uses agent settings introduced in 2.1.271.
- Python 3.9 or newer, available as `python3`. Fairtide's script uses only the standard library. On Windows, make sure `python3` resolves, for example through the Python launcher or an alias; otherwise Fairtide reports that its deterministic checks could not run.

## Personal use (all projects on your machine)

```bash
claude plugin marketplace add ARCFrontierSystems/project-guardian
claude plugin install fairtide@arc-frontier-systems
```

Or, inside a Claude Code session:

```
/plugin marketplace add ARCFrontierSystems/project-guardian
/plugin install fairtide@arc-frontier-systems
```

Adding the marketplace clones this repository. It contains the plugin and its development tooling. It does not contain deliberately vulnerable test applications; those are kept in a separate repository.

## Team use

To offer Fairtide to everyone working in a repository, commit `.claude/settings.json` in that repository:

```json
{
  "extraKnownMarketplaces": {
    "arc-frontier-systems": {
      "source": {"source": "github", "repo": "ARCFrontierSystems/project-guardian"}
    }
  },
  "enabledPlugins": {
    "fairtide@arc-frontier-systems": true
  }
}
```

## Try it without installing

```bash
git clone https://github.com/ARCFrontierSystems/project-guardian.git
cd /path/to/your/project
claude --plugin-dir /path/to/project-guardian/plugins/fairtide
```

## Allowing Fairtide's script

Fairtide runs `python3 <plugin directory>/scripts/fairtide.py` to validate results, check evidence, and compute the ship gate. Claude Code asks you to approve it; choose "always allow" for that command to avoid repeated prompts. For non-interactive runs, add this to your settings' `permissions.allow`:

```json
"Bash(python3 /absolute/path/to/plugins/fairtide/scripts/fairtide.py *)"
```

The script never makes network connections and never runs your project's code. The repository lint rejects any import of network or process modules in it.

## Updates and removal

- Update: `claude plugin update fairtide@arc-frontier-systems`. Automatic updates are off by default for third-party marketplaces.
- Remove: `claude plugin uninstall fairtide@arc-frontier-systems`.
- Fairtide's data lives in each project's `.fairtide/` directory (run output and the ledger). Delete it if you no longer need it.

## Cloud sessions and Cowork

Personal plugins and skills installed on your machine are not available in Claude Code cloud sessions or Cowork. To use Fairtide there, commit the team settings above to the repository you work on, or check what your Claude plan supports for account-level plugins.

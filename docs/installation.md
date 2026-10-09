# Installation

## Requirements

- Claude Code with plugin support. Flight Check was developed and tested with Claude Code 2.1.289 and uses agent settings introduced in 2.1.271.
- Python 3.9 or newer, available as `python3`. Flight Check's script uses only the standard library. On Windows, make sure `python3` resolves, for example through the Python launcher or an alias; otherwise Flight Check reports that its deterministic checks could not run.

## Personal use (all projects on your machine)

```bash
claude plugin marketplace add ARCFrontierSystems/flight-check
claude plugin install flight-check@arc-frontier-systems
```

Or, inside a Claude Code session:

```
/plugin marketplace add ARCFrontierSystems/flight-check
/plugin install flight-check@arc-frontier-systems
```

Adding the marketplace clones this repository. It contains the plugin and its development tooling. It does not contain deliberately vulnerable test applications; those are kept in a separate repository.

## Team use

To offer Flight Check to everyone working in a repository, commit `.claude/settings.json` in that repository:

```json
{
  "extraKnownMarketplaces": {
    "arc-frontier-systems": {
      "source": {"source": "github", "repo": "ARCFrontierSystems/flight-check"}
    }
  },
  "enabledPlugins": {
    "flight-check@arc-frontier-systems": true
  }
}
```

## Try it without installing

```bash
git clone https://github.com/ARCFrontierSystems/flight-check.git
cd /path/to/your/project
claude --plugin-dir /path/to/flight-check/plugins/flight-check
```

## Allowing Flight Check's script

Flight Check runs `python3 <plugin directory>/scripts/flight_check.py` to validate results, check evidence, and compute the ship gate. Each skill pre-approves only the subcommands it needs during its first turn; after that, Claude Code asks you. To avoid repeated prompts, or for non-interactive runs, allow the audit subcommands in your settings' `permissions.allow`:

```json
"Bash(python3 /absolute/path/to/plugins/flight-check/scripts/flight_check.py init-run *)",
"Bash(python3 /absolute/path/to/plugins/flight-check/scripts/flight_check.py validate *)",
"Bash(python3 /absolute/path/to/plugins/flight-check/scripts/flight_check.py finalize *)",
"Bash(python3 /absolute/path/to/plugins/flight-check/scripts/flight_check.py render *)"
```

Add `runs`, `findings`, and `packet` the same way if you build Attorney Review Packets non-interactively.

**Do not allow `flight_check.py ledger accept`, `revoke`, `legal`, or `close`, and do not allow `flight_check.py *`.** Those commands record decisions only you can make: accepted risks, counsel decisions, and closures. Their permission prompt is part of your confirmation, so a skill can never record one without you seeing it.

The script never makes network connections and never runs your project's code. The repository lint rejects any import of network or process modules in it.

## Updates and removal

- Update: `claude plugin update flight-check@arc-frontier-systems`. Automatic updates are off by default for third-party marketplaces.
- Remove: `claude plugin uninstall flight-check@arc-frontier-systems`.
- Flight Check's data lives in each project's `.flight-check/` directory (run output and the ledger). Delete it if you no longer need it.

## Cloud sessions and Cowork

Personal plugins and skills installed on your machine are not available in Claude Code cloud sessions or Cowork. To use Flight Check there, commit the team settings above to the repository you work on, or check what your Claude plan supports for account-level plugins.

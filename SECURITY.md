# Security policy

## Reporting a vulnerability in Flight Check

Please report security issues in Flight Check itself privately, using GitHub's private vulnerability reporting for this repository ("Security" tab, then "Report a vulnerability"). Do not open public issues for vulnerabilities.

In scope:
- **Code execution or exfiltration:** ways an audited project could make Flight Check execute code, write outside its output directory, read outside the audit root, or exfiltrate data.
- **Prompt injection** that changes Flight Check's behavior beyond what is documented in [docs/limitations.md](docs/limitations.md).
- **Tampering:** flaws that let findings, evidence, the ledger, or the ship gate be altered or bypassed.
- **Secrets:** secret-redaction failures in Flight Check's output files.
- **Dependencies and packaging** of the plugin.

Out of scope: findings that Flight Check reports about *other* projects. Report those to the owners of those projects.

We aim to acknowledge reports within 5 business days. We will coordinate disclosure with you.

## Supported versions

Only the latest released version receives security fixes. Flight Check 1.0.0 is the current release; fixes go into the next release.

## Design notes

- **Read-only agents.** Flight Check's audit agents can only read (Read, Grep, Glob).
- **Its own tooling only.** The audit skills pre-approve no command other than Flight Check's own script, which has no network access and never runs project code.
- **One narrow set of hooks, no MCP servers.** Watch mode's hooks run only Flight Check's own script, which reads local files and does nothing unless you turned watch mode on for the project. The plugin ships no MCP servers.
- **Hardened procedure.** See [docs/hardened-mode.md](docs/hardened-mode.md) for auditing untrusted code.

# Security policy

## Reporting a vulnerability in Fairtide

Please report security issues in Fairtide itself privately, using GitHub's private vulnerability reporting for this repository ("Security" tab, then "Report a vulnerability"). Do not open public issues for vulnerabilities.

In scope:
- **Code execution or exfiltration:** ways an audited project could make Fairtide execute code, write outside its output directory, read outside the audit root, or exfiltrate data.
- **Prompt injection** that changes Fairtide's behavior beyond what is documented in [docs/limitations.md](docs/limitations.md).
- **Tampering:** flaws that let findings, evidence, the ledger, or the ship gate be altered or bypassed.
- **Secrets:** secret-redaction failures in Fairtide's output files.
- **Dependencies and packaging** of the plugin.

Out of scope: findings that Fairtide reports about *other* projects. Report those to the owners of those projects.

We aim to acknowledge reports within 5 business days. We will coordinate disclosure with you.

## Supported versions

Only the latest released version receives security fixes. Fairtide is currently pre-release (0.1.0).

## Design notes

- **Read-only agents.** Fairtide's audit agents can only read (Read, Grep, Glob).
- **Its own tooling only.** The audit skills pre-approve no command other than Fairtide's own script, which has no network access and never runs project code.
- **No plugin extensions.** The plugin ships no hooks or MCP servers.
- **Hardened procedure.** See [docs/hardened-mode.md](docs/hardened-mode.md) for auditing untrusted code.

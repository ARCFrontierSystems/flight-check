# GitHub settings to switch on

Status: **saved for later, not yet applied.** The repository is already public (checked 2026-10-04), so apply these soon, and before accepting the first outside contribution. The two most urgent are private vulnerability reporting, because `SECURITY.md` already sends reporters there, and the description fix.

As of 2026-10-04. GitHub moves these menus from time to time; if a path below no longer matches, search the repository settings for the setting's name. A repository admin applies them in the GitHub web UI.

All of these are free for public repositories. If the repository is ever made private, rulesets and secret scanning need a paid GitHub plan, and private vulnerability reporting is not offered.

## 1. Repository basics

- **Description and topics** (repository home page, gear icon next to "About").
  - The current description overclaims: it says the project will "enforce secure development practices" and "protect applications and data". Flight Check finds and documents risks; it does not enforce or guarantee anything.
  - Suggested description: "Evidence-based ship-readiness audits for Claude Code projects. Finds and documents risks; does not certify security or legal compliance."
  - Suggested topics: `claude-code`, `claude-code-plugin`, `security-audit`, `code-review`, `release-readiness`, `accessibility`, `privacy`.
- **Private vulnerability reporting** (Settings → Advanced Security or Code security → Private vulnerability reporting → Enable). `SECURITY.md` tells reporters to use it, so it must be on.
- **Secret scanning and push protection** (same settings page, Secret Protection). Push protection blocks a push that contains a recognized credential.
- **Dependabot alerts** (same page). Flight Check has no runtime dependencies, but the workflow uses GitHub Actions. Optionally add `.github/dependabot.yml` with the `github-actions` ecosystem, so pinned action SHAs get update pull requests.
- **Repository secret `FLIGHT_CHECK_LEAK_DENYLIST`** (Settings → Secrets and variables → Actions → New repository secret).
  - Value: the private denylist, one term per line. Never commit it or paste it into issues, pull requests, or logs.
  - CI writes it to a temporary file for `tools/lint_plugin.py`. Without the secret, for example on pull requests from forks, which never receive secrets, that check is skipped; the hashed canaries in `tools/leak-hashes.txt` still run.
- **Unused features** (Settings → General → Features). Turn off the wiki, since documentation lives in `docs/`. Leave Discussions and Projects off unless you plan to use them.

## 2. Ruleset for the default branch

Settings → Rules → Rulesets → New branch ruleset. Target the default branch and set enforcement to Active.

- **Restrict deletions** and **block force pushes.**
- **Require a pull request before merging.**
  - Solo maintainer: set required approvals to 0, so you can merge your own pull requests while still going through CI.
  - With a second maintainer: require 1 approval, dismiss stale approvals when new commits are pushed, and require conversation resolution.
- **Require status checks to pass.** Add these checks, spelled exactly as the CI job names:
  - `Unit tests and lint (Python 3.9)`
  - `Unit tests and lint (Python 3.12)`
  - `Claude Code plugin validation`

  If a job name in `.github/workflows/ci.yml` changes, update the ruleset in the same change. Otherwise merges wait forever for a check that no longer runs.
- **Optional:** require signed commits, but only after confirming that every tool you commit with can sign. Require linear history if you prefer squash or rebase merges.
- **Bypass list:** leave it empty, or add only the repository admin for emergencies.

## 3. Ruleset for release tags

New tag ruleset targeting `v*`. Restrict updates and deletions and block force pushes. Restrict creation too, with the maintainers on the bypass list. This ensures a published release tag always points to the code that was reviewed.

## 4. GitHub Actions

Settings → Actions → General:

- **Actions permissions:** allow only actions created by GitHub. The workflow uses only `actions/checkout`, `actions/setup-python`, and `actions/setup-node`. If GitHub offers the option to require actions pinned to a full-length commit SHA, enable it; the workflow already pins by SHA.
- **Workflow permissions:** select read-only repository contents. Leave "Allow GitHub Actions to create and approve pull requests" unchecked. The workflow also sets `permissions: contents: read` and `persist-credentials: false` itself.
- **Fork pull request workflows:** require approval for all outside contributors.

## 5. CODEOWNERS

Once maintainers are settled, add `.github/CODEOWNERS`. Replace the placeholder with the real handle or team:

```
*                    @MAINTAINER
/plugins/            @MAINTAINER
/.github/            @MAINTAINER
/tools/leak-hashes.txt @MAINTAINER
```

Then turn on "Require review from Code Owners" in the default-branch ruleset. With a single maintainer this adds nothing, so it can wait.

## 6. Finish the rename to Flight Check

The product is now named Flight Check; the repository is still named `project-guardian`.

- Rename the repository to `flight-check` (Settings → General → Repository name). GitHub redirects old URLs, but then update `ARCFrontierSystems/project-guardian` in `README.md`, `docs/installation.md`, and the `$id` in `plugins/flight-check/schemas/flight-check.schema.json`.
- Use the Flight Check description and topics from section 1.
- Create the CI secret as `FLIGHT_CHECK_LEAK_DENYLIST` (the old `SEAWORTHY_LEAK_DENYLIST` name is no longer read).

## Checklist

- [ ] Description and topics corrected
- [ ] Private vulnerability reporting on
- [ ] Secret scanning and push protection on
- [ ] Dependabot alerts on (optional: `dependabot.yml` for Actions)
- [ ] `FLIGHT_CHECK_LEAK_DENYLIST` secret added
- [ ] Wiki off
- [ ] Default-branch ruleset active with the three required checks
- [ ] Tag ruleset for `v*` active
- [ ] Actions: GitHub-only actions, read-only token, fork approval required
- [ ] CODEOWNERS (when there is more than one maintainer)
- [ ] Repository renamed to `flight-check`

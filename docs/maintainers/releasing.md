# Releasing Flight Check

A release is a version bump merged to `main`, then a GitHub release with a `vX.Y.Z` tag on that merge commit.

## Steps

1. On a branch, set the new version in all three places, which must agree:
   - `plugins/flight-check/.claude-plugin/plugin.json` (`version`)
   - `plugins/flight-check/scripts/flight_check_lib/__init__.py` (`VERSION`)
   - `CHANGELOG.md`: rename the `Unreleased` heading to the version and date.
   Also update the status line in `README.md` and the supported version in `SECURITY.md`.
2. Run the checks listed in `CLAUDE.md` (unit tests, agent sync, lint with the private denylist, strict plugin validation).
   For a release that changes a state file format (`project.json`, `registers.json`, `ledger.json`, `history.jsonl`), also check the migration: the unit tests migrate a ledger in format 1.0 and earlier project state, and `docs/upgrading-from-rc.md` (or a newer upgrade page) must describe what changes, what to commit, and how to go back.
3. Open a pull request, wait for CI and the DCO check, and merge it.
4. On GitHub: Releases → Draft a new release → choose a tag, type `vX.Y.Z`, target `main` → title "Flight Check X.Y.Z" → paste the CHANGELOG section → Publish. Tick "Set as a pre-release" for versions below 1.0.0 or with a pre-release suffix such as `-rc.1`.
5. From a clean directory, check the published install: `claude plugin marketplace add ARCFrontierSystems/flight-check`, `claude plugin install flight-check@arc-frontier-systems`, then `/flight-check:audit` on a small project.

## Rolling back

Never move or delete a published tag; the tag ruleset in `github-settings.md` blocks it.

- **Users** can return to an earlier release at once by installing from that tag. See "Use a specific version" in [docs/installation.md](../installation.md).
- **Maintainers** revert the faulty change on `main` in a pull request and publish it as a new patch release (for example `v1.0.1`), so `claude plugin update` moves everyone forward to a working version.
- For a security problem, follow `SECURITY.md` and say in the release notes which versions are affected.

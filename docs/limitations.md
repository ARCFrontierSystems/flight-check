# Limitations

Flight Check is designed to make it harder to ship software without understanding its risks. It cannot make software safe, and it can be wrong. Known limitations:

## Analysis scope

- **Static and read-only.** Flight Check reads code, configuration, and documentation. It does not run the application or its tests, scan deployed infrastructure, or inspect cloud consoles, dashboards, or provider settings. Anything that depends on runtime or external state is reported as UNVERIFIED unless you import evidence.
- **Offline.** Flight Check does not query vulnerability databases. Known-vulnerability status of dependencies stays UNVERIFIED unless you import scanner output for the audited revision (`--evidence`).
- **Git history and artifacts.** Secrets or licensed material in version-control history, build artifacts, or CI logs are not examined unless imported as evidence.
- **Accessibility.** Static analysis can find missing names, labels, and structural problems. Contrast in context, focus order, and screen-reader behavior need runtime testing.
- **Large repositories.** Agents work within turn and context limits. Very large projects may be only partly examined; Flight Check records what it could not examine in coverage notes.

## Model behavior

- **False negatives.** Agents can miss issues, especially ones that span many files or depend on subtle framework behavior.
- **False positives.** The false-positive defense and the verifier reduce false positives but do not eliminate them.
- **Non-determinism.** Two audits of the same code can differ. The ledger flags findings that appear or disappear between runs, so differences are visible rather than silent.
- **Prompt injection.** Audited content can try to manipulate the auditor, either to suppress findings or to cause actions. Flight Check's agents are read-only and treat content as data, but no current approach fully prevents prompt injection. Use the [hardened procedure](hardened-mode.md) for untrusted code.

## Legal and compliance

- **Not legal advice.** The Legal Review Assistant identifies issues and prepares questions for qualified counsel. It does not determine which laws apply, interpret contracts, or judge enforceability or adequacy.
- **No jurisdiction research.** Jurisdiction-specific legal research is not performed. Anything that depends on it is marked UNVERIFIED.
- **No certification.** Compliance readiness is reported as evidence and gaps, never as compliance or certification.

## Mechanics

- **Evidence checking proves location, not meaning.** It confirms that cited text exists at the cited location. It cannot prove that the text means what the finding claims; the verifier and human review address that.
- **The ship gate depends on its inputs.** The gate is deterministic given its inputs, but those inputs come from model judgments about severity, confidence, and control states.
- **PDF fonts.** The Attorney Review Packet uses the standard PDF fonts. Characters outside Western European scripts are replaced in the PDF and disclosed. The accompanying Markdown file keeps the original text.
- **PDF accessibility.** The packet PDF declares its language and title, but it is not tagged, so screen readers may read it in the wrong order or miss its structure. The Markdown file written next to it has the same content and is the accessible version.

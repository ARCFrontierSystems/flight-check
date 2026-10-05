# Standards referenced by Flight Check

As of: 2026-10-04. Review this list at least once per release; standards change.

Flight Check refers to standards **by identifier only**, for example `CWE-89`, `WCAG 2.2 SC 1.1.1`, or `LLM01` from the OWASP Top 10 for LLM Applications 2026. It never reproduces their text, for two reasons:
- **Licensing.** Several standards are licensed under share-alike terms (OWASP projects use CC BY-SA 4.0) or are proprietary (PCI DSS, ISO/IEC standards, AICPA criteria). Quoting them would bring those terms into Flight Check.
- **Accuracy.** Paraphrased requirements drift from the source. Identifiers let readers consult the authoritative text.

| Area | Standard (version as of the date above) | Used for | License notes |
|---|---|---|---|
| Weaknesses | CWE (4.20) | Finding identifiers | Free to cite; MITRE terms apply to reproduced content |
| Web application security | OWASP ASVS 5.0.0; OWASP Top 10:2025 | Background for agent procedures | CC BY-SA 4.0: cite only |
| APIs | OWASP API Security Top 10 (2023) | Background | CC BY-SA 4.0: cite only |
| AI/LLM | OWASP Top 10 for LLM Applications 2026; OWASP Top 10 for Agentic Applications 2026 | Category identifiers in AI findings | CC BY-SA 4.0: cite only |
| Accessibility | WCAG 2.2 | Success criterion numbers | W3C document license: cite by number |
| Supply chain | SLSA 1.2; OpenSSF Scorecard; CycloneDX 1.7; SPDX 3.0.1 | Background | Permissive or community licenses |
| Payments | PCI DSS 4.0.1 | Potential considerations only; never compliance statements | Proprietary: identifiers only |
| Privacy and other regulations | For example GDPR, UK GDPR, CCPA/CPRA, COPPA, HIPAA, EU AI Act | Named only as potential considerations for counsel | Flight Check never states applicability or compliance |

Flight Check does not cite statutes, regulations, or case law in findings or Attorney Review Packets. Whether any law applies is a question for qualified counsel.

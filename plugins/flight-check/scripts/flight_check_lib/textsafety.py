"""Sanitizing, secret redaction, and language guards for text that came from audited projects.

Everything quoted from an audited project is untrusted. Before Flight Check renders it
into Markdown or PDF, control characters, bidirectional overrides, zero-width and
tag characters are removed, likely secrets are masked, and Markdown syntax that
could create links, images, or HTML is neutralized.
"""

import math
import re
import unicodedata

# Bidirectional formatting, zero-width, and other invisible characters used to hide text.
_INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)")


def sanitize(text):
    """Remove ANSI escapes, C0/C1 controls (except newline and tab), and invisible characters."""
    if text is None:
        return ""
    text = _ANSI.sub("", str(text))
    text = _INVISIBLE.sub("", text)
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat == "Cc" or cat == "Cf":
            continue
        out.append(ch)
    return "".join(out)


def find_invisible(text):
    """Return a list of (index, codepoint) for characters sanitize() would remove (excluding \\n, \\t)."""
    hits = []
    for i, ch in enumerate(text):
        if ch in "\n\t\r":
            continue
        if _INVISIBLE.match(ch) or unicodedata.category(ch) in ("Cc", "Cf"):
            hits.append((i, "U+%04X" % ord(ch)))
    return hits


# --- secret redaction -------------------------------------------------------

REDACTION_MARKER = re.compile(r"\[REDACTED(?::[^\]\n]{0,40})?\]")

_SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("url-credentials", re.compile(r"(?<=://)[^/\s:@]{1,64}:[^/\s@]{3,128}(?=@)")),
]

# key = "value" style assignments where the key name suggests a secret.
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|bearer)[A-Za-z0-9_.-]*)"
    r"(\s*[:=]\s*|\s*=>\s*)"
    r"(['\"]?)([^'\"\s,;}{)]{6,})(\3)"
)

_PLACEHOLDER = re.compile(
    r"(?i)^(\$\{?[A-Z0-9_]+\}?|process\.env\.[A-Za-z0-9_]+|os\.environ.*|env\(.*|getenv\(.*|<[^>]+>|x{4,}|\*{4,}|"
    r"changeme|change-me|your[-_].*|example.*|placeholder.*|dummy.*|redacted.*|none|null|true|false|undefined|"
    r"\[REDACTED.*)$"
)


def _entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = float(len(s))
    return -sum((c / n) * math.log(c / n, 2) for c in counts.values())


_HIGH_ENTROPY = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def redact(text):
    """Mask likely secrets. Returns (redacted_text, count)."""
    if not text:
        return text, 0
    count = 0

    def mark(kind):
        return "[REDACTED:%s]" % kind

    for kind, pattern in _SECRET_PATTERNS:
        text, n = pattern.subn(mark(kind), text)
        count += n

    def assign(m):
        nonlocal count
        value = m.group(4)
        if _PLACEHOLDER.match(value) or REDACTION_MARKER.search(value):
            return m.group(0)
        count += 1
        return "%s%s%s%s%s" % (m.group(1), m.group(2), m.group(3), mark("credential"), m.group(5))

    text = _ASSIGNMENT.sub(assign, text)

    def entropy(m):
        nonlocal count
        token = m.group(0)
        if REDACTION_MARKER.search(token):
            return token
        has_digit = any(c.isdigit() for c in token)
        has_alpha = any(c.isalpha() for c in token)
        if has_digit and has_alpha and _entropy(token) >= 4.2:
            count += 1
            return mark("high-entropy")
        return token

    text = _HIGH_ENTROPY.sub(entropy, text)
    return text, count


# --- language guards ----------------------------------------------------------

_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"|“[^”\n]{0,400}”|`[^`\n]{0,400}`")

_REGIMES = r"(?:gdpr|uk gdpr|hipaa|ccpa|cpra|pci(?:[\s-]dss)?|soc\s?2|iso(?:/iec)?\s?27001|iso\s?42001|coppa|ferpa|glba|ada|wcag(?:\s?2\.[0-2])?|section\s?508|eaa)"

OVERCLAIM_PATTERNS = [
    (re.compile(r"(?i)\b(?:is|are|be|being|remains?|now)\s+(?:fully\s+|completely\s+)?" + _REGIMES + r"[\s-]+(?:compliant|certified|conformant)\b"),
     "states compliance or certification with a named regime"),
    (re.compile(r"(?i)\blegally\s+compliant\b"), "states legal compliance"),
    (re.compile(r"(?i)\bcompliant\s+with\s+(?:all\s+)?(?:applicable\s+)?(?:laws|regulations|legal requirements)\b"), "states legal compliance"),
    (re.compile(r"(?i)\byou\s+are\s+(?:currently\s+)?(?:in\s+)?violat(?:ing|ion)\b"), "states a legal violation as fact"),
    (re.compile(r"(?i)\b(?:is|are)\s+(?:in\s+)?(?:clear\s+)?violation\s+of\b"), "states a legal violation as fact"),
    (re.compile(r"(?i)\bviolates\s+(?:the\s+)?" + _REGIMES + r"\b"), "states a legal violation as fact"),
    (re.compile(r"(?i)\b(?:is|are)\s+(?:legally\s+)?(?:fully\s+)?(?:un)?enforceable\b"), "determines enforceability"),
    (re.compile(r"(?i)\bprotected\s+from\s+(?:all\s+)?liability\b"), "asserts liability protection"),
    (re.compile(r"(?i)\b(?:this|it|that)\s+is\s+(?:perfectly\s+)?(?:il)?legal\b"), "states a legal conclusion"),
    (re.compile(r"(?i)\b(?:iso|soc\s?2)\s+certified\b"), "states certification"),
    (re.compile(r"(?i)\b(?:the\s+)?(?:application|project|system|app|service|codebase|software)\s+is\s+(?:fully\s+|completely\s+)?(?:secure|safe for production|production[-\s]safe)\b"), "asserts the project is secure or production safe"),
    (re.compile(r"(?i)\bproduction[-\s]safe\b"), "asserts production safety"),
    (re.compile(r"(?i)\bthis\s+(?:is|constitutes)\s+legal\s+advice\b"), "presents output as legal advice"),
]

VAGUE_PATTERNS = [
    re.compile(r"(?i)\b(?:could|should|can|might)\s+be\s+improved\b"),
    re.compile(r"(?i)\bfollow(?:ing)?\s+(?:security\s+)?best\s+practices\b"),
    re.compile(r"(?i)^\s*(?:general|various|potential|possible|some)?\s*(?:security|privacy|code quality|compliance|legal)\s+(?:issues?|concerns?|improvements?|risks?)\s*$"),
    re.compile(r"(?i)\bvarious\s+(?:issues|problems|weaknesses)\b"),
    re.compile(r"(?i)\bconsider\s+(?:improving|reviewing)\s+(?:security|privacy)\b"),
]


def strip_quoted(text):
    """Remove quoted spans so guards do not fire on text attributed to the audited project."""
    return _QUOTED.sub(" ", text or "")


def overclaims(text):
    """Return reasons for each overclaim phrase found outside quoted spans."""
    body = strip_quoted(text)
    found = []
    for pattern, reason in OVERCLAIM_PATTERNS:
        m = pattern.search(body)
        if m:
            found.append("%s: \"%s\"" % (reason, m.group(0).strip()))
    return found


def vague(text):
    body = strip_quoted(text)
    return [p.pattern for p in VAGUE_PATTERNS if p.search(body)]

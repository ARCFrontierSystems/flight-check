"""The user's local preferences for this project: how much an audit may cost before Flight Check asks, and which models
its agents use. Kept in `.flight-check/settings.json`, which stays on this machine (it is gitignored)."""

import os

from . import state
from .jsonio import InputError, dump_json, load_json

NAME = "settings.json"
TIERS = ("economy", "balanced", "thorough")
DEFAULTS = {"max_audit_usd": 5.0, "models": "balanced"}
# Model per agent role for each tier. "balanced" is what Flight Check's evaluations measured (Sonnet-class agents,
# set in each agent's own settings), so it needs no override. "economy" runs the checkers on a smaller model and keeps
# the verifier, which filters false positives, on the measured one. "thorough" uses the most capable model.
AGENT_MODELS = {
    "economy": {"inventory": "haiku", "domain": "haiku", "verifier": "sonnet"},
    "balanced": None,
    "thorough": {"inventory": "opus", "domain": "opus", "verifier": "opus"},
}


def load(root):
    path = os.path.join(state.default_state_dir(root), NAME)
    doc = {}
    if os.path.isfile(path) and not os.path.islink(path):
        try:
            doc = load_json(path, 64 * 1024)
        except InputError:
            doc = {}
    out = dict(DEFAULTS)
    if isinstance(doc, dict):
        if isinstance(doc.get("max_audit_usd"), (int, float)) and doc["max_audit_usd"] >= 0:
            out["max_audit_usd"] = float(doc["max_audit_usd"])
        if doc.get("models") in TIERS:
            out["models"] = doc["models"]
    return out


def save(root, max_audit_usd=None, models=None):
    sd = state.default_state_dir(root)
    path = os.path.join(sd, NAME)
    if os.path.islink(sd) or os.path.islink(path):
        raise state.StateError("%s is a symbolic link; refusing to write through it" % path)
    current = load(root)
    if max_audit_usd is not None:
        if max_audit_usd < 0:
            raise state.StateError("--max-audit-usd must be 0 (never ask) or more")
        current["max_audit_usd"] = float(max_audit_usd)
    if models is not None:
        if models not in TIERS:
            raise state.StateError("--models must be one of %s" % ", ".join(TIERS))
        current["models"] = models
    os.makedirs(sd, exist_ok=True)
    state._ensure_gitignore(sd)
    dump_json(path, dict(current, schema_version="1.0"))
    return current

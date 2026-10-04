"""Loads Fairtide's bundled catalogs and schema relative to the plugin directory."""

import os

from .jsonio import load_json

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _path(*parts):
    return os.path.join(PLUGIN_ROOT, *parts)


_cache = {}


def _load(key, *parts):
    if key not in _cache:
        _cache[key] = load_json(_path(*parts))
    return _cache[key]


def schema():
    return _load("schema", "schemas", "fairtide.schema.json")


def domains():
    return _load("domains", "references", "domains.json")["domains"]


def domain_ids():
    return [d["id"] for d in domains()]


def domain_title(domain_id):
    for d in domains():
        if d["id"] == domain_id:
            return d["title"]
    return domain_id


def controls():
    return _load("controls", "references", "required-controls.json")["controls"]


def control_by_id():
    return {c["id"]: c for c in controls()}

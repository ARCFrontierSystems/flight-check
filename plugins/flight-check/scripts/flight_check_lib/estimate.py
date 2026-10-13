"""A rough usage estimate before an audit starts, so the user can decide before agents run.

Calibrated on Flight Check's own measured audits (docs/audits/): one pass of a full audit of a small or medium project
used about $1.20 to $6 of usage, and the domain agents are most of that. The estimate scales by project size, by how
many domain agents run, and by passes. It cannot know how much code the agents will read, so it is a range, not a quote.
"""

from . import catalog

# Usage of one full pass (every domain agent, the inventory, and the verifier), in US dollars, by project size.
PER_PASS = [(60, 1.5, 3.0, "small"), (1500, 2.0, 5.0, "medium"), (None, 4.0, 10.0, "large")]
DOMAIN_SHARE = 0.75  # the part of a pass that the domain agents use; the rest is the inventory and the verifier


def _half(x):
    return round(x * 2) / 2


def estimate(n_files, agents=None, passes=1):
    """{"usd_low", "usd_high", ...} for an audit launching `agents` domain agents (all of them when None)."""
    total_agents = len({d["agent"] for d in catalog.domains()})
    agents = total_agents if agents is None else max(0, min(agents, total_agents))
    for limit, low, high, size in PER_PASS:
        if limit is None or n_files <= limit:
            break
    factor = (1 - DOMAIN_SHARE) + DOMAIN_SHARE * passes * agents / total_agents
    usd_low, usd_high = max(0.5, _half(low * factor)), max(1.0, _half(high * factor))
    summary = ("Estimated usage: about $%.2f to $%.2f (%d of %d domain agents, %d pass%s, a %s project of %d files). "
               "This is a rough range from Flight Check's measured audits; actual usage depends on how much code the "
               "agents read and on the model. On a Claude subscription it counts toward your usage limits instead of "
               "being billed." % (usd_low, usd_high, agents, total_agents, passes, "" if passes == 1 else "es", size, n_files))
    return {"usd_low": usd_low, "usd_high": usd_high, "agents": agents, "passes": passes, "files": n_files, "size": size,
            "summary": summary}

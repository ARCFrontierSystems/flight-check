"""A rough usage estimate before an audit starts, so the user can decide before agents run.

Calibrated on Flight Check's own measured audits (docs/audits/), all run with Sonnet-class agents: one pass of a full
audit of a small or medium project used about $1.20 to $6 of usage, and the domain agents are most of that. The estimate
scales by project size, by how many domain agents run, by passes, by the model tier, and for quick checks. It cannot know
how much code the agents will read, so it is a range, not a quote.
"""

from . import catalog

# Usage of one full pass (every domain agent, the inventory, and the verifier), in US dollars, by project size.
PER_PASS = [(60, 1.5, 3.0, "small"), (1500, 2.0, 5.0, "medium"), (None, 4.0, 10.0, "large")]
DOMAIN_SHARE = 0.75  # the part of a pass that the domain agents use; the rest is the inventory and the verifier
# Multipliers on the domain agents' share, relative to the measured (balanced) tier. Economy is measured
# (docs/audits/2026-10-13-usage-efficiency.md); thorough depends on the price of the most capable model.
TIER = {"economy": (0.25, 0.45), "balanced": (1.0, 1.0), "thorough": (1.7, 5.0)}
QUICK = 0.35  # a quick check's agents read a fraction of what a full check reads


def _half(x):
    return round(x * 2) / 2


def estimate(n_files, agents=None, passes=1, tier="balanced", quick=False):
    """{"usd_low", "usd_high", ...} for an audit launching `agents` domain agents (all of them when None)."""
    total_agents = len({d["agent"] for d in catalog.domains()})
    agents = total_agents if agents is None else max(0, min(agents, total_agents))
    for limit, low, high, size in PER_PASS:
        if limit is None or n_files <= limit:
            break
    t_low, t_high = TIER.get(tier, TIER["balanced"])
    share = DOMAIN_SHARE * passes * agents / total_agents * (QUICK if quick else 1.0)
    usd_low = max(0.5, _half(low * ((1 - DOMAIN_SHARE) + share * t_low)))
    usd_high = max(1.0, _half(high * ((1 - DOMAIN_SHARE) + share * t_high)))
    kind = "quick check" if quick else "audit"
    summary = ("Estimated usage for this %s: about $%.2f to $%.2f (%d of %d checking agents, %d pass%s, %s models, a %s "
               "project of %d files). This is a rough range from Flight Check's measured audits; actual usage depends on "
               "how much code the agents read. On a Claude subscription it counts toward your usage limits instead of "
               "being billed." % (kind, usd_low, usd_high, agents, total_agents, passes, "" if passes == 1 else "es", tier,
                                  size, n_files))
    return {"usd_low": usd_low, "usd_high": usd_high, "agents": agents, "passes": passes, "files": n_files, "size": size,
            "tier": tier, "quick": quick, "summary": summary}

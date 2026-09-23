"""business-idea-radar: collect market signals and turn them into opportunity leads.

The premise is that business ideas are visible in public data before anyone
writes them down: what people complain about, what they ask for, what suddenly
gets funded, what companies are hiring for. This package pulls from four free,
key-less sources, scores each signal for how actionable it looks, and produces
a short daily digest.

Deliberately no LLM anywhere. The scoring is arithmetic over observable facts
so the output is reproducible and costs nothing to run.

Public API::

    from business_idea_radar import Radar, Signals

    radar = Radar()
    digest = radar.collect()
    print(digest.render())
"""

from .radar import Digest, Lead, Radar, RadarError, ScoreWeights
from .signals import Signal, Signals

__all__ = [
    "Radar",
    "RadarError",
    "Digest",
    "Lead",
    "Signals",
    "Signal",
    "ScoreWeights",
]

__version__ = "0.1.0"

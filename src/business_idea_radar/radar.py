"""Turn raw signals into scored leads and a readable digest.

Scoring answers one question: *is there evidence somebody would pay for this?*
The signals used are all observable, so the ranking is reproducible rather than
a matter of taste:

  * demand     - how much discussion the signal attracted
  * momentum   - how recent it is, decayed over a half-life
  * specificity- concrete titles beat vague ones
  * market     - money signals, e.g. jobs hiring for the same skills
  * novelty    - signals not already seen in recent runs
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .signals import Signal, Signals

WIB = timezone(timedelta(hours=7))
HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
DEFAULT_STATE = Path(HOME) / "scripts" / "business_radar_state.json"

# Words that signal a concrete, monetisable problem rather than idle curiosity.
PAIN_WORDS = (
    "anyone else", "how do you", "how to", "recommendation", "recommend",
    "alternative to", "looking for", "struggling", "frustrating", "expensive",
    "waste", "manual", "spreadsheet", "workaround", "why is", "best way",
)

# Words that make a title specific enough to act on.
CONCRETE_WORDS = (
    "api", "cli", "plugin", "saas", "dashboard", "bot", "agent", "pipeline",
    "self-hosted", "open source", "open-source", "integration", "analytics",
    "monitoring", "billing", "invoice", "payroll", "scheduling", "compliance",
)

VAGUE_WORDS = ("idea", "thoughts", "discussion", "random", "misc", "question")

# Skill tags that show companies are actively paying for a capability.
HIGH_VALUE_TAGS = (
    "ai", "machine learning", "llm", "automation", "data", "analytics",
    "security", "devops", "infrastructure", "saas", "api", "integration",
    "fintech", "healthcare", "logistics", "compliance",
)


class RadarError(RuntimeError):
    """Raised when the radar cannot produce any output at all."""


@dataclass
class ScoreWeights:
    demand: float = 35.0
    momentum: float = 25.0
    specificity: float = 20.0
    market: float = 12.0
    novelty: float = 8.0


@dataclass
class Lead:
    """A signal that has been scored and can be acted on."""

    signal: Signal
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    angle: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.signal.title

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "source": self.signal.source,
            "title": self.signal.title,
            "url": self.signal.url,
            "metric": self.signal.metric_label,
            "angle": self.angle,
            "tags": self.tags,
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
        }


@dataclass
class Digest:
    """The result of one radar run."""

    leads: list[Lead]
    generated_at: datetime
    counts: dict[str, int] = field(default_factory=dict)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    def top(self, n: int = 5) -> list[Lead]:
        return self.leads[:n]

    def by_source(self, source: str) -> list[Lead]:
        return [lead for lead in self.leads if lead.signal.source == source]

    def render(self, limit: int = 6) -> str:
        """Telegram friendly text block."""
        lines = [
            "💡 *IDE BISNIS — SINYAL PASAR*",
            f"🕐 {self.generated_at.strftime('%d %b %Y %H:%M')} WIB",
            "",
        ]

        if not self.leads:
            lines.append("📭 Tidak ada sinyal baru yang lolos filter.")
            if self.sources_failed:
                lines.append("")
                lines.append(f"⚠️ Sumber gagal: {', '.join(self.sources_failed)}")
            return "\n".join(lines)

        total = sum(self.counts.values())
        lines.append(
            f"📊 {total} sinyal dari {len(self.sources_ok)} sumber"
            f" → *{len(self.leads)} leads*"
        )
        lines.append("")

        for i, lead in enumerate(self.top(limit), 1):
            lines.append(f"*{i}. {lead.title[:90]}*")
            lines.append(f"   Skor {lead.score:.0f}/100 · {lead.signal.metric_label}")
            if lead.angle:
                lines.append(f"   💡 {lead.angle}")
            if lead.tags:
                lines.append(f"   🏷 {', '.join(lead.tags[:5])}")
            lines.append(f"   🔗 {lead.signal.url}")
            lines.append("")

        lines.append("─" * 28)
        src_summary = " · ".join(
            f"{k}={v}" for k, v in sorted(self.counts.items()) if v
        )
        lines.append(f"📈 {src_summary}")
        if self.sources_failed:
            lines.append(f"⚠️ Gagal: {', '.join(self.sources_failed)}")
        lines.append("")
        lines.append("_Sinyal mentah dari data publik. Bukan jaminan pasar._")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "counts": self.counts,
            "sources_ok": self.sources_ok,
            "sources_failed": self.sources_failed,
            "leads": [lead.as_dict() for lead in self.leads],
        }


class Radar:
    """Collect signals, score them, and keep a short memory of what was seen."""

    def __init__(
        self,
        signals: Signals | None = None,
        weights: ScoreWeights | None = None,
        state_path: Path | str | None = None,
        memory_days: int = 7,
    ) -> None:
        self.signals = signals or Signals()
        self.weights = weights or ScoreWeights()
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE
        self.memory_days = memory_days

    # -- memory ------------------------------------------------------------

    def load_memory(self) -> dict[str, float]:
        """Map of seen titles to the timestamp they were first seen."""
        try:
            with self.state_path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_memory(self, memory: dict[str, float]) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.memory_days)).timestamp()
        trimmed = {k: v for k, v in memory.items() if v >= cutoff}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self.state_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(trimmed, fh, indent=2)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # -- scoring -----------------------------------------------------------

    @staticmethod
    def _demand(sig: Signal) -> float:
        """Discussion volume, log scaled so one viral post cannot dominate.

        The scale differs per source: a GitHub repo with 3000 stars and a HN
        thread with 300 comments are both strong, but they are not the same
        number, so each source gets its own saturation point.
        """
        metric = max(sig.metric, 0.0)
        if metric <= 0:
            return 0.0
        # Saturation point per source: the value at which demand is "full".
        ceilings = {
            "hn_ask": 500.0,
            "hn_show": 1500.0,
            "jobs_remote": 8.0,
            "jobs_board": 8.0,
            "github_trending": 5000.0,
        }
        ceiling = ceilings.get(sig.source, 500.0)
        return min(metric / ceiling, 1.0) ** 0.5

    def _momentum(self, sig: Signal) -> float:
        """Recency, halving every three days."""
        age = sig.extra.get("age_hours")
        if age is None:
            return 0.5  # unknown age, neutral
        half_life_hours = 72.0
        return float(0.5 ** (age / half_life_hours))

    @staticmethod
    def _specificity(sig: Signal) -> float:
        text = sig.title.lower()
        hits = sum(1 for w in CONCRETE_WORDS if w in text)
        vague = sum(1 for w in VAGUE_WORDS if w in text)
        pain = sum(1 for w in PAIN_WORDS if w in text)
        score = min(hits * 0.35, 1.0)
        score += min(pain * 0.25, 0.5)
        score -= min(vague * 0.2, 0.4)
        return max(min(score, 1.0), 0.0)

    @staticmethod
    def _market(sig: Signal) -> float:
        """Do employers pay for this? Job tag overlap and hiring volume."""
        if not sig.source.startswith("jobs"):
            return 0.3  # neutral baseline for non-job signals
        tags = [t.lower() for t in (sig.extra.get("tags") or [])]
        if not tags:
            return 0.0
        hits = sum(1 for t in tags if any(h in t for h in HIGH_VALUE_TAGS))
        return min(hits / 3.0, 1.0)

    def _novelty(self, sig: Signal, memory: dict[str, float]) -> float:
        key = sig.title.lower()[:80]
        return 0.0 if key in memory else 1.0

    @staticmethod
    def _angle(sig: Signal, tags: list[str]) -> str:
        """One short line on why this might be worth a look."""
        source = sig.source
        if source == "hn_ask":
            return "Orang sedang bertanya soal ini. Cek keluhannya di komentar."
        if source == "hn_show":
            return "Sudah ada yang membangun dan dapat perhatian. Lihat celahnya."
        if source == "github_trending":
            lang = sig.extra.get("language") or "?"
            return f"Tren developer ({lang}). Peluang layanan/tooling pendukung."
        if source.startswith("jobs"):
            if tags:
                return f"Perusahaan membayar untuk: {', '.join(tags[:3])}."
            return "Permintaan tenaga kerja, indikasi kebutuhan nyata."
        return ""

    def score(self, sig: Signal, memory: dict[str, float] | None = None) -> Lead:
        """Score one signal into a Lead."""
        memory = memory if memory is not None else {}
        w = self.weights
        breakdown = {
            "demand": round(self._demand(sig) * w.demand, 2),
            "momentum": round(self._momentum(sig) * w.momentum, 2),
            "specificity": round(self._specificity(sig) * w.specificity, 2),
            "market": round(self._market(sig) * w.market, 2),
            "novelty": round(self._novelty(sig, memory) * w.novelty, 2),
        }
        tags = [str(t) for t in (sig.extra.get("tags") or [])][:6]
        if not tags and sig.extra.get("language"):
            tags = [str(sig.extra["language"])]
        return Lead(
            signal=sig,
            score=round(sum(breakdown.values()), 2),
            breakdown=breakdown,
            angle=self._angle(sig, tags),
            tags=tags,
        )

    # -- top level ---------------------------------------------------------

    def collect(
        self,
        limit: int = 20,
        min_score: float = 25.0,
        use_memory: bool = True,
        source_filter: str | None = None,
    ) -> Digest:
        """Run every collector, score, and return the digest."""
        raw = self.signals.collect_all()
        counts: dict[str, int] = {}
        for sig in raw:
            counts[sig.source] = counts.get(sig.source, 0) + 1

        if not raw:
            raise RadarError(
                "no signals collected from any source, check network access"
            )

        memory = self.load_memory() if use_memory else {}
        leads = [self.score(s, memory) for s in raw]

        if source_filter:
            leads = [lead for lead in leads if lead.signal.source.startswith(source_filter)]

        leads = [lead for lead in leads if lead.score >= min_score]
        leads.sort(key=lambda lead: (-lead.score, lead.title))

        # Diversify: a single source should not fill the whole digest, because
        # the point is to see different kinds of evidence side by side.
        leads = self._diversify(leads, limit)

        # Record what we surfaced so tomorrow's novelty score is meaningful.
        if use_memory:
            now_ts = datetime.now(timezone.utc).timestamp()
            for lead in leads:
                memory[lead.signal.title.lower()[:80]] = now_ts
            self.save_memory(memory)

        return Digest(
            leads=leads,
            generated_at=datetime.now(WIB),
            counts=counts,
            sources_ok=sorted(counts.keys()),
            sources_failed=[],
        )

    @staticmethod
    def _diversify(leads: list[Lead], limit: int) -> list[Lead]:
        """Cap each source at roughly its fair share, then fill from the rest."""
        if not leads or limit <= 0:
            return leads[:limit]

        source_count = len({lead.signal.source for lead in leads})
        if source_count <= 1:
            return leads[:limit]

        # Round robin by source keeps each one represented near the top.
        buckets: dict[str, list[Lead]] = {}
        for lead in leads:
            buckets.setdefault(lead.signal.source, []).append(lead)

        ordered: list[Lead] = []
        while len(ordered) < limit and any(buckets.values()):
            for source in sorted(buckets):
                if buckets[source] and len(ordered) < limit:
                    ordered.append(buckets[source].pop(0))

        # Anything not yet shown, still in score order, tops up the tail.
        picked = {id(lead) for lead in ordered}
        for lead in leads:
            if len(ordered) >= limit:
                break
            if id(lead) not in picked:
                ordered.append(lead)
        return ordered

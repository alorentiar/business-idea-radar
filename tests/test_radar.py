"""Tests for signal collection and lead scoring.

Network is never touched: the fetchers are exercised through a fake transport,
and the scoring functions are pure, so the whole suite runs offline.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from business_idea_radar.cli import main
from business_idea_radar.radar import Digest, Radar, ScoreWeights
from business_idea_radar.signals import Signal, Signals, _age_hours, _strip_html


def now_iso(hours_ago: float = 1.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat().replace("+00:00", "Z")


def sig(source="hn_ask", title="How do you automate invoice reconciliation?", **extra):
    """Build a test signal. `metric` and `age_hours` can be overridden."""
    metric = extra.pop("metric", 100.0)
    base_extra = {"age_hours": extra.pop("age_hours", 2.0)}
    base_extra.update(extra)
    return Signal(
        source=source,
        title=title,
        url="https://example.com/x",
        metric=float(metric),
        metric_label=f"{metric:g} comments",
        extra=base_extra,
    )


# -- helpers --------------------------------------------------------------


def test_strip_html_unescapes_entities():
    raw = "Ask HN: &quot;best&quot; tool &amp; why &#x27;it&#x27;s slow"
    out = _strip_html(raw)
    assert '"best"' in out
    assert "&" in out
    assert "'it's slow" in out


def test_age_hours_parses_and_rejects():
    assert _age_hours(None) is None
    assert _age_hours("garbage") is None
    age = _age_hours(now_iso(5))
    assert age is not None and 4.9 < age < 5.1


# -- scoring --------------------------------------------------------------


def make_radar(tmp_path: Path, **kw) -> Radar:
    return Radar(state_path=tmp_path / "mem.json", **kw)


def test_demand_scales_per_source(tmp_path):
    """Each source has its own saturation point, so scores stay comparable."""
    r = make_radar(tmp_path)
    # A 250 comment HN thread is about half of its 500 ceiling -> sqrt(0.5)
    hn_mid = r._demand(Signal("hn_ask", "t", "u", metric=250))
    assert abs(hn_mid - math.sqrt(0.5)) < 0.01
    # A 2500 star repo is half of its 5000 ceiling, so the same fraction
    gh_mid = r._demand(Signal("github_trending", "t", "u", metric=2500))
    assert abs(gh_mid - hn_mid) < 0.01
    # Beyond the ceiling it saturates rather than growing without bound
    assert r._demand(Signal("hn_ask", "t", "u", metric=10_000)) == 1.0


def test_demand_is_monotonic(tmp_path):
    r = make_radar(tmp_path)
    values = [r._demand(Signal("hn_ask", "t", "u", metric=m)) for m in (10, 50, 200, 500)]
    assert values == sorted(values)
    assert all(0 <= v <= 1.0 for v in values)


def test_demand_of_zero_is_zero(tmp_path):
    assert make_radar(tmp_path)._demand(Signal("x", "t", "u", metric=0)) == 0.0


def test_momentum_halves_every_three_days(tmp_path):
    r = make_radar(tmp_path)
    fresh = r._momentum(sig(age_hours=0))
    three_days = r._momentum(sig(age_hours=72))
    assert abs(fresh - 1.0) < 0.01
    assert abs(three_days - 0.5) < 0.01


def test_momentum_unknown_age_is_neutral(tmp_path):
    r = make_radar(tmp_path)
    s = Signal("x", "t", "u")
    s.extra.pop("age_hours", None)
    assert r._momentum(s) == 0.5


def test_specificity_rewards_concrete_titles(tmp_path):
    r = make_radar(tmp_path)
    concrete = r._specificity(sig(title="Looking for a self-hosted analytics API"))
    vague = r._specificity(sig(title="Random thoughts discussion"))
    assert concrete > vague
    assert 0.0 <= vague <= 1.0


def test_market_only_applies_to_jobs(tmp_path):
    r = make_radar(tmp_path)
    non_job = r._market(sig(source="hn_ask"))
    job_hit = r._market(
        sig(source="jobs_remote", tags=["ai", "automation", "data"])
    )
    assert non_job == 0.3
    assert job_hit > non_job


def test_novelty_uses_memory(tmp_path):
    r = make_radar(tmp_path)
    s = sig(title="Unique title here")
    assert r._novelty(s, {}) == 1.0
    assert r._novelty(s, {s.title.lower()[:80]: 1.0}) == 0.0


def test_score_breakdown_sums(tmp_path):
    r = make_radar(tmp_path)
    lead = r.score(sig())
    assert abs(sum(lead.breakdown.values()) - lead.score) < 0.01
    assert lead.score > 0


def test_score_produces_an_angle(tmp_path):
    r = make_radar(tmp_path)
    assert r.score(sig(source="hn_ask")).angle
    assert r.score(sig(source="github_trending", language="Go")).angle
    assert r.score(sig(source="jobs_remote", tags=["ai"])).angle


def test_weights_are_configurable(tmp_path):
    """Weights rescale the score, and demand saturates at its ceiling."""
    weights = ScoreWeights(
        demand=100.0, momentum=0.0, specificity=0.0, market=0.0, novelty=0.0
    )
    r = make_radar(tmp_path, weights=weights)
    # hn_ask saturates at 500 comments, so 500+ gives the full 100 points.
    assert r.score(sig(metric=500)).score == 100.0
    # Half the ceiling gives sqrt(0.5) of the weight, about 70.7
    partial = r.score(sig(metric=250)).score
    assert 65 < partial < 75
    assert partial < r.score(sig(metric=500)).score


# -- memory ---------------------------------------------------------------


def test_memory_roundtrip(tmp_path):
    r = make_radar(tmp_path)
    r.save_memory({"something": datetime.now(timezone.utc).timestamp()})
    assert "something" in r.load_memory()


def test_memory_drops_old_entries(tmp_path):
    r = make_radar(tmp_path, memory_days=1)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
    fresh = datetime.now(timezone.utc).timestamp()
    r.save_memory({"old": old, "fresh": fresh})
    memory = r.load_memory()
    assert "fresh" in memory
    assert "old" not in memory


def test_memory_missing_file_is_empty(tmp_path):
    assert make_radar(tmp_path).load_memory() == {}


def test_memory_corrupt_file_recovers(tmp_path):
    path = tmp_path / "mem.json"
    path.write_text("{broken", encoding="utf-8")
    r = Radar(state_path=path)
    assert r.load_memory() == {}


# -- digest rendering -----------------------------------------------------


def make_digest(leads) -> Digest:
    return Digest(
        leads=leads,
        generated_at=datetime.now(timezone.utc),
        counts={"hn_ask": 3},
        sources_ok=["hn_ask"],
    )


def test_digest_render_includes_lead(tmp_path):
    r = make_radar(tmp_path)
    lead = r.score(sig(title="Automate invoice reconciliation"))
    text = make_digest([lead]).render()
    assert "IDE BISNIS" in text
    assert "Automate invoice" in text
    assert "example.com" in text


def test_digest_render_empty(tmp_path):
    text = make_digest([]).render()
    assert "Tidak ada sinyal baru" in text


def test_digest_as_dict_is_json_safe(tmp_path):
    r = make_radar(tmp_path)
    payload = json.loads(json.dumps(make_digest([r.score(sig())]).as_dict()))
    assert payload["leads"][0]["score"] > 0
    assert payload["counts"]["hn_ask"] == 3


def test_digest_top_and_by_source(tmp_path):
    r = make_radar(tmp_path)
    leads = [r.score(sig(source="hn_ask")), r.score(sig(source="jobs_remote"))]
    digest = make_digest(leads)
    assert len(digest.top(1)) == 1
    assert len(digest.by_source("jobs_remote")) == 1


# -- collection with a fake transport -------------------------------------


class FakeSignals(Signals):
    def __init__(self, batches):
        super().__init__()
        self._batches = batches

    def collect_all(self):
        return [s for batch in self._batches for s in batch]


def test_collect_scores_and_records(tmp_path):
    fake = FakeSignals([[sig(title="How do you handle payroll API integration?"), sig()]])
    r = Radar(signals=fake, state_path=tmp_path / "m.json")
    digest = r.collect(min_score=0)
    assert len(digest.leads) == 2
    assert (tmp_path / "m.json").exists()


def test_collect_respects_min_score(tmp_path):
    fake = FakeSignals([[sig(title="x")]])
    r = Radar(signals=fake, state_path=tmp_path / "m.json")
    assert r.collect(min_score=99).leads == []


def test_collect_source_filter(tmp_path):
    fake = FakeSignals([[sig(source="hn_ask"), sig(source="jobs_remote", tags=["ai"])]])
    r = Radar(signals=fake, state_path=tmp_path / "m.json")
    digest = r.collect(min_score=0, source_filter="jobs")
    assert all(lead.signal.source == "jobs_remote" for lead in digest.leads)


def test_collect_raises_when_everything_empty(tmp_path):
    import pytest

    from business_idea_radar.radar import RadarError

    r = Radar(signals=FakeSignals([]), state_path=tmp_path / "m.json")
    with pytest.raises(RadarError):
        r.collect()


def test_collect_marks_old_signals_as_not_novel(tmp_path):
    fake = FakeSignals([[sig(title="Fixed title for memory test")]])
    r = Radar(signals=fake, state_path=tmp_path / "m.json")
    first = r.collect(min_score=0)
    second = r.collect(min_score=0)
    assert first.leads[0].breakdown["novelty"] > second.leads[0].breakdown["novelty"]


def test_diversify_interleaves_sources(tmp_path):
    """One source must not own the whole digest."""
    fake = FakeSignals([[
        sig(source="github_trending", title=f"repo {i}") for i in range(6)
    ] + [
        sig(source="hn_ask", title=f"question {i}") for i in range(6)
    ]])
    r = Radar(signals=fake, state_path=tmp_path / "m.json")
    digest = r.collect(min_score=0, limit=6)
    sources = {lead.signal.source for lead in digest.top(4)}
    assert len(sources) == 2, "top of the digest should mix sources"


def test_diversify_respects_limit(tmp_path):
    leads = [sig(source="a", title=f"a{i}") for i in range(5)]
    r = make_radar(tmp_path)
    scored = [r.score(s) for s in leads]
    # Only one source present, so the limit is the only constraint
    assert len(Radar._diversify(scored, 3)) == 3


# -- cli ------------------------------------------------------------------


def test_cli_forget_on_empty_memory(tmp_path, capsys):
    code = main(["--state", str(tmp_path / "m.json"), "forget"])
    assert code == 0
    assert "cleared 0" in capsys.readouterr().out


def test_cli_forget_removes_file(tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text("{}", encoding="utf-8")
    assert main(["--state", str(path), "forget"]) == 0
    assert not path.exists()

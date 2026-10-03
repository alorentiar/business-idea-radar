"""Offline tests for the data-source collectors in signals.py.

The network never touched: every collector funnels through ``_safe`` -> ``_get_json``,
so monkeypatching ``_get_json`` exercises each parser's happy path AND its defensive
fallback (a dead source returns [] rather than raising).
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from business_idea_radar.signals import Signals


def _iso(hours: float = 1.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def sigs() -> Signals:
    return Signals(timeout=1)


def _patch_get_json(monkeypatch, payload):
    monkeypatch.setattr("business_idea_radar.signals._get_json", lambda *a, **k: payload)


def _patch_get_json_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr("business_idea_radar.signals._get_json", boom)


# -- Hacker News ----------------------------------------------------------


def test_ask_hn_parses_hits(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {
        "hits": [
            {
                "title": "Ask HN: How do you automate invoice reconciliation?",
                "objectID": "98765",
                "num_comments": 40,
                "points": 85,
                "created_at": _iso(2),
            }
        ]
    })
    out = sigs.ask_hn()
    assert len(out) == 1
    s = out[0]
    assert s.source == "hn_ask"
    assert s.metric == 40.0
    assert "98765" in s.url
    assert s.extra["points"] == 85
    assert s.extra["age_hours"] is not None


def test_ask_hn_skips_empty_titles(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {
        "hits": [{"title": "", "objectID": "1"}, {"title": None, "objectID": "2"}]
    })
    assert sigs.ask_hn() == []


def test_show_hn_uses_url_or_objectid(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {
        "hits": [
            {"title": "Show HN: A thing", "url": "https://a.b/c", "objectID": "x1",
             "points": 120, "num_comments": 10, "created_at": _iso()},
            {"title": "Show HN: No url", "objectID": "disc123",
             "points": 60, "num_comments": 2, "created_at": _iso()},
        ]
    })
    out = sigs.show_hn()
    assert out[0].url == "https://a.b/c"
    assert "disc123" in out[1].url
    assert out[0].metric == 120.0


# -- job boards -----------------------------------------------------------


def test_remote_jobs_skips_legal_notice(sigs, monkeypatch):
    _patch_get_json(monkeypatch, [
        {"position": "latest jobs", "company": "RemoteOK"},  # entry 0 = legal notice
        {"position": "Backend Engineer", "company": "Acme",
         "tags": ["python", "aws", "ai"], "url": "https://apply/acme"},
    ])
    out = sigs.remote_jobs()
    assert len(out) == 1
    assert out[0].title == "Backend Engineer @ Acme"
    assert out[0].metric == 3.0  # 3 tags
    assert "ai" in out[0].metric_label


def test_remote_jobs_non_list_returns_empty(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {"data": []})  # wrong shape (dict not list)
    assert sigs.remote_jobs() == []


def test_arbeitnow_parses(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {
        "data": [
            {"title": "Data Analyst", "company_name": "Corp",
             "tags": ["data", "analytics"], "url": "https://job/1"},
            {"title": "", "company_name": "NoTitle"},
        ]
    })
    out = sigs.arbeitnow_jobs()
    assert len(out) == 1
    assert out[0].title == "Data Analyst @ Corp"
    assert out[0].source == "jobs_board"


def test_arbeitnow_non_dict_returns_empty(sigs, monkeypatch):
    _patch_get_json(monkeypatch, [])
    assert sigs.arbeitnow_jobs() == []


# -- GitHub ---------------------------------------------------------------


def test_github_trending_parses_items(sigs, monkeypatch):
    _patch_get_json(monkeypatch, {
        "items": [
            {"full_name": "a/b", "description": "A nice <b>tool</b>",
             "stargazers_count": 1234, "html_url": "https://github.com/a/b",
             "language": "Go", "topics": ["cli"], "forks_count": 42},
        ]
    })
    out = sigs.github_trending()
    assert len(out) == 1
    s = out[0]
    assert s.source == "github_trending"
    assert s.metric == 1234.0
    assert s.extra["language"] == "Go"
    assert s.extra["forks"] == 42


def test_github_trending_non_dict_returns_empty(sigs, monkeypatch):
    _patch_get_json(monkeypatch, [])
    assert sigs.github_trending() == []


# -- defensive behaviour --------------------------------------------------


@pytest.mark.parametrize("fn", [
    lambda s: s.ask_hn(),
    lambda s: s.show_hn(),
    lambda s: s.remote_jobs(),
    lambda s: s.arbeitnow_jobs(),
    lambda s: s.github_trending(),
])
def test_all_collectors_survive_network_error(sigs, monkeypatch, fn):
    """A dead source must yield [] — never an exception."""
    _patch_get_json_error(monkeypatch)
    assert fn(sigs) == []


def test_all_collectors_survive_bad_json(sigs, monkeypatch):
    # ValueError from a non-dict payload shape: ask_hn does .get -> AttributeError.
    _patch_get_json(monkeypatch, "not-json")
    assert sigs.ask_hn() == []

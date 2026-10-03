"""Tests for the Markdown report export and sources --json CLI paths."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from business_idea_radar.cli import main
from business_idea_radar.radar import Digest, Radar
from business_idea_radar.signals import Signal


def _mk_lead(source="hn_ask", title="Automate invoice reconciliation"):
    sig = Signal(
        source=source,
        title=title,
        url="https://example.com/x",
        metric=40.0,
        metric_label="40 comments",
        extra={"tags": ["ai", "data"], "age_hours": 2.0},
    )
    return Radar(state_path="/tmp/never-used.json").score(sig)


def _digest(leads) -> Digest:
    return Digest(
        leads=leads,
        generated_at=datetime.now(timezone.utc),
        counts={"hn_ask": 1},
        sources_ok=["hn_ask"],
    )


# -- render_markdown ------------------------------------------------------


def test_render_markdown_includes_lead():
    text = _digest([_mk_lead()]).render_markdown()
    assert text.startswith("# ")
    assert "IDE BISNIS" in text
    assert "## 1." in text
    assert "example.com" in text
    assert "Skor" in text


def test_render_markdown_empty_reports_no_leads():
    text = _digest([]).render_markdown()
    assert "Tidak ada sinyal baru" in text


def test_render_markdown_reports_failed_sources():
    digest = Digest(
        leads=[],
        generated_at=datetime.now(timezone.utc),
        counts={},
        sources_ok=[],
        sources_failed=["jobs_remote"],
    )
    assert "Sumber gagal" in digest.render_markdown()


# -- CLI --out ------------------------------------------------------------


class _FakeRadar:
    def __init__(self, digest):
        self._digest = digest

    def collect(self, **kwargs):  # test double
        return self._digest


def test_cli_scan_out_writes_markdown(tmp_path, monkeypatch):
    out = tmp_path / "leads" / "digest.md"
    monkeypatch.setattr(
        "business_idea_radar.cli.Radar",
        lambda **kw: _FakeRadar(_digest([_mk_lead()])),
    )
    code = main(["--state", str(tmp_path / "m.json"), "scan", "--out", str(out)])
    assert code == 0
    content = out.read_text(encoding="utf-8")
    assert "IDE BISNIS" in content
    assert "example.com" in content


def test_cli_scan_out_creates_nested_dirs(tmp_path, monkeypatch):
    out = tmp_path / "a" / "b" / "c" / "report.md"
    monkeypatch.setattr(
        "business_idea_radar.cli.Radar",
        lambda **kw: _FakeRadar(_digest([])),
    )
    # Empty digest -> EXIT_NOTHING (4), but the report is still written.
    assert main(["--state", str(tmp_path / "m.json"), "scan", "--out", str(out)]) == 4
    assert "Tidak ada sinyal baru" in out.read_text(encoding="utf-8")


# -- CLI sources --json ---------------------------------------------------


class _AllEmptySignals:
    def ask_hn(self): return []
    def show_hn(self): return []
    def remote_jobs(self): return []
    def arbeitnow_jobs(self): return []
    def github_trending(self): return []


def test_sources_json_emits_parsable_json(capsys, monkeypatch):
    monkeypatch.setattr("business_idea_radar.cli.Signals", _AllEmptySignals)
    code = main(["sources", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1  # no source returned data -> EXIT_ERROR
    assert payload["total"] == 5
    assert payload["healthy"] == 0
    assert all(s["status"] in ("ok", "empty") for s in payload["sources"])


def test_sources_json_fails_when_all_empty(capsys, monkeypatch):
    monkeypatch.setattr("business_idea_radar.cli.Signals", _AllEmptySignals)
    assert main(["sources", "--json"]) == 1


def test_sources_json_ok_when_any_returns_data(capsys, monkeypatch):
    class _OneHealthy(_AllEmptySignals):
        def ask_hn(self): return [_mk_lead(source="hn_ask")]

    monkeypatch.setattr("business_idea_radar.cli.Signals", _OneHealthy)
    assert main(["sources", "--json"]) == 0


def test_sources_failure_marks_failed(capsys, monkeypatch):
    class _Sigs:
        def ask_hn(self):
            raise RuntimeError("boom")
        def show_hn(self): return []
        def remote_jobs(self): return []
        def arbeitnow_jobs(self): return []
        def github_trending(self): return []

    monkeypatch.setattr("business_idea_radar.cli.Signals", _Sigs)
    code = main(["sources", "--json"])
    payload = json.loads(capsys.readouterr().out)
    failed = payload["sources"][0]
    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError"
    assert code == 1  # all four remaining sources returned no data

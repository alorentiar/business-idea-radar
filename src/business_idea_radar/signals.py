"""Fetch raw market signals from free, key-less public APIs.

Four sources, each answering a different question:

  * Hacker News "Ask HN"  - what are people struggling with right now?
  * Hacker News "Show HN" - what is being built, and does it get traction?
  * Remote job boards     - what are companies paying for?
  * GitHub trending       - where is developer attention moving?

Every fetcher is defensive: a source that is down returns an empty list rather
than breaking the run, because a digest from three sources is still useful.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UA = {
    "User-Agent": "business-idea-radar/0.1 (+https://github.com/alorentiar/business-idea-radar)",
    "Accept": "application/json",
}

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
REMOTEOK = "https://remoteok.com/api"
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
GITHUB_SEARCH = "https://api.github.com/search/repositories"


@dataclass
class Signal:
    """One observation from one source."""

    source: str
    title: str
    url: str
    metric: float = 0.0
    metric_label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "metric": self.metric,
            "metric_label": self.metric_label,
            "extra": self.extra,
        }


def _get_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _safe(url: str, timeout: int = 25) -> Any:
    try:
        return _get_json(url, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&#x27;", "'").replace("&quot;", '"').replace("&amp;", "&")
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&#x2F;", "/")
    return re.sub(r"\s+", " ", text).strip()


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.0)
    except (ValueError, TypeError):
        return None


class Signals:
    """Collectors for each data source."""

    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout

    # -- Hacker News -------------------------------------------------------

    def ask_hn(self, query: str = "idea", limit: int = 20) -> list[Signal]:
        """Questions people are asking. High comment counts mean real pain."""
        url = (
            f"{HN_SEARCH}?tags=ask_hn&query={query}"
            f"&hitsPerPage={limit}&numericFilters=points>20"
        )
        data = _safe(url, self.timeout)
        out: list[Signal] = []
        for hit in ((data or {}).get("hits") or []):
            title = _strip_html(hit.get("title") or "")
            if not title:
                continue
            comments = int(hit.get("num_comments") or 0)
            points = int(hit.get("points") or 0)
            out.append(
                Signal(
                    source="hn_ask",
                    title=title,
                    url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    metric=float(comments),
                    metric_label=f"{comments} comments, {points} points",
                    extra={
                        "points": points,
                        "comments": comments,
                        "age_hours": _age_hours(hit.get("created_at")),
                    },
                )
            )
        return out

    def show_hn(self, limit: int = 20) -> list[Signal]:
        """Things people just built. Traction here hints at a live market."""
        url = f"{HN_SEARCH}?tags=show_hn&hitsPerPage={limit}&numericFilters=points>50"
        data = _safe(url, self.timeout)
        out: list[Signal] = []
        for hit in ((data or {}).get("hits") or []):
            title = _strip_html(hit.get("title") or "")
            if not title:
                continue
            points = int(hit.get("points") or 0)
            comments = int(hit.get("num_comments") or 0)
            out.append(
                Signal(
                    source="hn_show",
                    title=title,
                    url=hit.get("url")
                    or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    metric=float(points),
                    metric_label=f"{points} points, {comments} comments",
                    extra={"comments": comments, "age_hours": _age_hours(hit.get("created_at"))},
                )
            )
        return out

    # -- job boards --------------------------------------------------------

    def remote_jobs(self, limit: int = 60) -> list[Signal]:
        """Remote postings. Repeated skills across postings mean demand."""
        data = _safe(REMOTEOK, self.timeout)
        if not isinstance(data, list):
            return []
        out: list[Signal] = []
        for job in data[1 : limit + 1]:  # entry 0 is a legal notice
            position = str(job.get("position") or "").strip()
            if not position:
                continue
            company = str(job.get("company") or "").strip()
            tags = [str(t) for t in (job.get("tags") or []) if t]
            out.append(
                Signal(
                    source="jobs_remote",
                    title=f"{position} @ {company}" if company else position,
                    url=str(job.get("url") or job.get("apply_url") or REMOTEOK),
                    metric=float(len(tags)),
                    metric_label=", ".join(tags[:6]) or "no tags",
                    extra={"tags": tags, "company": company, "location": job.get("location")},
                )
            )
        return out

    def arbeitnow_jobs(self, limit: int = 60) -> list[Signal]:
        """Broader job board, useful for non-remote signals."""
        data = _safe(ARBEITNOW, self.timeout)
        if not isinstance(data, dict):
            return []
        out: list[Signal] = []
        for job in (data.get("data") or [])[:limit]:
            title = str(job.get("title") or "").strip()
            if not title:
                continue
            company = str(job.get("company_name") or "").strip()
            tags = [str(t) for t in (job.get("tags") or []) if t]
            out.append(
                Signal(
                    source="jobs_board",
                    title=f"{title} @ {company}" if company else title,
                    url=str(job.get("url") or ARBEITNOW),
                    metric=float(len(tags)),
                    metric_label=", ".join(tags[:6]) or "no tags",
                    extra={"tags": tags, "company": company},
                )
            )
        return out

    # -- GitHub ------------------------------------------------------------

    def github_trending(self, days: int = 30, min_stars: int = 200, limit: int = 25) -> list[Signal]:
        """Repositories that gained traction recently. Where attention is going."""
        since = (datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        # created after N days ago, plenty of stars already
        from datetime import timedelta

        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        url = (
            f"{GITHUB_SEARCH}?q=created:>{since}+stars:>{min_stars}"
            f"&sort=stars&order=desc&per_page={limit}"
        )
        data = _safe(url, self.timeout)
        if not isinstance(data, dict):
            return []
        out: list[Signal] = []
        for repo in data.get("items") or []:
            name = str(repo.get("full_name") or "").strip()
            if not name:
                continue
            stars = int(repo.get("stargazers_count") or 0)
            out.append(
                Signal(
                    source="github_trending",
                    title=f"{name} — {_strip_html(repo.get('description') or '')}",
                    url=str(repo.get("html_url") or ""),
                    metric=float(stars),
                    metric_label=f"{stars} stars",
                    extra={
                        "language": repo.get("language"),
                        "topics": repo.get("topics") or [],
                        "forks": repo.get("forks_count"),
                    },
                )
            )
        return out

    # -- combined ----------------------------------------------------------

    def collect_all(self) -> list[Signal]:
        """Every source, deduplicated by title."""
        seen: set[str] = set()
        out: list[Signal] = []
        for batch in (
            self.ask_hn(),
            self.show_hn(),
            self.remote_jobs(),
            self.arbeitnow_jobs(),
            self.github_trending(),
        ):
            for sig in batch:
                key = sig.title.lower()[:80]
                if key in seen:
                    continue
                seen.add(key)
                out.append(sig)
        return out

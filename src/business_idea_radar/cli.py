"""Command line interface for business-idea-radar."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .radar import DEFAULT_STATE, Radar, RadarError
from .signals import Signals

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING = 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="business-idea-radar",
        description=(
            "Collect market signals from public data and rank them as business "
            "leads. No API keys, no LLM."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--state",
        metavar="PATH",
        help=f"memory file (default: {DEFAULT_STATE})",
    )

    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="collect signals and print the digest")
    scan.add_argument("-n", "--limit", type=int, default=20, help="max leads to keep")
    scan.add_argument("--show", type=int, default=6, help="how many to print")
    scan.add_argument(
        "--min-score", type=float, default=25.0, help="hide leads below this"
    )
    scan.add_argument(
        "--source",
        help="only this source (hn_ask, hn_show, jobs_remote, jobs_board, github_trending)",
    )
    scan.add_argument("--json", action="store_true", help="emit JSON")
    scan.add_argument(
        "--explain", action="store_true", help="show the score breakdown"
    )
    scan.add_argument(
        "--fresh", action="store_true", help="ignore memory, treat everything as new"
    )
    scan.add_argument(
        "--no-memory", action="store_true", help="do not record what was shown"
    )

    sub.add_parser("sources", help="probe each source and report health")

    sub.add_parser("forget", help="clear the memory of previously seen signals")

    return p


def cmd_scan(args: argparse.Namespace) -> int:
    radar = Radar(state_path=args.state)
    try:
        digest = radar.collect(
            limit=args.limit,
            min_score=args.min_score,
            use_memory=not args.fresh,
            source_filter=args.source,
        )
    except RadarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(digest.as_dict(), indent=2))
        return EXIT_OK

    if not digest.leads:
        print("no leads above the score threshold right now")
        return EXIT_NOTHING

    print(digest.render(limit=args.show))
    if args.explain:
        print()
        print("score breakdown")
        for lead in digest.top(args.show):
            bits = ", ".join(f"{k}={v}" for k, v in sorted(lead.breakdown.items()))
            print(f"  {lead.score:5.1f}  {lead.title[:60]}")
            print(f"         {bits}")
    return EXIT_OK


def cmd_sources(args: argparse.Namespace) -> int:
    sig = Signals()
    probes = [
        ("hn_ask", sig.ask_hn, "Ask HN"),
        ("hn_show", sig.show_hn, "Show HN"),
        ("jobs_remote", sig.remote_jobs, "RemoteOK"),
        ("jobs_board", sig.arbeitnow_jobs, "ArbeitNow"),
        ("github_trending", sig.github_trending, "GitHub trending"),
    ]
    healthy = 0
    for _key, fn, label in probes:
        try:
            items = fn()
            count = len(items)
        except Exception as exc:  # noqa: BLE001 - report, never crash a probe
            print(f"  {label:20} FAILED ({type(exc).__name__})")
            continue
        state = "ok" if count else "empty"
        if count:
            healthy += 1
        print(f"  {label:20} {state} ({count} signals)")
    print()
    print(f"{healthy}/{len(probes)} sources returned data")
    return EXIT_OK if healthy else EXIT_ERROR


def cmd_forget(args: argparse.Namespace) -> int:
    radar = Radar(state_path=args.state)
    memory = radar.load_memory()
    count = len(memory)
    try:
        if radar.state_path.exists():
            radar.state_path.unlink()
    except OSError as exc:
        print(f"could not remove memory: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"cleared {count} remembered signal(s)")
    return EXIT_OK


COMMANDS = {"scan": cmd_scan, "sources": cmd_sources, "forget": cmd_forget}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

# business-idea-radar

Collect market signals from public data and rank them as **business leads**.

Most "idea generators" ask a language model to invent something. This does the
opposite: it watches four public sources where demand is already visible, scores
each signal on observable evidence, and prints the ones worth a second look.
There is no LLM anywhere in the pipeline, so the output is reproducible and
costs nothing to run.

```
$ business-idea-radar scan

💡 IDE BISNIS — SINYAL PASAR
🕐 23 Sep 2026 01:04 WIB

📊 184 sinyal dari 5 sumber → 12 leads

1. How do you handle invoice reconciliation across multiple banks?
   Skor 78/100 · 145 comments, 320 points
   💡 Orang sedang bertanya soal ini. Cek keluhannya di komentar.
   🔗 https://news.ycombinator.com/item?id=123
```

## Where the signals come from

| Source | Question it answers | Free? |
| --- | --- | --- |
| **Ask HN** | What are people stuck on right now? | yes, no key |
| **Show HN** | What is being built, and does it get traction? | yes, no key |
| **RemoteOK** | What are companies paying remote workers for? | yes, no key |
| **ArbeitNow** | Broader hiring demand, not only remote | yes, no key |
| **GitHub trending** | Where is developer attention moving? | yes, no key |

All five are key-less public endpoints, which is why the tool can run on a
schedule without secrets management.

## How scoring works

Every signal is scored 0-100 from five observable facts:

| Signal | Weight | What it measures |
| --- | --- | --- |
| **Demand** | 35 | Discussion volume, log scaled so one viral post cannot dominate |
| **Momentum** | 25 | Recency, halving every three days |
| **Specificity** | 20 | Concrete and pain-point language versus vague phrasing |
| **Market** | 12 | Whether employers are hiring for the same skills |
| **Novelty** | 8 | Not already surfaced in the last seven days |

A signal seen yesterday scores lower today, so the digest stays fresh without
anyone tuning it. Weights live in one dataclass and can be overridden.

## Requirements

- Python 3.9 or newer
- Network access to the five public APIs (no credentials)

## Install

From PyPI:

```bash
pip install business-idea-radar
```

From a clone, if you want to work on it:

```bash
git clone https://github.com/alorentiar/business-idea-radar
cd business-idea-radar
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Without installing at all:

```bash
python -m business_idea_radar scan
```

## Usage

```bash
# the daily digest
business-idea-radar scan

# see why things ranked where they did
business-idea-radar scan --explain

# only job market signals
business-idea-radar scan --source jobs

# only developer attention
business-idea-radar scan --source github

# treat everything as new, ignoring the seven day memory
business-idea-radar scan --fresh

# machine readable
business-idea-radar scan --json | jq '.leads[0]'

# check whether each source is reachable
business-idea-radar sources

# clear the memory of previously seen signals
business-idea-radar forget
```

Useful flags for `scan`:

| Flag | Purpose |
| --- | --- |
| `-n, --limit` | max leads to keep (default 20) |
| `--show` | how many to print (default 6) |
| `--min-score` | hide leads below this score (default 25) |
| `--source` | filter to one source prefix |
| `--json` | emit JSON |
| `--explain` | print the score breakdown |

Exit codes: `0` success, `1` error, `4` no leads above the threshold. The last
one lets a cron job stay silent on a quiet day.

## Running on a schedule

The tool is designed for cron. Combined with the `4` exit code, a wrapper can
notify only when something interesting shows up:

```bash
hourly_scan() {
  out=$(business-idea-radar scan --show 5)
  [ -n "$out" ] && send_to_chat "$out"
}
```

The memory file lives at `~/.hermes/scripts/business_radar_state.json` by
default, and can be moved with `--state` or the `HERMES_HOME` environment
variable. Old entries are pruned on every save.

## Library use

The scoring is importable and pure, so it is easy to test or extend:

```python
from business_idea_radar import Radar, Signals

radar = Radar(Signals())
digest = radar.collect(min_score=40)
for lead in digest.top(5):
    print(lead.score, lead.title)
    print("   ", lead.angle)
    print("   ", lead.breakdown)
```

Add a source by implementing one method that returns `Signal` objects and
registering it in `Signals.collect_all()`. The scoring does not need to change.

## Development

```bash
pip install -e ".[dev]"
pytest          # ~31 tests, no network access needed
ruff check .
```

Tests fake the transport, so the suite runs offline in under a second.

## Limitations

- Scoring is heuristic. It ranks *observable evidence*, not certainty.
- Job board tag quality varies, so the market signal is the weakest of the five.
- Ask HN results depend on Hacker News activity; a quiet week gives fewer leads.
- Repositories on GitHub can trend for reasons unrelated to a business need.

## License

MIT. See [LICENSE](LICENSE).

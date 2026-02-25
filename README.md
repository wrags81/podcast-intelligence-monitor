# Podcast Intelligence Monitor

Tracks 150 political and news podcasts across the ideological spectrum, analyzes episode content via the Claude AI API, and generates daily intelligence digests and an interactive dashboard.

## What It Does

- **Fetches** new episodes daily from RSS feeds across 150 podcasts (left, right, neutral)
- **Retrieves transcripts** from YouTube where available — enabling analysis of actual spoken content rather than show descriptions
- **Analyzes** each episode with Claude AI: summarizes content, extracts quotes, identifies political attacks, narrative themes, and messaging opportunities
- **Rates threat level** (low/medium/high) for each episode based on potential political impact
- **Generates** a daily HTML digest and serves an interactive web dashboard with charts and feeds

## Podcast Coverage

| Lean | Count | Examples |
|------|-------|---------|
| Left / Progressive | 50 | Pod Save America, The Daily (NYT), Ezra Klein Show, Majority Report, Democracy Now! |
| Right / Conservative | ~30–50 | Ben Shapiro, Tucker Carlson, Dan Bongino, Mark Levin, Charlie Kirk, Megyn Kelly, War Room |
| Neutral / Center | 50 | NPR Politics, The Dispatch, Bulwark, Lawfare, Pivot, Honestly (Bari Weiss) |

## Setup

### Requirements

```bash
pip install anthropic requests yt-dlp youtube-transcript-api
```

### Configuration

```bash
cp config.example.json config.json
# Edit config.json with your SMTP settings and recipient list (optional — for email delivery)
```

### API Key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### Run the full pipeline (fetch → analyze → digest)

```bash
python monitor.py run-all
```

### Individual steps

```bash
# Fetch new episodes from all RSS feeds (last 48 hours)
python monitor.py fetch --since-hours 48

# Analyze fetched episodes via Claude
python monitor.py analyze --max-episodes 150

# Generate digest
python monitor.py digest
```

### Dashboard

```bash
python dashboard.py
# Visit http://localhost:8080      — main dashboard
# Visit http://localhost:8080/right — right-wing only view
```

### Inspect podcast list

```bash
python monitor.py list-podcasts
```

## Daily Scheduling (cron)

```cron
# Run full pipeline daily at 6:30 AM ET
30 6 * * * cd /path/to/podcast-monitor && ANTHROPIC_API_KEY=sk-ant-... python monitor.py run-all
```

## Architecture

```
monitor.py          — Main pipeline: fetch RSS → analyze via Claude → generate digest
dashboard.py        — Local web dashboard with charts and trend visualization
data/podcasts.json  — Master list of 150 podcasts with RSS feeds and YouTube channel IDs
data/episodes.db    — SQLite database of all episodes and analyses (gitignored)
output/             — Generated HTML/text digests (gitignored)
```

## Transcript Access

The monitor uses two content sources, in priority order:

1. **YouTube transcripts** — For shows with known YouTube channels, fetches auto-generated captions via `youtube-transcript-api`. Yields 50,000–100,000 words of real spoken content per episode.
2. **RSS descriptions** — Fallback for shows without YouTube channels. Typically 200–500 words of producer-written summary.

Adding more YouTube channel IDs to `data/podcasts.json` improves analysis quality significantly.

### Taddy API (optional)

For shows not on YouTube, [Taddy](https://taddy.org) provides podcast transcripts (~$50–150/mo for full coverage). Add your key to `config.json` under `transcript_apis.taddy` — the `try_fetch_transcript()` function in `monitor.py` is ready for integration.

## Analysis Fields

Each episode is analyzed for:

| Field | Description |
|-------|-------------|
| `synopsis` | 2–3 sentence summary written for a political staffer |
| `key_topics` | Main issues discussed |
| `notable_quotes` | Direct quotes flagged for political significance, with speaker and context |
| `political_attacks` | Specific attacks on Democrats, progressives, or named individuals |
| `narrative_themes` | Overarching frames being pushed |
| `threat_level` | `low` / `medium` / `high` — impact on progressive political interests |
| `threat_rationale` | One-sentence explanation of the rating |

## Dashboard Panels

**Main dashboard (`/`):**
- Episode volume by lean over 14 days
- Trending topics across all podcasts (7 days)
- Threat level distribution
- Right-wing attacks feed (72h)
- Messaging opportunities feed (72h)

**Right-wing dashboard (`/right`):**
- Per-show episode and threat counts
- Top topics in right-wing media
- Clippable quotes from transcripts
- Full attack feed
- Complete episode rundown

## Adding More Sources

### Substack

Substack publications have RSS feeds at `https://[publication].substack.com/feed`. Add them to `data/podcasts.json` in the same format as existing entries — the fetch logic handles Substack identically to podcasts.

### New Podcasts

Edit `data/podcasts.json`. Each entry needs at minimum:
```json
{
  "name": "Show Name",
  "host": "Host Name",
  "rss": "https://feeds.example.com/show.rss"
}
```

Optionally add `"youtube_channel_id"` for transcript access.

## Methodology

See [`output/methodology.md`](output/methodology.md) for a full explanation of how threat levels are defined, what each dashboard panel shows, and the limitations of the system.

## License

MIT

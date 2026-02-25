#!/usr/bin/env python3
"""
Podcast Intelligence Monitor
Fetches recent podcast episodes, retrieves transcripts where available,
analyzes content via Claude API, and generates daily email digests.
"""

import json
import os
import sys
import time
import hashlib
import sqlite3
import smtplib
import argparse
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

import requests
import anthropic
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"

for d in [DATA_DIR, OUTPUT_DIR, LOG_DIR]:
    d.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "episodes.db"
PODCASTS_FILE = DATA_DIR / "podcasts.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Database ──────────────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            podcast_name TEXT,
            lean TEXT,
            title TEXT,
            published TEXT,
            description TEXT,
            audio_url TEXT,
            transcript TEXT,
            analysis TEXT,
            fetched_at TEXT,
            digest_included INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS digests (
            id TEXT PRIMARY KEY,
            date TEXT,
            content_html TEXT,
            content_text TEXT,
            created_at TEXT
        )
    """)
    conn.commit()

def episode_id(podcast_name: str, title: str, published: str) -> str:
    key = f"{podcast_name}|{title}|{published}"
    return hashlib.md5(key.encode()).hexdigest()

# ── RSS Fetching ───────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Podcast-Intelligence-Monitor/1.0"
}

def parse_rss_date(date_str: Optional[str]) -> Optional[datetime]:
    """Try multiple date formats used in podcast RSS."""
    if not date_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

def fetch_rss_episodes(podcast: dict, since_hours: int = 48) -> list[dict]:
    """Fetch episodes from RSS published within the last N hours."""
    rss_url = podcast.get("rss")
    if not rss_url:
        return []

    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"RSS fetch failed for {podcast['name']}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    episodes = []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log.warning(f"RSS parse failed for {podcast['name']}: {e}")
        return []

    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        pubdate_el = item.find("pubDate")
        enclosure_el = item.find("enclosure")
        summary_el = item.find("itunes:summary", ns)

        title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled"
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        if not desc and summary_el is not None:
            desc = (summary_el.text or "").strip()

        pub_str = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""
        pub_dt = parse_rss_date(pub_str)

        audio_url = ""
        if enclosure_el is not None:
            audio_url = enclosure_el.get("url", "")

        if pub_dt and pub_dt < cutoff:
            continue

        episodes.append({
            "id": episode_id(podcast["name"], title, pub_str),
            "podcast_name": podcast["name"],
            "host": podcast.get("host", ""),
            "lean": podcast.get("lean", "unknown"),
            "title": title,
            "published": pub_str,
            "description": desc[:2000],
            "audio_url": audio_url,
        })

    return episodes

# ── Transcript Fetching ────────────────────────────────────────────────────────
def _find_youtube_video_id(channel_id: str, episode_title: str) -> Optional[str]:
    """Find the most recent YouTube video on a channel matching the episode title."""
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "playlist_items": "1:10",  # check last 10 videos
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            entries = info.get("entries", [])
            if not entries:
                return None
            # Try to find a title match; fall back to most recent video
            title_lower = episode_title.lower()
            for entry in entries:
                video_title = (entry.get("title") or "").lower()
                # Match if significant words overlap
                words = [w for w in title_lower.split() if len(w) > 4]
                if words and sum(1 for w in words if w in video_title) >= max(1, len(words) // 2):
                    return entry["id"]
            # Fall back to latest video
            return entries[0]["id"]
    except Exception as e:
        log.debug(f"YouTube video lookup failed for channel {channel_id}: {e}")
        return None


def try_fetch_transcript(podcast: dict, episode_title: str) -> Optional[str]:
    """Fetch transcript from YouTube if channel ID is configured, else return None."""
    channel_id = podcast.get("youtube_channel_id")
    if not channel_id:
        return None

    log.info(f"Fetching YouTube transcript for '{episode_title}' ({podcast['name']})")
    video_id = _find_youtube_video_id(channel_id, episode_title)
    if not video_id:
        log.debug(f"No YouTube video found for '{episode_title}'")
        return None

    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        text = " ".join([t.text for t in transcript])
        log.info(f"Got YouTube transcript: {len(text)} chars for '{episode_title}'")
        return text[:30000]  # cap at 30k chars — plenty for analysis
    except Exception as e:
        log.debug(f"YouTube transcript fetch failed for video {video_id}: {e}")
        return None

# ── Claude Analysis (per episode) ─────────────────────────────────────────────
ANALYSIS_PROMPT = """You are a senior political analyst.
Analyze this podcast episode and produce a structured intelligence report.

Podcast: {podcast_name}
Lean: {lean}
Host(s): {host}
Episode Title: {title}
Published: {published}

Description/Summary/Transcript:
{content}

Produce a JSON object with EXACTLY these keys — no extras:
{{
  "synopsis": "2-3 sentence plain-English summary of what this episode covers and argues. Written for a busy political staffer. Include the podcast name and host by name.",
  "key_topics": ["topic1", "topic2", "topic3"],
  "notable_quotes": [
    {{
      "quote": "Exact or near-exact quote, or a direct paraphrase presented as the speaker's actual words",
      "speaker": "Name or role of who said it",
      "context": "1 sentence on why this is politically significant",
      "type": "attack|claim|admission|notable_position|cross_partisan_signal"
    }}
  ],
  "political_attacks": ["Specific attacks on Democrats, progressives, specific figures — use their exact framing"],
  "narrative_themes": ["overarching frames being pushed, e.g. government waste, parental rights, elite capture"],
  "threat_level": "low|medium|high",
  "threat_rationale": "One sentence on why this rates that threat level for progressive causes"
}}

For notable_quotes: extract 1-3 of the most politically significant moments — the kind a rapid response team would clip or screenshot. If working from a description rather than full transcript, reconstruct the implied position as a clearly attributed paraphrase.

Be specific. Name names. Capture exact framing. Skip generic filler."""


def analyze_episode(client: anthropic.Anthropic, episode: dict) -> Optional[dict]:
    """Send episode data to Claude for analysis."""
    content = episode.get("transcript") or episode.get("description") or ""
    if len(content) < 50:
        log.info(f"Skipping analysis for '{episode['title']}' — insufficient content")
        return None

    prompt = ANALYSIS_PROMPT.format(
        podcast_name=episode["podcast_name"],
        lean=episode["lean"],
        host=episode.get("host", "unknown"),
        title=episode["title"],
        published=episode["published"],
        content=content[:20000],
    )

    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse failed for '{episode['title']}': {e}")
        return None
    except Exception as e:
        log.error(f"Claude API error for '{episode['title']}': {e}")
        return None


# ── Digest Generation ──────────────────────────────────────────────────────────

META_SUMMARY_PROMPT = """You are a senior political intelligence analyst.

Today is {date}. Below are summaries of every podcast episode released today across right-wing, neutral/center, and left/progressive shows.

EPISODE DATA:
{analyses}

Write THREE sections, each containing exactly 3 bullet points. Each bullet should be one crisp, specific sentence — the single most important thing happening in that lane today. Think: what would a comms director need to know in 90 seconds?

Format exactly like this — no other text before or after:

RIGHT-WING TODAY
• [bullet 1]
• [bullet 2]
• [bullet 3]

CENTER/NEUTRAL TODAY
• [bullet 1]
• [bullet 2]
• [bullet 3]

LEFT/PROGRESSIVE TODAY
• [bullet 1]
• [bullet 2]
• [bullet 3]

Rules:
- Each bullet = one specific claim, frame, or trend. No vague summaries.
- Name specific shows or hosts when it adds weight.
- If a lean had no episodes today, write "• No new episodes tracked today." for all three bullets in that section.
- Do not add headers, explanations, or anything outside the exact format above."""


def build_meta_summary(client: anthropic.Anthropic, analyses_text: str, date_str: str) -> str:
    # Split by lean so all three are represented even if total text is large
    sections = {"right": [], "neutral": [], "left": []}
    current_lean = None
    for line in analyses_text.split("\n"):
        for lean in sections:
            if line.startswith(f"[{lean.upper()}]"):
                current_lean = lean
                break
        if current_lean:
            sections[current_lean].append(line)
    # Cap each lean at ~4000 chars so total stays within context
    balanced = ""
    for lean in ["right", "neutral", "left"]:
        chunk = "\n".join(sections[lean])[:4000]
        balanced += chunk + "\n\n"
    prompt = META_SUMMARY_PROMPT.format(date=date_str, analyses=balanced)
    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        log.error(f"Meta-summary generation failed: {e}")
        return (
            "RIGHT-WING TODAY\n• Error generating summary. Check logs.\n• —\n• —\n\n"
            "CENTER/NEUTRAL TODAY\n• Error generating summary. Check logs.\n• —\n• —\n\n"
            "LEFT/PROGRESSIVE TODAY\n• Error generating summary. Check logs.\n• —\n• —"
        )


def parse_meta_summary(raw: str) -> dict:
    result = {"right": [], "neutral": [], "left": []}
    current = None
    for line in raw.split("\n"):
        line = line.strip()
        if "RIGHT-WING" in line.upper():
            current = "right"
        elif "CENTER" in line.upper() or "NEUTRAL" in line.upper():
            current = "neutral"
        elif "LEFT" in line.upper() or "PROGRESSIVE" in line.upper():
            current = "left"
        elif line.startswith("•") and current:
            result[current].append(line[1:].strip())
    return result


def collect_notable_quotes(rows: list) -> list:
    all_quotes = []
    for row in rows:
        podcast_name, lean, title, published, analysis_json = row
        try:
            analysis = json.loads(analysis_json)
        except Exception:
            continue
        for q in analysis.get("notable_quotes", []):
            all_quotes.append({
                "podcast": podcast_name,
                "lean": lean,
                "episode_title": title,
                "quote": q.get("quote", ""),
                "speaker": q.get("speaker", ""),
                "context": q.get("context", ""),
                "type": q.get("type", ""),
            })
    type_order = {"attack": 0, "claim": 1, "notable_position": 2, "cross_partisan_signal": 3, "admission": 4}
    all_quotes.sort(key=lambda x: type_order.get(x["type"], 9))
    return all_quotes[:20]


def generate_digest(client: anthropic.Anthropic, conn: sqlite3.Connection, date_str: str) -> tuple[str, str]:
    rows = conn.execute("""
        SELECT podcast_name, lean, title, published, analysis
        FROM episodes
        WHERE date(fetched_at) = date('now')
        AND analysis IS NOT NULL
        AND digest_included = 0
        ORDER BY lean, podcast_name
    """).fetchall()

    if not rows:
        empty = "<p>No episodes analyzed today. Run <code>python monitor.py run-all</code>.</p>"
        return empty, "No episodes analyzed today."

    analyses_text = ""
    for row in rows:
        podcast, lean, title, published, analysis_json = row
        try:
            analysis = json.loads(analysis_json)
        except Exception:
            continue
        analyses_text += f"\n[{lean.upper()}] {podcast} — \"{title}\"\n"
        analyses_text += f"Synopsis: {analysis.get('synopsis', '')}\n"
        analyses_text += f"Attacks: {'; '.join(analysis.get('political_attacks', []))}\n"
        analyses_text += f"Themes: {', '.join(analysis.get('narrative_themes', []))}\n"

    raw_meta = build_meta_summary(client, analyses_text, date_str)
    meta = parse_meta_summary(raw_meta)
    notable_quotes = collect_notable_quotes(rows)

    episode_rundown = []
    for row in rows:
        podcast, lean, title, published, analysis_json = row
        try:
            analysis = json.loads(analysis_json)
        except Exception:
            continue
        episode_rundown.append({
            "podcast": podcast,
            "lean": lean,
            "title": title,
            "published": published,
            "synopsis": analysis.get("synopsis", "No synopsis available."),
            "threat_level": analysis.get("threat_level", ""),
            "key_topics": analysis.get("key_topics", []),
        })

    html = render_digest_html(date_str, len(rows), meta, notable_quotes, episode_rundown)
    text = render_digest_text(date_str, len(rows), meta, notable_quotes, episode_rundown)

    conn.execute(
        "UPDATE episodes SET digest_included = 1 WHERE date(fetched_at) = date('now') AND analysis IS NOT NULL"
    )
    conn.commit()
    return html, text


# ── HTML Renderer ──────────────────────────────────────────────────────────────

LEAN_CONFIG = {
    "right":   {"label": "RIGHT-WING",        "color": "#c0392b", "bg": "#fdf2f2", "border": "#e8b0b0", "dot": "🔴"},
    "neutral": {"label": "CENTER / NEUTRAL",  "color": "#5d6d7e", "bg": "#f4f6f7", "border": "#c0c9d0", "dot": "⚪"},
    "left":    {"label": "LEFT / PROGRESSIVE","color": "#1a5276", "bg": "#eaf4fb", "border": "#aed6f1", "dot": "🔵"},
}

TYPE_LABELS = {
    "attack":                ("⚔️",  "#c0392b", "Attack"),
    "claim":                 ("📌", "#7d6608", "Claim"),
    "notable_position":      ("💬", "#1a5276", "Notable position"),
    "cross_partisan_signal": ("🤝", "#1e8449", "Cross-partisan signal"),
    "admission":             ("⚠️",  "#6c3483", "Admission"),
}

def threat_badge(level: str) -> str:
    cfg = {
        "high":   ("HIGH THREAT", "#c0392b", "#fdf2f2"),
        "medium": ("MED THREAT",  "#d35400", "#fef5ec"),
        "low":    ("LOW THREAT",  "#1e8449", "#eafaf1"),
    }.get(level, ("", "#888", "#f5f5f5"))
    label, color, bg = cfg
    if not label:
        return ""
    return (f'<span style="font-size:10px;font-weight:700;letter-spacing:.8px;'
            f'color:{color};background:{bg};border:1px solid {color};'
            f'border-radius:3px;padding:2px 6px;">{label}</span>')

def lean_badge(lean: str) -> str:
    cfg = LEAN_CONFIG.get(lean, {"label": lean.upper(), "color": "#888", "bg": "#f5f5f5", "border": "#ccc", "dot": "⚫"})
    return (f'<span style="font-size:10px;font-weight:700;letter-spacing:.8px;'
            f'color:{cfg["color"]};background:{cfg["bg"]};border:1px solid {cfg["border"]};'
            f'border-radius:3px;padding:2px 7px;">{cfg["dot"]} {cfg["label"]}</span>')


def render_digest_html(date_str, episode_count, meta, notable_quotes, episode_rundown) -> str:

    # Section 1: 3x3 meta-summary panels
    meta_html = ""
    for lean in ["right", "neutral", "left"]:
        cfg = LEAN_CONFIG[lean]
        bullets = meta.get(lean, ["No data."])
        bullet_items = "".join(
            f'<li style="margin:7px 0;line-height:1.6;color:#2c3e50;">{b}</li>'
            for b in bullets
        )
        meta_html += (
            f'<div style="flex:1;min-width:200px;background:{cfg["bg"]};'
            f'border:1px solid {cfg["border"]};border-top:3px solid {cfg["color"]};'
            f'border-radius:6px;padding:18px 20px;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;'
            f'color:{cfg["color"]};text-transform:uppercase;margin-bottom:12px;'
            f'font-family:sans-serif;">{cfg["dot"]} {cfg["label"]}</div>'
            f'<ul style="margin:0;padding-left:18px;font-family:Georgia,serif;font-size:13.5px;">'
            f'{bullet_items}</ul></div>'
        )

    # Section 2: Notable quotes
    quotes_html = ""
    if notable_quotes:
        for q in notable_quotes:
            cfg = LEAN_CONFIG.get(q["lean"], {"color": "#888", "bg": "#f9f9f9", "border": "#ddd"})
            icon, type_color, type_label = TYPE_LABELS.get(
                q["type"], ("💬", "#555", q["type"].replace("_", " ").title())
            )
            context_html = (f' &nbsp;·&nbsp; <em>{q["context"]}</em>' if q.get("context") else "")
            quotes_html += (
                f'<div style="border-left:3px solid {cfg["color"]};background:{cfg["bg"]};'
                f'border-radius:0 6px 6px 0;padding:14px 18px;margin-bottom:14px;">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">'
                f'{lean_badge(q["lean"])}'
                f'<span style="font-size:11px;font-weight:700;color:{type_color};'
                f'background:{type_color}18;border-radius:3px;padding:2px 7px;font-family:sans-serif;">'
                f'{icon} {type_label.upper()}</span>'
                f'<span style="font-family:sans-serif;font-size:12px;color:#666;">{q["podcast"]}</span>'
                f'</div>'
                f'<blockquote style="margin:0 0 8px 0;font-family:Georgia,serif;font-size:14px;'
                f'font-style:italic;color:#2c3e50;line-height:1.6;">&ldquo;{q["quote"]}&rdquo;</blockquote>'
                f'<div style="font-family:sans-serif;font-size:12px;color:#555;">'
                f'<strong>&mdash; {q["speaker"]}</strong>{context_html}</div>'
                f'</div>'
            )
    else:
        quotes_html = (
            '<p style="color:#888;font-family:sans-serif;font-size:13px;">'
            'No notable quotes extracted today. Add full transcript integration via Taddy API for richer extraction.'
            '</p>'
        )

    # Section 3: Episode rundown
    rundown_html = ""
    for ep in episode_rundown:
        cfg = LEAN_CONFIG.get(ep["lean"], {"color": "#888", "bg": "#f9f9f9", "border": "#ddd"})
        topics_html = " ".join(
            f'<span style="font-size:11px;background:#ecf0f1;color:#555;'
            f'border-radius:3px;padding:2px 7px;margin:0 2px;">{t}</span>'
            for t in ep.get("key_topics", [])[:4]
        )
        rundown_html += (
            f'<div style="border:1px solid {cfg["border"]};border-left:3px solid {cfg["color"]};'
            f'border-radius:0 6px 6px 0;padding:14px 18px;margin-bottom:12px;">'
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;'
            f'gap:12px;flex-wrap:wrap;margin-bottom:6px;">'
            f'<div>{lean_badge(ep["lean"])}'
            f'<span style="font-family:sans-serif;font-size:13px;font-weight:700;'
            f'color:#1a1a2e;margin-left:8px;">{ep["podcast"]}</span>'
            f'<span style="font-family:sans-serif;font-size:11px;color:#888;margin-left:6px;">'
            f'{ep["published"]}</span></div>'
            f'{threat_badge(ep.get("threat_level", ""))}'
            f'</div>'
            f'<div style="font-family:Georgia,serif;font-size:13px;font-style:italic;'
            f'color:#34495e;margin-bottom:8px;">&ldquo;{ep["title"]}&rdquo;</div>'
            f'<p style="font-family:Georgia,serif;font-size:13.5px;color:#2c3e50;'
            f'line-height:1.65;margin:0 0 10px 0;">{ep["synopsis"]}</p>'
            f'<div>{topics_html}</div>'
            f'</div>'
        )

    section_header = (
        lambda label: (
            f'<div style="font-family:sans-serif;font-size:11px;font-weight:700;'
            f'letter-spacing:2px;text-transform:uppercase;color:#888;'
            f'border-top:1px solid #e8ecef;padding-top:28px;margin-bottom:16px;">{label}</div>'
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Podcast Intelligence — {date_str}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Georgia,serif;color:#1a1a2e;">
<div style="max-width:700px;margin:0 auto;padding:24px 16px;">

  <div style="background:#1a3a5c;border-radius:8px 8px 0 0;padding:24px 32px;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#7fb3d3;font-family:sans-serif;margin-bottom:4px;">Podcast Intelligence Monitor</div>
    <div style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-.3px;">Daily Intelligence Report</div>
    <div style="font-size:13px;color:#a8cce0;margin-top:6px;font-family:sans-serif;">{date_str} &nbsp;·&nbsp; {episode_count} episodes analyzed</div>
  </div>

  <div style="background:#ffffff;padding:32px;border-radius:0 0 8px 8px;box-shadow:0 2px 8px rgba(0,0,0,0.07);">

    <div style="font-family:sans-serif;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#888;margin-bottom:16px;">TODAY AT A GLANCE</div>
    <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:36px;">{meta_html}</div>

    {section_header("NOTABLE MOMENTS &amp; QUOTES")}
    <div style="margin-bottom:36px;">{quotes_html}</div>

    {section_header("TODAY'S EPISODE RUNDOWN")}
    <div>{rundown_html}</div>

  </div>

  <div style="text-align:center;padding:20px 0 8px;font-family:sans-serif;font-size:11px;color:#aaa;">
    Podcast Intelligence Monitor &nbsp;·&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC
  </div>

</div>
</body>
</html>"""


# ── Plain Text Renderer ────────────────────────────────────────────────────────
def render_digest_text(date_str, episode_count, meta, notable_quotes, episode_rundown) -> str:
    lines = [
        f"PODCAST INTELLIGENCE REPORT — {date_str}",
        f"{episode_count} episodes analyzed",
        "", "=" * 60, "TODAY AT A GLANCE", "=" * 60,
    ]
    for lean in ["right", "neutral", "left"]:
        cfg = LEAN_CONFIG[lean]
        lines.append(f"\n{cfg['label']}:")
        for b in meta.get(lean, ["No data."]):
            lines.append(f"  • {b}")

    lines += ["", "=" * 60, "NOTABLE MOMENTS & QUOTES", "=" * 60, ""]
    for q in notable_quotes:
        lines += [
            f"[{q['lean'].upper()}] {q['podcast']} — {q['type'].upper()}",
            f'  "{q["quote"]}"',
            f"  — {q['speaker']}",
        ]
        if q.get("context"):
            lines.append(f"  {q['context']}")
        lines.append("")

    lines += ["=" * 60, "TODAY'S EPISODE RUNDOWN", "=" * 60, ""]
    for ep in episode_rundown:
        lines += [
            f"[{ep['lean'].upper()}] {ep['podcast']}  |  {ep['published']}",
            f"  \"{ep['title']}\"",
            f"  {ep['synopsis']}",
        ]
        if ep.get("key_topics"):
            lines.append(f"  Topics: {', '.join(ep['key_topics'][:4])}")
        lines.append("")

    return "\n".join(lines)


# ── Email Delivery ─────────────────────────────────────────────────────────────
def send_email(html: str, text: str, date_str: str, to_addresses: list[str], smtp_config: dict):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Podcast Intelligence — {date_str}"
    msg["From"] = smtp_config["from"]
    msg["To"] = ", ".join(to_addresses)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL(smtp_config["host"], smtp_config["port"]) as server:
        server.login(smtp_config["username"], smtp_config["password"])
        server.sendmail(smtp_config["from"], to_addresses, msg.as_string())
    log.info(f"Digest sent to {len(to_addresses)} recipients")


# ── Pipeline ───────────────────────────────────────────────────────────────────
def run_fetch(conn: sqlite3.Connection, since_hours: int = 48):
    podcasts = json.loads(PODCASTS_FILE.read_text())
    total_new = 0
    for lean, podcast_list in podcasts.items():
        for podcast in podcast_list:
            podcast["lean"] = lean
            episodes = fetch_rss_episodes(podcast, since_hours)
            for ep in episodes:
                if conn.execute("SELECT id FROM episodes WHERE id = ?", (ep["id"],)).fetchone():
                    continue
                conn.execute(
                    """INSERT INTO episodes
                       (id, podcast_name, lean, title, published, description, audio_url, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ep["id"], ep["podcast_name"], ep["lean"], ep["title"],
                     ep["published"], ep["description"], ep["audio_url"],
                     datetime.now(timezone.utc).isoformat()),
                )
                total_new += 1
            time.sleep(0.5)
        conn.commit()
    log.info(f"Fetched {total_new} new episodes")
    return total_new


def run_analyze(conn: sqlite3.Connection, max_episodes: int = 100):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build podcast lookup for YouTube channel IDs
    podcasts_data = json.loads(PODCASTS_FILE.read_text())
    podcast_lookup = {}
    for lean, podcast_list in podcasts_data.items():
        for p in podcast_list:
            podcast_lookup[p["name"]] = p

    rows = conn.execute("""
        SELECT id, podcast_name, lean, title, published, description, transcript
        FROM episodes
        WHERE analysis IS NULL
        AND (description IS NOT NULL AND length(description) > 50
             OR transcript IS NOT NULL)
        ORDER BY fetched_at DESC
        LIMIT ?
    """, (max_episodes,)).fetchall()
    log.info(f"Analyzing {len(rows)} episodes...")
    analyzed = 0
    for row in rows:
        ep_id, podcast_name, lean, title, published, description, transcript = row
        podcast = podcast_lookup.get(podcast_name, {})

        # Try YouTube transcript if we don't already have one
        if not transcript and podcast.get("youtube_channel_id"):
            transcript = try_fetch_transcript(podcast, title)
            if transcript:
                conn.execute("UPDATE episodes SET transcript = ? WHERE id = ?",
                             (transcript, ep_id))
                conn.commit()

        episode = {
            "id": ep_id, "podcast_name": podcast_name, "lean": lean,
            "host": podcast.get("host", ""),
            "title": title, "published": published,
            "description": description, "transcript": transcript,
        }
        analysis = analyze_episode(client, episode)
        if analysis:
            conn.execute("UPDATE episodes SET analysis = ? WHERE id = ?",
                         (json.dumps(analysis), ep_id))
            analyzed += 1
        conn.commit()
        time.sleep(1)
    log.info(f"Analyzed {analyzed} episodes")
    return analyzed


def run_digest(conn: sqlite3.Connection, config: dict):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    date_str = datetime.now().strftime("%B %d, %Y")
    log.info("Generating digest...")
    html, text = generate_digest(client, conn, date_str)
    digest_id = datetime.now().strftime("%Y%m%d")
    html_path = OUTPUT_DIR / f"digest_{digest_id}.html"
    text_path = OUTPUT_DIR / f"digest_{digest_id}.txt"
    html_path.write_text(html)
    text_path.write_text(text)
    log.info(f"Digest saved: {html_path}")
    conn.execute(
        "INSERT OR REPLACE INTO digests (id, date, content_html, content_text, created_at) VALUES (?,?,?,?,?)",
        (digest_id, date_str, html, text, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    smtp = config.get("smtp", {})
    recipients = config.get("recipients", [])
    if smtp and recipients:
        try:
            send_email(html, text, date_str, recipients, smtp)
        except Exception as e:
            log.error(f"Email send failed: {e}")
    else:
        log.info("No SMTP config — digest saved to disk only")
    return html_path


# ── CLI ────────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def main():
    parser = argparse.ArgumentParser(description="Podcast Intelligence Monitor")
    parser.add_argument("command",
                        choices=["fetch", "analyze", "digest", "run-all", "list-podcasts"])
    parser.add_argument("--since-hours", type=int, default=48)
    parser.add_argument("--max-episodes", type=int, default=100)
    args = parser.parse_args()
    config = load_config()

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        if args.command == "fetch":
            run_fetch(conn, args.since_hours)
        elif args.command == "analyze":
            run_analyze(conn, args.max_episodes)
        elif args.command == "digest":
            run_digest(conn, config)
        elif args.command == "run-all":
            log.info("=== Starting full pipeline run ===")
            new = run_fetch(conn, args.since_hours)
            if new > 0:
                run_analyze(conn, args.max_episodes)
            run_digest(conn, config)
            log.info("=== Pipeline complete ===")
        elif args.command == "list-podcasts":
            podcasts = json.loads(PODCASTS_FILE.read_text())
            for lean, plist in podcasts.items():
                print(f"\n{'='*50}\n {lean.upper()} ({len(plist)} podcasts)\n{'='*50}")
                for p in plist:
                    rss = "✓ RSS" if p.get("rss") else "✗ No RSS"
                    tx = "✓ Transcripts" if p.get("transcript_url") else ""
                    print(f"  {p['name']} | {rss} {tx}")


if __name__ == "__main__":
    main()

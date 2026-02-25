#!/usr/bin/env python3
"""
Podcast Intelligence Dashboard
Serves a web UI with charts, trend lines, and episode browser.
Run: python dashboard.py
Visit: http://localhost:8080
"""

import json
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import re

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "episodes.db"

# ── Data helpers ───────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure tables exist even if DB is fresh
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
    conn.commit()
    return conn

def get_stats():
    try:
        with get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            analyzed = conn.execute("SELECT COUNT(*) FROM episodes WHERE analysis IS NOT NULL").fetchone()[0]
            by_lean = conn.execute(
                "SELECT lean, COUNT(*) as cnt FROM episodes GROUP BY lean"
            ).fetchall()
            recent = conn.execute("""
                SELECT podcast_name, lean, title, published, analysis
                FROM episodes
                WHERE analysis IS NOT NULL
                ORDER BY fetched_at DESC
                LIMIT 20
            """).fetchall()
            threats = conn.execute("""
                SELECT json_extract(analysis, '$.threat_level') as tl, COUNT(*) as cnt
                FROM episodes WHERE analysis IS NOT NULL
                GROUP BY tl
            """).fetchall()
        return {
            "total": total,
            "analyzed": analyzed,
            "by_lean": {r["lean"]: r["cnt"] for r in by_lean},
            "recent": [dict(r) for r in recent],
            "threats": {r["tl"]: r["cnt"] for r in threats},
        }
    except Exception:
        return {"total": 0, "analyzed": 0, "by_lean": {}, "recent": [], "threats": {}}

def get_trending_topics():
    """Extract top topics from recent analyses."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT analysis FROM episodes
                WHERE analysis IS NOT NULL
                AND fetched_at > datetime('now', '-7 days')
            """).fetchall()
        topic_counts = {}
        for row in rows:
            try:
                analysis = json.loads(row["analysis"])
                for topic in analysis.get("key_topics", []):
                    topic = topic.lower().strip()
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
            except Exception:
                continue
        return sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    except Exception:
        return []

def get_daily_volume(days=14):
    """Episode count by day and lean."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT date(fetched_at) as day, lean, COUNT(*) as cnt
                FROM episodes
                WHERE fetched_at > datetime('now', '-{} days')
                GROUP BY day, lean
                ORDER BY day
            """.format(days)).fetchall()
        data = {}
        for row in rows:
            day = row["day"]
            if day not in data:
                data[day] = {"left": 0, "right": 0, "neutral": 0}
            data[day][row["lean"]] = row["cnt"]
        return data
    except Exception:
        return {}

def get_attacks():
    """Recent political attacks from right-wing podcasts."""
    try:
        with get_conn() as conn:
            # Try last 3 days first; fall back to most recent 30 episodes if empty
            rows = conn.execute("""
                SELECT podcast_name, title, published,
                       json_extract(analysis, '$.political_attacks') as attacks,
                       json_extract(analysis, '$.threat_level') as threat_level
                FROM episodes
                WHERE lean = 'right'
                AND analysis IS NOT NULL
                AND json_extract(analysis, '$.political_attacks') IS NOT NULL
                AND json_extract(analysis, '$.political_attacks') != '[]'
                ORDER BY fetched_at DESC
                LIMIT 30
            """).fetchall()
        results = []
        for row in rows:
            try:
                attacks = json.loads(row["attacks"] or "[]")
            except Exception:
                attacks = []
            if attacks:
                results.append({
                    "podcast": row["podcast_name"],
                    "title": row["title"],
                    "published": row["published"],
                    "attacks": attacks,
                    "threat_level": row["threat_level"],
                })
        return results
    except Exception:
        return []

def get_opportunities():
    """
    Messaging opportunities: use messaging_opportunities if populated,
    otherwise surface narrative themes from left/neutral podcasts as proxy.
    """
    try:
        with get_conn() as conn:
            # First try dedicated field
            rows = conn.execute("""
                SELECT podcast_name, lean,
                       json_extract(analysis, '$.messaging_opportunities') as opps
                FROM episodes
                WHERE analysis IS NOT NULL
                AND json_extract(analysis, '$.messaging_opportunities') IS NOT NULL
                AND json_extract(analysis, '$.messaging_opportunities') != '[]'
                ORDER BY fetched_at DESC
            """).fetchall()

        results = []
        for row in rows:
            try:
                opps = json.loads(row["opps"] or "[]")
            except Exception:
                opps = []
            for opp in opps:
                results.append({"podcast": row["podcast_name"], "lean": row["lean"], "opp": opp})

        # Fall back: surface narrative themes from left/neutral as messaging signals
        if not results:
            with get_conn() as conn:
                rows = conn.execute("""
                    SELECT podcast_name, lean,
                           json_extract(analysis, '$.narrative_themes') as themes
                    FROM episodes
                    WHERE analysis IS NOT NULL
                    AND lean IN ('left', 'neutral')
                    AND json_extract(analysis, '$.narrative_themes') IS NOT NULL
                    AND json_extract(analysis, '$.narrative_themes') != '[]'
                    ORDER BY fetched_at DESC
                    LIMIT 20
                """).fetchall()
            for row in rows:
                try:
                    themes = json.loads(row["themes"] or "[]")
                    if isinstance(themes, str):
                        themes = [themes]
                except Exception:
                    themes = []
                for t in themes[:2]:  # max 2 per episode
                    results.append({"podcast": row["podcast_name"], "lean": row["lean"], "opp": t})

        return results[:25]
    except Exception:
        return []

# Campaign theme keywords
CAMPAIGN_KEYWORDS = {
    "cruelty": [
        "cruelty", "cruel", "heartless", "inhumane", "suffering", "punish",
        "harm", "hurt", "cut", "strip", "deny", "rip away", "slash", "brutal",
        "vicious", "callous", "merciless", "pain", "devastate", "abandon",
        "medicaid", "snap", "food stamps", "disability", "veterans benefits",
        "deportation", "family separation", "children", "vulnerable",
    ],
    "affordability": [
        "afford", "affordability", "cost", "price", "expense", "expensive",
        "housing", "rent", "mortgage", "healthcare", "prescription", "drug price",
        "grocery", "food", "inflation", "wage", "salary", "income", "debt",
        "student loan", "childcare", "utilities", "insurance", "copay",
        "out of pocket", "middle class", "working family", "paycheck",
        "tariff", "tax cut", "billionaire", "corporate", "profit",
    ],
}

def _text_matches_themes(text, themes=None):
    """Return which campaign themes a text string matches."""
    if not text:
        return []
    text_lower = text.lower()
    if themes is None:
        themes = list(CAMPAIGN_KEYWORDS.keys())
    matched = []
    for theme in themes:
        if any(kw in text_lower for kw in CAMPAIGN_KEYWORDS[theme]):
            matched.append(theme)
    return matched

def _parse_date(date_str):
    """Parse various date formats into a datetime. Returns None on failure."""
    if not date_str:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str[:len(fmt)+5].strip(), fmt)
        except Exception:
            pass
    return None

def get_campaign_intelligence(hours=72):
    """
    Pull episodes published within the last `hours` hours and surface all moments
    relevant to the cruelty/affordability campaign themes.
    Uses published date (not fetched_at) so seeded historical data still shows.
    Returns a list of episode dicts with filtered notable moments.
    """
    try:
        cutoff = datetime.now(tz=None) - timedelta(hours=hours)
        # Make cutoff timezone-naive for comparison
        cutoff_naive = cutoff.replace(tzinfo=None)

        with get_conn() as conn:
            rows = conn.execute("""
                SELECT podcast_name, lean, title, published, audio_url, analysis, fetched_at
                FROM episodes
                WHERE analysis IS NOT NULL
                ORDER BY fetched_at DESC
            """).fetchall()

        # Filter in Python using robust date parsing
        filtered = []
        for row in rows:
            pub = _parse_date(row["published"])
            if pub is not None:
                pub_naive = pub.replace(tzinfo=None)
                if pub_naive >= cutoff_naive:
                    filtered.append(row)

        # If nothing matched published date, fall back to fetched_at filter
        if not filtered:
            cutoff_str = (datetime.now(tz=None) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
            with get_conn() as conn:
                filtered = conn.execute("""
                    SELECT podcast_name, lean, title, published, audio_url, analysis, fetched_at
                    FROM episodes
                    WHERE analysis IS NOT NULL
                    AND fetched_at > ?
                    ORDER BY fetched_at DESC
                """, (cutoff_str,)).fetchall()

        rows = filtered

        results = []
        for row in rows:
            try:
                a = json.loads(row["analysis"])
            except Exception:
                continue

            ep_themes = set()
            moments = []

            # Check synopsis / narrative themes
            synopsis = a.get("synopsis", "") or a.get("one_liner", "")
            narrative_themes = a.get("narrative_themes", [])
            if isinstance(narrative_themes, str):
                narrative_themes = [narrative_themes]

            # Check key topics
            for topic in (a.get("key_topics") or []):
                t = _text_matches_themes(topic)
                ep_themes.update(t)

            # Notable quotes
            for q in (a.get("notable_quotes") or []):
                quote_text = q.get("quote", "") if isinstance(q, dict) else str(q)
                context = q.get("context", "") if isinstance(q, dict) else ""
                combined = f"{quote_text} {context}"
                matched = _text_matches_themes(combined)
                if matched:
                    ep_themes.update(matched)
                    moments.append({
                        "type": "quote",
                        "themes": matched,
                        "text": quote_text,
                        "speaker": q.get("speaker", "") if isinstance(q, dict) else "",
                        "context": context,
                        "badge": (q.get("type", "") if isinstance(q, dict) else "").replace("_", " "),
                    })

            # Political attacks
            for atk in (a.get("political_attacks") or []):
                atk_text = atk if isinstance(atk, str) else str(atk)
                matched = _text_matches_themes(atk_text)
                if matched:
                    ep_themes.update(matched)
                    moments.append({
                        "type": "attack",
                        "themes": matched,
                        "text": atk_text,
                        "speaker": "",
                        "context": "",
                        "badge": "attack",
                    })

            # Messaging opportunities
            for opp in (a.get("messaging_opportunities") or []):
                opp_text = opp if isinstance(opp, str) else str(opp)
                matched = _text_matches_themes(opp_text)
                if matched:
                    ep_themes.update(matched)
                    moments.append({
                        "type": "opportunity",
                        "themes": matched,
                        "text": opp_text,
                        "speaker": "",
                        "context": "",
                        "badge": "opportunity",
                    })

            # Narrative themes
            for nt in narrative_themes:
                matched = _text_matches_themes(nt)
                if matched:
                    ep_themes.update(matched)
                    moments.append({
                        "type": "narrative",
                        "themes": matched,
                        "text": nt,
                        "speaker": "",
                        "context": "",
                        "badge": "narrative frame",
                    })

            # Also check synopsis itself
            synopsis_themes = _text_matches_themes(synopsis)
            ep_themes.update(synopsis_themes)

            # Only include episodes with at least one campaign-relevant moment
            # OR whose synopsis/topics clearly match
            if ep_themes or moments:
                # Even if no specific moments flagged, include episode if synopsis matches
                if not moments and synopsis_themes:
                    moments.append({
                        "type": "synopsis",
                        "themes": synopsis_themes,
                        "text": synopsis,
                        "speaker": "",
                        "context": "",
                        "badge": "episode summary",
                    })

                pub = row["published"] or row["fetched_at"] or ""
                results.append({
                    "podcast": row["podcast_name"],
                    "lean": row["lean"],
                    "title": row["title"] or "(Untitled)",
                    "published": pub[:16] if pub else "",
                    "audio_url": row["audio_url"] or "",
                    "synopsis": synopsis,
                    "threat": a.get("threat_level", "low"),
                    "themes": sorted(ep_themes),
                    "moments": moments,
                    "topics": (a.get("key_topics") or [])[:5],
                })

        return results
    except Exception as e:
        return []

# ── Campaign Intelligence HTML ──────────────────────────────────────────────────
def build_campaign_html(episodes, hours=72):
    def lean_color(lean):
        return {"left": "#2a9d8f", "right": "#e8333c", "neutral": "#555"}.get(lean, "#888")

    def threat_color(level):
        return {"high": "#e8333c", "medium": "#f0a500", "low": "#2a9d8f"}.get(level, "#888")

    def theme_pill(theme):
        colors = {"cruelty": "#7c1d1d", "affordability": "#1a3a5c"}
        bg = colors.get(theme, "#555")
        return f'<span class="theme-pill" style="background:{bg};">{theme.upper()}</span>'

    def moment_type_style(mtype):
        return {
            "quote": ("#7c3aed", "💬"),
            "attack": ("#e8333c", "🚨"),
            "opportunity": ("#2a9d8f", "💡"),
            "narrative": ("#f0a500", "📣"),
            "synopsis": ("#2c5f8a", "📋"),
        }.get(mtype, ("#888", "•"))

    if not episodes:
        body = """
        <div class="empty-state">
            <div style="font-size:48px;margin-bottom:16px;">📭</div>
            <p>No episodes with cruelty or affordability signals found in the last 72 hours.</p>
            <p style="font-size:13px;margin-top:8px;color:#888;">Run the pipeline to fetch and analyze new episodes: <code>python monitor.py run-all</code></p>
        </div>"""
    else:
        # Count by theme
        cruelty_count = sum(1 for e in episodes if "cruelty" in e["themes"])
        afford_count = sum(1 for e in episodes if "affordability" in e["themes"])
        both_count = sum(1 for e in episodes if "cruelty" in e["themes"] and "affordability" in e["themes"])
        high_threat = sum(1 for e in episodes if e["threat"] == "high")

        stats_strip = f"""
        <div class="camp-stats">
            <div class="camp-stat">
                <div class="camp-stat-num">{len(episodes)}</div>
                <div class="camp-stat-label">Relevant Episodes ({hours}h)</div>
            </div>
            <div class="camp-stat cruelty">
                <div class="camp-stat-num">{cruelty_count}</div>
                <div class="camp-stat-label">Cruelty Theme</div>
            </div>
            <div class="camp-stat afford">
                <div class="camp-stat-num">{afford_count}</div>
                <div class="camp-stat-label">Affordability Theme</div>
            </div>
            <div class="camp-stat both">
                <div class="camp-stat-num">{both_count}</div>
                <div class="camp-stat-label">Both Themes</div>
            </div>
            <div class="camp-stat threat">
                <div class="camp-stat-num">{high_threat}</div>
                <div class="camp-stat-label">High Threat</div>
            </div>
        </div>"""

        episodes_html = ""
        for ep in episodes:
            lc = lean_color(ep["lean"])
            tc = threat_color(ep["threat"])
            theme_pills = "".join(theme_pill(t) for t in ep["themes"])
            link_html = (
                f'<a href="{ep["audio_url"]}" class="ep-link" target="_blank" rel="noopener">🔗 Listen</a>'
                if ep["audio_url"] else ""
            )
            topics_str = " · ".join(ep["topics"]) if ep["topics"] else ""

            moments_html = ""
            for m in ep["moments"]:
                mcolor, micon = moment_type_style(m["type"])
                mpills = "".join(theme_pill(t) for t in m["themes"])
                speaker_html = f'<span class="moment-speaker">— {m["speaker"]}</span>' if m.get("speaker") else ""
                context_html = f'<div class="moment-context">{m["context"]}</div>' if m.get("context") else ""
                badge_html = f'<span class="moment-badge" style="background:{mcolor};">{micon} {m["badge"].upper() or m["type"].upper()}</span>' if m.get("badge") or m.get("type") else ""
                moments_html += f"""
                <div class="moment" style="border-left-color:{mcolor};">
                    <div class="moment-header">
                        {badge_html}
                        {mpills}
                    </div>
                    <div class="moment-text">{'&ldquo;' if m['type']=='quote' else ''}{m['text']}{'&rdquo;' if m['type']=='quote' else ''}</div>
                    {speaker_html}
                    {context_html}
                </div>"""

            episodes_html += f"""
            <div class="ep-card">
                <div class="ep-card-header">
                    <div class="ep-meta-left">
                        <span class="lean-badge" style="background:{lc};">{ep['lean'].upper()}</span>
                        <span class="threat-badge" style="background:{tc};">{ep['threat'].upper()} THREAT</span>
                        {theme_pills}
                    </div>
                    <div class="ep-meta-right">
                        <span class="ep-date">📅 {ep['published'] or 'Date unknown'}</span>
                        {link_html}
                    </div>
                </div>
                <div class="ep-podcast">{ep['podcast']}</div>
                <div class="ep-title">"{ep['title']}"</div>
                {f'<div class="ep-topics">Topics: {topics_str}</div>' if topics_str else ''}
                {f'<div class="ep-synopsis">{ep["synopsis"]}</div>' if ep["synopsis"] else ''}
                <div class="moments-section">
                    <div class="moments-label">CAMPAIGN-RELEVANT MOMENTS</div>
                    {moments_html if moments_html else '<div class="no-moments">Episode matches on topic/summary level — run with full transcripts for deeper extraction.</div>'}
                </div>
            </div>"""

        body = stats_strip + episodes_html

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Campaign Intelligence — Cruelty &amp; Affordability</title>
<style>
  :root {{
    --navy: #1a3a5c;
    --red: #e8333c;
    --teal: #2a9d8f;
    --bg: #f4f5f7;
    --card: #ffffff;
    --border: #e2e5ea;
    --text: #1a1a2e;
    --muted: #6b7280;
    --cruelty: #7c1d1d;
    --afford: #1a3a5c;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Georgia, serif; background: var(--bg); color: var(--text); }}
  header {{
    background: var(--navy);
    color: white;
    padding: 0 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 64px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    position: sticky; top: 0; z-index: 100;
  }}
  .logo {{ display: flex; align-items: center; gap: 14px; }}
  .logo-mark {{
    width: 36px; height: 36px;
    background: var(--red);
    border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 18px; color: white; font-family: sans-serif;
  }}
  .logo-text {{ font-size: 15px; font-weight: bold; }}
  .logo-sub {{ font-size: 11px; color: #7fb3d3; letter-spacing: 1px; text-transform: uppercase; }}
  .header-nav {{ display: flex; gap: 24px; align-items: center; }}
  .nav-link {{ color: #a8cce0; font-family: sans-serif; font-size: 13px; text-decoration: none; }}
  .nav-link:hover {{ color: white; }}
  .nav-link.active {{ color: white; font-weight: bold; border-bottom: 2px solid var(--red); padding-bottom: 2px; }}

  .main {{ max-width: 1100px; margin: 0 auto; padding: 28px 24px; }}

  .page-title {{
    font-size: 22px;
    font-weight: bold;
    color: var(--navy);
    margin-bottom: 6px;
  }}
  .page-subtitle {{
    font-family: sans-serif;
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 28px;
    line-height: 1.6;
  }}
  .theme-legend {{
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .legend-item {{
    display: flex; align-items: center; gap: 8px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    font-family: sans-serif;
    font-size: 12px;
  }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }}
  .legend-label {{ font-weight: bold; }}
  .legend-desc {{ color: var(--muted); margin-left: 4px; }}

  /* Stats */
  .camp-stats {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 14px;
    margin-bottom: 28px;
  }}
  .camp-stat {{
    background: var(--card);
    border-radius: 10px;
    padding: 18px;
    border: 1px solid var(--border);
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  }}
  .camp-stat-num {{
    font-size: 32px;
    font-weight: bold;
    color: var(--navy);
    line-height: 1;
    margin-bottom: 6px;
  }}
  .camp-stat-label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; font-family: sans-serif; }}
  .camp-stat.cruelty .camp-stat-num {{ color: var(--cruelty); }}
  .camp-stat.afford .camp-stat-num {{ color: var(--afford); }}
  .camp-stat.both .camp-stat-num {{ color: #7c3aed; }}
  .camp-stat.threat .camp-stat-num {{ color: var(--red); }}

  /* Episode cards */
  .ep-card {{
    background: var(--card);
    border-radius: 10px;
    border: 1px solid var(--border);
    margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    overflow: hidden;
  }}
  .ep-card-header {{
    padding: 14px 20px;
    background: #f8f9fb;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }}
  .ep-meta-left {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .ep-meta-right {{ display: flex; align-items: center; gap: 14px; }}
  .lean-badge, .threat-badge {{
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 10px;
    color: white;
    font-family: sans-serif;
    font-weight: bold;
    letter-spacing: 0.5px;
  }}
  .theme-pill {{
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 10px;
    color: white;
    font-family: sans-serif;
    font-weight: bold;
    letter-spacing: 0.5px;
  }}
  .ep-date {{ font-family: sans-serif; font-size: 12px; color: var(--muted); }}
  .ep-link {{
    font-family: sans-serif;
    font-size: 12px;
    color: var(--navy);
    text-decoration: none;
    font-weight: bold;
    padding: 4px 10px;
    border: 1px solid var(--navy);
    border-radius: 4px;
  }}
  .ep-link:hover {{ background: var(--navy); color: white; }}
  .ep-podcast {{
    font-family: sans-serif;
    font-size: 12px;
    font-weight: bold;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 14px 20px 4px;
  }}
  .ep-title {{
    font-size: 16px;
    font-weight: bold;
    color: var(--navy);
    padding: 0 20px 8px;
    line-height: 1.4;
  }}
  .ep-topics {{
    font-family: sans-serif;
    font-size: 11px;
    color: var(--muted);
    padding: 0 20px 8px;
  }}
  .ep-synopsis {{
    font-size: 13px;
    color: #444;
    line-height: 1.7;
    padding: 0 20px 12px;
  }}
  .moments-section {{
    border-top: 1px solid var(--border);
    padding: 16px 20px;
    background: #fafbfc;
  }}
  .moments-label {{
    font-family: sans-serif;
    font-size: 10px;
    font-weight: bold;
    color: var(--muted);
    letter-spacing: 2px;
    margin-bottom: 12px;
    text-transform: uppercase;
  }}
  .moment {{
    border-left: 3px solid #ccc;
    padding: 10px 14px;
    margin-bottom: 10px;
    background: white;
    border-radius: 0 6px 6px 0;
  }}
  .moment:last-child {{ margin-bottom: 0; }}
  .moment-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }}
  .moment-badge {{
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    color: white;
    font-family: sans-serif;
    font-weight: bold;
    letter-spacing: 0.5px;
  }}
  .moment-text {{ font-size: 13px; line-height: 1.7; color: #222; margin-bottom: 4px; }}
  .moment-speaker {{ font-family: sans-serif; font-size: 12px; color: var(--muted); font-style: italic; display: block; margin-top: 4px; }}
  .moment-context {{ font-family: sans-serif; font-size: 12px; color: var(--muted); margin-top: 6px; line-height: 1.5; border-top: 1px dashed var(--border); padding-top: 6px; }}
  .no-moments {{ font-family: sans-serif; font-size: 12px; color: var(--muted); font-style: italic; }}
  .empty-state {{
    text-align: center;
    padding: 80px 20px;
    color: var(--muted);
    font-family: sans-serif;
  }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-mark">P</div>
    <div>
      <div class="logo-text">Podcast Intelligence Dashboard</div>
      <div class="logo-sub">Political Media Monitor</div>
    </div>
  </div>
  <div class="header-nav">
    <a href="/" class="nav-link">Overview</a>
    <a href="/right" class="nav-link">Right-Wing</a>
    <a href="/campaign" class="nav-link active">Campaign Intel</a>
  </div>
</header>

<div class="main">
  <div class="page-title">🎯 Campaign Intelligence — Last {hours} Hours</div>
  <div class="page-subtitle">
    All podcast episodes <strong>published in the last {hours} hours</strong> surfaced for <strong>cruelty</strong> and <strong>affordability</strong> campaign themes.
    Includes notable quotes, political attacks, narrative frames, and direct links to source audio.
    Updated: {datetime.now().strftime('%B %d, %Y at %H:%M UTC')}
    &nbsp;·&nbsp; <a href="/campaign?hours=168" style="color:#2c5f8a;">7 days</a>
    &nbsp;·&nbsp; <a href="/campaign?hours=72" style="color:#2c5f8a;">72h</a>
    &nbsp;·&nbsp; <a href="/campaign?hours=24" style="color:#2c5f8a;">24h</a>
  </div>

  <div class="theme-legend">
    <div class="legend-item">
      <div class="legend-dot" style="background:#7c1d1d;"></div>
      <span class="legend-label">CRUELTY</span>
      <span class="legend-desc">— cuts, harm, suffering, vulnerable populations, inhumane policy</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background:#1a3a5c;"></div>
      <span class="legend-label">AFFORDABILITY</span>
      <span class="legend-desc">— cost of living, housing, healthcare, wages, corporate profits</span>
    </div>
  </div>

  {body}
</div>
</body>
</html>"""


# ── HTML Dashboard ─────────────────────────────────────────────────────────────
def build_dashboard_html(stats, topics, volume, attacks, opps):
    topic_labels = [t[0] for t in topics[:12]]
    topic_values = [t[1] for t in topics[:12]]
    
    days_sorted = sorted(volume.keys())
    vol_labels = days_sorted[-14:]
    vol_left = [volume.get(d, {}).get("left", 0) for d in vol_labels]
    vol_right = [volume.get(d, {}).get("right", 0) for d in vol_labels]
    vol_neutral = [volume.get(d, {}).get("neutral", 0) for d in vol_labels]

    # Threat badge colors
    def threat_color(level):
        return {"high": "#e8333c", "medium": "#f0a500", "low": "#2a9d8f"}.get(level, "#888")

    attacks_html = ""
    for a in attacks[:15]:
        color = threat_color(a.get("threat_level"))
        attacks_list = "".join(f"<li>{atk}</li>" for atk in a.get("attacks", []))
        attacks_html += f"""
        <div class="card attack-card">
          <div class="card-header">
            <span class="badge" style="background:{color};">{(a.get('threat_level') or 'unknown').upper()}</span>
            <strong>{a['podcast']}</strong>
          </div>
          <div class="episode-title">{a['title']}</div>
          <ul class="attack-list">{attacks_list}</ul>
        </div>"""

    opps_html = ""
    for o in opps:
        lean_color = {"left": "#2a9d8f", "right": "#e8333c", "neutral": "#555"}.get(o["lean"], "#888")
        opps_html += f"""
        <div class="opp-item">
          <span class="lean-tag" style="background:{lean_color};">{o['lean']}</span>
          <span class="opp-source">{o['podcast']}</span>
          <span class="opp-text">{o['opp']}</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Podcast Intelligence Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {{
    --navy: #1a3a5c;
    --red: #e8333c;
    --blue: #2c5f8a;
    --teal: #2a9d8f;
    --amber: #f0a500;
    --bg: #f4f5f7;
    --card: #ffffff;
    --border: #e2e5ea;
    --text: #1a1a2e;
    --muted: #6b7280;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Georgia', serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }}
  header {{
    background: var(--navy);
    color: white;
    padding: 0 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 64px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .logo {{ display: flex; align-items: center; gap: 14px; }}
  .logo-mark {{
    width: 36px; height: 36px;
    background: var(--red);
    border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 18px; color: white;
    font-family: sans-serif;
  }}
  .logo-text {{ font-size: 15px; font-weight: bold; letter-spacing: 0.3px; }}
  .logo-sub {{ font-size: 11px; color: #7fb3d3; letter-spacing: 1px; text-transform: uppercase; }}
  .header-right {{ font-size: 12px; color: #a8cce0; }}
  
  .main {{ max-width: 1280px; margin: 0 auto; padding: 24px 24px; }}
  
  /* Stats strip */
  .stats-strip {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }}
  .stat-card {{
    background: var(--card);
    border-radius: 10px;
    padding: 20px;
    border: 1px solid var(--border);
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  }}
  .stat-number {{
    font-size: 36px;
    font-weight: bold;
    color: var(--navy);
    line-height: 1;
  }}
  .stat-label {{
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 6px;
    font-family: sans-serif;
  }}
  .stat-card.red .stat-number {{ color: var(--red); }}
  .stat-card.teal .stat-number {{ color: var(--teal); }}
  .stat-card.amber .stat-number {{ color: var(--amber); }}
  
  /* Grid layout */
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .span-2 {{ grid-column: span 2; }}
  
  .panel {{
    background: var(--card);
    border-radius: 10px;
    border: 1px solid var(--border);
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  }}
  .panel-header {{
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .panel-title {{
    font-size: 14px;
    font-weight: bold;
    color: var(--navy);
    font-family: sans-serif;
    letter-spacing: 0.3px;
  }}
  .panel-subtitle {{ font-size: 11px; color: var(--muted); font-family: sans-serif; }}
  .panel-dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--red);
    flex-shrink: 0;
  }}
  .panel-body {{ padding: 20px; }}
  canvas {{ max-height: 240px; }}
  
  /* Attacks */
  .card {{ border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px; }}
  .attack-card {{ border-left: 3px solid var(--red); }}
  .card-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-family: sans-serif; font-size: 13px; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 10px; color: white; font-family: sans-serif; font-weight: bold; letter-spacing: 0.5px; }}
  .episode-title {{ font-size: 12px; color: var(--muted); margin-bottom: 8px; font-family: sans-serif; }}
  .attack-list {{ padding-left: 18px; font-size: 13px; line-height: 1.7; }}
  
  /* Opportunities */
  .opp-item {{ display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; }}
  .opp-item:last-child {{ border-bottom: none; }}
  .lean-tag {{ padding: 2px 8px; border-radius: 4px; font-size: 10px; color: white; font-family: sans-serif; flex-shrink: 0; margin-top: 2px; }}
  .opp-source {{ font-family: sans-serif; color: var(--muted); font-size: 11px; flex-shrink: 0; min-width: 130px; margin-top: 2px; }}
  .opp-text {{ line-height: 1.5; }}
  
  /* Scrollable panels */
  .scrollable {{ max-height: 480px; overflow-y: auto; padding-right: 4px; }}
  .scrollable::-webkit-scrollbar {{ width: 4px; }}
  .scrollable::-webkit-scrollbar-track {{ background: var(--bg); }}
  .scrollable::-webkit-scrollbar-thumb {{ background: #ccc; border-radius: 4px; }}
  
  /* Refresh indicator */
  .live-dot {{ width: 8px; height: 8px; background: #22c55e; border-radius: 50%; animation: pulse 2s infinite; display: inline-block; margin-right: 6px; }}
  @keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.4; }} }}
  
  .section-label {{
    font-family: sans-serif;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--muted);
    margin: 28px 0 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-mark">P</div>
    <div>
      <div class="logo-text">Podcast Intelligence Dashboard</div>
      <div class="logo-sub">Political Media Monitor</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:28px;">
    <nav style="display:flex;gap:20px;">
      <a href="/" style="color:white;font-family:sans-serif;font-size:13px;text-decoration:none;border-bottom:2px solid var(--red);padding-bottom:2px;">Overview</a>
      <a href="/right" style="color:#a8cce0;font-family:sans-serif;font-size:13px;text-decoration:none;">Right-Wing</a>
      <a href="/campaign" style="color:#a8cce0;font-family:sans-serif;font-size:13px;text-decoration:none;">🎯 Campaign Intel</a>
    </nav>
    <div class="header-right">
      <span class="live-dot"></span>
      {datetime.now().strftime('%b %d, %Y %H:%M')} UTC
    </div>
  </div>
</header>

<div class="main">

  <!-- Stats -->
  <div class="stats-strip">
    <div class="stat-card">
      <div class="stat-number">{stats['total']}</div>
      <div class="stat-label">Total Episodes</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">{stats['analyzed']}</div>
      <div class="stat-label">Analyzed</div>
    </div>
    <div class="stat-card red">
      <div class="stat-number">{stats['by_lean'].get('right', 0)}</div>
      <div class="stat-label">Right-Wing</div>
    </div>
    <div class="stat-card teal">
      <div class="stat-number">{stats['by_lean'].get('left', 0)}</div>
      <div class="stat-label">Left/Progressive</div>
    </div>
    <div class="stat-card amber">
      <div class="stat-number">{stats['threats'].get('high', 0)}</div>
      <div class="stat-label">High Threat Today</div>
    </div>
  </div>

  <p class="section-label">Volume &amp; Trends</p>
  
  <div class="grid-2">
    <!-- Volume by lean -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-dot"></div>
        <div>
          <div class="panel-title">Episode Volume by Lean (14 days)</div>
          <div class="panel-subtitle">Episodes fetched per day</div>
        </div>
      </div>
      <div class="panel-body">
        <canvas id="volumeChart"></canvas>
      </div>
    </div>

    <!-- Topic frequency -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-dot" style="background:#2a9d8f;"></div>
        <div>
          <div class="panel-title">Trending Topics (7 days)</div>
          <div class="panel-subtitle">Most discussed across all podcasts</div>
        </div>
      </div>
      <div class="panel-body">
        <canvas id="topicsChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Threat distribution -->
  <div class="grid-3">
    <div class="panel">
      <div class="panel-header">
        <div class="panel-dot" style="background:#f0a500;"></div>
        <div><div class="panel-title">Threat Level Distribution</div></div>
      </div>
      <div class="panel-body">
        <canvas id="threatChart"></canvas>
      </div>
    </div>
    
    <div class="panel span-2">
      <div class="panel-header">
        <div class="panel-dot" style="background:#2c5f8a;"></div>
        <div>
          <div class="panel-title">Lean Breakdown</div>
          <div class="panel-subtitle">Podcasts in monitoring universe</div>
        </div>
      </div>
      <div class="panel-body">
        <canvas id="leanPieChart"></canvas>
      </div>
    </div>
  </div>

  <p class="section-label">Intelligence Feed</p>

  <div class="grid-2">
    <!-- Right-wing attacks -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-dot"></div>
        <div>
          <div class="panel-title">🚨 Right-Wing Attacks (72h)</div>
          <div class="panel-subtitle">Attacks requiring counter-messaging</div>
        </div>
      </div>
      <div class="panel-body scrollable">
        {attacks_html if attacks_html else '<p style="color:#888;font-family:sans-serif;font-size:13px;">No high-signal attacks found in last 72h. Run analyzer to populate.</p>'}
      </div>
    </div>

    <!-- Messaging opportunities -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-dot" style="background:#2a9d8f;"></div>
        <div>
          <div class="panel-title">💡 Messaging Opportunities (72h)</div>
          <div class="panel-subtitle">Issues to go on offense with</div>
        </div>
      </div>
      <div class="panel-body scrollable">
        {opps_html if opps_html else '<p style="color:#888;font-family:sans-serif;font-size:13px;">No opportunities found yet. Run analyzer to populate.</p>'}
      </div>
    </div>
  </div>

  <!-- Campaign Intel CTA -->
  <div style="
    background: linear-gradient(135deg, #1a3a5c 0%, #7c1d1d 100%);
    border-radius: 12px;
    padding: 28px 32px;
    margin-top: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.15);
  ">
    <div>
      <div style="color:white;font-size:18px;font-weight:bold;margin-bottom:8px;">🎯 Campaign Intelligence: Cruelty &amp; Affordability</div>
      <div style="color:#a8cce0;font-family:sans-serif;font-size:13px;line-height:1.6;">
        View all episodes from the last 72 hours filtered for cruelty and affordability signals —
        including notable quotes, attacks, narrative frames, and direct links to source audio.
      </div>
    </div>
    <a href="/campaign" style="
      background: white;
      color: #1a3a5c;
      font-family: sans-serif;
      font-size: 13px;
      font-weight: bold;
      padding: 12px 24px;
      border-radius: 8px;
      text-decoration: none;
      white-space: nowrap;
      flex-shrink: 0;
    ">View Campaign Intel →</a>
  </div>

</div>

<script>
// Volume chart
new Chart(document.getElementById('volumeChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(vol_labels)},
    datasets: [
      {{
        label: 'Right',
        data: {json.dumps(vol_right)},
        borderColor: '#e8333c',
        backgroundColor: 'rgba(232,51,60,0.08)',
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 3,
      }},
      {{
        label: 'Left',
        data: {json.dumps(vol_left)},
        borderColor: '#2a9d8f',
        backgroundColor: 'rgba(42,157,143,0.08)',
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 3,
      }},
      {{
        label: 'Neutral',
        data: {json.dumps(vol_neutral)},
        borderColor: '#6b7280',
        backgroundColor: 'rgba(107,114,128,0.08)',
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 3,
      }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'bottom' }} }},
    scales: {{ y: {{ beginAtZero: true }} }},
  }}
}});

// Topics chart
new Chart(document.getElementById('topicsChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(topic_labels)},
    datasets: [{{
      data: {json.dumps(topic_values)},
      backgroundColor: '#2c5f8a',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ beginAtZero: true }} }},
  }}
}});

// Threat chart
new Chart(document.getElementById('threatChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['High', 'Medium', 'Low'],
    datasets: [{{
      data: [
        {stats['threats'].get('high', 0)},
        {stats['threats'].get('medium', 0)},
        {stats['threats'].get('low', 0)},
      ],
      backgroundColor: ['#e8333c', '#f0a500', '#2a9d8f'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'bottom' }} }},
    cutout: '65%',
  }}
}});

// Lean pie
new Chart(document.getElementById('leanPieChart'), {{
  type: 'bar',
  data: {{
    labels: ['Right-Wing', 'Left/Progressive', 'Neutral/Center'],
    datasets: [{{
      data: [
        {stats['by_lean'].get('right', 0)},
        {stats['by_lean'].get('left', 0)},
        {stats['by_lean'].get('neutral', 0)},
      ],
      backgroundColor: ['#e8333c', '#2a9d8f', '#6b7280'],
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true }} }},
  }}
}});
</script>

</body>
</html>"""

# ── Right-Wing Dashboard ───────────────────────────────────────────────────────
def get_right_wing_data():
  try:
    with get_conn() as conn:
        episodes = conn.execute("""
            SELECT podcast_name, title, published, analysis, fetched_at
            FROM episodes
            WHERE lean = 'right' AND analysis IS NOT NULL
            ORDER BY fetched_at DESC
        """).fetchall()

        per_show = conn.execute("""
            SELECT podcast_name, COUNT(*) as cnt,
                   SUM(CASE WHEN json_extract(analysis,'$.threat_level')='high' THEN 1 ELSE 0 END) as high_cnt
            FROM episodes
            WHERE lean = 'right' AND analysis IS NOT NULL
            GROUP BY podcast_name
            ORDER BY cnt DESC
        """).fetchall()

    topic_counts = {}
    quotes = []
    attacks_all = []
    episode_list = []

    for row in episodes:
        try:
            a = json.loads(row["analysis"])
        except Exception:
            continue
        for t in a.get("key_topics", []):
            topic_counts[t.lower().strip()] = topic_counts.get(t.lower().strip(), 0) + 1
        for q in a.get("notable_quotes", []):
            quotes.append({
                "podcast": row["podcast_name"],
                "title": row["title"],
                "quote": q.get("quote", ""),
                "speaker": q.get("speaker", ""),
                "type": q.get("type", ""),
                "context": q.get("context", ""),
            })
        for atk in a.get("political_attacks", []):
            attacks_all.append({"podcast": row["podcast_name"], "attack": atk,
                                 "threat": a.get("threat_level", "")})
        episode_list.append({
            "podcast": row["podcast_name"],
            "title": row["title"],
            "published": row["published"],
            "synopsis": a.get("synopsis", ""),
            "threat": a.get("threat_level", "low"),
            "topics": a.get("key_topics", [])[:4],
        })

    top_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:15]
    return {
        "episodes": episode_list,
        "per_show": [dict(r) for r in per_show],
        "top_topics": top_topics,
        "quotes": quotes[:20],
        "attacks": attacks_all[:30],
    }
  except Exception:
    return {"episodes": [], "per_show": [], "top_topics": [], "quotes": [], "attacks": []}


def build_right_dashboard_html(data):
    def threat_color(level):
        return {"high": "#e8333c", "medium": "#f0a500", "low": "#2a9d8f"}.get(level, "#888")

    topic_labels = [t[0] for t in data["top_topics"]]
    topic_values = [t[1] for t in data["top_topics"]]
    show_labels = [r["podcast_name"] for r in data["per_show"][:10]]
    show_counts = [r["cnt"] for r in data["per_show"][:10]]
    show_highs = [r["high_cnt"] for r in data["per_show"][:10]]

    quotes_html = ""
    for q in data["quotes"][:12]:
        type_colors = {"attack": "#e8333c", "claim": "#f0a500", "admission": "#2a9d8f",
                       "notable_position": "#2c5f8a", "cross_partisan_signal": "#7c3aed"}
        tc = type_colors.get(q["type"], "#888")
        quotes_html += f"""
        <div class="quote-card">
          <div class="quote-meta">
            <span class="badge" style="background:{tc};">{q['type'].replace('_',' ').upper()}</span>
            <span class="quote-source">{q['podcast']}</span>
          </div>
          <blockquote>"{q['quote']}"</blockquote>
          <div class="quote-speaker">— {q['speaker']}</div>
          {f'<div class="quote-context">{q["context"]}</div>' if q.get("context") else ''}
        </div>"""

    attacks_html = ""
    for a in data["attacks"][:20]:
        tc = threat_color(a["threat"])
        attacks_html += f"""
        <div class="attack-row">
          <span class="badge" style="background:{tc};flex-shrink:0;">{a['threat'].upper()}</span>
          <span class="atk-source">{a['podcast']}</span>
          <span class="atk-text">{a['attack']}</span>
        </div>"""

    episodes_html = ""
    for ep in data["episodes"][:40]:
        tc = threat_color(ep["threat"])
        topics_str = ", ".join(ep["topics"])
        episodes_html += f"""
        <div class="ep-row">
          <div class="ep-header">
            <span class="badge" style="background:{tc};">{ep['threat'].upper()}</span>
            <strong class="ep-show">{ep['podcast']}</strong>
            <span class="ep-date">{ep['published'][:16] if ep['published'] else ''}</span>
          </div>
          <div class="ep-title">"{ep['title']}"</div>
          <div class="ep-synopsis">{ep['synopsis']}</div>
          {f'<div class="ep-topics">Topics: {topics_str}</div>' if topics_str else ''}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Right-Wing Podcast Monitor</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {{
    --navy: #1a3a5c; --red: #c0392b; --bg: #f4f5f7;
    --card: #fff; --border: #e2e5ea; --text: #1a1a2e; --muted: #6b7280;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Georgia, serif; background: var(--bg); color: var(--text); }}
  header {{
    background: #8b0000; color: white; padding: 0 32px;
    display: flex; align-items: center; justify-content: space-between;
    height: 64px; box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    position: sticky; top: 0; z-index: 100;
  }}
  .logo {{ display: flex; align-items: center; gap: 14px; }}
  .logo-mark {{ width: 36px; height: 36px; background: white; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 18px; color: #8b0000; font-family: sans-serif; }}
  .logo-text {{ font-size: 15px; font-weight: bold; }}
  .logo-sub {{ font-size: 11px; color: #ffaaaa; letter-spacing: 1px; text-transform: uppercase; }}
  .nav-link {{ color: #ffcccc; font-family: sans-serif; font-size: 13px; text-decoration: none; }}
  .nav-link:hover {{ color: white; }}
  .main {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
  .stats-strip {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--card); border-radius: 10px; padding: 20px;
    border: 1px solid var(--border); text-align: center; }}
  .stat-number {{ font-size: 36px; font-weight: bold; color: #8b0000; line-height: 1; }}
  .stat-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 1px; margin-top: 6px; font-family: sans-serif; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .panel {{ background: var(--card); border-radius: 10px; border: 1px solid var(--border);
    overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }}
  .panel-header {{ padding: 16px 20px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px; }}
  .panel-title {{ font-size: 14px; font-weight: bold; color: var(--navy);
    font-family: sans-serif; }}
  .panel-subtitle {{ font-size: 11px; color: var(--muted); font-family: sans-serif; }}
  .panel-body {{ padding: 20px; }}
  .scrollable {{ max-height: 500px; overflow-y: auto; }}
  canvas {{ max-height: 260px; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 10px; color: white;
    font-family: sans-serif; font-weight: bold; letter-spacing: 0.5px; }}
  .section-label {{ font-family: sans-serif; font-size: 11px; text-transform: uppercase;
    letter-spacing: 2px; color: var(--muted); margin: 28px 0 12px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
  /* Quotes */
  .quote-card {{ border-left: 3px solid #8b0000; padding: 14px 16px; margin-bottom: 14px;
    background: #fff8f8; border-radius: 0 6px 6px 0; }}
  .quote-meta {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
  .quote-source {{ font-family: sans-serif; font-size: 12px; color: var(--muted); }}
  blockquote {{ font-style: italic; line-height: 1.6; font-size: 14px; margin-bottom: 6px; }}
  .quote-speaker {{ font-family: sans-serif; font-size: 12px; color: #8b0000; font-weight: bold; }}
  .quote-context {{ font-family: sans-serif; font-size: 12px; color: var(--muted);
    margin-top: 6px; line-height: 1.5; }}
  /* Attacks */
  .attack-row {{ display: flex; align-items: flex-start; gap: 10px; padding: 10px 0;
    border-bottom: 1px solid var(--border); font-size: 13px; }}
  .attack-row:last-child {{ border-bottom: none; }}
  .atk-source {{ font-family: sans-serif; color: var(--muted); font-size: 11px;
    flex-shrink: 0; min-width: 130px; margin-top: 2px; }}
  .atk-text {{ line-height: 1.5; }}
  /* Episodes */
  .ep-row {{ padding: 14px 0; border-bottom: 1px solid var(--border); }}
  .ep-row:last-child {{ border-bottom: none; }}
  .ep-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-family: sans-serif; }}
  .ep-show {{ font-size: 13px; }}
  .ep-date {{ font-size: 11px; color: var(--muted); margin-left: auto; }}
  .ep-title {{ font-size: 13px; color: #8b0000; margin-bottom: 4px; }}
  .ep-synopsis {{ font-size: 13px; line-height: 1.6; color: #333; }}
  .ep-topics {{ font-family: sans-serif; font-size: 11px; color: var(--muted); margin-top: 4px; }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-mark">R</div>
    <div>
      <div class="logo-text">Right-Wing Podcast Monitor</div>
      <div class="logo-sub">Political Media Intelligence</div>
    </div>
  </div>
  <nav style="display:flex;gap:20px;align-items:center;">
    <a href="/" class="nav-link">Overview</a>
    <a href="/right" class="nav-link" style="color:white;font-weight:bold;border-bottom:2px solid #ff6666;padding-bottom:2px;">Right-Wing</a>
    <a href="/campaign" class="nav-link">🎯 Campaign Intel</a>
  </nav>
</header>

<div class="main">
  <div class="stats-strip">
    <div class="stat-card">
      <div class="stat-number">{len(data['episodes'])}</div>
      <div class="stat-label">Episodes Tracked</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">{len(data['per_show'])}</div>
      <div class="stat-label">Shows Monitored</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">{sum(1 for e in data['episodes'] if e['threat']=='high')}</div>
      <div class="stat-label">High Threat Episodes</div>
    </div>
    <div class="stat-card">
      <div class="stat-number">{len(data['quotes'])}</div>
      <div class="stat-label">Notable Quotes Captured</div>
    </div>
  </div>

  <p class="section-label">Volume &amp; Topics</p>
  <div class="grid-2">
    <div class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Episodes by Show</div>
        <div class="panel-subtitle">Top 10 most active right-wing podcasts</div></div>
      </div>
      <div class="panel-body"><canvas id="showChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Top Topics (Right-Wing Only)</div>
        <div class="panel-subtitle">Most discussed subjects</div></div>
      </div>
      <div class="panel-body"><canvas id="topicsChart"></canvas></div>
    </div>
  </div>

  <p class="section-label">Notable Quotes &amp; Clips</p>
  <div class="panel">
    <div class="panel-header">
      <div><div class="panel-title">Clippable Moments</div>
        <div class="panel-subtitle">Quotes flagged for political significance</div></div>
    </div>
    <div class="panel-body scrollable">
      {quotes_html or '<p style="color:#888;font-family:sans-serif;font-size:13px;">No quotes yet — run analyzer with full transcripts for rich extraction.</p>'}
    </div>
  </div>

  <p class="section-label">Attack Feed</p>
  <div class="panel">
    <div class="panel-header">
      <div><div class="panel-title">Political Attacks on Democrats &amp; Progressives</div>
        <div class="panel-subtitle">All attacks extracted from right-wing episodes</div></div>
    </div>
    <div class="panel-body scrollable">
      {attacks_html or '<p style="color:#888;font-family:sans-serif;font-size:13px;">No attacks found yet.</p>'}
    </div>
  </div>

  <p class="section-label">Episode Rundown</p>
  <div class="panel">
    <div class="panel-header">
      <div><div class="panel-title">All Right-Wing Episodes</div>
        <div class="panel-subtitle">Most recent first</div></div>
    </div>
    <div class="panel-body scrollable">
      {episodes_html or '<p style="color:#888;font-family:sans-serif;font-size:13px;">No episodes yet.</p>'}
    </div>
  </div>
</div>

<script>
new Chart(document.getElementById('showChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(show_labels)},
    datasets: [
      {{ label: 'Episodes', data: {json.dumps(show_counts)}, backgroundColor: 'rgba(139,0,0,0.7)', borderRadius: 4 }},
      {{ label: 'High Threat', data: {json.dumps(show_highs)}, backgroundColor: 'rgba(232,51,60,0.4)', borderRadius: 4 }},
    ]
  }},
  options: {{
    responsive: true, indexAxis: 'y',
    plugins: {{ legend: {{ position: 'bottom' }} }},
    scales: {{ x: {{ beginAtZero: true }} }},
  }}
}});
new Chart(document.getElementById('topicsChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(topic_labels)},
    datasets: [{{ data: {json.dumps(topic_values)}, backgroundColor: '#8b0000', borderRadius: 4 }}]
  }},
  options: {{
    responsive: true, indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ beginAtZero: true }} }},
  }}
}});
</script>
</body></html>"""


# ── HTTP Server ────────────────────────────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/right":
            data = get_right_wing_data()
            html = build_right_dashboard_html(data)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        elif parsed.path == "/campaign":
            qs = parse_qs(parsed.query)
            hours = int(qs.get("hours", ["72"])[0])
            episodes = get_campaign_intelligence(hours=hours)
            html = build_campaign_html(episodes, hours=hours)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        elif parsed.path == "/" or parsed.path == "/dashboard":
            stats = get_stats()
            topics = get_trending_topics()
            volume = get_daily_volume()
            attacks = get_attacks()
            opps = get_opportunities()
            html = build_dashboard_html(stats, topics, volume, attacks, opps)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        elif parsed.path == "/api/stats":
            stats = get_stats()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())

        elif parsed.path == "/api/topics":
            topics = get_trending_topics()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(topics).encode())
        
        else:
            self.send_response(404)
            self.end_headers()

def main():
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", 8080)))
    print(f"\n🎙  Podcast Intelligence Dashboard")
    print(f"    http://localhost:{port}")
    print(f"    Press Ctrl+C to stop\n")
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")

if __name__ == "__main__":
    main()

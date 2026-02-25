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
            rows = conn.execute("""
                SELECT podcast_name, title, published,
                       json_extract(analysis, '$.political_attacks') as attacks,
                       json_extract(analysis, '$.threat_level') as threat_level
                FROM episodes
                WHERE lean = 'right'
                AND analysis IS NOT NULL
                AND fetched_at > datetime('now', '-3 days')
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
    """Messaging opportunities from recent analyses."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT podcast_name, lean,
                       json_extract(analysis, '$.messaging_opportunities') as opps
                FROM episodes
                WHERE analysis IS NOT NULL
                AND fetched_at > datetime('now', '-3 days')
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
        return results[:25]
    except Exception:
        return []

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
  <div class="header-right">
    <span class="live-dot"></span>
    Last updated: {datetime.now().strftime('%b %d, %Y %H:%M')} UTC
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
  <a href="/" class="nav-link">← All Podcasts</a>
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

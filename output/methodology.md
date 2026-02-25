# Podcast Intelligence Monitor — Methodology & User Guide

## What This Tool Does

The Podcast Intelligence Monitor automatically tracks 150 political and news podcasts across the ideological spectrum, reads their content, and uses AI to identify what narratives are being pushed, what attacks are being made against progressive causes, and where there are opportunities to go on offense. It runs daily and delivers both an email digest and an interactive dashboard.

---

## How the Pipeline Works

### Step 1: RSS Feed Fetching
Every day the system pulls the latest episodes from RSS feeds — the standard format podcasts use to publish new content. For each episode, we capture the title, publish date, episode description, and any links.

### Step 2: Transcript Retrieval
This is where we go beyond just reading show descriptions:

- **YouTube transcripts (primary):** For shows with known YouTube channels (Ben Shapiro, Tucker Carlson, Dan Bongino, Mark Levin, etc.), we search the channel for a matching video and pull the auto-generated transcript. These transcripts contain the actual spoken words of the show — everything said by hosts and guests, in order, with speaker context. A single episode can yield 80,000+ words of real content.

- **RSS description (fallback):** For shows without YouTube channels, or when the YouTube transcript isn't available, we use the episode's RSS description — typically 200–500 words written by the show's producers. This is less rich than a transcript but still captures key topics and framing.

**Why transcripts matter:** An RSS description for a Tucker Carlson episode might say "Tucker discusses immigration and the economy." The actual transcript reveals the specific claims made, the exact framing used, what Democrats were attacked by name, and what emotional arguments were deployed. That specificity is what makes analysis actionable.

### Step 3: Claude AI Analysis
Each episode's content is sent to Claude (Anthropic's AI) with a structured prompt. Claude returns a JSON analysis covering:

- **Synopsis:** 2–3 sentence summary of what the episode covered
- **Key topics:** The main issues discussed (e.g., "immigration," "DEI," "January 6")
- **Notable quotes:** Direct quotes suitable for clipping or sharing — things hosts actually said that are newsworthy or illustrative
- **Political attacks:** Specific attacks made against Democrats, progressives, or progressive causes — by name where possible
- **Narrative themes:** The overarching frames being pushed (e.g., "Democrats are destroying the economy," "crime is out of control in blue cities")
- **Messaging opportunities:** Issues where the right-wing framing creates an opening for progressive counterattack
- **Threat level:** Low, medium, or high (explained below)
- **Threat rationale:** Why that threat level was assigned

### Step 4: Digest & Dashboard Generation
Analyses are aggregated into:
- A **daily HTML email digest** with top threats, attacks, and messaging opportunities grouped by ideological lean
- An **interactive web dashboard** with charts and searchable episode feeds

---

## What "Threat Level" Means

Threat level rates how dangerous a given episode is to **progressive causes and Democratic political standing**, on a three-point scale:

| Level | Meaning |
|-------|---------|
| **Low** | Episode covers topics with little direct impact on progressive causes — general news, entertainment segments, topics where the framing isn't particularly harmful |
| **Medium** | Episode pushes narratives or attacks that could gain traction — active criticism of Democratic policies, framing that could hurt progressives in polling, topics that may spread to mainstream media |
| **High** | Episode poses a direct threat — a coordinated attack on a specific Democrat or progressive cause, a viral-ready claim that's false or misleading, a narrative that's already gaining mainstream pickup, or content that could directly affect electoral outcomes |

**Important context:** Threat level is assessed from the perspective of progressive political interests. A "high threat" episode isn't necessarily one that's more extreme — it's one that's more *effective* at shifting opinion or creating political damage. A calm, reasonable-sounding critique of a Democratic policy can be rated higher threat than an unhinged conspiracy rant that only appeals to a narrow audience.

---

## Dashboard Panels Explained

### Main Dashboard (`localhost:8080`)

**Episode Volume Trends (line chart, top left)**
Shows how many new episodes were published per day over the last 14 days, broken out by political lean (left, right, neutral). Useful for spotting when a particular lane is ramping up output — a surge in right-wing episodes often precedes a coordinated messaging push.

**Trending Topics (bar chart, top right)**
The most-discussed topics across all podcasts in the last 7 days, ranked by episode count. This shows what the entire podcasting ecosystem is focused on. When the same topic appears across left, right, and neutral pods simultaneously, it's usually being driven by a real news event. When it appears only on right-wing shows, it may be an emerging narrative that hasn't broken through yet.

**Threat Level Distribution (doughnut chart, bottom left)**
The breakdown of episode analyses by threat level (low/medium/high) over the past 30 days. A rising share of "high" threat episodes indicates an escalating coordinated messaging campaign.

**Right-Wing Attacks Feed (bottom center)**
A real-time feed of specific political attacks identified in right-wing episodes over the last 72 hours. These are direct quotes and paraphrases of attacks made against Democrats, progressives, and liberal institutions — pulled from episode transcripts and descriptions. Useful for rapid response teams who need to know what's being said before it spreads.

**Messaging Opportunities Feed (bottom right)**
Episodes where Claude identified issues or framings that create openings for progressive counterattack. For example: if right-wing shows spend a week attacking Biden's economy but polls show voters trust Democrats more on healthcare, that's a messaging opportunity — they're playing on weak ground.

---

### Right-Wing Dashboard (`localhost:8080/right`)

A dedicated view focused exclusively on right-of-center podcasts. Panels include:

**Stats Strip (top)**
- Total right-wing episodes in the database
- Number of distinct shows being monitored
- Count of high-threat episodes in the last 7 days
- Total notable quotes captured

**Episodes by Show (bar chart)**
Which shows are producing the most content. Volume matters: a show publishing 5 episodes a week reaches its audience far more than one that publishes monthly.

**Top Topics (bar chart)**
The most-discussed topics specifically within right-wing media — useful for spotting emerging narratives before they cross over to mainstream coverage.

**Notable Quotes / Clippable Moments**
Direct quotes from episode transcripts that are newsworthy, illustrative of the movement's current messaging, or suitable for use in rapid response, research, or press work. These are drawn from real transcripts, not paraphrased summaries.

**Attack Feed**
Specific attacks on Democrats and progressives — who is being targeted, on what issue, and with what framing. Organized by recency.

**Full Episode Rundown**
Every analyzed right-wing episode with its full AI-generated summary, threat level, topics, and source link.

---

## Podcast Coverage

| Lean | Count | Examples |
|------|-------|---------|
| Left / Progressive | 50 | Pod Save America, The Daily (NYT), Ezra Klein, MeidasTouch, Majority Report, Democracy Now! |
| Right / Conservative | ~30–50 | Ben Shapiro, Tucker Carlson, Dan Bongino, Mark Levin, Charlie Kirk, Megyn Kelly, War Room (Bannon) |
| Neutral / Center | 50 | NPR Politics, The Dispatch, Bulwark, WSJ's The Journal, Lawfare, Pivot, Honestly (Bari Weiss) |

Note: The right-wing count fluctuates as shows move between hosting platforms and RSS feeds change. The list is actively maintained.

---

## What Claude Is — and Isn't — Doing

Claude does not have political opinions and is not programmed to label things conservative or liberal based on the outlet. The threat assessment is explicitly framed from a progressive political standpoint in the system prompt — Claude is told to evaluate episodes for their potential impact on Democratic causes. This means:

- A factually accurate criticism of a Democratic policy can still be rated "high threat"
- A false or misleading claim might be rated "low threat" if it's unlikely to gain mainstream traction
- The system is a **political intelligence tool**, not a fact-checking tool

Claude can make mistakes — especially on partial information (RSS descriptions vs. full transcripts). Analysis quality is significantly better when transcripts are available.

---

## Data Freshness

- Episodes are fetched daily (or on-demand)
- Analysis runs immediately after fetching
- The dashboard reflects whatever is currently in the database
- The digest covers the previous 24–48 hours of new episodes

---

## Limitations

1. **Transcript availability:** Not all shows have YouTube channels. Shows without transcripts are analyzed on RSS descriptions only, which limits depth.
2. **Paywalled content:** Some shows (e.g., certain Bongino content) appear to have paywalled YouTube transcripts that return no text.
3. **RSS feed stability:** Right-wing shows in particular frequently move between hosting platforms, breaking feed URLs. The list requires periodic maintenance.
4. **AI accuracy:** Claude produces high-quality summaries but can mischaracterize nuanced content, especially in short descriptions. Always verify notable quotes against source material before external use.
5. **No audio analysis:** We are not transcribing audio directly — we rely on YouTube auto-captions or RSS descriptions. Live-to-tape shows with no YouTube presence are analyzed on description text only.

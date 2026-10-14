# Google Trends Scraper — Reliable, With Insights, Regions, Related & Trending Now

Get Google Trends data for any keyword in one run: **interest over time**, **interest by country, region, city or US metro**, **related queries and topics (top, rising and breakout)** and **today's trending searches** — plus **ready-made insights**: the peak, the latest value, recent momentum, where it's most popular and which searches are breaking out, written up in plain English.

## Why this one

- **Built for Google's rate limits.** When Google pushes back, the Actor switches to a fresh residential IP and cookie, backs off and retries — up to 8 times per request by default. Warm sessions are reused across keywords, so long lists stay fast and cheap.
- **Answers, not just numbers.** Every report includes `insights` (peak, latest, % change, direction, top regions, breakout searches) and a `summary` paragraph — ideal for dashboards, newsletters and AI agents.
- **Many countries in one run.** List `US`, `DE`, `GB`, `US-CA`… and get a report for each.
- **Spreadsheet mode.** Switch output to flat rows for clean CSV / Excel / Google Sheets exports.
- **Never pay for broken data.** Incomplete reports are delivered free, marked with `missingSections`.
- **Respects your spending limit** and stops cleanly when it's reached.

## What you get

| Data | What it tells you |
|---|---|
| Interest over time | Search interest (0–100) for every hour, day, week or month in the period |
| Insights & summary | Average, peak date, latest value, recent change %, rising / falling / stable |
| Interest by region | Where each term is searched most: countries, regions/states, cities or US DMAs |
| Related queries | Top and rising searches made alongside each term; "Breakout" = growth above 5000% |
| Related topics | Top and rising topics, with Google Knowledge Graph IDs |
| Trending now | Today's trending searches per country, with traffic estimates and news links |

Works for web, image, news, Google Shopping and YouTube search, every category, and any period from the past hour to 2004–today.

## How to use it

1. Add **search terms** (plain keywords, or topic IDs such as `/m/05p0rrx`).
2. Add **countries or regions**, or leave empty for worldwide.
3. Pick a **time range**.
4. Turn on **Compare terms** to put up to 5 terms on one shared 0–100 scale, or leave it off for a separate report per term.
5. Optionally add countries under **Trending now**.
6. Run. Each report shows its title and summary in the **Reports** view; open JSON for every data point. A run summary is saved as `OUTPUT` in the key-value store.

### Example input

```json
{
  "searchTerms": ["bitcoin", "ethereum", "solana"],
  "compareTerms": true,
  "geos": ["US", "DE"],
  "timeRange": "today 12-m",
  "trendingNowGeos": ["US", "GB"]
}
```

### Example output (keyword report, shortened)

```json
{
  "type": "keywordReport",
  "title": "bitcoin vs ethereum · US · The past 12 months",
  "summary": "Most searched overall: \"bitcoin\". \"bitcoin\": averaged 61/100 in US over the past 12 months, peaking on 2025-11-09; latest value 58 (up 21% in the most recent quarter of the period: rising). Most popular in Nevada, Wyoming, Colorado. Breakout searches: bitcoin etf options. …",
  "insights": {
    "bitcoin": {
      "averageInterest": 61.4,
      "peak": { "date": "2025-11-09T00:00:00Z", "value": 100 },
      "latest": { "date": "2026-10-04T00:00:00Z", "value": 58 },
      "recentChangePercent": 21.0,
      "direction": "rising",
      "topRegions": [{ "name": "Nevada", "value": 100 }],
      "topQueries": ["bitcoin price", "btc"],
      "risingQueries": [{ "query": "bitcoin etf options", "growth": "Breakout" }],
      "breakoutQueries": ["bitcoin etf options"],
      "topTopics": ["Bitcoin", "Cryptocurrency"]
    }
  },
  "searchTerms": ["bitcoin", "ethereum"],
  "geo": "US",
  "timeRange": "today 12-m",
  "interestOverTime": [
    { "date": "2025-10-12T00:00:00Z", "formattedTime": "Oct 12 – 18, 2025", "values": { "bitcoin": 58, "ethereum": 17 }, "isPartial": false }
  ],
  "averageInterest": { "bitcoin": 61.4, "ethereum": 18.2 },
  "regionResolution": "REGION",
  "interestByRegion": [{ "geoCode": "US-NV", "geoName": "Nevada", "values": { "bitcoin": 100, "ethereum": 31 } }],
  "relatedQueries": {
    "bitcoin": {
      "top": [{ "query": "bitcoin price", "value": 100, "formattedValue": "100", "link": "https://trends.google.com/trends/explore?..." }],
      "rising": [{ "query": "bitcoin etf options", "value": 5000, "formattedValue": "Breakout", "link": "..." }]
    }
  },
  "relatedTopics": { "bitcoin": { "top": [{ "topic": "Bitcoin", "topicType": "Currency", "topicId": "/m/05p0rrx", "value": 100 }], "rising": [] } },
  "trendsUrl": "https://trends.google.com/trends/explore?date=today+12-m&q=bitcoin%2Cethereum&geo=US",
  "scrapedAt": "2026-10-14T13:00:00Z"
}
```

### Spreadsheet rows

With **Output format → Spreadsheet rows**, each report becomes flat rows with a `type` column: `termSummary` (one per term, with the insights), `timelinePoint`, `regionInterest`, `relatedQuery` and `relatedTopic`. Export as CSV or Excel and filter by `type`. The price is the same as one report.

### Trending now

```json
{
  "type": "trendingSearch",
  "geo": "US",
  "rank": 1,
  "title": "world cup",
  "approxTraffic": "2M+",
  "approxTrafficNumber": 2000000,
  "publishedAt": "2026-10-14T15:00:00Z",
  "news": [{ "title": "Final tonight", "url": "https://...", "source": "BBC" }]
}
```

## Use it from AI agents

Connect it through the Apify MCP server and ask your agent things like *"How has interest in heat pumps changed in Germany over the past 5 years, and what are the breakout searches?"* The `summary` and `insights` fields give the agent the answer directly, without crunching 260 data points.

## Use it from code

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_API_TOKEN")
run = client.actor("YOUR_USERNAME/google-trends-reliable").call(run_input={
    "searchTerms": ["electric bike"],
    "geos": ["US", "GB"],
    "timeRange": "today 5-y",
})
for report in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(report["title"], "→", report["summary"])
```

## Pricing

You pay per **keyword report** — one per term, or one per comparison group, per location — and per **trending search**. Proxy and compute costs are included. Partial reports are free. Set a maximum cost per run and the Actor stops cleanly when it's reached.

## Tips

- **Values are relative.** 100 is the peak for the chosen terms, place and period — not a search count. Turn on Compare terms to put several terms on the same scale.
- **"Breakout"** means the query grew by more than 5000% in the period.
- **Low-volume terms** can return empty data; the summary will say so. That's Google not having enough data, not an error.
- **Partial periods:** the last point is often incomplete (`isPartial: true`) and is left out of the insights.
- **Use residential proxies** (the default). Datacenter IPs get rate-limited quickly.

## FAQ

**How many keywords can I run at once?** As many as you like — they're processed in parallel sessions (3 by default, up to 10).

**Can I compare more than 5 terms?** Google allows 5 per comparison; longer lists are split into groups of 5, each with its own scale.

**Which category IDs exist?** Open Google Trends, pick a category, and copy the number after `cat=` in the URL.

**Is it legal?** The Actor reads publicly available, aggregated, non-personal statistics from Google Trends. Check Google's terms and the rules that apply to your use.

## Feedback

Found a problem or want another field? Open an issue on the Issues tab — fixes usually ship within days.

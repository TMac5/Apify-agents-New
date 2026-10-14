# Launch checklist (for you, not the Store page)

## 1. Update and build
Replace the changed files on GitHub, then click **Build** in Apify. When it's done, open the **Input** tab: the form will have new fields (Countries or regions, Output format…). Click **Reset input** if old fields linger.

## 2. Test runs (aim: every one succeeds)
| # | Input | What to check |
|---|---|---|
| 1 | Default input | 2 reports; Reports view shows title + summary |
| 2 | Terms: `heat pump`; Countries: `US`, `DE`, `GB` | 3 reports, one per country |
| 3 | Countries: `US-CA`; Region breakdown: Automatic | interestByRegion lists cities |
| 4 | 4 terms, Compare on | 1 report, "Most searched overall" in summary |
| 5 | Time range Past 7 days | hourly-ish dates in interestOverTime |
| 6 | Custom `2024-01-01 2024-06-30` | dates only in that range |
| 7 | Trending now: `US`, `GB`, `DE` (no terms) | ~10–20 items per country |
| 8 | Term `lithuanian beekeeping` | summary says not enough search volume, run still succeeds |
| 9 | Output format: Spreadsheet rows | "Spreadsheet rows" view filled; export CSV looks clean |
| 10 | 20 terms, Parallel sessions 5 | all 20 reports, check run time and cost |

Write down each run's **cost** (top of the run page) and number of reports. Cost ÷ reports = your cost per report.

## 3. Pricing (Actor → Publication → Monetization → Pay per event)
Event names must match exactly:
| Event name | Charged when | Start price |
|---|---|---|
| `keyword-report` | one complete report (any output format) | $0.015 |
| `trending-search` | one trending search | $0.002 |

Keep Apify's automatic `apify-actor-start` event. Make sure $0.015 is at least 3× your measured cost per report. Apify takes 20%.

## 4. Publish
- Title, description and README are already written. Categories: **SEO tools**, **Marketing**, **AI**.
- Add an icon (a simple chart-line image works).
- Complete **KYC** under Settings → Payouts. Payout minimum: $20 PayPal, $100 bank.
- Keep only one Actor: delete the duplicate before publishing.
- Register your income activity for taxes before the money arrives.

## 5. After launch
- Check the run success rate daily for two weeks; fix failures fast — reliability is the pitch.
- Answer every issue on the Issues tab.
- Post it where Trends users are: SEO / marketing communities, Apify's Discord.

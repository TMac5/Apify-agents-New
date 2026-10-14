"""Turn raw Trends data into ready-to-use insights and a plain-English summary.

This is what makes the output useful to AI agents and busy humans: instead of
52 numbers per term, they get the peak, the latest value, the recent momentum,
where it's popular and which searches are breaking out — plus one readable paragraph.
"""

from __future__ import annotations

from typing import Any

from .trends_api import time_range_label

RISING_THRESHOLD = 15.0  # % change that counts as a real move rather than noise


def _complete_points(rows: list[dict[str, Any]], term: str) -> list[tuple[str, int]]:
    return [(r['date'], r['values'][term]) for r in rows
            if not r.get('isPartial') and r['values'].get(term) is not None]


def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


def term_insights(item: dict[str, Any], term: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    rows = item.get('interestOverTime')
    if rows is not None:
        points = _complete_points(rows, term)
        if points and any(v for _, v in points):
            values = [v for _, v in points]
            peak_value = max(values)
            peak_date = next(d for d, v in points if v == peak_value)
            out['averageInterest'] = round(_mean(values), 1)
            out['peak'] = {'date': peak_date, 'value': peak_value}
            out['latest'] = {'date': points[-1][0], 'value': points[-1][1]}
            n = max(1, len(points) // 4)  # compare the latest quarter of the period with the quarter before
            if len(points) >= 2 * n:
                recent, previous = _mean(values[-n:]), _mean(values[-2 * n:-n])
                if previous > 0:
                    change = round((recent - previous) / previous * 100, 1)
                    out['recentChangePercent'] = change
                    out['changeWindowPoints'] = n
                    out['direction'] = ('rising' if change >= RISING_THRESHOLD
                                        else 'falling' if change <= -RISING_THRESHOLD else 'stable')
        else:
            out['averageInterest'] = 0
            out['direction'] = 'no data'

    regions = item.get('interestByRegion')
    if regions:
        ranked = sorted((r for r in regions if (r['values'].get(term) or 0) > 0),
                        key=lambda r: r['values'][term], reverse=True)
        out['topRegions'] = [{'name': r['geoName'], 'value': r['values'][term]} for r in ranked[:5]]

    queries = (item.get('relatedQueries') or {}).get(term)
    if queries:
        out['topQueries'] = [q['query'] for q in queries['top'][:5]]
        out['risingQueries'] = [{'query': q['query'], 'growth': q['formattedValue']} for q in queries['rising'][:5]]
        out['breakoutQueries'] = [q['query'] for q in queries['rising'] if q.get('formattedValue') == 'Breakout'][:10]

    topics = (item.get('relatedTopics') or {}).get(term)
    if topics:
        out['topTopics'] = [t['topic'] for t in topics['top'][:5] if t.get('topic')]
        out['risingTopics'] = [{'topic': t['topic'], 'growth': t['formattedValue']}
                               for t in topics['rising'][:5] if t.get('topic')]
    return out


def _date_only(iso: str) -> str:
    return iso[:16].replace('T', ' ').replace(' 00:00', '') if iso else iso


def term_sentence(term: str, ins: dict[str, Any], period: str, place: str) -> str:
    if ins.get('direction') == 'no data':
        return f'"{term}": not enough search volume {place} over {period} for Google to report data.'
    clauses: list[str] = []
    if 'peak' in ins:
        clause = (f'averaged {ins["averageInterest"]:g}/100 {place} over {period}, '
                  f'peaking on {_date_only(ins["peak"]["date"])}; latest value {ins["latest"]["value"]}')
        change = ins.get('recentChangePercent')
        if change is not None:
            clause += (f' ({"up" if change > 0 else "down"} {abs(change):g}% in the most recent quarter '
                       f'of the period: {ins["direction"]})')
        clauses.append(clause)
    if ins.get('topRegions'):
        clauses.append('most popular in ' + ', '.join(r['name'] for r in ins['topRegions'][:3]))
    if ins.get('breakoutQueries'):
        clauses.append('breakout searches: ' + ', '.join(ins['breakoutQueries'][:5]))
    elif ins.get('risingQueries'):
        clauses.append('rising searches: ' + ', '.join(f'{q["query"]} ({q["growth"]})' for q in ins['risingQueries'][:3]))
    elif ins.get('topQueries'):
        clauses.append('top related searches: ' + ', '.join(ins['topQueries'][:3]))
    if not clauses:
        return f'"{term}": no data collected.'
    return f'"{term}": ' + '. '.join(c[0].upper() + c[1:] if i else c for i, c in enumerate(clauses)) + '.'


def add_insights(item: dict[str, Any]) -> None:
    """Add `title`, `insights` (per term) and `summary` to a keyword report in place."""
    terms = item['searchTerms']
    period = time_range_label(item['timeRange'])
    worldwide = item['geo'] == 'WORLDWIDE'
    place = 'worldwide' if worldwide else f'in {item["geo"]}'
    item['title'] = f'{" vs ".join(terms)} · {"Worldwide" if worldwide else item["geo"]} · {period[0].upper() + period[1:]}'
    item['insights'] = {term: term_insights(item, term) for term in terms}
    sentences = []
    for t in terms:
        item['insights'][t]['summary'] = term_sentence(t, item['insights'][t], period, place)
        sentences.append(item['insights'][t]['summary'])
    if len(terms) > 1 and all(item['insights'][t].get('averageInterest') is not None for t in terms):
        leader = max(terms, key=lambda t: item['insights'][t]['averageInterest'])
        sentences.insert(0, f'Most searched overall: "{leader}".')
    if item.get('missingSections'):
        sentences.append('Note: ' + ', '.join(item['missingSections']) + ' could not be collected for this report.')
    item['summary'] = ' '.join(sentences)

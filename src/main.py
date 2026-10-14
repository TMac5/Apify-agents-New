"""Google Trends Actor: interest over time, by region, related queries/topics, insights, and trending now."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from apify import Actor

from .insights import add_insights
from .trends_api import (
    EMBED_URL,
    GEO_URL,
    RELATED_URL,
    SEARCH_PROPERTIES,
    TIMESERIES_URL,
    TRENDING_RSS_URL,
    InputError,
    build_explore_params,
    build_widget_params,
    chunk_terms,
    explore_url,
    extract_embed_tokens,
    is_over_quota,
    normalize_geo,
    parse_geo,
    parse_related,
    parse_timeline,
    parse_trending_rss,
    resolve_resolution,
    timeline_averages,
    validate_time_range,
)
from .trends_client import (
    ContentBlocked,
    TransportFactory,
    TrendsBlockedError,
    TrendsClient,
    TrendsRequestError,
    curl_cffi_transport_factory,
)

REPORT_EVENT = 'keyword-report'
TRENDING_EVENT = 'trending-search'
TZ = 0  # request UTC so timestamps are unambiguous

SECTIONS = ('interestOverTime', 'interestByRegion', 'relatedQueries', 'relatedTopics')
SECTION_ERRORS = (TrendsBlockedError, TrendsRequestError, KeyError, ValueError, TypeError, IndexError)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


# ----------------------------------------------------------------------------- input


class Settings:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.terms: list[str] = [t for t in (raw.get('searchTerms') or []) if isinstance(t, str) and t.strip()]
        self.compare: bool = bool(raw.get('compareTerms', False))

        geos_raw = raw.get('geos')
        if not geos_raw and raw.get('geo') is not None:  # older single-location input
            geos_raw = [raw.get('geo')]
        geos: list[str] = []
        for g in geos_raw or ['']:
            code = normalize_geo(g)
            if code not in geos:
                geos.append(code)
        self.geos: list[str] = geos or ['']

        preset = raw.get('timeRange') or 'today 12-m'
        custom = (raw.get('customTimeRange') or '').strip()
        if preset == 'custom' and not custom:
            raise InputError('Time range is set to Custom, but no custom range was given (e.g. "2024-01-01 2024-12-31").')
        self.time_range: str = validate_time_range(custom if preset == 'custom' else preset)
        self.category: int = int(raw.get('category') or 0)
        prop = raw.get('searchProperty') or 'web'
        self.search_property: str = '' if prop == 'web' else prop
        if self.search_property not in SEARCH_PROPERTIES:
            raise InputError(f'Unknown search type "{prop}".')

        self.sections: list[str] = [name for name, flag in zip(SECTIONS, (
            raw.get('includeInterestOverTime', True),
            raw.get('includeInterestByRegion', True),
            raw.get('includeRelatedQueries', True),
            raw.get('includeRelatedTopics', True),
        )) if flag]
        self.resolution_choice: str = raw.get('regionResolution') or 'AUTO'
        self.low_volume: bool = bool(raw.get('includeLowVolumeRegions', False))
        self.output_format: str = raw.get('outputFormat') or 'reports'
        if self.output_format not in ('reports', 'rows'):
            raise InputError(f'Unknown output format "{self.output_format}".')

        self.trending_geos: list[str] = []
        for g in raw.get('trendingNowGeos') or []:
            code = normalize_geo(g)
            if not code or '-' in code:
                raise InputError(f'Trending now needs two-letter country codes such as US or DE; got "{g}".')
            if code not in self.trending_geos:
                self.trending_geos.append(code)

        self.hl: str = raw.get('language') or 'en-US'
        self.max_attempts: int = max(1, min(20, int(raw.get('maxAttemptsPerRequest') or 8)))
        self.concurrency: int = max(1, min(10, int(raw.get('maxConcurrency') or 3)))

        if not self.terms and not self.trending_geos:
            raise InputError('Add at least one search term, or at least one country under "Trending now".')
        if self.terms and not self.sections:
            raise InputError('Turn on at least one data type (interest over time, by region, related queries or topics).')

    def resolution_for(self, geo: str) -> tuple[str, str | None]:
        return resolve_resolution(geo, self.resolution_choice)


class Job:
    def __init__(self, terms: list[str], geo: str, resolution: str) -> None:
        self.terms, self.geo, self.resolution = terms, geo, resolution

    def label(self) -> str:
        return f'{" vs ".join(self.terms)} [{self.geo or "worldwide"}]'


# ----------------------------------------------------------------------------- fetching


async def get_widget(client: TrendsClient, s: Settings, widget: str, terms: list[str], geo: str) -> dict[str, Any] | None:
    """Get the token for one embed widget (TIMESERIES, GEO_MAP, RELATED_QUERIES, RELATED_TOPICS).

    Google's embed pages carry the same tokens as the old /api/explore endpoint, which now
    answers HTTP 500 to scripted requests. A page without a token, or an "over quota" token,
    means this IP is being refused, so the client rotates and retries.
    """
    holder: dict[str, Any] = {}

    def validate(html: str) -> None:
        try:
            token = extract_embed_tokens(html)[0]
        except ValueError as exc:
            raise ContentBlocked(f'embed page without token: {html[:120]!r}') from exc
        if is_over_quota(token):
            raise ContentBlocked('embed quota exceeded for this IP')
        holder['token'] = token

    params = build_explore_params(terms, geo, s.time_range, s.category, s.search_property, s.hl, TZ)
    await client.get_text(f'{EMBED_URL}/{widget}', params, validate=validate)
    return holder.get('token')


async def fetch_section(client: TrendsClient, s: Settings, job: Job, name: str) -> Any:
    terms, geo = job.terms, job.geo
    if name == 'interestOverTime':
        w = await get_widget(client, s, 'TIMESERIES', terms, geo)
        return parse_timeline(await client.get_json(TIMESERIES_URL, build_widget_params(w, s.hl, TZ)), terms) if w else []
    if name == 'interestByRegion':
        w = await get_widget(client, s, 'GEO_MAP', terms, geo)
        if not w:
            return []
        w['request']['resolution'] = job.resolution
        w['request']['includeLowSearchVolumeGeos'] = s.low_volume
        return parse_geo(await client.get_json(GEO_URL, build_widget_params(w, s.hl, TZ)), terms)
    widget, kind = ('RELATED_QUERIES', 'queries') if name == 'relatedQueries' else ('RELATED_TOPICS', 'topics')
    result: dict[str, Any] = {}
    for term in terms:  # related data is per term, so ask for each term on its own
        w = await get_widget(client, s, widget, [term], geo)
        result[term] = (parse_related(await client.get_json(RELATED_URL, build_widget_params(w, s.hl, TZ)), kind)
                        if w else {'top': [], 'rising': []})
    return result


async def fetch_sections(client: TrendsClient, s: Settings, job: Job, names: list[str]) -> tuple[dict[str, Any], list[str]]:
    parts: dict[str, Any] = {}
    missing: list[str] = []
    for name in names:
        try:
            parts[name] = await fetch_section(client, s, job, name)
        except SECTION_ERRORS as exc:
            Actor.log.warning(f'{job.label()}: could not get {name} yet: {exc}')
            missing.append(name)
    return parts, missing


def build_report(s: Settings, job: Job, parts: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        'type': 'keywordReport',
        'title': '',
        'summary': '',
        'searchTerms': job.terms,
        'geo': job.geo or 'WORLDWIDE',
        'timeRange': s.time_range,
        'category': s.category,
        'searchProperty': s.search_property or 'web',
    }
    if 'interestOverTime' in parts:
        item['interestOverTime'] = parts['interestOverTime']
        item['averageInterest'] = timeline_averages(parts['interestOverTime'], job.terms)
    if 'interestByRegion' in parts:
        item['regionResolution'] = job.resolution
        item['interestByRegion'] = parts['interestByRegion']
    for key in ('relatedQueries', 'relatedTopics'):
        if key in parts:
            item[key] = parts[key]
    if missing:
        item['missingSections'] = missing
    add_insights(item)
    item['trendsUrl'] = explore_url(job.terms, job.geo, s.time_range, s.category, s.search_property)
    item['scrapedAt'] = now_iso()
    return item


def report_to_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a report into spreadsheet-friendly rows (one per term × date / region / query / topic)."""
    base = {'geo': item['geo'], 'timeRange': item['timeRange']}
    extra = {'missingSections': item['missingSections']} if item.get('missingSections') else {}
    rows: list[dict[str, Any]] = []
    for term in item['searchTerms']:
        ins = item['insights'].get(term, {})
        rows.append({
            'type': 'termSummary', 'term': term, **base,
            'averageInterest': ins.get('averageInterest'),
            'peakDate': (ins.get('peak') or {}).get('date'), 'peakValue': (ins.get('peak') or {}).get('value'),
            'latestValue': (ins.get('latest') or {}).get('value'),
            'recentChangePercent': ins.get('recentChangePercent'), 'direction': ins.get('direction'),
            'summary': ins.get('summary'),
            'trendsUrl': item['trendsUrl'], **extra,
        })
        for point in item.get('interestOverTime', []):
            rows.append({'type': 'timelinePoint', 'term': term, **base, 'date': point['date'],
                         'formattedTime': point['formattedTime'], 'value': point['values'].get(term),
                         'isPartial': point['isPartial']})
        for region in item.get('interestByRegion', []):
            rows.append({'type': 'regionInterest', 'term': term, **base, 'regionResolution': item.get('regionResolution'),
                         'regionCode': region.get('geoCode'), 'regionName': region['geoName'],
                         'value': region['values'].get(term)})
        for key, row_type, name_key in (('relatedQueries', 'relatedQuery', 'query'), ('relatedTopics', 'relatedTopic', 'topic')):
            lists = (item.get(key) or {}).get(term) or {}
            for list_name in ('top', 'rising'):
                for rank, entry in enumerate(lists.get(list_name, []), start=1):
                    row = {'type': row_type, 'term': term, **base, 'list': list_name, 'rank': rank,
                           name_key: entry.get(name_key), 'value': entry.get('value'),
                           'formattedValue': entry.get('formattedValue'), 'link': entry.get('link')}
                    if row_type == 'relatedTopic':
                        row['topicType'], row['topicId'] = entry.get('topicType'), entry.get('topicId')
                    rows.append(row)
    for row in rows:
        row['scrapedAt'] = item['scrapedAt']
    return rows


# ----------------------------------------------------------------------------- output & charging


class Stats:
    def __init__(self, total_jobs: int) -> None:
        self.total_jobs = total_jobs
        self.reports = 0
        self.partial = 0
        self.trending = 0
        self.failed: list[dict[str, Any]] = []
        self.limit_reached = False

    @property
    def jobs_done(self) -> int:
        return self.reports + self.partial + len([f for f in self.failed if 'searchTerms' in f])


def limit_reached_for(event: str, stats: Stats) -> bool:
    if stats.limit_reached:
        return True
    try:
        if Actor.get_charging_manager().is_event_charge_limit_reached(event):
            stats.limit_reached = True
    except Exception:  # noqa: BLE001 - older SDKs or local runs without pricing
        pass
    return stats.limit_reached


async def push_report(s: Settings, item: dict[str, Any], stats: Stats) -> None:
    complete = not item.get('missingSections')
    if s.output_format == 'reports':
        if complete:
            result = await Actor.push_data(item, charged_event_name=REPORT_EVENT)
            if getattr(result, 'event_charge_limit_reached', False):
                stats.limit_reached = True
        else:
            await Actor.push_data(item)  # partial reports are free
    else:
        if complete:
            result = await Actor.charge(event_name=REPORT_EVENT)
            if getattr(result, 'event_charge_limit_reached', False):
                stats.limit_reached = True
                if getattr(result, 'charged_count', 1) == 0:
                    return  # could not be paid for within the user's limit: don't hand it out
        await Actor.push_data(report_to_rows(item))
    if complete:
        stats.reports += 1
    else:
        stats.partial += 1


# ----------------------------------------------------------------------------- run


async def run(transport_factory: TransportFactory = curl_cffi_transport_factory) -> None:
    async with Actor:
        raw = await Actor.get_input() or {}
        try:
            s = Settings(raw)
        except (InputError, ValueError, TypeError) as exc:
            await Actor.fail(status_message=f'Invalid input: {exc}')
            return

        proxy_cfg = await Actor.create_proxy_configuration(actor_proxy_input=raw.get('proxyConfiguration'))

        async def proxy_url_for(session_id: str) -> str | None:
            return await proxy_cfg.new_url(session_id=session_id) if proxy_cfg else None

        def new_client() -> TrendsClient:
            return TrendsClient(transport_factory, proxy_url_for, hl=s.hl, max_attempts=s.max_attempts, log=Actor.log)

        jobs: list[Job] = []
        warned: set[str] = set()
        for geo in s.geos:
            resolution, warning = s.resolution_for(geo)
            if warning and 'interestByRegion' in s.sections and s.terms and warning not in warned:
                warned.add(warning)
                Actor.log.warning(warning)
            jobs.extend(Job(group, geo, resolution) for group in chunk_terms(s.terms, s.compare))

        stats = Stats(len(jobs))
        queue: asyncio.Queue[Job] = asyncio.Queue()
        for job in jobs:
            queue.put_nowait(job)
        if jobs:
            Actor.log.info(f'Fetching {len(jobs)} report(s) with {min(s.concurrency, len(jobs))} parallel session(s).')

        async def worker() -> None:
            # Each worker keeps its proxy session (IP + cookie) warm across jobs and only
            # rotates when Google pushes back — fewer warm-ups means faster, cheaper runs.
            client = new_client()
            try:
                while not queue.empty():
                    job = queue.get_nowait()
                    if limit_reached_for(REPORT_EVENT, stats):
                        continue
                    try:
                        parts, missing = await fetch_sections(client, s, job, s.sections)
                        if missing:
                            await client.close()  # next request opens a fresh IP and cookie
                            more, missing = await fetch_sections(client, s, job, missing)
                            parts.update(more)
                        if not parts:
                            raise TrendsBlockedError('every section failed after retrying on fresh IPs')
                        await push_report(s, build_report(s, job, parts, missing), stats)
                    except Exception as exc:  # noqa: BLE001 - one bad job must never end the run
                        Actor.log.error(f'{job.label()}: failed: {exc}')
                        stats.failed.append({'searchTerms': job.terms, 'geo': job.geo or 'WORLDWIDE', 'error': str(exc)})
                    await Actor.set_status_message(f'Reports: {stats.jobs_done}/{stats.total_jobs} done')
            finally:
                await client.close()

        await asyncio.gather(*(worker() for _ in range(min(s.concurrency, len(jobs)))))

        if s.trending_geos:
            client = new_client()
            try:
                for geo in s.trending_geos:
                    if limit_reached_for(TRENDING_EVENT, stats):
                        break
                    try:
                        xml_text = await client.get_text(TRENDING_RSS_URL, {'geo': geo})
                        for entry in parse_trending_rss(xml_text, geo):
                            result = await Actor.push_data({'type': 'trendingSearch', **entry, 'scrapedAt': now_iso()},
                                                           charged_event_name=TRENDING_EVENT)
                            stats.trending += 1
                            if getattr(result, 'event_charge_limit_reached', False):
                                stats.limit_reached = True
                                break
                    except Exception as exc:  # noqa: BLE001 - one bad country must not end the run
                        Actor.log.error(f'Trending now for {geo}: failed: {exc}')
                        stats.failed.append({'trendingGeo': geo, 'error': str(exc)})
            finally:
                await client.close()

        await Actor.set_value('OUTPUT', {
            'reports': stats.reports,
            'partialReports': stats.partial,
            'trendingSearches': stats.trending,
            'failed': stats.failed,
            'stoppedAtSpendingLimit': stats.limit_reached,
        })
        message = (
            f'Done: {stats.reports} report{"s" if stats.reports != 1 else ""}'
            + (f', {stats.trending} trending searches' if s.trending_geos else '')
            + (f', {stats.partial} partial (free)' if stats.partial else '')
            + (f', {len(stats.failed)} failed' if stats.failed else '')
            + (' — stopped at your spending limit' if stats.limit_reached else '')
        )
        Actor.log.info(message)
        if stats.reports + stats.partial + stats.trending == 0 and not stats.limit_reached:
            await Actor.fail(status_message=f'No data could be collected. {message}')
            return
        await Actor.set_status_message(message, is_terminal=True)

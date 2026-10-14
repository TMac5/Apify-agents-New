"""Offline tests: parsers, retry/rotation logic, and a full simulated run.

Run with:  python3 -m unittest discover -s tests -v
No network, Apify SDK or curl_cffi needed — both are replaced by fakes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- fake Apify SDK


class FakeLog:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append((level, msg))


class ChargeResult:
    def __init__(self, reached):
        self.event_charge_limit_reached = reached


class FakeActor:
    input: dict = {}
    pushed: list = []
    charged: list = []
    kv: dict = {}
    failed_with: str | None = None
    status: str | None = None
    charge_limit: int | None = None
    log = FakeLog()

    @classmethod
    def reset(cls, actor_input, charge_limit=None):
        cls.input, cls.pushed, cls.charged, cls.kv = actor_input, [], [], {}
        cls.failed_with, cls.status, cls.charge_limit, cls.log = None, None, charge_limit, FakeLog()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @classmethod
    async def get_input(cls):
        return cls.input

    @classmethod
    async def push_data(cls, item, charged_event_name=None):
        if isinstance(item, list):
            cls.pushed.extend(item)
            return None
        cls.pushed.append(item)
        if charged_event_name:
            cls.charged.append(charged_event_name)
            reached = cls.charge_limit is not None and len(cls.charged) >= cls.charge_limit
            return ChargeResult(reached)
        return None

    @classmethod
    async def charge(cls, event_name, count=1):
        cls.charged.append(event_name)
        reached = cls.charge_limit is not None and len(cls.charged) >= cls.charge_limit
        return ChargeResult(reached)

    @classmethod
    async def create_proxy_configuration(cls, actor_proxy_input=None):
        class Cfg:
            async def new_url(self, session_id):
                return f'http://proxy/{session_id}'
        return Cfg()

    @classmethod
    async def set_value(cls, key, value):
        cls.kv[key] = value

    @classmethod
    async def set_status_message(cls, msg, is_terminal=None):
        cls.status = msg

    @classmethod
    async def fail(cls, status_message=None, **_):
        cls.failed_with = status_message


fake_apify = types.ModuleType('apify')
fake_apify.Actor = FakeActor()
# Class-level attributes are reached through the instance, matching `from apify import Actor`.
sys.modules['apify'] = fake_apify

from src import main as actor_main  # noqa: E402
from src import trends_api as api  # noqa: E402
from src.trends_client import HttpResponse, TrendsBlockedError, TrendsClient  # noqa: E402

# ---------------------------------------------------------------- fixtures

XSSI = ")]}',\n"


def explore_json(terms):
    widgets = [
        {'id': 'TIMESERIES', 'token': 'tok-ts', 'request': {'time': 'x', 'comparisonItem': [{'k': t} for t in terms]}},
        {'id': 'GEO_MAP', 'token': 'tok-geo', 'request': {'resolution': 'COUNTRY'}},
    ]
    for i, t in enumerate(terms):
        restriction = {'complexKeywordsRestriction': {'keyword': [{'type': 'BROAD', 'value': t}]}}
        widgets.append({'id': f'RELATED_TOPICS_{i}' if len(terms) > 1 else 'RELATED_TOPICS', 'token': f'tok-rt-{t}',
                        'request': {'restriction': restriction}})
        widgets.append({'id': f'RELATED_QUERIES_{i}' if len(terms) > 1 else 'RELATED_QUERIES', 'token': f'tok-rq-{t}',
                        'request': {'restriction': restriction}})
    return {'widgets': widgets}


def embed_page(widget, terms):
    tokens = {w['id'].rstrip('_0123456789'): w for w in explore_json(terms)['widgets']}
    token = dict(tokens[widget], type='fe_line_chart')
    payload = json.dumps(token).replace('"', '\\x22').replace('=', '\\u003d')
    return f"<html><script>var x = JSON.parse('{payload}');</script></html>"


def timeline_json(n):
    return {'default': {'timelineData': [
        {'time': '1704067200', 'formattedTime': 'Jan 1, 2024', 'value': [50 + i for i in range(n)], 'hasData': [True] * n},
        {'time': '1704672000', 'formattedTime': 'Jan 8, 2024', 'value': [70 + i for i in range(n)], 'hasData': [True] * n},
        {'time': '1705276800', 'formattedTime': 'Jan 15, 2024', 'value': [1] * n, 'hasData': [True] * n, 'isPartial': True},
    ]}}


def geo_json(n):
    return {'default': {'geoMapData': [
        {'geoCode': 'US-CA', 'geoName': 'California', 'value': [40] * n, 'hasData': [True] * n},
        {'geoCode': 'US-NY', 'geoName': 'New York', 'value': [100] * n, 'hasData': [True] * n},
        {'geoCode': 'US-WY', 'geoName': 'Wyoming', 'value': [0] * n, 'hasData': [False] * n},
    ]}}


RELATED_QUERIES = {'default': {'rankedList': [
    {'rankedKeyword': [{'query': 'bitcoin price', 'value': 100, 'formattedValue': '100', 'link': '/trends/explore?q=bitcoin+price'}]},
    {'rankedKeyword': [{'query': 'bitcoin etf', 'value': 4250, 'formattedValue': 'Breakout', 'link': '/trends/explore?q=bitcoin+etf'}]},
]}}
RELATED_TOPICS = {'default': {'rankedList': [
    {'rankedKeyword': [{'topic': {'mid': '/m/05p0rrx', 'title': 'Bitcoin', 'type': 'Currency'}, 'value': 100, 'formattedValue': '100', 'link': '/x'}]},
    {'rankedKeyword': []},
]}}
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>world cup</title><ht:approx_traffic>2M+</ht:approx_traffic><pubDate>Wed, 14 Oct 2026 08:00:00 -0700</pubDate>
<ht:picture>https://img/1</ht:picture><ht:news_item><ht:news_item_title>Final tonight</ht:news_item_title>
<ht:news_item_url>https://news/1</ht:news_item_url><ht:news_item_source>BBC</ht:news_item_source></ht:news_item></item>
<item><title>eclipse</title><ht:approx_traffic>500+</ht:approx_traffic><pubDate>Wed, 14 Oct 2026 09:00:00 -0700</pubDate></item>
</channel></rss>"""


class FakeGoogle:
    """Serves fixtures; can rate-limit the first N calls of each proxy session or block whole IPs."""

    def __init__(self, block_first_sessions=0, always_block=False, fail_paths=(), no_token_sessions=0, html_first_widget=0):
        self.html_first_widget = html_first_widget
        self.no_token_sessions = no_token_sessions
        self.block_first_sessions = block_first_sessions
        self.always_block = always_block
        self.fail_paths = set(fail_paths)
        self.sessions = []
        self.calls = []
        self.resolutions = []

    async def factory(self, proxy_url):
        index = len(self.sessions)
        self.sessions.append(proxy_url)
        google = self
        blocked = self.always_block or index < self.block_first_sessions

        class T:
            async def request(self, method, url, params):
                await asyncio.sleep(0)  # yield like real network I/O so workers interleave
                google.calls.append((proxy_url, method, url))
                if blocked:
                    return HttpResponse(429, 'Too Many Requests', url)
                path = urlparse(url).path
                if path in google.fail_paths:
                    return HttpResponse(429, 'Too Many Requests', url)
                if path == '/trends/explore':
                    return HttpResponse(200, '<html>ok</html>', url)
                if path == '/trends/api/explore':
                    return HttpResponse(500, 'Internal Server Error', url)  # what Google really does now
                if path.startswith('/trends/embed/explore/'):
                    widget = path.rsplit('/', 1)[1]
                    terms = [c['keyword'] for c in json.loads(params['req'])['comparisonItem']]
                    if google.no_token_sessions and len(google.sessions) <= google.no_token_sessions:
                        return HttpResponse(200, '<html>consent page</html>', url)
                    return HttpResponse(200, embed_page(widget, terms), url)
                if path.startswith('/trends/api/widgetdata/') and google.html_first_widget > 0:
                    google.html_first_widget -= 1
                    return HttpResponse(200, '<html>unusual traffic</html>', url)
                if path == '/trends/api/widgetdata/multiline':
                    n = len(json.loads(params['req'])['comparisonItem'])
                    return HttpResponse(200, XSSI + json.dumps(timeline_json(n)), url)
                if path == '/trends/api/widgetdata/comparedgeo':
                    google.resolutions.append(json.loads(params['req'])['resolution'])
                    return HttpResponse(200, XSSI + json.dumps(geo_json(1)), url)
                if path == '/trends/api/widgetdata/relatedsearches':
                    body = RELATED_QUERIES if params['token'].startswith('tok-rq') else RELATED_TOPICS
                    return HttpResponse(200, XSSI + json.dumps(body), url)
                if path == '/trending/rss':
                    return HttpResponse(200, RSS, url)
                return HttpResponse(404, 'nope', url)

            async def close(self):
                pass

        return T()


async def no_sleep(_):
    return None


def run_actor(actor_input, google, charge_limit=None):
    FakeActor.reset(actor_input, charge_limit)
    import src.trends_client as tc
    tc_sleep = tc.TrendsClient.__init__.__defaults__
    try:
        # Make every client use a no-op sleep so tests are instant.
        orig_init = tc.TrendsClient.__init__

        def fast_init(self, *a, **kw):
            kw['sleep'] = no_sleep
            orig_init(self, *a, **kw)

        tc.TrendsClient.__init__ = fast_init
        asyncio.run(actor_main.run(transport_factory=google.factory))
    finally:
        tc.TrendsClient.__init__ = orig_init
    del tc_sleep
    return FakeActor


# ---------------------------------------------------------------- tests


class ParserTests(unittest.TestCase):
    def test_strip_xssi(self):
        self.assertEqual(api.strip_xssi(")]}',\n{\"a\": 1}"), {'a': 1})
        self.assertEqual(api.strip_xssi(")]}'\n[1,2]"), [1, 2])

    def test_time_range_validation(self):
        self.assertEqual(api.validate_time_range('today 12-m'), 'today 12-m')
        self.assertEqual(api.validate_time_range('2024-01-01 2024-06-30'), '2024-01-01 2024-06-30')
        with self.assertRaises(api.InputError):
            api.validate_time_range('last year')

    def test_chunking_dedupes_and_caps_at_five(self):
        terms = ['a', 'b', 'A', ' c ', '', 'd', 'e', 'f', 'g']
        self.assertEqual(api.chunk_terms(terms, compare=True), [['a', 'b', 'c', 'd', 'e'], ['f', 'g']])
        self.assertEqual(api.chunk_terms(['x', 'y'], compare=False), [['x'], ['y']])

    def test_resolution_rules(self):
        self.assertEqual(api.resolve_resolution('', 'CITY')[0], 'COUNTRY')
        self.assertEqual(api.resolve_resolution('DE', 'DMA')[0], 'REGION')
        self.assertEqual(api.resolve_resolution('US', 'DMA'), ('DMA', None))
        self.assertEqual(api.resolve_resolution('US', 'COUNTRY')[0], 'REGION')

    def test_timeline_and_averages_skip_partial(self):
        rows = api.parse_timeline(timeline_json(2), ['a', 'b'])
        self.assertEqual(rows[0]['values'], {'a': 50, 'b': 51})
        self.assertEqual(rows[0]['date'], '2024-01-01T00:00:00Z')
        self.assertTrue(rows[2]['isPartial'])
        self.assertEqual(api.timeline_averages(rows, ['a', 'b']), {'a': 60.0, 'b': 61.0})

    def test_geo_sorted_and_no_data_dropped(self):
        rows = api.parse_geo(geo_json(1), ['a'])
        self.assertEqual([r['geoName'] for r in rows], ['New York', 'California'])

    def test_related(self):
        q = api.parse_related(RELATED_QUERIES, 'queries')
        self.assertEqual(q['rising'][0]['formattedValue'], 'Breakout')
        self.assertTrue(q['top'][0]['link'].startswith('https://trends.google.com/trends/explore'))
        t = api.parse_related(RELATED_TOPICS, 'topics')
        self.assertEqual(t['top'][0]['topic'], 'Bitcoin')
        self.assertEqual(t['rising'], [])

    def test_rss(self):
        items = api.parse_trending_rss(RSS, 'US')
        self.assertEqual([i['rank'] for i in items], [1, 2])
        self.assertEqual(items[0]['approxTrafficNumber'], 2_000_000)
        self.assertEqual(items[0]['publishedAt'], '2026-10-14T15:00:00Z')
        self.assertEqual(items[0]['news'][0]['source'], 'BBC')
        self.assertEqual(items[1]['news'], [])


class ClientTests(unittest.TestCase):
    def test_rotates_ip_until_unblocked(self):
        google = FakeGoogle(block_first_sessions=3)

        async def go():
            async def proxy(sid):
                return f'http://proxy/{sid}'
            client = TrendsClient(google.factory, proxy, max_attempts=8, sleep=no_sleep)
            text = await client.get_text('https://trends.google.com/trending/rss', {'geo': 'US'})
            return client, text

        client, text = asyncio.run(go())
        self.assertIn('world cup', text)
        self.assertEqual(len(google.sessions), 4)
        self.assertEqual(len(set(google.sessions)), 4, 'each retry must use a new proxy session')
        self.assertEqual(client.rotations, 3)

    def test_gives_up_after_max_attempts(self):
        google = FakeGoogle(always_block=True)

        async def go():
            async def proxy(sid):
                return None
            client = TrendsClient(google.factory, proxy, max_attempts=3, sleep=no_sleep)
            await client.get_text('https://trends.google.com/trending/rss')

        with self.assertRaises(TrendsBlockedError):
            asyncio.run(go())
        self.assertEqual(len(google.sessions), 3)


class EmbedTests(unittest.TestCase):
    def test_extracts_escaped_token(self):
        tokens = api.extract_embed_tokens(embed_page('TIMESERIES', ['a', 'b']))
        self.assertEqual(tokens[0]['token'], 'tok-ts')
        self.assertEqual(len(tokens[0]['request']['comparisonItem']), 2)

    def test_no_token_raises(self):
        with self.assertRaises(ValueError):
            api.extract_embed_tokens('<html>consent</html>')


class EndToEndTests(unittest.TestCase):
    def test_rotates_past_consent_pages(self):
        actor = run_actor({'searchTerms': ['bitcoin']}, FakeGoogle(no_token_sessions=2))
        self.assertEqual(actor.charged, ['keyword-report'])

    def test_separate_terms_full_report(self):
        actor = run_actor({'searchTerms': ['bitcoin', 'ethereum'], 'geo': 'US'}, FakeGoogle(block_first_sessions=2))
        self.assertIsNone(actor.failed_with)
        reports = [p for p in actor.pushed if p['type'] == 'keywordReport']
        self.assertEqual(len(reports), 2)
        self.assertEqual(actor.charged, ['keyword-report', 'keyword-report'])
        r = reports[0]
        for key in ('interestOverTime', 'averageInterest', 'interestByRegion', 'relatedQueries', 'relatedTopics', 'trendsUrl'):
            self.assertIn(key, r)
        self.assertNotIn('missingSections', r)
        self.assertEqual(actor.kv['OUTPUT']['reports'], 2)

    def test_compare_mode_groups_terms(self):
        actor = run_actor({'searchTerms': ['a', 'b', 'c'], 'compareTerms': True}, FakeGoogle())
        reports = [p for p in actor.pushed if p['type'] == 'keywordReport']
        self.assertEqual(len(reports), 1)
        self.assertEqual(set(reports[0]['relatedQueries']), {'a', 'b', 'c'})
        self.assertEqual(set(reports[0]['interestOverTime'][0]['values']), {'a', 'b', 'c'})
        self.assertEqual(reports[0]['geo'], 'WORLDWIDE')
        self.assertEqual(reports[0]['regionResolution'], 'COUNTRY')
        self.assertIn('Most searched overall', reports[0]['summary'])

    def test_partial_report_is_free(self):
        google = FakeGoogle(fail_paths={'/trends/api/widgetdata/comparedgeo'})
        actor = run_actor({'searchTerms': ['bitcoin'], 'maxAttemptsPerRequest': 2}, google)
        self.assertEqual(actor.pushed[0]['missingSections'], ['interestByRegion'])
        self.assertEqual(actor.charged, [], 'partial reports must not be charged')
        self.assertIsNone(actor.failed_with)

    def test_trending_only(self):
        actor = run_actor({'trendingNowGeos': ['us', 'GB']}, FakeGoogle())
        trending = [p for p in actor.pushed if p['type'] == 'trendingSearch']
        self.assertEqual(len(trending), 4)
        self.assertEqual({t['geo'] for t in trending}, {'US', 'GB'})
        self.assertEqual(actor.charged.count('trending-search'), 4)

    def test_stops_at_spending_limit(self):
        actor = run_actor({'trendingNowGeos': ['US', 'GB', 'DE']}, FakeGoogle(), charge_limit=3)
        self.assertEqual(len(actor.charged), 3)
        self.assertTrue(actor.kv['OUTPUT']['stoppedAtSpendingLimit'])

    def test_total_failure_fails_run(self):
        actor = run_actor({'searchTerms': ['bitcoin'], 'maxAttemptsPerRequest': 2}, FakeGoogle(always_block=True))
        self.assertIsNotNone(actor.failed_with)
        self.assertEqual(actor.pushed, [])

    def test_bad_input_fails_cleanly(self):
        actor = run_actor({'searchTerms': ['x'], 'timeRange': 'custom', 'customTimeRange': 'yesterday'}, FakeGoogle())
        self.assertIn('Invalid input', actor.failed_with)
        actor = run_actor({}, FakeGoogle())
        self.assertIn('Invalid input', actor.failed_with)


class FeatureTests(unittest.TestCase):
    def test_insights_and_summary(self):
        actor = run_actor({'searchTerms': ['bitcoin'], 'geos': ['US']}, FakeGoogle())
        r = actor.pushed[0]
        ins = r['insights']['bitcoin']
        self.assertEqual(ins['peak'], {'date': '2024-01-08T00:00:00Z', 'value': 70})
        self.assertEqual(ins['latest']['value'], 70)
        self.assertEqual(ins['recentChangePercent'], 40.0)
        self.assertEqual(ins['direction'], 'rising')
        self.assertEqual(ins['topRegions'][0]['name'], 'New York')
        self.assertEqual(ins['breakoutQueries'], ['bitcoin etf'])
        self.assertEqual(r['title'], 'bitcoin · US · The past 12 months')
        self.assertIn('Breakout searches: bitcoin etf', r['summary'])
        self.assertEqual(r['regionResolution'], 'REGION')

    def test_multiple_geos_and_auto_resolution(self):
        google = FakeGoogle()
        actor = run_actor({'searchTerms': ['a', 'b'], 'geos': ['us', 'worldwide', 'US-CA']}, google)
        reports = [p for p in actor.pushed if p['type'] == 'keywordReport']
        self.assertEqual(len(reports), 6)
        self.assertEqual({r['geo'] for r in reports}, {'US', 'WORLDWIDE', 'US-CA'})
        self.assertEqual(set(google.resolutions), {'REGION', 'COUNTRY', 'CITY'})
        self.assertFalse([l for l in actor.log.lines if l[0] == 'warning'], 'automatic mode must not warn')

    def test_sessions_are_reused_across_jobs(self):
        google = FakeGoogle()
        run_actor({'searchTerms': [f't{i}' for i in range(9)], 'maxConcurrency': 3}, google)
        self.assertEqual(len(google.sessions), 3, 'one warm session per worker, not per term')

    def test_non_json_widget_response_is_retried(self):
        actor = run_actor({'searchTerms': ['bitcoin'], 'maxConcurrency': 1}, FakeGoogle(html_first_widget=2))
        self.assertEqual(actor.charged, ['keyword-report'])
        self.assertNotIn('missingSections', actor.pushed[0])

    def test_spreadsheet_rows(self):
        actor = run_actor({'searchTerms': ['bitcoin', 'eth'], 'compareTerms': True, 'outputFormat': 'rows'}, FakeGoogle())
        types = {r['type'] for r in actor.pushed}
        self.assertEqual(types, {'termSummary', 'timelinePoint', 'regionInterest', 'relatedQuery', 'relatedTopic'})
        self.assertEqual(actor.charged, ['keyword-report'], 'one charge per report, not per row')
        summary = [r for r in actor.pushed if r['type'] == 'termSummary' and r['term'] == 'eth'][0]
        self.assertTrue(summary['summary'].startswith('"eth"'))
        points = [r for r in actor.pushed if r['type'] == 'timelinePoint' and r['term'] == 'bitcoin']
        self.assertEqual([p['value'] for p in points], [50, 70, 1])

    def test_rows_respect_spending_limit(self):
        actor = run_actor({'searchTerms': ['a', 'b', 'c'], 'outputFormat': 'rows', 'maxConcurrency': 1},
                          FakeGoogle(), charge_limit=1)
        self.assertEqual(len(actor.charged), 1)
        self.assertTrue(actor.kv['OUTPUT']['stoppedAtSpendingLimit'])

    def test_input_validation(self):
        for bad in ({'searchTerms': ['x'], 'geos': ['United States']},
                    {'trendingNowGeos': ['US-CA']},
                    {'searchTerms': ['x'], 'timeRange': 'custom'},
                    {'searchTerms': ['x'], 'includeInterestOverTime': False, 'includeInterestByRegion': False,
                     'includeRelatedQueries': False, 'includeRelatedTopics': False}):
            actor = run_actor(bad, FakeGoogle())
            self.assertIn('Invalid input', actor.failed_with or '', bad)

    def test_explicit_bad_resolution_warns_once(self):
        actor = run_actor({'searchTerms': ['a', 'b'], 'geos': ['DE'], 'regionResolution': 'DMA'}, FakeGoogle())
        warnings = [l for l in actor.log.lines if l[0] == 'warning']
        self.assertEqual(len(warnings), 1)

    def test_no_data_term(self):
        google = FakeGoogle()
        orig = timeline_json
        globals()['timeline_json'] = lambda n: {'default': {'timelineData': []}}
        try:
            actor = run_actor({'searchTerms': ['zzqx']}, google)
        finally:
            globals()['timeline_json'] = orig
        self.assertEqual(actor.pushed[0]['insights']['zzqx']['direction'], 'no data')
        self.assertIn('not enough search volume', actor.pushed[0]['summary'])


if __name__ == '__main__':
    unittest.main()

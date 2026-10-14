"""Pure helpers for Google Trends: request building, validation and response parsing.

Nothing in this module does network I/O, so it can be unit-tested without the
Apify SDK or an HTTP client installed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

BASE = 'https://trends.google.com'
EXPLORE_URL = f'{BASE}/trends/api/explore'
TIMESERIES_URL = f'{BASE}/trends/api/widgetdata/multiline'
GEO_URL = f'{BASE}/trends/api/widgetdata/comparedgeo'
RELATED_URL = f'{BASE}/trends/api/widgetdata/relatedsearches'
TRENDING_RSS_URL = f'{BASE}/trending/rss'
EMBED_URL = f'{BASE}/trends/embed/explore'  # + /TIMESERIES, /GEO_MAP, /RELATED_QUERIES, /RELATED_TOPICS
WARMUP_URL = 'https://www.google.com/'

MAX_TERMS_PER_COMPARISON = 5

TIME_RANGE_PRESETS = {
    'now 1-H',
    'now 4-H',
    'now 1-d',
    'now 7-d',
    'today 1-m',
    'today 3-m',
    'today 12-m',
    'today 5-y',
    'all',
}
_DATE_RANGE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{4}-\d{2}-\d{2}$')
_HOUR_RANGE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2} \d{4}-\d{2}-\d{2}T\d{2}$')

SEARCH_PROPERTIES = {'', 'images', 'news', 'froogle', 'youtube'}
RESOLUTIONS = {'COUNTRY', 'REGION', 'CITY', 'DMA'}  # plus 'AUTO' in the input


class InputError(ValueError):
    """Raised when the Actor input cannot produce a valid Trends request."""


def validate_time_range(time_range: str) -> str:
    time_range = (time_range or '').strip()
    if time_range in TIME_RANGE_PRESETS or _DATE_RANGE.match(time_range) or _HOUR_RANGE.match(time_range):
        return time_range
    raise InputError(
        f'Invalid time range "{time_range}". Use a preset such as "today 12-m" or a custom range '
        'like "2024-01-01 2024-12-31".'
    )


def chunk_terms(terms: list[str], compare: bool) -> list[list[str]]:
    """Split search terms into request groups.

    In compare mode, terms share one 0-100 scale, which Google allows for at most
    5 terms at a time. Otherwise each term is analysed on its own scale.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms:
        term = (term or '').strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            cleaned.append(term)
    if not compare:
        return [[t] for t in cleaned]
    return [cleaned[i:i + MAX_TERMS_PER_COMPARISON] for i in range(0, len(cleaned), MAX_TERMS_PER_COMPARISON)]


_GEO = re.compile(r'^[A-Z]{2}(-[A-Z0-9]{1,3}){0,2}$')
WORLDWIDE_ALIASES = {'', 'WORLDWIDE', 'WORLD', 'WW', 'GLOBAL', 'ALL'}


def normalize_geo(geo: str | None) -> str:
    """Return '' for worldwide or an upper-case Trends geo code like US, DE or US-CA."""
    geo = (geo or '').strip().upper().replace('_', '-')
    if geo in WORLDWIDE_ALIASES:
        return ''
    if not _GEO.match(geo):
        raise InputError(
            f'"{geo}" is not a valid location. Use a two-letter country code (US, DE, LT), '
            'a region like US-CA, or leave it empty for worldwide.'
        )
    return geo


def resolve_resolution(geo: str, requested: str) -> tuple[str, str | None]:
    """Return a resolution Google accepts for this geo, plus a warning if we had to change the user's choice."""
    requested = (requested or 'AUTO').upper()
    auto = 'COUNTRY' if not geo else ('REGION' if '-' not in geo else 'CITY')
    if requested not in RESOLUTIONS:
        return auto, None
    if not geo and requested != 'COUNTRY':
        return 'COUNTRY', 'Worldwide searches can only be broken down by country; used countries.'
    if geo and requested == 'COUNTRY':
        return auto, f'A search inside {geo} cannot be broken down by country; used {auto.lower()}.'
    if requested == 'DMA' and not geo.startswith('US'):
        return auto, f'Metro areas (DMA) only exist for the US; used {auto.lower()} for {geo}.'
    if requested == 'REGION' and '-' in geo:
        return 'CITY', f'{geo} is already a region; broke it down by city instead.'
    return requested, None


TIME_RANGE_LABELS = {
    'now 1-H': 'the past hour',
    'now 4-H': 'the past 4 hours',
    'now 1-d': 'the past day',
    'now 7-d': 'the past 7 days',
    'today 1-m': 'the past 30 days',
    'today 3-m': 'the past 90 days',
    'today 12-m': 'the past 12 months',
    'today 5-y': 'the past 5 years',
    'all': '2004 to today',
}


def time_range_label(time_range: str) -> str:
    if time_range in TIME_RANGE_LABELS:
        return TIME_RANGE_LABELS[time_range]
    start, _, end = time_range.partition(' ')
    return f'{start.replace("T", " ")}:00 to {end.replace("T", " ")}:00' if 'T' in start else f'{start} to {end}'


def build_explore_params(
    terms: list[str], geo: str, time_range: str, category: int, search_property: str, hl: str, tz: int
) -> dict[str, str]:
    req = {
        'comparisonItem': [{'keyword': t, 'geo': geo, 'time': time_range} for t in terms],
        'category': int(category or 0),
        'property': search_property or '',
    }
    return {'hl': hl, 'tz': str(tz), 'req': json.dumps(req, separators=(',', ':'))}


def build_widget_params(widget: dict[str, Any], hl: str, tz: int) -> dict[str, str]:
    return {
        'req': json.dumps(widget['request'], separators=(',', ':')),
        'token': widget['token'],
        'tz': str(tz),
        'hl': hl,
    }


def explore_url(terms: list[str], geo: str, time_range: str, category: int, search_property: str) -> str:
    params = {'date': time_range, 'q': ','.join(terms), 'hl': 'en'}
    if geo:
        params['geo'] = geo
    if category:
        params['cat'] = str(category)
    if search_property:
        params['gprop'] = search_property
    return f'{BASE}/trends/explore?{urlencode(params)}'


def strip_xssi(text: str) -> Any:
    """Google prefixes JSON with an anti-XSSI guard such as )]}', — drop it and parse."""
    starts = [i for i in (text.find('{'), text.find('[')) if i != -1]
    if not starts:
        raise ValueError('Response did not contain JSON')
    return json.loads(text[min(starts):])


def split_widgets(explore_json: dict[str, Any]) -> dict[str, Any]:
    """Group explore widgets by type. Related widgets are keyed by the term they belong to."""
    out: dict[str, Any] = {'timeseries': None, 'geo': None, 'related_queries': {}, 'related_topics': {}}
    for widget in explore_json.get('widgets', []):
        wid = widget.get('id', '')
        if wid == 'TIMESERIES' and out['timeseries'] is None:
            out['timeseries'] = widget
        elif wid.startswith('GEO_MAP') and out['geo'] is None:
            out['geo'] = widget
        elif wid.startswith('RELATED_QUERIES'):
            out['related_queries'][_related_widget_term(widget)] = widget
        elif wid.startswith('RELATED_TOPICS'):
            out['related_topics'][_related_widget_term(widget)] = widget
    return out


def _related_widget_term(widget: dict[str, Any]) -> str:
    try:
        keywords = widget['request']['restriction']['complexKeywordsRestriction']['keyword']
        return keywords[0]['value']
    except (KeyError, IndexError, TypeError):
        return widget.get('title', '') or widget.get('id', '')


def parse_timeline(data: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    rows = []
    for point in data.get('default', {}).get('timelineData', []):
        ts = int(point.get('time', 0))
        values = point.get('value', [])
        rows.append({
            'date': datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z'),
            'timestamp': ts,
            'formattedTime': point.get('formattedTime'),
            'values': {term: (values[i] if i < len(values) else None) for i, term in enumerate(terms)},
            'isPartial': bool(point.get('isPartial', False)),
        })
    return rows


def timeline_averages(rows: list[dict[str, Any]], terms: list[str]) -> dict[str, float | None]:
    averages: dict[str, float | None] = {}
    for term in terms:
        vals = [r['values'][term] for r in rows if not r['isPartial'] and r['values'].get(term) is not None]
        averages[term] = round(sum(vals) / len(vals), 2) if vals else None
    return averages


def parse_geo(data: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    rows = []
    for region in data.get('default', {}).get('geoMapData', []):
        values = region.get('value', [])
        row: dict[str, Any] = {
            'geoCode': region.get('geoCode'),
            'geoName': region.get('geoName'),
            'values': {term: (values[i] if i < len(values) else None) for i, term in enumerate(terms)},
        }
        if 'coordinates' in region:
            row['coordinates'] = region['coordinates']
        if any(region.get('hasData', [True])):
            rows.append(row)
    rows.sort(key=lambda r: max((v or 0) for v in r['values'].values()) if r['values'] else 0, reverse=True)
    return rows


def parse_related(data: dict[str, Any], kind: str) -> dict[str, list[dict[str, Any]]]:
    """Parse a relatedsearches response. kind is 'queries' or 'topics'."""
    ranked = data.get('default', {}).get('rankedList', [])
    result: dict[str, list[dict[str, Any]]] = {'top': [], 'rising': []}
    for bucket, idx in (('top', 0), ('rising', 1)):
        if idx >= len(ranked):
            continue
        for item in ranked[idx].get('rankedKeyword', []):
            entry: dict[str, Any] = {
                'value': item.get('value'),
                'formattedValue': item.get('formattedValue'),
                'link': f"{BASE}{item['link']}" if item.get('link') else None,
            }
            if kind == 'queries':
                entry = {'query': item.get('query'), **entry}
            else:
                topic = item.get('topic', {})
                entry = {'topic': topic.get('title'), 'topicType': topic.get('type'), 'topicId': topic.get('mid'), **entry}
            result[bucket].append(entry)
    return result


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or '').strip() or None
    return None


def parse_traffic(text: str | None) -> int | None:
    if not text:
        return None
    m = re.match(r'([\d.,]+)\s*([KkMm]?)', text.replace(' ', ''))
    if not m:
        return None
    number = float(m.group(1).replace(',', ''))
    mult = {'k': 1_000, 'm': 1_000_000}.get(m.group(2).lower(), 1)
    return int(number * mult)


def parse_trending_rss(xml_text: str, geo: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items = []
    for rank, item in enumerate((el for el in root.iter() if _local(el.tag) == 'item'), start=1):
        news = []
        for child in item:
            if _local(child.tag) == 'news_item':
                news.append({
                    'title': _child_text(child, 'news_item_title'),
                    'url': _child_text(child, 'news_item_url'),
                    'source': _child_text(child, 'news_item_source'),
                    'picture': _child_text(child, 'news_item_picture'),
                })
        published = _child_text(item, 'pubDate')
        try:
            published_iso = parsedate_to_datetime(published).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if published else None
        except (TypeError, ValueError):
            published_iso = published
        traffic = _child_text(item, 'approx_traffic')
        items.append({
            'geo': geo,
            'rank': rank,
            'title': _child_text(item, 'title'),
            'approxTraffic': traffic,
            'approxTrafficNumber': parse_traffic(traffic),
            'publishedAt': published_iso,
            'picture': _child_text(item, 'picture'),
            'pictureSource': _child_text(item, 'picture_source'),
            'news': news,
        })
    return items


_EMBED_JSON = re.compile(r"JSON\.parse\('([^']+)'\)")
_JS_ESCAPE = re.compile(r'\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)')
OVER_QUOTA = 'USER_TYPE_EMBED_OVER_QUOTA'


def _unescape_js(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        esc = m.group(1)
        if esc[0] in 'xu' and len(esc) > 1:
            return chr(int(esc[1:], 16))
        return {'n': '\n', 't': '\t', 'r': '\r'}.get(esc, esc)
    return _JS_ESCAPE.sub(repl, text)


def extract_embed_tokens(html: str) -> list[dict[str, Any]]:
    """Pull the widget tokens Google embeds in /trends/embed/explore/<WIDGET> pages.

    Each token looks like {"request": {...}, "token": "...", "type": "fe_line_chart", "userType": ...}.
    Raises ValueError if the page holds no token (usually a block or consent page).
    """
    tokens: list[dict[str, Any]] = []
    for raw in _EMBED_JSON.findall(html):
        try:
            obj = json.loads(_unescape_js(raw))
        except ValueError:
            continue
        candidates = obj if isinstance(obj, list) else [obj]
        tokens.extend(c for c in candidates if isinstance(c, dict) and 'token' in c and 'request' in c)
    if not tokens:
        raise ValueError('No widget token found in embed page')
    return tokens


def is_over_quota(token: dict[str, Any]) -> bool:
    return token.get('userType') == OVER_QUOTA

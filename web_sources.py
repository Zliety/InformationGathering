"""Discover public news links and extract original article text, without RSS or search APIs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from lxml import html, etree
import trafilatura
from pypdf import PdfReader

BEIJING = timezone(timedelta(hours=8))
MAX_PAGE_BYTES = 3 * 1024 * 1024
TOPICS = (
    ('宏观经济数据', ('GDP', 'PMI', '统计', '经济', '工业', '利润', '营收', '投资')),
    ('同业产业动态', ('制造业', '消费电子', '新能源', '汽车', '储能', '产销', '电池')),
    ('主要竞争对手', ('比亚迪', '立讯', '蓝思', '歌尔')),
    ('大陆地区劳动新闻', ('就业', '失业', '裁员', '劳动', '社保', '退休', '工时', '人口', '出生')),
    ('人力资源分析报告', ('人力资源', '招聘', '薪酬', '人才', '岗位', '劳动力')),
    ('疫情/天灾动态', ('疫情', '传染病', '地震', '台风', '洪水', '暴雨', '水灾')),
    ('人力预测及变化', ('用工', '人力', '招工', '缺工', '劳动力', '员工', 'DL', 'IDL')),
)


class SourceError(Exception):
    """An error safe to record without page bodies or credentials."""


def canonical_url(value):
    parsed = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('spm', 'from')]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'),
                       urlencode(sorted(query)), ''))


def checked_url(value, domains):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password
                or any(c.isspace() for c in value) or any(c in value for c in '|<>')
                or not any(host == domain or host.endswith('.' + domain) for domain in domains)
                or host == 'localhost' or host.endswith(('.local', '.internal'))):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
    except (ValueError, TypeError, AttributeError):
        raise SourceError('URL is outside the configured HTTPS source domains') from None


class SourceRedirectHandler(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def __init__(self, domains):
        super().__init__()
        self.domains = domains

    def redirect_request(self, request, response, code, message, headers, new_url):
        checked_url(new_url, self.domains)
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_public(url, domains, timeout, form=None):
    checked_url(url, domains)
    request = Request(url, data=urlencode(form).encode() if form is not None else None, headers={'User-Agent': 'Mozilla/5.0 (compatible; InformationGathering/2.0)',
                                    'Accept': 'text/html,application/json,application/pdf;q=0.9,*/*;q=0.5'})
    opener = build_opener(SourceRedirectHandler(domains), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = checked_url(response.geturl(), domains)
            limit = 8 * 1024 * 1024 if urlsplit(final_url).path.lower().endswith('.pdf') else MAX_PAGE_BYTES
            data = response.read(limit + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise SourceError(f'Source returned HTTP {code}') from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise SourceError('Source network, TLS, or timeout failure') from None
    if len(data) > limit:
        raise SourceError('Source page exceeds the size limit')
    return data, final_url


def parse_date(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r'\d{10,13}', value)):
            stamp = float(value)
            return datetime.fromtimestamp(stamp / 1000 if stamp > 10**11 else stamp, BEIJING).date()
        if not isinstance(value, str):
            return None
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            return parsed.astimezone(BEIJING).date() if parsed.tzinfo else parsed.date()
        except ValueError:
            pass
        match = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})(?:日|\b)', text)
        if match:
            return date(*(int(part) for part in match.groups()))
        return parsedate_to_datetime(text).astimezone(BEIJING).date()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def load_sources(path):
    try:
        if Path(path).stat().st_size > 64 * 1024:
            raise ValueError
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        sources = data['sources']
        if data.get('version') != 1 or not isinstance(sources, list) or not 1 <= len(sources) <= 40:
            raise ValueError
        ids = set()
        for source in sources:
            if (not isinstance(source, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', source.get('id', ''))
                    or source['id'] in ids or source.get('type') not in ('html', 'newsnow', 'cninfo')
                    or not isinstance(source.get('name'), str) or not source['name'].strip()
                    or any(c in source['name'] for c in '\r\n|')):
                raise ValueError
            domains = source['allowed_domains']
            if (not isinstance(domains, list) or not domains
                    or any(not isinstance(d, str) or not re.fullmatch(r'[a-z0-9.-]+', d) for d in domains)):
                raise ValueError
            if source['type'] == 'html':
                checked_url(source['url'], domains)
                re.compile(source['link_pattern'])
            elif source['type'] == 'cninfo':
                if domains != ['cninfo.com.cn'] or not re.fullmatch(r'\d{6}', source.get('security_code', '')):
                    raise ValueError
            elif not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', source.get('platform', '')):
                raise ValueError
            ids.add(source['id'])
        return sources
    except (OSError, ValueError, KeyError, TypeError, SourceError, re.error):
        raise SourceError('News source configuration is missing or invalid') from None


def discover(source, settings, today=None):
    if source['type'] == 'cninfo':
        today = today or datetime.now(BEIJING).date()
        form = dict(pageNum='1', pageSize='30', column='szse', tabName='fulltext',
                    plate='', stock='', searchkey=source['security_code'], secid='', category='',
                    trade='', seDate=f'{today - timedelta(days=2)}~{today}',
                    sortName='time', sortType='desc', isHLtitle='false')
        data, _ = fetch_public('https://www.cninfo.com.cn/new/hisAnnouncement/query',
                               source['allowed_domains'], settings['source_timeout'], form=form)
        try:
            result = json.loads(data)
            items = result['announcements']
            if items is None and result.get('totalAnnouncement') == 0:
                items = []
            if not isinstance(items, list):
                raise ValueError
            entries = []
            for item in items:
                if not isinstance(item, dict) or item.get('secCode') != source['security_code']:
                    continue
                title = item.get('announcementTitle')
                adjunct = item.get('adjunctUrl')
                if not isinstance(title, str) or not isinstance(adjunct, str) or not adjunct.lower().endswith('.pdf'):
                    continue
                title = html.fragment_fromstring(title, create_parent=True).text_content()
                entries.append((title, urljoin('https://static.cninfo.com.cn/', adjunct), item.get('announcementTime')))
        except (ValueError, KeyError, TypeError, etree.LxmlError):
            raise SourceError('CNINFO returned an invalid announcement response') from None
    elif source['type'] == 'newsnow':
        base = settings['newsnow_api_url']
        host = urlsplit(base).hostname
        data, _ = fetch_public(base + '?' + urlencode({'id': source['platform']}), [host], settings['source_timeout'])
        try:
            result = json.loads(data)
            items = result['items']
            if result.get('status') not in (None, 'success', 'cache') or not isinstance(items, list):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SourceError('NewsNow returned an invalid source response') from None
        entries = []
        for item in items:
            if isinstance(item, dict):
                extra = item.get('extra') if isinstance(item.get('extra'), dict) else {}
                entries.append((item.get('title'), item.get('url'), item.get('pubDate') or extra.get('date')))
    else:
        data, final_url = fetch_public(source['url'], source['allowed_domains'], settings['source_timeout'])
        try:
            tree = html.fromstring(data)
        except (ValueError, TypeError, etree.LxmlError):
            raise SourceError('Source returned invalid HTML') from None
        entries = []
        pattern = re.compile(source['link_pattern'])
        for anchor in tree.xpath('//a[@href]'):
            try:
                url = urljoin(final_url, anchor.get('href', ''))
                matches = pattern.search(urlsplit(url).path)
            except ValueError:
                continue
            if not matches:
                continue
            title = ' '.join(anchor.text_content().split()) or anchor.get('title', '').strip()
            parent = next(iter(anchor.iterancestors('li')), None)
            date_text = parent.text_content()[:500] if parent is not None else (anchor.tail or '')
            entries.append((title, url, date_text))
        if not entries:
            raise SourceError('No article links found; check source layout or access restrictions')
    candidates, seen = [], set()
    for title, url, published in entries:
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            continue
        # Upgrade links on configured source domains to HTTPS before fetching.
        if url.startswith('http://'):
            url = 'https://' + url[7:]
        try:
            url = checked_url(url, source['allowed_domains'])
        except SourceError:
            continue
        key = canonical_url(url)
        if key not in seen:
            candidates.append(dict(title=title.strip(), url=url, published_date=parse_date(published), source=source))
            seen.add(key)
    return candidates[:30]


def rank_candidates(candidates, extra_keywords=()):
    def matches(item, words):
        title = item['title'].casefold()
        return sum(word.casefold() in title for word in words)
    ranked, used = [], set()
    for _, words in TOPICS:
        options = [item for item in candidates if canonical_url(item['url']) not in used and matches(item, words)]
        if options:
            best = max(options, key=lambda item: matches(item, words) + 3 * matches(item, extra_keywords))
            ranked.append(best)
            used.add(canonical_url(best['url']))
    remaining = sorted(candidates, key=lambda item: sum(matches(item, words) for _, words in TOPICS)
                       + 3 * matches(item, extra_keywords), reverse=True)
    # Give each source with relevant news a turn before taking more from the same feed.
    covered = {item['source']['id'] for item in ranked}
    for item in remaining:
        key = canonical_url(item['url'])
        relevant = any(matches(item, words) for _, words in TOPICS) or matches(item, extra_keywords)
        if key not in used and item['source']['id'] not in covered and relevant:
            ranked.append(item)
            used.add(key)
            covered.add(item['source']['id'])
    ranked.extend(item for item in remaining if canonical_url(item['url']) not in used)
    return ranked


def original_html_title(data, extracted_title):
    """Read title metadata/visible headings without evaluating publisher scripts."""
    def clean(value):
        if not isinstance(value, str):
            return None
        value = ' '.join(value.split())
        if (not value or len(value) > 400
                or re.search(r'document\s*\.|\b(?:var|let|const)\s+\w+\s*=|'
                             r'<\s*/?(?:script|br)\b|\bfunction\s*\(|\bif\s*\(', value, re.I)):
            return None
        return value
    try:
        tree = html.fromstring(data)
        for node in tree.xpath('//script|//style|//noscript|//template'):
            node.drop_tree()
        # ArticleTitle is used by Chinese government publishing systems.
        for key in ('articletitle', 'og:title', 'twitter:title'):
            for node in tree.xpath('//meta[@content]'):
                if (node.get('name') or node.get('property') or '').lower() == key:
                    title = clean(node.get('content'))
                    if title:
                        return title
        for node in tree.xpath('//h1'):
            title = clean(node.text_content())
            if title:
                return title
        title = clean(extracted_title)
        if title:
            return title
        for node in tree.xpath('//title'):
            title = clean(node.text_content())
            if title:
                return title
    except (ValueError, TypeError, etree.LxmlError):
        pass
    # Never synthesize a headline from JavaScript or an aggregator's shortened title.
    raise SourceError('Article has no verifiable original title without script content')


def extract_document(data, url, candidate, today):
    if candidate['source']['type'] == 'cninfo':
        if not data.startswith(b'%PDF-'):
            raise SourceError('Announcement did not return a PDF')
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise ValueError
            text = '\n'.join(page.extract_text() or '' for page in reader.pages[:12])
            if len(text.strip()) < 120:
                raise ValueError
        except Exception:
            raise SourceError('Announcement PDF has no usable text or could not be parsed') from None
        published = candidate['published_date']
        if not published:
            raise SourceError('Announcement publication date is unavailable')
        if not today - timedelta(days=2) <= published <= today:
            return None
        return dict(title=candidate['title'], url=canonical_url(url), source=candidate['source']['name'],
                    published_date=str(published), text=text[:8000],
                    text_truncated=len(text) > 8000 or len(reader.pages) > 12)
    try:
        document = trafilatura.bare_extraction(data, url=url, with_metadata=True,
                                              include_comments=False, include_tables=True,
                                              favor_precision=True,
                                              date_extraction_params={'original_date': True, 'extensive_search': False})
        result = document.as_dict() if document else None
    except (ValueError, TypeError, etree.LxmlError):
        raise SourceError('Article HTML could not be parsed') from None
    if not result:
        raise SourceError('Article body could not be extracted')
    title = original_html_title(data, result.get('title'))
    text = result.get('text')
    published = parse_date(result.get('date')) or candidate['published_date']
    if not isinstance(title, str) or not title.strip() or not isinstance(text, str) or len(text.strip()) < 120:
        raise SourceError('Article original title or body is missing or too short')
    if not published:
        raise SourceError('Article publication date is unavailable')
    if not today - timedelta(days=2) <= published <= today:
        return None
    return dict(title=' '.join(title.split()), url=canonical_url(url), source=candidate['source']['name'],
                published_date=str(published), text=text[:8000], text_truncated=len(text) > 8000)


def collect_documents(settings, today, excluded_urls=(), excluded_titles=()):
    sources = load_sources(settings['sources_path'])
    candidates, problems = [], []
    success = 0
    def get_source(source):
        try:
            return discover(source, settings, today), None
        except SourceError as exc:
            return [], f'Source {source["id"]}: {exc}'
    with ThreadPoolExecutor(max_workers=4) as pool:
        for source, (items, error) in zip(sources, pool.map(get_source, sources)):
            if error:
                problems.append(error)
                print(error, flush=True)
            else:
                success += 1
                print(f'Source {source["id"]}: {len(items)} candidate links', flush=True)
                candidates.extend(items)
    if not success:
        raise SourceError('All configured news sources failed; check source access and configuration')
    def title_key(value):
        import unicodedata
        return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())
    seen, seen_titles = set(excluded_urls), set(excluded_titles)
    available = []
    for item in candidates:
        key = canonical_url(item['url'])
        published = item['published_date']
        if (key in seen or title_key(item['title']) in seen_titles
                or (published and not today - timedelta(days=2) <= published <= today)):
            continue
        seen.add(key)
        available.append(item)
    # Lists without publication dates often contain old reports; try dated current links first.
    available.sort(key=lambda item: item['published_date'] or date.min, reverse=True)
    documents = []
    attempted = stale = duplicates = 0
    ranked = rank_candidates(available, settings['news_keywords'])
    print(f'Candidate budget: {len(ranked)} available; up to {settings["max_articles"]} downloads', flush=True)
    for item in ranked[:settings['max_articles']]:
        attempted += 1
        try:
            data, url = fetch_public(item['url'], item['source']['allowed_domains'], settings['source_timeout'])
            document = extract_document(data, url, item, today)
            if (document and canonical_url(document['url']) not in excluded_urls
                    and title_key(document['title']) not in seen_titles):
                documents.append(document)
                seen_titles.add(title_key(document['title']))
            elif document is None:
                stale += 1
            else:
                duplicates += 1
        except SourceError as exc:
            message = f'Article from {item["source"]["id"]}: {exc}'
            problems.append(message)
            print(message, flush=True)
    print(f'Article attempts: {attempted}; outside date window: {stale}; duplicates: {duplicates}', flush=True)
    print(f'Fetched {len(documents)} original articles; {len(problems)} source/article failures', flush=True)
    return documents, problems

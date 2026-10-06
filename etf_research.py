"""Public ETF research: attributable data, bounded reads, no portfolio inputs.

Uses only Python's standard library. Reported chart returns are never promoted
to official returns; daily portfolio changes are never called executed trades.
"""
from __future__ import annotations

import copy
import contextvars
import io
import http.cookiejar
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
BASIC_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap47_L'
HOLDERS_URL = 'https://openapi.tdcc.com.tw/v1/opendata/1-5'
TPEX_URL = 'https://info.tpex.org.tw/api/etfFilter'
TPEX_ACTIVE_URL = 'https://www.tpex.org.tw/www/zh-tw/ETF/list?type=active&response=json'
OFFICIAL_PAGE = 'https://www.twse.com.tw/zh/ETFortune/etfInfo/'
_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.RLock()
_KEY_LOCKS: dict[str, threading.Lock] = {}
_QUOTE_LOCK = threading.Lock()
_OUTBOUND = threading.BoundedSemaphore(4)
_DEADLINE = contextvars.ContextVar('etf_deadline', default=None)
MAX_BYTES = 24_000_000
EZMONEY = {'00981A': '49YTW', '00403A': '63YTW', '00988A': '61YTW', '00411A': '64YTW', '00987D': '65YTW'}
FH = {'00991A': 'ETF23', '00409A': 'ETF26', '00986D': 'ETF25', '00998A': 'ETF24'}
AB = {'00404A': 'TW00000404A5', '00980D': 'TW00000980D8', '00984D': 'TW00000984D0'}
AB_URL = 'https://webapi.alliancebernstein.com/v2/funds/tw/zh-tw/investor/'
YUANTA = {'00990A'}
YUANTA_URL = 'https://etfapi.yuantaetfs.com/ectranslation/api/bridge?APIType=ETFAPI&CompanyName=YUANTAFUNDS&PageName=%2FtradeInfo%2Fpcf%2F00990A&DeviceId=null&FuncId=PCF%2FDaily&ticker=00990A&AppName=ETF&Platform=ETF&Device=3'


def now_iso() -> str:
    return datetime.now(TAIPEI).isoformat(timespec='seconds')


def number(value):
    try:
        n = float(str(value).replace(',', '').replace('%', '').strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def date_iso(value) -> str | None:
    s = str(value or '').strip()
    match = re.fullmatch(r'(\d{3,4})[/.-]?(\d{2})[/.-]?(\d{2})', s)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    if year < 1911:
        year += 1911
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def read_public(url: str) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'ETFResearch/1.0', 'Accept': '*/*'})
    # Some public issuer downloads establish an anonymous session by redirecting
    # to the same URL. Retain those cookies only for this single public read.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    deadline = _DEADLINE.get()
    remaining = deadline - time.monotonic() if deadline is not None else 8
    if remaining <= 0:
        raise TimeoutError('public read deadline exceeded')
    if not _OUTBOUND.acquire(timeout=min(2, remaining)):
        raise TimeoutError('public upstream busy')
    try:
        with opener.open(request, timeout=min(8, remaining)) as response:
            chunks, size = [], 0
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('public read deadline exceeded')
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('public response exceeds size limit')
            body = b''.join(chunks)
    finally:
        _OUTBOUND.release()
    if len(body) > MAX_BYTES:
        raise ValueError('public response exceeds size limit')
    return body


def cached(key, seconds, producer):
    with _LOCK:
        key_lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with key_lock:
        value = _CACHE.get(key)
        if value and time.monotonic() - value[0] < seconds:
            return copy.deepcopy(value[1])
        result = producer()
        with _LOCK:
            if len(_CACHE) >= 256:
                oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
                _CACHE.pop(oldest, None)
            _CACHE[key] = (time.monotonic(), result)
        return copy.deepcopy(result)


def normalize_basic(row: dict) -> dict | None:
    code = str(row.get('基金代號', '')).strip()
    if not re.fullmatch(r'00\d{2,4}[ABD]?', code):
        return None
    fund_type = str(row.get('基金類型') or '')
    return {
        'code': code, 'name': str(row.get('基金簡稱') or code),
        'fund_type': fund_type, 'active': '主動式' in fund_type,
        'market': 'bond' if '債券' in fund_type or code.endswith(('B', 'D')) else ('global' if '國外' in fund_type or '境外' in fund_type or row.get('是否包含國外成分股') == '是' else 'tw'),
        'benchmark': str(row.get('績效指標中文名稱') or row.get('標的指數/追蹤指數名稱') or '未揭露'),
        'listing_date': date_iso(row.get('上市日期')),
        'units': number(row.get('發行單位數/轉換數')),
        'basic_as_of': date_iso(row.get('出表日期')),
        'basic_source': BASIC_URL,
        # This official dataset does not contain fund fees or NAV.
        'management_fee': None, 'custody_fee': None,
        'fee_status': '此官方資料表未提供，請核對投信公開說明書',
        'official_url': OFFICIAL_PAGE + code,
        'plain': not any(s in fund_type for s in ['槓桿', '反向', '期貨']),
    }


def basic_document() -> dict:
    def load():
        try:
            raw = json.loads(read_public(BASIC_URL))
            if not isinstance(raw, list):
                raise ValueError('unexpected official basic schema')
            rows = [item for row in raw if isinstance(row, dict) and (item := normalize_basic(row))]
            if len(rows) < 20:
                raise ValueError('incomplete official basic response')
            return {'rows': rows, 'status': 'live_official', 'fetched_at': now_iso(), 'source': BASIC_URL}
        except Exception as exc:
            baseline = json.loads((ROOT / 'web/etf-basics.json').read_text(encoding='utf-8'))
            return {**baseline, 'status': 'baseline_fallback', 'error': type(exc).__name__}
    return cached('basics', 1800, load)


def normalize_holders(raw: list) -> dict:
    by_code = {}
    for raw_row in raw:
        row = {k.lstrip('\ufeff').strip(): v for k, v in raw_row.items()}
        if str(row.get('持股分級', '')).strip() != '17':
            continue
        code = str(row.get('證券代號', '')).strip()
        date = date_iso(row.get('資料日期'))
        count = number(row.get('人數'))
        if date and count is not None and count >= 0 and date <= datetime.now(TAIPEI).date().isoformat():
            if code not in by_code or date > by_code[code]['as_of']:
                by_code[code] = {'code': code, 'holders': int(count), 'as_of': date}
    if not by_code:
        raise ValueError('no dated official holder totals')
    # One comparable date; never rank rows from mixed reporting weeks.
    latest = max(r['as_of'] for r in by_code.values())
    return {'rows': [r for r in by_code.values() if r['as_of'] == latest], 'as_of': latest, 'source': HOLDERS_URL, 'fetched_at': now_iso()}


def normalize_tpex(raw: dict, active_codes: set[str]) -> list[dict]:
    rows = []
    for item in raw.get('data', []):
        code = str(item.get('stockNo') or '').strip()
        if not re.fullmatch(r'00\d{2,4}[ABD]?', code):
            continue
        name = str(item.get('stockName') or code)
        rows.append({'code': code, 'name': name, 'active': code in active_codes, 'market': 'bond' if code.endswith(('B', 'D')) else 'global', 'fund_type': '上櫃主動式ETF' if code in active_codes else '上櫃原型ETF', 'listing_date': date_iso(item.get('listingDate')), 'benchmark': str(item.get('indexName') or '未揭露'), 'units': None, 'basic_as_of': None, 'basic_source': TPEX_URL, 'basic_fetched_at': now_iso(), 'official_url': 'https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=active&code=' + code + '&response=json' if code in active_codes else 'https://info.tpex.org.tw/zh-tw/etf', 'plain': True, 'trading_market': 'TPEx', 'quote_symbol': code + '.TWO', 'yahoo_url': 'https://tw.stock.yahoo.com/quote/' + code + '.TWO', 'issuer': str(item.get('issuer') or '')})
    if len(rows) < 20:
        raise ValueError('incomplete TPEx ETF catalog')
    return rows


def tpex_document() -> dict:
    def load():
        try:
            active = json.loads(read_public(TPEX_ACTIVE_URL))
            if active.get('stat') != 'ok':
                raise ValueError('invalid TPEx active list')
            active_codes = {r[0] for r in active['tables'][0]['data']}
            rows = normalize_tpex(json.loads(read_public(TPEX_URL)), active_codes)
            return {'rows': rows, 'status': 'live_official', 'fetched_at': now_iso(), 'source': TPEX_URL}
        except Exception as exc:
            baseline = json.loads((ROOT / 'web/etf-tpex.json').read_text(encoding='utf8'))
            return {**baseline, 'status': 'baseline_official', 'error': type(exc).__name__}
    return cached('tpex', 1800, load)


def holder_document() -> dict:
    def load():
        try:
            data = normalize_holders(json.loads(read_public(HOLDERS_URL)))
            return {**data, 'status': 'live_official_weekly'}
        except Exception as exc:
            baseline_path = ROOT / 'web/etf-holders.json'
            if baseline_path.exists():
                return {**json.loads(baseline_path.read_text(encoding='utf8')), 'status': 'baseline_official_weekly', 'error': type(exc).__name__}
            return {'rows': [], 'status': 'unavailable', 'error': type(exc).__name__}
    return cached('holders', 21600, load)


def category(row: dict) -> str:
    name = row.get('name', '')
    if row.get('market') == 'bond':
        if '非投' in name:
            return '非投資等級債'
        if any(s in name for s in ['1-3', '0-5', '短期']):
            return '短天期債'
        if any(s in name for s in ['美債20', '20年美', '公債20']):
            return '長天期公債'
        return '投資級債'
    if row.get('active'):
        return '主動股票'
    if row.get('market') == 'global':
        return '全球與海外股票'
    if any(s in name for s in ['股息', '高息', '優息', '填息']):
        return '股息策略'
    if any(s in name for s in ['半導體', '科技', '電子', 'IC']):
        return '產業主題'
    return '核心市值'


GUIDES = [
    ('核心市值', '長期累積與台股核心配置', '比較總費用、追蹤差異、成交流動性與台積電集中度；以分批研究為主。', '市值加權仍集中台灣與大型科技股。'),
    ('股息策略', '規律現金流需求', '同時比較近一年實際配息、含息總報酬、配息來源及填息；最新一期乘配息次數只是年化試算。', '配息會自淨值扣除，高配息不代表高總報酬。'),
    ('產業主題', '可承擔波動的衛星配置', '檢視產業集中、成分重疊及估值；人氣與短期冠軍不足以決定買點。', '單一產業回撤可能大於大盤。'),
    ('全球與海外股票', '分散地區與產業', '先區分廣泛市場、單一國家與窄主題，檢視匯率、交易時差、費用與折溢價。', '海外題材仍可能高度重疊；台幣價格受匯率影響。'),
    ('短天期債', '較低利率敏感度的債券配置', '核對存續期間、信用品質、匯率及實際總報酬。', '短債仍有匯率與信用風險，並非存款。'),
    ('長天期公債', '可承受利率波動的配置', '比較存續期間與利率情境，避免把固定配息當成保本。', '利率上升時長債價格可能大幅下跌。'),
    ('投資級債', '票息與信用品質平衡', '核對平均信評、存續期間、信用利差、產業分布與匯率。', '投資級債仍有信用利差及價格損失風險。'),
    ('非投資等級債', '承擔信用風險的收益配置', '檢視違約風險、信用利差與股票風險相關性。', '不能當作低風險避險部位。'),
    ('主動股票', '可持續追蹤經理人策略的配置', '核對費用、完整持股、集中度及相同起訖日含息績效。', '新基金成立以來報酬不能直接與完整年度報酬排名。'),
]


def catalog() -> dict:
    seed = json.loads((ROOT / 'web/etf-seed.json').read_text(encoding='utf-8'))
    basics = basic_document()
    tpex = tpex_document()
    holder_data = holder_document()
    holder_by_code = {r['code']: r for r in holder_data['rows']}
    official = {r['code']: r for r in basics['rows']}
    for row in tpex['rows']:
        official[row['code']] = row
    rows = []
    seeds = {r['code']: r for r in seed['rows']}
    fee_path = ROOT / 'web/etf-fees.json'
    fees = json.loads(fee_path.read_text(encoding='utf8')) if fee_path.exists() else {}
    codes = list(seeds) + [r['code'] for r in official.values() if (r.get('active') or r.get('plain', not any(s in r.get('fund_type', '') for s in ['槓桿', '反向', '期貨']))) and r['code'] not in seeds]
    for code in codes:
        chart = seeds.get(code, {})
        row = {**official.get(code, {}), **chart, 'code': code}
        # The official name and product classification take priority.
        if code in official:
            row.update({k: official[code][k] for k in ['name', 'fund_type', 'active']})
        if code in fees:
            row.update(fees[code])
        else:
            row.setdefault('name', code)
            row.setdefault('active', code.endswith(('A', 'D')))
            row.setdefault('official_url', OFFICIAL_PAGE + code)
            row.setdefault('basic_as_of', None)
            row.setdefault('basic_source', None)
        row.setdefault('market', 'bond' if code.endswith(('B', 'D')) else 'tw')
        row.setdefault('trading_market', 'TWSE')
        row.setdefault('quote_symbol', code + ('.TWO' if row['trading_market'] == 'TPEx' else '.TW'))
        row.setdefault('yahoo_url', 'https://tw.stock.yahoo.com/quote/' + row['quote_symbol'])
        row['chart_snapshot'] = copy.deepcopy(chart) if chart.get('ranking_as_of') else None
        holder = holder_by_code.get(code)
        if holder:
            row.update(holders=holder['holders'], ranking_as_of=holder['as_of'], ranking_status='official_weekly', holder_source=HOLDERS_URL, popularity_rank=None)
            # A chart month change must not be applied to a later weekly count.
            row['holders_month_change'] = chart.get('holders_month_change') if holder['as_of'] == chart.get('ranking_as_of') else None
        elif holder_data['rows']:
            row.update(holders=None, ranking_as_of=None, ranking_status='missing_official_total', popularity_rank=None)
        row['category'] = category(row)
        row['holdings_supported'] = code in EZMONEY or code in FH or code in AB or code in YUANTA
        row['ranking_age_days'] = (datetime.now(TAIPEI).date() - datetime.fromisoformat(row['ranking_as_of']).date()).days if row.get('ranking_as_of') else None
        row['recommendation_status'] = '分類研究候選；尚未完成費用與同期間含息績效比較，無買賣判定'
        rows.append(row)
    if holder_data['rows']:
        for market in ['tw', 'global', 'bond']:
            group = sorted([r for r in rows if r['market'] == market and r.get('holders') is not None and r.get('ranking_status') == 'official_weekly'], key=lambda r: (-r['holders'], r['code']))
            for rank, row in enumerate(group, 1):
                row['popularity_rank'] = rank
    guides = []
    for label, purpose, criteria, risk in GUIDES:
        candidates = sorted([r for r in rows if r['category'] == label and r.get('holders') is not None and r.get('ranking_status') == 'official_weekly'], key=lambda r: (-r['holders'], r['code']))[:4]
        guides.append({'category': label, 'purpose': purpose, 'criteria': criteria, 'risk': risk, 'codes': [r['code'] for r in candidates], 'selection_basis': '最新同一週集保受益人數的同類排序，不是預期報酬排序'})
    return {
        'generated_at': now_iso(), 'rows': rows, 'guides': guides,
        'basic_status': {k: v for k, v in basics.items() if k != 'rows'},
        'tpex_status': {k: v for k, v in tpex.items() if k != 'rows'},
        'holder_status': {k: v for k, v in holder_data.items() if k != 'rows'},
        'active_count': sum(r['active'] for r in rows),
        'holdings_supported_count': sum(r['holdings_supported'] for r in rows),
        'sources': seed['sources'], 'video': seed['video'],
        'ranking_basis': '各類集保受益人數前20大；取分級17總計，不重複加總各分級。集保週資料，不是盤中即時人氣。',
        'scope': 'TWSE及TPEx官方清單中的原型及主動ETF；槓桿／反向／商品期貨不列分類候選。未取得同週集保總計人數的基金不進人氣排名。',
        'disclosure': '分類候選供研究；尚未取得的費用、即時淨值、官方含息報酬與交易紀錄顯示NA，不推測補值。',
    }


def validate_codes(raw: str) -> list[str]:
    codes = list(dict.fromkeys(raw.upper().split(',')))
    if not codes or len(codes) > 20 or any(not re.fullmatch(r'00\d{2,4}[ABD]?', c) for c in codes):
        raise ValueError('每次限1至20個ETF代碼')
    allowed = {r['code'] for r in catalog()['rows']}
    if any(c not in allowed for c in codes):
        raise ValueError('只接受研究中心已收錄的ETF')
    return codes


def quotes(raw: str, fetch_quote) -> dict:
    codes = validate_codes(raw)
    # Cache each public security once, rather than every arbitrary combination.
    # Serializing refreshes prevents overlapping requests from duplicating reads.
    with _QUOTE_LOCK:
        missing = [c for c in codes if ('quote:' + c not in _CACHE or time.monotonic() - _CACHE['quote:' + c][0] >= 60)]
        if missing:
            rows = {r['code']: r for r in catalog()['rows']}
            mapping = {c + '.TW': rows[c].get('quote_symbol', c + '.TW') for c in missing}
            import inspect
            if 'yahoo_symbol_map' in inspect.signature(fetch_quote).parameters:
                payload = fetch_quote([c + '.TW' for c in missing], yahoo_symbol_map=mapping)
            else:
                payload = fetch_quote([c + '.TW' for c in missing])
            results = {r['symbol'].split('.')[0]: r for r in payload.get('quoteResponse', {}).get('result', [])}
            with _LOCK:
                for c in missing:
                    _CACHE['quote:' + c] = (time.monotonic(), results.get(c))
        from app import is_tw_market_session
        return {'quoteResponse': {'result': [copy.deepcopy(_CACHE['quote:' + c][1]) for c in codes if _CACHE.get('quote:' + c, (0, None))[1] is not None], 'error': None}, 'updated_at': now_iso(), 'fetchedAt': now_iso(), 'marketSession': is_tw_market_session(), 'research_only': True}


def xlsx_rows(body: bytes) -> list[list[str]]:
    """Read a small issuer workbook without a runtime dependency."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 20_000_000:
            raise ValueError('oversized workbook')
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [''.join(si.itertext()) for si in root]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        result = []
        for row in sheet.findall('.//m:sheetData/m:row', ns):
            cells = {}
            for cell in row.findall('m:c', ns):
                ref = re.match(r'([A-Z]+)', cell.get('r', 'A1'))[1]
                col = 0
                for ch in ref:
                    col = col * 26 + ord(ch) - 64
                v = cell.find('m:v', ns)
                text = v.text if v is not None and v.text is not None else ''
                if cell.get('t') == 's':
                    text = shared[int(text)]
                elif cell.get('t') == 'inlineStr':
                    text = ''.join(cell.find('m:is', ns).itertext())
                cells[col - 1] = text
            if cells:
                result.append([cells.get(i, '') for i in range(max(cells) + 1)])
        return result


def parse_ezmoney(body: bytes, code: str, source: str) -> dict:
    rows = xlsx_rows(body)
    effective_date = as_of = units = nav = aum_twd = None
    stocks = []
    derivatives = []
    columns = None
    asset_type = None
    for row in rows:
        joined = ' '.join(row)
        dates = re.findall(r'\d{3,4}/\d{2}/\d{2}', joined)
        if dates and effective_date is None:
            effective_date = date_iso(dates[0])
        if '每受益權單位淨資產價值' in joined and dates:
            as_of = date_iso(dates[0])
            nav = number(re.sub(r'^NTD\s*', '', row[-1]))
        if '基金淨資產價值(元)' in joined:
            aum_twd = number(re.sub(r'^NTD\s*', '', row[-1]))
        if '已發行受益權單位總數' in joined:
            units = number(row[-1])
        if '期貨代號' in row:
            asset_type = 'futures'
            columns = {'code': row.index('期貨代號'), 'name': row.index('期貨名稱'), 'shares': row.index('口數'), 'weight_pct': row.index('持股權重')}
            continue
        if '持股權重' in row and any(k in row for k in ['股票代號', '債券代號', '基金代號']):
            asset_type = 'stock' if '股票代號' in row else 'bond' if '債券代號' in row else 'fund'
            columns = {'code': 0, 'name': 1, 'shares': 2, 'weight_pct': row.index('持股權重')}
            continue
        if columns:
            values = {k: row[v] if v < len(row) else '' for k, v in columns.items()}
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{1,30}', values['code']) and number(values['weight_pct']) is not None:
                weight = number(values['weight_pct'])
                # Excel stores percentage-formatted numeric cells as fractions.
                if weight is not None and '%' not in values['weight_pct']:
                    weight *= 100
                (derivatives if asset_type == 'futures' else stocks).append({'code': values['code'], 'name': values['name'], 'shares': number(values['shares']), 'weight_pct': weight, 'asset_type': asset_type, 'quantity_unit': '口數' if asset_type == 'futures' else '面額' if asset_type == 'bond' else '股數' if asset_type == 'stock' else '單位數'})
    if not as_of or not effective_date or not stocks or as_of > datetime.now(TAIPEI).date().isoformat():
        raise ValueError('issuer workbook lacks dated stock holdings')
    if len({s['code'] for s in stocks}) != len(stocks):
        raise ValueError('duplicate stock codes in workbook')
    weights = [s['weight_pct'] for s in stocks if s['weight_pct'] is not None]
    if any(w < 0 or w > 100 for w in weights) or sum(weights) > 101:
        raise ValueError('invalid holding weights')
    return {'code': code, 'as_of': as_of, 'effective_date': effective_date, 'units': units, 'aum_twd': aum_twd, 'nav': nav, 'holdings': stocks, 'derivatives': derivatives, 'source': source, 'fetched_at': now_iso(), 'scope': '官方PCF現貨證券部位（股票／債券／基金）；期貨另列；現金及其他資產未列入表內'}


def ezmoney_snapshot(code: str, effective: str | None = None) -> dict:
    today = datetime.now(TAIPEI)
    params = {'fundCode': EZMONEY[code], 'specificDate': 'true' if effective else 'false', 'date': f'{today.year - 1911:03d}/{today.month:02d}/{today.day:02d}'}
    if effective:
        day = datetime.fromisoformat(effective)
        params['date'] = f'{day.year - 1911:03d}/{day.month:02d}/{day.day:02d}'
    url = 'https://www.ezmoney.com.tw/ETF/Transaction/PCFExcelNPOI?' + urllib.parse.urlencode(params)
    result = parse_ezmoney(read_public(url), code, url)
    if effective and result['effective_date'] != effective:
        raise ValueError('issuer returned a different historical date')
    return result


def parse_fh(body: bytes, code: str, source: str) -> dict:
    rows = xlsx_rows(body)
    metadata = {}
    positions, derivatives = [], []
    columns = None
    asset_type = None
    as_of = None
    pending = None
    for row in rows:
        if pending:
            metadata[pending] = number(row[0])
            pending = None
        for label, key in [('基金資產淨值', 'aum_twd'), ('基金在外流通單位數', 'units'), ('基金每單位淨值', 'nav')]:
            if row[0] == label:
                pending = key
        if row[0].startswith('日期:'):
            as_of = date_iso(row[0].split(':', 1)[1].strip())
        if any('權重' in c for c in row) and any(k in row[0] for k in ['代號', '代碼']):
            asset_type = 'futures' if '期貨' in row[0] else 'fund' if '基金' in row[0] else 'bond' if '面額' in row else 'stock'
            columns = {'code': 0, 'name': 1, 'shares': 2, 'weight_pct': next(i for i,c in enumerate(row) if '權重' in c)}
            continue
        if columns and len(row) > columns['weight_pct']:
            weight = number(row[columns['weight_pct']])
            if weight is None:
                continue
            qty = re.sub(r'^\([A-Z]{3}\)', '', row[2])
            item = {'code': row[0], 'name': row[1], 'shares': number(qty), 'quantity_unit': '面額' if asset_type == 'bond' else '口數' if asset_type == 'futures' else '股數' if asset_type == 'stock' else '單位數', 'asset_type': asset_type, 'weight_pct': weight if '%' in row[columns['weight_pct']] else weight * 100}
            (derivatives if asset_type == 'futures' else positions).append(item)
    if not as_of or not positions or as_of > datetime.now(TAIPEI).date().isoformat():
        raise ValueError('no dated issuer holdings')
    if len({p['code'] for p in positions}) != len(positions) or any(p['weight_pct'] < 0 or p['weight_pct'] > 100 for p in positions) or sum(p['weight_pct'] for p in positions) > 102:
        raise ValueError('invalid issuer holding weights')
    return {'code': code, 'as_of': as_of, 'effective_date': as_of, **metadata, 'holdings': positions, 'derivatives': derivatives, 'source': source, 'fetched_at': now_iso(), 'scope': '官方完整現貨證券部位（股票／債券／基金）；期貨另列；其餘權重為現金及其他資產'}


def fh_snapshot(code: str, date: str) -> dict:
    url = 'https://www.fhtrust.com.tw/api/assetsExcel/' + FH[code] + '/' + date.replace('-', '')
    result = parse_fh(read_public(url), code, url)
    if result['as_of'] != date:
        raise ValueError('issuer returned a different date')
    return result


def fh_pair(code: str) -> tuple[dict, dict | None]:
    snapshots = []
    today = datetime.now(TAIPEI).date()
    for days in range(8):
        day = today - timedelta(days=days)
        if day.weekday() >= 5:
            continue
        try:
            snapshots.append(fh_snapshot(code, day.isoformat()))
        except Exception:
            continue
        if len(snapshots) == 2:
            break
    if not snapshots:
        raise ValueError('no recent dated issuer holdings')
    return snapshots[0], snapshots[1] if len(snapshots) > 1 else None


def parse_ab(raw: dict, code: str, source: str, basket: dict | None = None) -> dict:
    positions, derivatives, dates = [], [], set()
    for group in raw.get('domesticHoldings', []):
        date = datetime.strptime(group['asOfDate'], '%m/%d/%Y').date().isoformat()
        dates.add(date)
        label = group.get('holdingCategory', '')
        asset_type = 'stock' if 'equity' in label else 'bond' if 'bond' in label else 'options' if 'options' in label else 'futures' if 'futures' in label else 'other'
        if asset_type == 'other':
            continue
        for item in group.get('holdings', []):
            weight = number(item.get('holdingPerc'))
            if weight is None:
                raise ValueError('missing official weight')
            row = {'code': str(item.get('holdingCode') or item.get('holding') or '').strip(), 'name': str(item.get('holding') or ''), 'shares': number(item.get('holdingShares')), 'weight_pct': weight, 'asset_type': asset_type, 'quantity_unit': '股數' if asset_type == 'stock' else '面額' if asset_type == 'bond' else '口數'}
            (positions if asset_type in ('stock', 'bond') else derivatives).append(row)
    if len(dates) != 1 or not positions:
        raise ValueError('no complete dated holdings')
    as_of = dates.pop()
    if as_of > datetime.now(TAIPEI).date().isoformat() or len({p['code'] for p in positions}) != len(positions) or any(p['weight_pct'] < 0 or p['weight_pct'] > 100 for p in positions) or sum(p['weight_pct'] for p in positions) > 102:
        raise ValueError('invalid issuer holdings')
    matched = basket if basket and basket.get('navAsOfDate') == as_of and basket.get('asOfDate') == as_of else {}
    return {'code': code, 'as_of': as_of, 'effective_date': matched.get('announcementDate'), 'units': number(matched.get('shares')), 'aum_twd': number(matched.get('aum')) if matched.get('currency') == 'TWD' else None, 'nav': number(matched.get('nav')), 'holdings': positions, 'derivatives': derivatives, 'source': source, 'fetched_at': now_iso(), 'scope': '官方現貨股票／債券部位；選擇權與期貨另列；未推定成分市值幣別'}


def ab_snapshot(code: str, date: str | None = None) -> dict:
    base = AB_URL + AB[code]
    suffix = '?date=' + date if date else ''
    url = base + '/holdings' + suffix
    raw = json.loads(read_public(url))
    try:
        basket = json.loads(read_public(base + '/basket' + suffix))
        if date and basket.get('asOfDate') != date:
            # Issuer basket dates are announcement dates (T+1), whereas holdings
            # dates are valuation dates. Require a matching date before units.
            for gap in range(1, 5):
                effective = (datetime.fromisoformat(date) + timedelta(days=gap)).date().isoformat()
                candidate = json.loads(read_public(base + '/basket?date=' + effective))
                if candidate.get('asOfDate') == date and candidate.get('navAsOfDate') == date:
                    basket = candidate
                    break
    except Exception:
        basket = None
    result = parse_ab(raw, code, url, basket)
    if date and result['as_of'] != date:
        raise ValueError('issuer ignored historical date')
    return result


def ab_pair(code: str) -> tuple[dict, dict | None]:
    current = ab_snapshot(code)
    for gap in range(1, 5):
        day = datetime.fromisoformat(current['as_of']) - timedelta(days=gap)
        if day.weekday() >= 5:
            continue
        try:
            return current, ab_snapshot(code, day.date().isoformat())
        except Exception:
            continue
    return current, None


def parse_yuanta(raw: dict, code: str, source: str) -> dict:
    meta = raw.get('PCF') or {}
    date = date_iso(meta.get('trandate'))
    if meta.get('markcd') != code or not date or date > datetime.now(TAIPEI).date().isoformat():
        raise ValueError('invalid issuer product or date')
    positions, derivatives = [], []
    for key, asset, unit in [('StockWeights', 'stock', '股數'), ('ETFWeights', 'fund', '單位數'), ('BondWeights', 'bond', '面額'), ('FutureWeights', 'futures', '口數')]:
        for item in raw.get('FundWeights', {}).get(key) or []:
            weight = number(item.get('weights'))
            if weight is None:
                raise ValueError('missing official weight')
            row = {'code': str(item.get('code') or '').strip(), 'name': str(item.get('name') or ''), 'weight_pct': weight, 'shares': number(item.get('qty')), 'quantity_unit': unit, 'asset_type': asset}
            (derivatives if asset == 'futures' else positions).append(row)
    if not positions or len({p['code'] for p in positions}) != len(positions) or any(p['weight_pct'] < 0 or p['weight_pct'] > 100 for p in positions) or sum(p['weight_pct'] for p in positions) > 102:
        raise ValueError('invalid issuer holdings')
    return {'code': code, 'as_of': date, 'effective_date': date_iso(meta.get('anndate')), 'units': number(meta.get('osunit')), 'aum_twd': number(meta.get('totalav')), 'nav': number(meta.get('nav')), 'holdings': positions, 'derivatives': derivatives, 'source': source, 'fetched_at': now_iso(), 'scope': '官方PCF現貨股票／債券／ETF部位；期貨另列；現金未列入本表'}


def yuanta_pair(code: str) -> tuple[dict, dict | None]:
    current = parse_yuanta(json.loads(read_public(YUANTA_URL)), code, YUANTA_URL)
    for gap in range(1, 5):
        day = datetime.fromisoformat(current['effective_date']) - timedelta(days=gap)
        if day.weekday() >= 5:
            continue
        source = YUANTA_URL + '&date=' + day.strftime('%Y%m%d')
        try:
            previous = parse_yuanta(json.loads(read_public(source)), code, source)
            if previous['as_of'] < current['as_of']:
                return current, previous
        except Exception:
            continue
    return current, None


def compare_holdings(previous: dict | None, current: dict) -> dict:
    """Weight changes and raw shares; never infer trade notional from weights."""
    if not previous:
        return {'status': 'no_previous_snapshot', 'rows': [], 'increases_share_pct': None, 'decreases_share_pct': None, 'note': '未取得前一期完整快照，無法判定新進／出清或買賣比例。'}
    gap = (datetime.fromisoformat(current['as_of']) - datetime.fromisoformat(previous['as_of'])).days
    if gap <= 0 or gap > 7 or previous.get('scope') != current.get('scope'):
        return {'status': 'incomparable', 'rows': [], 'increases_share_pct': None, 'decreases_share_pct': None, 'note': '日期或揭露範圍不一致，不計算異動。'}
    before = {r['code']: r for r in previous['holdings']}
    after = {r['code']: r for r in current['holdings']}
    rows = []
    for code in sorted(before.keys() | after.keys()):
        old, new = before.get(code), after.get(code)
        p, n = (old or {}).get('weight_pct', 0), (new or {}).get('weight_pct', 0)
        delta = n - p if n is not None and p is not None else None
        shares_p, shares_n = (old or {}).get('shares', 0), (new or {}).get('shares', 0)
        units_p, units_n = previous.get('units'), current.get('units')
        adjusted = ((shares_n / units_n) / (shares_p / units_p) - 1) * 100 if shares_n is not None and shares_p and units_p and units_n else None
        rows.append({'code': code, 'name': (new or old)['name'], 'quantity_unit': (new or old).get('quantity_unit', '股數'), 'previous_weight_pct': p, 'weight_pct': n, 'weight_change_pp': round(delta, 5) if delta is not None else None, 'shares_change': shares_n - shares_p if shares_n is not None and shares_p is not None else None, 'quantity_per_unit_change_pct': round(adjusted, 5) if adjusted is not None else None, 'change': '新進揭露' if old is None else '不再揭露' if new is None else '持續持有'})
    increases = sum(max(r['weight_change_pp'] or 0, 0) for r in rows)
    decreases = -sum(min(r['weight_change_pp'] or 0, 0) for r in rows)
    total = increases + decreases
    return {'status': 'comparable', 'previous_as_of': previous['as_of'], 'as_of': current['as_of'], 'rows': rows, 'increases_share_pct': round(increases / total * 100, 2) if total else None, 'decreases_share_pct': round(decreases / total * 100, 2) if total else None, 'increases_pp': round(increases, 5), 'decreases_pp': round(decreases, 5), 'denominator': '現貨證券權重增加百分點合計＋減少百分點絕對值合計', 'executed_buy_ratio_pct': None, 'executed_sell_ratio_pct': None, 'note': '增加／減少權重占異動比例，不是實際買賣比例或週轉率。股數變化可能含申贖、分割及其他公司行動；官方未揭露逐筆交易，不據此宣稱經理人買賣。'}


def holdings(code: str) -> dict:
    allowed = {r['code']: r for r in catalog()['rows']}
    if code not in allowed or not allowed[code]['active']:
        raise ValueError('只接受已收錄的主動式ETF')
    if code not in EZMONEY and code not in FH and code not in AB and code not in YUANTA:
        return {'code': code, 'status': 'not_connected', 'source': allowed[code]['official_url'], 'holdings': [], 'note': '此投信官方持股資料尚未串接；請前往官方商品頁查詢，不以其他基金成分代填。'}
    def load():
        token = _DEADLINE.set(time.monotonic() + 35)
        try:
            if code in FH:
                current, previous = fh_pair(code)
            elif code in AB:
                current, previous = ab_pair(code)
            elif code in YUANTA:
                current, previous = yuanta_pair(code)
            else:
                current = ezmoney_snapshot(code)
                previous = None
                # PCF effective date follows the underlying holdings/NAV date.
                for gap in range(1, 5):
                    day = datetime.fromisoformat(current['effective_date']) - timedelta(days=gap)
                    if day.weekday() >= 5:
                        continue
                    try:
                        candidate = ezmoney_snapshot(code, day.date().isoformat())
                        if candidate['as_of'] < current['as_of']:
                            previous = candidate
                            break
                    except Exception:
                        continue
            age = (datetime.now(TAIPEI).date() - datetime.fromisoformat(current['as_of']).date()).days
            return {**current, 'status': 'official_snapshot', 'age_days': age, 'freshness': 'recent' if 0 <= age <= 4 else 'stale', 'comparison': compare_holdings(previous, current), 'top10_weight_pct': round(sum(s['weight_pct'] or 0 for s in sorted(current['holdings'], key=lambda s: s['weight_pct'] or 0, reverse=True)[:10]), 2)}
        except Exception as exc:
            baseline_path = ROOT / 'web/etf-holdings.json'
            if baseline_path.exists():
                baseline = json.loads(baseline_path.read_text(encoding='utf8')).get(code)
                if baseline:
                    current, previous = baseline['current'], baseline.get('previous')
                    age = (datetime.now(TAIPEI).date() - datetime.fromisoformat(current['as_of']).date()).days
                    return {**current, 'status': 'baseline_official', 'age_days': age, 'freshness': 'recent' if 0 <= age <= 4 else 'stale', 'comparison': compare_holdings(previous, current), 'error': type(exc).__name__, 'note': '官方即時讀取失敗，顯示已核對的官方快照；請注意資料日期。', 'top10_weight_pct': round(sum(s['weight_pct'] or 0 for s in sorted(current['holdings'], key=lambda s: s['weight_pct'] or 0, reverse=True)[:10]), 2)}
            return {'code': code, 'status': 'unavailable', 'holdings': [], 'note': '官方持股讀取失敗；無有效快照，不推測補值。', 'error': type(exc).__name__}
        finally:
            _DEADLINE.reset(token)
    return cached('holdings:' + code, 1800, load)

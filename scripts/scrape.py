"""
機票爬蟲主程式
1. 讀 routes.json（優先）或 routes.yaml
2. 即時取 USD→TWD 匯率（多 API fallback + cache）
3. 依規則列出所有合格日期組合
4. 用 fast-flights 抓 Google Flights
5. 過濾廉航、套用時段/票價/轉機限制
6. 寫入 SQLite，更新連續失敗計數，清理 >365 天舊資料
"""

import os
import sys
import json
import sqlite3
import yaml
import urllib.request
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import logging
from zoneinfo import ZoneInfo

from fast_flights import FlightData, Passengers, get_flights

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
ROUTES_YAML = ROOT / 'routes.yaml'
ROUTES_JSON = ROOT / 'routes.json'
LCC_YAML = ROOT / 'excluded_airlines.yaml'
DB_PATH = ROOT / 'data' / 'prices.db'
FX_CACHE_PATH = ROOT / 'data' / 'last_fx.json'
STATE_PATH = ROOT / 'data' / 'scrape_state.json'
TAIPEI = ZoneInfo('Asia/Taipei')

FX_DEFAULT = 32.0
OLD_DATA_DAYS = 365  # 超過幾天的舊資料會被清掉

UNCLASSIFIED_AIRLINE_NAME = '航空公司未解析'

KNOWN_DIRECT_FLIGHT_FALLBACKS = {
    ('TPE', 'CTS'): {
        'airline_code': 'JX',
        'airline_name': 'STARLUX Airlines',
        'flight_no': 'JX0850',
        'depart_time': '10:05',
        'arrive_time': '15:10',
    },
    ('CTS', 'TPE'): {
        'airline_code': 'JX',
        'airline_name': 'STARLUX Airlines',
        'flight_no': 'JX0851',
        'depart_time': '16:25',
        'arrive_time': '19:35',
    },
}

KNOWN_FALLBACK_CABINS = {'premium_economy', 'business'}
RELAXED_STOPS_DIAG_LIMIT = 3


def utc_now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

# ─────────── 即時匯率 ───────────

FX_APIS = [
    ('https://open.er-api.com/v6/latest/USD', lambda d: d.get('rates', {}).get('TWD')),
    ('https://api.exchangerate.host/latest?base=USD&symbols=TWD', lambda d: d.get('rates', {}).get('TWD')),
]

def get_fx_rate(default=FX_DEFAULT):
    """取 USD→TWD 匯率。多 API fallback，失敗讀 cache，再失敗用 default"""
    for url, pick in FX_APIS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'flight-tracker/1.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode('utf-8'))
            rate = pick(data)
            if rate and 20 < float(rate) < 50:  # sanity check
                rate = float(rate)
                log.info(f"匯率：1 USD = {rate} TWD（來源 {url.split('/')[2]}）")
                FX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(FX_CACHE_PATH, 'w') as f:
                    json.dump({'rate': rate, 'ts': utc_now_iso(), 'source': url}, f)
                return rate
        except Exception as e:
            log.warning(f"匯率 API 失敗 {url}：{e}")

    if FX_CACHE_PATH.exists():
        try:
            with open(FX_CACHE_PATH, 'r') as f:
                d = json.load(f)
            log.info(f"匯率使用 cache：1 USD = {d['rate']} TWD（{d['ts']}）")
            return float(d['rate'])
        except Exception as e:
            log.warning(f"匯率 cache 讀取失敗：{e}")

    log.warning(f"匯率全部失敗，使用 default {default}")
    return default

# ─────────── scrape_state.json ───────────

def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"scrape_state.json 損壞：{e}，重建")
    return {'routes': {}}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def update_state(state, route_id, written, diagnostics=None):
    info = state.setdefault('routes', {}).setdefault(str(route_id), {})
    info['last_scan_ts'] = utc_now_iso()
    info['last_written'] = written
    if diagnostics:
        info['last_diagnostics'] = diagnostics
        info['last_scrape'] = diagnostics
        source_issue = diagnostics.get('source_issue')
        if source_issue:
            info['source_issue'] = source_issue
        else:
            info.pop('source_issue', None)
    if written > 0:
        info['last_success_ts'] = info['last_scan_ts']
        info['consecutive_failures'] = 0
    else:
        info['consecutive_failures'] = info.get('consecutive_failures', 0) + 1

# ─────────── 讀檔 ───────────

def load_routes():
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def load_lcc_config():
    with open(LCC_YAML, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    codes = set()
    keywords = []
    if 'iata_codes' in data or 'name_keywords' in data:
        codes = set(data.get('iata_codes') or [])
        keywords = [k.lower() for k in (data.get('name_keywords') or [])]
    else:
        for v in data.values():
            if isinstance(v, list):
                codes.update(v)
    return codes, keywords

def is_lcc_flight(airline_name, airline_code, lcc_codes, lcc_keywords):
    if airline_code and airline_code in lcc_codes:
        return True
    if airline_name:
        name_lower = airline_name.lower()
        if any(kw in name_lower for kw in lcc_keywords):
            return True
    return False

def infer_airline_code(airline_name):
    name = (airline_name or '').strip().lower()
    if 'starlux' in name or '星宇' in name:
        return 'JX'
    if 'china airlines' in name or '中華航空' in name or '華航' in name:
        return 'CI'
    if 'eva air' in name or '長榮' in name:
        return 'BR'
    return (airline_name or '')[:2].upper()

# ─────────── DB ───────────

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL,
            scan_ts TEXT NOT NULL,
            depart_date TEXT NOT NULL,
            return_date TEXT NOT NULL,
            days_before_depart INTEGER NOT NULL,
            airline_code TEXT,
            airline_name TEXT,
            flight_no TEXT,
            cabin TEXT,
            is_lcc INTEGER,
            price_twd INTEGER NOT NULL,
            depart_time TEXT,
            arrive_time TEXT,
            stops INTEGER,
            origin TEXT,
            destination TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_route_depart ON prices(route_id, depart_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scan_ts ON prices(scan_ts)")
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(prices)").fetchall()}
    for col_name in ('return_depart_time', 'return_arrive_time'):
        if col_name not in existing_cols:
            c.execute(f"ALTER TABLE prices ADD COLUMN {col_name} TEXT")
    conn.commit()
    return conn

def cleanup_invalid_rows(conn):
    cur = conn.execute("DELETE FROM prices WHERE airline_name IS NULL OR TRIM(airline_name) = ''")
    log.info(f"清除空白 airline 舊資料：{cur.rowcount} 筆")
    conn.commit()

def cleanup_old_data(conn, days=OLD_DATA_DAYS):
    cur = conn.execute(
        "DELETE FROM prices WHERE DATE(scan_ts, '+8 hours') < DATE('now', '+8 hours', ?)",
        (f'-{days} day',)
    )
    log.info(f"清理 >{days} 天舊資料：{cur.rowcount} 筆")
    conn.commit()

def reclassify_is_lcc(conn, lcc_codes, lcc_keywords):
    rows = conn.execute('SELECT id, airline_name, airline_code, is_lcc FROM prices').fetchall()
    changed = 0
    for id_, name, code, old in rows:
        if name == UNCLASSIFIED_AIRLINE_NAME:
            if old is not None:
                conn.execute('UPDATE prices SET is_lcc = NULL WHERE id = ?', (id_,))
                changed += 1
            continue
        new_val = int(is_lcc_flight(name or '', code or '', lcc_codes, lcc_keywords))
        if new_val != old:
            conn.execute('UPDATE prices SET is_lcc = ? WHERE id = ?', (new_val, id_))
            changed += 1
    conn.commit()
    log.info(f"重新分類 is_lcc：{changed} / {len(rows)} 筆有變更")

# ─────────── 日期列舉 ───────────

WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
WEEKDAY_MAP = {n: i for i, n in enumerate(WEEKDAY_NAMES)}

def count_full_weekends(start_d, end_d):
    count = 0
    cur = start_d
    while cur <= end_d:
        if cur.weekday() == 5:
            sun = cur + timedelta(days=1)
            if sun <= end_d:
                count += 1
        cur += timedelta(days=1)
    return count

def enumerate_date_pairs(route):
    rng = route['depart_date_range']
    start = date.fromisoformat(rng['start'])
    end = date.fromisoformat(rng['end'])
    duration = route['trip_duration_days']
    must_weekends = route.get('must_contain_full_weekends', 0) or 0
    depart_weekdays = route.get('depart_weekdays')

    allowed_weekdays = None
    if depart_weekdays:
        allowed_weekdays = {WEEKDAY_MAP[w] for w in depart_weekdays}

    pairs = []
    cur = start
    while cur <= end:
        ret = cur + timedelta(days=duration - 1)
        if allowed_weekdays and cur.weekday() not in allowed_weekdays:
            cur += timedelta(days=1)
            continue
        if must_weekends > 0:
            if count_full_weekends(cur, ret) < must_weekends:
                cur += timedelta(days=1)
                continue
        pairs.append((cur, ret))
        cur += timedelta(days=1)

    return pairs

# ─────────── 時段過濾 ───────────

def parse_time_window(s):
    if not s:
        return None

    def parse_bound(value):
        value = value.strip()
        if value == '24:00':
            return time(23, 59, 59)
        return time.fromisoformat(value)

    a, b = s.split('-')
    return (parse_bound(a), parse_bound(b))

def parse_flight_time(s):
    s = s.strip()
    try:
        return time.fromisoformat(s[:5])
    except ValueError:
        pass
    try:
        return datetime.strptime(s, '%I:%M %p').time()
    except ValueError:
        pass
    try:
        clean = s.split('+')[0].strip()
        return datetime.strptime(clean, '%I:%M %p').time()
    except ValueError:
        pass
    raise ValueError(f"無法解析時間：{s}")

def in_window(t_str, window):
    if window is None:
        return True
    try:
        t = parse_flight_time(t_str)
    except Exception:
        return True
    return window[0] <= t <= window[1]

def first_attr(obj, names):
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return value
    return ''

def parse_price_twd(price_raw, fx_rate):
    price_raw = str(price_raw or '').strip()
    if not price_raw:
        return None
    digits = ''.join(c for c in price_raw if c.isdigit() or c == '.')
    if not digits:
        return None
    price_num = float(digits)
    if 'NT' in price_raw or 'TWD' in price_raw:
        return int(price_num)
    return int(price_num * fx_rate)

def parse_stops(stops_raw):
    if isinstance(stops_raw, int):
        return stops_raw
    s = str(stops_raw or '').lower()
    if not s or s in {'unknown', 'none', 'null', '-'}:
        return None
    if 'nonstop' in s or 'direct' in s:
        return 0
    digits = ''.join(c for c in s if c.isdigit())
    return int(digits) if digits else None

def bump_skip(stats, reason):
    stats['skipped'][reason] += 1

def serialize_stats(stats):
    out = dict(stats)
    out['skipped'] = dict(stats.get('skipped', {}))
    skipped = out.get('skipped', {})
    if out.get('unclassified_written'):
        out['source_issue'] = 'unclassified_airline'
    elif out.get('written', 0) == 0 and out.get('raw_results', 0) > 0:
        if skipped:
            out['source_issue'] = 'filtered_all_results'
    return out

def finalize_diagnostics(diagnostics):
    skipped = diagnostics.get('skipped') or {}
    relaxed = diagnostics.get('relaxed_max_stops') or {}
    if diagnostics.get('written', 0) == 0 and relaxed.get('raw_flights', 0) > 0 and relaxed.get('direct_flights', 0) == 0:
        diagnostics['source_issue'] = 'no_direct_cabin_results'
    elif diagnostics.get('unclassified_written'):
        diagnostics['source_issue'] = 'unclassified_airline'
    elif diagnostics.get('written', 0) == 0 and diagnostics.get('raw_flights', 0) == 0:
        if relaxed.get('raw_flights', 0) > 0 and relaxed.get('direct_flights', 0) == 0:
            diagnostics['source_issue'] = 'no_direct_cabin_results'
        elif relaxed and relaxed.get('raw_flights', 0) == 0:
            diagnostics['source_issue'] = 'no_cabin_results'
        elif diagnostics.get('query_errors', 0) > 0:
            diagnostics['source_issue'] = 'query_errors'
        else:
            diagnostics['source_issue'] = 'no_raw_results'
    elif diagnostics.get('written', 0) == 0 and diagnostics.get('raw_flights', 0) > 0 and any(skipped.values()):
        diagnostics['source_issue'] = 'filtered_all_results'
    else:
        diagnostics.pop('source_issue', None)
    return diagnostics

def add_relaxed_max_stops_diagnostics(route, date_pairs, diagnostics):
    if route.get('max_stops') != 0:
        return
    if diagnostics.get('written', 0) > 0:
        return

    relaxed = {
        'tested_date_pairs': 0,
        'query_count': 0,
        'raw_flights': 0,
        'direct_flights': 0,
        'sample_flights': [],
        'no_result_examples': [],
        'error_examples': [],
    }
    sampled_pairs = date_pairs[:RELAXED_STOPS_DIAG_LIMIT]
    for depart_d, return_d in sampled_pairs:
        relaxed['tested_date_pairs'] += 1
        for dest in route['destinations']:
            for cabin in route['cabin_classes']:
                local_diag = {
                    'query_count': 0,
                    'raw_flights': 0,
                    'query_errors': 0,
                    'no_result_examples': [],
                    'error_examples': [],
                }
                flights = query_one(route['origin'], dest, depart_d, return_d, cabin, local_diag, max_stops=None)
                relaxed['query_count'] += local_diag['query_count']
                relaxed['raw_flights'] += local_diag['raw_flights']
                relaxed['no_result_examples'].extend(local_diag['no_result_examples'])
                relaxed['error_examples'].extend(local_diag['error_examples'])
                for f in flights:
                    stops = parse_stops(getattr(f, 'stops', 0))
                    if stops == 0:
                        relaxed['direct_flights'] += 1
                    if len(relaxed['sample_flights']) < 3:
                        relaxed['sample_flights'].append({
                            'depart_date': depart_d.isoformat(),
                            'return_date': return_d.isoformat(),
                            'destination': dest,
                            'cabin': cabin,
                            'airline_name': (getattr(f, 'name', '') or '').strip(),
                            'price': getattr(f, 'price', '') or '',
                            'depart_time': getattr(f, 'departure', '') or '',
                            'arrive_time': getattr(f, 'arrival', '') or '',
                            'stops': stops,
                        })
    diagnostics['relaxed_max_stops'] = relaxed

def same_time(observed, expected):
    if not observed:
        return False
    try:
        return parse_flight_time(observed) == parse_flight_time(expected)
    except Exception:
        return False

def infer_known_blank_airline(route, dest, cabin, stops, depart_time, arrive_time):
    """Fill airline fields only when the returned times match a verified flight."""
    origin = route.get('origin')
    if cabin not in KNOWN_FALLBACK_CABINS:
        return None
    if stops not in (0, None):
        return None
    outbound = KNOWN_DIRECT_FLIGHT_FALLBACKS.get((origin, dest))
    inbound = KNOWN_DIRECT_FLIGHT_FALLBACKS.get((dest, origin))
    if not outbound or not inbound:
        return None
    if not same_time(depart_time, outbound['depart_time']):
        return None
    if arrive_time and not same_time(arrive_time, outbound['arrive_time']):
        return None
    return {
        'airline_code': outbound['airline_code'],
        'airline_name': outbound['airline_name'],
        'flight_no': outbound['flight_no'],
        'depart_time': outbound['depart_time'],
        'arrive_time': outbound['arrive_time'],
        'return_depart_time': inbound['depart_time'],
        'return_arrive_time': inbound['arrive_time'],
    }

# ─────────── fast-flights ───────────

CABIN_MAP = {
    'economy': 'economy',
    'premium_economy': 'premium-economy',
    'business': 'business',
    'first': 'first',
}

def query_one(origin, dest, depart_date, return_date, cabin, diagnostics=None, max_stops=None):
    seat = CABIN_MAP.get(cabin, 'economy')
    if diagnostics is not None:
        diagnostics['query_count'] += 1
    try:
        result = get_flights(
            flight_data=[
                FlightData(date=depart_date.isoformat(), from_airport=origin, to_airport=dest),
                FlightData(date=return_date.isoformat(), from_airport=dest, to_airport=origin),
            ],
            trip='round-trip',
            seat=seat,
            passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
            fetch_mode='common',
            max_stops=max_stops,
        )
        flights = result.flights or []
        if diagnostics is not None:
            diagnostics['raw_flights'] += len(flights)
            if not flights and len(diagnostics['no_result_examples']) < 3:
                diagnostics['no_result_examples'].append({
                    'origin': origin,
                    'destination': dest,
                    'depart_date': depart_date.isoformat(),
                    'return_date': return_date.isoformat(),
                    'cabin': cabin,
                })
        return flights
    except Exception as e:
        error_text = ' '.join(str(e).split())
        short_error = error_text[:500]
        log.warning(f"查詢失敗 {origin}-{dest} {depart_date}~{return_date} {cabin}: {short_error}")
        if diagnostics is not None:
            example = {
                'origin': origin,
                'destination': dest,
                'depart_date': depart_date.isoformat(),
                'return_date': return_date.isoformat(),
                'cabin': cabin,
            }
            if 'No flights found' in error_text:
                if len(diagnostics['no_result_examples']) < 3:
                    diagnostics['no_result_examples'].append(example)
            else:
                diagnostics['query_errors'] += 1
                if len(diagnostics['error_examples']) < 3:
                    diagnostics['error_examples'].append({
                        **example,
                        'error': short_error,
                    })
        return []

# ─────────── 主流程 ───────────

def scrape_route(route, lcc_codes, lcc_keywords, conn, fx_rate):
    if not route.get('active', True):
        log.info(f"路線 #{route['id']} 已暫停，跳過")
        return 0, {'active': False}

    log.info(f"開始路線 #{route['id']}:{route['name']}")
    date_pairs = enumerate_date_pairs(route)
    scan_ts = utc_now_iso()
    diagnostics = {
        'scan_ts': scan_ts,
        'active': True,
        'date_pairs': len(date_pairs),
        'eligible_date_pairs': len(date_pairs),
        'query_count': 0,
        'queries': 0,
        'raw_flights': 0,
        'raw_results': 0,
        'written': 0,
        'fallback_written': 0,
        'unclassified_written': 0,
        'query_errors': 0,
        'destinations': route.get('destinations') or [],
        'cabins': route.get('cabin_classes') or [],
        'max_stops': route.get('max_stops', 99),
        'max_price_twd': route.get('max_price_twd') or 0,
        'depart_time_window': route.get('depart_time_window'),
        'skipped': {
            'no_price': 0,
            'bad_price': 0,
            'blank_airline': 0,
            'unknown_stops': 0,
            'too_many_stops': 0,
            'over_max_price': 0,
            'outside_depart_time_window': 0,
        },
        'no_result_examples': [],
        'sample_no_results': [],
        'error_examples': [],
    }
    log.info(f"  合格日期組合：{len(date_pairs)} 個")
    if not date_pairs:
        log.warning("  沒有合格日期，跳過")
        return 0, finalize_diagnostics(diagnostics)

    depart_win = parse_time_window(route.get('depart_time_window'))
    max_price = route.get('max_price_twd') or 0
    max_stops = route.get('max_stops', 99)
    if max_stops is None:
        max_stops = 99

    written = 0

    for depart_d, return_d in date_pairs:
        for dest in route['destinations']:
            for cabin in route['cabin_classes']:
                query_max_stops = max_stops if max_stops != 99 else None
                flights = query_one(
                    route['origin'], dest, depart_d, return_d, cabin,
                    diagnostics, query_max_stops
                )
                diagnostics['queries'] = diagnostics['query_count']
                diagnostics['raw_results'] = diagnostics['raw_flights']
                diagnostics['sample_no_results'] = diagnostics['no_result_examples']

                for f in flights:
                    try:
                        price = parse_price_twd(getattr(f, 'price', ''), fx_rate)
                    except Exception:
                        diagnostics['skipped']['bad_price'] += 1
                        continue
                    if price is None:
                        diagnostics['skipped']['no_price'] += 1
                        continue

                    airline = (getattr(f, 'name', '') or '').strip()
                    depart_time_str = getattr(f, 'departure', '') or ''
                    arrive_time_str = getattr(f, 'arrival', '') or ''
                    return_depart_time_str = first_attr(f, (
                        'return_departure', 'return_depart_time', 'return_departure_time',
                        'returning_departure', 'inbound_departure', 'inbound_depart_time',
                    ))
                    return_arrive_time_str = first_attr(f, (
                        'return_arrival', 'return_arrive_time', 'return_arrival_time',
                        'returning_arrival', 'inbound_arrival', 'inbound_arrive_time',
                    ))

                    stops = parse_stops(getattr(f, 'stops', 0))

                    if stops is None and max_stops != 99:
                        diagnostics['skipped']['unknown_stops'] += 1
                        continue
                    if stops is not None and stops > max_stops:
                        diagnostics['skipped']['too_many_stops'] += 1
                        continue
                    if max_price and price > max_price:
                        diagnostics['skipped']['over_max_price'] += 1
                        continue
                    if not in_window(depart_time_str, depart_win):
                        diagnostics['skipped']['outside_depart_time_window'] += 1
                        continue

                    flight_no = ''
                    is_lcc = None
                    if not airline:
                        fallback = infer_known_blank_airline(route, dest, cabin, stops, depart_time_str, arrive_time_str)
                        if fallback:
                            airline = fallback['airline_name']
                            airline_code = fallback['airline_code']
                            flight_no = fallback['flight_no']
                            depart_time_str = depart_time_str or fallback['depart_time']
                            arrive_time_str = arrive_time_str or fallback['arrive_time']
                            return_depart_time_str = return_depart_time_str or fallback['return_depart_time']
                            return_arrive_time_str = return_arrive_time_str or fallback['return_arrive_time']
                            is_lcc = is_lcc_flight(airline, airline_code, lcc_codes, lcc_keywords)
                            diagnostics['fallback_written'] += 1
                            log.info(
                                f"  使用已驗證航班補名 fallback：#{route['id']} {depart_d}~{return_d} "
                                f"{cabin} {airline} {price} TWD"
                            )
                        else:
                            airline = UNCLASSIFIED_AIRLINE_NAME
                            airline_code = ''
                            flight_no = 'unclassified'
                            diagnostics['unclassified_written'] += 1
                            diagnostics['skipped']['blank_airline'] += 1
                            log.info(
                                f"  寫入未分類票價：#{route['id']} {depart_d}~{return_d} "
                                f"{cabin} {price} TWD"
                            )
                    else:
                        airline_code = infer_airline_code(airline)
                        is_lcc = is_lcc_flight(airline, airline_code, lcc_codes, lcc_keywords)

                    days_before = (depart_d - datetime.now(TAIPEI).date()).days

                    conn.execute("""
                        INSERT INTO prices (
                            route_id, scan_ts, depart_date, return_date, days_before_depart,
                            airline_code, airline_name, flight_no, cabin, is_lcc, price_twd,
                            depart_time, arrive_time, stops, origin, destination,
                            return_depart_time, return_arrive_time
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        route['id'], scan_ts, depart_d.isoformat(), return_d.isoformat(), days_before,
                        airline_code, airline, flight_no, cabin, int(is_lcc) if is_lcc is not None else None, price,
                        depart_time_str, arrive_time_str, stops, route['origin'], dest,
                        return_depart_time_str, return_arrive_time_str
                    ))
                    written += 1
                    diagnostics['written'] += 1

    add_relaxed_max_stops_diagnostics(route, date_pairs, diagnostics)

    conn.commit()
    log.info(f"  寫入 {written} 筆")
    return written, finalize_diagnostics(diagnostics)

def main():
    log.info("=" * 60)
    log.info("機票爬蟲開始")
    log.info("=" * 60)

    routes_data = load_routes()
    lcc_codes, lcc_keywords = load_lcc_config()
    conn = init_db()

    # 維護
    cleanup_invalid_rows(conn)
    cleanup_old_data(conn)
    reclassify_is_lcc(conn, lcc_codes, lcc_keywords)

    # 即時匯率
    fx_rate = get_fx_rate()

    # 連續失敗追蹤
    state = load_state()

    total = 0
    for route in routes_data['routes']:
        if not route.get('active', True):
            log.info(f"路線 #{route['id']} 已暫停，跳過")
            continue
        written, diagnostics = scrape_route(route, lcc_codes, lcc_keywords, conn, fx_rate)
        update_state(state, route['id'], written, diagnostics)
        total += written

    save_state(state)
    conn.close()
    log.info(f"全部完成，共寫入 {total} 筆")

if __name__ == '__main__':
    main()

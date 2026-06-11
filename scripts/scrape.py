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
from datetime import date, datetime, time, timedelta
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
                    json.dump({'rate': rate, 'ts': datetime.utcnow().isoformat(), 'source': url}, f)
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

def update_state(state, route_id, written):
    info = state.setdefault('routes', {}).setdefault(str(route_id), {})
    info['last_scan_ts'] = datetime.utcnow().isoformat()
    info['last_written'] = written
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

# ─────────── fast-flights ───────────

CABIN_MAP = {
    'economy': 'economy',
    'premium_economy': 'premium-economy',
    'business': 'business',
    'first': 'first',
}

def query_one(origin, dest, depart_date, return_date, cabin):
    seat = CABIN_MAP.get(cabin, 'economy')
    try:
        result = get_flights(
            flight_data=[
                FlightData(date=depart_date.isoformat(), from_airport=origin, to_airport=dest),
                FlightData(date=return_date.isoformat(), from_airport=dest, to_airport=origin),
            ],
            trip='round-trip',
            seat=seat,
            passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
            fetch_mode='fallback',
        )
        return result.flights
    except Exception as e:
        log.warning(f"查詢失敗 {origin}-{dest} {depart_date}~{return_date} {cabin}: {e}")
        return []

# ─────────── 主流程 ───────────

def scrape_route(route, lcc_codes, lcc_keywords, conn, fx_rate):
    if not route.get('active', True):
        log.info(f"路線 #{route['id']} 已暫停，跳過")
        return 0

    log.info(f"開始路線 #{route['id']}:{route['name']}")
    date_pairs = enumerate_date_pairs(route)
    log.info(f"  合格日期組合：{len(date_pairs)} 個")
    if not date_pairs:
        log.warning("  沒有合格日期，跳過")
        return 0

    depart_win = parse_time_window(route.get('depart_time_window'))
    max_price = route.get('max_price_twd') or 0
    max_stops = route.get('max_stops', 99)
    if max_stops is None:
        max_stops = 99

    scan_ts = datetime.utcnow().isoformat()
    written = 0

    for depart_d, return_d in date_pairs:
        for dest in route['destinations']:
            for cabin in route['cabin_classes']:
                flights = query_one(route['origin'], dest, depart_d, return_d, cabin)
                for f in flights:
                    try:
                        price_raw = str(getattr(f, 'price', '') or '').strip()
                        if not price_raw:
                            continue
                        digits = ''.join(c for c in price_raw if c.isdigit() or c == '.')
                        if not digits:
                            continue
                        price_num = float(digits)
                        if 'NT' in price_raw or 'TWD' in price_raw:
                            price = int(price_num)
                        else:
                            price = int(price_num * fx_rate)
                    except Exception:
                        continue

                    airline = (getattr(f, 'name', '') or '').strip()
                    if not airline:
                        continue
                    airline_code = airline[:2].upper() if airline else ''
                    is_lcc = is_lcc_flight(airline, airline_code, lcc_codes, lcc_keywords)

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

                    stops_raw = getattr(f, 'stops', 0)
                    if isinstance(stops_raw, int):
                        stops = stops_raw
                    else:
                        s = str(stops_raw or '').lower()
                        if 'nonstop' in s or 'direct' in s:
                            stops = 0
                        else:
                            digits = ''.join(c for c in s if c.isdigit())
                            stops = int(digits) if digits else 0

                    if stops > max_stops:
                        continue
                    if max_price and price > max_price:
                        continue
                    if not in_window(depart_time_str, depart_win):
                        continue

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
                        airline_code, airline, '', cabin, int(is_lcc), price,
                        depart_time_str, arrive_time_str, stops, route['origin'], dest,
                        return_depart_time_str, return_arrive_time_str
                    ))
                    written += 1

    conn.commit()
    log.info(f"  寫入 {written} 筆")
    return written

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
        written = scrape_route(route, lcc_codes, lcc_keywords, conn, fx_rate)
        update_state(state, route['id'], written)
        total += written

    save_state(state)
    conn.close()
    log.info(f"全部完成，共寫入 {total} 筆")

if __name__ == '__main__':
    main()

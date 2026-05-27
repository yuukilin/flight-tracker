"""
機票爬蟲主程式
1. 讀 routes.yaml（或 routes.json，優先用 routes.json）
2. 依規則列出所有合格日期組合
3. 用 fast-flights 抓 Google Flights
4. 過濾廉航、套用時段/票價/轉機限制
5. 寫入 SQLite
"""

import os
import sys
import json
import sqlite3
import yaml
from datetime import date, datetime, time, timedelta
from pathlib import Path
import logging

from fast_flights import FlightData, Passengers, get_flights

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
ROUTES_YAML = ROOT / 'routes.yaml'
ROUTES_JSON = ROOT / 'routes.json'
LCC_YAML = ROOT / 'excluded_airlines.yaml'
DB_PATH = ROOT / 'data' / 'prices.db'

# ─────────── 讀檔 ───────────

def load_routes():
    """優先讀 routes.json（給 Telegram bot 修改用），沒有才讀 yaml"""
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def load_lcc_codes():
    with open(LCC_YAML, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    codes = set()
    for region_airlines in data.values():
        codes.update(region_airlines)
    return codes

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
    conn.commit()
    return conn

# ─────────── 日期列舉 ───────────

WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
WEEKDAY_MAP = {n: i for i, n in enumerate(WEEKDAY_NAMES)}

def count_full_weekends(start_d, end_d):
    """計算區間內完整的(週六+週日)對數"""
    count = 0
    cur = start_d
    while cur <= end_d:
        if cur.weekday() == 5:  # Saturday
            sun = cur + timedelta(days=1)
            if sun <= end_d:
                count += 1
        cur += timedelta(days=1)
    return count

def enumerate_date_pairs(route):
    """根據規則列出所有合格的 (出發日, 回程日) 組合"""
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
    a, b = s.split('-')
    return (time.fromisoformat(a.strip()), time.fromisoformat(b.strip()))

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

# ─────────── fast-flights 包裝 ───────────

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

def scrape_route(route, lcc_codes, conn):
    if not route.get('active', True):
        log.info(f"路線 #{route['id']} 已暫停，跳過")
        return 0

    log.info(f"開始路線 #{route['id']}：{route['name']}")

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
                        # 嘗試解析價格
                        digits = ''.join(c for c in price_raw if c.isdigit() or c == '.')
                        if not digits:
                            continue
                        price_num = float(digits)
                        # fast-flights 預設回 USD，沒指定貨幣的話假設 USD → TWD
                        if 'NT' in price_raw or 'TWD' in price_raw:
                            price = int(price_num)
                        else:
                            price = int(price_num * 32)  # TODO: 接即時匯率
                    except Exception:
                        continue

                    airline = getattr(f, 'name', '') or ''
                    airline_code = airline[:2].upper() if airline else ''
                    is_lcc = airline_code in lcc_codes

                    depart_time_str = getattr(f, 'departure', '') or ''
                    arrive_time_str = getattr(f, 'arrival', '') or ''
                    stops = getattr(f, 'stops', 0) or 0

                    if stops > max_stops:
                        continue
                    if max_price and price > max_price:
                        continue
                    if not in_window(depart_time_str, depart_win):
                        continue

                    days_before = (depart_d - date.today()).days

                    conn.execute("""
                        INSERT INTO prices (
                            route_id, scan_ts, depart_date, return_date, days_before_depart,
                            airline_code, airline_name, flight_no, cabin, is_lcc, price_twd,
                            depart_time, arrive_time, stops, origin, destination
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        route['id'], scan_ts, depart_d.isoformat(), return_d.isoformat(), days_before,
                        airline_code, airline, '', cabin, int(is_lcc), price,
                        depart_time_str, arrive_time_str, stops, route['origin'], dest
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
    lcc_codes = load_lcc_codes()
    conn = init_db()

    total = 0
    for route in routes_data['routes']:
        total += scrape_route(route, lcc_codes, conn)

    conn.close()
    log.info(f"全部完成，共寫入 {total} 筆")

if __name__ == '__main__':
    main()

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

def load_lcc_config():
    """讀廉航設定。回傳 (iata_codes set, name_keywords list)"""
    with open(LCC_YAML, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    codes = set()
    keywords = []
    # 新格式：頂層有 iata_codes 和 name_keywords
    if 'iata_codes' in data or 'name_keywords' in data:
        codes = set(data.get('iata_codes') or [])
        keywords = [k.lower() for k in (data.get('name_keywords') or [])]
    else:
        # 舊格式：每個 region key 都是 list
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

def scrape_route(route, lcc_codes, lcc_keywords, conn):
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

                    airline = (getattr(f, 'name', '') or '').strip()
                    # 跳過 fast-flights 沒解析出航空公司名稱的航班（資料不完整，不可信）
                    if not airline:
                        continue
                    airline_code = airline[:2].upper() if airline else ''
                    is_lcc = is_lcc_flight(airline, airline_code, lcc_codes, lcc_keywords)

                    depart_time_str = getattr(f, 'departure', '') or ''
                    arrive_time_str = getattr(f, 'arrival', '') or ''

                    # fast-flights 的 stops 可能是 int、"Nonstop"、"1 stop"、"2 stops" 等
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

def reclassify_is_lcc(conn, lcc_codes, lcc_keywords):
    """重跑所有歷史資料的 is_lcc 分類，讓舊資料能跟著新 LCC 名單修正"""
    rows = conn.execute('SELECT id, airline_name, airline_code, is_lcc FROM prices').fetchall()
    changed = 0
    for id_, name, code, old in rows:
        new_val = int(is_lcc_flight(name or '', code or '', lcc_codes, lcc_keywords))
        if new_val != old:
            conn.execute('UPDATE prices SET is_lcc = ? WHERE id = ?', (new_val, id_))
            changed += 1
    conn.commit()
    log.info(f"重新分類 is_lcc：{changed} / {len(rows)} 筆有變更")

def cleanup_invalid_rows(conn):
    """清掉 airline_name 空白的舊資料（資料不完整、不可信）"""
    cur = conn.execute("DELETE FROM prices WHERE airline_name IS NULL OR TRIM(airline_name) = ''")
    log.info(f"清除空白 airline 舊資料：{cur.rowcount} 筆")
    conn.commit()

def main():
    log.info("=" * 60)
    log.info("機票爬蟲開始")
    log.info("=" * 60)

    routes_data = load_routes()
    lcc_codes, lcc_keywords = load_lcc_config()
    conn = init_db()

    # 維護：清掉空白 airline 的舊資料，並重跑 is_lcc 分類
    cleanup_invalid_rows(conn)
    reclassify_is_lcc(conn, lcc_codes, lcc_keywords)

    total = 0
    for route in routes_data['routes']:
        total += scrape_route(route, lcc_codes, lcc_keywords, conn)

    conn.close()
    log.info(f"全部完成，共寫入 {total} 筆")

if __name__ == '__main__':
    main()

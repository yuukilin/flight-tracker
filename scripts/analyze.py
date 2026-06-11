"""
歷史比較與分級
- 對每條路線：找今日最低價，比過去 30/90/365 天的百分位
- 輸出 JSON 給 notify.py 用
"""

import sqlite3
import yaml
import json
from datetime import datetime, timedelta
from pathlib import Path
import logging
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / 'data' / 'prices.db'
ROUTES_YAML = ROOT / 'routes.yaml'
ROUTES_JSON = ROOT / 'routes.json'
OUTPUT_JSON = ROOT / 'data' / 'analysis.json'
TAIPEI = ZoneInfo('Asia/Taipei')
SCAN_DATE_SQL = "DATE(scan_ts, '+8 hours')"

def load_routes():
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)['routes']
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)['routes']

def percentile(sorted_values, p):
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)

def history_stats(history):
    stats = {'count': len(history)}
    if not history:
        return stats
    sorted_history = sorted(history)
    stats.update({
        'min': sorted_history[0],
        'p25': percentile(sorted_history, 25),
        'p50': percentile(sorted_history, 50),
        'p75': percentile(sorted_history, 75),
        'max': sorted_history[-1],
    })
    return stats

def analyze_route(conn, route):
    today = datetime.now(TAIPEI).date()
    today_str = today.isoformat()

    cur = conn.execute(f"""
        SELECT MIN(price_twd) FROM prices
        WHERE route_id = ?
          AND {SCAN_DATE_SQL} = ?
          AND is_lcc = 0
    """, (route['id'], today_str))
    today_min = cur.fetchone()[0]

    if today_min is None:
        return {
            'route_id': route['id'],
            'route_name': route['name'],
            'today_min': None,
            'message': '今日無傳統航空資料',
        }

    def get_history(days):
        d_from = (today - timedelta(days=days)).isoformat()
        d_to = (today - timedelta(days=1)).isoformat()
        cur = conn.execute(f"""
            SELECT {SCAN_DATE_SQL} as d, MIN(price_twd)
            FROM prices
            WHERE route_id = ?
              AND {SCAN_DATE_SQL} BETWEEN ? AND ?
              AND is_lcc = 0
            GROUP BY d
            ORDER BY MIN(price_twd)
        """, (route['id'], d_from, d_to))
        return [row[1] for row in cur.fetchall()]

    history_30 = get_history(30)
    history_90 = get_history(90)
    history_365 = get_history(365)
    history_stats_30 = history_stats(history_30)
    history_stats_90 = history_stats(history_90)
    history_stats_365 = history_stats(history_365)

    def classify(today_min, history):
        if len(history) < 7:
            return ['insufficient_data', f'歷史資料只有 {len(history)} 天，需 ≥ 7 天']
        p25 = percentile(history, 25)
        p50 = percentile(history, 50)
        p75 = percentile(history, 75)
        if today_min <= p25:
            return ['cheap', f'低於 P25 ({p25:.0f})']
        if today_min <= p50:
            return ['good', f'低於 P50 ({p50:.0f})']
        if today_min <= p75:
            return ['normal', f'P50-P75 ({p50:.0f}-{p75:.0f})']
        return ['expensive', f'高於 P75 ({p75:.0f})']

    return {
        'route_id': route['id'],
        'route_name': route['name'],
        'today_min': today_min,
        'analysis_30': classify(today_min, history_30),
        'analysis_90': classify(today_min, history_90),
        'analysis_365': classify(today_min, history_365),
        'history_count_30': len(history_30),
        'history_count_90': len(history_90),
        'history_count_365': len(history_365),
        'history_stats_30': history_stats_30,
        'history_stats_90': history_stats_90,
        'history_stats_365': history_stats_365,
    }

def main():
    log.info("分析開始")
    conn = sqlite3.connect(DB_PATH)
    routes = load_routes()
    results = []
    for route in routes:
        if not route.get('active', True):
            continue
        results.append(analyze_route(conn, route))

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info(f"分析完成，寫入 {OUTPUT_JSON}")

if __name__ == '__main__':
    main()

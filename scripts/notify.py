"""
推 Telegram 通知
讀 analysis.json，根據 notify_threshold 決定推不推
"""

import os
import json
import yaml
import requests
import sqlite3
from datetime import date
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / 'data' / 'prices.db'
ROUTES_YAML = ROOT / 'routes.yaml'
ROUTES_JSON = ROOT / 'routes.json'
ANALYSIS_JSON = ROOT / 'data' / 'analysis.json'

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

STATUS_EMOJI = {
    'cheap': '💚 便宜',
    'good': '💛 還不錯',
    'normal': '🟡 普通',
    'expensive': '🔴 偏貴',
    'insufficient_data': '⚪ 資料不足',
    'no_data_today': '❓ 今日無資料',
}

def load_routes():
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return {r['id']: r for r in json.load(f)['routes']}
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return {r['id']: r for r in yaml.safe_load(f)['routes']}

def should_notify(threshold, status):
    if threshold == 'any':
        return True
    th = {'cheap': 0, 'good': 1, 'normal': 2}.get(threshold, 99)
    st = {'cheap': 0, 'good': 1, 'normal': 2}.get(status, 99)
    return st <= th

def get_top_flights(route_id, today_str, is_lcc, limit=3):
    """取當天該路線的便宜航班 Top N。is_lcc=0 拿傳統，is_lcc=1 拿廉航
    用 GROUP BY 去重複（同航空、同日期、同價格只算一次）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT airline_name, depart_date, return_date, MIN(price_twd) as p, MIN(depart_time), MIN(stops), destination
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) = ?
          AND is_lcc = ?
        GROUP BY airline_name, depart_date, return_date, destination
        ORDER BY p ASC
        LIMIT ?
    """, (route_id, today_str, is_lcc, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def format_message(analyses, routes):
    today = date.today()
    lines = [f"✈️ 機票雷達 {today.isoformat()}", ""]

    has_notify = False
    for a in analyses:
        route = routes.get(a['route_id'])
        if not route:
            continue
        if a.get('today_min') is None:
            # 沒有傳統航空資料，看是否有廉航可列
            lcc_top = get_top_flights(a['route_id'], today.isoformat(), is_lcc=1)
            if not lcc_top:
                continue
            has_notify = True
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append(f"📍 {a['route_name']}")
            lines.append("⚠️ 今日無傳統航空資料，僅有廉航：")
            for i, (name, dd, rd, price, dt, stops, dest) in enumerate(lcc_top, 1):
                stops_str = "直飛" if stops == 0 else f"轉 {stops} 次"
                lines.append(f"  {i}. {name or '?'}（{dd}→{rd}，{stops_str}）NT$ {price:,}  ⚠ 未含行李費")
            lines.append("")
            continue

        analysis = a.get('analysis_90') or a.get('analysis_30') or ['insufficient_data', '']
        status, reason = analysis

        threshold = route.get('notify_threshold', 'cheap')
        if not should_notify(threshold, status):
            continue

        has_notify = True
        emoji = STATUS_EMOJI.get(status, '⚪')
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(f"📍 {a['route_name']}")
        lines.append(f"💰 今日最低（傳統航空）：NT$ {a['today_min']:,}")
        lines.append(f"📊 {emoji}（{reason}）")
        lines.append(f"📈 樣本：30天 {a['history_count_30']} / 90天 {a['history_count_90']} / 365天 {a['history_count_365']}")

        # 傳統航空 Top 3
        top = get_top_flights(a['route_id'], today.isoformat(), is_lcc=0)
        if top:
            lines.append("")
            lines.append("🛫 傳統航空最便宜：")
            for i, (name, dd, rd, price, dt, stops, dest) in enumerate(top, 1):
                stops_str = "直飛" if stops == 0 else f"轉 {stops} 次"
                lines.append(f"  {i}. {name or '?'}（{dd}→{rd}，{stops_str}）NT$ {price:,}")

        # 廉航僅供參考
        lcc_top = get_top_flights(a['route_id'], today.isoformat(), is_lcc=1, limit=2)
        if lcc_top:
            lines.append("")
            lines.append("⚠️ 廉航（未含行李費，僅供參考）：")
            for i, (name, dd, rd, price, dt, stops, dest) in enumerate(lcc_top, 1):
                stops_str = "直飛" if stops == 0 else f"轉 {stops} 次"
                lines.append(f"  {i}. {name or '?'}（{dd}→{rd}，{stops_str}）NT$ {price:,}")

        lines.append("")

    if not has_notify:
        return None
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    resp = requests.post(url, json={'chat_id': CHAT_ID, 'text': text})
    resp.raise_for_status()
    log.info("Telegram 訊息已送出")

def main():
    if not ANALYSIS_JSON.exists():
        log.warning("找不到 analysis.json，跳過通知")
        return
    with open(ANALYSIS_JSON, 'r', encoding='utf-8') as f:
        analyses = json.load(f)
    routes = load_routes()

    msg = format_message(analyses, routes)
    if msg is None:
        log.info("無達到通知門檻的路線，不送訊息")
        # 還是送一條心跳訊息確認系統活著（首次測試用，正式上線可拿掉）
        if os.environ.get('SEND_HEARTBEAT') == '1':
            send_telegram(f"💓 機票雷達系統運轉中 {date.today().isoformat()}\n本次無達門檻的路線")
        return

    send_telegram(msg)

if __name__ == '__main__':
    main()

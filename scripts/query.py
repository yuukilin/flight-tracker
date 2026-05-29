"""
查詢工具：被 Worker 透過 query.yml workflow 觸發
action=history   過去 N 天每日最低
action=best      歷史最低 5 筆
action=chart     價格走勢 PNG（sendPhoto）

環境變數：
  TELEGRAM_BOT_TOKEN
"""

import argparse
import os
import sqlite3
import json
import yaml
from pathlib import Path
from datetime import date, timedelta
from urllib.parse import quote_plus

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / 'data' / 'prices.db'
ROUTES_JSON = ROOT / 'routes.json'
LCC_YAML = ROOT / 'excluded_airlines.yaml'

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

CABIN_QUERY_LABEL = {
    'economy': 'economy',
    'premium_economy': 'premium economy',
    'business': 'business class',
    'first': 'first class',
}


def load_route(rid):
    if not ROUTES_JSON.exists():
        return None
    try:
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            for r in json.load(f).get('routes', []):
                if r['id'] == rid:
                    return r
    except Exception:
        pass
    return None


def load_lcc_config():
    if not LCC_YAML.exists():
        return set(), []
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


def is_traditional_flight(airline_name, airline_code, is_lcc, lcc_codes, lcc_keywords):
    if is_lcc:
        return False
    return not is_lcc_flight(airline_name or '', airline_code or '', lcc_codes, lcc_keywords)


def google_flights_url(route, depart_date=None, return_date=None, destination=None):
    route = route or {}
    origin = route.get('origin', '')
    dest = destination or (route.get('destinations') or [''])[0]
    cabin = CABIN_QUERY_LABEL.get((route.get('cabin_classes') or ['economy'])[0], 'economy')
    if depart_date and return_date:
        query = f"Flights from {origin} to {dest} on {depart_date} returning {return_date} {cabin}"
    else:
        rng = route.get('depart_date_range') or {}
        query = f"Flights from {origin} to {dest} {rng.get('start', '')} {rng.get('end', '')} {cabin}"
    return "https://www.google.com/travel/flights?q=" + quote_plus(query)


def send_text(chat_id, text, buttons=None):
    if not TOKEN:
        print(f"[no token] would send: {text}")
        return
    import requests

    payload = {'chat_id': chat_id, 'text': text}
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': buttons}
    resp = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()


def send_photo(chat_id, png_path, caption=''):
    if not TOKEN:
        print(f"[no token] would send photo {png_path}: {caption}")
        return
    import requests

    with open(png_path, 'rb') as f:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={'chat_id': chat_id, 'caption': caption},
            files={'photo': f},
            timeout=60,
        )
        resp.raise_for_status()


def action_history(rid, days, chat_id):
    if not DB_PATH.exists():
        send_text(chat_id, f"#{rid} 查詢失敗：找不到 prices.db（cache 可能還沒建立）")
        return
    route = load_route(rid)
    name = route['name'] if route else f"#{rid}"
    conn = sqlite3.connect(DB_PATH)
    today = date.today()
    d_from = (today - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT DATE(scan_ts) AS d, price_twd, airline_name, airline_code, is_lcc
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) >= ?
          AND airline_name IS NOT NULL
          AND TRIM(airline_name) <> ''
    """, (rid, d_from)).fetchall()
    conn.close()

    lcc_codes, lcc_keywords = load_lcc_config()
    daily = {}
    counts = {}
    for d, price, airline_name, airline_code, is_lcc in rows:
        if not is_traditional_flight(airline_name, airline_code, is_lcc, lcc_codes, lcc_keywords):
            continue
        daily[d] = min(price, daily.get(d, price))
        counts[d] = counts.get(d, 0) + 1

    if not daily:
        send_text(chat_id, f"#{rid} {name}\n過去 {days} 天沒有傳統航空資料。")
        return

    lines = [f"#{rid} {name}", f"過去 {days} 天每日最低（傳統航空）"]
    for d in sorted(daily.keys(), reverse=True):
        lines.append(f"{d}：NT$ {daily[d]:,}（{counts[d]} 筆）")
    send_text(chat_id, '\n'.join(lines))


def action_best(rid, chat_id, limit=5):
    if not DB_PATH.exists():
        send_text(chat_id, f"#{rid} 查詢失敗：找不到 prices.db")
        return
    route = load_route(rid)
    name = route['name'] if route else f"#{rid}"
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT price_twd, airline_name, airline_code, depart_date, return_date, destination, stops, is_lcc
        FROM prices
        WHERE route_id = ?
          AND airline_name IS NOT NULL
          AND TRIM(airline_name) <> ''
        ORDER BY price_twd ASC
    """, (rid,)).fetchall()
    conn.close()

    lcc_codes, lcc_keywords = load_lcc_config()
    best_by_key = {}
    for price, airline_name, airline_code, dd, rd, dest, stops, is_lcc in rows:
        if not is_traditional_flight(airline_name, airline_code, is_lcc, lcc_codes, lcc_keywords):
            continue
        key = (airline_name, dd, rd, dest)
        old = best_by_key.get(key)
        if old is None or price < old[0]:
            best_by_key[key] = (price, airline_name, dd, rd, dest, stops)

    best_rows = sorted(best_by_key.values(), key=lambda row: row[0])[:limit]
    if not best_rows:
        send_text(chat_id, f"#{rid} {name}\n歷史最低：目前沒有傳統航空資料。")
        return

    lines = [f"#{rid} {name}", f"歷史最低 {limit} 筆（傳統航空）"]
    buttons = []
    for i, (p, an, dd, rd, dest, stops) in enumerate(best_rows, 1):
        s = "直飛" if stops == 0 else f"轉機 {stops} 次"
        lines.append(f"{i}. NT$ {p:,}｜{an}｜{dd} 去，{rd} 回｜{dest}｜{s}")
        buttons.append([{'text': f"第 {i} 筆開 Google Flights", 'url': google_flights_url(route, dd, rd, dest)}])
    lines.append("")
    lines.append("說明：Google Flights 開啟後會重新查價，實際票價與可訂位狀態以頁面顯示為準。")
    send_text(chat_id, '\n'.join(lines), buttons=buttons)


def action_chart(rid, days, chat_id):
    if not DB_PATH.exists():
        send_text(chat_id, f"#{rid} 圖表失敗：找不到 prices.db")
        return

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        send_text(chat_id, "圖表功能缺少 matplotlib，請確認 requirements.txt 已安裝。")
        return

    route = load_route(rid)
    name = route['name'] if route else f"#{rid}"
    conn = sqlite3.connect(DB_PATH)
    today = date.today()
    d_from = (today - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT DATE(scan_ts) AS d, price_twd, airline_name, airline_code, is_lcc
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) >= ?
          AND airline_name IS NOT NULL
          AND TRIM(airline_name) <> ''
    """, (rid, d_from)).fetchall()
    conn.close()

    lcc_codes, lcc_keywords = load_lcc_config()
    daily = {}
    for d, price, airline_name, airline_code, is_lcc in rows:
        if not is_traditional_flight(airline_name, airline_code, is_lcc, lcc_codes, lcc_keywords):
            continue
        daily[d] = min(price, daily.get(d, price))

    if not daily:
        send_text(chat_id, f"#{rid} {name}\n過去 {days} 天沒有傳統航空資料可畫。")
        return

    dates = sorted(daily.keys())
    prices = [daily[d] for d in dates]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(range(len(dates)), prices, marker='o', linewidth=1.5)
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(dates, rotation=45, ha='right', fontsize=8)
    ax.set_ylabel('Price (TWD)')
    ax.set_title(f'Route #{rid} - Last {days} days (Full-service carriers only)')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out = Path('/tmp/chart.png')
    fig.savefig(out, dpi=120)
    plt.close(fig)

    caption = f"#{rid} {name}\n過去 {days} 天最低價走勢（傳統航空）"
    send_photo(chat_id, out, caption=caption)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', required=True, choices=['history', 'best', 'chart'])
    ap.add_argument('--route-id', type=int, required=True)
    ap.add_argument('--chat-id', required=True)
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()
    args.days = min(365, max(1, args.days))

    if args.action == 'history':
        action_history(args.route_id, args.days, args.chat_id)
    elif args.action == 'best':
        action_best(args.route_id, args.chat_id)
    elif args.action == 'chart':
        action_chart(args.route_id, args.days, args.chat_id)


if __name__ == '__main__':
    main()

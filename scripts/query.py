"""
查詢工具：被 Worker 透過 query.yml workflow 觸發
action=history   過去 N 天每日最低
action=best      歷史最低 5 筆
action=chart     價格走勢 PNG（sendPhoto）
action=debug     上次掃描診斷

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
SCRAPE_STATE_JSON = ROOT / 'data' / 'scrape_state.json'
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


def load_scrape_state():
    if not SCRAPE_STATE_JSON.exists():
        return {}
    try:
        with open(SCRAPE_STATE_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


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


def airline_search_url(airline_name):
    if not airline_name:
        return None
    return "https://www.google.com/search?q=" + quote_plus(f"{airline_name} official site booking")


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


def send_photo(chat_id, png_path, caption='', buttons=None):
    if not TOKEN:
        print(f"[no token] would send photo {png_path}: {caption}")
        return
    import requests

    data = {'chat_id': chat_id, 'caption': caption}
    if buttons:
        data['reply_markup'] = json.dumps({'inline_keyboard': buttons}, ensure_ascii=False)
    with open(png_path, 'rb') as f:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data=data,
            files={'photo': f},
            timeout=60,
        )
        resp.raise_for_status()


def route_label(route):
    if not route:
        return '找不到路線設定'
    dest = '/'.join(route.get('destinations') or ['?'])
    cabins = '/'.join(route.get('cabin_classes') or ['?'])
    rng = route.get('depart_date_range') or {}
    return (
        f"{route.get('origin', '?')} → {dest}｜{cabins}｜"
        f"{rng.get('start', '?')} 至 {rng.get('end', '?')}｜"
        f"{route.get('trip_duration_days', '?')} 天｜跨 {route.get('must_contain_full_weekends', 0)} 個週末"
    )


def money(n):
    if n is None:
        return '無資料'
    return f"NT$ {int(n):,}"


def stops_label(stops):
    if stops in (None, ''):
        return '轉機不明'
    return '直飛' if stops == 0 else f"轉機 {stops} 次"


def get_latest_scan_date(conn, rid):
    row = conn.execute(
        "SELECT DATE(MAX(scan_ts)), MAX(scan_ts) FROM prices WHERE route_id = ?",
        (rid,),
    ).fetchone()
    if not row or not row[0]:
        return None, None
    return row[0], row[1]


def summarize_expected_vs_actual(route, breakdown):
    if not route:
        return []
    expected_origin = route.get('origin')
    expected_dest = set(route.get('destinations') or [])
    expected_cabin = set(route.get('cabin_classes') or [])
    actual_origin = {r[0] for r in breakdown if r[0]}
    actual_dest = {r[1] for r in breakdown if r[1]}
    actual_cabin = {r[2] for r in breakdown if r[2]}

    lines = []
    if actual_origin and expected_origin not in actual_origin:
        lines.append(f"出發地不一致：設定 {expected_origin}，資料庫看到 {', '.join(sorted(actual_origin))}")
    if actual_dest and not actual_dest.issubset(expected_dest):
        lines.append(f"目的地不一致：設定 {', '.join(sorted(expected_dest))}，資料庫看到 {', '.join(sorted(actual_dest))}")
    if actual_cabin and not actual_cabin.issubset(expected_cabin):
        lines.append(f"艙等不一致：設定 {', '.join(sorted(expected_cabin))}，資料庫看到 {', '.join(sorted(actual_cabin))}")
    if not lines:
        lines.append("設定與最新資料庫分布看起來一致。")
    return lines


def unique_price_rows(rows, limit):
    seen = set()
    out = []
    for row in rows:
        price, airline_name, _, dd, rd, dest, stops, _, cabin = row
        key = (price, airline_name, dd, rd, dest, stops, cabin)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


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
        buttons = [[{'text': f"#{rid} 開 Google Flights", 'url': google_flights_url(route)}]] if route else None
        send_text(
            chat_id,
            f"📈 #{rid} {name}｜每日最低\n\n一句話：過去 {days} 天沒有傳統航空資料。\n建議：先開 Google Flights 或跑 /debug {rid} 檢查抓取狀態。",
            buttons=buttons,
        )
        return

    best_day = min(daily, key=daily.get)
    latest_day = max(daily)
    lines = [
        f"📈 #{rid} {name}｜每日最低",
        "",
        f"一句話：過去 {days} 天最低是 {best_day} 的 NT$ {daily[best_day]:,}。",
        f"最新一天：{latest_day}｜NT$ {daily[latest_day]:,}｜{counts[latest_day]} 筆",
        "",
        "每日明細（傳統航空）",
    ]
    for d in sorted(daily.keys(), reverse=True):
        lines.append(f"{d}：NT$ {daily[d]:,}（{counts[d]} 筆）")
    lines.append("")
    lines.append("查票：下方按鈕會開 Google Flights 重新查即時價格。")
    buttons = [[{'text': f"#{rid} 開 Google Flights", 'url': google_flights_url(route)}]] if route else None
    send_text(chat_id, '\n'.join(lines), buttons=buttons)


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
        send_text(chat_id, f"🏆 #{rid} {name}｜歷史最低\n\n一句話：目前沒有傳統航空資料可排名。")
        return

    best_price, best_airline, best_dd, best_rd, best_dest, best_stops = best_rows[0]
    lines = [
        f"🏆 #{rid} {name}｜歷史最低",
        "",
        f"一句話：目前最低是 {money(best_price)}，{best_airline}，{best_dd} → {best_rd}。",
        "",
        f"最低 {limit} 筆（傳統航空）",
    ]
    buttons = []
    for i, (p, an, dd, rd, dest, stops) in enumerate(best_rows, 1):
        s = "直飛" if stops == 0 else f"轉機 {stops} 次"
        lines.append(f"{i}. {money(p)}｜{an}｜{dd} → {rd}｜{dest}｜{s}")
        row = [{'text': f"第 {i} 筆 Google Flights", 'url': google_flights_url(route, dd, rd, dest)}]
        airline_url = airline_search_url(an)
        if airline_url:
            row.append({'text': '搜尋航空公司官網', 'url': airline_url})
        buttons.append(row)
    lines.append("")
    lines.append("備註：Google Flights 開啟後會重新查價，實際票價與可訂位狀態以頁面顯示為準。")
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
        buttons = [[{'text': f"#{rid} 開 Google Flights", 'url': google_flights_url(route)}]] if route else None
        send_text(chat_id, f"📉 #{rid} {name}｜走勢圖\n\n一句話：過去 {days} 天沒有傳統航空資料可畫。", buttons=buttons)
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

    caption = f"📉 #{rid} {name}｜走勢圖\n過去 {days} 天最低價（傳統航空）"
    buttons = [[{'text': f"#{rid} 開 Google Flights", 'url': google_flights_url(route)}]] if route else None
    send_photo(chat_id, out, caption=caption, buttons=buttons)


def action_debug(rid, chat_id):
    route = load_route(rid)
    name = route['name'] if route else f"#{rid}"
    if not route:
        send_text(chat_id, f"#{rid} 診斷失敗：找不到這條路線設定。")
        return

    lines = [
        f"🧪 #{rid} {name}｜抓取診斷",
        "",
        "先看結論：下面「設定核對」若顯示一致，代表抓取方向大致正確。",
        "",
        "路線設定",
        route_label(route),
        f"啟用狀態：{'啟用' if route.get('active', True) else '暫停'}",
        f"最多轉機：{route.get('max_stops', '未設定')} 次",
        f"價上限：{money(route.get('max_price_twd')) if route.get('max_price_twd') else '不限'}",
        f"通知門檻：{route.get('notify_threshold', '未設定')}",
        "",
    ]

    state = load_scrape_state()
    route_state = (state.get('routes') or {}).get(str(rid), {})
    if route_state:
        lines.extend([
            "最近掃描",
            f"last_scan_ts：{route_state.get('last_scan_ts', '無')}",
            f"last_success_ts：{route_state.get('last_success_ts', '無')}",
            f"last_written：{route_state.get('last_written', 0)} 筆",
            f"consecutive_failures：{route_state.get('consecutive_failures', 0)}",
            "",
        ])
    else:
        lines.extend([
            "最近掃描",
            "scrape_state.json 尚無這條路線紀錄。",
            "",
        ])

    if not DB_PATH.exists():
        lines.append("資料庫狀態：找不到 prices.db。可能是 cache 尚未建立，或 query workflow 沒有還原到資料庫。")
        send_text(chat_id, '\n'.join(lines))
        return

    conn = sqlite3.connect(DB_PATH)
    lcc_codes, lcc_keywords = load_lcc_config()
    latest_date, latest_ts = get_latest_scan_date(conn, rid)
    if not latest_date:
        conn.close()
        lines.append("資料庫狀態：prices.db 存在，但這條路線還沒有任何票價資料。")
        send_text(chat_id, '\n'.join(lines))
        return

    rows = conn.execute("""
        SELECT price_twd, airline_name, airline_code, depart_date, return_date,
               destination, stops, is_lcc, origin, cabin
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) = ?
        ORDER BY price_twd ASC
    """, (rid, latest_date)).fetchall()

    breakdown = conn.execute("""
        SELECT origin, destination, cabin, COUNT(*), MIN(price_twd)
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) = ?
        GROUP BY origin, destination, cabin
        ORDER BY COUNT(*) DESC
    """, (rid, latest_date)).fetchall()
    conn.close()

    traditional = []
    lcc = []
    blank_airline = 0
    for price, airline_name, airline_code, dd, rd, dest, stops, is_lcc, origin, cabin in rows:
        if not (airline_name or '').strip():
            blank_airline += 1
            continue
        item = (price, airline_name, airline_code, dd, rd, dest, stops, origin, cabin)
        if is_traditional_flight(airline_name, airline_code, is_lcc, lcc_codes, lcc_keywords):
            traditional.append(item)
        else:
            lcc.append(item)

    lines.extend([
        "最新資料庫",
        f"最新掃描日期：{latest_date}",
        f"最新 scan_ts：{latest_ts}",
        f"總筆數：{len(rows)} 筆",
        f"傳統航空：{len(traditional)} 筆",
        f"廉航：{len(lcc)} 筆",
    ])
    if blank_airline:
        lines.append(f"航空公司空白：{blank_airline} 筆（已排除在最低票清單外）")
    lines.append("")

    lines.append("設定核對")
    lines.extend(summarize_expected_vs_actual(route, breakdown))
    lines.append("")

    lines.append("實際分布")
    for origin, dest, cabin, n, min_price in breakdown[:8]:
        lines.append(f"{origin} → {dest}｜{cabin}｜{n} 筆｜最低 {money(min_price)}")
    if len(breakdown) > 8:
        lines.append(f"另有 {len(breakdown) - 8} 組分布未列出。")
    lines.append("")

    if traditional:
        lines.append("傳統航空最低 5 筆")
        for i, (price, airline_name, _, dd, rd, dest, stops, _, cabin) in enumerate(unique_price_rows(traditional, 5), 1):
            lines.append(f"{i}. {money(price)}｜{airline_name}｜{dd} 去，{rd} 回｜{dest}｜{cabin}｜{stops_label(stops)}")
    else:
        lines.append("傳統航空最低 5 筆")
        lines.append("沒有可列出的傳統航空票價。")
    lines.append("")

    if lcc:
        lines.append("廉航參考 3 筆")
        for i, (price, airline_name, _, dd, rd, dest, stops, _, cabin) in enumerate(unique_price_rows(lcc, 3), 1):
            lines.append(f"{i}. {money(price)}｜{airline_name}｜{dd} 去，{rd} 回｜{dest}｜{cabin}｜{stops_label(stops)}")
        lines.append("提醒：廉航通常未含行李費，僅供參考。")
        lines.append("")

    lines.append("判讀")
    if traditional:
        lines.append("如果實際分布的出發地、目的地、艙等都和設定一致，代表抓取方向是對的。")
        lines.append("價格仍需點 Google Flights 重新查價確認，因為 Google 會即時更新可訂位與票價。")
    else:
        lines.append("如果總筆數很多但傳統航空為 0，通常是航空公司分類或搜尋結果本身需要檢查。")

    buttons = [[{'text': f"#{rid} 開 Google Flights", 'url': google_flights_url(route)}]]
    send_text(chat_id, '\n'.join(lines), buttons=buttons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', required=True, choices=['history', 'best', 'chart', 'debug'])
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
    elif args.action == 'debug':
        action_debug(args.route_id, args.chat_id)


if __name__ == '__main__':
    main()

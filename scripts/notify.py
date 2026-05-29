"""
推 Telegram 通知
讀 analysis.json + prices.db，輸出包含具體掃描數字的訊息。
讓使用者能一眼分辨「真的沒有便宜票」vs「根本沒抓到資料」。

環境變數：
  TELEGRAM_BOT_TOKEN  必填
  TELEGRAM_CHAT_ID    必填（可逗號分隔多個 chat_id）
  SEND_HEARTBEAT      '1' 時即使沒達門檻也送完整心跳；'0' 時只在達門檻時送
"""

import os
import json
import yaml
import requests
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
import logging
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / 'data' / 'prices.db'
ROUTES_YAML = ROOT / 'routes.yaml'
ROUTES_JSON = ROOT / 'routes.json'
ANALYSIS_JSON = ROOT / 'data' / 'analysis.json'
SCRAPE_STATE_JSON = ROOT / 'data' / 'scrape_state.json'

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_IDS = [c.strip() for c in os.environ['TELEGRAM_CHAT_ID'].split(',') if c.strip()]

# 異常下殺：今日 vs 上次最低價跌幅超過此 % 會獨立警報（無視 notify_threshold）
ANOMALY_DROP_PCT = -20.0

# 連續失敗：scrape_state.json 中 consecutive_failures ≥ 此數會警報
FAILURE_THRESHOLD = 3

STATUS_LABEL = {
    'cheap': '便宜',
    'good': '不錯',
    'normal': '普通',
    'expensive': '偏貴',
    'insufficient_data': '資料不足',
}

STATUS_EXPLAIN = {
    'cheap': '低於歷史便宜區，值得優先檢查。',
    'good': '低於近期常見價格，可以列入觀察。',
    'normal': '接近歷史常見區間，不急。',
    'expensive': '高於歷史常見區間，除非行程剛需，否則先等等。',
    'insufficient_data': '歷史資料還不夠，現在只能看「有沒有票」和「今天最低價」。',
}

CABIN_LABEL = {
    'economy': '經濟艙',
    'premium_economy': '豪華經濟艙',
    'business': '商務艙',
    'first': '頭等艙',
}

CABIN_QUERY_LABEL = {
    'economy': 'economy',
    'premium_economy': 'premium economy',
    'business': 'business class',
    'first': 'first class',
}


# ─────────── 讀檔 ───────────

def load_routes():
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return {r['id']: r for r in json.load(f)['routes']}
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return {r['id']: r for r in yaml.safe_load(f)['routes']}


# ─────────── 門檻判斷 ───────────

def should_notify(threshold, status):
    if threshold == 'any':
        return True
    th = {'cheap': 0, 'good': 1, 'normal': 2}.get(threshold, 99)
    st = {'cheap': 0, 'good': 1, 'normal': 2}.get(status, 99)
    return st <= th


# ─────────── SQLite 查詢 ───────────

def get_top_flights(conn, route_id, today_str, is_lcc, limit=3):
    cur = conn.execute("""
        SELECT airline_name, depart_date, return_date, MIN(price_twd) as p,
               MIN(depart_time), MIN(stops), destination
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) = ?
          AND is_lcc = ?
        GROUP BY airline_name, depart_date, return_date, destination
        ORDER BY p ASC
        LIMIT ?
    """, (route_id, today_str, is_lcc, limit))
    return cur.fetchall()


def get_today_counts(conn, route_id, today_str):
    cur = conn.execute("""
        SELECT
            SUM(CASE WHEN is_lcc = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_lcc = 1 THEN 1 ELSE 0 END)
        FROM prices
        WHERE route_id = ?
          AND DATE(scan_ts) = ?
    """, (route_id, today_str))
    r = cur.fetchone()
    return (r[0] or 0, r[1] or 0)


def get_yesterday_min(conn, route_id, today_str):
    """昨日（最近一次有資料的前一天）傳統航空最低價"""
    cur = conn.execute("""
        SELECT MIN(price_twd) FROM prices
        WHERE route_id = ?
          AND is_lcc = 0
          AND DATE(scan_ts) < ?
          AND DATE(scan_ts) >= DATE(?, '-7 day')
        GROUP BY DATE(scan_ts)
        ORDER BY DATE(scan_ts) DESC
        LIMIT 1
    """, (route_id, today_str, today_str))
    row = cur.fetchone()
    return row[0] if row else None


def get_total_today(conn, today_str):
    cur = conn.execute("SELECT COUNT(*) FROM prices WHERE DATE(scan_ts) = ?", (today_str,))
    return cur.fetchone()[0]


def money(n):
    if n is None:
        return '無資料'
    return f"NT$ {int(n):,}"


def cabin_label(route):
    cabins = route.get('cabin_classes') or []
    return '、'.join(CABIN_LABEL.get(c, c) for c in cabins) or '未設定'


def format_stops(stops):
    if stops in (None, ''):
        return '轉機不明'
    return '直飛' if stops == 0 else f"轉機 {stops} 次"


def format_date_pair(depart_date, return_date):
    return f"{depart_date} 去，{return_date} 回"


def google_flights_url(route, depart_date=None, return_date=None, destination=None):
    origin = route.get('origin', '')
    dest = destination or (route.get('destinations') or [''])[0]
    cabin = CABIN_QUERY_LABEL.get((route.get('cabin_classes') or ['economy'])[0], 'economy')

    if depart_date and return_date:
        query = f"Flights from {origin} to {dest} on {depart_date} returning {return_date} {cabin}"
    else:
        rng = route.get('depart_date_range') or {}
        start = rng.get('start', '')
        end = rng.get('end', '')
        query = f"Flights from {origin} to {dest} {start} {end} {cabin}"
    return "https://www.google.com/travel/flights?q=" + quote_plus(query)


def get_best_traditional_flight(conn, route_id, today_str):
    rows = get_top_flights(conn, route_id, today_str, is_lcc=0, limit=1)
    return rows[0] if rows else None


def build_link_buttons(conn, analyses, routes, today_str):
    rows = []
    for a in analyses:
        route = routes.get(a['route_id'])
        if not route or a.get('today_min') is None:
            continue
        best = get_best_traditional_flight(conn, a['route_id'], today_str)
        if best:
            _, dd, rd, _, _, _, dest = best
            url = google_flights_url(route, dd, rd, dest)
        else:
            url = google_flights_url(route)
        rows.append([{'text': f"#{a['route_id']} 開 Google Flights", 'url': url}])
    return rows


# ─────────── 訊息組裝 ───────────

def collect_notify_ids(analyses, routes):
    """挑出達 notify_threshold 的路線 id"""
    notify_ids = set()
    for a in analyses:
        route = routes.get(a['route_id'])
        if not route:
            continue
        if a.get('today_min') is None:
            continue
        analysis = a.get('analysis_90') or a.get('analysis_30') or ['insufficient_data', '']
        status = analysis[0]
        threshold = route.get('notify_threshold', 'cheap')
        if should_notify(threshold, status):
            notify_ids.add(a['route_id'])
    return notify_ids


def load_scrape_state():
    if not SCRAPE_STATE_JSON.exists():
        return {}
    try:
        with open(SCRAPE_STATE_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def collect_anomaly_alerts(conn, analyses, routes, today_str):
    """跌幅 ≥ ANOMALY_DROP_PCT 的路線（無視 threshold）"""
    alerts = []
    for a in analyses:
        if a.get('today_min') is None:
            continue
        yest = get_yesterday_min(conn, a['route_id'], today_str)
        if not yest:
            continue
        today_min = a['today_min']
        diff = today_min - yest
        pct = diff / yest * 100
        if pct <= ANOMALY_DROP_PCT:
            route = routes.get(a['route_id'], {})
            name = route.get('name', a.get('route_name', f"#{a['route_id']}"))
            alerts.append({
                'id': a['route_id'],
                'name': name,
                'yest': yest,
                'today': today_min,
                'pct': pct,
            })
    return alerts


def collect_failure_alerts(state, routes):
    """連續失敗 ≥ FAILURE_THRESHOLD 次的路線"""
    alerts = []
    for rid_str, info in (state.get('routes') or {}).items():
        failures = info.get('consecutive_failures', 0)
        if failures < FAILURE_THRESHOLD:
            continue
        try:
            rid = int(rid_str)
        except ValueError:
            continue
        route = routes.get(rid)
        if not route:
            continue
        if route.get('active') is False:
            continue
        alerts.append({
            'id': rid,
            'name': route.get('name', f"#{rid}"),
            'failures': failures,
            'last_success_ts': info.get('last_success_ts'),
        })
    return alerts


def build_route_block(conn, a, route, today_str, verbose):
    """單條路線一段：先講結論，再放最低票與資料狀態。"""
    lines = []
    name = route.get('name', a.get('route_name', f"#{a['route_id']}"))
    origin = route.get('origin', '?')
    dest_str = '/'.join(route.get('destinations') or ['?'])
    rng = route.get('depart_date_range') or {}
    weekends = route.get('must_contain_full_weekends') or 0

    lines.append(f"#{a['route_id']} {name}")
    lines.append(f"路線：{origin} → {dest_str}｜{cabin_label(route)}｜{route.get('trip_duration_days')} 天｜跨 {weekends} 個完整週末")
    lines.append(f"日期：{rng.get('start', '?')} 至 {rng.get('end', '?')}")

    trad_n, lcc_n = get_today_counts(conn, a['route_id'], today_str)

    today_min = a.get('today_min')
    if today_min is None:
        lines.append("結果：今天沒有抓到傳統航空票價。")
        lines.append(f"資料量：傳統航空 {trad_n} 筆，廉航 {lcc_n} 筆")
        lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=2)
        if lcc_top:
            lines.append("廉航參考：")
            for i, (an, dd, rd, price, _, stops, _) in enumerate(lcc_top, 1):
                lines.append(f"{i}. {an or '航空公司不明'}｜{format_date_pair(dd, rd)}｜{format_stops(stops)}｜{money(price)}")
            lines.append("提醒：廉航價格通常未含行李費，先不要拿來跟傳統航空直接比較。")
        return lines

    analysis = a.get('analysis_90') or a.get('analysis_30') or ['insufficient_data', '']
    status, reason = analysis
    label = STATUS_LABEL.get(status, status)
    lines.append(f"結果：有抓到票，最低 {money(today_min)}。")
    lines.append(f"判斷：{label}。{STATUS_EXPLAIN.get(status, reason)}")
    if reason:
        lines.append(f"原因：{reason}")
    lines.append(f"資料量：傳統航空 {trad_n} 筆，廉航 {lcc_n} 筆")
    lines.append(f"歷史樣本：30 天 {a['history_count_30']} 天｜90 天 {a['history_count_90']} 天｜365 天 {a['history_count_365']} 天")

    yest = get_yesterday_min(conn, a['route_id'], today_str)
    if yest:
        diff = today_min - yest
        pct = diff / yest * 100 if yest else 0
        if diff < 0:
            lines.append(f"變化：比上次低 {money(abs(diff))}（{pct:+.1f}%）。")
        elif diff > 0:
            lines.append(f"變化：比上次高 {money(abs(diff))}（{pct:+.1f}%）。")
        else:
            lines.append("變化：和上次最低價相同。")

    top_limit = 3 if verbose else 1
    top = get_top_flights(conn, a['route_id'], today_str, is_lcc=0, limit=top_limit)
    if top:
        title = "最低幾組（傳統航空）：" if verbose else "今日最低組合："
        lines.append(title)
        for i, (an, dd, rd, price, _, stops, _) in enumerate(top, 1):
            lines.append(f"{i}. {an or '航空公司不明'}｜{format_date_pair(dd, rd)}｜{format_stops(stops)}｜{money(price)}")

    lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=1 if not verbose else 2)
    if lcc_top:
        lines.append("廉航參考（未含行李費）：")
        for i, (an, dd, rd, price, _, stops, _) in enumerate(lcc_top, 1):
            lines.append(f"{i}. {an or '航空公司不明'}｜{format_date_pair(dd, rd)}｜{format_stops(stops)}｜{money(price)}")

    lines.append("查詢：可點下方 Google Flights 按鈕重新查同一組條件。")
    return lines


def build_full_message(conn, analyses, routes, notify_ids, anomalies, failures):
    today = date.today()
    today_str = today.isoformat()
    now_str = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M')
    total_today = get_total_today(conn, today_str)

    active = [a for a in analyses if routes.get(a['route_id'])]
    no_data_n = sum(1 for a in active if a.get('today_min') is None)

    if anomalies:
        conclusion = f"有 {len(anomalies)} 條出現明顯降價，建議先檢查。"
    elif notify_ids:
        conclusion = f"有 {len(notify_ids)} 條達通知門檻。"
    elif failures:
        conclusion = f"有 {len(failures)} 條連續抓不到資料，需要檢查設定。"
    elif no_data_n:
        conclusion = f"有 {no_data_n} 條今天沒有抓到傳統航空資料。"
    else:
        conclusion = "系統正常，這次沒有達通知門檻。"

    lines = [
        f"機票追蹤更新｜{now_str} 台北時間",
        f"結論：{conclusion}",
        f"本次掃描：{len(active)} 條路線，寫入 {total_today} 筆票價",
    ]
    if no_data_n:
        lines.append(f"資料提醒：其中 {no_data_n} 條今天沒有傳統航空票價")
    lines.append("")

    # 異常下殺警報（最顯眼，放在最上面）
    if anomalies:
        lines.append("明顯降價")
        for al in anomalies:
            lines.append(
                f"#{al['id']} {al['name']}：{money(al['yest'])} → {money(al['today'])}（{al['pct']:+.1f}%）"
            )
        lines.append("")

    # 連續失敗警報
    if failures:
        lines.append("需要檢查的路線")
        for fa in failures:
            ts = fa.get('last_success_ts') or '從未成功'
            lines.append(f"#{fa['id']} {fa['name']}：已連續 {fa['failures']} 次無資料，上次成功 {ts}")
        lines.append("")

    for a in active:
        route = routes[a['route_id']]
        # 異常下殺的路線一律展開細節，方便看
        verbose = a['route_id'] in notify_ids or any(al['id'] == a['route_id'] for al in anomalies)
        lines.extend(build_route_block(conn, a, route, today_str, verbose=verbose))
        lines.append("")

    lines.append("說明：Google Flights 開啟後仍會重新查價，實際票價與可訂位狀態以頁面顯示為準。")

    return "\n".join(lines).strip()


# ─────────── Telegram ───────────

def send_telegram(text, buttons=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'text': text}
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': buttons}
    for chat_id in CHAT_IDS:
        try:
            resp = requests.post(url, json={'chat_id': chat_id, **payload}, timeout=20)
            resp.raise_for_status()
            log.info(f"Telegram 訊息已送出 → {chat_id}")
        except Exception as e:
            log.error(f"Telegram 送出失敗 → {chat_id}: {e}")


# ─────────── main ───────────

def main():
    if not ANALYSIS_JSON.exists():
        log.warning("找不到 analysis.json，跳過通知")
        return
    with open(ANALYSIS_JSON, 'r', encoding='utf-8') as f:
        analyses = json.load(f)
    routes = load_routes()
    conn = sqlite3.connect(DB_PATH)

    today_str = date.today().isoformat()
    notify_ids = collect_notify_ids(analyses, routes)
    anomalies = collect_anomaly_alerts(conn, analyses, routes, today_str)
    state = load_scrape_state()
    failures = collect_failure_alerts(state, routes)
    send_heartbeat = os.environ.get('SEND_HEARTBEAT') == '1'

    # 異常下殺一律送，無視 threshold
    must_send = bool(notify_ids or anomalies or failures or send_heartbeat)
    if not must_send:
        log.info("無達門檻、無異常、無失敗、未啟用心跳，不送訊息")
        conn.close()
        return

    msg = build_full_message(conn, analyses, routes, notify_ids, anomalies, failures)
    buttons = build_link_buttons(conn, analyses, routes, today_str)
    conn.close()
    if msg:
        send_telegram(msg, buttons=buttons)


if __name__ == '__main__':
    main()

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

STATUS_EMOJI = {
    'cheap': '💚 便宜',
    'good': '💛 還不錯',
    'normal': '🟡 普通',
    'expensive': '🔴 偏貴',
    'insufficient_data': '⚪ 資料不足',
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
            name = a.get('route_name', route.get('name', f"#{a['route_id']}"))
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
    """單條路線一段：verbose=True 時加 Top 航班，False 時只列摘要"""
    lines = []
    name = a.get('route_name', route.get('name', f"#{a['route_id']}"))
    origin = route.get('origin', '?')
    dest_str = '/'.join(route.get('destinations') or ['?'])
    cabin = ','.join(route.get('cabin_classes') or [])
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(f"📍 #{a['route_id']} {name}")
    lines.append(f"   {origin}→{dest_str}（{cabin}）")

    trad_n, lcc_n = get_today_counts(conn, a['route_id'], today_str)
    lines.append(f"   今日抓到：傳統 {trad_n} 筆 / 廉航 {lcc_n} 筆")

    today_min = a.get('today_min')
    if today_min is None:
        lines.append("   ❌ 今日無傳統航空資料")
        lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=3)
        if lcc_top:
            lines.append("   ⚠️ 僅有廉航（未含行李費）：")
            for i, (an, dd, rd, price, _, stops, _) in enumerate(lcc_top, 1):
                stops_str = "直飛" if stops == 0 else f"轉{stops}"
                lines.append(f"     {i}. {an or '?'} {dd}→{rd} {stops_str} NT$ {price:,}")
        return lines

    analysis = a.get('analysis_90') or a.get('analysis_30') or ['insufficient_data', '']
    status, reason = analysis
    emoji = STATUS_EMOJI.get(status, '⚪')
    lines.append(f"   💰 今日最低 NT$ {today_min:,} {emoji}")
    lines.append(f"   📊 {reason}")
    lines.append(f"   📈 樣本 30/90/365：{a['history_count_30']}/{a['history_count_90']}/{a['history_count_365']} 天")

    yest = get_yesterday_min(conn, a['route_id'], today_str)
    if yest:
        diff = today_min - yest
        pct = diff / yest * 100 if yest else 0
        arrow = "↓" if diff < 0 else ("↑" if diff > 0 else "→")
        lines.append(f"   📉 vs 上次：{arrow} NT$ {abs(diff):,} ({pct:+.1f}%)")

    if not verbose:
        return lines

    top = get_top_flights(conn, a['route_id'], today_str, is_lcc=0)
    if top:
        lines.append("   🛫 傳統航空最便宜：")
        for i, (an, dd, rd, price, _, stops, _) in enumerate(top, 1):
            stops_str = "直飛" if stops == 0 else f"轉{stops}"
            lines.append(f"     {i}. {an or '?'} {dd}→{rd} {stops_str} NT$ {price:,}")
    lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=2)
    if lcc_top:
        lines.append("   ⚠️ 廉航（未含行李費，僅供參考）：")
        for i, (an, dd, rd, price, _, stops, _) in enumerate(lcc_top, 1):
            stops_str = "直飛" if stops == 0 else f"轉{stops}"
            lines.append(f"     {i}. {an or '?'} {dd}→{rd} {stops_str} NT$ {price:,}")
    return lines


def build_full_message(conn, analyses, routes, notify_ids, anomalies, failures):
    today = date.today()
    today_str = today.isoformat()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    total_today = get_total_today(conn, today_str)

    active = [a for a in analyses if routes.get(a['route_id'])]
    no_data_n = sum(1 for a in active if a.get('today_min') is None)

    if anomalies:
        head_emoji = "⚡"
    elif notify_ids:
        head_emoji = "🔔"
    else:
        head_emoji = "💓"

    lines = [
        f"{head_emoji} 機票雷達 {now_str}",
        f"📦 共掃 {len(active)} 條路線，今日總寫入 {total_today} 筆",
    ]
    if no_data_n:
        lines.append(f"⚠️ 其中 {no_data_n} 條今日 0 筆資料")
    if notify_ids:
        lines.append(f"🔔 {len(notify_ids)} 條達通知門檻（下方展開）")
    else:
        lines.append("ℹ️ 本次沒有路線達通知門檻")
    lines.append("")

    # 異常下殺警報（最顯眼，放在最上面）
    if anomalies:
        lines.append("⚡⚡⚡ 異常下殺警報 ⚡⚡⚡")
        for al in anomalies:
            lines.append(
                f"  #{al['id']} {al['name']}：NT$ {al['yest']:,} → NT$ {al['today']:,} ({al['pct']:+.1f}%)"
            )
        lines.append("")

    # 連續失敗警報
    if failures:
        lines.append("⚠️ 連續掃描失敗（請檢查設定）：")
        for fa in failures:
            ts = fa.get('last_success_ts') or '從未成功'
            lines.append(f"  #{fa['id']} {fa['name']}：已連續 {fa['failures']} 次無資料（上次成功 {ts}）")
        lines.append("")

    for a in active:
        route = routes[a['route_id']]
        # 異常下殺的路線一律展開細節，方便看
        verbose = a['route_id'] in notify_ids or any(al['id'] == a['route_id'] for al in anomalies)
        lines.extend(build_route_block(conn, a, route, today_str, verbose=verbose))

    return "\n".join(lines)


# ─────────── Telegram ───────────

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            resp = requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=20)
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
    conn.close()
    if msg:
        send_telegram(msg)


if __name__ == '__main__':
    main()

"""
推 Telegram 通知
讀 analysis.json + prices.db，輸出包含具體掃描數字的訊息。
讓使用者能一眼分辨「真的沒有便宜票」vs「根本沒抓到資料」。

環境變數：
  TELEGRAM_BOT_TOKEN  必填
  TELEGRAM_CHAT_ID    必填（可逗號分隔多個 chat_id）
  SEND_HEARTBEAT      '1' 時即使沒達門檻也送完整心跳；'0' 時只在達門檻時送
  STATUS_WEBHOOK_URL  選填；設定後會把 data/status.json 同步到 Worker KV

產出檔：
  data/status.json          /menu 狀態面板用
  data/notified_state.json  避免同一價格或同一事件重複通知
"""

import os
import json
import yaml
import requests
import sqlite3
import re
from datetime import datetime, timedelta, timezone
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
STATUS_JSON = ROOT / 'data' / 'status.json'
NOTIFIED_STATE_JSON = ROOT / 'data' / 'notified_state.json'
TAIPEI = ZoneInfo('Asia/Taipei')
SCAN_DATE_SQL = "DATE(scan_ts, '+8 hours')"

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_IDS = [c.strip() for c in os.environ['TELEGRAM_CHAT_ID'].split(',') if c.strip()]
STATUS_WEBHOOK_URL = os.environ.get('STATUS_WEBHOOK_URL', '').strip()

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

STATUS_RANK = {
    'cheap': 0,
    'good': 1,
    'normal': 2,
    'expensive': 3,
    'insufficient_data': 9,
}

STATUS_BADGE = {
    'cheap': '💰 便宜',
    'good': '🟡 不錯',
    'normal': '⚪ 普通',
    'expensive': '⚪ 偏貴',
    'insufficient_data': '🔵 資料不足',
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

NORMAL_NO_DATA_ISSUES = {'no_direct_cabin_results', 'no_cabin_results'}


def utc_now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def utc_now_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


# ─────────── 讀檔 ───────────

def load_routes():
    if ROUTES_JSON.exists():
        with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
            return {r['id']: r for r in json.load(f)['routes']}
    with open(ROUTES_YAML, 'r', encoding='utf-8') as f:
        return {r['id']: r for r in yaml.safe_load(f)['routes']}


def load_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"{path.name} 讀取失敗，改用空狀態：{e}")
        return fallback


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────── 門檻判斷 ───────────

def should_notify(threshold, status):
    if threshold == 'any':
        return True
    th = {'cheap': 0, 'good': 1, 'normal': 2}.get(threshold, 99)
    st = {'cheap': 0, 'good': 1, 'normal': 2}.get(status, 99)
    return st <= th


# ─────────── SQLite 查詢 ───────────

_PRICE_COLUMNS = None

def price_columns(conn):
    global _PRICE_COLUMNS
    if _PRICE_COLUMNS is None:
        _PRICE_COLUMNS = {row[1] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
    return _PRICE_COLUMNS

def get_top_flights(conn, route_id, today_str, is_lcc, limit=3):
    cols = price_columns(conn)
    return_depart_expr = "return_depart_time" if "return_depart_time" in cols else "NULL"
    return_arrive_expr = "return_arrive_time" if "return_arrive_time" in cols else "NULL"
    lcc_filter = "is_lcc IS NULL" if is_lcc is None else "is_lcc = ?"
    params = [route_id, today_str]
    if is_lcc is not None:
        params.append(is_lcc)
    params.append(limit)
    cur = conn.execute(f"""
        SELECT airline_name, depart_date, return_date, price_twd,
               depart_time, arrive_time, stops, destination,
               return_depart_time, return_arrive_time
        FROM (
            SELECT
                airline_name, depart_date, return_date, price_twd,
                depart_time, arrive_time, stops, destination,
                {return_depart_expr} AS return_depart_time,
                {return_arrive_expr} AS return_arrive_time,
                ROW_NUMBER() OVER (
                    PARTITION BY airline_name, depart_date, return_date, destination
                    ORDER BY price_twd ASC, COALESCE(depart_time, ''), id ASC
                ) AS rn
            FROM prices
            WHERE route_id = ?
              AND {SCAN_DATE_SQL} = ?
              AND {lcc_filter}
        )
        WHERE rn = 1
        ORDER BY price_twd ASC, COALESCE(depart_time, ''), airline_name
        LIMIT ?
    """, params)
    return cur.fetchall()


def get_today_counts(conn, route_id, today_str):
    cur = conn.execute(f"""
        SELECT
            SUM(CASE WHEN is_lcc = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_lcc = 1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_lcc IS NULL THEN 1 ELSE 0 END)
        FROM prices
        WHERE route_id = ?
          AND {SCAN_DATE_SQL} = ?
    """, (route_id, today_str))
    r = cur.fetchone()
    return (r[0] or 0, r[1] or 0, r[2] or 0)


def get_yesterday_min(conn, route_id, today_str):
    """昨日（最近一次有資料的前一天）傳統航空最低價"""
    cur = conn.execute(f"""
        SELECT MIN(price_twd) FROM prices
        WHERE route_id = ?
          AND is_lcc = 0
          AND {SCAN_DATE_SQL} < ?
          AND {SCAN_DATE_SQL} >= DATE(?, '-7 day')
        GROUP BY {SCAN_DATE_SQL}
        ORDER BY {SCAN_DATE_SQL} DESC
        LIMIT 1
    """, (route_id, today_str, today_str))
    row = cur.fetchone()
    return row[0] if row else None


def get_total_today(conn, today_str):
    cur = conn.execute(f"SELECT COUNT(*) FROM prices WHERE {SCAN_DATE_SQL} = ?", (today_str,))
    return cur.fetchone()[0]


def money(n):
    if n is None:
        return '無資料'
    return f"NT$ {int(n):,}"


def signed_money(n):
    sign = '+' if n > 0 else '-'
    return f"{sign}NT$ {abs(int(n)):,}"

def primary_history_stats(a):
    return a.get('history_stats_90') or a.get('history_stats_30') or {}

def format_history_basis(a, today_min, status):
    stats = primary_history_stats(a)
    if (stats.get('count') or 0) < 7:
        return None, None

    p25 = stats.get('p25')
    p50 = stats.get('p50')
    p75 = stats.get('p75')
    count = stats.get('count', 0)
    basis = (
        f"歷史區間：{count} 天樣本｜"
        f"便宜線 P25 {money(p25)}｜"
        f"中位 P50 {money(p50)}｜"
        f"偏貴線 P75 {money(p75)}"
    )

    gap = None
    if today_min is not None:
        if status == 'cheap' and p25 is not None:
            gap = f"差距：比便宜線低 {money(p25 - today_min)}"
        elif status == 'good' and p50 is not None:
            gap = f"差距：比中位數低 {money(p50 - today_min)}"
        elif status == 'normal' and p25 is not None and p75 is not None:
            gap = f"差距：落在常見區 {money(p25)} ~ {money(p75)}"
        elif status == 'expensive' and p75 is not None:
            gap = f"差距：比偏貴線高 {money(today_min - p75)}"

    return basis, gap


def cabin_label(route):
    cabins = route.get('cabin_classes') or []
    return '、'.join(CABIN_LABEL.get(c, c) for c in cabins) or '未設定'


def format_stops(stops):
    if stops in (None, ''):
        return '轉機不明'
    return '直飛' if stops == 0 else f"轉機 {stops} 次"


def format_date_pair(depart_date, return_date):
    return f"{depart_date} 去，{return_date} 回"

def format_time_short(value):
    if not value:
        return '時間不明'
    raw = ' '.join(str(value).strip().split())
    if not raw:
        return '時間不明'
    time_part = re.split(r'\s+on\s+', raw, maxsplit=1)[0].strip()
    time_part = re.sub(r'\s*\+\d+$', '', time_part).strip()
    for fmt in ('%I:%M %p', '%H:%M'):
        try:
            return datetime.strptime(time_part, fmt).strftime('%H:%M')
        except ValueError:
            pass
    return time_part or raw

def format_flight_times(depart_time, arrive_time, return_depart_time=None, return_arrive_time=None):
    outbound = f"去程 {format_time_short(depart_time)}→{format_time_short(arrive_time)}"
    if return_depart_time or return_arrive_time:
        inbound = f"回程 {format_time_short(return_depart_time)}→{format_time_short(return_arrive_time)}"
        return f"{outbound}｜{inbound}"
    return f"{outbound}｜回程時間待確認"


def route_meta(route):
    origin = route.get('origin', '?')
    dest_str = '/'.join(route.get('destinations') or ['?'])
    rng = route.get('depart_date_range') or {}
    start = rng.get('start', '?')
    end = rng.get('end', '?')
    days = route.get('trip_duration_days', '?')
    weekends = route.get('must_contain_full_weekends') or 0
    weekend_text = f"｜跨 {weekends} 週末" if weekends else ""
    return f"{origin} → {dest_str}｜{cabin_label(route)}｜{days} 天{weekend_text}｜{start} ~ {end}"


def priority_profile(price_events, anomalies, failures, no_data_n):
    if anomalies:
        return {
            'level': 'must_read',
            'badge': '🔴 必看',
            'summary': f"有 {len(anomalies)} 條明顯降價，先看這封。",
        }
    if price_events:
        return {
            'level': 'worth_reading',
            'badge': '🟠 值得看',
            'summary': f"有 {len(price_events)} 條達到通知條件，可以打開看。",
        }
    if failures:
        return {
            'level': 'check',
            'badge': '🟡 檢查一下',
            'summary': f"有 {len(failures)} 條連續抓不到資料，可能要修設定。",
        }
    if no_data_n:
        return {
            'level': 'check',
            'badge': '🟡 檢查一下',
            'summary': f"有 {no_data_n} 條今天沒有傳統航空資料，不是價格訊號。",
        }
    return {
        'level': 'skip',
        'badge': '🟢 可略過',
        'summary': '沒有新便宜票，也沒有需要處理的錯誤。',
    }


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


def airline_search_url(airline_name):
    if not airline_name:
        return None
    if 'starlux' in airline_name.lower() or '星宇' in airline_name:
        return None
    return "https://www.google.com/search?q=" + quote_plus(f"{airline_name} official site booking")


def get_best_traditional_flight(conn, route_id, today_str):
    rows = get_top_flights(conn, route_id, today_str, is_lcc=0, limit=1)
    return rows[0] if rows else None


def build_link_buttons(conn, analyses, routes, today_str):
    rows = []
    for a in analyses:
        route = routes.get(a['route_id'])
        if not route:
            continue
        best = get_best_traditional_flight(conn, a['route_id'], today_str)
        if best:
            airline, dd, rd, _, _, _, _, dest, _, _ = best
            url = google_flights_url(route, dd, rd, dest)
        else:
            airline = ''
            url = google_flights_url(route)
        row = [{'text': f"#{a['route_id']} Google Flights", 'url': url}]
        airline_url = airline_search_url(airline)
        if airline_url:
            row.append({'text': '搜尋航空公司官網', 'url': airline_url})
        rows.append(row)
    return rows


# ─────────── 訊息組裝 ───────────

def analysis_status(a):
    analysis = a.get('analysis_90') or a.get('analysis_30') or ['insufficient_data', '']
    return analysis[0], analysis[1] if len(analysis) > 1 else ''


def best_flight_dict(row, route=None):
    if not row:
        return None
    airline, dd, rd, price, depart_time, arrive_time, stops, dest, return_depart_time, return_arrive_time = row
    out = {
        'airline_name': airline,
        'depart_date': dd,
        'return_date': rd,
        'price_twd': price,
        'depart_time': depart_time,
        'arrive_time': arrive_time,
        'return_depart_time': return_depart_time,
        'return_arrive_time': return_arrive_time,
        'time_summary': format_flight_times(depart_time, arrive_time, return_depart_time, return_arrive_time),
        'stops': stops,
        'destination': dest,
    }
    if route:
        out['google_flights_url'] = google_flights_url(route, dd, rd, dest)
        out['airline_search_url'] = airline_search_url(airline)
    return out


def flight_signature(best, today_min):
    if not best:
        return f"price:{today_min}"
    airline, dd, rd, price, depart_time, arrive_time, stops, dest, return_depart_time, return_arrive_time = best
    return '|'.join(str(x) for x in [
        airline, dd, rd, price, depart_time, arrive_time, stops, dest,
        return_depart_time, return_arrive_time,
    ])


def collect_price_events(conn, analyses, routes, today_str, notified_state):
    """只挑出真的需要再次提醒的票價事件，避免同價位每天重複通知。"""
    events = []
    route_states = notified_state.setdefault('routes', {})
    for a in analyses:
        route = routes.get(a['route_id'])
        if not route:
            continue
        if a.get('today_min') is None:
            continue
        status, _ = analysis_status(a)
        threshold = route.get('notify_threshold', 'cheap')
        if not should_notify(threshold, status):
            continue

        rid = str(a['route_id'])
        previous = route_states.get(rid, {})
        today_min = int(a['today_min'])
        prev_min = previous.get('last_min_price_twd')
        prev_status = previous.get('last_status')
        best = get_best_traditional_flight(conn, a['route_id'], today_str)
        signature = flight_signature(best, today_min)
        reason = None

        if prev_min is None:
            reason = '首次達通知門檻'
        elif today_min < int(prev_min):
            diff = int(prev_min) - today_min
            pct = diff / int(prev_min) * 100 if prev_min else 0
            reason = f"比上次通知低 {money(diff)}（-{pct:.1f}%）"
        elif STATUS_RANK.get(status, 99) < STATUS_RANK.get(prev_status, 99):
            reason = f"狀態變好：{STATUS_LABEL.get(prev_status, prev_status)} → {STATUS_LABEL.get(status, status)}"
        elif threshold == 'any' and signature != previous.get('last_signature'):
            reason = '最低組合有變化'

        if reason:
            events.append({
                'id': a['route_id'],
                'name': route.get('name', a.get('route_name', f"#{a['route_id']}")),
                'reason': reason,
                'status': status,
                'today_min': today_min,
                'best': best_flight_dict(best, route),
                'signature': signature,
            })
    return events


def load_scrape_state():
    if not SCRAPE_STATE_JSON.exists():
        return {}
    try:
        with open(SCRAPE_STATE_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def collect_anomaly_alerts(conn, analyses, routes, today_str, notified_state):
    """跌幅 ≥ ANOMALY_DROP_PCT 的路線（無視 threshold）"""
    alerts = []
    route_states = notified_state.setdefault('routes', {})
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
            signature = f"{today_str}:{int(today_min)}:{int(yest)}"
            rid = str(a['route_id'])
            if route_states.get(rid, {}).get('last_anomaly_signature') == signature:
                continue
            route = routes.get(a['route_id'], {})
            name = route.get('name', a.get('route_name', f"#{a['route_id']}"))
            alerts.append({
                'id': a['route_id'],
                'name': name,
                'yest': yest,
                'today': today_min,
                'pct': pct,
                'signature': signature,
            })
    return alerts


def collect_failure_alerts(state, routes, notified_state):
    """連續失敗 ≥ FAILURE_THRESHOLD 次的路線"""
    alerts = []
    route_states = notified_state.setdefault('routes', {})
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
        last_alert_count = route_states.get(rid_str, {}).get('last_failure_alert_count', 0)
        if failures == last_alert_count:
            continue
        if failures != FAILURE_THRESHOLD and failures % FAILURE_THRESHOLD != 0:
            continue
        alerts.append({
            'id': rid,
            'name': route.get('name', f"#{rid}"),
            'failures': failures,
            'last_success_ts': info.get('last_success_ts'),
        })
    return alerts


def update_notified_state(notified_state, price_events, anomalies, failures):
    now = utc_now_iso()
    routes = notified_state.setdefault('routes', {})
    for ev in price_events:
        info = routes.setdefault(str(ev['id']), {})
        info['last_notified_at'] = now
        info['last_min_price_twd'] = int(ev['today_min'])
        info['last_status'] = ev.get('status')
        info['last_signature'] = ev.get('signature')
        info['last_reason'] = ev.get('reason')
    for al in anomalies:
        info = routes.setdefault(str(al['id']), {})
        info['last_anomaly_at'] = now
        info['last_anomaly_signature'] = al.get('signature')
    for fa in failures:
        info = routes.setdefault(str(fa['id']), {})
        info['last_failure_alert_at'] = now
        info['last_failure_alert_count'] = fa.get('failures', 0)
    notified_state['updated_at_utc'] = now


def build_route_block(conn, a, route, today_str, verbose, route_state=None):
    """單條路線一段：固定成「今日、重點、資料、最低票」格式。"""
    lines = []
    name = route.get('name', a.get('route_name', f"#{a['route_id']}"))
    route_state = route_state or {}

    lines.append("────────────")
    lines.append(f"#{a['route_id']} {name}")
    lines.append(route_meta(route))

    trad_n, lcc_n, unknown_n = get_today_counts(conn, a['route_id'], today_str)

    today_min = a.get('today_min')
    if today_min is None:
        source_issue = route_state.get('source_issue')
        diagnostics = route_state.get('last_diagnostics') or route_state.get('last_scrape') or {}
        relaxed = diagnostics.get('relaxed_max_stops') or {}
        cabin_name = cabin_label(route)
        if source_issue in NORMAL_NO_DATA_ISSUES:
            lines.append("今日：⚪ 沒有符合條件票價")
        else:
            lines.append("今日：⚠️ 沒有傳統航空票價")
        if source_issue == 'unclassified_airline':
            lines.append("重點：Google 有回價格但航空公司欄位空白，已列為未分類票價；這比較像來源解析問題，不是沒票。")
        elif source_issue == 'no_direct_cabin_results':
            relaxed_count = relaxed.get('raw_flights', 0)
            lines.append(f"重點：Google 沒回直飛{cabin_name}；放寬轉機後有 {relaxed_count} 筆{cabin_name}結果，代表目前沒有符合直飛條件的可用資料。")
        elif source_issue == 'no_cabin_results':
            lines.append(f"重點：Google 對{cabin_name}連放寬轉機也沒回結果，這是目前來源沒有符合艙等資料，不是價格訊號。")
        elif source_issue == 'no_raw_results':
            lines.append("重點：Google 對這組條件沒有回傳航班；這比較像來源查不到，不是價格訊號。")
        elif source_issue == 'query_errors':
            lines.append("重點：Google 查詢發生錯誤，請用 /debug 看錯誤樣本。")
        else:
            lines.append("重點：這不是便宜或偏貴，而是資料不足；若連續出現再用 /debug 檢查。")
        lines.append(f"資料：傳統 {trad_n} 筆｜廉航 {lcc_n} 筆｜未分類 {unknown_n} 筆")
        sample_flights = relaxed.get('sample_flights') or []
        if source_issue == 'no_direct_cabin_results' and sample_flights:
            lines.append("放寬轉機樣本：")
            for i, sample in enumerate(sample_flights[:2], 1):
                airline = sample.get('airline_name') or '航空公司未解析'
                price = sample.get('price') or '價格待確認'
                stops = format_stops(sample.get('stops'))
                lines.append(f"{i}. {price}｜{airline}｜{sample.get('depart_date')} → {sample.get('return_date')}｜{stops}")
            lines.append("提醒：這些不符合本路線的直飛設定，所以不納入歷史分位。")
        unknown_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=None, limit=2)
        if unknown_top:
            lines.append("未分類票價：")
            for i, (an, dd, rd, price, dep_t, arr_t, stops, _, ret_dep_t, ret_arr_t) in enumerate(unknown_top, 1):
                times = format_flight_times(dep_t, arr_t, ret_dep_t, ret_arr_t)
                lines.append(f"{i}. {money(price)}｜{dd} → {rd}｜{times}｜{format_stops(stops)}")
            lines.append("提醒：未分類票價不納入歷史分位，避免把廉航或未知來源誤當傳統航空。")
        lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=2)
        if lcc_top:
            lines.append("廉航參考：")
            for i, (an, dd, rd, price, dep_t, arr_t, stops, _, ret_dep_t, ret_arr_t) in enumerate(lcc_top, 1):
                times = format_flight_times(dep_t, arr_t, ret_dep_t, ret_arr_t)
                lines.append(f"{i}. {money(price)}｜{an or '航空公司不明'}｜{dd} → {rd}｜{times}｜{format_stops(stops)}")
            lines.append("提醒：廉航多半未含行李費，先不要直接和傳統航空比。")
        return lines

    status, reason = analysis_status(a)
    badge = STATUS_BADGE.get(status, STATUS_LABEL.get(status, status))
    lines.append(f"今日：{money(today_min)}｜{badge}")
    lines.append(f"重點：{STATUS_EXPLAIN.get(status, reason)}")
    basis, gap = format_history_basis(a, today_min, status)
    if basis:
        lines.append(basis)
    if gap:
        lines.append(gap)
    elif reason:
        lines.append(f"依據：{reason}")
    lines.append(
        f"資料：傳統 {trad_n} 筆｜廉航 {lcc_n} 筆｜未分類 {unknown_n} 筆｜歷史 {a['history_count_90']}/7 天"
    )

    yest = get_yesterday_min(conn, a['route_id'], today_str)
    if yest:
        diff = today_min - yest
        pct = diff / yest * 100 if yest else 0
        direction = "低" if diff < 0 else "高"
        if diff == 0:
            lines.append("變化：和上次最低價相同")
        else:
            lines.append(f"變化：比上次{direction} {money(abs(diff))}（{pct:+.1f}%）")

    top_limit = 3 if verbose else 1
    top = get_top_flights(conn, a['route_id'], today_str, is_lcc=0, limit=top_limit)
    if top:
        title = "最低票：" if not verbose else "最低幾組："
        lines.append(title)
        for i, (an, dd, rd, price, dep_t, arr_t, stops, _, ret_dep_t, ret_arr_t) in enumerate(top, 1):
            prefix = f"{i}. " if verbose else ""
            times = format_flight_times(dep_t, arr_t, ret_dep_t, ret_arr_t)
            lines.append(f"{prefix}{money(price)}｜{an or '航空公司不明'}｜{dd} → {rd}｜{times}｜{format_stops(stops)}")

    lcc_top = get_top_flights(conn, a['route_id'], today_str, is_lcc=1, limit=1 if not verbose else 2)
    if lcc_top:
        lines.append("廉航參考：")
        for i, (an, dd, rd, price, dep_t, arr_t, stops, _, ret_dep_t, ret_arr_t) in enumerate(lcc_top, 1):
            times = format_flight_times(dep_t, arr_t, ret_dep_t, ret_arr_t)
            lines.append(f"{i}. {money(price)}｜{an or '航空公司不明'}｜{dd} → {rd}｜{times}｜{format_stops(stops)}")

    lines.append("查票：下方有 Google Flights 按鈕，開啟後會重新查即時價格。")
    return lines


def build_full_message(conn, analyses, routes, price_events, anomalies, failures, scrape_state):
    today = datetime.now(TAIPEI).date()
    today_str = today.isoformat()
    now_str = datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')
    total_today = get_total_today(conn, today_str)

    active = [a for a in analyses if routes.get(a['route_id'])]
    no_data_n = 0
    normal_no_data_n = 0
    for a in active:
        if a.get('today_min') is not None:
            continue
        route_state = (scrape_state.get('routes') or {}).get(str(a['route_id']), {})
        if route_state.get('source_issue') in NORMAL_NO_DATA_ISSUES:
            normal_no_data_n += 1
        else:
            no_data_n += 1
    event_ids = {ev['id'] for ev in price_events}

    priority = priority_profile(price_events, anomalies, failures, no_data_n)

    lines = [
        f"{priority['badge']}｜機票雷達",
        f"{now_str} 台北時間",
        "",
        f"一句話：{priority['summary']}",
        "",
        "重點",
        f"• 新機會 {len(price_events)}｜明顯降價 {len(anomalies)}｜需檢查 {len(failures)}",
        f"• 掃描 {len(active)} 條路線｜寫入 {total_today} 筆票價",
    ]
    if no_data_n:
        lines.append(f"• {no_data_n} 條今天沒有傳統航空票價")
    if normal_no_data_n:
        lines.append(f"• {normal_no_data_n} 條沒有符合條件票價（已判定非價格訊號）")
    lines.append("")

    # 異常下殺警報（最顯眼，放在最上面）
    if anomalies:
        lines.append("💥 明顯降價")
        for al in anomalies:
            lines.append(
                f"#{al['id']} {al['name']}：{money(al['yest'])} → {money(al['today'])}（{al['pct']:+.1f}%）"
            )
        lines.append("")

    if price_events:
        lines.append("🔥 新機會")
        for ev in price_events:
            lines.append(
                f"#{ev['id']} {ev['name']}｜{money(ev['today_min'])}｜{STATUS_BADGE.get(ev['status'], ev['status'])}｜{ev['reason']}"
            )
        lines.append("")

    # 連續失敗警報
    if failures:
        lines.append("⚠️ 需要檢查")
        for fa in failures:
            ts = fa.get('last_success_ts') or '從未成功'
            lines.append(f"#{fa['id']} {fa['name']}：已連續 {fa['failures']} 次無資料，上次成功 {ts}")
        lines.append("")

    lines.append("路線明細")

    for a in active:
        route = routes[a['route_id']]
        # 異常下殺的路線一律展開細節，方便看
        verbose = a['route_id'] in event_ids or any(al['id'] == a['route_id'] for al in anomalies)
        route_state = (scrape_state.get('routes') or {}).get(str(a['route_id']), {})
        lines.extend(build_route_block(conn, a, route, today_str, verbose=verbose, route_state=route_state))
        lines.append("")

    lines.append("備註：Google Flights 會重新查即時價格；實際票價與可訂位狀態以頁面為準。")

    return "\n".join(lines).strip()


def next_scheduled_scan_taipei():
    now = datetime.now(TAIPEI)
    for add_days in (0, 1):
        base = now + timedelta(days=add_days)
        candidate = base.replace(hour=9, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate.strftime('%Y-%m-%d %H:%M 台北時間')
    return ''


def build_status_payload(conn, analyses, routes, price_events, anomalies, failures, scrape_state):
    today_str = datetime.now(TAIPEI).date().isoformat()
    now_utc = utc_now_z()
    now_tpe = datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')
    analysis_by_id = {a['route_id']: a for a in analyses}
    price_event_by_id = {ev['id']: ev for ev in price_events}
    anomaly_by_id = {al['id']: al for al in anomalies}
    failure_by_id = {fa['id']: fa for fa in failures}
    total_today = get_total_today(conn, today_str)

    route_items = []
    for rid in sorted(routes):
        route = routes[rid]
        a = analysis_by_id.get(rid)
        state = (scrape_state.get('routes') or {}).get(str(rid), {})
        trad_n, lcc_n, unknown_n = get_today_counts(conn, rid, today_str)
        status = None
        reason = ''
        today_min = None
        history_counts = {}
        best = None
        if a:
            today_min = a.get('today_min')
            status, reason = analysis_status(a)
            history_counts = {
                '30': a.get('history_count_30', 0),
                '90': a.get('history_count_90', 0),
                '365': a.get('history_count_365', 0),
            }
            if today_min is not None:
                best = best_flight_dict(get_best_traditional_flight(conn, rid, today_str), route)
        route_items.append({
            'id': rid,
            'name': route.get('name', f"#{rid}"),
            'active': route.get('active', True),
            'origin': route.get('origin'),
            'destinations': route.get('destinations') or [],
            'cabin_classes': route.get('cabin_classes') or [],
            'depart_date_range': route.get('depart_date_range') or {},
            'trip_duration_days': route.get('trip_duration_days'),
            'notify_threshold': route.get('notify_threshold', 'cheap'),
            'today_min': today_min,
            'status': status,
            'status_label': STATUS_LABEL.get(status, status) if status else None,
            'reason': reason,
            'traditional_count': trad_n,
            'lcc_count': lcc_n,
            'unclassified_count': unknown_n,
            'history_counts': history_counts,
            'best_flight': best,
            'last_scan_ts': state.get('last_scan_ts'),
            'last_success_ts': state.get('last_success_ts'),
            'last_written': state.get('last_written', 0),
            'consecutive_failures': state.get('consecutive_failures', 0),
            'source_issue': state.get('source_issue'),
            'last_scrape': state.get('last_scrape'),
            'google_flights_url': google_flights_url(route),
            'price_event': price_event_by_id.get(rid),
            'anomaly': anomaly_by_id.get(rid),
            'failure': failure_by_id.get(rid),
        })

    active_routes = [r for r in route_items if r['active']]
    no_data_n = sum(
        1 for r in active_routes
        if r['today_min'] is None and r.get('source_issue') not in NORMAL_NO_DATA_ISSUES
    )
    normal_no_data_n = sum(
        1 for r in active_routes
        if r['today_min'] is None and r.get('source_issue') in NORMAL_NO_DATA_ISSUES
    )
    priority = priority_profile(price_events, anomalies, failures, no_data_n)

    return {
        'generated_at_utc': now_utc,
        'generated_at_taipei': f"{now_tpe} 台北時間",
        'scan_date': today_str,
        'next_scheduled_scan_taipei': next_scheduled_scan_taipei(),
        'total_routes': len(route_items),
        'active_routes': len(active_routes),
        'scanned_routes': len(analyses),
        'total_written_today': total_today,
        'priority': priority,
        'conclusion': priority['summary'],
        'routes': route_items,
        'price_events': price_events,
        'anomalies': anomalies,
        'failures': failures,
        'no_data_count': no_data_n,
        'normal_no_data_count': normal_no_data_n,
    }


def save_and_publish_status(payload):
    save_json(STATUS_JSON, payload)
    if not STATUS_WEBHOOK_URL:
        return
    try:
        resp = requests.post(
            STATUS_WEBHOOK_URL,
            json=payload,
            headers={'Authorization': f"Bearer {TOKEN}"},
            timeout=20,
        )
        resp.raise_for_status()
        log.info("狀態已同步到 Worker KV")
    except Exception as e:
        log.warning(f"狀態同步 Worker KV 失敗：{e}")


# ─────────── Telegram ───────────

def send_telegram(text, buttons=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'text': text}
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': buttons}
    sent = 0
    for chat_id in CHAT_IDS:
        try:
            resp = requests.post(url, json={'chat_id': chat_id, **payload}, timeout=20)
            resp.raise_for_status()
            log.info(f"Telegram 訊息已送出 → {chat_id}")
            sent += 1
        except Exception as e:
            log.error(f"Telegram 送出失敗 → {chat_id}: {e}")
    return sent


# ─────────── main ───────────

def main():
    if not ANALYSIS_JSON.exists():
        log.warning("找不到 analysis.json，跳過通知")
        return
    with open(ANALYSIS_JSON, 'r', encoding='utf-8') as f:
        analyses = json.load(f)
    routes = load_routes()
    conn = sqlite3.connect(DB_PATH)

    today_str = datetime.now(TAIPEI).date().isoformat()
    notified_state = load_json(NOTIFIED_STATE_JSON, {'routes': {}})
    price_events = collect_price_events(conn, analyses, routes, today_str, notified_state)
    anomalies = collect_anomaly_alerts(conn, analyses, routes, today_str, notified_state)
    state = load_scrape_state()
    failures = collect_failure_alerts(state, routes, notified_state)
    send_heartbeat = os.environ.get('SEND_HEARTBEAT') == '1'

    status_payload = build_status_payload(conn, analyses, routes, price_events, anomalies, failures, state)
    save_and_publish_status(status_payload)

    # 明顯降價、值得注意、連續失敗一律送；其他依心跳設定送摘要。
    must_send = bool(price_events or anomalies or failures or send_heartbeat)
    if not must_send:
        log.info("無新提醒、無異常、無失敗、未啟用心跳，不送訊息")
        conn.close()
        return

    msg = build_full_message(conn, analyses, routes, price_events, anomalies, failures, state)
    buttons = build_link_buttons(conn, analyses, routes, today_str)
    conn.close()
    if msg:
        sent = send_telegram(msg, buttons=buttons)
        if sent:
            update_notified_state(notified_state, price_events, anomalies, failures)
            save_json(NOTIFIED_STATE_JSON, notified_state)


if __name__ == '__main__':
    main()

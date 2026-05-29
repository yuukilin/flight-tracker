// Cloudflare Worker：Telegram bot 互動處理
// 接 Telegram webhook → 處理指令 → 改 GitHub routes.json / 觸發 workflow → 回訊息
// 支援：按鈕引導 /add、城市名→機場、欄位驗證、多 chat_id、summary 確認、美化 /show

const HELP_TEXT = `🤖 機票追蹤 Bot

【路線管理】
/list                       列出所有追蹤航線
/show <id>                  看某條航線詳細（含操作按鈕）
/add                        新增航線（按鈕引導，也可接一句話）
/edit <id> <field> <value>  修改某欄位
/clone <id>                 複製一條路線（可再 /edit 改）
/remove <id>                刪除航線
/pause <id>                 暫停（不刪、不通知）
/resume <id>                恢復
/threshold <id> <level>     改通知門檻（cheap / good / any）
/menu                       顯示主選單

【掃描 & 查詢】
/scan                       立即觸發排程
/history <id> [days]        過去 N 天每日最低（預設 30 天）
/best <id>                  歷史最低 5 筆
/chart <id> [days]          走勢圖 PNG（預設 30 天）
/debug <id>                 檢查上次抓取是否符合路線設定
/last <id>                  同 /debug

【其他】
/cancel                     取消當前 /add 對話
/help                       顯示這份說明

提示：
• 不想背指令就輸入 /menu
• 可直接打：我想明年10月到12月去札幌，豪經，9天，跨兩個週末
• /add 是快速新增：目的地、出發地、日期、天數、艙等、週末、通知
• 日期可以輸入 10/1-12/31、10月到12月、賞楓、寒假
• 抵達機場可輸入城市中文名（東京、大阪、首爾、曼谷…）自動轉成 IATA
• /edit 可改欄位：name / origin / destinations / cabin / dates / duration / weekends / max_price / max_stops / depart_time / return_time / threshold
  例：/edit 1 threshold any
  例：/edit 1 max_price 35000
  例：/edit 1 destinations NRT,HND`;

// ─── 機場字典（城市中文名 → 機場代碼列表）───
const AIRPORTS = {
  // 日本
  '東京': [['NRT', '成田'], ['HND', '羽田']],
  '大阪': [['KIX', '關西'], ['ITM', '伊丹']],
  '名古屋': [['NGO', '中部']],
  '福岡': [['FUK', '福岡']],
  '札幌': [['CTS', '新千歲']],
  '沖繩': [['OKA', '那霸']],
  '仙台': [['SDJ', '仙台']],
  '廣島': [['HIJ', '廣島']],
  '岡山': [['OKJ', '岡山']],
  '小松': [['KMQ', '小松']],
  // 韓國
  '首爾': [['ICN', '仁川'], ['GMP', '金浦']],
  '釜山': [['PUS', '金海']],
  '濟州': [['CJU', '濟州']],
  // 東南亞
  '曼谷': [['BKK', '蘇凡納布'], ['DMK', '廊曼']],
  '清邁': [['CNX', '清邁']],
  '普吉': [['HKT', '普吉']],
  '新加坡': [['SIN', '樟宜']],
  '吉隆坡': [['KUL', '吉隆坡']],
  '雅加達': [['CGK', '蘇加諾']],
  '峇里島': [['DPS', '伍拉萊']],
  '馬尼拉': [['MNL', '尼諾艾奎諾']],
  '宿霧': [['CEB', '宿霧']],
  '長灘島': [['KLO', '卡利博']],
  '胡志明': [['SGN', '新山一']],
  '河內': [['HAN', '內排']],
  '峴港': [['DAD', '峴港']],
  '芽莊': [['CXR', '金蘭']],
  '金邊': [['PNH', '金邊']],
  '暹粒': [['REP', '暹粒']],
  '仰光': [['RGN', '仰光']],
  // 港澳中
  '香港': [['HKG', '香港']],
  '澳門': [['MFM', '澳門']],
  '上海': [['PVG', '浦東'], ['SHA', '虹橋']],
  '北京': [['PEK', '首都'], ['PKX', '大興']],
  '廣州': [['CAN', '白雲']],
  '深圳': [['SZX', '寶安']],
  '杭州': [['HGH', '蕭山']],
  '成都': [['CTU', '雙流'], ['TFU', '天府']],
  '廈門': [['XMN', '高崎']],
  '青島': [['TAO', '膠東']],
  // 美加
  '洛杉磯': [['LAX', '洛杉磯']],
  '舊金山': [['SFO', '舊金山']],
  '紐約': [['JFK', '甘迺迪'], ['EWR', '紐華克'], ['LGA', '拉瓜地亞']],
  '西雅圖': [['SEA', '西塔科']],
  '芝加哥': [['ORD', '歐海爾']],
  '波士頓': [['BOS', '羅根']],
  '溫哥華': [['YVR', '溫哥華']],
  '多倫多': [['YYZ', '皮爾遜']],
  '夏威夷': [['HNL', '檀香山']],
  '達拉斯': [['DFW', '達福']],
  // 歐洲
  '倫敦': [['LHR', '希斯洛'], ['LGW', '蓋威克']],
  '巴黎': [['CDG', '戴高樂'], ['ORY', '奧利']],
  '法蘭克福': [['FRA', '法蘭克福']],
  '阿姆斯特丹': [['AMS', '史基浦']],
  '蘇黎世': [['ZRH', '蘇黎世']],
  '羅馬': [['FCO', '達文西']],
  '米蘭': [['MXP', '馬爾彭薩']],
  '巴塞隆納': [['BCN', '埃爾普拉特']],
  '馬德里': [['MAD', '巴拉哈斯']],
  '維也納': [['VIE', '施威夏']],
  '伊斯坦堡': [['IST', '伊斯坦堡']],
  '慕尼黑': [['MUC', '慕尼黑']],
  // 澳紐
  '雪梨': [['SYD', '京斯福德史密斯']],
  '墨爾本': [['MEL', '圖拉馬林']],
  '布里斯本': [['BNE', '布里斯本']],
  '奧克蘭': [['AKL', '奧克蘭']],
  '基督城': [['CHC', '基督城']],
  // 中東
  '杜拜': [['DXB', '杜拜']],
  '多哈': [['DOH', '哈馬德']],
  '阿布達比': [['AUH', '阿布達比']],
  // 台灣
  '台北': [['TPE', '桃園'], ['TSA', '松山']],
  '高雄': [['KHH', '小港']],
  '台中': [['RMQ', '清泉崗']],
  '台南': [['TNN', '台南']],
  '花蓮': [['HUN', '花蓮']],
};

// 艙等標籤對應
const CABIN_LABEL_TO_VAL = {
  '經濟艙': 'economy',
  '豪華經濟': 'premium_economy',
  '商務艙': 'business',
  '頭等艙': 'first',
};
const CABIN_VAL_TO_LABEL = Object.fromEntries(
  Object.entries(CABIN_LABEL_TO_VAL).map(([k, v]) => [v, k])
);

const CABIN_QUERY_LABEL = {
  economy: 'economy',
  premium_economy: 'premium economy',
  business: 'business class',
  first: 'first class',
};

// 通知門檻按鈕
const THRESHOLD_LABEL_TO_VAL = {
  '便宜才通知 cheap': 'cheap',
  '不錯就通知 good': 'good',
  '每次都通知 any': 'any',
};

const ADD_DEFAULTS = {
  active: true,
  max_price_twd: 0,
  depart_time_window: null,
  return_time_window: null,
  max_stops: 1,
};

const NATURAL_ADD_DEFAULTS = {
  origin: 'TPE',
  notify_threshold: 'cheap',
};

// 時段按鈕對應
const TIME_PRESET_TO_VAL = {
  '早班 06-12': '06:00-12:00',
  '午班 12-18': '12:00-18:00',
  '晚班 18-24': '18:00-23:59',
};

const TIME_EMPTY_RE = /^(skip|none|null|不限|不限制|不用限制)$/i;

// ─── base64 ↔ UTF-8 ───
function b64ToUtf8(b64) {
  const binary = atob(b64.replace(/\n/g, ''));
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return new TextDecoder('utf-8').decode(bytes);
}

function utf8ToB64(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

// ─── Telegram API ───
async function callTelegram(env, method, body) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function sendMsg(env, chatId, text, opts = {}) {
  return callTelegram(env, 'sendMessage', { chat_id: chatId, text, ...opts });
}

async function editMsg(env, chatId, messageId, text, opts = {}) {
  return callTelegram(env, 'editMessageText', {
    chat_id: chatId,
    message_id: messageId,
    text,
    ...opts,
  });
}

async function answerCallback(env, callbackId, text = '') {
  return callTelegram(env, 'answerCallbackQuery', {
    callback_query_id: callbackId,
    text,
    cache_time: 0,
  });
}

function kbOpts(rows, oneTime = true) {
  const keyboard = rows.map((row) =>
    (Array.isArray(row) ? row : [row]).map((cell) =>
      typeof cell === 'string' ? { text: cell } : cell
    )
  );
  return {
    reply_markup: {
      keyboard,
      one_time_keyboard: oneTime,
      resize_keyboard: true,
    },
  };
}

function inlineKbOpts(rows) {
  const inline_keyboard = rows.map((row) =>
    row.map((cell) => (typeof cell === 'string' ? { text: cell, callback_data: cell } : cell))
  );
  return { reply_markup: { inline_keyboard } };
}

function removeKbOpts() {
  return { reply_markup: { remove_keyboard: true } };
}

function mainMenuOpts() {
  return kbOpts([
    ['➕ 新增路線', '📋 我的路線'],
    ['🔍 立即掃描', '❓ 說明'],
    ['🏆 歷史最低', '📈 價格走勢'],
  ], false);
}

const BOT_COMMANDS = [
  { command: 'menu', description: '顯示主選單' },
  { command: 'add', description: '新增追蹤路線' },
  { command: 'list', description: '查看所有路線' },
  { command: 'show', description: '查看單一路線' },
  { command: 'scan', description: '立即掃描票價' },
  { command: 'history', description: '查看過去每日最低' },
  { command: 'best', description: '查看歷史最低' },
  { command: 'chart', description: '取得價格走勢圖' },
  { command: 'debug', description: '檢查上次抓取資料' },
  { command: 'help', description: '查看說明' },
];

async function setupTelegramMenu(env) {
  await callTelegram(env, 'setMyCommands', { commands: BOT_COMMANDS });
  await callTelegram(env, 'setChatMenuButton', { menu_button: { type: 'commands' } });
}

// ─── GitHub API ───
const GH_HEADERS = (env) => ({
  Authorization: `Bearer ${env.GITHUB_TOKEN}`,
  'User-Agent': 'flight-bot',
  Accept: 'application/vnd.github+json',
});

async function readFile(env, path) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${path}`;
  const r = await fetch(url, { headers: GH_HEADERS(env) });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub read fail: ${r.status} ${await r.text()}`);
  const data = await r.json();
  return { content: b64ToUtf8(data.content), sha: data.sha };
}

async function writeFile(env, path, newContent, sha, message) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${path}`;
  const body = { message, content: utf8ToB64(newContent) };
  if (sha) body.sha = sha;
  const r = await fetch(url, {
    method: 'PUT',
    headers: { ...GH_HEADERS(env), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`GitHub write fail: ${r.status} ${await r.text()}`);
}

async function triggerWorkflow(env, workflowFile = 'scrape.yml', inputs = undefined) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;
  const body = { ref: 'main' };
  if (inputs) body.inputs = inputs;
  const r = await fetch(url, {
    method: 'POST',
    headers: { ...GH_HEADERS(env), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Workflow trigger fail: ${r.status} ${await r.text()}`);
}

async function loadRoutes(env) {
  const file = await readFile(env, 'routes.json');
  if (!file) return { data: { routes: [], next_id: 1 }, sha: null };
  return { data: JSON.parse(file.content), sha: file.sha };
}

async function saveRoutes(env, data, sha, message) {
  await writeFile(env, 'routes.json', JSON.stringify(data, null, 2), sha, message);
}

// ─── 驗證 ───
function isIata(s) {
  return /^[A-Z]{3}$/.test(s);
}
function isDate(s) {
  return /^\d{4}-\d{2}-\d{2}$/.test(s) && !isNaN(Date.parse(s));
}
function isTimePart(s, allowEndOfDay = false) {
  const m = String(s).match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return false;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  if (allowEndOfDay && hh === 24 && mm === 0) return true;
  return hh >= 0 && hh <= 23 && mm >= 0 && mm <= 59;
}

function isTimeWindow(s) {
  const parts = String(s).split('-').map((x) => x.trim());
  if (parts.length !== 2) return false;
  return isTimePart(parts[0]) && isTimePart(parts[1], true);
}

function normalizeDays(n, fallback = 30) {
  if (!Number.isFinite(n)) return fallback;
  return Math.min(365, Math.max(1, n));
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function taipeiTodayParts() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const get = (type) => Number(parts.find((p) => p.type === type)?.value);
  return { year: get('year'), month: get('month'), day: get('day') };
}

function taipeiTodayIso() {
  const t = taipeiTodayParts();
  return formatYmd(t.year, t.month, t.day);
}

function formatYmd(year, month, day) {
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function dateKey(dateStr) {
  return Number(String(dateStr).replace(/-/g, ''));
}

function addMonthsToToday(months) {
  const t = taipeiTodayParts();
  const d = new Date(Date.UTC(t.year, t.month - 1, t.day));
  d.setUTCMonth(d.getUTCMonth() + months);
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
  };
}

function inferYearForMonth(month, forceNextYear = false) {
  const t = taipeiTodayParts();
  if (forceNextYear) return t.year + 1;
  return month < t.month ? t.year + 1 : t.year;
}

function presetDateRange(text) {
  const t = text.replace(/\s+/g, '');
  const today = taipeiTodayParts();
  const todayStr = formatYmd(today.year, today.month, today.day);
  if (t.includes('未來3個月')) {
    const end = addMonthsToToday(3);
    return { start: todayStr, end: formatYmd(end.year, end.month, end.day), label: '未來 3 個月' };
  }
  if (t.includes('未來半年') || t.includes('未來6個月')) {
    const end = addMonthsToToday(6);
    return { start: todayStr, end: formatYmd(end.year, end.month, end.day), label: '未來半年' };
  }
  if (t.includes('暑假')) {
    const y = today.month > 8 ? today.year + 1 : today.year;
    return { start: `${y}-07-01`, end: `${y}-08-31`, label: `${y} 暑假` };
  }
  if (t.includes('寒假')) {
    const y = today.month <= 2 ? today.year : today.year + 1;
    return { start: `${y}-01-01`, end: `${y}-02-${daysInMonth(y, 2)}`, label: `${y} 寒假` };
  }
  if (t.includes('賞楓') || t.includes('楓葉') || t.includes('秋天')) {
    const y = today.month > 12 ? today.year + 1 : today.year;
    return { start: `${y}-10-01`, end: `${y}-12-31`, label: `${y} 賞楓季` };
  }
  if (t.includes('櫻花')) {
    const y = today.month > 4 ? today.year + 1 : today.year;
    return { start: `${y}-03-15`, end: `${y}-04-15`, label: `${y} 櫻花季` };
  }
  if (t.includes('跨年')) {
    const y = today.month === 12 && today.day > 20 ? today.year + 1 : today.year;
    return { start: `${y}-12-20`, end: `${y + 1}-01-05`, label: `${y} 跨年` };
  }
  return null;
}

function parseFlexibleDateRange(raw) {
  const preset = presetDateRange(raw);
  if (preset) return preset;

  let text = raw.trim()
    .replace(/[～〜－–—]/g, '-')
    .replace(/\s+/g, '')
    .replace(/（.*?）|\(.*?\)/g, '');
  const forceNextYear = text.includes('明年');
  text = text.replace(/明年/g, '').replace(/今年/g, '');

  let m = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[,，~到至-]+(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
  if (m) {
    const start = formatYmd(Number(m[1]), Number(m[2]), Number(m[3]));
    const end = formatYmd(Number(m[4]), Number(m[5]), Number(m[6]));
    if (dateKey(start) > dateKey(end)) throw new Error('開始日期需早於結束日期');
    return { start, end, label: `${start} 到 ${end}` };
  }

  m = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[,，~到至]+(\d{1,2})[-/](\d{1,2})$/);
  if (m) {
    const y = Number(m[1]);
    const start = formatYmd(y, Number(m[2]), Number(m[3]));
    let end = formatYmd(y, Number(m[4]), Number(m[5]));
    if (dateKey(start) > dateKey(end)) end = formatYmd(y + 1, Number(m[4]), Number(m[5]));
    return { start, end, label: `${start} 到 ${end}` };
  }

  m = text.match(/^(\d{1,2})\/(\d{1,2})[-~到至](\d{1,2})\/(\d{1,2})$/);
  if (m) {
    const startMonth = Number(m[1]);
    const y = inferYearForMonth(startMonth, forceNextYear);
    const start = formatYmd(y, startMonth, Number(m[2]));
    let end = formatYmd(y, Number(m[3]), Number(m[4]));
    if (dateKey(start) > dateKey(end)) end = formatYmd(y + 1, Number(m[3]), Number(m[4]));
    return { start, end, label: `${start} 到 ${end}` };
  }

  m = text.match(/^(\d{1,2})月(\d{1,2})?日?[-~到至](\d{1,2})月(\d{1,2})?日?$/);
  if (m) {
    const startMonth = Number(m[1]);
    const startDay = Number(m[2] || 1);
    const endMonth = Number(m[3]);
    const y = inferYearForMonth(startMonth, forceNextYear);
    const start = formatYmd(y, startMonth, startDay);
    let end = formatYmd(y, endMonth, Number(m[4] || daysInMonth(y, endMonth)));
    if (dateKey(start) > dateKey(end)) end = formatYmd(y + 1, endMonth, Number(m[4] || daysInMonth(y + 1, endMonth)));
    return { start, end, label: `${start} 到 ${end}` };
  }

  m = text.match(/^(\d{1,2})[-~到至](\d{1,2})月$/);
  if (m) {
    const startMonth = Number(m[1]);
    const endMonth = Number(m[2]);
    const y = inferYearForMonth(startMonth, forceNextYear);
    const start = formatYmd(y, startMonth, 1);
    let end = formatYmd(y, endMonth, daysInMonth(y, endMonth));
    if (dateKey(start) > dateKey(end)) end = formatYmd(y + 1, endMonth, daysInMonth(y + 1, endMonth));
    return { start, end, label: `${start} 到 ${end}` };
  }

  throw new Error('日期看不懂。可輸入：10/1-12/31、10月到12月、明年10月到12月、賞楓、寒假');
}

// 解析「東京 大阪 NRT」→ { codes:[NRT, HND, KIX, ITM, NRT], labels:[...] }
function parseDestinations(text) {
  const tokens = text.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
  if (tokens.length === 0) throw new Error('沒有解析到任何機場');
  const codes = [];
  const labels = [];
  for (const tk of tokens) {
    const up = tk.toUpperCase();
    if (isIata(up)) {
      codes.push(up);
      labels.push(up);
    } else if (AIRPORTS[tk]) {
      for (const [code, name] of AIRPORTS[tk]) {
        codes.push(code);
        labels.push(`${code}(${tk}${name})`);
      }
    } else {
      throw new Error(`無法解析「${tk}」，請輸入城市中文名（例如東京、大阪）或 IATA 三碼（例如 NRT）`);
    }
  }
  const uniq = [...new Set(codes)];
  return { codes: uniq, labels };
}

function parseOriginCode(text) {
  const raw = String(text || '').trim();
  const m = raw.match(/^([A-Za-z]{3})/);
  if (m) return m[1].toUpperCase();
  const parsed = parseDestinations(raw);
  return parsed.codes[0];
}

function parseCabinClasses(text) {
  const aliases = {
    '經濟': 'economy',
    '經濟艙': 'economy',
    '豪經': 'premium_economy',
    '豪華經濟': 'premium_economy',
    '豪華經濟艙': 'premium_economy',
    '商務': 'business',
    '商務艙': 'business',
    '頭等': 'first',
    '頭等艙': 'first',
  };
  const tokens = text.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
  return tokens.map((t) => aliases[t] || CABIN_LABEL_TO_VAL[t] || t.toLowerCase());
}

function destinationLabel(codes) {
  const list = codes || [];
  for (const code of list) {
    for (const [city, airports] of Object.entries(AIRPORTS)) {
      if (airports.some(([airportCode]) => airportCode === code)) return city;
    }
  }
  return list.join('/');
}

function cabinShortLabel(cabins) {
  const labels = {
    economy: '經濟',
    premium_economy: '豪經',
    business: '商務',
    first: '頭等',
  };
  return (cabins || []).map((c) => labels[c] || c).join('/');
}

function googleFlightsUrl(route) {
  const origin = route.origin || '';
  const dest = (route.destinations || [''])[0];
  const cabin = CABIN_QUERY_LABEL[(route.cabin_classes || ['economy'])[0]] || 'economy';
  const start = route.depart_date_range?.start || '';
  const end = route.depart_date_range?.end || '';
  const query = `Flights from ${origin} to ${dest} ${start} ${end} ${cabin}`;
  return `https://www.google.com/travel/flights?q=${encodeURIComponent(query)}`;
}

function buildAddRoute(id, data) {
  const route = {
    id,
    created_at: new Date().toISOString().slice(0, 10),
    ...ADD_DEFAULTS,
    ...data,
  };
  if (!route.name) {
    const dest = destinationLabel(route.destinations);
    const cabin = cabinShortLabel(route.cabin_classes);
    route.name = `${dest}${cabin ? cabin : ''} ${route.trip_duration_days || '?'} 天`;
  }
  return route;
}

// ─── /add 流程定義 ───
const ADD_STEPS = [
  {
    key: 'destinations',
    prompt: `想追哪個目的地？

可直接點按鈕，也可以輸入城市名或機場代碼。
例：東京、札幌、NRT、NRT HND`,
    keyboard: [
      ['東京', '大阪', '札幌'],
      ['沖繩', '福岡', '首爾'],
      ['曼谷', '香港', '新加坡'],
    ],
    parse: (v) => parseDestinations(v),
    validate: (v) => (v.codes.length > 0 ? null : '至少需 1 個目的地'),
    postParse: (v) => v.codes,
    confirmText: (v) => `目的地：${v.labels.join(' / ')}`,
  },
  {
    key: 'origin',
    prompt: '從哪裡出發？',
    keyboard: [
      ['TPE 桃園', 'TSA 松山'],
      ['KHH 高雄', 'RMQ 台中'],
    ],
    parse: (v) => {
      const m = v.match(/^([A-Za-z]{3})/);
      return m ? m[1].toUpperCase() : v.trim().toUpperCase();
    },
    validate: (v) => (isIata(v) ? null : '須為 3 字母 IATA 代碼'),
  },
  {
    key: 'depart_date_range',
    prompt: `想追哪段出發日期？

可以點按鈕，也可以直接打：
10/1-12/31
10月到12月
明年10月到12月
賞楓 / 寒假 / 暑假`,
    keyboard: [
      ['未來 3 個月', '未來半年'],
      ['暑假', '寒假'],
      ['賞楓', '跨年'],
    ],
    parse: (v) => parseFlexibleDateRange(v),
    validate: (v) => {
      if (!v.start || !v.end) return '請給一段日期';
      if (!isDate(v.start) || !isDate(v.end)) return '日期格式不正確';
      if (v.start > v.end) return '開始日期需早於結束日期';
      const today = taipeiTodayIso();
      if (v.end < today) return '這段日期已經過了，請選未來日期';
      if (v.start < today) return `開始日期已經過了，請從 ${today} 之後開始`;
      return null;
    },
    confirmText: (v) => `日期：${v.label || `${v.start} 到 ${v.end}`}`,
    postParse: (v) => ({ start: v.start, end: v.end }),
  },
  {
    key: 'trip_duration_days',
    prompt: '大概要玩幾天？',
    keyboard: [['3', '5', '7'], ['9', '10', '14']],
    parse: (v) => parseFirstInteger(v),
    validate: (v) => (Number.isFinite(v) && v >= 1 && v <= 90 ? null : '須為 1-90 整數'),
  },
  {
    key: 'cabin_classes',
    prompt: '想看什麼艙等？',
    keyboard: [
      ['經濟艙', '豪華經濟'],
      ['商務艙', '頭等艙'],
    ],
    parse: (v) => {
      return parseCabinClasses(v);
    },
    validate: (v) => {
      const valid = ['economy', 'premium_economy', 'business', 'first'];
      const bad = v.filter((c) => !valid.includes(c));
      return bad.length === 0
        ? null
        : `不認識的艙等：${bad.join(',')}（可選經濟艙、豪華經濟、商務艙、頭等艙）`;
    },
  },
  {
    key: 'must_contain_full_weekends',
    prompt: '需不需要跨完整週末？',
    keyboard: [['不用限制週末'], ['至少 1 個週末'], ['至少 2 個週末']],
    parse: (v) => {
      const n = parseFirstInteger(v);
      return Number.isFinite(n) ? n : 0;
    },
    validate: (v) => (v >= 0 && v <= 5 ? null : '須為 0-5'),
  },
  {
    key: 'notify_threshold',
    prompt: '什麼時候通知你？',
    keyboard: [
      ['便宜才通知 cheap'],
      ['不錯就通知 good'],
      ['每次都通知 any'],
    ],
    parse: (v) => THRESHOLD_LABEL_TO_VAL[v] || v.trim().toLowerCase(),
    validate: (v) => (['cheap', 'good', 'any'].includes(v) ? null : '須為 cheap / good / any'),
  },
];

const ADD_CONFIRM_KEYBOARD = [['✅ 確認新增', '✏️ 修改'], ['❌ 取消']];

const ADD_DRAFT_EDIT_CHOICES = [
  { label: '改目的地', key: 'destinations' },
  { label: '改出發地', key: 'origin' },
  { label: '改日期', key: 'depart_date_range' },
  { label: '改天數', key: 'trip_duration_days' },
  { label: '改艙等', key: 'cabin_classes' },
  { label: '改週末', key: 'must_contain_full_weekends' },
  { label: '改通知', key: 'notify_threshold' },
];

const ADD_FIELD_LABELS = {
  destinations: '目的地',
  origin: '出發地',
  depart_date_range: '日期',
  trip_duration_days: '天數',
  cabin_classes: '艙等',
  must_contain_full_weekends: '週末',
  notify_threshold: '通知標準',
};

function normalizeUserText(text) {
  return String(text || '')
    .replace(/[０-９Ａ-Ｚａ-ｚ]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/／/g, '/')
    .replace(/[－–—～〜]/g, '-')
    .trim();
}

function compactUserText(text) {
  return normalizeUserText(text).replace(/\s+/g, '');
}

function parseChineseInteger(text) {
  const s = String(text || '').trim();
  if (/^\d+$/.test(s)) return Number(s);
  const digits = {
    零: 0,
    一: 1,
    二: 2,
    兩: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
  };
  if (Object.prototype.hasOwnProperty.call(digits, s)) return digits[s];
  if (s === '十') return 10;
  const ten = s.match(/^([一二兩三四五六七八九])?十([一二三四五六七八九])?$/);
  if (ten) {
    const tens = ten[1] ? digits[ten[1]] : 1;
    const ones = ten[2] ? digits[ten[2]] : 0;
    return tens * 10 + ones;
  }
  return NaN;
}

function parseFirstInteger(text) {
  const digit = String(text || '').match(/\d+/);
  if (digit) return parseInt(digit[0]);
  const word = String(text || '').match(/[一二兩三四五六七八九十]+/);
  return word ? parseChineseInteger(word[0]) : NaN;
}

function findAirportMentions(text) {
  const normalized = normalizeUserText(text);
  const mentions = [];
  const cities = Object.keys(AIRPORTS).sort((a, b) => b.length - a.length);
  for (const city of cities) {
    let start = 0;
    while (start < normalized.length) {
      const idx = normalized.indexOf(city, start);
      if (idx < 0) break;
      const airports = AIRPORTS[city];
      mentions.push({
        type: 'city',
        value: city,
        index: idx,
        length: city.length,
        codes: airports.map(([code]) => code),
        originCode: airports[0][0],
      });
      start = idx + city.length;
    }
  }

  const iataRe = /(^|[^A-Za-z])([A-Za-z]{3})(?=$|[^A-Za-z])/g;
  let m;
  while ((m = iataRe.exec(normalized)) !== null) {
    const index = m.index + m[1].length;
    const code = m[2].toUpperCase();
    mentions.push({
      type: 'iata',
      value: code,
      index,
      length: 3,
      codes: [code],
      originCode: code,
    });
  }

  mentions.sort((a, b) => (a.index - b.index) || (b.length - a.length));
  const filtered = [];
  for (const mention of mentions) {
    const overlaps = filtered.some((x) =>
      mention.index < x.index + x.length && x.index < mention.index + mention.length
    );
    if (!overlaps) filtered.push(mention);
  }
  return filtered;
}

function hasDirectionalWordBefore(text, mention) {
  const before = text.slice(Math.max(0, mention.index - 5), mention.index);
  return /(去|到|飛往|飛)$/.test(before) || /(去|到|飛往|飛)/.test(before.slice(-3));
}

function extractAirportsFromSentence(text) {
  const normalized = normalizeUserText(text);
  const mentions = findAirportMentions(normalized);
  if (mentions.length === 0) return {};

  let originMention = null;
  let destMention = null;
  for (const mention of mentions) {
    const before = normalized.slice(Math.max(0, mention.index - 5), mention.index);
    const after = normalized.slice(mention.index + mention.length, mention.index + mention.length + 5);
    if (/從|自/.test(before) || /出發/.test(after)) originMention = mention;
    if (hasDirectionalWordBefore(normalized, mention)) destMention = mention;
  }

  if (!destMention && mentions.length >= 2) {
    for (let i = 0; i < mentions.length - 1; i += 1) {
      const current = mentions[i];
      const next = mentions[i + 1];
      const between = normalized.slice(current.index + current.length, next.index);
      if (/(到|去|飛往|飛|出發)/.test(between)) {
        if (!originMention) originMention = current;
        destMention = next;
        break;
      }
    }
  }

  if (!destMention) {
    destMention = mentions.find((mention) => mention !== originMention) || mentions[0];
  }

  const out = {};
  if (originMention && originMention !== destMention) out.origin = originMention.originCode;
  if (destMention) out.destinations = [...new Set(destMention.codes)];
  return out;
}

function extractDateRangeFromSentence(text) {
  const normalized = normalizeUserText(text);
  try {
    return parseFlexibleDateRange(normalized);
  } catch {
    // 繼續從整句裡找日期片段。
  }

  const compact = compactUserText(text);
  const patterns = [
    /(?:明年|今年)?\d{4}[-/]\d{1,2}[-/]\d{1,2}[-,，~到至]+(?:\d{4}[-/])?\d{1,2}[-/]\d{1,2}/,
    /(?:明年|今年)?\d{1,2}\/\d{1,2}[-~到至]\d{1,2}\/\d{1,2}/,
    /(?:明年|今年)?\d{1,2}月(?:\d{1,2}日?)?[-~到至]\d{1,2}月(?:\d{1,2}日?)?/,
    /(?:明年|今年)?\d{1,2}[-~到至]\d{1,2}月/,
  ];
  for (const pattern of patterns) {
    const m = compact.match(pattern);
    if (!m) continue;
    try {
      return parseFlexibleDateRange(m[0]);
    } catch {
      // 試下一個片段。
    }
  }
  return null;
}

function extractDurationDays(text) {
  const compact = compactUserText(text);
  let m = compact.match(/(?:玩|去|待|住|旅行|旅遊)?(\d{1,2})天/);
  if (m) return Number(m[1]);
  m = compact.match(/(?:玩|去|待|住|旅行|旅遊)?([一二兩三四五六七八九十]{1,3})天/);
  if (!m) return undefined;
  const n = parseChineseInteger(m[1]);
  return Number.isFinite(n) ? n : undefined;
}

function extractWeekendCount(text) {
  const compact = compactUserText(text);
  if (/(不用|不需要|無需|不要).{0,4}[週周]末/.test(compact)) return 0;
  const m = compact.match(/(?:跨|含|包含|至少)?([0-5一二兩三四五])個?(?:完整)?[週周]末/);
  if (m) {
    const n = parseChineseInteger(m[1]);
    return Number.isFinite(n) ? n : undefined;
  }
  if (/(跨|含|包含).{0,2}[週周]末/.test(compact)) return 1;
  return undefined;
}

function extractCabinClassesFromSentence(text) {
  let compact = compactUserText(text).toLowerCase();
  const aliases = [
    ['豪華經濟艙', 'premium_economy'],
    ['豪華經濟', 'premium_economy'],
    ['豪經', 'premium_economy'],
    ['經濟艙', 'economy'],
    ['經濟', 'economy'],
    ['商務艙', 'business'],
    ['商務', 'business'],
    ['頭等艙', 'first'],
    ['頭等', 'first'],
    ['premiumeconomy', 'premium_economy'],
    ['business', 'business'],
    ['economy', 'economy'],
    ['first', 'first'],
  ];
  const cabins = [];
  for (const [label, value] of aliases) {
    if (!compact.includes(label)) continue;
    cabins.push(value);
    compact = compact.split(label).join(' ');
  }
  return [...new Set(cabins)];
}

function extractNotifyThreshold(text) {
  const compact = compactUserText(text).toLowerCase();
  if (/(每次都通知|每次通知|全部通知|any)/.test(compact)) return 'any';
  if (/(不錯|還行|good)/.test(compact)) return 'good';
  if (/(便宜|cheap)/.test(compact)) return 'cheap';
  return null;
}

function extractMaxStops(text) {
  const compact = compactUserText(text);
  if (/(直飛|不轉機|不要轉機)/.test(compact)) return 0;
  const m = compact.match(/(?:最多|可)?轉(?:機)?([0-3一二兩三])次/);
  if (!m) return undefined;
  const n = parseChineseInteger(m[1]);
  return Number.isFinite(n) ? n : undefined;
}

function extractMaxPriceTwd(text) {
  const compact = compactUserText(text).replace(/,/g, '');
  let m = compact.match(/(?:預算|上限|低於|不超過|不要超過|小於).{0,8}?(\d{1,3})萬(\d)?/);
  if (m) return Number(m[1]) * 10000 + Number(m[2] || 0) * 1000;
  m = compact.match(/(?:預算|上限|低於|不超過|不要超過|小於).{0,8}?(?:NT\$|TWD|台幣|新台幣)?(\d{4,6})/i);
  if (m) return Number(m[1]);
  return undefined;
}

function hasAddValue(data, key) {
  const value = data?.[key];
  if (value === undefined || value === null || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (key === 'depart_date_range') return Boolean(value.start && value.end);
  return true;
}

function validateStoredAddValue(key, value) {
  if (key === 'destinations') return Array.isArray(value) && value.length > 0 ? null : '至少需 1 個目的地';
  const step = addStepByKey(key);
  if (!step?.validate) return null;
  return step.validate(value);
}

function nextMissingAddStep(data, startIdx = 0) {
  for (let i = startIdx; i < ADD_STEPS.length; i += 1) {
    if (!hasAddValue(data, ADD_STEPS[i].key)) return i;
  }
  return -1;
}

function addStepByKey(key) {
  return ADD_STEPS.find((step) => step.key === key);
}

function addStepIndexByKey(key) {
  return ADD_STEPS.findIndex((step) => step.key === key);
}

function parseAddStepInput(step, text) {
  const value = step.parse ? step.parse(text) : text;
  if (step.validate) {
    const err = step.validate(value);
    if (err) throw new Error(err);
  }
  const extraNote = step.confirmText ? `✓ ${step.confirmText(value)}\n\n` : '';
  const stored = step.postParse ? step.postParse(value) : value;
  return { stored, extraNote };
}

function addConfirmOpts() {
  return kbOpts(ADD_CONFIRM_KEYBOARD);
}

function addDraftEditOpts() {
  const rows = ADD_DRAFT_EDIT_CHOICES.map((choice) => [choice.label]);
  rows.push(['✅ 回到確認'], ['❌ 取消']);
  return kbOpts(rows);
}

function formatNaturalAddNotes(notes) {
  if (!notes || notes.length === 0) return '';
  return `\n\n我先補上的預設：\n${notes.map((note) => `• ${note}`).join('\n')}`;
}

function formatAddDraftPartial(data) {
  const lines = [];
  if (hasAddValue(data, 'origin')) lines.push(`出發：${data.origin}`);
  if (hasAddValue(data, 'destinations')) lines.push(`抵達：${data.destinations.join(', ')}`);
  if (hasAddValue(data, 'depart_date_range')) lines.push(`日期：${data.depart_date_range.start} ~ ${data.depart_date_range.end}`);
  if (hasAddValue(data, 'trip_duration_days')) lines.push(`天數：${data.trip_duration_days} 天`);
  if (hasAddValue(data, 'cabin_classes')) {
    const cabins = data.cabin_classes.map((c) => CABIN_VAL_TO_LABEL[c] || c).join(', ');
    lines.push(`艙等：${cabins}`);
  }
  if (hasAddValue(data, 'must_contain_full_weekends')) {
    lines.push(`週末：需含 ${data.must_contain_full_weekends} 個完整週末`);
  }
  if (data.max_stops !== undefined) lines.push(`轉機：最多 ${data.max_stops} 次`);
  if (data.max_price_twd !== undefined) lines.push(`價上限：${data.max_price_twd ? 'NT$ ' + data.max_price_twd.toLocaleString() : '不限'}`);
  if (hasAddValue(data, 'notify_threshold')) lines.push(`通知：${data.notify_threshold}`);
  return lines.length > 0 ? lines.join('\n') : '目前還沒抓到明確欄位';
}

function parseNaturalAddSentence(raw, force = false) {
  const text = normalizeUserText(raw);
  const compact = compactUserText(raw);
  const data = {};
  const notes = [];
  const parsedSignals = [];

  const airports = extractAirportsFromSentence(text);
  if (airports.destinations?.length) {
    data.destinations = airports.destinations;
    parsedSignals.push('destinations');
  }
  if (airports.origin) {
    data.origin = airports.origin;
  } else if (data.destinations) {
    data.origin = NATURAL_ADD_DEFAULTS.origin;
    notes.push('出發地未說，先用台北桃園 TPE');
  }

  const dateRange = extractDateRangeFromSentence(text);
  if (dateRange) {
    data.depart_date_range = { start: dateRange.start, end: dateRange.end };
    parsedSignals.push('depart_date_range');
  }

  const duration = extractDurationDays(text);
  if (duration !== undefined) {
    data.trip_duration_days = duration;
    parsedSignals.push('trip_duration_days');
  }

  const cabins = extractCabinClassesFromSentence(text);
  if (cabins.length > 0) {
    data.cabin_classes = cabins;
    parsedSignals.push('cabin_classes');
  }

  const weekends = extractWeekendCount(text);
  if (weekends !== undefined) {
    data.must_contain_full_weekends = weekends;
    parsedSignals.push('must_contain_full_weekends');
  }

  const threshold = extractNotifyThreshold(text);
  if (threshold) {
    data.notify_threshold = threshold;
  } else if (parsedSignals.length >= 2) {
    data.notify_threshold = NATURAL_ADD_DEFAULTS.notify_threshold;
    notes.push('通知標準先用 cheap，也就是便宜才通知');
  }

  const maxStops = extractMaxStops(text);
  if (maxStops !== undefined) data.max_stops = maxStops;

  const maxPrice = extractMaxPriceTwd(text);
  if (maxPrice !== undefined) data.max_price_twd = maxPrice;

  for (const step of ADD_STEPS) {
    if (!hasAddValue(data, step.key)) continue;
    const err = validateStoredAddValue(step.key, data[step.key]);
    if (!err) continue;
    delete data[step.key];
    notes.push(`${ADD_FIELD_LABELS[step.key] || step.key}需要再補一次：${err}`);
  }

  const hasIntent = /(想去|想飛|我要去|我要飛|追蹤|新增|機票|航班|旅行|旅遊|去|飛)/.test(compact);
  const hasEnoughSignals = parsedSignals.length >= 2 || (hasIntent && parsedSignals.length >= 1) || (force && parsedSignals.length >= 1);
  if (!hasEnoughSignals) return { ok: false };

  const missing = ADD_STEPS
    .filter((step) => !hasAddValue(data, step.key))
    .map((step) => step.key);
  return { ok: true, data, missing, notes };
}

function parseRouteDateRangeEdit(text) {
  const raw = String(text || '').trim();
  const exact = raw.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
  if (exact.length === 2 && isDate(exact[0]) && isDate(exact[1])) {
    return { start: exact[0], end: exact[1], label: `${exact[0]} 到 ${exact[1]}` };
  }
  return parseFlexibleDateRange(raw);
}

function parseBudgetTwd(text) {
  const compact = compactUserText(text).replace(/,/g, '');
  if (/^(不限|不限制|無上限|0)$/.test(compact)) return 0;
  const fromSentence = extractMaxPriceTwd(`預算${compact}`);
  if (fromSentence !== undefined) return fromSentence;
  const m = compact.match(/\d+/);
  return m ? parseInt(m[0]) : 0;
}

function formatRouteSummary(r) {
  const cabins = (r.cabin_classes || []).map((c) => CABIN_VAL_TO_LABEL[c] || c).join(', ');
  const dests = (r.destinations || []).join(', ');
  const dep = r.depart_time_window || '不限';
  const ret = r.return_time_window || '不限';
  const status = r.active === false ? '⏸️ 暫停' : '🟢 啟用';
  const weekendCount = r.must_contain_full_weekends ?? '?';
  return [
    `${status} #${r.id ?? '?'} ${r.name}`,
    `   出發：${r.origin}`,
    `   抵達：${dests}`,
    `   艙等：${cabins}`,
    `   日期：${r.depart_date_range?.start ?? '?'} ~ ${r.depart_date_range?.end ?? '?'}`,
    `   天數：${r.trip_duration_days} 天，需含 ${weekendCount} 個完整週末`,
    `   時段：去 ${dep} / 回 ${ret}`,
    `   轉機：最多 ${r.max_stops ?? 0} 次`,
    `   價上限：${r.max_price_twd ? 'NT$ ' + r.max_price_twd.toLocaleString() : '不限'}`,
    `   通知：${r.notify_threshold}`,
  ].join('\n');
}

// ─── 授權 ───
function isAuthorized(env, chatId) {
  const allowed = String(env.AUTHORIZED_CHAT_ID || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return allowed.includes(String(chatId));
}

// ─── 主入口 ───
export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('OK');
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response('Bad request', { status: 400 });
    }

    if (update.callback_query) {
      await handleCallback(env, update.callback_query);
      return new Response('OK');
    }

    const msg = update.message;
    if (!msg || !msg.text) return new Response('OK');
    const chatId = String(msg.chat.id);

    if (!isAuthorized(env, chatId)) {
      await sendMsg(env, chatId, '❌ 無權限');
      return new Response('OK');
    }

    const text = msg.text.trim();
    try {
      const state = await env.STATE.get(`dlg:${chatId}`, 'json');
      if (state && state.flow) {
        await handleFlow(env, chatId, text, state);
      } else if (text.startsWith('/')) {
        await handleCommand(env, chatId, text);
      } else {
        await handleMenuText(env, chatId, text);
      }
    } catch (e) {
      await sendMsg(env, chatId, `❌ 錯誤：${e.message}`, removeKbOpts());
    }
    return new Response('OK');
  },
};

// ─── 指令分派 ───
async function handleCommand(env, chatId, text) {
  const [cmd, ...args] = text.split(/\s+/);
  switch (cmd) {
    case '/help':
    case '/start':
      await setupTelegramMenu(env).catch(() => {});
      return sendMsg(env, chatId, HELP_TEXT, mainMenuOpts());
    case '/menu':
      return cmdMenu(env, chatId);
    case '/list':
      return cmdList(env, chatId);
    case '/show':
      return cmdShow(env, chatId, parseInt(args[0]));
    case '/add':
      if (args.length > 0) return cmdAddFromSentence(env, chatId, args.join(' '), true);
      return cmdAddStart(env, chatId);
    case '/edit':
      return cmdEdit(env, chatId, parseInt(args[0]), args[1], args.slice(2).join(' '));
    case '/clone':
      return cmdClone(env, chatId, parseInt(args[0]));
    case '/remove':
      return cmdRemove(env, chatId, parseInt(args[0]));
    case '/pause':
      return cmdToggleActive(env, chatId, parseInt(args[0]), false);
    case '/resume':
      return cmdToggleActive(env, chatId, parseInt(args[0]), true);
    case '/threshold':
      return cmdThreshold(env, chatId, parseInt(args[0]), args[1]);
    case '/scan':
      return cmdScan(env, chatId);
    case '/history':
      return cmdQuery(env, chatId, 'history', parseInt(args[0]), normalizeDays(parseInt(args[1]) || 30));
    case '/best':
      return cmdQuery(env, chatId, 'best', parseInt(args[0]), 30);
    case '/chart':
      return cmdQuery(env, chatId, 'chart', parseInt(args[0]), normalizeDays(parseInt(args[1]) || 30));
    case '/debug':
    case '/last':
      return cmdQuery(env, chatId, 'debug', parseInt(args[0]), 30);
    case '/cancel':
      await env.STATE.delete(`dlg:${chatId}`);
      return sendMsg(env, chatId, '已取消', removeKbOpts());
    default:
      return sendMsg(env, chatId, `未知指令：${cmd}。/help 看說明`);
  }
}

// ─── /threshold ───
async function cmdThreshold(env, chatId, id, level) {
  if (isNaN(id) || !level) {
    return sendMsg(env, chatId, '用法：/threshold <id> <cheap|good|any>');
  }
  const lvl = level.toLowerCase();
  if (!['cheap', 'good', 'any'].includes(lvl)) {
    return sendMsg(env, chatId, '門檻須為 cheap / good / any');
  }
  const { data, sha } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  const old = r.notify_threshold;
  r.notify_threshold = lvl;
  await saveRoutes(env, data, sha, `Set threshold #${id} ${old}→${lvl}`);
  await sendMsg(env, chatId, `✅ #${id} 通知門檻：${old} → ${lvl}`, removeKbOpts());
}

// ─── /clone ───
async function cmdClone(env, chatId, id) {
  if (isNaN(id)) return sendMsg(env, chatId, '用法：/clone <id>');
  const { data, sha } = await loadRoutes(env);
  const src = data.routes.find((x) => x.id === id);
  if (!src) return sendMsg(env, chatId, `找不到 #${id}`);
  const newId = data.next_id || (Math.max(0, ...data.routes.map((r) => r.id)) + 1);
  const cloned = {
    ...JSON.parse(JSON.stringify(src)),
    id: newId,
    active: true,
    name: `${src.name} (複製)`,
    created_at: new Date().toISOString().slice(0, 10),
  };
  data.routes.push(cloned);
  data.next_id = newId + 1;
  await saveRoutes(env, data, sha, `Clone route #${id} → #${newId}`);
  await sendMsg(
    env,
    chatId,
    `✅ 已複製 #${id} → #${newId}：${cloned.name}\n用 /edit ${newId} <field> <value> 修改欄位`,
    removeKbOpts()
  );
}

// ─── /edit ───
const EDIT_FIELD_HANDLERS = {
  name: { apply: (r, v) => { r.name = v; }, validate: (v) => (v.length > 0 && v.length <= 50 ? null : '名稱需 1-50 字') },
  origin: {
    apply: (r, v) => { r.origin = parseOriginCode(v); },
    validate: (v) => {
      try {
        return isIata(parseOriginCode(v)) ? null : '須為 3 字母 IATA 代碼';
      } catch (e) {
        return e.message;
      }
    },
  },
  destinations: {
    apply: (r, v) => {
      const p = parseDestinations(v);
      r.destinations = p.codes;
    },
    validate: () => null,
  },
  cabin: {
    apply: (r, v) => {
      const tokens = v.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
      r.cabin_classes = tokens.map((t) => CABIN_LABEL_TO_VAL[t] || t.toLowerCase());
    },
    validate: (v) => {
      const tokens = v.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
      const vals = tokens.map((t) => CABIN_LABEL_TO_VAL[t] || t.toLowerCase());
      const valid = ['economy', 'premium_economy', 'business', 'first'];
      const bad = vals.filter((c) => !valid.includes(c));
      return bad.length === 0 ? null : `不認識的艙等：${bad.join(',')}`;
    },
  },
  dates: {
    apply: (r, v) => {
      const parsed = parseRouteDateRangeEdit(v);
      r.depart_date_range = { start: parsed.start, end: parsed.end };
    },
    validate: (v) => {
      let parsed;
      try {
        parsed = parseRouteDateRangeEdit(v);
      } catch (e) {
        return e.message;
      }
      if (!parsed.start || !parsed.end) return '請給一段日期';
      if (!isDate(parsed.start) || !isDate(parsed.end)) return '日期格式不正確';
      if (parsed.start > parsed.end) return '開始日期需早於結束日期';
      const today = taipeiTodayIso();
      if (parsed.end < today) return '這段日期已經過了，請選未來日期';
      if (parsed.start < today) return `開始日期已經過了，請從 ${today} 之後開始`;
      return null;
    },
  },
  duration: {
    apply: (r, v) => { r.trip_duration_days = parseFirstInteger(v); },
    validate: (v) => {
      const n = parseFirstInteger(v);
      return n >= 1 && n <= 90 ? null : '須為 1-90 整數';
    },
  },
  weekends: {
    apply: (r, v) => {
      const n = parseFirstInteger(v);
      r.must_contain_full_weekends = Number.isFinite(n) ? n : 0;
    },
    validate: (v) => {
      const n = parseFirstInteger(v);
      const out = Number.isFinite(n) ? n : 0;
      return out >= 0 && out <= 5 ? null : '須為 0-5';
    },
  },
  max_price: {
    apply: (r, v) => {
      r.max_price_twd = parseBudgetTwd(v);
    },
    validate: () => null,
  },
  max_stops: {
    apply: (r, v) => {
      const n = parseFirstInteger(v);
      r.max_stops = Number.isFinite(n) ? n : 0;
    },
    validate: (v) => {
      const n = parseFirstInteger(v);
      const out = Number.isFinite(n) ? n : 0;
      return out >= 0 && out <= 3 ? null : '須為 0-3';
    },
  },
  depart_time: {
    apply: (r, v) => {
      if (TIME_EMPTY_RE.test(v)) {
        r.depart_time_window = null;
      } else {
        r.depart_time_window = TIME_PRESET_TO_VAL[v] || v.trim();
      }
    },
    validate: (v) => {
      if (TIME_EMPTY_RE.test(v)) return null;
      const out = TIME_PRESET_TO_VAL[v] || v.trim();
      return isTimeWindow(out) ? null : '格式須為 HH:MM-HH:MM 或 skip';
    },
  },
  return_time: {
    apply: (r, v) => {
      if (TIME_EMPTY_RE.test(v)) {
        r.return_time_window = null;
      } else {
        r.return_time_window = TIME_PRESET_TO_VAL[v] || v.trim();
      }
    },
    validate: (v) => {
      if (TIME_EMPTY_RE.test(v)) return null;
      const out = TIME_PRESET_TO_VAL[v] || v.trim();
      return isTimeWindow(out) ? null : '格式須為 HH:MM-HH:MM 或 skip';
    },
  },
  threshold: {
    apply: (r, v) => { r.notify_threshold = v.toLowerCase(); },
    validate: (v) => (['cheap', 'good', 'any'].includes(v.toLowerCase()) ? null : '須為 cheap / good / any'),
  },
};

const EDIT_ROUTE_FIELDS = [
  {
    field: 'destinations',
    label: '改目的地',
    prompt: `新的目的地？

可以輸入城市名或機場代碼。
例：札幌、東京、NRT HND`,
    keyboard: [
      ['東京', '大阪', '札幌'],
      ['沖繩', '福岡', '首爾'],
      ['曼谷', '香港', '新加坡'],
    ],
  },
  {
    field: 'origin',
    label: '改出發地',
    prompt: '新的出發地？可輸入台北、高雄、TPE、KHH。',
    keyboard: [
      ['台北', 'TPE 桃園', 'TSA 松山'],
      ['高雄', 'KHH 高雄', 'RMQ 台中'],
    ],
  },
  {
    field: 'dates',
    label: '改日期',
    prompt: `新的出發日期範圍？

可輸入：
10/1-12/31
10月到12月
明年10月到12月
賞楓 / 寒假 / 暑假 / 跨年`,
    keyboard: [
      ['未來 3 個月', '未來半年'],
      ['暑假', '寒假'],
      ['賞楓', '跨年'],
    ],
  },
  {
    field: 'duration',
    label: '改天數',
    prompt: '新的旅遊天數？例：9、九天、14 天。',
    keyboard: [['3', '5', '7'], ['9', '10', '14']],
  },
  {
    field: 'cabin',
    label: '改艙等',
    prompt: '新的艙等？',
    keyboard: [
      ['經濟艙', '豪華經濟'],
      ['商務艙', '頭等艙'],
    ],
  },
  {
    field: 'weekends',
    label: '改週末',
    prompt: '需要含幾個完整週末？',
    keyboard: [['不用限制週末'], ['至少 1 個週末'], ['至少 2 個週末']],
  },
  {
    field: 'max_stops',
    label: '改轉機',
    prompt: '最多可轉機幾次？',
    keyboard: [['直飛'], ['最多 1 次'], ['最多 2 次']],
  },
  {
    field: 'max_price',
    label: '改預算',
    prompt: '票價上限？可輸入「不限」、35000、3萬5。',
    keyboard: [['不限'], ['30000', '40000'], ['50000', '60000']],
  },
  {
    field: 'depart_time',
    label: '改去程時段',
    prompt: '去程出發時段？可選預設或輸入 HH:MM-HH:MM。',
    keyboard: [['不限'], ['早班 06-12'], ['午班 12-18'], ['晚班 18-24']],
  },
  {
    field: 'return_time',
    label: '改回程時段',
    prompt: '回程出發時段？可選預設或輸入 HH:MM-HH:MM。',
    keyboard: [['不限'], ['早班 06-12'], ['午班 12-18'], ['晚班 18-24']],
  },
  {
    field: 'threshold',
    label: '改通知',
    prompt: '什麼情況通知？',
    keyboard: [
      ['便宜才通知 cheap'],
      ['不錯就通知 good'],
      ['每次都通知 any'],
    ],
  },
  {
    field: 'name',
    label: '改名稱',
    prompt: '新的路線名稱？例：北海道豪經 9 天。',
    keyboard: null,
  },
];

const EDIT_FIELD_LABELS = Object.fromEntries(
  EDIT_ROUTE_FIELDS.map((item) => [item.field, item.label.replace(/^改/, '')])
);

function findEditRouteField(field) {
  return EDIT_ROUTE_FIELDS.find((item) => item.field === field);
}

function applyRouteEditValue(route, field, value) {
  const handler = EDIT_FIELD_HANDLERS[field];
  if (!handler) throw new Error(`不認識的欄位：${field}`);
  const err = handler.validate(value);
  if (err) throw new Error(err);
  handler.apply(route, value);
}

async function cmdEdit(env, chatId, id, field, value) {
  if (isNaN(id) || !field || !value) {
    const list = Object.keys(EDIT_FIELD_HANDLERS).join(' / ');
    return sendMsg(env, chatId, `用法：/edit <id> <field> <value>\n\n支援欄位：${list}\n例：/edit 1 threshold any`);
  }
  const handler = EDIT_FIELD_HANDLERS[field];
  if (!handler) {
    const list = Object.keys(EDIT_FIELD_HANDLERS).join(' / ');
    return sendMsg(env, chatId, `不認識的欄位：${field}\n支援：${list}`);
  }
  const { data, sha } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  try {
    applyRouteEditValue(r, field, value);
  } catch (e) {
    return sendMsg(env, chatId, `❌ ${e.message}`);
  }
  await saveRoutes(env, data, sha, `Edit route #${id} ${field}`);
  await sendMsg(env, chatId, `✅ #${id} ${field} 已更新\n\n${formatRouteSummary(r)}`, removeKbOpts());
}

// ─── /history /best /chart 觸發 query.yml ───
async function cmdQuery(env, chatId, action, id, days) {
  if (isNaN(id)) return sendMsg(env, chatId, `用法：/${action} <id> [days]`);
  const { data } = await loadRoutes(env);
  const route = data.routes.find((x) => x.id === id);
  if (!route) return sendMsg(env, chatId, `找不到 #${id}`);
  await triggerWorkflow(env, 'query.yml', {
    action,
    route_id: String(id),
    chat_id: String(chatId),
    days: String(days),
  });
  const note = action === 'best'
    ? `✅ 已觸發歷史最低查詢 #${id}，約 1-2 分鐘後回傳`
    : action === 'debug'
      ? `✅ 已觸發抓取診斷 #${id}，約 1-2 分鐘後回傳`
      : `✅ 已觸發 ${action} 查詢 #${id}（${days} 天），約 1-2 分鐘後回傳`;
  await sendMsg(env, chatId, note, removeKbOpts());
}

async function cmdMenu(env, chatId) {
  await setupTelegramMenu(env).catch(() => {});
  return sendMsg(
    env,
    chatId,
    '你想做什麼？\n\n常用功能都在下面，直接點就好。',
    mainMenuOpts()
  );
}

async function handleMenuText(env, chatId, text) {
  const t = text.replace(/\s+/g, '');
  const naturalAddHandled = await cmdAddFromSentence(env, chatId, text, false);
  if (naturalAddHandled) return;
  if (t.includes('新增')) return cmdAddStart(env, chatId);
  if (t.includes('我的路線') || t.includes('路線')) return cmdList(env, chatId);
  if (t.includes('掃描')) return cmdScan(env, chatId);
  if (t.includes('說明') || t.includes('help')) return sendMsg(env, chatId, HELP_TEXT, mainMenuOpts());
  if (t.includes('歷史最低') || t.includes('最低')) return sendRoutePicker(env, chatId, 'best');
  if (t.includes('價格走勢') || t.includes('走勢') || t.includes('圖')) return sendRoutePicker(env, chatId, 'chart');
  return sendMsg(env, chatId, '我看不懂這句。你可以點下面按鈕，或輸入 /help。', mainMenuOpts());
}

function routeButtons(route) {
  const id = route.id;
  const activeButton = route.active === false
    ? { text: '恢復追蹤', callback_data: `resume:${id}` }
    : { text: '暫停追蹤', callback_data: `pause:${id}` };
  return inlineKbOpts([
    [
      { text: '開 Google Flights', url: googleFlightsUrl(route) },
    ],
    [
      { text: '每日最低', callback_data: `history:${id}` },
      { text: '走勢圖', callback_data: `chart:${id}` },
    ],
    [
      { text: '歷史最低', callback_data: `best:${id}` },
      { text: '立即掃描', callback_data: 'scan' },
    ],
    [
      { text: '檢查抓取', callback_data: `debug:${id}` },
      { text: '修改設定', callback_data: `edit_route:${id}` },
    ],
    [
      activeButton,
      { text: '改通知', callback_data: `threshold:${id}` },
    ],
    [
      { text: '複製路線', callback_data: `clone:${id}` },
      { text: '刪除', callback_data: `remove:${id}` },
    ],
  ]);
}

function routeEditButtons(route) {
  const id = route.id;
  return inlineKbOpts([
    [
      { text: '改目的地', callback_data: `edit_field:${id}:destinations` },
      { text: '改日期', callback_data: `edit_field:${id}:dates` },
    ],
    [
      { text: '改天數', callback_data: `edit_field:${id}:duration` },
      { text: '改艙等', callback_data: `edit_field:${id}:cabin` },
    ],
    [
      { text: '改週末', callback_data: `edit_field:${id}:weekends` },
      { text: '改轉機', callback_data: `edit_field:${id}:max_stops` },
    ],
    [
      { text: '改預算', callback_data: `edit_field:${id}:max_price` },
      { text: '改通知', callback_data: `edit_field:${id}:threshold` },
    ],
    [
      { text: '改出發地', callback_data: `edit_field:${id}:origin` },
      { text: '改名稱', callback_data: `edit_field:${id}:name` },
    ],
    [
      { text: '改去程時段', callback_data: `edit_field:${id}:depart_time` },
      { text: '改回程時段', callback_data: `edit_field:${id}:return_time` },
    ],
    [
      { text: '回路線面板', callback_data: `route:${id}` },
    ],
  ]);
}

async function sendRoutePicker(env, chatId, action) {
  const { data } = await loadRoutes(env);
  if (data.routes.length === 0) {
    return sendMsg(env, chatId, '目前沒有路線。先點「➕ 新增路線」。', mainMenuOpts());
  }
  const title = {
    show: '要看哪一條路線？',
    history: '要查哪一條路線的每日最低？',
    best: '要查哪一條路線的歷史最低？',
    chart: '要看哪一條路線的走勢圖？',
  }[action] || '要選哪一條路線？';
  const rows = data.routes.map((r) => [
    {
      text: `#${r.id} ${r.name}`,
      callback_data: `${action}:${r.id}`,
    },
  ]);
  return sendMsg(env, chatId, title, inlineKbOpts(rows));
}

function formatEditCurrentValue(route, field) {
  if (field === 'destinations') return (route.destinations || []).join(', ') || '未設定';
  if (field === 'origin') return route.origin || '未設定';
  if (field === 'dates') {
    return `${route.depart_date_range?.start || '?'} ~ ${route.depart_date_range?.end || '?'}`;
  }
  if (field === 'duration') return `${route.trip_duration_days || '?'} 天`;
  if (field === 'cabin') {
    return (route.cabin_classes || []).map((c) => CABIN_VAL_TO_LABEL[c] || c).join(', ') || '未設定';
  }
  if (field === 'weekends') return `${route.must_contain_full_weekends || 0} 個完整週末`;
  if (field === 'max_stops') return `最多 ${route.max_stops ?? 0} 次`;
  if (field === 'max_price') return route.max_price_twd ? `NT$ ${route.max_price_twd.toLocaleString()}` : '不限';
  if (field === 'depart_time') return route.depart_time_window || '不限';
  if (field === 'return_time') return route.return_time_window || '不限';
  if (field === 'threshold') return route.notify_threshold || '未設定';
  if (field === 'name') return route.name || '未設定';
  return '未設定';
}

function routeEditFieldOpts(choice) {
  if (!choice?.keyboard) return kbOpts([['取消修改']]);
  return kbOpts([...choice.keyboard, ['取消修改']]);
}

async function cmdRouteEditMenu(env, chatId, id, messageId = null) {
  if (isNaN(id)) return sendMsg(env, chatId, '找不到這條路線');
  const { data } = await loadRoutes(env);
  const route = data.routes.find((x) => x.id === id);
  if (!route) return sendMsg(env, chatId, `找不到 #${id}`);
  const text = `要修改 #${id} 哪個設定？\n\n${formatRouteSummary(route)}`;
  if (messageId) {
    const edited = await editMsg(env, chatId, messageId, text, routeEditButtons(route));
    if (edited?.ok) return;
  }
  return sendMsg(env, chatId, text, routeEditButtons(route));
}

async function startRouteEditField(env, chatId, id, field) {
  const choice = findEditRouteField(field);
  if (!choice) return sendMsg(env, chatId, '找不到這個設定項目，請重新打開路線面板。');
  const { data } = await loadRoutes(env);
  const route = data.routes.find((x) => x.id === id);
  if (!route) return sendMsg(env, chatId, `找不到 #${id}`);
  await env.STATE.put(
    `dlg:${chatId}`,
    JSON.stringify({ flow: 'edit_route_field', routeId: id, field }),
    { expirationTtl: 1800 }
  );
  return sendMsg(
    env,
    chatId,
    `目前${choice.label.replace(/^改/, '')}：${formatEditCurrentValue(route, field)}\n\n${choice.prompt}\n\n輸入 /cancel 或點「取消修改」可取消。`,
    routeEditFieldOpts(choice)
  );
}

async function handleCallback(env, cb) {
  const data = cb.data || '';
  const chatId = String(cb.message?.chat?.id || cb.from?.id || '');
  const messageId = cb.message?.message_id;
  if (!isAuthorized(env, chatId)) {
    await answerCallback(env, cb.id, '無權限');
    return;
  }
  await answerCallback(env, cb.id);

  const [action, rawId, rawValue] = data.split(':');
  const id = parseInt(rawId);

  if (action === 'scan') return cmdScan(env, chatId);
  if (action === 'history') return cmdQuery(env, chatId, 'history', id, 30);
  if (action === 'best') return cmdQuery(env, chatId, 'best', id, 30);
  if (action === 'chart') return cmdQuery(env, chatId, 'chart', id, 30);
  if (action === 'debug') return cmdQuery(env, chatId, 'debug', id, 30);
  if (action === 'pause') return cmdToggleActive(env, chatId, id, false);
  if (action === 'resume') return cmdToggleActive(env, chatId, id, true);
  if (action === 'clone') return cmdClone(env, chatId, id);
  if (action === 'edit_route') return cmdRouteEditMenu(env, chatId, id, messageId);
  if (action === 'edit_field') return startRouteEditField(env, chatId, id, rawValue);
  if (action === 'threshold') {
    return sendMsg(
      env,
      chatId,
      `#${id} 要什麼情況通知？`,
      inlineKbOpts([
        [{ text: '便宜才通知', callback_data: `set_threshold:${id}:cheap` }],
        [{ text: '不錯就通知', callback_data: `set_threshold:${id}:good` }],
        [{ text: '每次都通知', callback_data: `set_threshold:${id}:any` }],
      ])
    );
  }
  if (action === 'set_threshold') return cmdThreshold(env, chatId, id, rawValue);
  if (action === 'remove') {
    return sendMsg(
      env,
      chatId,
      `確定要刪除 #${id} 嗎？`,
      inlineKbOpts([
        [
          { text: '確定刪除', callback_data: `remove_yes:${id}` },
          { text: '取消', callback_data: `route:${id}` },
        ],
      ])
    );
  }
  if (action === 'remove_yes') return cmdRemove(env, chatId, id);
  if (action === 'route' || action === 'show') {
    const { data: routesData } = await loadRoutes(env);
    const route = routesData.routes.find((x) => x.id === id);
    if (!route) return sendMsg(env, chatId, `找不到 #${id}`);
    const text = `📍 路線詳細\n\n${formatRouteSummary(route)}`;
    if (messageId) {
      const edited = await editMsg(env, chatId, messageId, text, routeButtons(route));
      if (edited?.ok) return;
    }
    return sendMsg(env, chatId, text, routeButtons(route));
  }
  return sendMsg(env, chatId, '這個按鈕已過期，請重新輸入 /menu。', mainMenuOpts());
}

async function cmdList(env, chatId) {
  const { data } = await loadRoutes(env);
  if (data.routes.length === 0) {
    return sendMsg(env, chatId, '尚無追蹤航線。點「➕ 新增路線」開始。', mainMenuOpts());
  }
  const lines = ['📋 追蹤中的航線：', ''];
  for (const r of data.routes) {
    const status = r.active === false ? '⏸️' : '🟢';
    const cabins = (r.cabin_classes || []).join(',');
    const dests = (r.destinations || []).join('/');
    lines.push(`${status} #${r.id} ${r.name}`);
    lines.push(`   ${r.origin}→${dests}（${cabins}）`);
    lines.push(`   ${r.depart_date_range?.start}~${r.depart_date_range?.end} 共 ${r.trip_duration_days} 天`);
    lines.push(`   通知門檻：${r.notify_threshold}`);
    lines.push('');
  }
  lines.push('點下面路線可以直接操作。');
  const buttons = data.routes.map((r) => [
    { text: `操作 #${r.id} ${r.name}`, callback_data: `route:${r.id}` },
    { text: '查票', url: googleFlightsUrl(r) },
  ]);
  await sendMsg(env, chatId, lines.join('\n'), inlineKbOpts(buttons));
}

async function cmdShow(env, chatId, id) {
  if (isNaN(id)) return sendMsg(env, chatId, '用法：/show <id>');
  const { data } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  const text = `📍 路線詳細\n\n${formatRouteSummary(r)}`;
  await sendMsg(env, chatId, text, routeButtons(r));
}

async function cmdRemove(env, chatId, id) {
  if (isNaN(id)) return sendMsg(env, chatId, '用法：/remove <id>');
  const { data, sha } = await loadRoutes(env);
  const idx = data.routes.findIndex((x) => x.id === id);
  if (idx < 0) return sendMsg(env, chatId, `找不到 #${id}`);
  const removed = data.routes.splice(idx, 1)[0];
  await saveRoutes(env, data, sha, `Remove route #${id} via bot`);
  await sendMsg(env, chatId, `✅ 已刪除 #${id}：${removed.name}`, removeKbOpts());
}

async function cmdToggleActive(env, chatId, id, active) {
  if (isNaN(id)) return sendMsg(env, chatId, `用法：/${active ? 'resume' : 'pause'} <id>`);
  const { data, sha } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  r.active = active;
  await saveRoutes(env, data, sha, `${active ? 'Resume' : 'Pause'} route #${id}`);
  await sendMsg(env, chatId, `✅ #${id} ${active ? '已恢復' : '已暫停'}`, removeKbOpts());
}

async function cmdScan(env, chatId) {
  await triggerWorkflow(env, 'scrape.yml');
  await sendMsg(env, chatId, '✅ 已觸發排程，約 1-3 分鐘後通知', removeKbOpts());
}

// ─── /add 多輪 ───
async function cmdAddFromSentence(env, chatId, text, force) {
  const parsed = parseNaturalAddSentence(text, force);
  if (!parsed.ok) {
    if (force) {
      await sendMsg(env, chatId, '我還看不出足夠的路線資訊，改用逐步新增。');
      await cmdAddStart(env, chatId);
      return true;
    }
    return false;
  }

  const state = {
    flow: 'add_confirm',
    data: parsed.data,
    source: 'natural_add',
  };
  const missingIdx = nextMissingAddStep(state.data, 0);
  if (missingIdx < 0) {
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    const intro = `我理解成這樣：${formatNaturalAddNotes(parsed.notes)}`;
    await sendAddConfirmPreview(env, chatId, state, intro);
    return true;
  }

  state.flow = 'add';
  state.step = missingIdx;
  await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
  const step = ADD_STEPS[missingIdx];
  const preview = formatAddDraftPartial(state.data);
  const note = [
    '我先抓到這些：',
    '',
    preview,
    formatNaturalAddNotes(parsed.notes),
    '',
    `還需要補「${ADD_FIELD_LABELS[step.key] || step.key}」。`,
    '',
  ].join('\n');
  await promptStep(env, chatId, missingIdx, note);
  return true;
}

async function cmdAddStart(env, chatId) {
  await env.STATE.put(
    `dlg:${chatId}`,
    JSON.stringify({ flow: 'add', step: 0, data: {} }),
    { expirationTtl: 1800 }
  );
  const step = ADD_STEPS[0];
  const opts = step.keyboard ? kbOpts(step.keyboard) : removeKbOpts();
  await sendMsg(
    env,
    chatId,
    `開始快速新增路線。\n只要回答幾個旅行問題就好，進階設定之後還能再改。\n\n任何時候輸入 /cancel 取消。\n\n${step.prompt}`,
    opts
  );
}

async function promptStep(env, chatId, idx, extraNote = '') {
  const step = ADD_STEPS[idx];
  const opts = step.keyboard ? kbOpts(step.keyboard) : removeKbOpts();
  await sendMsg(env, chatId, extraNote + step.prompt, opts);
}

async function sendAddConfirmPreview(env, chatId, state, intro) {
  const preview = formatRouteSummary(buildAddRoute('?', state.data));
  await sendMsg(
    env,
    chatId,
    `${intro}\n\n${preview}\n\n我已先用推薦設定：不限預算、去回時段不限、最多轉 1 次。\n按下方按鈕確認、修改或取消。`,
    addConfirmOpts()
  );
}

async function handleFlow(env, chatId, text, state) {
  if (state.flow === 'add') return handleAddFlow(env, chatId, text, state);
  if (state.flow === 'add_confirm') return handleAddConfirm(env, chatId, text, state);
  if (state.flow === 'add_draft_edit') return handleAddDraftEdit(env, chatId, text, state);
  if (state.flow === 'add_draft_field') return handleAddDraftField(env, chatId, text, state);
  if (state.flow === 'edit_route_field') return handleRouteEditField(env, chatId, text, state);
  await env.STATE.delete(`dlg:${chatId}`);
  await sendMsg(env, chatId, '未知對話狀態，已清除', removeKbOpts());
}

async function handleAddFlow(env, chatId, text, state) {
  // 對話內也允許 /cancel
  if (text === '/cancel') {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '已取消', removeKbOpts());
  }

  const step = ADD_STEPS[state.step];
  let parsed;
  try {
    parsed = parseAddStepInput(step, text);
  } catch (e) {
    return sendMsg(
      env,
      chatId,
      `❌ ${e.message}\n請重新輸入：`,
      step.keyboard ? kbOpts(step.keyboard) : removeKbOpts()
    );
  }

  // postParse：把存進 state.data 的形式縮減
  state.data = state.data || {};
  state.data[step.key] = parsed.stored;
  const nextStep = nextMissingAddStep(state.data, state.step + 1);

  if (nextStep < 0) {
    state.flow = 'add_confirm';
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    await sendAddConfirmPreview(env, chatId, state, `${parsed.extraNote}📋 請確認新增：`);
    return;
  }

  state.step = nextStep;
  await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
  await promptStep(env, chatId, state.step, parsed.extraNote);
}

async function handleAddConfirm(env, chatId, text, state) {
  if (/^\/cancel$|取消|cancel|❌/i.test(text)) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '已取消，未新增任何路線', removeKbOpts());
  }
  if (/修改|edit|✏️/i.test(text)) {
    state.flow = 'add_draft_edit';
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    return sendMsg(env, chatId, '要修改哪一項？', addDraftEditOpts());
  }
  if (!/確認|✅|confirm/i.test(text)) {
    return sendMsg(
      env,
      chatId,
      '請點「✅ 確認新增」、「✏️ 修改」或「❌ 取消」',
      addConfirmOpts()
    );
  }
  const { data, sha } = await loadRoutes(env);
  const newId = data.next_id || (Math.max(0, ...data.routes.map((r) => r.id)) + 1);
  const route = buildAddRoute(newId, state.data);
  data.routes.push(route);
  data.next_id = newId + 1;
  await saveRoutes(env, data, sha, `Add route #${newId} via bot`);
  await env.STATE.delete(`dlg:${chatId}`);
  await sendMsg(
    env,
    chatId,
    `✅ 已新增 #${newId}：${route.name}\n下次排程會自動追蹤，或輸入 /scan 立即抓`,
    removeKbOpts()
  );
}

async function handleAddDraftEdit(env, chatId, text, state) {
  if (/^\/cancel$|取消|cancel|❌/i.test(text)) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '已取消，未新增任何路線', removeKbOpts());
  }
  if (/確認|回到確認|✅/i.test(text)) {
    state.flow = 'add_confirm';
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    return sendAddConfirmPreview(env, chatId, state, '📋 請確認新增：');
  }
  const compact = compactUserText(text);
  const choice = ADD_DRAFT_EDIT_CHOICES.find((item) => compact.includes(item.label.replace(/^改/, '')));
  if (!choice) {
    return sendMsg(env, chatId, '請選一個要修改的項目，或點「✅ 回到確認」。', addDraftEditOpts());
  }
  state.flow = 'add_draft_field';
  state.editKey = choice.key;
  await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
  return promptStep(env, chatId, addStepIndexByKey(choice.key), `請輸入新的「${ADD_FIELD_LABELS[choice.key] || choice.key}」。\n\n`);
}

async function handleAddDraftField(env, chatId, text, state) {
  if (/^\/cancel$|取消|cancel|❌/i.test(text)) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '已取消，未新增任何路線', removeKbOpts());
  }
  const step = addStepByKey(state.editKey);
  if (!step) {
    state.flow = 'add_draft_edit';
    delete state.editKey;
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    return sendMsg(env, chatId, '找不到要修改的欄位，請重新選一次。', addDraftEditOpts());
  }

  let parsed;
  try {
    parsed = parseAddStepInput(step, text);
  } catch (e) {
    return sendMsg(
      env,
      chatId,
      `❌ ${e.message}\n請重新輸入：`,
      step.keyboard ? kbOpts(step.keyboard) : removeKbOpts()
    );
  }

  state.data = state.data || {};
  state.data[step.key] = parsed.stored;
  delete state.editKey;
  state.flow = 'add_confirm';
  await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
  return sendAddConfirmPreview(env, chatId, state, `${parsed.extraNote}📋 已更新，請再確認：`);
}

async function handleRouteEditField(env, chatId, text, state) {
  const id = Number(state.routeId);
  if (/^\/cancel$|取消|cancel|取消修改|❌/i.test(text)) {
    await env.STATE.delete(`dlg:${chatId}`);
    await sendMsg(env, chatId, '已取消修改', removeKbOpts());
    if (!isNaN(id)) return cmdRouteEditMenu(env, chatId, id);
    return;
  }

  const choice = findEditRouteField(state.field);
  if (!choice) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '找不到這個設定項目，已取消修改。', removeKbOpts());
  }

  const { data, sha } = await loadRoutes(env);
  const route = data.routes.find((x) => x.id === id);
  if (!route) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, `找不到 #${id}`, removeKbOpts());
  }

  try {
    applyRouteEditValue(route, state.field, text);
  } catch (e) {
    return sendMsg(
      env,
      chatId,
      `❌ ${e.message}\n請重新輸入「${choice.label.replace(/^改/, '')}」：`,
      routeEditFieldOpts(choice)
    );
  }

  await saveRoutes(env, data, sha, `Edit route #${id} ${state.field} via bot buttons`);
  await env.STATE.delete(`dlg:${chatId}`);
  await sendMsg(env, chatId, `✅ #${id} ${choice.label.replace(/^改/, '')}已更新`, removeKbOpts());
  return sendMsg(env, chatId, `📍 路線詳細\n\n${formatRouteSummary(route)}`, routeButtons(route));
}

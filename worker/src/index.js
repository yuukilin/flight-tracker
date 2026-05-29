// Cloudflare Worker：Telegram bot 互動處理
// 接 Telegram webhook → 處理指令 → 改 GitHub routes.json / 觸發 workflow → 回訊息
// 支援：按鈕引導 /add、城市名→機場、欄位驗證、多 chat_id、summary 確認、美化 /show

const HELP_TEXT = `🤖 機票追蹤 Bot

【路線管理】
/list                       列出所有追蹤航線
/show <id>                  看某條航線詳細（含操作按鈕）
/add                        新增航線（按鈕引導）
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

【其他】
/cancel                     取消當前 /add 對話
/help                       顯示這份說明

提示：
• 不想背指令就輸入 /menu
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

// 時段按鈕對應
const TIME_PRESET_TO_VAL = {
  '早班 06-12': '06:00-12:00',
  '午班 12-18': '12:00-18:00',
  '晚班 18-24': '18:00-23:59',
};

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
    parse: (v) => parseInt(v),
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
      const m = v.match(/\d+/);
      return m ? parseInt(m[0]) : 0;
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

function formatRouteSummary(r) {
  const cabins = (r.cabin_classes || []).map((c) => CABIN_VAL_TO_LABEL[c] || c).join(', ');
  const dests = (r.destinations || []).join(', ');
  const dep = r.depart_time_window || '不限';
  const ret = r.return_time_window || '不限';
  const status = r.active === false ? '⏸️ 暫停' : '🟢 啟用';
  return [
    `${status} #${r.id ?? '?'} ${r.name}`,
    `   出發：${r.origin}`,
    `   抵達：${dests}`,
    `   艙等：${cabins}`,
    `   日期：${r.depart_date_range?.start ?? '?'} ~ ${r.depart_date_range?.end ?? '?'}`,
    `   天數：${r.trip_duration_days} 天，需含 ${r.must_contain_full_weekends || 0} 個完整週末`,
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
    apply: (r, v) => { r.origin = v.toUpperCase(); },
    validate: (v) => (isIata(v.toUpperCase()) ? null : '須為 3 字母 IATA 代碼'),
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
      const [s, e] = v.split(/[,，]/).map((x) => x.trim());
      r.depart_date_range = { start: s, end: e };
    },
    validate: (v) => {
      const [s, e] = v.split(/[,，]/).map((x) => x.trim());
      if (!s || !e) return '格式：YYYY-MM-DD,YYYY-MM-DD';
      if (!isDate(s) || !isDate(e)) return '日期格式須為 YYYY-MM-DD';
      if (s > e) return '開始日期需早於結束日期';
      return null;
    },
  },
  duration: {
    apply: (r, v) => { r.trip_duration_days = parseInt(v); },
    validate: (v) => {
      const n = parseInt(v);
      return n >= 1 && n <= 90 ? null : '須為 1-90 整數';
    },
  },
  weekends: {
    apply: (r, v) => { r.must_contain_full_weekends = parseInt(v) || 0; },
    validate: (v) => {
      const n = parseInt(v) || 0;
      return n >= 0 && n <= 5 ? null : '須為 0-5';
    },
  },
  max_price: {
    apply: (r, v) => {
      const m = v.match(/\d+/);
      r.max_price_twd = m ? parseInt(m[0]) : 0;
    },
    validate: () => null,
  },
  max_stops: {
    apply: (r, v) => {
      const m = v.match(/\d+/);
      r.max_stops = m ? parseInt(m[0]) : 0;
    },
    validate: (v) => {
      const m = v.match(/\d+/);
      const n = m ? parseInt(m[0]) : 0;
      return n >= 0 && n <= 3 ? null : '須為 0-3';
    },
  },
  depart_time: {
    apply: (r, v) => {
      if (/^skip|none|null$/i.test(v)) {
        r.depart_time_window = null;
      } else {
        r.depart_time_window = TIME_PRESET_TO_VAL[v] || v.trim();
      }
    },
    validate: (v) => {
      if (/^skip|none|null$/i.test(v)) return null;
      const out = TIME_PRESET_TO_VAL[v] || v.trim();
      return isTimeWindow(out) ? null : '格式須為 HH:MM-HH:MM 或 skip';
    },
  },
  return_time: {
    apply: (r, v) => {
      if (/^skip|none|null$/i.test(v)) {
        r.return_time_window = null;
      } else {
        r.return_time_window = TIME_PRESET_TO_VAL[v] || v.trim();
      }
    },
    validate: (v) => {
      if (/^skip|none|null$/i.test(v)) return null;
      const out = TIME_PRESET_TO_VAL[v] || v.trim();
      return isTimeWindow(out) ? null : '格式須為 HH:MM-HH:MM 或 skip';
    },
  },
  threshold: {
    apply: (r, v) => { r.notify_threshold = v.toLowerCase(); },
    validate: (v) => (['cheap', 'good', 'any'].includes(v.toLowerCase()) ? null : '須為 cheap / good / any'),
  },
};

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
  const err = handler.validate(value);
  if (err) return sendMsg(env, chatId, `❌ ${err}`);

  const { data, sha } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  try {
    handler.apply(r, value);
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
      { text: '每日最低', callback_data: `history:${id}` },
      { text: '走勢圖', callback_data: `chart:${id}` },
    ],
    [
      { text: '歷史最低', callback_data: `best:${id}` },
      { text: '立即掃描', callback_data: 'scan' },
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
  if (action === 'pause') return cmdToggleActive(env, chatId, id, false);
  if (action === 'resume') return cmdToggleActive(env, chatId, id, true);
  if (action === 'clone') return cmdClone(env, chatId, id);
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

async function handleFlow(env, chatId, text, state) {
  if (state.flow === 'add') return handleAddFlow(env, chatId, text, state);
  if (state.flow === 'add_confirm') return handleAddConfirm(env, chatId, text, state);
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
  let value;
  try {
    value = step.parse ? step.parse(text) : text;
  } catch (e) {
    return sendMsg(
      env,
      chatId,
      `❌ ${e.message}\n請重新輸入：`,
      step.keyboard ? kbOpts(step.keyboard) : removeKbOpts()
    );
  }
  if (step.validate) {
    const err = step.validate(value);
    if (err) {
      return sendMsg(
        env,
        chatId,
        `❌ ${err}\n請重新輸入：`,
        step.keyboard ? kbOpts(step.keyboard) : removeKbOpts()
      );
    }
  }

  // 給確認文字（例如多目的地解析結果）
  let extraNote = '';
  if (step.confirmText) {
    extraNote = `✓ ${step.confirmText(value)}\n\n`;
  }

  // postParse：把存進 state.data 的形式縮減
  const stored = step.postParse ? step.postParse(value) : value;
  state.data[step.key] = stored;
  state.step += 1;

  if (state.step >= ADD_STEPS.length) {
    state.flow = 'add_confirm';
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    const preview = formatRouteSummary(buildAddRoute('?', state.data));
    await sendMsg(
      env,
      chatId,
      `${extraNote}📋 請確認新增：\n\n${preview}\n\n我已先用推薦設定：不限預算、去回時段不限、最多轉 1 次。\n按下方按鈕確認或取消。`,
      kbOpts([['✅ 確認新增'], ['❌ 取消']])
    );
    return;
  }

  await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
  await promptStep(env, chatId, state.step, extraNote);
}

async function handleAddConfirm(env, chatId, text, state) {
  if (/^\/cancel$|取消|cancel|❌/i.test(text)) {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '已取消，未新增任何路線', removeKbOpts());
  }
  if (!/確認|✅|confirm/i.test(text)) {
    return sendMsg(
      env,
      chatId,
      '請點「✅ 確認新增」或「❌ 取消」',
      kbOpts([['✅ 確認新增'], ['❌ 取消']])
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

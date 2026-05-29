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

【掃描 & 查詢】
/scan                       立即觸發排程
/history <id> [days]        過去 N 天每日最低（預設 30 天）
/best <id>                  歷史最低 5 筆
/chart <id> [days]          走勢圖 PNG（預設 30 天）

【其他】
/cancel                     取消當前 /add 對話
/help                       顯示這份說明

提示：
• /add 需要選的步驟都有按鈕，不必打字
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

function removeKbOpts() {
  return { reply_markup: { remove_keyboard: true } };
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

// ─── /add 流程定義 ───
const ADD_STEPS = [
  {
    key: 'name',
    prompt: '請輸入航線名稱（例如「北海道豪經 9 天」）',
    validate: (v) => (v.length > 0 && v.length <= 50 ? null : '名稱需 1-50 字'),
  },
  {
    key: 'origin',
    prompt: '出發機場（點按鈕或輸入 IATA 三碼）',
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
    key: 'destinations',
    prompt: `抵達機場（可多個，逗號或空格分隔）

可輸入：
• 城市中文名：東京 大阪 札幌 沖繩 首爾 曼谷 香港 新加坡…
• IATA 三碼：NRT, HND, ICN…
• 兩者混用：東京 KIX 首爾`,
    parse: (v) => parseDestinations(v),
    validate: (v) => (v.codes.length > 0 ? null : '至少需 1 個目的地'),
    postParse: (v) => v.codes, // 寫入 state.data 時只存 codes
    confirmText: (v) => `解析結果：${v.labels.join(' / ')}`,
  },
  {
    key: 'cabin_classes',
    prompt: '艙等（可點按鈕，多個用逗號分隔）',
    keyboard: [
      ['經濟艙', '豪華經濟'],
      ['商務艙', '頭等艙'],
    ],
    parse: (v) => {
      const tokens = v.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
      return tokens.map((t) => CABIN_LABEL_TO_VAL[t] || t.toLowerCase());
    },
    validate: (v) => {
      const valid = ['economy', 'premium_economy', 'business', 'first'];
      const bad = v.filter((c) => !valid.includes(c));
      return bad.length === 0
        ? null
        : `不認識的艙等：${bad.join(',')}（須為 economy / premium_economy / business / first）`;
    },
  },
  {
    key: 'depart_date_range',
    prompt: '出發日期區間（格式 YYYY-MM-DD,YYYY-MM-DD）\n例如：2026-10-01,2026-12-31',
    parse: (v) => {
      const [s, e] = v.split(/[,，]/).map((x) => x.trim());
      return { start: s, end: e };
    },
    validate: (v) => {
      if (!v.start || !v.end) return '請給兩個日期，用逗號分隔';
      if (!isDate(v.start) || !isDate(v.end)) return '日期格式須為 YYYY-MM-DD';
      if (v.start > v.end) return '開始日期需早於結束日期';
      return null;
    },
  },
  {
    key: 'trip_duration_days',
    prompt: '行程天數',
    keyboard: [['3', '5', '7'], ['9', '10', '14']],
    parse: (v) => parseInt(v),
    validate: (v) => (Number.isFinite(v) && v >= 1 && v <= 90 ? null : '須為 1-90 整數'),
  },
  {
    key: 'must_contain_full_weekends',
    prompt: '需含幾個完整週末（0=不限）',
    keyboard: [['0', '1', '2']],
    parse: (v) => parseInt(v) || 0,
    validate: (v) => (v >= 0 && v <= 5 ? null : '須為 0-5'),
  },
  {
    key: 'max_price_twd',
    prompt: '票價上限 TWD（0=不限）',
    keyboard: [
      ['0 不限', '10000', '20000'],
      ['30000', '50000', '80000'],
    ],
    parse: (v) => {
      const m = v.match(/\d+/);
      return m ? parseInt(m[0]) : 0;
    },
    validate: (v) => (v >= 0 ? null : '須 ≥ 0'),
  },
  {
    key: 'depart_time_window',
    prompt: '出發時段',
    keyboard: [
      ['早班 06-12', '午班 12-18'],
      ['晚班 18-24', 'skip 不限'],
    ],
    parse: (v) => {
      if (/^skip/i.test(v)) return null;
      return TIME_PRESET_TO_VAL[v] || v.trim();
    },
    validate: (v) => (v === null || isTimeWindow(v) ? null : '格式須為 HH:MM-HH:MM'),
  },
  {
    key: 'return_time_window',
    prompt: '回程時段',
    keyboard: [
      ['早班 06-12', '午班 12-18'],
      ['晚班 18-24', 'skip 不限'],
    ],
    parse: (v) => {
      if (/^skip/i.test(v)) return null;
      return TIME_PRESET_TO_VAL[v] || v.trim();
    },
    validate: (v) => (v === null || isTimeWindow(v) ? null : '格式須為 HH:MM-HH:MM'),
  },
  {
    key: 'max_stops',
    prompt: '最多轉幾次',
    keyboard: [['0 直飛', '1 可轉一次', '2 可轉兩次']],
    parse: (v) => {
      const m = v.match(/\d+/);
      return m ? parseInt(m[0]) : 0;
    },
    validate: (v) => (v >= 0 && v <= 3 ? null : '須為 0-3'),
  },
  {
    key: 'notify_threshold',
    prompt: '通知門檻',
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
        await sendMsg(env, chatId, '請用 / 開頭的指令。/help 看說明');
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
      return sendMsg(env, chatId, HELP_TEXT, removeKbOpts());
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

async function cmdList(env, chatId) {
  const { data } = await loadRoutes(env);
  if (data.routes.length === 0) {
    return sendMsg(env, chatId, '尚無追蹤航線。/add 開始新增', removeKbOpts());
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
  lines.push('輸入 /show <id> 看單條詳細跟操作按鈕');
  await sendMsg(env, chatId, lines.join('\n'), removeKbOpts());
}

async function cmdShow(env, chatId, id) {
  if (isNaN(id)) return sendMsg(env, chatId, '用法：/show <id>');
  const { data } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  const text = `📍 路線詳細\n\n${formatRouteSummary(r)}\n\n下方按鈕可直接操作：`;
  const buttons = r.active === false
    ? [[`/resume ${id}`], [`/remove ${id}`, `/scan`]]
    : [[`/pause ${id}`], [`/remove ${id}`, `/scan`]];
  await sendMsg(env, chatId, text, kbOpts(buttons, false));
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
  await sendMsg(env, chatId, `開始新增航線（任何時候輸入 /cancel 取消）\n\n${step.prompt}`, opts);
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
    const preview = formatRouteSummary({ id: '?', active: true, ...state.data });
    await sendMsg(
      env,
      chatId,
      `${extraNote}📋 請確認新增：\n\n${preview}\n\n按下方按鈕確認或取消`,
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
  const route = {
    id: newId,
    active: true,
    created_at: new Date().toISOString().slice(0, 10),
    ...state.data,
  };
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

// Cloudflare Worker：Telegram bot 互動處理
// 接收 Telegram webhook → 處理指令 → 改 GitHub 上的 routes.json → 回訊息

const HELP_TEXT = `🤖 機票追蹤 Bot

/list              列出所有追蹤航線
/show <id>         看某條航線細節
/add               新增航線（多輪對話）
/remove <id>       刪除航線
/pause <id>        暫停（不刪、不通知）
/resume <id>       恢復
/scan              立即觸發排程
/cancel            取消當前對話
/help              顯示這份說明`;

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
    if (chatId !== String(env.AUTHORIZED_CHAT_ID)) {
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
      await sendMsg(env, chatId, `❌ 錯誤：${e.message}`);
    }

    return new Response('OK');
  }
};

// ─── Telegram API ───
async function sendMsg(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
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
  const content = atob(data.content.replace(/\n/g, ''));
  return { content, sha: data.sha };
}

async function writeFile(env, path, newContent, sha, message) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${path}`;
  const body = {
    message,
    content: btoa(unescape(encodeURIComponent(newContent))),
  };
  if (sha) body.sha = sha;
  const r = await fetch(url, {
    method: 'PUT',
    headers: { ...GH_HEADERS(env), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`GitHub write fail: ${r.status} ${await r.text()}`);
}

async function triggerWorkflow(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/scrape.yml/dispatches`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { ...GH_HEADERS(env), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main' }),
  });
  if (!r.ok) throw new Error(`Workflow trigger fail: ${r.status} ${await r.text()}`);
}

// ─── routes.json 操作 ───
async function loadRoutes(env) {
  const file = await readFile(env, 'routes.json');
  if (!file) return { data: { routes: [], next_id: 1 }, sha: null };
  return { data: JSON.parse(file.content), sha: file.sha };
}

async function saveRoutes(env, data, sha, message) {
  const content = JSON.stringify(data, null, 2);
  await writeFile(env, 'routes.json', content, sha, message);
}

// ─── 指令處理 ───
async function handleCommand(env, chatId, text) {
  const [cmd, ...args] = text.split(/\s+/);
  switch (cmd) {
    case '/help':
    case '/start':
      return sendMsg(env, chatId, HELP_TEXT);
    case '/list':
      return cmdList(env, chatId);
    case '/show':
      return cmdShow(env, chatId, parseInt(args[0]));
    case '/add':
      return cmdAddStart(env, chatId);
    case '/remove':
      return cmdRemove(env, chatId, parseInt(args[0]));
    case '/pause':
      return cmdToggleActive(env, chatId, parseInt(args[0]), false);
    case '/resume':
      return cmdToggleActive(env, chatId, parseInt(args[0]), true);
    case '/scan':
      return cmdScan(env, chatId);
    case '/cancel':
      await env.STATE.delete(`dlg:${chatId}`);
      return sendMsg(env, chatId, '已取消');
    default:
      return sendMsg(env, chatId, `未知指令：${cmd}。/help 看說明`);
  }
}

async function cmdList(env, chatId) {
  const { data } = await loadRoutes(env);
  if (data.routes.length === 0) {
    return sendMsg(env, chatId, '尚無追蹤航線。/add 開始新增');
  }
  const lines = ['📋 追蹤中的航線：', ''];
  for (const r of data.routes) {
    const status = r.active ? '🟢' : '⏸️';
    lines.push(`${status} #${r.id} ${r.name}`);
    lines.push(`   ${r.origin}→${r.destinations.join('/')} (${r.cabin_classes.join(',')})`);
    lines.push(`   ${r.depart_date_range.start}~${r.depart_date_range.end} 共 ${r.trip_duration_days} 天`);
    lines.push('');
  }
  await sendMsg(env, chatId, lines.join('\n'));
}

async function cmdShow(env, chatId, id) {
  const { data } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  await sendMsg(env, chatId, JSON.stringify(r, null, 2));
}

async function cmdRemove(env, chatId, id) {
  const { data, sha } = await loadRoutes(env);
  const idx = data.routes.findIndex((x) => x.id === id);
  if (idx < 0) return sendMsg(env, chatId, `找不到 #${id}`);
  const removed = data.routes.splice(idx, 1)[0];
  await saveRoutes(env, data, sha, `Remove route #${id} via bot`);
  await sendMsg(env, chatId, `✅ 已刪除 #${id}：${removed.name}`);
}

async function cmdToggleActive(env, chatId, id, active) {
  const { data, sha } = await loadRoutes(env);
  const r = data.routes.find((x) => x.id === id);
  if (!r) return sendMsg(env, chatId, `找不到 #${id}`);
  r.active = active;
  await saveRoutes(env, data, sha, `${active ? 'Resume' : 'Pause'} route #${id}`);
  await sendMsg(env, chatId, `✅ #${id} ${active ? '已恢復' : '已暫停'}`);
}

async function cmdScan(env, chatId) {
  await triggerWorkflow(env);
  await sendMsg(env, chatId, '✅ 已觸發排程，約 1-3 分鐘後通知');
}

// ─── /add 多輪對話 ───
const ADD_STEPS = [
  { key: 'name', prompt: '請輸入航線名稱（例如「北海道豪經 9 天」）' },
  { key: 'origin', prompt: '出發機場 IATA 代碼（例如 TPE）', parse: (v) => v.trim().toUpperCase() },
  {
    key: 'destinations',
    prompt: '抵達機場（可多個，逗號分隔，例如 CTS,HKD）',
    parse: (v) => v.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean),
  },
  {
    key: 'cabin_classes',
    prompt: '艙等（可多選，逗號分隔）：economy / premium_economy / business / first',
    parse: (v) => v.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
  },
  {
    key: 'depart_date_range',
    prompt: '出發日期區間（格式 YYYY-MM-DD,YYYY-MM-DD）',
    parse: (v) => {
      const [s, e] = v.split(',').map((x) => x.trim());
      return { start: s, end: e };
    },
  },
  { key: 'trip_duration_days', prompt: '行程天數（整數）', parse: (v) => parseInt(v) },
  { key: 'must_contain_full_weekends', prompt: '需含 N 個完整週末（0=不限）', parse: (v) => parseInt(v) || 0 },
  { key: 'max_price_twd', prompt: '票價上限 TWD（0=不限）', parse: (v) => parseInt(v) || 0 },
  {
    key: 'depart_time_window',
    prompt: '出發時段（例如 09:00-12:00，或回 skip）',
    parse: (v) => (v.toLowerCase() === 'skip' ? null : v.trim()),
  },
  {
    key: 'return_time_window',
    prompt: '回程時段（例如 18:00-21:00，或回 skip）',
    parse: (v) => (v.toLowerCase() === 'skip' ? null : v.trim()),
  },
  { key: 'max_stops', prompt: '最多轉幾次（0=直飛、1=可轉一次）', parse: (v) => parseInt(v) || 0 },
  { key: 'notify_threshold', prompt: '通知門檻（cheap / good / any）', parse: (v) => v.trim().toLowerCase() },
];

async function cmdAddStart(env, chatId) {
  await env.STATE.put(
    `dlg:${chatId}`,
    JSON.stringify({ flow: 'add', step: 0, data: {} }),
    { expirationTtl: 1800 }
  );
  await sendMsg(env, chatId, `開始新增航線（任何時候 /cancel 取消）\n\n${ADD_STEPS[0].prompt}`);
}

async function handleFlow(env, chatId, text, state) {
  if (state.flow !== 'add') {
    await env.STATE.delete(`dlg:${chatId}`);
    return sendMsg(env, chatId, '未知對話狀態，已清除');
  }

  const step = ADD_STEPS[state.step];
  try {
    const value = step.parse ? step.parse(text) : text;
    state.data[step.key] = value;
  } catch (e) {
    return sendMsg(env, chatId, `格式錯誤：${e.message}。請再輸入一次`);
  }

  state.step += 1;
  if (state.step >= ADD_STEPS.length) {
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
      `✅ 已新增 #${newId}：${route.name}\n下次排程會自動追蹤，或回 /scan 立即抓`
    );
  } else {
    await env.STATE.put(`dlg:${chatId}`, JSON.stringify(state), { expirationTtl: 1800 });
    await sendMsg(env, chatId, ADD_STEPS[state.step].prompt);
  }
}

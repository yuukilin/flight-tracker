# Flight Tracker —— 交接文件（給新對話的 Claude）

> 寫於 2026-05-28，由勇成（@yuukilin）跟上一輪 Claude 一起做的優化進度紀錄。
> **新對話 Claude 開工前必讀順序**：
> 1. 先讀 `CLAUDE.md`（專案總覽，已有的）
> 2. 再讀這份 `HANDOFF.md`（上一輪做了什麼、還剩什麼）
> 3. 再開始動工

---

## 一、上一輪在幹嘛

使用者在 Telegram 用 `/add` 加完第一條路線「北海道豪經 9 天」後，發現 4 個問題：

1. `/list` 中文變亂碼 `åæµ·éè±ªç¶ 9 å¤©`
2. 想用按鈕點選不要打字
3. heartbeat 訊息「本次無達門檻的路線」不知道是真的沒有還是壞了
4. 想要城市中文名 → 機場 IATA 代碼的對應

上一輪做了「列出所有要改的清單 + 一項一項做」的優化工程。

---

## 二、已完成 ✅（檔案已改但**尚未 commit / push / deploy**）

### P0 已修
- **#1 Worker base64 UTF-8 亂碼**：`worker/src/index.js` 加 `b64ToUtf8` / `utf8ToB64`，替代直接用 `atob/btoa`。已用 Node 驗證來回正確。
- **#2 心跳訊息資訊化**：`scripts/notify.py` 整份重寫。現在會輸出「掃了 N 條、總寫入 M 筆、各路線今日傳統/廉航筆數、最低價、狀態、歷史樣本、跌幅」，使用者一眼分得出「真的沒便宜」vs「沒抓到資料」。
- **#3 查 Actions log**：用 Chrome MCP 進 GitHub，**確認系統沒壞**——run #9 scrape 寫了 260 筆，只是 analyze 算出 insufficient_data（歷史 0 天）所以 notify 跳過。改完 notify.py 之後使用者就不會再誤會。

### P1 已做
- **#4 /add 流程按鈕化**：每步給 ReplyKeyboardMarkup，可以點按鈕也可以打字。
- **#5 機場字典**：`worker/src/index.js` 內建 ~80 個台灣人常去的目的地（日、韓、東南亞、港澳中、美加、歐洲、澳紐、中東、台灣），輸入「東京」自動轉成 NRT + HND。
- **#6 /add summary 確認**：流程最後出 summary，按「✅ 確認新增 / ❌ 取消」。

### P2 已做
- **#8 即時匯率**：`scripts/scrape.py` 改用 `open.er-api.com` → fallback `exchangerate.host` → fallback `data/last_fx.json` cache → 最後 fallback 寫死 32。每次 scrape 開始時拿一次。

### P3 已做
- **#12 routes.json 欄位驗證**：/add 每步 parse 完做 validate（日期格式、IATA 三碼、艙等 enum、行程天數 1-90 等），失敗給友善錯誤訊息。
- **#14 多 chat_id 支援**：Worker 的 `AUTHORIZED_CHAT_ID` 跟 notify.py 的 `TELEGRAM_CHAT_ID` 都改成支援逗號分隔多個。
- **#15 美化 /show**：原本吐 raw JSON，現在用 `formatRouteSummary()` 顯示人話 + 底部 reply keyboard 操作按鈕（/pause、/remove、/scan）。

---

## 三、2026-05-29 接手後已確認 / 已補完 ✅

Codex 已讀 `worker/src/index.js`、`scripts/notify.py`、`scripts/scrape.py`、`scripts/query.py`、`query.yml`，確認 HELP_TEXT 裡列的主要指令都有底層實作：

| 指令 | HELP_TEXT 有列 | 底層實作存在？ |
|---|---|---|
| /list | ✅ | ✅（cmdList） |
| /show | ✅ | ✅（cmdShow） |
| /add | ✅ | ✅（cmdAddStart + handleAddFlow + handleAddConfirm） |
| /edit | ✅ | ✅（cmdEdit + EDIT_FIELD_HANDLERS） |
| /clone | ✅ | ✅（cmdClone） |
| /remove | ✅ | ✅（cmdRemove） |
| /pause | ✅ | ✅（cmdToggleActive） |
| /resume | ✅ | ✅（cmdToggleActive） |
| /threshold | ✅ | ✅（cmdThreshold） |
| /scan | ✅ | ✅（cmdScan） |
| /history | ✅ | ✅（cmdQuery → query.yml → scripts/query.py） |
| /best | ✅ | ✅（cmdQuery → query.yml → scripts/query.py） |
| /chart | ✅ | ✅（cmdQuery → query.yml → scripts/query.py） |

### 本輪補的防呆
- `晚班 18-24` 改存 `18:00-23:59`，且 Python 端仍能容忍舊資料的 `24:00`，避免 scrape 因時間格式炸掉。
- 暫停路線不再更新連續失敗計數，也不會在 notify 裡跳連續失敗警報。
- `scrape_state.json`、`last_fx.json` 已加入 Actions cache / artifact，連續失敗與匯率 cache 才能跨 run 保存。
- `query.yml` 的輸入改成環境變數引用，避免 workflow shell input 沒加引號。
- `scripts/query.py` 改成只讀 DB，並用最新 `excluded_airlines.yaml` 即時排除已知廉航；舊 DB 還沒重分類時，`/best` 也不會把已知 LCC 列進傳統航空。
- `/history`、`/best` 乾跑成功；`/chart` 在本機因未安裝 matplotlib 會走友善錯誤，GitHub Actions 會依 `requirements.txt` 安裝。
- `CLAUDE.md` 已更新到 2026-05-29 狀態。

### 仍未做
- 尚未 commit / push / deploy。
- `data/prices.db` 與 `data/analysis.json` 雖已列在 `.gitignore`，但目前仍被 git 追蹤；未來若要完全符合「data 不入 git」設計，可另做 `git rm --cached data/prices.db data/analysis.json`。

---

## 四、立刻可以 deploy 的部分（給使用者）

下面這些已經改完的檔案，使用者可以馬上 push + deploy 拿到下列好處：
- ✅ `/list` 中文不再亂碼
- ✅ /add 按鈕化、城市名 → 機場、summary 確認、欄位驗證
- ✅ heartbeat 訊息資訊量大增（不會再誤會系統壞了）
- ✅ 即時匯率
- ✅ 多 chat_id 支援
- ✅ /show 美化
- ✅ `/edit` `/clone` `/threshold`
- ✅ `/history` `/best` `/chart`
- ✅ 異常下殺、連續失敗警報

### 步驟

```bash
cd /Users/yuukilin/Desktop/python/flight-tracker

# 1. 看一下要 commit 的檔案
git status
git diff --stat

# 應該會看到包含：
#   modified: .github/workflows/scrape.yml
#   modified: CLAUDE.md
#   modified: requirements.txt
#   modified: scripts/notify.py
#   modified: scripts/scrape.py
#   modified: worker/src/index.js
#   new file: .github/workflows/query.yml
#   new file: HANDOFF.md
#   new file: scripts/query.py

# 2. commit
git add .github/workflows/scrape.yml .github/workflows/query.yml CLAUDE.md HANDOFF.md requirements.txt scripts/notify.py scripts/scrape.py scripts/query.py worker/src/index.js
git commit -m "Improve Telegram flight bot controls and query workflows"

# 3. push 到 GitHub（這個會觸發 Actions 排程嗎？不會，cron 才會）
git push

# 4. 重新部署 Worker
cd worker
wrangler deploy
```

部署完之後試：
- `/list` 應該看到正確中文「#1 北海道豪經 9 天」
- `/add` 開始問問題，每步出按鈕
- 抵達機場那步輸入「東京」→ 自動解析成 NRT + HND
- 流程最後出 summary，點「✅ 確認新增」才真的存
- 之後排程跑完，Telegram 收到的訊息會有豐富資訊（總筆數、各路線狀態、跌幅）

---

## 五、新對話 Claude 開工第一步建議

按優先順序：

1. **先看 git status**：確認上一輪這批檔案是否已 commit / push / deploy。

2. **若尚未部署，優先 push + deploy**：`query.yml` 不上 GitHub，`/history` `/best` `/chart` 會觸發不到；Worker 不 deploy，Telegram 還是舊版本。

3. **部署後測 Telegram**：
   - `/list`
   - `/show 1`
   - `/history 1 30`
   - `/best 1`
   - `/chart 1 30`
   - `/scan`

4. **可選整理**：處理 `data/prices.db` / `data/analysis.json` 仍被 git 追蹤的歷史包袱。

---

## 六、檔案位置速查

```
/Users/yuukilin/Desktop/python/flight-tracker/
├── CLAUDE.md                          專案總覽（必讀）
├── HANDOFF.md                         這份（必讀）
├── README.md
├── requirements.txt
├── routes.yaml                        (人類手編)
├── routes.json                        (Bot 改、程式優先讀)
├── excluded_airlines.yaml             (廉航名單)
├── .github/workflows/
│   ├── scrape.yml                     排程抓價、分析、通知
│   └── query.yml                      /history /best /chart 查詢
├── data/                              目標是不入 git，Actions cache + Artifact 管
│   ├── prices.db
│   ├── analysis.json
│   ├── last_fx.json                   ← 新增（匯率 cache）
│   └── scrape_state.json              ← 新增（連續失敗計數）
├── scripts/
│   ├── scrape.py                      改完（即時匯率、連續失敗追蹤、清理舊資料）
│   ├── analyze.py                     沒動
│   ├── notify.py                      改完（資訊豐富心跳、異常下殺、連續失敗警報）
│   └── query.py                       /history /best /chart 查詢與畫 PNG
└── worker/
    ├── wrangler.toml
    └── src/index.js                   大改寫（按鈕化、機場字典、summary、驗證、多 chat_id）
```

---

## 七、使用者偏好快速重點

- 繁中、不用簡體
- 解釋技術要高中生能懂
- 不確定就說不確定，禁止猜測
- 改程式碼必須貼完整可執行版本
- 抓網頁優先 Chrome MCP，WebFetch 失敗自動改 Chrome MCP
- 每次對話第一步 `TZ=Asia/Taipei date`
- 使用者零程式背景，所有技術說明要用高中生聽得懂的方式
- Codex 會審查 Claude 的輸出，所以做事要乾淨完整

---

## 八、Secrets / 重要金鑰

- **GitHub PAT 過期日：2026-08-25**（90 天從 2026-05-27 起算）。過期前要重發 + 重設 wrangler secret。
- Worker URL：`https://flight-bot.sonyzxcgo7411.workers.dev`
- GitHub Repo：`yuukilin/flight-tracker`
- 兩個地方有 secret 要設：GitHub repo Settings → Secrets → Actions，以及本機 `wrangler secret put <NAME>`。細節見 CLAUDE.md 第六章。

---

## 九、上一輪沒做但討論過的「未來想做」

CLAUDE.md 第五章「未來可加」原列了 5 項，其中 4 項已在這輪 task list（/chart /history /threshold 即時匯率 中華電簡訊備援），即時匯率已完成；剩下：

- **中華電信簡訊備援**：Telegram 掛了用簡訊推，要錢可能要等真的需要再加。
- 這輪還新增討論過但都沒做的：
  - 多日掃描去重（避免一樣的票每天通知）
  - 旅遊旺季標籤（日本黃金週、台灣連假自動跳警告）
  - 廉航行李費估算欄

---

希望這份檔讓接手 Claude 無痛上工 ✈️

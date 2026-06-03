# Flight Tracker —— 交接文件（給新對話的 Codex / Claude）

> 寫於 2026-05-28，更新於 2026-06-03。
> **新對話 Claude 開工前必讀順序**：
> 1. 先讀 `CLAUDE.md`（專案總覽，已有的）
> 2. 再讀這份 `HANDOFF.md`（上一輪做了什麼、還剩什麼）
> 3. 再開始動工

---

## 0、新對話快速摘要（先看這裡）

這是一套「完全免費、零 VPS、Telegram 操作」的機票價格追蹤系統。

系統架構：
- 使用者在 Telegram 操作 bot。
- Cloudflare Worker `flight-bot` 接 Telegram webhook。
- Worker 用 GitHub API 修改 `routes.json`，也可以觸發 GitHub Actions。
- GitHub Actions 每天台北時間 09:00 / 21:00 跑 scrape，抓 Google Flights，寫入 SQLite cache，分析歷史百分位，最後推 Telegram 通知。

最新狀態：
- Telegram bot 已支援一句話新增、路線設定按鈕修改、抓取診斷、Google Flights 查票按鈕。
- `/menu` 已改成首頁狀態面板，會讀 Worker KV 裡的最新掃描狀態。
- GitHub Actions 掃描後會寫 `data/status.json`、更新 `data/notified_state.json`，並把 status 同步到 Worker KV。
- 通知已加入去重：同一價格、同一狀態不會一直被當成新提醒。
- `data/prices.db` 與 `data/analysis.json` 已從 git 追蹤移除，只靠 Actions cache / artifact 保存。
- 本輪完成後需 push 到 GitHub 並重新部署 Worker，讓 `/menu` 狀態面板與 `/internal/status` 上線。

現在 Telegram bot 已支援：
- `/menu` 主選單
- `/menu` 首頁狀態面板：上次掃描、寫入筆數、各路線最低價、資料量、下次排程
- `/list` 路線列表，點路線可進操作面板
- 路線操作按鈕：每日最低、走勢圖、歷史最低、立即掃描、抓取診斷、修改設定、暫停/恢復、改通知、複製、刪除
- `/add` 快速新增路線：目的地、出發地、日期、天數、艙等、週末、通知標準
- 一句話新增路線，例如「我想明年10月到12月去札幌，豪經，9天，跨兩個週末」
- `/add` 日期可輸入：`10/1-12/31`、`10月到12月`、`明年10月到12月`、`賞楓`、`滑雪`、`寒假`、`暑假`、`跨年`

下一輪主要目標：
- 觀察下一次實跑後 `/menu` 是否成功顯示最新掃描狀態。
- 若通知仍覺得太長，把 heartbeat 摘要再壓短，只列有變化的路線。

---

## 一、上一輪在幹嘛

使用者在 Telegram 用 `/add` 加完第一條路線「北海道豪經 9 天」後，發現 4 個問題：

1. `/list` 中文變亂碼 `åæµ·éè±ªç¶ 9 å¤©`
2. 想用按鈕點選不要打字
3. heartbeat 訊息「本次無達門檻的路線」不知道是真的沒有還是壞了
4. 想要城市中文名 → 機場 IATA 代碼的對應

上一輪做了「列出所有要改的清單 + 一項一項做」的優化工程。

---

## 二、已完成 ✅（截至 2026-05-29 已 push / deploy）

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

### 2026-05-29 介面升級已做
- `/menu` 主選單與 Telegram command menu。
- `/list` 路線下方有「操作 #id」按鈕。
- `/show` / 路線操作面板有每日最低、走勢圖、歷史最低、立即掃描、暫停/恢復、改通知、複製、刪除。
- `/add` 改成快速新增，只問必要旅行問題。
- `/add` 支援人話日期與旅行季節：`10/1-12/31`、`10月到12月`、`明年10月到12月`、`賞楓`、`寒假`、`暑假`、`跨年`。
- `/add` 自動命名路線，例如「札幌豪經 9 天」。
- `/add` 預設：不限預算、去回時段不限、最多轉 1 次，後續可再改。

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

### 2026-06-03 已補完
- 一句話新增路線已完成。
- 修改路線全按鈕化已完成。
- `/debug` / `/last` 抓取診斷已完成。
- Google Flights 查票按鈕與航空公司官網搜尋入口已完成。
- `/menu` 首頁狀態面板已完成。
- 通知去重已完成。
- `data/prices.db` 與 `data/analysis.json` 已從 git 追蹤移除。

---

## 四、立刻可以 deploy 的部分（給使用者）

下面這批是 2026-06-03 新增或調整的項目，push + deploy 後生效：
- `/menu` 首頁狀態面板
- 掃描後同步 `status.json` 到 Worker KV
- `notified_state.json` 通知去重
- Google Flights + 航空公司官網搜尋按鈕
- `data/prices.db` / `data/analysis.json` 退出 git 追蹤
- `CLAUDE.md` / `HANDOFF.md` 更新到最新狀態

### 步驟

```bash
cd /Users/yuukilin/Desktop/python/flight-tracker

# 1. 看一下要 commit 的檔案
git status
git diff --stat

# 應該會看到包含：
#   modified: .github/workflows/scrape.yml
#   modified: .gitignore
#   modified: CLAUDE.md
#   modified: HANDOFF.md
#   modified: scripts/notify.py
#   modified: scripts/query.py
#   modified: worker/src/index.js
#   deleted from git index: data/prices.db, data/analysis.json

# 2. commit
git add .github/workflows/scrape.yml .gitignore CLAUDE.md HANDOFF.md scripts/notify.py scripts/query.py worker/src/index.js
git add -u data/prices.db data/analysis.json
git commit -m "Add flight bot status panel and notification dedupe"

# 3. push 到 GitHub（這個會觸發 Actions 排程嗎？不會，cron 才會）
git push

# 4. 重新部署 Worker
cd worker
wrangler deploy
```

部署完之後試：
- `/menu` 應顯示狀態面板；若還沒實跑新 workflow，會提示尚無最新狀態。
- `/scan` 跑完後，下一次 `/menu` 應顯示上次掃描、寫入筆數與各路線狀態。
- `/best 1` 應附 Google Flights 與航空公司官網搜尋按鈕。

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

4. **觀察通知去重**：同一條路線若沒有更便宜或狀態變好，應只出現在 heartbeat 摘要，不再被列為新提醒。

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
│   ├── status.json                    ← /menu 狀態面板資料
│   ├── notified_state.json            ← 通知去重狀態
│   ├── last_fx.json                   ← 匯率 cache
│   └── scrape_state.json              ← 連續失敗計數
├── scripts/
│   ├── scrape.py                      改完（即時匯率、連續失敗追蹤、清理舊資料）
│   ├── analyze.py                     沒動
│   ├── notify.py                      推播、status.json、通知去重
│   └── query.py                       /history /best /chart /debug 查詢與畫 PNG
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

## 九、優化 Roadmap 狀態（2026-06-03 更新）

使用者覺得目前 bot 已經能用，但整體仍偏「工程師指令工具」，希望變成更像真正旅行助理的 Telegram bot。下列是當時 roadmap 與目前狀態。

### P0：一句話新增路線（已完成）
- 目標：使用者可以直接打「我想明年10月到12月去札幌，豪經，9天，跨兩個週末」。
- Bot 解析後回：「我理解成 台北→札幌、2027/10/01-2027/12/31、豪經、9天、跨2個週末，對嗎？」
- 使用者按「確認新增 / 修改 / 取消」。
- 先支援常見句型即可，不必一次做自然語言萬能解析。

### P1：路線設定全按鈕化（已完成）
- `/show <id>` 的路線面板已有「修改設定」按鈕，點進後顯示：
  - 改目的地
  - 改日期
  - 改天數
  - 改艙等
  - 改週末
  - 改轉機
  - 改預算
  - 改通知標準
- 每一項都走小型多輪對話，不需要背 `/edit 1 field value`。

### P1：首頁狀態面板（已完成）
- `/menu` 不只顯示按鈕，會顯示目前系統狀態：
  - 目前追蹤幾條路線
  - 上次掃描時間
  - 上次總共抓到幾筆票
  - 有沒有路線連續抓不到資料
  - 今日最便宜 / 最值得注意的路線
- Python `notify.py` 會產出 `data/status.json`，Actions 會同步到 Worker KV，Worker 從 KV 顯示。

### P1：避免重複通知同一張票（已完成）
- 目標：同一條路線、同一個價格，如果昨天已經通知過，今天沒有更便宜就不要再吵。
- 已用 `data/notified_state.json` 記錄每條路線上次通知的最低價、狀態與航班摘要。
- 若價格更低、狀態從 normal 變 good/cheap，或異常下殺，再重新通知。

### P2：路線過期自動提醒
- 若某條路線的結束日期已過，Bot 主動提醒：
  - 「北海道豪經 9 天已過期，要暫停還是複製成新路線？」
- 按鈕：
  - 暫停
  - 複製並改日期
  - 刪除

### P2：通知訊息改得更像人話（大致完成，可再壓短）
- 目前通知已改成結論優先、少圖示、分段說明。若仍覺得太長，可再壓成：
  - 「北海道豪經今天有票，但還不算便宜。最低 NT$42,300，比上次低 3%。歷史資料還不夠，先觀察。」
- 再保留詳細資料區塊給需要的人看。

### P2：旅行季節模板（部分完成）
- 日本常用模板：
  - 賞楓：10-12月
  - 櫻花：3/15-4/15
  - 滑雪：12-2月
  - 暑假：7-8月
  - 寒假：1-2月
  - 跨年：12/20-1/5
- 之後可以依目的地做不同模板。

### P3：便宜程度分數
- 不只顯示 cheap/good/normal，可加一個 0-100 的「便宜度」。
- 白話規則：越接近 100 越值得買。
- 可用歷史百分位轉分數：例如低於 P25 給 75 分以上，低於 P10 給 90 分以上。

### P3：每週總結
- 每週日自動送：
  - 本週哪條路線降最多
  - 哪條路線一直抓不到資料
  - 哪條路線接近便宜區
  - 哪條路線可考慮暫停或延長日期

### 建議下一輪順序
1. 實跑 `/scan`，確認 `/menu` 是否讀到最新掃描狀態。
2. 若通知仍太長，壓縮 heartbeat 摘要。
3. 再做「路線過期自動提醒」或「每週總結」。

---

## 十、上一輪沒做但討論過的「未來想做」

CLAUDE.md 第五章「未來可加」原列了 5 項，其中 4 項已在這輪 task list（/chart /history /threshold 即時匯率 中華電簡訊備援），即時匯率已完成；剩下：

- **中華電信簡訊備援**：Telegram 掛了用簡訊推，要錢可能要等真的需要再加。
- 這輪還新增討論過但尚未完整做的：
  - 旅遊旺季標籤（日本黃金週、台灣連假自動跳警告）
  - 廉航行李費估算欄

---

## 十一、2026-06-03 本輪完成

### 1. `/menu` 首頁狀態面板
- `scripts/notify.py` 每次掃描後會產出 `data/status.json`。
- `scrape.yml` 會用 `STATUS_WEBHOOK_URL=https://flight-bot.sonyzxcgo7411.workers.dev/internal/status` 把 status 同步到 Worker。
- Worker 新增 `/internal/status`，使用 `Authorization: Bearer <TELEGRAM_BOT_TOKEN>` 驗證後寫進 KV `status:latest`。
- `/menu` 會讀 KV，顯示追蹤路線數、上次掃描、本次寫入、下次排程、各路線最低價與資料量。
- 若 status 不存在或超過 36 小時，`/menu` 會明講狀態不足或可能過舊。

### 2. 避免重複通知
- 新增 `data/notified_state.json`，由 Actions cache / artifact 保存。
- 只有以下情況會當成新提醒：
  - 首次達通知門檻
  - 比上次通知更便宜
  - 狀態變好，例如 normal → good / cheap
  - `notify_threshold=any` 且最低組合有變化
  - 明顯降價或連續失敗達警戒次數
- 一般 heartbeat 仍可照常送摘要，但不會把同一張票一直包裝成新事件。

### 3. 查票入口
- 通知、`/best`、路線面板都保留 Google Flights 查票按鈕。
- 因 fast-flights 沒有真正購票結帳 URL，所以不硬寫航空公司購票頁。
- 最低票會附「搜尋航空公司官網」按鈕，用 Google 搜尋航空公司官網與 booking 入口。

### 4. 資料檔不進 git
- `.gitignore` 已納入 `prices.db`、`analysis.json`、`scrape_state.json`、`notified_state.json`、`status.json`、`last_fx.json`。
- `data/prices.db` 與 `data/analysis.json` 已用 `git rm --cached` 從版本追蹤移除；本機檔案仍保留。

### 5. 介面文字
- `/menu`、`/list`、`/show` 與常見成功/錯誤訊息已降低圖示使用。
- 通知維持結論優先，避免用大量 icon 混淆判讀。

---

希望這份檔讓接手 Claude 無痛上工 ✈️

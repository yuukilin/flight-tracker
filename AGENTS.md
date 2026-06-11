# Flight Tracker —— 機票追蹤系統專案

> 這個檔給 Codex 看，新對話開始前請先讀完。
> 寫於 2026-05-27，由勇成（@yuukilin）與 Codex 共同建置。

---

## 一、專案是什麼

一套「**完全免費、零 VPS、可從 Telegram 互動操作**」的機票價格追蹤系統。

**核心需求：**
- 自動追蹤指定路線的 Google Flights 票價
- 跟歷史價格做百分位比較，告訴使用者「現在便宜還是貴」
- 廉航不納入歷史比較（裸票價會誤導），但仍列出供參考
- 使用者完全用 Telegram bot 管理路線（/add /list /remove /scan）
- 不放 VPS，全部跑在 GitHub Actions + Cloudflare Workers

---

## 二、架構

```
┌──────────────────────────────────────────────────────────────┐
│  使用者 Telegram                                              │
│   /list /add /remove /scan ...                                │
└───────────┬──────────────────────────────────▲────────────────┘
            │                                  │
            ▼                                  │
┌────────────────────────────────────────────────────────────────┐
│  Cloudflare Worker (flight-bot)                                │
│  URL: https://flight-bot.sonyzxcgo7411.workers.dev             │
│  - 接 Telegram webhook                                          │
│  - 用 GitHub API 改 routes.json                                 │
│  - 用 GitHub API 觸發 workflow_dispatch（/scan）                │
│  - 用 KV (binding: STATE) 存對話狀態                            │
│  - 驗證 chat_id（只接受授權使用者）                              │
└───────────┬──────────────────────────────────▲────────────────┘
            │                                  │
            ▼ 改 routes.json                   │ 推 commit
┌────────────────────────────────────────────────────────────────┐
│  GitHub Repo: yuukilin/flight-tracker                          │
│  https://github.com/yuukilin/flight-tracker                    │
│  - 程式碼、設定、廉航名單                                        │
│  - data/ 不在 git（用 Actions cache + Artifacts 管理）           │
└───────────┬──────────────────────────────────▲────────────────┘
            │                                  │
            ▼ cron 09:00 (台北)                │ commit data
┌────────────────────────────────────────────────────────────────┐
│  GitHub Actions (.github/workflows/scrape.yml)                 │
│  1. Restore prices.db from cache                               │
│  2. scrape.py: fast-flights 抓 Google Flights                  │
│  3. analyze.py: 算歷史百分位                                    │
│  4. notify.py: 推 Telegram 訊息                                │
│  5. Save prices.db to cache + upload Artifact                  │
└────────────────────────────────────────────────────────────────┘
```

---

## 三、檔案結構

```
flight-tracker/
├── AGENTS.md                    ← 你正在看的這份
├── README.md
├── requirements.txt              ← Python 套件
├── routes.yaml                   ← 路線設定（手動編輯）
├── routes.json                   ← Bot 改的路線設定（程式優先讀這個）
├── excluded_airlines.yaml        ← 廉航名單（iata_codes + name_keywords）
├── .gitignore                    ← 排除 data/prices.db, data/analysis.json
├── .github/
│   └── workflows/
│       ├── ci.yml                ← Push/PR 輕量檢查（不抓票、不發 Telegram）
│       ├── query.yml             ← /history /best /chart /debug 查詢
│       └── scrape.yml            ← Actions 排程：cron 0 1 * * *（UTC）
├── data/
│   └── .gitkeep                  ← 空資料夾佔位（prices.db 不入 git）
├── scripts/
│   ├── scrape.py                 ← 主爬蟲（含 reclassify_is_lcc, cleanup_invalid_rows）
│   ├── analyze.py                ← 百分位分析
│   ├── download_latest_artifact.py ← 下載最新 Actions artifact 到 data/
│   └── notify.py                 ← Telegram 推播
└── worker/
    ├── wrangler.toml             ← Cloudflare Worker 設定（含 KV id）
    ├── README.md
    └── src/
        └── index.js              ← Worker 主程式（指令處理 + GitHub API + KV 對話狀態）
```

---

## 四、關鍵設計決定（過程中討論定下的）

### 4.1 廉航處理
- **過濾**：傳統航空納入歷史比較；廉航標 is_lcc=1，**僅在 Telegram 分開顯示「未含行李費」**，不納入百分位計算
- **偵測**：兩種條件擇一即可（OR）
  - IATA 兩碼匹配 `iata_codes`
  - 航空公司名稱包含 `name_keywords`（小寫子字串比對）
- **codeshare 處理**：例如 "China Southern, Jetstar" → 因為含 "jetstar" → 算 LCC

### 4.2 資料持久化
- `data/prices.db` **不入 git**（避免 Actions auto-commit 跟使用者本地 push 衝突）
- 用 `actions/cache@v4`，key 用 `prices-db-${{ github.run_id }}`，restore-keys 用 `prices-db-` 前綴
- 另外 `actions/upload-artifact@v4` 上傳 30 天備援，可從 Actions 頁面下載

### 4.3 廉航附加費
- **不在程式裡計算附加費**（樂桃、虎航等行李費）
- 原因：使用者選擇「排除廉航 → 僅供參考」，不需精算
- 廉航區純粹是 Google Flights 顯示的裸票價，user 看到要自己估行李費

### 4.4 歷史比較邏輯
- 用 P25/P50/P75 分位數判定 cheap / good / normal / expensive
- 至少要 7 天歷史資料才能算（否則回 `insufficient_data`）
- 90 天分位為主要判斷依據，30 天、365 天作為輔助
- **第一個月會一直顯示「資料不足」是正常的**

### 4.5 routes.json vs routes.yaml
- `routes.yaml`：人類手動編輯用，git 追蹤
- `routes.json`：Bot 寫的版本，git 追蹤（會被 Worker 改）
- **scrape.py / analyze.py / notify.py 優先讀 json**，沒有才 fallback 到 yaml
- 第一次 /add 後，routes.json 會被建立，從此 yaml 被忽略

### 4.6 艙等與時段限制
- 一條 route 只追蹤一種艙等；若要比較豪經、商務，請分成兩條 route，避免歷史分位混在一起。
- 目前只支援 `depart_time_window`（去程起飛時段）過濾；回程時段尚未可靠拆出，不開放設定。

### 4.7 Cloudflare Worker
- 用 KV 存對話狀態（多輪 /add 用，binding: STATE，ttl: 1800 秒）
- 用 GitHub API（PAT）讀寫 routes.json
- 用 GitHub API 觸發 workflow_dispatch
- 5 個 secret：TELEGRAM_BOT_TOKEN、GITHUB_TOKEN、GITHUB_OWNER、GITHUB_REPO、AUTHORIZED_CHAT_ID

---

## 五、現在的狀態（2026-05-27 截止）

### ✅ 已完成
1. **階段 0–4**：GitHub repo、Actions 排程、Python scrape pipeline、Telegram 推播全部跑通
2. **階段 5（Worker）**：Cloudflare Worker 已部署，URL `https://flight-bot.sonyzxcgo7411.workers.dev`
3. **階段 6（Webhook）**：Telegram webhook 已指向 Worker
4. **階段 7（指令測試）**：`/help` 已驗證通

### ⏳ 進行中
- `/add` `/list` `/scan` `/remove` 等指令的實際測試（user 在做）
- 第一條真實路線「北海道豪經 9 天 跨 2 週末」的新增

### 🐛 已知小議題
- **149 筆空白 airline**：fast-flights 有時抓不到航空公司名（已在 scrape.py 加 `if not airline: continue` 跳過）
- **匯率寫死 1 USD = 32 TWD**：未來可接即時匯率 API
- **下次 scrape 跑完 reclassify_is_lcc 才會修對舊資料**：所以新加 LCC 名單後，要等下一次 Actions 才生效

### 📋 未來可加（user 想到再做）
- `/chart <id> <days>`：傳價格走勢圖 PNG
- `/threshold <id> <level>`：改通知門檻
- `/history <id>`：看某條路線過去 7 天最低價
- 即時匯率
- 中華電信簡訊備援（Telegram 掛了用）

---

## 六、Secrets 清單（**不放實際值**）

| 位置 | Secret 名稱 | 用途 |
|---|---|---|
| GitHub repo (Settings → Secrets → Actions) | `TELEGRAM_BOT_TOKEN` | notify.py 用 |
| 同上 | `TELEGRAM_CHAT_ID` | notify.py 用，純數字 |
| Cloudflare Worker (wrangler secret) | `TELEGRAM_BOT_TOKEN` | Worker 送訊息給 Telegram |
| 同上 | `GITHUB_TOKEN` | PAT，scope: repo + workflow，過期 90 天 |
| 同上 | `GITHUB_OWNER` | `yuukilin` |
| 同上 | `GITHUB_REPO` | `flight-tracker` |
| 同上 | `AUTHORIZED_CHAT_ID` | 只有這個 chat_id 能操作 bot |

GitHub PAT 過期日：2026-08-25（90 天從 2026-05-27 起算）。**過期前要重發+重設 wrangler secret**。

---

## 七、常用 Debug 指令

### 看 SQLite 資料
最新的 prices.db 在 GitHub Actions cache，下載方式：
1. https://github.com/yuukilin/flight-tracker/actions → 點任何一次 run
2. 頁面右上 `Artifacts` 下載 `prices-db-XXXX.zip`
3. 解壓用 [SQLite Browser](https://sqlitebrowser.org/) 開

或本地下載最新成功 run 的 artifact：
```bash
python3 scripts/download_latest_artifact.py
```
若 GitHub 要求授權，先設定 `GITHUB_TOKEN` 或 `GH_TOKEN` 後重試。

或本地用 python 看：
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('data/prices.db')
print('total:', conn.execute('SELECT COUNT(*) FROM prices').fetchone())
for row in conn.execute('SELECT airline_name, is_lcc, COUNT(*) FROM prices GROUP BY airline_name, is_lcc ORDER BY is_lcc DESC, COUNT(*) DESC LIMIT 20'):
    print(row)
"
```

### 看 Worker log
Cloudflare Dashboard → Workers & Pages → flight-bot → Logs → Begin log stream

### 手動觸發 Actions
- 從 Telegram：傳 `/scan`
- 從 GitHub 網頁：Actions → Flight Price Scrape → Run workflow
- 從 Terminal（要 gh CLI）：`gh workflow run scrape.yml`

### 看 Telegram webhook 狀態
```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

### 重新部署 Worker
```bash
cd /Users/yuukilin/Desktop/python/flight-tracker/worker
wrangler deploy
```

### 看 Wrangler 已設定的 secrets
```bash
wrangler secret list
```

---

## 八、使用者偏好（從 global AGENTS.md）

- **語言**：繁體中文，不可簡體、不可中國大陸用語
- **解釋**：科技/理組討論用高中生能聽懂的方式
- **誠實**：不確定就說「我不確定」，禁止猜測填補
- **互動**：需求不明時先復述理解再精準發問
- **程式碼**：修改必須貼完整可執行版本，不可只給片段
- **抓網頁**：優先 Chrome MCP；WebFetch 失敗自動改 Chrome MCP；金融機構網站直接 Chrome MCP
- **時間規則**：每次對話第一動作先 `TZ=Asia/Taipei date`

---

## 九、給接手 Codex 的話

1. **先讀這份 AGENTS.md 全文**，再做任何事
2. 確認 user 連的資料夾就是 `/Users/yuukilin/Desktop/python/flight-tracker/`
3. 修改任何檔案前看一下 `git log --oneline -10` 了解最近改了什麼
4. push 衝突的處理已經在「資料持久化」設計裡解決，user 本地 push 應該永遠不衝突
5. 任何 LCC 漏網（傳統航空欄出現廉航名稱）→ 補 `excluded_airlines.yaml` 的 `name_keywords`
6. Bot 沒回應 → 第一個檢查 Cloudflare Worker logs；第二個檢查 webhook 狀態
7. Actions 失敗 → 看 log 的最後一個紅 X 步驟，貼錯誤訊息給 user

歡迎接手，希望這份檔讓你能無痛上工 ✈️

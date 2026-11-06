# 台股分析 App

用來長期追蹤 0050、0056 等台股的網頁 App，手機也能用。資料來自 FinMind（證交所、櫃買中心公開資料）。

## 部署到雲端（手機用）

### 1. 取得 FinMind token
到 https://finmindtrade.com 免費註冊，在會員頁面複製 API token。
雲端主機的 IP 是很多人共用的，沒有 token 很快就會被限制查詢次數，所以這一步一定要做。

### 2. 把程式放上 GitHub
1. 登入 GitHub，右上角「+」→「New repository」，名稱例如 `tw-stock-app`，選 Private 或 Public 都可以，按「Create repository」。
2. 在新 repo 的頁面點「uploading an existing file」，把資料夾裡的所有檔案拖進去，按「Commit changes」。
   - Mac 的 Finder 預設看不到 `.streamlit` 資料夾，看不到就略過，它不是必要的。
   - 不要上傳 `secrets.toml`（裡面會有 token 和密碼）。

### 3. 部署到 Streamlit Community Cloud（免費）
1. 到 https://share.streamlit.io ，用 GitHub 帳號登入。
2. 按「Create app」→ 選從 GitHub 部署，填入：
   - Repository：剛剛建立的 repo
   - Branch：`main`
   - Main file path：`app.py`
3. 打開「Advanced settings」：
   - Python version 選 3.12
   - Secrets 貼上下面兩行（改成你自己的值）：
     ```toml
     FINMIND_TOKEN = "你的 FinMind token"
     APP_PASSWORD = "自己設一組密碼"
     ```
     不想要密碼就只貼第一行。
4. 按「Deploy」，等幾分鐘就會得到一個 `https://xxx.streamlit.app` 的網址。

### 4. 加到手機主畫面
1. 用手機打開網址，在「自選股」分頁設定好清單（預設是 0050、0056）。
2. iPhone：Safari 分享按鈕 →「加入主畫面」。Android：Chrome 選單 →「加到主畫面」。
3. 自選股和目前選的股票存在網址裡，從主畫面打開就是同一份清單。

### 之後要更新程式
在 GitHub repo 上傳新版檔案覆蓋舊的，Streamlit Cloud 會在一兩分鐘內自動更新。

### 注意
- App 一段時間沒人用會休眠，下次打開要等幾十秒喚醒，按畫面上的按鈕即可。
- 知道網址的人都能打開，所以建議設密碼。

## 投資紀錄設定（Google 試算表）

App 用一份 Google 試算表存放交易紀錄、設定和通知狀態。需要建立一個 Google「服務帳號」讓 App 讀寫這份試算表，只要做一次。

### 1. 建立服務帳號與金鑰
1. 到 https://console.cloud.google.com ，用你的 Google 帳號登入，建立一個新專案（名稱隨意，例如 `tw-stock`）。
2. 在上方搜尋列搜尋「Google Sheets API」，進去後按「啟用」。再搜尋「Google Drive API」，同樣按「啟用」。
3. 左側選單「IAM 與管理」→「服務帳戶」→「建立服務帳戶」。名稱隨意填，其他步驟直接按「繼續」和「完成」，不用指定角色。
4. 點進剛建立的服務帳戶 →「金鑰」分頁 →「新增金鑰」→「建立新的金鑰」→ 選 JSON →「建立」。瀏覽器會下載一個 `.json` 檔。
   - 這個檔案等於密碼，不要上傳到 GitHub，也不要傳給別人。
5. 打開這個 JSON 檔，找到 `"client_email"` 那一行，複製後面的 email（長得像 `xxx@xxx.iam.gserviceaccount.com`）。

### 2. 建立試算表並共用
1. 到 https://sheets.google.com 建立一份空白試算表，名稱隨意。
2. 按右上角「共用」，貼上剛剛複製的服務帳戶 email，權限選「編輯者」，按「傳送」。
3. 複製試算表的網址。

App 第一次寫入時會自動建立「交易紀錄」「設定」「通知紀錄」三個工作表。你也可以直接在試算表裡修改紀錄。

### 3. 把設定貼到 Streamlit
到 share.streamlit.io → 你的 App 右邊「⋮」→「Settings」→「Secrets」，改成下面這樣（保留原本的兩行）：

```toml
FINMIND_TOKEN = "你的 FinMind token"
APP_PASSWORD = "你的密碼"
SHEET_URL = "https://docs.google.com/spreadsheets/d/....../edit"
GCP_SERVICE_ACCOUNT = '''
把下載的 JSON 檔全部內容貼在這裡（從第一個 { 到最後一個 }）
'''
```

注意 `GCP_SERVICE_ACCOUNT` 前後是**三個單引號**，JSON 原封不動貼上就好，不用改任何字。存檔後 App 會自動重新啟動。

## 收盤通知設定（Telegram）

每個平日晚上 9:30 左右（GitHub 排程可能延遲幾十分鐘），自動檢查自選股，以下情況會傳 Telegram 訊息給你：
- 某一檔的「本月建議」和上次不一樣（例如從「正常投入」變成「建議加碼」）
- 扣款日的前一個平日，提醒這個月依規則要投入多少錢、準備金會剩多少

通知用的是你在 App 裡**按「儲存設定」存下來的**規則、預算和自選股。

### 1. 建立 Telegram 機器人
1. 在 Telegram 搜尋 `@BotFather`，傳送 `/newbot`，依指示取名字。最後一步的帳號名稱要以 `bot` 結尾。
2. BotFather 會給你一串 token（像 `123456789:ABCdef...`），複製起來。
3. 搜尋你剛建立的機器人，按「開始」，隨便傳一則訊息給它。
4. 用瀏覽器打開 `https://api.telegram.org/bot<你的token>/getUpdates`（把 `<你的token>` 換掉），找到 `"chat":{"id":` 後面的數字，那就是你的 chat id。

### 2. 在 GitHub 設定 Secrets
到 GitHub repo →「Settings」→「Secrets and variables」→「Actions」→「New repository secret」，新增這五個：

| 名稱 | 內容 |
|---|---|
| `FINMIND_TOKEN` | FinMind token |
| `TELEGRAM_BOT_TOKEN` | BotFather 給的 token |
| `TELEGRAM_CHAT_ID` | 上面查到的 chat id |
| `SHEET_URL` | 試算表網址 |
| `GCP_SERVICE_ACCOUNT` | JSON 金鑰檔的全部內容（直接貼，不用加引號） |

Secrets 只有 GitHub Actions 讀得到，就算 repo 是公開的，別人也看不到。

### 3. 測試
到 repo 的「Actions」分頁 → 左邊選「收盤通知」→ 右邊「Run workflow」→ 按綠色按鈕。一兩分鐘後應該會收到訊息。
如果出現紅色叉叉，點進去看錯誤訊息。

### 注意
- 公開的 repo 如果 60 天都沒有任何更新，GitHub 會自動暫停排程並寄信通知你，到「Actions」分頁按一下重新啟用就好。
- 通知失敗時 GitHub 會寄 email 給你。

## 在自己電腦上執行

需要 Python 3.10 以上：

```bash
pip install -r requirements.txt
streamlit run app.py
```

Windows 也可以直接雙擊 `run.bat`。Token 可以貼在側邊欄，或設成環境變數 `FINMIND_TOKEN`。

## 功能

| 分頁 | 內容 |
|---|---|
| 分析 | 本月建議（建議加碼／正常投入／建議減碼）與倍數，依你設定的規則計算；年線位階（和過去 5 年比）、距近一年高點、長期趨勢、殖利率位階、和台積電的連動；年線乖離走勢圖；短線指標參考 |
| 圖表 | 日/週/月 K 線（可切換還原權息）、均線或布林通道、KD/RSI/MACD；配息紀錄與殖利率；個股另有本益比與月營收；三大法人買賣超 |
| 投資計畫 | 輸入預算（每月固定，或一筆錢分 N 個月），依分級規則算出本月建議與投入金額、約可買幾股、準備金變化；一鍵記錄本月投入；回測這套規則；穩健性檢查（從過去每個月分別開始，看分級投入有幾成機會贏過固定金額） |
| 我的持倉 | 記錄買進、賣出、準備金存入／動用；自動算出持有股數、平均成本、損益、預估已領配息、實際年化報酬、準備金餘額（需要連接 Google 試算表） |
| 自選股 | 多檔一起看年線乖離、乖離分位、距高點、殖利率、本月建議、RSI、KD |
| 短線回測 | 均線、MACD、KD 交叉策略，和買進持有比較 |

## 價格怎麼處理

- **分割**：0050 在 2025-06-18 一拆四。分割前的價格會自動換算成分割後的單位，圖表和指標才不會在那天出現假的暴跌。
- **還原權息**：位階、年線乖離和回測都把配息加回去計算。0056 這種高配息的 ETF，如果只看股價，會因為每次除息都往下掉而低估報酬。
- K 線圖預設顯示「只換算分割」的價格，和現在的報價一致；打開「還原權息價格」可以看含配息的走勢。

## 位階判斷規則

| 項目 | 偏便宜 | 偏貴 |
|---|---|---|
| 年線位階 | 年線乖離在過去 5 年的 25% 分位以下 | 75% 分位以上 |
| 近一年高點 | 從高點回檔 10% 以上 | 不判斷偏貴（長期上漲的標的常創新高） |
| 殖利率位階 | 殖利率在過去 5 年的 75% 分位以上 | 25% 分位以下 |

長期趨勢：季線在年線之上且年線上揚為多頭，兩者都相反為空頭，其他為整理。

## 投資計畫的分級規則

每個月依位階決定投入基本金額的幾倍（倍數可在 App 裡調整）：

| 級別 | 顯示的建議 | 判斷方式 | 預設倍數 |
|---|---|---|---|
| 大跌 | 建議大幅加碼 | 你設定的條件成立（從近一年高點回檔 X%，或低於年線 X%） | 2 |
| 偏便宜 | 建議加碼 | 年線乖離在過去 5 年的 25% 分位以下 | 1.5 |
| 中性 | 正常投入 | 其他情況 | 1 |
| 偏貴 | 建議減碼 | 年線乖離在過去 5 年的 75% 分位以上 | 0.75 |

- 倍數大於 1：多出來的錢從準備金拿，準備金不夠就只拿剩下的。
- 倍數小於 1：沒投入的錢存回準備金，留給之後便宜的時候用。
- 所以總投入不會超過預算。
- 連接 Google 試算表後，準備金餘額會從投資紀錄自動計算；沒有連接時要手動填。

App 顯示的建議和金額都是照你自己設定的規則計算，不是專業投資顧問的意見，也不保證未來的結果。

## 檔案

```
app.py          介面
notify.py       收盤通知（GitHub Actions 執行）
finmind.py      資料讀取
adjust.py       分割與還原權息
longterm.py     位階分析、分級規則、投資計畫回測
indicators.py   技術指標
backtest.py     短線策略回測
charts.py       圖表
portfolio.py    持倉、成本、損益、準備金
settings.py     規則與預算設定
storage.py      Google 試算表存取
.github/workflows/notify.yml   通知排程
```

本工具只整理數據，不構成投資建議。

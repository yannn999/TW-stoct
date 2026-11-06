"""台股分析網頁 App（手機也能用）

本機執行：streamlit run app.py
雲端部署：見 README.md
"""
from __future__ import annotations

import hmac
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

import adjust as adj
import backtest as bt
import charts
import finmind as fm
import indicators as ind
import longterm as lt

st.set_page_config(page_title="台股分析", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

HIST_DAYS = 365 * 8 + 120          # 抓 8 年：1 年算年線、再 1 年起算分位，回測還能有 5 年以上
DEFAULT_WATCH = ["0050", "0056"]
FREQ_LABEL = {"日K": "D", "週K": "W", "月K": "M"}
RANGE_BARS = {"3個月": 63, "6個月": 126, "1年": 250, "3年": 750, "全部": None}
BARS_PER = {"D": 1, "W": 5, "M": 21}
CFG = charts.CONFIG


# ================================================================ 設定與密碼

def secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:  # noqa: BLE001  沒有 secrets 檔案時會丟例外
        value = default
    return str(value) if value is not None else default


APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("📈 台股分析")
    entered = st.text_input("密碼", type="password", key="password")
    if entered:
        if hmac.compare_digest(entered.encode(), APP_PASSWORD.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("密碼不正確，請再試一次。")
    st.stop()


# ================================================================ 資料（含快取）

@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_stock_list(token: str) -> pd.DataFrame:
    return fm.stock_list(token)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_split_table(token: str) -> pd.DataFrame:
    try:
        return fm.split_table(HIST_DAYS, token)
    except fm.FinMindError:
        # 拿不到分割表也沒關係，adjust 會用跳空偵測
        return pd.DataFrame(columns=["stock_id", "before_price", "after_price"])


@st.cache_data(ttl=3600, show_spinner=False)
def load_adjusted(stock_id: str, token: str) -> adj.Adjusted | None:
    px = fm.price(stock_id, HIST_DAYS, token)
    if px.empty:
        return None
    notes = []
    try:
        div = fm.dividend_result(stock_id, HIST_DAYS, token)
    except fm.FinMindError as exc:
        div = None
        notes.append(f"讀不到除權息資料（{exc}），還原股價沒有加回配息。")
    tbl = load_split_table(token)
    a = adj.adjust(px, div, tbl[tbl["stock_id"] == stock_id])
    a.notes = notes + a.notes
    return a


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_valuation(stock_id: str, token: str) -> pd.DataFrame:
    return fm.valuation(stock_id, token=token)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def load_revenue(stock_id: str, token: str) -> pd.DataFrame:
    return fm.month_revenue(stock_id, token=token)


@st.cache_data(ttl=3600, show_spinner=False)
def load_institutional(stock_id: str, token: str) -> pd.DataFrame:
    return fm.institutional(stock_id, token=token)


def safe(fn, *args):
    """呼叫資料函式；失敗時顯示錯誤並回傳 None。"""
    try:
        return fn(*args)
    except fm.FinMindError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.error(f"讀取資料時發生錯誤：{exc}")
    return None


def corr_with_2330(stock_id: str, a: adj.Adjusted, token: str) -> float | None:
    """ETF 和台積電近一年日報酬的相關係數。"""
    if stock_id == "2330" or not stock_id.startswith("00"):
        return None
    try:
        ref = load_adjusted("2330", token)
    except Exception:  # noqa: BLE001
        return None
    if ref is None:
        return None
    j = pd.concat([a.total["close"].pct_change(), ref.total["close"].pct_change()], axis=1).dropna().tail(250)
    return float(j.corr().iloc[0, 1]) if len(j) > 60 else None


def yield_series(a: adj.Adjusted) -> pd.Series | None:
    return adj.trailing_yield(a.split["close"], a.dividends) if len(a.dividends) else None


def current_rule() -> lt.TierRule:
    """投資計畫分頁的設定；其他分頁也用同一份。還沒打開過該分頁時用預設值。"""
    ss = st.session_state
    dip = lt.DipRule(ss.get("rule_kind", "drawdown"), float(ss.get("rule_thr", 10.0)))
    mult = {t: float(ss.get(f"m_{t}", lt.DEFAULT_MULT[t])) for t in lt.TIERS}
    return lt.TierRule(mult=mult, dip=dip)


# ================================================================ 格式工具

def fmt_pct(v, digits=2) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:+.{digits}f}%"


def fmt_num(v, digits=2) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.{digits}f}"


def color_updown(v):
    if not isinstance(v, (int, float)) or pd.isna(v) or v == 0:
        return ""
    return f"color: {charts.UP}" if v > 0 else f"color: {charts.DOWN}"


def stat_grid(items: list[tuple[str, str, str | None]]) -> None:
    """手機也維持三欄的小指標格。items：(標題, 數值, 副標)；副標以 + 或 - 開頭時用紅/綠色。"""
    cells = []
    for label, value, sub in items:
        sub_html = ""
        if sub:
            color = charts.UP if sub.startswith("+") else charts.DOWN if sub.startswith("-") else "inherit"
            sub_html = f'<div style="font-size:.78rem;color:{color};opacity:.9">{sub}</div>'
        cells.append(
            '<div style="padding:.5rem .6rem;border:1px solid rgba(128,128,128,.25);border-radius:.5rem;min-width:0">'
            f'<div style="font-size:.78rem;opacity:.7">{label}</div>'
            f'<div style="font-size:1.25rem;font-weight:600;font-variant-numeric:tabular-nums;'
            f'overflow-wrap:anywhere">{value}</div>{sub_html}</div>')
    st.markdown('<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.5rem">'
                + "".join(cells) + "</div>", unsafe_allow_html=True)


# ================================================================ 側邊欄

TOKEN_SECRET = secret("FINMIND_TOKEN") or os.environ.get("FINMIND_TOKEN", "")
with st.sidebar:
    st.header("設定")
    if TOKEN_SECRET:
        token = TOKEN_SECRET
        st.caption("FinMind Token：已從設定檔讀取。")
    else:
        token = st.text_input("FinMind Token（選填）", type="password",
                              help="不填也能用，但查詢次數較少。到 finmindtrade.com 免費註冊即可取得。")
    if st.button("重新抓取資料", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("資料來源：FinMind（證交所、櫃買中心公開資料）。股價在每個交易日收盤後更新，"
               "法人資料約在晚上 8 點後更新。")
    st.caption("本工具只整理數據，不構成投資建議。")


# ================================================================ 選股票

st.markdown("### 📈 台股分析")
stocks = safe(load_stock_list, token)
name_by_id, industry_by_id, labels, id_by_label = {}, {}, [], {}
default_id = st.query_params.get("s", "0050")
if stocks is not None and not stocks.empty:
    stocks = stocks.sort_values("stock_id")
    labels = (stocks["stock_id"] + " " + stocks["stock_name"]).tolist()
    id_by_label = dict(zip(labels, stocks["stock_id"]))
    name_by_id = dict(zip(stocks["stock_id"], stocks["stock_name"]))
    industry_by_id = dict(zip(stocks["stock_id"], stocks["industry_category"]))
    default_label = next((l for l in labels if l.split(" ")[0] == default_id), labels[0])
    chosen = st.selectbox("股票（可輸入代號或名稱搜尋）", labels, index=labels.index(default_label),
                          key="stock_select")
    stock_id = id_by_label[chosen]
else:
    stock_id = st.text_input("股票代號", value=default_id, key="stock_text").strip()
st.query_params["s"] = stock_id
stock_name = name_by_id.get(stock_id, "")
is_etf = stock_id.startswith("00")

try:
    with st.spinner("讀取資料…"):
        a = load_adjusted(stock_id, token)
except fm.FinMindError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.error(f"讀取資料時發生錯誤：{exc}")
    st.stop()
if a is None:
    st.warning(f"找不到 {stock_id} 的股價資料，請確認代號。")
    st.stop()

feat = lt.features(a.total)
yld = yield_series(a)
daily_split = ind.add_all(a.split)
last_split = daily_split.iloc[-1]
prev_split = daily_split.iloc[-2] if len(daily_split) > 1 else last_split
last_feat = feat.iloc[-1]
rule = current_rule()

tab_an, tab_plan, tab_chart, tab_watch, tab_bt = st.tabs(
    ["分析", "投資計畫", "圖表", "自選股", "短線回測"])

# ================================================================ 分析

with tab_an:
    st.markdown(f"#### {stock_id} {stock_name}")
    st.caption(f"{industry_by_id.get(stock_id, '')}　資料日 {a.split.index[-1]:%Y-%m-%d}")

    chg = last_split["close"] - prev_split["close"]
    chg_pct = chg / prev_split["close"] * 100 if prev_split["close"] else 0
    yld_txt = f"{yld.dropna().iloc[-1]:.2f}%" if yld is not None and yld.notna().any() else "—"
    stat_grid([
        ("收盤", fmt_num(last_split["close"]), f"{chg:+.2f}（{chg_pct:+.2f}%）"),
        ("年線乖離", fmt_pct(last_feat["bias"], 1), None),
        ("距近一年高點", fmt_pct(last_feat["dd"], 1), None),
        ("近12月殖利率", yld_txt, None),
        ("成交量（張）", f"{last_split['volume']:,.0f}", None),
        ("年線", fmt_num(last_split.get("MA240")), None),
    ])
    st.caption("台股慣例：紅色代表上漲、綠色代表下跌。年線乖離和距高點都用還原權息價格計算。")

    # ---- 依你的規則，目前屬於哪一級
    if pd.notna(last_feat["bias"]) or pd.notna(last_feat["dd"]):
        t = rule.tier(last_feat)
        m = rule.mult[t]
        msg = (f"**本月建議：{lt.TIER_ACTION[t]}**（位階：{lt.TIER_LABEL[t]}，投入基本金額的 {m:g} 倍）  \n"
               + "  \n".join(f"・{r}" for r in rule.reasons(last_feat)))
        (st.success if t in ("dip", "cheap") else st.warning if t == "expensive" else st.info)(msg)
        st.caption("這個建議是依你在「投資計畫」分頁設定的規則算出來的，規則和倍數可以在那裡調整，"
                   "那裡也會算出這個月要投入多少錢。")

    # ---- 規則式分析
    findings = lt.analyze(feat, yld, corr_with_2330(stock_id, a, token))
    counts = {k: sum(f.state == k for f in findings) for k in ("cheap", "neutral", "expensive")}
    st.markdown(f"##### 位階摘要：偏便宜 {counts['cheap']} 項、中性 {counts['neutral']} 項、"
                f"偏貴 {counts['expensive']} 項")
    for f in findings:
        label, color = lt.STATE_LABEL[f.state]
        with st.container(border=True):
            st.markdown(f"**{f.name}**　:{color}-badge[{label}]  \n{f.headline}")
            if f.detail:
                st.caption(f.detail)

    if feat["bias"].notna().sum() > 60:
        st.markdown("##### 年線乖離走勢")
        st.plotly_chart(charts.position(feat.loc[feat.index[-1] - pd.DateOffset(years=5):]),
                        width="stretch", config=CFG)
        st.caption("虛線是過去 5 年的 25% 與 75% 分位。藍線越靠近下方虛線，代表相對年線越便宜。")

    # ---- 短線參考
    with st.expander("短線指標（參考用）"):
        notes = []
        for n in (5, 20, 60):
            ma = last_split.get(f"MA{n}")
            if pd.notna(ma):
                notes.append(f"收盤 {'站上' if last_split['close'] > ma else '跌破'} MA{n}（{ma:,.2f}）")
        if pd.notna(last_split["K"]):
            k, d = last_split["K"], last_split["D"]
            zone = "高檔區（>80）" if k > 80 else "低檔區（<20）" if k < 20 else "中間區"
            notes.append(f"KD：K={k:.1f}、D={d:.1f}，{'K>D' if k > d else 'K<D'}，位於{zone}")
        if pd.notna(last_split["RSI"]):
            r = last_split["RSI"]
            notes.append(f"RSI(14)={r:.1f}" + ("，偏熱（>70）" if r > 70 else "，偏冷（<30）" if r < 30 else ""))
        if pd.notna(last_split["OSC"]):
            notes.append(f"MACD 柱狀體{'為正' if last_split['OSC'] > 0 else '為負'}"
                         f"（{'擴大' if abs(last_split['OSC']) > abs(prev_split['OSC']) else '縮小'}中）")
        st.markdown("\n".join(f"- {n}" for n in notes))
        st.caption("短線指標反映的是幾天到幾週的走勢，對長期持有只是參考。")

    for n in a.notes:
        st.caption(f"資料說明：{n}")
    st.caption("以上只描述數據的相對位置，不代表買賣建議。位階便宜不保證不會繼續跌，偏貴也可能繼續漲。")

# ================================================================ 圖表

with tab_chart:
    c1, c2 = st.columns(2)
    freq_label = c1.radio("週期", list(FREQ_LABEL), horizontal=True, key="freq")
    span = c2.select_slider("顯示範圍", options=list(RANGE_BARS), value="1年", key="span")
    c3, c4 = st.columns(2)
    overlay = c3.radio("主圖", ["均線", "布林通道", "無"], horizontal=True, key="overlay")
    panels = c4.multiselect("副圖", ["KD", "RSI", "MACD"], default=["KD"], key="panels")
    use_total = st.toggle("還原權息價格", value=False, key="use_total",
                          help="關閉：和現在報價一致（已換算分割）。開啟：把歷年配息加回去，看真正的長期報酬。")

    freq = FREQ_LABEL[freq_label]
    base = a.total if use_total else a.split
    bars = ind.add_all(ind.resample(base, freq))
    n = RANGE_BARS[span]
    view = bars.tail(max(n // BARS_PER[freq], 12)) if n else bars
    st.plotly_chart(charts.kline(view, freq, overlay, panels), width="stretch", config=CFG)

    if len(a.dividends):
        st.markdown("##### 配息與殖利率")
        st.plotly_chart(charts.dividend_yield(yld, a.dividends), width="stretch", config=CFG)
        recent = a.dividends.sort_index(ascending=False).head(8)
        tbl = pd.DataFrame({
            "除息日": [d.strftime("%Y-%m-%d") for d in recent.index],
            "每股配息（元）": recent.to_numpy(),
            "除息前收盤": [a.split["close"].iloc[a.split.index.get_loc(d) - 1] for d in recent.index],
        })
        tbl["單次殖利率%"] = tbl["每股配息（元）"] / tbl["除息前收盤"] * 100
        st.dataframe(tbl.style.format({"每股配息（元）": "{:.3f}", "除息前收盤": "{:,.2f}",
                                       "單次殖利率%": "{:.2f}"}), width="stretch", hide_index=True)
        if a.splits:
            st.caption("配息金額已換算成分割後的每股金額，方便和現在的股價比較。")

    if not is_etf:
        st.markdown("##### 本益比與殖利率")
        val = safe(load_valuation, stock_id, token)
        if val is not None and not val.empty:
            st.plotly_chart(charts.valuation(val), width="stretch", config=CFG)
            per = val["PER"][val["PER"] > 0]
            if len(per):
                st.caption(f"近三年本益比：最低 {per.min():.1f}、中位數 {per.median():.1f}、最高 {per.max():.1f}；"
                           f"目前 {per.iloc[-1]:.1f}，位於 {(per < per.iloc[-1]).mean() * 100:.0f}% 分位")
        else:
            st.info("沒有本益比資料。")
        st.markdown("##### 月營收")
        rev = safe(load_revenue, stock_id, token)
        if rev is not None and not rev.empty:
            st.plotly_chart(charts.revenue(rev), width="stretch", config=CFG)
            r = rev.iloc[-1]
            st.caption(f"最新 {rev.index[-1]:%Y 年 %m 月}：{r['revenue'] / 1e8:,.2f} 億元，"
                       f"月增 {fmt_pct(r['mom'], 1)}、年增 {fmt_pct(r['yoy'], 1)}")
        else:
            st.info("沒有月營收資料。")

    st.markdown("##### 三大法人買賣超")
    inst = safe(load_institutional, stock_id, token)
    if inst is not None and not inst.empty:
        st.plotly_chart(charts.institutional(inst.tail(60), a.split["close"]), width="stretch", config=CFG)
        summary = pd.DataFrame({"近1日": inst.tail(1).sum(), "近5日": inst.tail(5).sum(),
                                "近20日": inst.tail(20).sum()}).T
        st.dataframe(summary.style.format("{:+,.0f}").map(color_updown), width="stretch")
        st.caption("單位：張。正數為買超、負數為賣超。")
    else:
        st.info("沒有法人買賣資料。")

# ================================================================ 投資計畫

with tab_plan:
    st.markdown("#### 投資計畫")
    st.caption(f"{stock_id} {stock_name}　預算只算這一檔。")

    # ---- 1. 預算
    st.markdown("**1. 預算**")
    mode = st.radio("預算形式", ["每月固定", "一筆分批"], horizontal=True, key="plan_mode",
                    help="每月固定：每個月拿出一樣的錢，另有一筆準備金。一筆分批：手上有一筆錢，分 N 個月投入，其中一部分當準備金。")
    if mode == "每月固定":
        q1, q2 = st.columns(2)
        base = float(q1.number_input("每月基本金額（元）", 1000, 10_000_000, 5000, step=1000, key="plan_monthly"))
        reserve_init = float(q2.number_input("準備金（元）", 0, 100_000_000, 20000, step=1000, key="plan_reserve",
                                             help="另外存著、專門用來逢低多投的錢。"))
        months = None
        reserve_now = reserve_init
    else:
        q1, q2, q3 = st.columns(3)
        total = float(q1.number_input("總金額（元）", 10_000, 100_000_000, 300_000, step=10_000, key="plan_total"))
        months = int(q2.number_input("分幾個月", 2, 120, 12, key="plan_months"))
        res_pct = float(q3.number_input("準備金比例 %", 0, 90, 30, step=5, key="plan_res_pct"))
        reserve_init = total * res_pct / 100
        base = (total - reserve_init) / months
        st.caption(f"每月基本金額 {base:,.0f} 元 × {months} 個月，準備金 {reserve_init:,.0f} 元。")
        reserve_now = float(st.number_input(
            "目前準備金餘額（元）", 0.0, 100_000_000.0, reserve_init, step=1000.0,
            help="第一個月就是上面算出的準備金。之後每個月照實際剩下的金額修改，本月計畫才會正確。"))

    # ---- 2. 分級規則
    st.markdown("**2. 分級規則**")
    k1, k2 = st.columns(2)
    kind = k1.radio("「大跌」的條件", list(lt.RULE_KINDS), format_func=lt.RULE_KINDS.get,
                    horizontal=True, key="rule_kind")
    thr_label = "回檔幅度 %" if kind == "drawdown" else "低於年線幅度 %（0 = 跌破就算）"
    k2.number_input(thr_label, 0.0, 60.0, 10.0, step=1.0, key="rule_thr")
    mcols = st.columns(4)
    for col, t in zip(mcols, lt.TIERS):
        col.number_input(f"{lt.TIER_LABEL[t]} 倍數", 0.0, 5.0, lt.DEFAULT_MULT[t], step=0.25, key=f"m_{t}")
    rule = current_rule()
    st.caption("判斷順序：先看「大跌」條件；不成立的話，看年線乖離在過去 5 年的分位："
               "25% 以下為偏便宜、75% 以上為偏貴，中間為中性。"
               "倍數大於 1 時多出來的錢從準備金拿（不夠就只拿剩下的），小於 1 時沒投的錢存回準備金。")

    # ---- 3. 本月計畫
    st.markdown("**3. 本月計畫**")
    if pd.isna(last_feat["bias"]) and pd.isna(last_feat["dd"]):
        st.info("這檔的資料不到一年，還沒辦法判斷位階。")
    else:
        t = rule.tier(last_feat)
        m = rule.mult[t]
        amt, d_res = lt.month_amount(base, reserve_now, m)
        px_now = last_split["close"]
        n_sh = int(amt // (px_now * (1 + lt.FEE_RATE)))
        stat_grid([
            ("本月建議", lt.TIER_ACTION[t], f"位階：{lt.TIER_LABEL[t]}"),
            ("倍數", f"{m:g} 倍", None),
            ("本月投入", f"{amt:,.0f} 元", None),
            ("約可買", f"{n_sh:,} 股", f"以收盤 {px_now:,.2f} 計"),
            ("準備金變化", f"{d_res:+,.0f} 元", None),
            ("準備金剩下", f"{reserve_now + d_res:,.0f} 元", None),
        ])
        lines = rule.reasons(last_feat)
        if m > 1 and amt < base * m:
            lines.append(f"規則要投 {base * m:,.0f} 元，但準備金只剩 {reserve_now:,.0f} 元，所以只能投 {amt:,.0f} 元。")
        st.markdown("\n".join(f"- {x}" for x in lines))
        st.caption(f"依據 {a.split.index[-1]:%Y-%m-%d} 收盤資料，依你設定的規則計算。"
                   "規則本身是否合適，可以參考下面的回測；位階便宜不代表之後不會再跌。")

    # ---- 4. 回測
    st.markdown("**4. 這套規則過去的表現**")
    valid = feat["bias_pct"].dropna()
    min_start = (valid.index[0] if len(valid) else a.split.index[-1]).date()
    need_days = 31 * (months or 2) + 10
    max_start = (a.split.index[-1] - pd.Timedelta(days=need_days)).date()
    if min_start >= max_start:
        st.info("歷史資料不夠長，沒辦法回測這個設定。")
    else:
        r1c, r2c = st.columns(2)
        start = r1c.date_input("回測開始日",
                               value=min(max(min_start, date.today() - timedelta(days=365 * 5)), max_start),
                               min_value=min_start, max_value=max_start, key="plan_start")
        day = r2c.number_input("每月扣款日", 1, 28, 6, key="plan_day")
        reinvest = st.checkbox("配息再投入", value=True, key="plan_reinvest")
        with st.expander("手續費設定"):
            f1, f2 = st.columns(2)
            disc = f1.number_input("手續費折扣", 0.1, 1.0, 1.0, step=0.05, key="plan_disc",
                                   help="1 = 不打折；6 折填 0.6")
            min_fee = f2.number_input("每筆最低手續費（元）", 0, 20, 1, key="plan_minfee",
                                      help="許多券商的定期定額最低收 1 元，一般交易最低 20 元。")
        costs = lt.Costs(fee_discount=disc, min_fee=min_fee, reinvest=reinvest, day=int(day))
        price = a.split["close"]
        sim = lambda **kw: lt.simulate_plan(price, a.dividends, feat, start, costs, **kw)  # noqa: E731
        try:
            if mode == "每月固定":
                results = [
                    sim(name="分級投入", upfront=reserve_init, base=base, monthly_new=base,
                        reserve0=reserve_init, rule=rule),
                    sim(name="固定金額（準備金不動）", upfront=reserve_init, base=base, monthly_new=base,
                        reserve0=reserve_init),
                    sim(name="固定金額＋準備金一開始投入", upfront=reserve_init, base=base, monthly_new=base,
                        reserve0=0.0, lump_first=True),
                ]
                note = ("三種方式的錢一樣多：每月同樣的基本金額，加上開始日就準備好的準備金。"
                        "差別在準備金怎麼用。")
            else:
                results = [
                    sim(name="分級投入", upfront=total, base=base, monthly_new=0, reserve0=reserve_init,
                        months=months, rule=rule),
                    sim(name=f"平均分 {months} 個月", upfront=total, base=total / months, monthly_new=0,
                        reserve0=0.0, months=months),
                    sim(name="開始日一次投入", upfront=total, base=0, monthly_new=0, reserve0=0.0,
                        lump_first=True),
                ]
                note = (f"三種方式都是同一筆 {total:,.0f} 元。分級投入在 {months} 個月後如果準備金還有剩，"
                        "會以現金留著（列在「未投入現金」）。")
        except ValueError as exc:
            st.warning(str(exc))
        else:
            comp = pd.DataFrame({r.name: r.stats for r in results}).T
            fmt = {c: "{:,.0f}" for c in comp.columns}
            fmt.update({"總報酬%": "{:+.2f}", "年化報酬%": "{:+.2f}", "最大帳面虧損%": "{:.2f}"})
            st.dataframe(comp.style.format(fmt, na_rep="—").map(color_updown, subset=["總報酬%", "年化報酬%"]),
                         width="stretch")
            st.caption(note + " 年化報酬用 XIRR 計算，會考慮每筆錢投入的時間點。"
                       "期末總值包含持股市值和還沒投入的現金。最大帳面虧損 = 期間內總值低於累計投入最多的幅度。")
            st.plotly_chart(charts.dca(results), width="stretch", config=CFG)

            tiered = results[0]
            st.markdown("##### 分級投入的每月位階")
            st.plotly_chart(charts.tier_marks(price.loc[pd.Timestamp(start):], tiered.events),
                            width="stretch", config=CFG)
            with st.expander("分級投入的每筆紀錄"):
                if not tiered.events.empty:
                    ev = tiered.events.sort_values("日期", ascending=False).copy()
                    ev["日期"] = ev["日期"].astype(str)
                    st.dataframe(ev.style.format({"金額": "{:,.0f}", "價格": "{:,.2f}", "股數": "{:,}",
                                                  "準備金餘額": "{:,.0f}"}),
                                 width="stretch", hide_index=True)
            st.caption("回測假設：扣款日遇假日順延，以當天收盤價買整數股，剩下的零錢留到下次。"
                       "位階用前一個交易日的收盤判斷，分位只用當天以前 5 年的資料。"
                       "配息在除息日以收盤價再投入（實際入帳約晚一個月）。"
                       "沒有計入配息的所得稅與二代健保補充保費。過去績效不代表未來結果。")

# ================================================================ 自選股

with tab_watch:
    saved = [s for s in st.query_params.get("w", ",".join(DEFAULT_WATCH)).split(",") if s]
    if labels:
        label_by_id = {v: k for k, v in id_by_label.items()}
        picked = st.multiselect("自選股", labels, default=[label_by_id[s] for s in saved if s in label_by_id],
                                key="watch_select")
        watch = [id_by_label[p] for p in picked]
    else:
        text = st.text_input("自選股代號（用逗號分隔）", ",".join(saved), key="watch_text")
        watch = [s.strip() for s in text.split(",") if s.strip()]
    st.query_params["w"] = ",".join(watch)
    st.caption("自選股存在網址裡。設定好之後把這個網址加到手機主畫面，下次打開就是同一份清單。")

    if not watch:
        st.info("還沒有自選股，從上方選單加入。")
    else:
        rows = []
        bar = st.progress(0.0, text="讀取自選股…")
        for i, sid in enumerate(watch):
            try:
                w = load_adjusted(sid, token)
            except fm.FinMindError as exc:
                st.error(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                st.warning(f"{sid} 讀取失敗：{exc}")
                w = None
            if w is not None and len(w.split) > 25:
                wf = lt.features(w.total)
                wl = wf.iloc[-1]
                snap = ind.snapshot(ind.add_all(w.split.tail(300)))
                wy = yield_series(w)
                rows.append({
                    "代號": sid, "名稱": name_by_id.get(sid, ""),
                    "收盤": snap["收盤"], "漲跌%": snap["漲跌%"],
                    "年線乖離%": wl["bias"], "乖離分位": wl["bias_pct"],
                    "距高點%": wl["dd"],
                    "殖利率%": wy.dropna().iloc[-1] if wy is not None and wy.notna().any() else np.nan,
                    "本月建議": f"{lt.TIER_ACTION[rule.tier(wl)]} ×{rule.mult[rule.tier(wl)]:g}",
                    "RSI": snap["RSI"], "KD": snap["KD"],
                })
            bar.progress((i + 1) / len(watch), text=f"讀取自選股…{sid}")
        bar.empty()

        if rows:
            table = pd.DataFrame(rows).set_index("代號")
            st.dataframe(
                table.style.format({"收盤": "{:,.2f}", "漲跌%": "{:+.2f}", "年線乖離%": "{:+.1f}",
                                    "乖離分位": "{:.0f}", "距高點%": "{:+.1f}", "殖利率%": "{:.2f}",
                                    "RSI": "{:.1f}"}, na_rep="—")
                .map(color_updown, subset=["漲跌%"]),
                width="stretch", height=min(600, 40 + 36 * len(table)))
            st.caption(f"乖離分位：目前的年線乖離比過去 5 年中多少 % 的日子高（越低越便宜）。"
                       "本月建議：依你在「投資計畫」分頁設定的規則，這個月該加碼、正常投入或減碼，以及投入基本金額的幾倍。"
                       "要看單一檔的詳細分析，請在最上方選擇該股票。")

# ================================================================ 短線回測

with tab_bt:
    st.markdown("#### 短線策略回測")
    st.caption(f"{stock_id} {stock_name}")
    st.caption("規則：收盤出現訊號，隔天開盤價全額進場或出場；只做多、不融資。使用還原權息價格，配息已計入報酬。")
    daily = ind.add_all(a.total)

    b1, b2, b3 = st.columns(3)
    strategy = b1.selectbox("策略", ["均線交叉", "MACD 交叉", "KD 交叉"], key="strategy")
    signal, desc = None, ""
    if strategy == "均線交叉":
        short = b2.number_input("短均線（日）", 2, 120, 5, key="bt_short")
        long_ = b3.number_input("長均線（日）", 5, 240, 20, key="bt_long")
        if short < long_:
            signal = bt.signals_ma(daily, int(short), int(long_))
            desc = f"MA{short} 由下往上穿過 MA{long_} 買進，由上往下穿過時賣出。"
        else:
            st.warning("短均線要小於長均線。")
    elif strategy == "MACD 交叉":
        signal = bt.signals_macd(daily)
        desc = "DIF 由下往上穿過 MACD 線買進，由上往下穿過時賣出。"
    else:
        level = b2.slider("KD 分界", 20, 80, 50, key="bt_level")
        signal = bt.signals_kd(daily, level)
        desc = f"K 值低於 {level} 時黃金交叉買進，高於 {level} 時死亡交叉賣出。"
    if desc:
        st.caption(desc)

    first_ok = daily.dropna(subset=["MA60", "MACD"]).index.min()
    min_s = first_ok.date() if pd.notna(first_ok) else daily.index[0].date()
    max_s = (daily.index[-1] - pd.Timedelta(days=30)).date()
    if signal is not None and min_s < max_s:
        d1, d2 = st.columns(2)
        bt_start = d1.date_input("回測起始日",
                                 value=min(max(min_s, date.today() - timedelta(days=365 * 2)), max_s),
                                 min_value=min_s, max_value=max_s, key="bt_start")
        capital = d2.number_input("本金（元）", 10_000, 100_000_000, 100_000, step=10_000, key="bt_capital")
        d3, d4 = st.columns(2)
        discount = d3.number_input("手續費折扣", 0.1, 1.0, 0.6, step=0.05, key="bt_disc",
                                   help="券商常見 6 折 = 0.6；不打折填 1")
        etf_tax = d4.checkbox("ETF（證交稅 0.1%）", value=is_etf, key="bt_etf")
        try:
            res = bt.run(daily, signal, pd.Timestamp(bt_start), capital=capital,
                         costs=bt.Costs(fee_discount=discount, tax_rate=0.001 if etf_tax else 0.003))
        except ValueError as exc:
            st.warning(str(exc))
        else:
            s = res.stats
            diff = s["總報酬%"] - s["買進持有報酬%"]
            stat_grid([
                ("策略總報酬", fmt_pct(s["總報酬%"]), f"{diff:+.2f}% vs 買進持有"),
                ("年化報酬", fmt_pct(s["年化報酬%"]), None),
                ("最大回撤", f"{s['最大回撤%']:.2f}%", f"買進持有 {s['買進持有最大回撤%']:.2f}%"),
                ("交易次數", f"{s['交易次數']} 次" + ("（持有中）" if s["持有中"] else ""), None),
                ("勝率", "—" if pd.isna(s["勝率%"]) else f"{s['勝率%']:.0f}%", None),
                ("買進持有報酬", fmt_pct(s["買進持有報酬%"]), None),
            ])

            st.plotly_chart(charts.equity(res.equity, res.benchmark), width="stretch", config=CFG)
            st.plotly_chart(charts.trades_on_price(daily.loc[pd.Timestamp(bt_start):], res.trades),
                            width="stretch", config=CFG)
            if not res.trades.empty:
                t = res.trades.copy()
                t["買進日"] = t["買進日"].astype(str)
                t["賣出日"] = t["賣出日"].map(lambda d: "持有中" if d is None or pd.isna(d) else str(d))
                st.dataframe(t.style.format({"買進價": "{:,.2f}", "賣出價": "{:,.2f}", "股數": "{:,}",
                                             "損益": "{:+,.0f}", "報酬%": "{:+.2f}"})
                             .map(color_updown, subset=["損益", "報酬%"]),
                             width="stretch", hide_index=True)
            else:
                st.info("回測期間內沒有出現買進訊號。")
            st.caption("已扣除手續費（最低 20 元）與證交稅，沒有計入滑價。"
                       "因為用還原權息價格，早期的成交價會比當時實際的報價低。過去績效不代表未來結果。")
    elif signal is not None:
        st.info("歷史資料太短，沒辦法回測。")

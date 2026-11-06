"""FinMind API 資料存取層。

所有函式回傳 pandas DataFrame，日期欄位已轉成 datetime，數量單位已換成「張」。
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import requests

API_URL = "https://api.finmindtrade.com/api/v4/data"
TIMEOUT = 20


class FinMindError(RuntimeError):
    """API 回傳錯誤（額度用完、參數錯誤、網路問題）。"""


def _token() -> str:
    return os.environ.get("FINMIND_TOKEN", "").strip()


def _get(dataset: str, data_id: str | None = None,
         start_date: str | None = None, end_date: str | None = None,
         token: str | None = None) -> pd.DataFrame:
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    tok = (token or _token()).strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}

    try:
        resp = requests.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise FinMindError(f"連不上 FinMind：{exc}") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise FinMindError(f"FinMind 回傳格式錯誤（HTTP {resp.status_code}）") from exc

    status = payload.get("status", resp.status_code)
    if status != 200:
        msg = payload.get("msg", "未知錯誤")
        if status == 402 or "limit" in str(msg).lower():
            raise FinMindError(
                "FinMind 免費額度已用完。請到 finmindtrade.com 免費註冊取得 token，"
                "填在側邊欄的「FinMind Token」即可提高額度。")
        raise FinMindError(f"FinMind 錯誤（{status}）：{msg}")

    return pd.DataFrame(payload.get("data", []))


def _start(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- 個別資料集

def stock_list(token: str | None = None) -> pd.DataFrame:
    """上市、上櫃、興櫃股票清單：stock_id, stock_name, industry_category, type。"""
    df = _get("TaiwanStockInfo", token=token)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # 同一檔可能有多列（例如興櫃轉上市），保留最新那列
    df = df.sort_values("date").drop_duplicates("stock_id", keep="last")
    return df[["stock_id", "stock_name", "industry_category", "type"]].reset_index(drop=True)


def price(stock_id: str, days: int = 800, token: str | None = None) -> pd.DataFrame:
    """日 K：index=date，欄位 open/high/low/close/volume(張)/value(元)。"""
    df = _get("TaiwanStockPrice", stock_id, _start(days), token=token)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"max": "high", "min": "low",
                            "Trading_Volume": "volume", "Trading_money": "value"})
    # 沒有成交的日子 FinMind 會給 0，去掉以免畫出跌到 0 的 K 棒
    df = df[df["close"] > 0]
    df["volume"] = df["volume"] / 1000  # 股 → 張
    return df.set_index("date")[["open", "high", "low", "close", "volume", "value"]].sort_index()


def valuation(stock_id: str, days: int = 1100, token: str | None = None) -> pd.DataFrame:
    """本益比、殖利率、股價淨值比：index=date，欄位 PER/PBR/dividend_yield。"""
    df = _get("TaiwanStockPER", stock_id, _start(days), token=token)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["PER", "PBR", "dividend_yield"]].sort_index()


def month_revenue(stock_id: str, years: int = 4, token: str | None = None) -> pd.DataFrame:
    """月營收：index=營收所屬月份（月初），欄位 revenue(元)/mom(%)/yoy(%)。"""
    df = _get("TaiwanStockMonthRevenue", stock_id, _start(365 * years + 400), token=token)
    if df.empty:
        return df
    df["period"] = pd.to_datetime(
        dict(year=df["revenue_year"], month=df["revenue_month"], day=1))
    df = df.drop_duplicates("period", keep="last").set_index("period").sort_index()
    out = df[["revenue"]].astype(float)
    out["mom"] = out["revenue"].pct_change() * 100
    # 用日期對齊算年增率，避免中間缺月時對錯月份
    last_year = out["revenue"].copy()
    last_year.index = last_year.index + pd.DateOffset(years=1)
    out["yoy"] = (out["revenue"] / last_year.reindex(out.index) - 1) * 100
    cutoff = out.index.max() - pd.DateOffset(years=years)
    return out[out.index > cutoff]


def dividend_result(stock_id: str, days: int = 2400, token: str | None = None) -> pd.DataFrame:
    """除權除息結果：index=除權息日，欄位 before_price/reference_price/kind。"""
    df = _get("TaiwanStockDividendResult", stock_id, _start(days), token=token)
    cols = ["before_price", "reference_price", "kind"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    df["kind"] = df["stock_or_cache_dividend"].astype(str)
    for c in ("before_price", "reference_price"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["before_price"] > 0) & (df["reference_price"] > 0)]
    return df.set_index("date")[cols].sort_index()


def split_table(days: int = 2400, token: str | None = None) -> pd.DataFrame:
    """所有股票的分割紀錄：index=分割日期，欄位 stock_id/before_price/after_price。"""
    df = _get("TaiwanStockSplitPrice", None, _start(days), token=token)
    cols = ["stock_id", "before_price", "after_price"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    for c in ("before_price", "after_price"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["before_price"] > 0) & (df["after_price"] > 0)]
    return df.set_index("date")[cols].sort_index()


INSTITUTION_GROUP = {
    "Foreign_Investor": "外資",
    "Foreign_Dealer_Self": "外資",
    "Investment_Trust": "投信",
    "Dealer": "自營商",
    "Dealer_self": "自營商",
    "Dealer_Hedging": "自營商",
}


def institutional(stock_id: str, days: int = 120, token: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（張）：index=date，欄位 外資/投信/自營商/合計。"""
    df = _get("TaiwanStockInstitutionalInvestorsBuySell", stock_id, _start(days), token=token)
    cols = ["外資", "投信", "自營商"]
    if df.empty:
        return pd.DataFrame(columns=cols + ["合計"])
    df["date"] = pd.to_datetime(df["date"])
    df["group"] = df["name"].map(INSTITUTION_GROUP)
    df = df.dropna(subset=["group"])
    df["net"] = (df["buy"] - df["sell"]) / 1000  # 股 → 張
    out = df.pivot_table(index="date", columns="group", values="net", aggfunc="sum")
    out = out.reindex(columns=cols).fillna(0).sort_index()
    out["合計"] = out[cols].sum(axis=1)
    return out

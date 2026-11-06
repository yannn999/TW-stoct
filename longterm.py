"""長期持有用的位階分析與定期定額回測。

位階一律用還原權息價格計算，配息多的 ETF（例如 0056）才不會因為除息而看起來一直在跌。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

YEAR_BARS = 240        # 年線
HIGH_BARS = 252        # 近一年高點
LOOKBACK_YEARS = 5     # 歷史分位比較的年數
FEE_RATE = 0.001425


# ================================================================ 位階特徵

def features(total: pd.DataFrame) -> pd.DataFrame:
    c = total["close"]
    ma240 = c.rolling(YEAR_BARS, min_periods=YEAR_BARS).mean()
    ma60 = c.rolling(60, min_periods=60).mean()
    return pd.DataFrame({
        "close": c,
        "MA60": ma60,
        "MA240": ma240,
        "bias": (c / ma240 - 1) * 100,                                          # 年線乖離 %
        "dd": (c / c.rolling(HIGH_BARS, min_periods=60).max() - 1) * 100,       # 距近一年高點 %
        "ma240_slope": (ma240 / ma240.shift(20) - 1) * 100,                     # 年線近 20 日變化 %
    })


def percentile(series: pd.Series, years: int = LOOKBACK_YEARS) -> tuple[float, float]:
    """最新值在過去 N 年的百分位（0–100）以及實際涵蓋的年數。"""
    s = series.dropna()
    if len(s) < 120:
        return np.nan, 0.0
    hist = s.loc[s.index[-1] - pd.DateOffset(years=years):]
    covered = (hist.index[-1] - hist.index[0]).days / 365.25
    return float((hist < hist.iloc[-1]).mean() * 100), covered


# ================================================================ 加碼規則

RULE_KINDS = {
    "drawdown": "從近一年高點回檔",
    "below_ma": "低於年線",
}


@dataclass
class DipRule:
    kind: str = "drawdown"
    threshold: float = 10.0   # drawdown：回檔幾 %；below_ma：低於年線幾 %（0 = 跌破就算）

    def met(self, row: pd.Series) -> bool:
        if self.kind == "drawdown":
            return pd.notna(row["dd"]) and row["dd"] <= -self.threshold
        return pd.notna(row["bias"]) and row["bias"] <= -self.threshold

    def describe(self) -> str:
        if self.kind == "drawdown":
            return f"從近一年高點回檔 {self.threshold:g}% 以上"
        return "跌破年線" if self.threshold == 0 else f"低於年線 {self.threshold:g}% 以上"

    def current_text(self, row: pd.Series) -> str:
        if self.kind == "drawdown":
            return f"目前距近一年高點 {row['dd']:+.1f}%"
        return f"目前年線乖離 {row['bias']:+.1f}%"


# ================================================================ 規則式分析

@dataclass
class Finding:
    name: str
    state: str        # cheap / neutral / expensive / up / down / info
    headline: str
    detail: str = ""


STATE_LABEL = {
    "cheap": ("偏便宜", "green"), "neutral": ("中性", "gray"), "expensive": ("偏貴", "red"),
    "up": ("多頭", "red"), "down": ("空頭", "green"), "flat": ("整理", "gray"), "info": ("參考", "blue"),
}


def _band(p: float, low_is_cheap: bool = True) -> str:
    if pd.isna(p):
        return "info"
    if p <= 25:
        return "cheap" if low_is_cheap else "expensive"
    if p >= 75:
        return "expensive" if low_is_cheap else "cheap"
    return "neutral"


def analyze(feat: pd.DataFrame, yld: pd.Series | None, corr_2330: float | None) -> list[Finding]:
    out: list[Finding] = []
    last = feat.iloc[-1]

    # 1. 年線乖離與歷史分位
    if pd.notna(last["bias"]):
        p, yrs = percentile(feat["bias"])
        side = "高於" if last["bias"] >= 0 else "低於"
        detail = (f"現在的乖離比過去 {yrs:.1f} 年中 {p:.0f}% 的交易日都高。"
                  "25% 以下視為偏便宜、75% 以上視為偏貴。") if pd.notna(p) else ""
        out.append(Finding("年線位階", _band(p),
                           f"價格{side}年線 {abs(last['bias']):.1f}%", detail))
    else:
        out.append(Finding("年線位階", "info", "資料不滿一年，還算不出年線。"))

    # 2. 距近一年高點
    if pd.notna(last["dd"]):
        dd = last["dd"]
        state = "cheap" if dd <= -10 else "neutral"
        msg = "接近一年來的高點" if dd > -3 else f"從近一年高點回檔 {abs(dd):.1f}%"
        out.append(Finding("近一年高點", state, msg,
                           "回檔 10% 以上視為偏便宜。長期上漲的標的常創新高，接近高點本身不代表偏貴。"))

    # 3. 長期趨勢
    if pd.notna(last["MA240"]) and pd.notna(last["ma240_slope"]):
        rising = last["ma240_slope"] > 0
        above = last["MA60"] > last["MA240"]
        if rising and above:
            out.append(Finding("長期趨勢", "up", "季線在年線之上，年線往上走",
                               f"年線近 20 個交易日變化 {last['ma240_slope']:+.2f}%"))
        elif not rising and not above:
            out.append(Finding("長期趨勢", "down", "季線在年線之下，年線往下走",
                               f"年線近 20 個交易日變化 {last['ma240_slope']:+.2f}%"))
        else:
            out.append(Finding("長期趨勢", "flat", "季線和年線方向不一致",
                               f"季線{'在' if above else '不在'}年線之上，年線近 20 個交易日變化 {last['ma240_slope']:+.2f}%"))

    # 4. 殖利率位階
    if yld is not None and yld.notna().any() and yld.dropna().iloc[-1] > 0:
        y = yld.dropna()
        p, yrs = percentile(y)
        detail = (f"比過去 {yrs:.1f} 年中 {p:.0f}% 的交易日都高。殖利率越高代表相對配息越便宜，"
                  "但也可能是配息金額本身變多。") if pd.notna(p) else ""
        out.append(Finding("殖利率位階", _band(p, low_is_cheap=False),
                           f"近 12 個月殖利率 {y.iloc[-1]:.2f}%", detail))

    # 5. 和台積電的連動
    if corr_2330 is not None and pd.notna(corr_2330):
        level = "很高" if corr_2330 >= 0.8 else "中等" if corr_2330 >= 0.5 else "偏低"
        out.append(Finding("和台積電連動", "info", f"近一年日報酬相關係數 {corr_2330:.2f}（{level}）",
                           "1 代表漲跌完全同步。相關係數越高，這檔的表現越受台積電影響。"))
    return out


# ================================================================ 定期定額回測

@dataclass
class DcaParams:
    monthly: float = 5000
    day: int = 6
    extra: float = 5000
    rule: DipRule = field(default_factory=DipRule)
    reinvest: bool = True
    fee_discount: float = 1.0
    min_fee: float = 1.0


@dataclass
class DcaResult:
    name: str
    daily: pd.DataFrame        # value, invested
    events: pd.DataFrame       # 日期, 類型, 金額, 價格, 股數
    stats: dict


def _fee(amount: float, p: DcaParams) -> float:
    return max(amount * FEE_RATE * p.fee_discount, p.min_fee) if amount > 0 else 0.0


def _buy(budget: float, price: float, p: DcaParams) -> tuple[int, float]:
    n = int(budget // (price * (1 + FEE_RATE * p.fee_discount)))
    while n > 0 and n * price + _fee(n * price, p) > budget:
        n -= 1
    return (n, n * price + _fee(n * price, p)) if n > 0 else (0, 0.0)


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float:
    """年化內部報酬率（%）。flows：投入為負、期末市值為正。"""
    if len(flows) < 2:
        return np.nan
    t0 = flows[0][0]
    t = np.array([(d - t0).days / 365.25 for d, _ in flows])
    a = np.array([v for _, v in flows])

    def npv(r):
        return float(np.sum(a / (1 + r) ** t))

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2 * 100


def _dca_dates(index: pd.DatetimeIndex, start: pd.Timestamp, day: int) -> set:
    """每個月第一個 >= 扣款日的交易日。"""
    out = set()
    months = pd.period_range(start, index[-1], freq="M")
    for m in months:
        target = max(pd.Timestamp(m.year, m.month, min(day, m.days_in_month)), start)
        pos = index.searchsorted(target)
        if pos < len(index) and index[pos].to_period("M") == m:
            out.add(index[pos])
    return out


def simulate(price: pd.Series, dividends: pd.Series, feat: pd.DataFrame, start,
             p: DcaParams, mode: str, lump: float = 0.0) -> DcaResult:
    """mode: 'dca' 純定期定額、'dip' 定期定額＋逢低加碼、'lump' 單筆投入 lump 元。"""
    px = price.loc[pd.Timestamp(start):].dropna()
    if len(px) < 2:
        raise ValueError("回測區間太短")
    divs = dividends.reindex(px.index).fillna(0.0)
    prev_feat = feat.reindex(px.index).shift(1)   # 用前一天收盤的資料判斷，不偷看當天
    dca_days = _dca_dates(px.index, px.index[0], p.day) if mode != "lump" else set()

    shares, cash, invested = 0, 0.0, 0.0
    flows, events, rows = [], [], []

    def buy(day, amount, kind, new_money=True):
        nonlocal shares, cash, invested
        if new_money:
            cash += amount
            invested += amount
            flows.append((day, -amount))
        n, spent = _buy(cash, px[day], p)
        if n:
            shares += n
            cash -= spent
        events.append({"日期": day.date(), "類型": kind, "金額": amount, "價格": px[day], "股數": n})

    for day in px.index:
        if divs[day] > 0 and shares > 0:
            got = shares * divs[day]
            if p.reinvest:
                cash += got
                n, spent = _buy(cash, px[day], p)
                if n:
                    shares += n
                    cash -= spent
                events.append({"日期": day.date(), "類型": "配息再投入", "金額": got, "價格": px[day], "股數": n})
            else:
                cash += got
                events.append({"日期": day.date(), "類型": "配息（領現金）", "金額": got, "價格": px[day], "股數": 0})
        if mode == "lump" and day == px.index[0]:
            buy(day, lump, "單筆投入")
        if day in dca_days:
            buy(day, p.monthly, "定期定額")
            if mode == "dip" and p.extra > 0 and p.rule.met(prev_feat.loc[day]):
                buy(day, p.extra, "逢低加碼")
        rows.append((day, shares * px[day] + cash, invested))

    daily = pd.DataFrame(rows, columns=["date", "value", "invested"]).set_index("date")
    end_value = daily["value"].iloc[-1]
    flows.append((px.index[-1], end_value))
    ev = pd.DataFrame(events)
    active = daily[daily["invested"] > 0]
    stats = {
        "總投入": invested,
        "期末市值": end_value,
        "總報酬%": (end_value / invested - 1) * 100 if invested else np.nan,
        "年化報酬%": xirr(flows),
        "最大帳面虧損%": float((active["value"] / active["invested"] - 1).min() * 100) if len(active) else np.nan,
        "加碼次數": int((ev["類型"] == "逢低加碼").sum()) if not ev.empty else 0,
        "累計配息": float(ev.loc[ev["類型"].str.startswith("配息"), "金額"].sum()) if not ev.empty else 0.0,
    }
    name = {"dca": "純定期定額", "dip": "定期定額＋逢低加碼", "lump": "單筆投入"}[mode]
    return DcaResult(name, daily, ev, stats)

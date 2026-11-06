"""長期持有用的位階分析、分級投入規則與回測。

位階一律用還原權息價格計算，配息多的 ETF（例如 0056）才不會因為除息而看起來一直在跌。
回測時每一天的位階只用「當天以前」的資料計算，不會偷看未來。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

YEAR_BARS = 240        # 年線
HIGH_BARS = 252        # 近一年高點
PCT_WINDOW = 1250      # 位階分位的比較期間（約 5 年交易日）
PCT_MIN = 250          # 至少一年資料才算分位
LOOKBACK_YEARS = 5
FEE_RATE = 0.001425


# ================================================================ 位階特徵

def rolling_pct(s: pd.Series, window: int = PCT_WINDOW, min_periods: int = PCT_MIN) -> pd.Series:
    """每一天的值在「過去 window 個交易日」中的百分位（0–100），只用當天以前的資料。"""
    def f(w):
        last = w[-1]
        prev = w[:-1]
        prev = prev[~np.isnan(prev)]
        return (prev < last).mean() * 100 if len(prev) and not np.isnan(last) else np.nan
    return s.rolling(window, min_periods=min_periods).apply(f, raw=True)


def features(total: pd.DataFrame) -> pd.DataFrame:
    c = total["close"]
    ma240 = c.rolling(YEAR_BARS, min_periods=YEAR_BARS).mean()
    ma60 = c.rolling(60, min_periods=60).mean()
    bias = (c / ma240 - 1) * 100
    return pd.DataFrame({
        "close": c,
        "MA60": ma60,
        "MA240": ma240,
        "bias": bias,                                                            # 年線乖離 %
        "bias_pct": rolling_pct(bias),                                           # 乖離在過去 5 年的分位
        "bias_n": bias.notna().rolling(PCT_WINDOW, min_periods=1).sum(),         # 分位用到幾天資料
        "dd": (c / c.rolling(HIGH_BARS, min_periods=60).max() - 1) * 100,        # 距近一年高點 %
        "ma240_slope": (ma240 / ma240.shift(20) - 1) * 100,                      # 年線近 20 日變化 %
    })


def percentile(series: pd.Series, years: int = LOOKBACK_YEARS) -> tuple[float, float]:
    """最新值在過去 N 年的百分位（0–100）以及實際涵蓋的年數。"""
    s = series.dropna()
    if len(s) < 120:
        return np.nan, 0.0
    hist = s.loc[s.index[-1] - pd.DateOffset(years=years):]
    covered = (hist.index[-1] - hist.index[0]).days / 365.25
    return float((hist < hist.iloc[-1]).mean() * 100), covered


# ================================================================ 加碼規則與分級

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
            return bool(pd.notna(row["dd"]) and row["dd"] <= -self.threshold)
        return bool(pd.notna(row["bias"]) and row["bias"] <= -self.threshold)

    def describe(self) -> str:
        if self.kind == "drawdown":
            return f"從近一年高點回檔 {self.threshold:g}% 以上"
        return "跌破年線" if self.threshold == 0 else f"低於年線 {self.threshold:g}% 以上"

    def current_text(self, row: pd.Series) -> str:
        if self.kind == "drawdown":
            return f"目前距近一年高點 {row['dd']:+.1f}%"
        return f"目前年線乖離 {row['bias']:+.1f}%"


TIERS = ["dip", "cheap", "neutral", "expensive"]
TIER_LABEL = {"dip": "大跌", "cheap": "偏便宜", "neutral": "中性", "expensive": "偏貴"}
TIER_COLOR = {"dip": "green", "cheap": "green", "neutral": "gray", "expensive": "red"}
TIER_ACTION = {"dip": "建議大幅加碼", "cheap": "建議加碼", "neutral": "正常投入", "expensive": "建議減碼"}
DEFAULT_MULT = {"dip": 2.0, "cheap": 1.5, "neutral": 1.0, "expensive": 0.75}


@dataclass
class TierRule:
    mult: dict = field(default_factory=lambda: dict(DEFAULT_MULT))
    dip: DipRule = field(default_factory=DipRule)
    cheap_pct: float = 25.0       # 乖離分位 ≤ 這個值 → 偏便宜
    expensive_pct: float = 75.0   # 乖離分位 ≥ 這個值 → 偏貴

    def tier(self, row: pd.Series) -> str:
        if self.dip.met(row):
            return "dip"
        p = row.get("bias_pct")
        if p is None or pd.isna(p):
            return "neutral"
        if p <= self.cheap_pct:
            return "cheap"
        if p >= self.expensive_pct:
            return "expensive"
        return "neutral"

    def reasons(self, row: pd.Series) -> list[str]:
        out = []
        if pd.notna(row.get("bias")):
            p = row.get("bias_pct")
            ptxt = f"，在過去 5 年中位於 {p:.0f}% 分位" if pd.notna(p) else "（資料不足，無法算分位）"
            out.append(f"年線乖離 {row['bias']:+.1f}%{ptxt}"
                       f"（≤{self.cheap_pct:g}% 偏便宜、≥{self.expensive_pct:g}% 偏貴）")
        out.append(f"加碼條件「{self.dip.describe()}」{'成立' if self.dip.met(row) else '未成立'}，"
                   f"{self.dip.current_text(row)}")
        return out


def month_amount(base: float, reserve: float, mult: float) -> tuple[float, float]:
    """依倍數算出本次投入金額與準備金變化。

    倍數 > 1：多出來的部分從準備金拿，準備金不夠就只拿剩下的。
    倍數 < 1：沒投入的部分存進準備金。
    回傳 (投入金額, 準備金變化)。
    """
    target = base * mult
    if target >= base:
        extra = min(target - base, max(reserve, 0.0))
        return base + extra, -extra
    return target, base - target


# ================================================================ 規則式分析

@dataclass
class Finding:
    name: str
    state: str        # cheap / neutral / expensive / up / down / flat / info
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
        p = last["bias_pct"]
        yrs = max(last["bias_n"] - 1, 0) / 250
        side = "高於" if last["bias"] >= 0 else "低於"
        detail = (f"現在的乖離比過去約 {yrs:.1f} 年中 {p:.0f}% 的交易日都高。"
                  "25% 以下視為偏便宜、75% 以上視為偏貴。") if pd.notna(p) else "資料不足，還算不出分位。"
        out.append(Finding("年線位階", _band(p), f"價格{side}年線 {abs(last['bias']):.1f}%", detail))
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
        slope = f"年線近 20 個交易日變化 {last['ma240_slope']:+.2f}%"
        if rising and above:
            out.append(Finding("長期趨勢", "up", "季線在年線之上，年線往上走", slope))
        elif not rising and not above:
            out.append(Finding("長期趨勢", "down", "季線在年線之下，年線往下走", slope))
        else:
            out.append(Finding("長期趨勢", "flat", "季線和年線方向不一致",
                               f"季線{'在' if above else '不在'}年線之上，{slope}"))

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


# ================================================================ 回測

@dataclass
class Costs:
    fee_discount: float = 1.0
    min_fee: float = 1.0
    reinvest: bool = True
    day: int = 6              # 每月扣款日


@dataclass
class PlanResult:
    name: str
    daily: pd.DataFrame        # value, invested, reserve
    events: pd.DataFrame
    stats: dict


def _fee(amount: float, c: Costs) -> float:
    return max(amount * FEE_RATE * c.fee_discount, c.min_fee) if amount > 0 else 0.0


def _buy(budget: float, price: float, c: Costs) -> tuple[int, float]:
    n = int(budget // (price * (1 + FEE_RATE * c.fee_discount)))
    while n > 0 and n * price + _fee(n * price, c) > budget:
        n -= 1
    return (n, n * price + _fee(n * price, c)) if n > 0 else (0, 0.0)


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float:
    """年化內部報酬率（%）。flows：投入為負、期末市值為正。"""
    if len(flows) < 2:
        return np.nan
    t0 = flows[0][0]
    t = np.array([(d - t0).days / 365.25 for d, _ in flows])
    a = np.array([v for _, v in flows])
    if (t[-1] - t[0]) < 1 / 365:
        return np.nan

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


def monthly_dates(index: pd.DatetimeIndex, start: pd.Timestamp, day: int) -> list[pd.Timestamp]:
    """每個月第一個 >= 扣款日的交易日（遇假日順延）。"""
    out = []
    for m in pd.period_range(start, index[-1], freq="M"):
        target = max(pd.Timestamp(m.year, m.month, min(day, m.days_in_month)), start)
        pos = index.searchsorted(target)
        if pos < len(index) and index[pos].to_period("M") == m:
            out.append(index[pos])
    return out


def simulate_plan(price: pd.Series, dividends: pd.Series, feat: pd.DataFrame, start, c: Costs, *,
                  name: str, upfront: float, base: float, monthly_new: float, reserve0: float,
                  months: int | None = None, rule: TierRule | None = None,
                  lump_first: bool = False) -> PlanResult:
    """通用模擬。

    upfront     開始日一次拿出來的錢（計入投入）。其中 reserve0 是準備金，其餘是基本投入用的錢。
    base        每月基本投入金額
    monthly_new 每月新增的錢（每月固定模式 = base；一筆分批模式 = 0）
    months      只投入前 N 個月（None = 每個月都投入）
    rule        分級規則（None = 固定金額，準備金不動用）
    lump_first  開始日把基本投入用的錢一次投入（比較用）
    """
    px = price.loc[pd.Timestamp(start):].dropna()
    if len(px) < 2:
        raise ValueError("回測區間太短")
    divs = dividends.reindex(px.index).fillna(0.0)
    prev_feat = feat.reindex(px.index).shift(1)   # 用前一個交易日收盤判斷，不偷看當天
    dates = monthly_dates(px.index, px.index[0], c.day)
    if months is not None:
        dates = dates[:months]
    dates = set(dates)

    shares = 0
    pool = upfront - reserve0     # 等待按月投入的錢
    reserve = reserve0
    spare = 0.0                   # 買整數股剩下的零錢、未再投入的配息
    invested = upfront
    flows = [(px.index[0], -upfront)] if upfront else []
    events, rows = [], []
    tier_count = {t: 0 for t in TIERS}

    def buy(day, amount, kind, tier=""):
        nonlocal shares, spare
        n, spent = _buy(amount + spare, px[day], c)
        shares += n
        spare = amount + spare - spent
        events.append({"日期": day.date(), "類型": kind, "位階": tier, "金額": amount,
                       "價格": px[day], "股數": n, "準備金餘額": reserve})

    for day in px.index:
        if divs[day] > 0 and shares > 0:
            got = shares * divs[day]
            if c.reinvest:
                buy(day, got, "配息再投入")
            else:
                spare += got
                events.append({"日期": day.date(), "類型": "配息（領現金）", "位階": "", "金額": got,
                               "價格": px[day], "股數": 0, "準備金餘額": reserve})
        if lump_first and day == px.index[0] and pool > 0:
            amt, pool = pool, 0.0
            buy(day, amt, "一次投入")
        if day in dates:
            if monthly_new:
                pool += monthly_new
                invested += monthly_new
                flows.append((day, -monthly_new))
            b = min(base, pool)
            if b > 0:
                if rule is None:
                    pool -= b
                    buy(day, b, "定期定額")
                else:
                    t = rule.tier(prev_feat.loc[day])
                    tier_count[t] += 1
                    amt, d_res = month_amount(b, reserve, rule.mult[t])
                    pool -= b
                    reserve += d_res
                    buy(day, amt, "分級投入", TIER_LABEL[t])
        rows.append((day, shares * px[day] + pool + reserve + spare, invested, reserve))

    daily = pd.DataFrame(rows, columns=["date", "value", "invested", "reserve"]).set_index("date")
    end = daily.iloc[-1]
    flows.append((px.index[-1], end["value"]))
    active = daily[daily["invested"] > 0]
    stats = {
        "總投入": invested,
        "期末總值": end["value"],
        "總報酬%": (end["value"] / invested - 1) * 100 if invested else np.nan,
        "年化報酬%": xirr(flows),
        "最大帳面虧損%": float((active["value"] / active["invested"] - 1).min() * 100) if len(active) else np.nan,
        "未投入現金": pool + reserve + spare,
    }
    if rule is not None:
        stats.update({f"{TIER_LABEL[t]}次數": tier_count[t] for t in TIERS})
    return PlanResult(name, daily, pd.DataFrame(events), stats)

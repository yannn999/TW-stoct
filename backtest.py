"""簡單的單一個股、全進全出回測。

規則：
- 收盤出現訊號 → 隔天開盤價成交（避免用到當天收盤才知道的資訊）
- 可買零股，股數取整數
- 成本：手續費 0.1425% × 折扣（最低 20 元），賣出加證交稅（股票 0.3%、ETF 0.1%）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FEE_RATE = 0.001425
MIN_FEE = 20


@dataclass
class Costs:
    fee_discount: float = 0.6   # 券商手續費折扣，例如 6 折 = 0.6
    tax_rate: float = 0.003     # 股票 0.3%，ETF 0.1%
    min_fee: float = MIN_FEE

    def fee(self, amount: float) -> float:
        return max(amount * FEE_RATE * self.fee_discount, self.min_fee) if amount > 0 else 0.0


@dataclass
class Result:
    equity: pd.Series
    benchmark: pd.Series
    trades: pd.DataFrame
    stats: dict = field(default_factory=dict)


# ------------------------------------------------------------------ 訊號

def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def signals_ma(df: pd.DataFrame, short: int, long: int) -> pd.Series:
    s = df["close"].rolling(short, min_periods=short).mean()
    l = df["close"].rolling(long, min_periods=long).mean()
    return _cross_up(s, l).astype(int) - _cross_down(s, l).astype(int)


def signals_macd(df: pd.DataFrame) -> pd.Series:
    return _cross_up(df["DIF"], df["MACD"]).astype(int) - _cross_down(df["DIF"], df["MACD"]).astype(int)


def signals_kd(df: pd.DataFrame, level: float = 50) -> pd.Series:
    """K 在 level 以下黃金交叉買進；K 在 level 以上死亡交叉賣出。"""
    buy = _cross_up(df["K"], df["D"]) & (df["K"] < level)
    sell = _cross_down(df["K"], df["D"]) & (df["K"] > level)
    return buy.astype(int) - sell.astype(int)


# ------------------------------------------------------------------ 執行

def _buy(cash: float, px: float, costs: Costs) -> tuple[int, float]:
    shares = int(cash // (px * (1 + FEE_RATE * costs.fee_discount)))
    while shares > 0 and shares * px + costs.fee(shares * px) > cash:
        shares -= 1
    if shares <= 0:
        return 0, 0.0
    amount = shares * px
    return shares, amount + costs.fee(amount)


def _sell_proceeds(shares: int, px: float, costs: Costs) -> float:
    amount = shares * px
    return amount - costs.fee(amount) - amount * costs.tax_rate


def run(df: pd.DataFrame, signal: pd.Series, start, end=None,
        capital: float = 100_000, costs: Costs | None = None) -> Result:
    costs = costs or Costs()
    window = df.loc[start:end] if end is not None else df.loc[start:]
    sig = signal.reindex(window.index).fillna(0)
    if len(window) < 2:
        raise ValueError("回測區間太短")

    cash, shares, pending = capital, 0, 0
    entry = None
    equity, trades = [], []

    for day, row in window.iterrows():
        px_open = row["open"]
        if pending == 1 and shares == 0:
            n, spent = _buy(cash, px_open, costs)
            if n:
                shares, cash = n, cash - spent
                entry = (day, px_open, spent)
        elif pending == -1 and shares > 0:
            got = _sell_proceeds(shares, px_open, costs)
            cash += got
            trades.append({"買進日": entry[0].date(), "買進價": entry[1],
                           "賣出日": day.date(), "賣出價": px_open,
                           "股數": shares, "損益": got - entry[2],
                           "報酬%": (got / entry[2] - 1) * 100})
            shares, entry = 0, None
        pending = 0
        equity.append(cash + shares * row["close"])
        s = sig.loc[day]
        if s == 1 and shares == 0:
            pending = 1
        elif s == -1 and shares > 0:
            pending = -1

    equity = pd.Series(equity, index=window.index, name="策略")

    # 未平倉部位以最後收盤價計，列在交易表最後
    if shares > 0:
        last_px = window["close"].iloc[-1]
        got = _sell_proceeds(shares, last_px, costs)
        trades.append({"買進日": entry[0].date(), "買進價": entry[1],
                       "賣出日": None, "賣出價": last_px, "股數": shares,
                       "損益": got - entry[2], "報酬%": (got / entry[2] - 1) * 100})

    # 買進持有：第一天開盤買進
    n, spent = _buy(capital, window["open"].iloc[0], costs)
    bench = pd.Series(capital - spent + n * window["close"].to_numpy(),
                      index=window.index, name="買進持有")

    trades_df = pd.DataFrame(trades)
    return Result(equity, bench, trades_df, _stats(equity, bench, trades_df, shares > 0, capital))


def _max_drawdown(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min() * 100)


def _cagr(eq: pd.Series) -> float:
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0 or eq.iloc[0] <= 0:
        return np.nan
    return ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100


def _stats(eq, bench, trades, open_pos, capital) -> dict:
    closed = trades[trades["賣出日"].notna()] if not trades.empty else trades
    return {
        "總報酬%": (eq.iloc[-1] / capital - 1) * 100,
        "年化報酬%": _cagr(eq),
        "最大回撤%": _max_drawdown(eq),
        "交易次數": len(closed),
        "勝率%": (closed["損益"] > 0).mean() * 100 if len(closed) else np.nan,
        "持有中": open_pos,
        "買進持有報酬%": (bench.iloc[-1] / capital - 1) * 100,
        "買進持有最大回撤%": _max_drawdown(bench),
    }

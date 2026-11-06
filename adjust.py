"""股價調整：分割與還原權息。

兩種價格：
- split：只把分割前的價格換算成分割後的單位。數字和現在的報價一致，適合看 K 線。
- total：再把配息加回去（還原權息），代表「配息再投入」的總報酬，適合算位階和回測。

分割的判斷方式：台股有 10% 漲跌幅限制，所以前一天收盤到隔天開盤不可能跳空超過四成。
出現這種跳空，而且比例接近整數倍（例如 0050 一拆四，價格變成約 1/4），就視為分割。
如果 FinMind 的價格資料本來就已經換算過分割，就不會有跳空，也就不會重複調整。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]


@dataclass
class Adjusted:
    split: pd.DataFrame                 # 只調整分割
    total: pd.DataFrame                 # 還原權息
    dividends: pd.Series                # 每股配息（以現在的股數單位計），index=除息日
    splits: list = field(default_factory=list)   # [(日期, 1 股變幾股)]
    notes: list = field(default_factory=list)    # 給使用者看的資料說明


def detect_splits(px: pd.DataFrame, table: pd.DataFrame | None = None) -> list[tuple[pd.Timestamp, float]]:
    if len(px) < 2:
        return []
    gap = px["open"] / px["close"].shift(1)
    events: dict[pd.Timestamp, float] = {}

    # 1) FinMind 分割表，且價格資料裡真的看得到跳空才採用
    if table is not None and not table.empty:
        for d, row in table.iterrows():
            ratio = row["before_price"] / row["after_price"]
            pos = px.index.searchsorted(d)
            if 0 < pos < len(px) and abs(gap.iloc[pos] * ratio - 1) < 0.15:
                events[px.index[pos]] = float(ratio)

    # 2) 跳空偵測（涵蓋分割表沒有的情況，例如併股、面額變更）
    for day, g in gap.dropna().items():
        if day in events:
            continue
        if g < 0.6:
            k = float(round(1 / g))
        elif g > 1.8:
            k = 1 / round(g)
        else:
            continue
        if k != 1 and abs(g * k - 1) < 0.15:
            events[day] = k
    return sorted(events.items())


def adjust(px: pd.DataFrame, div: pd.DataFrame | None = None,
           split_tbl: pd.DataFrame | None = None) -> Adjusted:
    notes = []
    splits = detect_splits(px, split_tbl)

    split = px.copy()
    for day, ratio in splits:
        before = split.index < day
        split.loc[before, PRICE_COLS] = split.loc[before, PRICE_COLS] / ratio
        split.loc[before, "volume"] = split.loc[before, "volume"] * ratio
        notes.append(f"{day:%Y-%m-%d} 分割（1 股變 {ratio:g} 股），之前的價格已換算成分割後的單位。")

    factor = pd.Series(1.0, index=px.index)
    cash = {}
    if div is not None and not div.empty:
        for d, row in div.iterrows():
            pos = px.index.searchsorted(d)
            if pos == 0 or pos >= len(px):
                continue  # 除息日在資料範圍外
            day = px.index[pos]
            f = row["reference_price"] / row["before_price"]
            if not 0.5 < f < 1:
                continue
            factor[px.index < day] *= f
            if "息" in str(row["kind"]):
                # 用前一天的（分割後）收盤價推算每股配息，單位自然和現在的股數一致
                prev_close = split["close"].iloc[pos - 1]
                cash[day] = cash.get(day, 0.0) + prev_close * (1 - f)

    total = split.copy()
    total[PRICE_COLS] = total[PRICE_COLS].mul(factor, axis=0)
    dividends = pd.Series(cash, dtype=float).sort_index()
    dividends.index.name = "date"
    return Adjusted(split=split, total=total, dividends=dividends, splits=splits, notes=notes)


def trailing_yield(close: pd.Series, dividends: pd.Series) -> pd.Series:
    """近 12 個月現金殖利率（%）。資料不滿一年的日子為 NaN。"""
    d = dividends.reindex(close.index, fill_value=0.0)
    out = d.rolling("365D").sum() / close * 100
    out[close.index < close.index[0] + pd.Timedelta(days=365)] = np.nan
    return out

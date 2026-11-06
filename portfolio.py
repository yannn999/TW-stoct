"""從交易紀錄算出持倉、成本、損益、準備金餘額與實際年化報酬。

- 股數和價格會依分割換算成現在的單位（例如 0050 在 2025-06-18 一拆四之前買的 1 股 = 現在 4 股）。
- 成本用平均成本法：賣出時依比例減少成本。
- 配息不用手動記錄，依每次除息日的持股股數自動估算（稅前、未扣二代健保補充保費）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import longterm as lt

FEE_RATE = 0.001425


def trade_amount(kind: str, shares: float, price: float, fee_discount: float = 1.0,
                 min_fee: float = 1.0, tax_rate: float = 0.001) -> float:
    """買進：付出的總金額（含手續費）。賣出：拿回的金額（扣手續費與證交稅）。"""
    gross = shares * price
    fee = max(gross * FEE_RATE * fee_discount, min_fee) if gross > 0 else 0.0
    if kind == "買進":
        return round(gross + fee)
    return round(gross - fee - gross * tax_rate)


def normalize(records: pd.DataFrame, splits: list[tuple[pd.Timestamp, float]]) -> pd.DataFrame:
    """把分割前的股數、價格換算成分割後的單位。"""
    df = records.copy()
    for day, ratio in splits:
        before = df["日期"] < day
        df.loc[before, "股數"] = df.loc[before, "股數"] * ratio
        df.loc[before, "價格"] = df.loc[before, "價格"] / ratio
    return df


def reserve_balance(records: pd.DataFrame, sid: str) -> float:
    r = records[(records["代號"] == sid) & (records["類型"] == "準備金")]
    return float(r["金額"].sum())


def position(records: pd.DataFrame, sid: str, price: pd.Series, dividends: pd.Series,
             splits: list) -> dict | None:
    """單一股票的持倉摘要。price 是分割調整後的收盤價。"""
    tr = records[(records["代號"] == sid) & records["類型"].isin(["買進", "賣出"])]
    if tr.empty:
        return None
    tr = normalize(tr, splits).sort_values("日期")

    shares, cost, realized = 0.0, 0.0, 0.0
    flows = []
    holding = []                            # (日期, 交易後股數)
    for _, r in tr.iterrows():
        if r["類型"] == "買進":
            shares += r["股數"]
            cost += r["金額"]
            flows.append((r["日期"], -r["金額"]))
        else:
            n = min(r["股數"], shares)
            removed = cost * (n / shares) if shares else 0.0
            realized += r["金額"] - removed
            cost -= removed
            shares -= n
            flows.append((r["日期"], r["金額"]))
        holding.append((r["日期"], shares))

    # 依除息日前一天的持股估算配息
    held = pd.Series([h for _, h in holding], index=[d for d, _ in holding])
    held = held.groupby(level=0).last()
    div_total = 0.0
    for d, per_share in dividends.items():
        before = held[held.index < d]
        if len(before) and before.iloc[-1] > 0:
            got = before.iloc[-1] * per_share
            div_total += got
            flows.append((d, got))

    last_px = float(price.iloc[-1])
    value = shares * last_px
    flows.sort(key=lambda x: x[0])
    flows.append((price.index[-1], value))
    invested = float(tr.loc[tr["類型"] == "買進", "金額"].sum())
    unreal = value - cost
    return {
        "持有股數": shares,
        "平均成本": cost / shares if shares else np.nan,
        "持有成本": cost,
        "現價": last_px,
        "市值": value,
        "未實現損益": unreal,
        "未實現報酬%": unreal / cost * 100 if cost else np.nan,
        "已實現損益": realized,
        "預估已領配息": div_total,
        "累計買進": invested,
        "總損益": unreal + realized + div_total,
        "年化報酬%": lt.xirr(flows) if len(flows) > 1 else np.nan,
    }

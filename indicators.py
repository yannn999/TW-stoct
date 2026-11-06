"""技術指標。輸入為 index=date、含 open/high/low/close/volume 的 DataFrame。

參數預設採台股看盤軟體常見設定（KD 9-3-3、MACD 12-26-9、布林 20-2）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def resample(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """日 K 轉週 K / 月 K。freq: 'D'、'W'、'M'。"""
    if freq == "D":
        return df
    rule = {"W": "W-FRI", "M": "ME"}[freq]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "value": "sum"}
    out = df.resample(rule).agg({k: v for k, v in agg.items() if k in df.columns})
    out = out.dropna(subset=["close"])
    # 用該期最後一個交易日當標籤，K 棒才不會落在沒交易的週末
    last_day = df["close"].resample(rule).apply(lambda s: s.index.max() if len(s) else pd.NaT)
    out.index = pd.DatetimeIndex(last_day.reindex(out.index))
    return out


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI。"""
    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # 期間內完全沒跌 → RSI = 100
    out = out.where(~((avg_loss == 0) & avg_gain.notna()), 100.0)
    return out


def kd(df: pd.DataFrame, n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> pd.DataFrame:
    """台式 KD：RSV 取 n 日，K = 前K×(m-1)/m + RSV/m，起始值 50。"""
    low_n = df["low"].rolling(n, min_periods=n).min()
    high_n = df["high"].rolling(n, min_periods=n).max()
    span = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["close"] - low_n) / span * 100).fillna(50)
    rsv[low_n.isna()] = np.nan

    k_vals, d_vals = [], []
    k_prev = d_prev = 50.0
    for v in rsv.to_numpy():
        if np.isnan(v):
            k_vals.append(np.nan)
            d_vals.append(np.nan)
            continue
        k_prev = k_prev * (k_smooth - 1) / k_smooth + v / k_smooth
        d_prev = d_prev * (d_smooth - 1) / d_smooth + k_prev / d_smooth
        k_vals.append(k_prev)
        d_vals.append(d_prev)
    return pd.DataFrame({"K": k_vals, "D": d_vals}, index=df.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    out = pd.DataFrame({"DIF": dif, "MACD": dea, "OSC": dif - dea}, index=close.index)
    out.iloc[: slow - 1] = np.nan
    return out


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"BB_mid": mid, "BB_up": mid + k * std, "BB_low": mid - k * std})


def add_all(df: pd.DataFrame, ma_windows=(5, 20, 60, 240)) -> pd.DataFrame:
    """回傳加上所有指標欄位的新 DataFrame。"""
    out = df.copy()
    for n in ma_windows:
        out[f"MA{n}"] = sma(out["close"], n)
    out["VOL_MA20"] = sma(out["volume"], 20)
    out["RSI"] = rsi(out["close"], 14)
    out = out.join(kd(out)).join(macd(out["close"])).join(bollinger(out["close"]))
    return out


def snapshot(df: pd.DataFrame) -> dict:
    """自選股表格用的最新狀態摘要（需先經過 add_all）。"""
    last = df.iloc[-1]
    prev_close = df["close"].iloc[-2] if len(df) > 1 else np.nan

    def ret(n):
        return (last["close"] / df["close"].iloc[-n - 1] - 1) * 100 if len(df) > n else np.nan

    k, d = last.get("K"), last.get("D")
    if pd.isna(k):
        kd_state = "—"
    elif k > 80:
        kd_state = "高檔"
    elif k < 20:
        kd_state = "低檔"
    else:
        kd_state = "K>D" if k > d else "K<D"

    return {
        "收盤": last["close"],
        "漲跌%": (last["close"] / prev_close - 1) * 100,
        "5日%": ret(5),
        "20日%": ret(20),
        "RSI": last.get("RSI"),
        "K": k,
        "KD": kd_state,
        "站上MA20": bool(last["close"] > last["MA20"]) if pd.notna(last.get("MA20")) else None,
        "量比": last["volume"] / last["VOL_MA20"] if last.get("VOL_MA20") else np.nan,
        "資料日": df.index[-1].date(),
    }

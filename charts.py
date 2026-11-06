"""Plotly 圖表。顏色採台股慣例：紅漲綠跌。

手機上拖曳圖表會搶走頁面捲動，所以所有圖都關掉拖曳（dragmode=False），
改用畫面上的「顯示範圍」選單切換時間範圍。點一下圖仍然會顯示數值。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP = "#e5383b"
DOWN = "#1a9e5c"
MA_COLORS = {5: "#f4a261", 10: "#e9c46a", 20: "#4361ee", 60: "#9d4edd", 120: "#6c757d", 240: "#8d6e63"}
INST_COLORS = {"外資": "#4361ee", "投信": "#f4a261", "自營商": "#9d4edd"}
PLAN_COLORS = ["#e5383b", "#4361ee", "#8d99ae"]
TIER_COLORS = {"大跌": "#1a9e5c", "偏便宜": "#74c69d", "中性": "#8d99ae", "偏貴": "#e5383b"}
GRID = "rgba(128,128,128,0.35)"
CONFIG = {"displayModeBar": False, "scrollZoom": False}


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=36, b=8), dragmode=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
    )
    return fig


def _gaps(index: pd.DatetimeIndex) -> list:
    """非交易日（週末、國定假日）從 x 軸拿掉，K 棒才會連續。"""
    all_days = pd.date_range(index.min(), index.max(), freq="D")
    return [d.strftime("%Y-%m-%d") for d in all_days.difference(index)]


def kline(df: pd.DataFrame, freq: str, overlay: str, panels: list[str],
          ma_windows=(5, 20, 60, 240)) -> go.Figure:
    rows = 2 + len(panels)
    heights = ([0.55, 0.15] + [0.3 / len(panels)] * len(panels)) if panels else [0.78, 0.22]
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=heights, subplot_titles=["", "成交量（張）"] + panels)

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="K線", increasing=dict(line=dict(color=UP), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN), showlegend=False), 1, 1)

    if overlay == "均線":
        for n in ma_windows:
            col = f"MA{n}"
            if col in df and df[col].notna().any():
                fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col, mode="lines",
                                         line=dict(width=1.3, color=MA_COLORS.get(n))), 1, 1)
    elif overlay == "布林通道":
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_up"], name="上軌", mode="lines",
                                 line=dict(width=1, color="#adb5bd")), 1, 1)
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_low"], name="下軌", mode="lines",
                                 line=dict(width=1, color="#adb5bd"),
                                 fill="tonexty", fillcolor="rgba(173,181,189,0.15)"), 1, 1)
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_mid"], name="中軌(MA20)", mode="lines",
                                 line=dict(width=1.3, color="#4361ee")), 1, 1)

    vol_colors = [UP if c >= o else DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], marker_color=vol_colors,
                         name="成交量", showlegend=False), 2, 1)

    for i, p in enumerate(panels, start=3):
        if p == "KD":
            fig.add_trace(go.Scatter(x=df.index, y=df["K"], name="K", line=dict(color="#f4a261", width=1.3)), i, 1)
            fig.add_trace(go.Scatter(x=df.index, y=df["D"], name="D", line=dict(color="#4361ee", width=1.3)), i, 1)
            for lvl in (20, 80):
                fig.add_hline(y=lvl, line=dict(color=GRID, dash="dot", width=1), row=i, col=1)
            fig.update_yaxes(range=[0, 100], row=i, col=1)
        elif p == "RSI":
            fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI(14)", line=dict(color="#9d4edd", width=1.3)), i, 1)
            for lvl in (30, 70):
                fig.add_hline(y=lvl, line=dict(color=GRID, dash="dot", width=1), row=i, col=1)
            fig.update_yaxes(range=[0, 100], row=i, col=1)
        elif p == "MACD":
            osc_colors = [UP if v >= 0 else DOWN for v in df["OSC"].fillna(0)]
            fig.add_trace(go.Bar(x=df.index, y=df["OSC"], name="OSC", marker_color=osc_colors, showlegend=False), i, 1)
            fig.add_trace(go.Scatter(x=df.index, y=df["DIF"], name="DIF", line=dict(color="#f4a261", width=1.3)), i, 1)
            fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD", line=dict(color="#4361ee", width=1.3)), i, 1)

    fig.update_xaxes(rangeslider_visible=False)
    if freq == "D" and len(df) > 1:
        fig.update_xaxes(rangebreaks=[dict(values=_gaps(df.index))])
    return _base_layout(fig, 480 + 150 * len(panels))


def position(feat: pd.DataFrame) -> go.Figure:
    """年線乖離走勢，標出 25% / 75% 分位線。"""
    s = feat["bias"].dropna()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s, name="年線乖離 %", line=dict(color="#4361ee", width=1.5),
                             fill="tozeroy", fillcolor="rgba(67,97,238,0.10)"))
    if len(s) > 120:
        for q, label, color in ((0.25, "25% 分位", DOWN), (0.75, "75% 分位", UP)):
            v = s.quantile(q)
            fig.add_hline(y=v, line=dict(color=color, dash="dash", width=1),
                          annotation_text=f"{label} {v:+.1f}%", annotation_position="top left")
    fig.add_hline(y=0, line=dict(color=GRID, width=1))
    fig.update_yaxes(title_text="%", ticksuffix="%")
    return _base_layout(fig, 300)


def dividend_yield(yld: pd.Series, dividends: pd.Series) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=dividends.index, y=dividends, name="每股配息（元）",
                         marker_color="#a5b4fc", width=86400000 * 12), secondary_y=False)
    y = yld.dropna()
    fig.add_trace(go.Scatter(x=y.index, y=y, name="近12月殖利率 %", line=dict(color="#f4a261", width=1.5)),
                  secondary_y=True)
    fig.update_yaxes(title_text="元", secondary_y=False)
    fig.update_yaxes(title_text="%", secondary_y=True, showgrid=False)
    return _base_layout(fig, 320)


def valuation(df: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=df.index, y=df["PER"], name="本益比", line=dict(color="#4361ee")), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df["dividend_yield"], name="殖利率 %", line=dict(color="#f4a261")), secondary_y=True)
    fig.update_yaxes(title_text="倍", secondary_y=False)
    fig.update_yaxes(title_text="%", secondary_y=True, showgrid=False)
    return _base_layout(fig, 320)


def revenue(df: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=df.index, y=df["revenue"] / 1e8, name="月營收（億元）",
                         marker_color="#a5b4fc"), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df["yoy"], name="年增率 %", mode="lines+markers",
                             line=dict(color=UP), marker=dict(size=5)), secondary_y=True)
    fig.add_hline(y=0, line=dict(color=GRID, width=1), secondary_y=True)
    fig.update_yaxes(title_text="億元", secondary_y=False)
    fig.update_yaxes(title_text="%", secondary_y=True, showgrid=False)
    fig.update_xaxes(tickformat="%Y-%m")
    return _base_layout(fig, 320)


def institutional(df: pd.DataFrame, price: pd.Series | None = None) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for col in ("外資", "投信", "自營商"):
        fig.add_trace(go.Bar(x=df.index, y=df[col], name=col, marker_color=INST_COLORS[col]), secondary_y=False)
    if price is not None:
        fig.add_trace(go.Scatter(x=df.index, y=price.reindex(df.index), name="收盤價",
                                 line=dict(color="#8d6e63", width=1.5)), secondary_y=True)
    fig.update_layout(barmode="relative")
    fig.update_yaxes(title_text="張", secondary_y=False)
    fig.update_yaxes(title_text="股價", secondary_y=True, showgrid=False)
    if len(df) > 1:
        fig.update_xaxes(rangebreaks=[dict(values=_gaps(df.index))])
    return _base_layout(fig, 340)


def dca(results: list) -> go.Figure:
    """各方案的總值（實線）與累計投入（虛線，只畫第一個方案，三者投入相同時就是同一條）。"""
    fig = go.Figure()
    for i, r in enumerate(results):
        color = PLAN_COLORS[i % len(PLAN_COLORS)]
        fig.add_trace(go.Scatter(x=r.daily.index, y=r.daily["value"], name=r.name,
                                 line=dict(color=color, width=2 if i == 0 else 1.5)))
    r0 = results[0]
    fig.add_trace(go.Scatter(x=r0.daily.index, y=r0.daily["invested"], name="累計投入",
                             line=dict(color="#8d6e63", width=1, dash="dot")))
    if "reserve" in r0.daily and r0.daily["reserve"].max() > 0:
        fig.add_trace(go.Scatter(x=r0.daily.index, y=r0.daily["reserve"], name=f"{r0.name} 準備金",
                                 line=dict(color=PLAN_COLORS[0], width=1, dash="dash")))
    fig.update_yaxes(title_text="元", tickformat=",")
    return _base_layout(fig, 380)


def tier_marks(price: pd.Series, events: pd.DataFrame) -> go.Figure:
    """股價走勢上標出每個月的位階與投入金額。"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=price.index, y=price, name="收盤價", line=dict(color="#8d6e63", width=1.3)))
    if not events.empty and "位階" in events:
        e = events[events["類型"] == "分級投入"]
        for tier, color in TIER_COLORS.items():
            sub = e[e["位階"] == tier]
            if len(sub):
                fig.add_trace(go.Scatter(
                    x=pd.to_datetime(sub["日期"]), y=sub["價格"], mode="markers", name=tier,
                    marker=dict(size=9, color=color, line=dict(width=1, color="white")),
                    customdata=sub["金額"], hovertemplate="%{x|%Y-%m-%d}<br>投入 %{customdata:,.0f} 元"))
    return _base_layout(fig, 320)


def equity(eq: pd.Series, bench: pd.Series) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq, name="策略", line=dict(color="#4361ee", width=2)))
    fig.add_trace(go.Scatter(x=bench.index, y=bench, name="買進持有", line=dict(color="#8d99ae", width=1.5)))
    fig.update_yaxes(title_text="元", tickformat=",")
    return _base_layout(fig, 340)


def trades_on_price(df: pd.DataFrame, trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["close"], name="收盤價", line=dict(color="#8d6e63", width=1.3)))
    if not trades.empty:
        fig.add_trace(go.Scatter(x=pd.to_datetime(trades["買進日"]), y=trades["買進價"], mode="markers",
                                 name="買進", marker=dict(symbol="triangle-up", size=10, color=UP)))
        sold = trades[trades["賣出日"].notna()]
        fig.add_trace(go.Scatter(x=pd.to_datetime(sold["賣出日"]), y=sold["賣出價"], mode="markers",
                                 name="賣出", marker=dict(symbol="triangle-down", size=10, color=DOWN)))
    return _base_layout(fig, 300)


def robust(df: pd.DataFrame) -> go.Figure:
    """穩健性檢查：每個開始月份，分級投入比「準備金先投」每年多或少的報酬。"""
    d = df["差距（vs 準備金先投）"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["開始月"], y=d, name="分級投入 − 準備金先投",
                         marker_color=[UP if v >= 0 else DOWN for v in d.fillna(0)]))
    fig.add_trace(go.Scatter(x=df["開始月"], y=df["差距（vs 準備金不動）"], name="分級投入 − 準備金不動",
                             mode="lines", line=dict(color="#4361ee", width=1.3)))
    fig.add_hline(y=0, line=dict(color=GRID, width=1))
    fig.update_yaxes(title_text="年化報酬差（%）", ticksuffix="%")
    fig.update_xaxes(title_text="開始投資的月份", type="category", nticks=8)
    return _base_layout(fig, 320)

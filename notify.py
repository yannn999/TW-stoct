"""收盤通知：由 GitHub Actions 在每個平日晚上自動執行。

會傳 Telegram 訊息的情況：
1. 自選股的「本月建議」和上一次通知時不一樣（例如從「正常投入」變成「建議加碼」）
2. 每月扣款日的前一個平日，提醒這個月依規則要投入多少

需要的環境變數（在 GitHub 的 Secrets 設定）：
  TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID、FINMIND_TOKEN
  GCP_SERVICE_ACCOUNT、SHEET_URL（用來讀你的規則、預算、準備金，並記住上次通知的狀態）

本機測試：python notify.py --force --dry-run
"""
from __future__ import annotations

import argparse
import calendar
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import adjust as adj
import finmind as fm
import longterm as lt
import portfolio as pf
import settings as cfg
import storage

HIST_DAYS = 365 * 8 + 120
TZ = ZoneInfo("Asia/Taipei")
FOOTER = "以上依你設定的規則計算，不是投資建議。"


def reminder_day(today: date, day: int) -> date:
    """這個月扣款日的前一個平日。"""
    last = calendar.monthrange(today.year, today.month)[1]
    target = date(today.year, today.month, min(day, last))
    d = target - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def deduction_day(today: date, day: int) -> date:
    last = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, min(day, last))


def analyse(sid: str, token: str, rule: lt.TierRule):
    px = fm.price(sid, HIST_DAYS, token)
    if px.empty:
        return None
    try:
        div = fm.dividend_result(sid, HIST_DAYS, token)
    except fm.FinMindError:
        div = None
    a = adj.adjust(px, div)
    feat = lt.features(a.total)
    return a, feat.iloc[-1]


def build(today: date, token: str, settings: dict, records: pd.DataFrame, log: pd.DataFrame | None,
          watch: list[str], names: dict, force: bool) -> tuple[str | None, list[list]]:
    """回傳 (訊息文字或 None, 要寫進通知紀錄的列)。"""
    rule = cfg.tier_rule(settings)
    blocks, reminders, log_rows = [], [], []
    send = force
    data_dates = set()

    for sid in watch:
        res = analyse(sid, token, rule)
        if res is None:
            blocks.append(f"{sid}：找不到股價資料")
            continue
        a, last = res
        data_dates.add(a.split.index[-1].date())
        t = rule.tier(last)
        label = lt.TIER_LABEL[t]
        m = rule.mult[t]

        prev = None
        if log is not None and not log.empty:
            mine = log[log["代號"] == sid]
            if len(mine):
                prev = str(mine["位階"].iloc[-1])
        changed = log is not None and prev != label
        if log is None and t in ("dip", "cheap"):
            changed = True          # 沒有試算表記住狀態時，只在便宜的時候通知
        send = send or changed

        head = f"{sid} {names.get(sid, '')}".strip()
        line = f"本月建議：{lt.TIER_ACTION[t]}（{label} ×{m:g}）"
        if changed and prev:
            line += f"　← 原本{prev}"
        pct = f"，5 年分位 {last['bias_pct']:.0f}%" if pd.notna(last["bias_pct"]) else ""
        detail = f"年線乖離 {last['bias']:+.1f}%{pct}；距近一年高點 {last['dd']:+.1f}%"
        blocks.append(f"{head}\n{line}\n{detail}")
        log_rows.append([today.isoformat(), sid, label, "變化" if changed else "狀態"])

        # 扣款日提醒
        if cfg.has_plan(settings, sid):
            plan = cfg.plan_values(settings, sid)
            rd, dd = reminder_day(today, plan["plan_day"]), deduction_day(today, plan["plan_day"])
            month_key = today.strftime("%Y-%m")
            already = log is not None and not log[(log["代號"] == sid) & (log["類型"] == "月提醒")
                                                  & log["日期"].astype(str).str.startswith(month_key)].empty
            if rd <= today <= dd and not already:
                base, reserve_init, _ = cfg.plan_budget(plan)
                has_res = ((records["代號"] == sid) & (records["類型"] == "準備金")).any()
                reserve = pf.reserve_balance(records, sid) if has_res else reserve_init
                amt, d_res = lt.month_amount(base, reserve, m)
                px = float(a.split["close"].iloc[-1])
                n = int(amt // (px * (1 + lt.FEE_RATE)))
                reminders.append(f"{head}：扣款日 {dd:%m/%d}\n依規則本月投入 {amt:,.0f} 元（約 {n:,} 股），"
                                 f"準備金 {d_res:+,.0f} 元，之後剩 {reserve + d_res:,.0f} 元")
                log_rows.append([today.isoformat(), sid, label, "月提醒"])
                send = True

    if not send:
        return None, []
    when = max(data_dates).isoformat() if data_dates else today.isoformat()
    parts = [f"📈 台股分析｜{when} 收盤資料", *blocks]
    if reminders:
        parts.append("📅 扣款提醒\n" + "\n".join(reminders))
    parts.append(FOOTER)
    return "\n\n".join(parts), log_rows


def send_telegram(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        raise RuntimeError("沒有設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=20)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise RuntimeError(f"Telegram 傳送失敗：HTTP {r.status_code} {body.get('description', r.text[:200])}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="不管有沒有變化都傳（測試用）")
    ap.add_argument("--dry-run", action="store_true", help="只印出訊息，不傳也不寫紀錄")
    args = ap.parse_args(argv)

    if not args.dry_run and not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        print("還沒有設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，略過通知。設定方式見 README「收盤通知設定」。")
        return 0

    today = datetime.now(TZ).date()
    token = os.environ.get("FINMIND_TOKEN", "")
    store = storage.from_env()
    if store is None:
        print("沒有設定 Google 試算表，使用預設規則；只在偏便宜或大跌時通知。")
    settings = store.settings() if store else {}
    records = store.records() if store else storage.parse_records(pd.DataFrame(columns=storage.RECORD_COLS))
    log = store.notify_log() if store else None
    watch = [s.strip() for s in settings.get("watchlist", os.environ.get("WATCHLIST", cfg.DEFAULT_WATCH)).split(",")
             if s.strip()]
    try:
        info = fm.stock_list(token)
        names = dict(zip(info["stock_id"], info["stock_name"]))
    except Exception:  # noqa: BLE001
        names = {}

    text, rows = build(today, token, settings, records, log, watch, names, args.force)
    if text is None:
        print("今天沒有變化，也不是扣款提醒日，不傳訊息。")
        return 0
    print(text)
    if args.dry_run:
        return 0
    send_telegram(text)
    if store and rows:
        store.add_notify(rows)
    print("已傳送。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

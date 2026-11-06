"""Google 試算表存取。

試算表裡有三個工作表（第一次使用時會自動建立）：
- 交易紀錄：日期、代號、類型（買進／賣出／準備金）、股數、價格、金額、備註
- 設定：項目、值
- 通知紀錄：日期、代號、位階、類型

沒有設定 Google 帳號時，App 其他功能照常運作，只是不能存紀錄。
"""
from __future__ import annotations

import json
import os

import pandas as pd

RECORD_COLS = ["日期", "代號", "類型", "股數", "價格", "金額", "備註"]
SHEETS = {
    "交易紀錄": RECORD_COLS,
    "設定": ["項目", "值"],
    "通知紀錄": ["日期", "代號", "位階", "類型"],
}
RECORD_TYPES = ["買進", "賣出", "準備金"]


class StoreError(RuntimeError):
    """連不上試算表或權限不足。"""


class Store:
    def __init__(self, info: dict, url: str):
        try:
            import gspread
            self._gspread = gspread
            self.sh = gspread.service_account_from_dict(info).open_by_url(url)
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_explain(exc)) from exc

    # ---------------------------------------------------------- 共用
    def _ws(self, name: str):
        try:
            return self.sh.worksheet(name)
        except self._gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=name, rows=1000, cols=len(SHEETS[name]))
            ws.append_row(SHEETS[name], value_input_option="RAW")
            return ws

    def _rows(self, name: str) -> pd.DataFrame:
        try:
            rows = self._ws(name).get_all_records(numericise_ignore=["all"], expected_headers=SHEETS[name])
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_explain(exc)) from exc
        return pd.DataFrame(rows, columns=SHEETS[name])

    def _append(self, name: str, rows: list[list]) -> None:
        try:
            ws = self._ws(name)
            for r in rows:
                ws.append_row([str(x) for x in r], value_input_option="RAW")
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_explain(exc)) from exc

    # ---------------------------------------------------------- 交易紀錄
    def records(self) -> pd.DataFrame:
        return parse_records(self._rows("交易紀錄"))

    def add_records(self, rows: list[dict]) -> None:
        self._append("交易紀錄", [[r.get(c, "") for c in RECORD_COLS] for r in rows])

    def delete_record(self, sheet_row: int) -> None:
        """sheet_row：工作表上的列號（標題是第 1 列）。"""
        try:
            self._ws("交易紀錄").delete_rows(int(sheet_row))
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_explain(exc)) from exc

    # ---------------------------------------------------------- 設定
    def settings(self) -> dict:
        df = self._rows("設定")
        return {str(k): str(v) for k, v in zip(df["項目"], df["值"]) if str(k)}

    def save_settings(self, updates: dict) -> None:
        merged = {**self.settings(), **{k: str(v) for k, v in updates.items()}}
        try:
            ws = self._ws("設定")
            ws.clear()
            ws.update([SHEETS["設定"]] + [[k, v] for k, v in sorted(merged.items())], "A1", raw=True)
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_explain(exc)) from exc

    # ---------------------------------------------------------- 通知紀錄
    def notify_log(self) -> pd.DataFrame:
        return self._rows("通知紀錄")

    def add_notify(self, rows: list[list]) -> None:
        self._append("通知紀錄", rows)


def parse_records(df: pd.DataFrame) -> pd.DataFrame:
    """把試算表讀回來的文字轉成正確型別，並保留工作表列號（刪除時要用）。"""
    df = df.copy()
    df["列號"] = range(2, len(df) + 2)
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df["代號"] = df["代號"].astype(str).str.strip()
    for c in ("股數", "價格", "金額"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)
    df = df[df["日期"].notna() & df["類型"].isin(RECORD_TYPES)]
    return df.sort_values(["日期", "列號"]).reset_index(drop=True)


def _explain(exc: Exception) -> str:
    text = str(exc)
    name = type(exc).__name__
    if name == "SpreadsheetNotFound" or "404" in text:
        return "找不到試算表。請確認網址正確，並且已經把試算表「共用」給服務帳號的 email（編輯者權限）。"
    if "403" in text or "PERMISSION" in text.upper():
        return ("沒有權限存取試算表。請確認試算表已共用給服務帳號的 email（編輯者權限），"
                "並且在 Google Cloud 啟用了 Google Sheets API 和 Google Drive API。")
    return f"試算表連線失敗：{name}: {text[:200]}"


def connect(info: dict, url: str) -> Store:
    return Store(info, url)


def from_env() -> Store | None:
    """給 notify.py 用：從環境變數 GCP_SERVICE_ACCOUNT（JSON 文字）與 SHEET_URL 連線。"""
    raw, url = os.environ.get("GCP_SERVICE_ACCOUNT", ""), os.environ.get("SHEET_URL", "")
    if not raw.strip() or not url.strip():
        return None
    return connect(json.loads(raw), url)

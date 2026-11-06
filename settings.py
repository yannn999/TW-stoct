"""使用者設定：分級規則（全部股票共用）與每一檔的預算。

App 和收盤通知（notify.py）共用這份定義，存在 Google 試算表的「設定」工作表裡。
每一檔的預算存成「plan.<代號>.<項目>」，例如 plan.0050.plan_monthly。
"""
from __future__ import annotations

import longterm as lt

# 全部股票共用的分級規則
RULE_DEFAULTS = {
    "rule_kind": "drawdown",
    "rule_thr": 10.0,
    **{f"m_{t}": lt.DEFAULT_MULT[t] for t in lt.TIERS},
}

# 每一檔各自的預算
PLAN_DEFAULTS = {
    "plan_mode": "每月固定",
    "plan_monthly": 5000,
    "plan_reserve": 20000,
    "plan_total": 300_000,
    "plan_months": 12,
    "plan_res_pct": 30,
    "plan_day": 6,
}

DEFAULT_WATCH = "0050,0056"


def _cast(value, like):
    if isinstance(like, bool):
        return str(value).lower() in ("1", "true", "yes")
    if isinstance(like, int):
        return int(float(value))
    if isinstance(like, float):
        return float(value)
    return str(value)


def rule_values(settings: dict) -> dict:
    out = {}
    for k, d in RULE_DEFAULTS.items():
        try:
            out[k] = _cast(settings.get(k, d), d)
        except (TypeError, ValueError):
            out[k] = d
    if out["rule_kind"] not in lt.RULE_KINDS:
        out["rule_kind"] = RULE_DEFAULTS["rule_kind"]
    return out


def plan_values(settings: dict, sid: str) -> dict:
    out = {}
    for k, d in PLAN_DEFAULTS.items():
        try:
            out[k] = _cast(settings.get(f"plan.{sid}.{k}", d), d)
        except (TypeError, ValueError):
            out[k] = d
    if out["plan_mode"] not in ("每月固定", "一筆分批"):
        out["plan_mode"] = PLAN_DEFAULTS["plan_mode"]
    return out


def has_plan(settings: dict, sid: str) -> bool:
    return any(k.startswith(f"plan.{sid}.") for k in settings)


def tier_rule(values: dict) -> lt.TierRule:
    v = rule_values(values)
    return lt.TierRule(mult={t: v[f"m_{t}"] for t in lt.TIERS},
                       dip=lt.DipRule(v["rule_kind"], v["rule_thr"]))


def plan_budget(plan: dict) -> tuple[float, float, int | None]:
    """回傳 (每月基本金額, 初始準備金, 分幾個月；每月固定為 None)。"""
    if plan["plan_mode"] == "每月固定":
        return float(plan["plan_monthly"]), float(plan["plan_reserve"]), None
    total = float(plan["plan_total"])
    reserve = total * plan["plan_res_pct"] / 100
    months = max(int(plan["plan_months"]), 1)
    return (total - reserve) / months, reserve, months

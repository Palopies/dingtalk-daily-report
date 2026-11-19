#!/usr/bin/env python3
"""Attendance gate: decide whether a scheduled daily report SHOULD be written.

Read-only, deterministic (harness style) — no AI, no report submission.

Rules (fail-closed): any uncertainty => should_write=false. "宁可不写，不瞎写".

Usage:
  python3 scripts/attendance_gate.py --date YYYY-MM-DD [--user-id UID] [--config PATH] [-f json]

Exit code 0 always means "judgement completed" (whether write or skip).
A skip due to query failure is a normal fail-closed branch, not an error.
"""

import argparse
import datetime as dt
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ReportError, load_config, get_schedule, run_dws, emit, fail,
)


def _result(should_write: bool, gate: str, reason: str, date: str,
            user_id: str = "", summary: Dict[str, Any] = None) -> Dict[str, Any]:
    return {
        "ok": True,
        "should_write": bool(should_write),
        "gate": gate,
        "reason": reason,
        "date": date,
        "user_id": user_id,
        "attendance_summary": summary or {"on_duty": False, "check_count": 0},
    }


def _my_user_id() -> str:
    """Resolve current user_id from dws auth status."""
    payload = run_dws(["auth", "status", "-f", "json"], timeout=30)
    uid = (payload.get("user_id") or payload.get("userId") or "")
    if not uid:
        raise ReportError("无法从 dws auth status 解析 user_id", reason="user_id_unresolved")
    return str(uid)


def _attendance(uid: str, date: str) -> Dict[str, Any]:
    """Fetch today's check-result. Raises ReportError on failure (=> fail-closed)."""
    payload = run_dws([
        "attendance", "+check-result",
        "--users", uid,
        "--start", date,
        "--end", date,
        "-f", "json",
    ], timeout=60)
    # unwrap common shapes: {"data":{"records":[]}} / {"result":{"records":[]}} / {"records":[]}
    records = []
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("result") or payload
        if isinstance(data, dict):
            records = data.get("records") or data.get("items") or []
        elif isinstance(data, list):
            records = data
    if not isinstance(records, list):
        records = []
    # checkType values seen in the wild: "OnDuty" / "OffDuty"; be tolerant of separators.
    on_duty = any(
        str(r.get("checkType", "")).upper().replace("_", "").replace("-", "") == "ONDUTY"
        for r in records if isinstance(r, dict)
    )
    return {"on_duty": on_duty, "check_count": len(records), "records": records}


def _has_trip(uid: str, date: str) -> bool:
    """Check for a trip/travel approval on this date (best-effort)."""
    try:
        payload = run_dws([
            "attendance", "+list-approve",
            "--users", uid,
            "--types", "trip",
            "--start", date,
            "--end", date,
            "-f", "json",
        ], timeout=60)
    except ReportError:
        return False
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") or payload.get("result") or payload
    if isinstance(data, dict):
        records = data.get("records") or data.get("items") or []
    elif isinstance(data, list):
        records = data
    else:
        records = []
    return bool(records)


def judge(args) -> Dict[str, Any]:
    cfg = load_config(args.config, check_mode=False)
    sched = get_schedule(cfg)
    gate = sched["attendance_gate"]
    date = args.date or dt.date.today().isoformat()

    # gate off -> always write (no fail-closed needed; user explicitly opted out)
    if not sched.get("enabled") or gate == "off":
        return _result(True, "off", "未启用考勤门禁（或定时任务未开启），到点即写", date)

    # resolve user id
    try:
        uid = args.user_id or _my_user_id()
    except ReportError as e:
        return _result(False, gate, f"无法确认当前用户，跳过：{e}", date)

    # query attendance (fail-closed on any error)
    try:
        att = _attendance(uid, date)
    except ReportError as e:
        return _result(False, gate, f"考勤查询失败，跳过（不瞎写）：{e}", date, uid)

    summary = {"on_duty": att["on_duty"], "check_count": att["check_count"]}

    if gate == "on-duty":
        if att["on_duty"]:
            return _result(True, gate, "今日有上班打卡，写日报", date, uid, summary)
        return _result(False, gate, "今日无上班打卡（周末/请假/未打卡），跳过", date, uid, summary)

    if gate == "approved":
        if att["on_duty"]:
            return _result(True, gate, "今日有上班打卡，写日报", date, uid, summary)
        # no on-duty check: allow trip approval as "working"
        try:
            has_trip = _has_trip(uid, date)
        except Exception:
            has_trip = False
        if has_trip:
            return _result(True, gate, "今日有出差/外出审批，写日报", date, uid, summary)
        return _result(False, gate, "今日既无打卡也无出差审批，跳过", date, uid, summary)

    # unknown gate should have been normalized in get_schedule; fail-closed anyway
    return _result(False, gate, f"未知门禁策略 {gate!r}，跳过", date, uid, summary)


def main():
    p = argparse.ArgumentParser(description="考勤门禁：判断定时触发时今天该不该写日报（只读）")
    p.add_argument("--date", default=None, help="日期 YYYY-MM-DD（默认今天）")
    p.add_argument("--user-id", default=None, help="指定 userId（默认从 dws auth status 解析）")
    p.add_argument("--config", default=None, help="配置文件路径（覆盖默认/env）")
    p.add_argument("-f", "--format", default="json", choices=["json"])
    args = p.parse_args()
    try:
        emit(judge(args))
    except ReportError as e:
        fail(e)


if __name__ == "__main__":
    main()

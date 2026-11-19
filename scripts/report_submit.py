#!/usr/bin/env python3
"""The ONLY write path: submit a daily report via dws.

Usage:
  python3 scripts/report_submit.py --fields-json ./daily-report-fields.json \
      [--config PATH] [--profile KEY | --hint STR] [--dry-run | --confirmed] [-f json]

Flow: load config -> fetch LIVE template schema -> drift check -> build contents
-> write cwd-relative contents file -> dws report entry submit [--dry-run]
-> on real submit, read back via entry get -> structured JSON.

Confirmation gate: when config.confirm_before_submit is true, a real submit
requires --confirmed (an assertion that the agent showed the dry-run preview
and the user explicitly approved these exact contents + receivers). Without it,
exit 2. The dws CLI itself has no confirmation gate, so this is the only one.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ReportError, load_config, run_dws, get_template_fields, build_contents,
    receiver_ids, resolve_profile, emit, fail,
)

BUILD_FILE = ".daily-report-contents.build.json"


def _load_fields_file(path: str) -> dict:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ReportError(f"fields-json 不存在：{path}", reason="fields_file_missing",
                          payload={"path": path})
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ReportError(f"fields-json 解析失败：{e}", reason="fields_file_invalid")


def _preview(contents: list, profile: dict, tpl: dict) -> dict:
    """Human-readable preview data (the AI renders it to the user)."""
    return {
        "profile": {"key": profile.get("key"), "name": profile.get("name")},
        "template": {"name": tpl["name"], "id": tpl["id"]},
        "receiver_count": len(profile["receivers"]),
        "receivers": [{"label": r.get("label"), "user_id": r.get("user_id")}
                      for r in profile["receivers"]],
        "fields": [
            {
                "key": c["key"], "sort": c["sort"],
                "length": len(c["content"]),
                "preview": c["content"][:80].replace("\n", " "),
            }
            for c in contents
        ],
    }


def _extract_report_id(payload: dict) -> str:
    """Find reportId from submit response across likely shapes."""
    if not isinstance(payload, dict):
        return ""
    for key in ("report_id", "reportId", "reportID", "id"):
        v = payload.get(key)
        if v:
            return str(v)
    res = payload.get("result")
    if isinstance(res, dict):
        return _extract_report_id(res)
    # some responses put id in url
    return ""


def submit(args) -> dict:
    cfg = load_config(args.config)
    profile = resolve_profile(cfg, key=args.profile, hint=args.hint)
    payload_fields = _load_fields_file(args.fields_json)

    live = get_template_fields(profile["template"]["name"])

    # drift check on cached field names (core gate)
    cached = [f.get("name") for f in profile["template"]["fields"]]
    live_names = [f["name"] for f in live["fields"]]
    if cached != live_names:
        raise ReportError(
            "配置中的字段与实时模板不一致，请重跑首次设置刷新配置后再提交",
            reason="fields_drift",
            payload={"cached": cached, "live": live_names},
        )

    contents = build_contents(payload_fields, live, profile)
    preview = _preview(contents, profile, live)
    csv_ids = receiver_ids(profile)

    dry = args.dry_run
    if not dry and profile.get("confirm_before_submit", True) and not args.confirmed:
        raise ReportError(
            "真实提交前需用户确认：先把 dry-run 预览给用户，确认后追加 --confirmed 重跑",
            reason="confirmation_required",
            payload={"preview": preview},
        )

    # write cwd-relative contents file (report.md forbids /tmp absolute paths)
    build_path = os.path.join(".", BUILD_FILE)
    try:
        with open(build_path, "w", encoding="utf-8") as f:
            json.dump(contents, f, ensure_ascii=False)
        dws_args = [
            "report", "entry", "submit",
            "--template-id", live["id"] or profile["template"]["id"],
            "--contents-file", build_path,
            "--to-user-ids", csv_ids,
        ]
        if profile.get("to_chat"):
            dws_args.append("--to-chat")
        if dry:
            dws_args.append("--dry-run")
        dws_args += ["-f", "json"]
        result = run_dws(dws_args, timeout=90)
    finally:
        try:
            os.remove(build_path)
        except OSError:
            pass

    if dry:
        return {
            "ok": True, "dry_run": True, "executed": False,
            "profile": {"key": profile.get("key"), "name": profile.get("name")},
            "preview": preview,
            "contents": contents,
            "to_user_ids": csv_ids.split(","),
            "dws": result,
        }

    report_id = _extract_report_id(result)
    verified = False
    detail = None
    if report_id:
        try:
            detail = run_dws(["report", "entry", "get", "--report-id", report_id, "-f", "json"])
            got = {c.get("key") for c in (detail.get("result", {}).get("report_content") or [])}
            expected = set(c["key"] for c in contents)
            verified = got == expected
        except ReportError:
            verified = False

    return {
        "ok": True, "dry_run": False, "executed": True,
        "profile": {"key": profile.get("key"), "name": profile.get("name")},
        "report_id": report_id,
        "verified": verified,
        "url": (detail or {}).get("result", {}).get("url") if detail else None,
        "preview": preview,
        "dws": result,
    }


def main():
    p = argparse.ArgumentParser(description="提交钉钉日报（唯一写路径）")
    p.add_argument("--fields-json", required=True,
                   help="AI 生成的字段内容 JSON（含 fields 对象）")
    p.add_argument("--config", default=None, help="配置文件路径（覆盖默认/env）")
    p.add_argument("--profile", default=None, help="指定日志类型 profile.key")
    p.add_argument("--hint", default=None, help="用户原话，用于路由选定日志类型")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="仅预览，不提交")
    g.add_argument("--confirmed", action="store_true",
                   help="断言已向用户展示预览并取得明确确认")
    p.add_argument("-f", "--format", default="json", choices=["json"])
    args = p.parse_args()
    try:
        emit(submit(args))
    except ReportError as e:
        fail(e)


if __name__ == "__main__":
    main()

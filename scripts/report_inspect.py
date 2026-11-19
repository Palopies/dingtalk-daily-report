#!/usr/bin/env python3
"""Read-only dws report discovery helpers.

Usage:
  python3 scripts/report_inspect.py list-templates [--query STR] [--dws BIN] [-f json]
  python3 scripts/report_inspect.py get-fields --name NAME [--dws BIN] [-f json]

Used during first-run setup (discover template + fields) and before every
submit (drift detection). Never writes anything.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ReportError, emit, fail, run_dws, get_template_fields  # noqa: E402


def cmd_list_templates(args) -> dict:
    payload = run_dws(["report", "template", "list", "-f", "json"])
    items = payload.get("items")
    if not isinstance(items, list):
        raise ReportError("template list 未返回 items", reason="template_list_bad")
    templates = [
        {"name": it.get("report_template_name"), "id": it.get("report_template_id")}
        for it in items if it.get("report_template_name")
    ]
    if args.query:
        q = args.query
        templates = [t for t in templates if q in (t["name"] or "")]
    return {"ok": True, "count": len(templates), "templates": templates}


def cmd_get_fields(args) -> dict:
    tpl = get_template_fields(args.name)
    return {"ok": True, "template": tpl}


def main():
    p = argparse.ArgumentParser(description="dws report 只读发现工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list-templates", help="列出当前用户可用的日志模板")
    pl.add_argument("--query", default=None, help="按名称子串过滤")

    pf = sub.add_parser("get-fields", help="读取指定模板的字段定义")
    pf.add_argument("--name", required=True, help="模板精确名称")

    for sp in (pl, pf):
        sp.add_argument("-f", "--format", default="json", choices=["json"])

    args = p.parse_args()
    try:
        if args.cmd == "list-templates":
            emit(cmd_list_templates(args))
        elif args.cmd == "get-fields":
            emit(cmd_get_fields(args))
    except ReportError as e:
        fail(e)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Deterministic routing over the configured log profiles. No dws, no AI.

Usage:
  python3 scripts/report_profile.py list [--config PATH] [-f json]
  python3 scripts/report_profile.py select (--hint STR | --key KEY) [--config PATH] [-f json]

`select` never guesses: ambiguous / no-match returns action=needs_selection
with candidates so the agent asks the user.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ReportError, load_config, get_profiles, migrated_from_v1,
    select_profile, emit, fail,
)


def cmd_list(args) -> dict:
    cfg = load_config(args.config)
    profiles = get_profiles(cfg)
    return {
        "ok": True,
        "version": int(cfg.get("version", 1)),
        "migrated_from_v1": migrated_from_v1(cfg),
        "default_profile": cfg.get("default_profile"),
        "count": len(profiles),
        "profiles": [
            {
                "key": p["key"],
                "name": p["name"],
                "aliases": p["aliases"],
                "receiver_count": len(p["receivers"]),
                "is_default": p["key"] == cfg.get("default_profile"),
            }
            for p in profiles
        ],
    }


def cmd_select(args) -> dict:
    cfg = load_config(args.config)
    result = select_profile(cfg, key=args.key, hint=args.hint)
    out = {"ok": True, "hint": args.hint}
    if result["action"] == "selected":
        out.update({
            "action": "selected",
            "key": result["key"],
            "name": result["name"],
            "matched_by": result["matched_by"],
            "profile": result["profile"],
        })
    else:
        out.update({
            "action": "needs_selection",
            "reason": result["reason"],
            "prompt": result["prompt"],
            "candidates": result["candidates"],
        })
    return out


def main():
    p = argparse.ArgumentParser(description="日志类型（profile）路由，纯确定性")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列出所有日志类型")
    pl.add_argument("--config", default=None)

    ps = sub.add_parser("select", help="根据用户原话/ key 选定日志类型")
    g = ps.add_mutually_exclusive_group(required=True)
    g.add_argument("--hint", default=None, help="用户原话，如「帮我写周报」")
    g.add_argument("--key", default=None, help="直接指定 profile.key")
    ps.add_argument("--config", default=None)

    for sp in (pl, ps):
        sp.add_argument("-f", "--format", default="json", choices=["json"])

    args = p.parse_args()
    try:
        if args.cmd == "list":
            emit(cmd_list(args))
        elif args.cmd == "select":
            emit(cmd_select(args))
    except ReportError as e:
        fail(e)


if __name__ == "__main__":
    main()

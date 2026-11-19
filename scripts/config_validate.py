#!/usr/bin/env python3
"""Read-only config validator. Never writes the config.

Usage:
  python3 scripts/config_validate.py [--config PATH] [--profile KEY] [--resolve-receivers] [-f json]

Checks: required keys/types, non-empty receivers, file mode, template id+fields
match against live dws schema. With --resolve-receivers, tries to resolve each
receiver to a real person via dws (aisearch / contact); unresolved custom ids
(e.g. a short custom id) are a WARNING, not a hard failure, since the user supplied them.
"""

import argparse
import os
import re
import stat
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ReportError, config_path, load_config, run_dws, get_template_fields,
    get_profiles, migrated_from_v1, emit, fail,
)

PHONE_RE = re.compile(r"^1\d{10}$")


def _resolve_receiver(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Try to resolve one receiver; return status + matches. Best-effort."""
    uid = str(rec.get("user_id", ""))
    label = str(rec.get("label", ""))
    out = {"label": label, "user_id": uid, "status": "unresolved", "matches": []}

    queries = []
    if PHONE_RE.match(uid):
        queries.append(("phone", uid))
    if label:
        queries.append(("name", label))

    seen = set()
    for dim, q in queries:
        try:
            payload = run_dws(
                ["aisearch", "person", "--query", q, "--dimension", dim, "-f", "json"],
                timeout=40,
            )
        except ReportError:
            continue
        # aisearch result shape varies; walk common containers.
        items = _collect_people(payload)
        for p in items:
            key = str(p.get("userId") or p.get("userid") or p.get("id") or "")
            nm = p.get("name") or p.get("userName") or ""
            # aisearch person candidates put identity in identityRefs/meta
            if not key:
                ev = p.get("_searchEvidence") or {}
                for ref in ev.get("identityRefs") or []:
                    if ref.get("domain") == "contact.userId" and ref.get("value"):
                        key = str(ref["value"])
                        break
                if not key and p.get("meta"):
                    meta = p["meta"]
                    key = str(meta.get("staffId") or meta.get("jobNumber") or "")
            if not nm:
                nm = (p.get("meta") or {}).get("name") or (p.get("author") or "")
            if key and key not in seen:
                seen.add(key)
                out["matches"].append({"userId": key, "name": nm, "via": dim})
    out["matches"] = out["matches"][:5]

    if any(m["userId"] == uid for m in out["matches"]):
        out["status"] = "resolved_exact"
    elif out["matches"]:
        out["status"] = "resolved_fuzzy"
    else:
        # Custom short ids are common and valid in DingTalk; not a failure.
        out["status"] = "unresolved_custom_id_ok" if uid and not PHONE_RE.match(uid) else "unresolved"
    return out


def _collect_people(payload: Any) -> list:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "list", "personList", "users", "resultList", "result", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            nested = _collect_people(v)
            if nested:
                return nested
    # aisearch sometimes returns a plain object with userName
    if payload.get("userId") or payload.get("userid"):
        return [payload]
    return []


def _validate_one(profile: Dict[str, Any], resolve_recvs: bool) -> Dict[str, Any]:
    """Validate a single effective profile. Returns per-profile report."""
    prefix = f"[{profile.get('name') or profile.get('key')}] "
    issues: list = []
    warnings: list = []

    tpl = profile["template"]
    tpl_info: Dict[str, Any] = {}
    try:
        live = get_template_fields(tpl["name"])
        cached_names = [f.get("name") for f in tpl["fields"]]
        live_names = [f["name"] for f in live["fields"]]
        fields_match = cached_names == live_names
        id_match = (not tpl.get("id")) or (tpl["id"] == live["id"])
        tpl_info = {
            "name": live["name"], "id": live["id"],
            "live_fields": [{"name": f["name"], "sort": f["sort"]} for f in live["fields"]],
            "fields_match": fields_match, "id_match": id_match,
        }
        if not fields_match:
            issues.append(prefix + "缓存字段与实时模板不一致，请重跑首次设置刷新配置")
        if not id_match:
            warnings.append(prefix + f"template.id={tpl.get('id')} 与实时 {live['id']} 不一致，以实时为准")
    except ReportError as e:
        issues.append(prefix + f"模板校验失败：{e}")

    recv_report = []
    if resolve_recvs:
        for r in profile["receivers"]:
            recv_report.append(_resolve_receiver(r))
        fuzzy = [r for r in recv_report if r["status"] == "resolved_fuzzy"]
        if fuzzy:
            warnings.append(prefix + "接收人仅模糊匹配到多个结果，请人工确认 userId：" +
                            ", ".join(r["label"] for r in fuzzy))
    else:
        recv_report = [{"label": r.get("label"), "user_id": r.get("user_id")}
                       for r in profile["receivers"]]

    return {
        "key": profile.get("key"),
        "name": profile.get("name"),
        "ok": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "template": tpl_info,
        "receivers": recv_report,
        "receiver_count": len(profile["receivers"]),
        "confirm_before_submit": bool(profile.get("confirm_before_submit", True)),
    }


def validate(args) -> dict:
    path = config_path(args.config)
    cfg = load_config(args.config, check_mode=False)
    profiles = get_profiles(cfg)

    # file mode warning (once)
    file_warnings: list = []
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            file_warnings.append(f"配置文件权限 {oct(mode)} 偏宽松，建议 chmod 600")
    except OSError:
        pass

    # optional --profile filter
    if args.profile:
        profiles = [p for p in profiles if p["key"] == args.profile]
        if not profiles:
            raise ReportError(f"日志类型 key 不存在：{args.profile}", reason="profile_not_found",
                              payload={"key": args.profile})

    results = [_validate_one(p, args.resolve_receivers) for p in profiles]
    issues = [i for r in results for i in r["issues"]]
    warnings = file_warnings + [w for r in results for w in r["warnings"]]

    out: Dict[str, Any] = {
        "ok": len(issues) == 0,
        "config_path": path,
        "version": int(cfg.get("version", 1)),
        "migrated_from_v1": migrated_from_v1(cfg),
        "default_profile": cfg.get("default_profile"),
        "profile_count": len(results),
        "issues": issues,
        "warnings": warnings,
        "profiles": results,
    }
    # top-level mirror for single-profile (backward compat with v1 checks)
    if len(results) == 1:
        only = results[0]
        out.update({
            "template": only["template"],
            "receivers": only["receivers"],
            "receiver_count": only["receiver_count"],
            "confirm_before_submit": only["confirm_before_submit"],
        })
    return out


def main():
    p = argparse.ArgumentParser(description="校验日报 Skill 配置（只读）")
    p.add_argument("--config", default=None, help="配置文件路径（覆盖默认/env）")
    p.add_argument("--profile", default=None, help="只校验指定 profile.key（默认全部）")
    p.add_argument("--resolve-receivers", action="store_true", help="通过 dws 解析接收人")
    p.add_argument("-f", "--format", default="json", choices=["json"])
    args = p.parse_args()
    try:
        emit(validate(args))
    except ReportError as e:
        fail(e)


if __name__ == "__main__":
    main()

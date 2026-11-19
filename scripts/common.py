#!/usr/bin/env python3
"""Shared helpers for the dingtalk-daily-report skill.

All deterministic work lives here: config loading (v1 single-template and
v2 multi-profile), profile routing, dws invocation, template schema resolution
and contents assembly. No AI / no narrative generation.
"""

import json
import os
import re
import stat
import subprocess
import sys
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.dingtalk-daily-report/config.json")
CONFIG_ENV = "DINGTALK_DAILY_REPORT_CONFIG"

CURRENT_VERSION = 2
SHARED_KEYS = ("content_type", "item_type", "to_chat", "timezone")
KEY_RE = re.compile(r"^[a-z0-9_-]+$")

# Content-extraction strategy (AI-only step reads these; defaults for colleagues
# who omit the "content" block). Different people can tune how far back to look.
CONTENT_DEFAULTS: Dict[str, Any] = {
    "lookback_sessions": 5,        # how many most-recent sessions to read
    "lookback_days": 3,            # restrict conversation scan to last N days
    "today_first": True,           # prioritise today's conversation
    "fallback_to_previous": True,  # if today has nothing, pull from previous days
    "custom_instruction": "",      # per-person extra instruction for summarisation
}

# Scheduled daily-report defaults. Scheduling is OFF unless the user opts in.
SCHEDULE_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "cron": "",
    "profile": None,
    "timezone": "Asia/Shanghai",
    "durable": True,
    "attendance_gate": "on-duty",  # off | on-duty | approved
    "prompt": "",
}
VALID_GATES = ("off", "on-duty", "approved")


class ReportError(Exception):
    """Structured, user-facing error. Carries an optional payload."""

    def __init__(self, message: str, reason: str = "error", payload: Optional[Dict] = None):
        super().__init__(message)
        self.reason = reason
        self.payload = payload or {}


# ---------------------------------------------------------------------------
# Config file resolution & loading
# ---------------------------------------------------------------------------

def config_path(explicit: Optional[str] = None) -> str:
    """Resolve config path: --config > env > default."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get(CONFIG_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return DEFAULT_CONFIG_PATH


def config_exists(explicit: Optional[str] = None) -> bool:
    return os.path.isfile(config_path(explicit))


def _validate_template(tpl: Any) -> None:
    if not isinstance(tpl, dict):
        raise ReportError("配置 template 必须是对象", reason="config_invalid")
    for k in ("name", "id", "fields"):
        if k not in tpl:
            raise ReportError(f"配置 template 缺少：{k}", reason="config_invalid")
    if not isinstance(tpl.get("fields"), list) or not tpl["fields"]:
        raise ReportError("配置 template.fields 必须是非空数组", reason="config_invalid")
    for f in tpl["fields"]:
        if not isinstance(f, dict) or not f.get("name") or f.get("sort") in (None, ""):
            raise ReportError(f"template.fields 条目需要 name 与 sort：{f}", reason="config_invalid")


def _validate_receivers(recvs: Any) -> None:
    if not isinstance(recvs, list) or not recvs:
        raise ReportError("receivers 必须是非空数组（无接收人的日志对任何人不可见）",
                          reason="config_invalid")
    for r in recvs:
        if not isinstance(r, dict) or not r.get("user_id"):
            raise ReportError(f"接收人条目缺少 user_id：{r}", reason="config_invalid")


def load_config(explicit: Optional[str] = None, check_mode: bool = True) -> Dict[str, Any]:
    """Load and validate config (v1 single-template OR v2 multi-profile)."""
    path = config_path(explicit)
    if not os.path.isfile(path):
        raise ReportError(
            f"配置文件不存在：{path}。请先走首次配置流程。",
            reason="config_missing",
            payload={"config_path": path},
        )
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise ReportError(f"配置 JSON 解析失败：{e}", reason="config_invalid",
                          payload={"config_path": path})

    if not isinstance(cfg, dict):
        raise ReportError("配置根节点必须是对象", reason="config_invalid")

    version = cfg.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise ReportError(f"version 非法：{version}", reason="config_invalid")

    if version >= 2 or "profiles" in cfg:
        profiles = cfg.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            raise ReportError("v2 配置 profiles 必须是非空数组", reason="config_invalid")
        seen = set()
        for p in profiles:
            if not isinstance(p, dict):
                raise ReportError("profiles 条目必须是对象", reason="config_invalid")
            key = p.get("key")
            if not key or not KEY_RE.match(str(key)):
                raise ReportError(f"profile.key 非法（需 [a-z0-9_-]）：{key}", reason="config_invalid")
            if key in seen:
                raise ReportError(f"profile.key 重复：{key}", reason="config_invalid")
            seen.add(key)
            _validate_template(p.get("template"))
            _validate_receivers(p.get("receivers"))
        default = cfg.get("default_profile")
        if default and default not in seen:
            raise ReportError(f"default_profile={default} 不在 profiles 中", reason="config_invalid")
    else:
        # v1 single-template: validate top-level shape
        for key in ("template", "receivers"):
            if key not in cfg:
                raise ReportError(f"v1 配置缺少必填项：{key}", reason="config_invalid",
                                  payload={"config_path": path, "missing": key})
        _validate_template(cfg.get("template"))
        _validate_receivers(cfg.get("receivers"))

    if check_mode:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077:
                sys.stderr.write(
                    f"[warning] 配置文件权限为 {oct(mode)}，建议 chmod 600（含敏感接收人信息）\n"
                )
        except OSError:
            pass

    return cfg


# ---------------------------------------------------------------------------
# Profile normalization / routing
# ---------------------------------------------------------------------------

def content_config(profile: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge content strategy: defaults <- shared cfg.content <- profile.content."""
    merged = dict(CONTENT_DEFAULTS)
    if cfg and isinstance(cfg.get("content"), dict):
        merged.update({k: v for k, v in cfg["content"].items() if k in CONTENT_DEFAULTS})
    if isinstance(profile.get("content"), dict):
        merged.update({k: v for k, v in profile["content"].items() if k in CONTENT_DEFAULTS})
    return merged


def _harvest_aliases(name: str) -> List[str]:
    """Minimal aliases for an auto-wrapped v1 profile."""
    aliases = []
    if name:
        aliases.append(name)
    for token in ("日报", "周报", "月报", "出差", "拜访", "日报总结"):
        if token in (name or "") and token not in aliases:
            aliases.append(token)
    return aliases or ["日报"]


def _migrate_v1(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Wrap a v1 single-template config into one effective profile (in memory)."""
    name = (cfg.get("template") or {}).get("name") or "日报"
    profile = {
        "key": "default",
        "name": name,
        "aliases": _harvest_aliases(name),
        "template": cfg["template"],
        "receivers": cfg["receivers"],
        "confirm_before_submit": bool(cfg.get("confirm_before_submit", True)),
        "content": cfg.get("content", {}),
    }
    return [_normalize_profile(cfg, profile)]


def _normalize_profile(cfg: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill a profile into a full effective view (shared keys inherited)."""
    name = raw.get("name") or raw.get("template", {}).get("name") or "日志"
    key = raw.get("key") or re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "profile"
    aliases = raw.get("aliases")
    if not isinstance(aliases, list) or not aliases:
        aliases = _harvest_aliases(name)
    view: Dict[str, Any] = {
        "key": key,
        "name": name,
        "aliases": [str(a) for a in aliases],
        "template": raw["template"],
        "receivers": raw["receivers"],
        "confirm_before_submit": bool(raw.get("confirm_before_submit", True)),
        "content": content_config(raw, cfg),
    }
    for k in SHARED_KEYS:
        view[k] = raw[k] if k in raw else cfg.get(k, _shared_default(k))
    return view


def _shared_default(key: str) -> Any:
    return {
        "content_type": "markdown",
        "item_type": "1",
        "to_chat": False,
        "timezone": "Asia/Shanghai",
    }.get(key)


def get_profiles(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of normalized effective profile views (v2 or migrated v1)."""
    version = int(cfg.get("version", 1))
    if version >= 2 and isinstance(cfg.get("profiles"), list):
        return [_normalize_profile(cfg, p) for p in cfg["profiles"]]
    return _migrate_v1(cfg)


def migrated_from_v1(cfg: Dict[str, Any]) -> bool:
    return not (int(cfg.get("version", 1)) >= 2 and isinstance(cfg.get("profiles"), list))


def get_schedule(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return effective schedule view with defaults merged. Scheduling off by default."""
    merged = dict(SCHEDULE_DEFAULTS)
    user = cfg.get("schedule")
    if isinstance(user, dict):
        merged.update({k: v for k, v in user.items() if k in SCHEDULE_DEFAULTS})
    if merged["attendance_gate"] not in VALID_GATES:
        merged["attendance_gate"] = "on-duty"  # fail-safe default
    return merged


def get_profile(cfg: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    for p in get_profiles(cfg):
        if p["key"] == key:
            return p
    return None


def _candidates(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"key": p["key"], "name": p["name"], "aliases": p["aliases"],
         "receiver_count": len(p["receivers"])}
        for p in profiles
    ]


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def select_profile(cfg: Dict[str, Any], key: Optional[str] = None,
                   hint: Optional[str] = None,
                   default_profile: Optional[str] = None) -> Dict[str, Any]:
    """Pure routing. Returns dict with action=selected|needs_selection.

    No I/O. Tiers: explicit key > only profile > exact (name/alias) >
    substring (alias/name in hint) > default_profile > needs_selection.
    """
    profiles = get_profiles(cfg)
    default = default_profile if default_profile is not None else cfg.get("default_profile")

    if key:
        prof = get_profile(cfg, key)
        if not prof:
            raise ReportError(f"日志类型 key 不存在：{key}", reason="profile_not_found",
                              payload={"key": key, "candidates": _candidates(profiles)})
        return {"action": "selected", "key": prof["key"], "name": prof["name"],
                "matched_by": "explicit_key", "profile": prof}

    if len(profiles) == 1:
        prof = profiles[0]
        return {"action": "selected", "key": prof["key"], "name": prof["name"],
                "matched_by": "only_profile", "profile": prof}

    h = _norm(hint)

    # Tier 1: exact
    exact = []
    for p in profiles:
        if h and h == _norm(p["name"]):
            exact.append((p, "name_exact"))
            break
    if not exact:
        for p in profiles:
            if any(h == _norm(a) for a in p["aliases"]):
                exact.append((p, "alias_exact"))
    if exact:
        # name exact wins over alias exact; dedupe by key keeping first tier
        p, how = exact[0]
        return {"action": "selected", "key": p["key"], "name": p["name"],
                "matched_by": how, "profile": p}

    # Tier 2: substring (alias/name appears IN hint, len>=2, directional)
    sub = []
    if h:
        for p in profiles:
            terms = [_norm(p["name"])] + [_norm(a) for a in p["aliases"]]
            hit = any(len(t) >= 2 and t in h for t in terms if t)
            if hit:
                sub.append(p)
    # dedupe
    uniq = {p["key"]: p for p in sub}
    sub = list(uniq.values())
    if len(sub) == 1:
        p = sub[0]
        return {"action": "selected", "key": p["key"], "name": p["name"],
                "matched_by": "alias_substring", "profile": p}
    if len(sub) > 1:
        names = " / ".join(p["name"] for p in sub)
        return {"action": "needs_selection", "reason": "ambiguous", "hint": hint,
                "prompt": f"匹配到多个日志类型，请告诉我要写哪一个：{names}",
                "candidates": _candidates(sub)}

    # Tier 3: default or ask
    if default:
        prof = get_profile(cfg, default)
        if prof:
            return {"action": "selected", "key": prof["key"], "name": prof["name"],
                    "matched_by": "default", "profile": prof}

    return {"action": "needs_selection", "reason": "no_match", "hint": hint,
            "prompt": "没识别到要写哪种日志，请从列表里选一个："
                      + " / ".join(p["name"] for p in profiles),
            "candidates": _candidates(profiles)}


def resolve_profile(cfg: Dict[str, Any], key: Optional[str] = None,
                    hint: Optional[str] = None) -> Dict[str, Any]:
    """Return effective profile view or raise ReportError on needs_selection."""
    result = select_profile(cfg, key=key, hint=hint)
    if result["action"] == "needs_selection":
        raise ReportError(
            result["prompt"], reason="profile_needs_selection",
            payload={
                "action": "needs_selection",
                "reason": result["reason"],
                "prompt": result["prompt"],
                "candidates": result["candidates"],
            },
        )
    return result["profile"]


# ---------------------------------------------------------------------------
# dws runner & contents assembly (unchanged behaviour)
# ---------------------------------------------------------------------------

def run_dws(args: List[str], timeout: int = 60) -> Dict[str, Any]:
    """Run a dws command, parse its JSON output. Raises ReportError on failure."""
    cmd = ["dws"] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ReportError("未找到 dws 命令，请先安装 dingtalk-workspace-cli",
                          reason="dws_missing")
    except subprocess.TimeoutExpired:
        raise ReportError(f"dws 命令超时（{timeout}s）: {' '.join(cmd)}", reason="dws_timeout")

    out = proc.stdout.strip()
    payload = _extract_json(out)
    if payload is None:
        err = (proc.stderr or out or "(empty output)").strip()
        raise ReportError(f"dws 未返回 JSON：{err[:500]}", reason="dws_bad_output",
                          payload={"command": cmd, "stdout": out[:500], "stderr": proc.stderr[:500]})

    if isinstance(payload, dict):
        if "error" in payload:
            err = payload["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise ReportError(f"dws 调用失败：{msg}", reason=err.get("reason", "dws_error")
                              if isinstance(err, dict) else "dws_error",
                              payload={"command": cmd, "error": err})
        if payload.get("errcode") not in (None, 0):
            raise ReportError(
                f"dws 返回 errcode={payload.get('errcode')}: {payload.get('errmsg')}",
                reason="dws_errcode", payload={"command": cmd, "result": payload},
            )
        if payload.get("success") is False:
            raise ReportError(f"dws success=false: {payload.get('message') or payload}",
                              reason="dws_false", payload={"command": cmd, "result": payload})
    return payload


def _extract_json(text: str) -> Optional[Any]:
    """Extract the first complete JSON value from a (possibly prefixed) string."""
    if not text:
        return None
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def get_template_fields(name: str) -> Dict[str, Any]:
    """Fetch live template schema from dws. Returns {id,name,fields:[{name,sort,type}]}."""
    payload = run_dws(["report", "template", "get", "--name", name, "-f", "json"])
    result = payload.get("result") or {}
    fields_raw = result.get("report_template_fields")
    if not isinstance(fields_raw, list) or not fields_raw:
        raise ReportError(f"模板「{name}」未返回字段定义", reason="template_no_fields",
                          payload={"result": result})
    fields = []
    for f in fields_raw:
        fields.append({
            "name": f.get("field_name"),
            "sort": int(f.get("field_sort", 0)),
            "type": int(f.get("field_type", 1)),
        })
    return {
        "id": result.get("report_template_id") or result.get("template_id"),
        "name": result.get("report_template_name") or name,
        "fields": fields,
    }


def build_contents(payload: Dict[str, Any], live_template: Dict[str, Any],
                   profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate fields payload against live template and build contents array."""
    fields_payload = payload.get("fields")
    if not isinstance(fields_payload, dict):
        raise ReportError("fields-json 缺少 fields 对象", reason="fields_bad")

    live_names = [f["name"] for f in live_template["fields"]]
    given_names = list(fields_payload.keys())
    missing = [n for n in live_names if n not in fields_payload]
    extra = [n for n in given_names if n not in live_names]
    if missing or extra:
        raise ReportError(
            "字段与实时模板不一致，请重新读取模板字段后修正 fields-json",
            reason="fields_drift",
            payload={"missing": missing, "extra": extra, "live_fields": live_names},
        )

    content_type = profile.get("content_type", "markdown")
    item_type = str(profile.get("item_type", "1"))
    contents = []
    sort_by_name = {f["name"]: f["sort"] for f in live_template["fields"]}
    for f in live_template["fields"]:
        name = f["name"]
        value = fields_payload.get(name)
        if value is None:
            value = ""
        contents.append({
            "key": name,
            "sort": str(sort_by_name[name]),
            "content": str(value),
            "contentType": content_type,
            "type": item_type,
        })
    return contents


def receiver_ids(profile: Dict[str, Any]) -> str:
    return ",".join(r["user_id"] for r in profile["receivers"])


def emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(err: ReportError) -> None:
    emit({"ok": False, "reason": err.reason, "error": str(err), **err.payload})
    sys.exit(2)

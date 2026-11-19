---
name: dingtalk-daily-report
description: 根据近期与 AI 的对话总结并提交钉钉日志（走 dws report），支持配置多种日志类型（日报/周报/出差日志等）按意图路由，并可设置每天定时自动写。Use when 用户说 写日志/写日报/写周报/写出差日志/写日报并提交/提交日报/生成日报/日报总结/填写日报/帮我交日报/设置定时日报/每天几点写日志，或下班前要求把今天/最近的工作整理成日志提交。配置缺失时先走首次设置；意图不清先询问。命令前缀：dws report。
metadata:
  cli_version: ">=1.0.61"
  category: product
  requires:
    bins:
      - dws
---

# 钉钉日志 Skill（dingtalk-daily-report）

## 职责边界（分而治之）

- **AI 只做三件事**：① 判断用户要写哪种日志（意图不清就问）；② 把近期对话按字段**提炼成正文**（只用事实，不编造）；③ 展示预览、取得用户确认。
- **其余全部是确定性脚本**（`scripts/`）：日志类型路由、模板发现、字段→sort 映射、漂移检测、拼 JSON、调 dws、提交、回读校验。
- 不要自己手敲 `dws report` 命令去提交，一律走 `scripts/report_submit.py`。

## 脚本一览（相对本 skill 目录调用）

| 脚本 | 用途 | 写操作？ |
|---|---|---|
| `python3 scripts/report_profile.py list` | 列出所有日志类型（profile） | 只读 |
| `python3 scripts/report_profile.py select --hint "<用户原话>"` 或 `--key KEY` | 路由选定日志类型 | 只读 |
| `python3 scripts/report_inspect.py list-templates [--query X]` | 列可用钉钉模板 | 只读 |
| `python3 scripts/report_inspect.py get-fields --name "<模板名>"` | 读模板字段 | 只读 |
| `python3 scripts/config_validate.py [--profile KEY]` | 校验配置/接收人 | 只读 |
| `python3 scripts/report_submit.py --fields-json ./f.json --profile KEY --dry-run` | 预览（不提交） | 否 |
| `python3 scripts/report_submit.py --fields-json ./f.json --profile KEY --confirmed` | 真实提交 | **是** |

所有脚本默认读 `~/.dingtalk-daily-report/config.json`；可用 `--config PATH` 或环境变量 `DINGTALK_DAILY_REPORT_CONFIG` 覆盖。

## 配置检测（每次入口第一步）

1. 跑 `python3 scripts/config_validate.py`。
2. 若 `reason=config_missing` → 走【首次设置】。
3. 若有 `issues`（字段漂移等）→ 停下来按提示处理，不要带病提交。
4. `profiles` 列出用户已配置的日志类型；多类型时每次都要先【路由】。

## 首次设置（交互式，配置文件由你写）

> 没有 setup 脚本：由你问用户、写 JSON、设权限。收集到的值是**用户自己的配置**，不要硬编码起别人的值当默认。

1. **循环添加日志类型**（可多种）：对每一种问：
   - 日志类型名称 `name`（如"日报"/"周报"/"出差日志"）与 `key`（英文短标识，如 `daily`/`weekly`/`trip`）。
   - `aliases`：**"你平时用哪个词指这份日志？"**（如 日报/工作日报/daily）——多个词便于路由。
   - 模板：用 `list-templates [--query]` 搜出候选让用户选，`get-fields --name` 拿精确字段展示核对。
   - 接收人：姓名 + userId 列表；可用 `config_validate.py --resolve-receivers` 辅助验证。
   - 提炼策略 `content`：`lookback_sessions`(读几条会话)、`lookback_days`(看几天内)、`today_first`、`fallback_to_previous`(今天没内容是否用前几天)、`custom_instruction`(个人额外要求)。
   - `confirm_before_submit`（建议 true）。
   - 问"还要再加一种日志吗？"→ 是则重复，否则继续。
2. **默认类型**：多类型时问"只说'写日志'时默认写哪种？"→ `default_profile` 填其 key（用户若说"不确定就问"，可不设默认）。
3. **定时任务**（可选）：问"要不要每天定时自动写？几点？写哪种？"→ 见【定时任务】章节配置 `schedule`。
4. **写配置**到 `~/.dingtalk-daily-report/config.json`（v2 结构）：
   ```json
   {
     "version": 2,
     "default_profile": "daily",
     "content_type": "markdown", "item_type": "1", "to_chat": false,
     "timezone": "Asia/Shanghai",
     "schedule": {"enabled": true, "cron": "15 18 * * *", "profile": "daily",
                  "durable": true, "prompt": "按 dingtalk-daily-report skill 写今天的日报并提交（用 profile=daily）"},
     "profiles": [
       {
         "key": "daily", "name": "日报",
         "aliases": ["日报", "工作日报", "daily"],
         "template": {"name":"<模板名>","id":"<id>","fields":[{"name":"<字段>","sort":1}]},
         "receivers": [{"label":"<姓名>","user_id":"<userId>"}],
         "confirm_before_submit": true,
         "content": {"lookback_sessions":5,"lookback_days":3,"today_first":true,"fallback_to_previous":true,"custom_instruction":""}
       }
     ]
   }
   ```
   > 旧版 v1（单 `template`）配置会被脚本在内存自动包装成单 profile，无需迁移；当新增第二种类型时写成上面完整 v2。
5. **设权限并复验**：`chmod 700 ~/.dingtalk-daily-report`；`chmod 600 .../config.json`；再跑 `config_validate.py`，`ok:true` 即完成。

## 路由（每次"写日志"第 0 步，多类型必做）

1. 跑 `python3 scripts/report_profile.py select --hint "<用户原话>"`。
2. `action=selected` → 用返回的 `profile`（含 template/receivers/content/confirm）。触发映射：写日报→hint"日报"；写周报→"周报"；出差日志→"出差"；裸"写日志"→原话。
3. `action=needs_selection` → **绝不猜**：把 `prompt` 和 `candidates` 展示给用户问"写哪个"，用户选定后用 `select --key <key>` 重跑，再继续。

## 日常流程（已选定 profile 后）

1. 用该 profile 的 `content` 策略（脚本已把默认值合并进 profile.content）：按 `lookback_sessions`/`lookback_days`/`today_first`/`fallback_to_previous` 决定读哪些对话；今天没东西按配置回退前几天。套用 `custom_instruction`。
2. **AI 独占：提炼正文**。只从对话取事实；参考上一份日志的"明日计划"（`dws report +report-latest`）作线索但非必填。按 profile 的模板字段逐一写正文：**没信息的字段写"无"或先问用户，绝不编造**。
3. 在当前工作目录写 `./daily-report-fields.json`：
   `{"date":"YYYY-MM-DD","fields":{"<精确字段名1>":"<markdown>",...}}`，key 必须与 `get-fields` 字段名逐字一致。
4. **dry-run 预览**：`python3 scripts/report_submit.py --fields-json ./daily-report-fields.json --profile <key> --dry-run`，把 `preview` 渲染给用户（模板名、每个字段正文摘要、接收人），说明这是预览、还没提交。
5. **确认闸门**（`confirm_before_submit:true`）：明确问"确认提交这份<日志类型>给这 N 个人吗？"；用户同意后才跑 `... --profile <key> --confirmed` 真提交。改动内容/接收人后必须重新 dry-run + 确认。
6. **回执**：返回 `ok:true`、`report_id`、`verified`、`url` 后把钉钉链接给用户；`verified:false` 则提示去钉钉核对。

## 定时任务（每天自动写）

> 用 CodeBuddy 的 `CronCreate` 工具（调度提示词，在本会话触发）。**会话级任务：Agent 不在线 / 会话结束，任务就不会触发——正好符合"不在线就不写"**。循环任务默认 3 天后自动过期。

1. 配置 `schedule` 块（见上）：`enabled`、`cron`（5 段本地时间，6:15 = `15 18 * * *`）、`profile`、`durable`（建议 true，写盘跨重启仍在本会话触发）、`prompt`。
2. 用户要求"每天定时写日志"时：
   - 确认 `schedule.enabled`；未配置则先问时间+写哪种并写入。
   - 调 `CronCreate`：`cron` 取 `schedule.cron`；`prompt` 取 `schedule.prompt`（明确写清"按本 skill 写今日<类型>并提交，profile=<key>"）；`recurring:true`；`durable` 取 `schedule.durable`。
   - **告知用户**：任务 ID、每天触发时间、以及"仅在本 Agent 会话在线时触发；循环任务 3 天自动过期，需续期可重新设置"。
3. 用 `CronList` 查看、需要时取消后重建。不要把定时写成 hook。

## 规则 / 禁止

- **不编造正文**；信息不足写"无"或问用户。
- **多日志类型不猜**：路由 `needs_selection` 时按候选提问。
- **提交一律带 `--profile`**；`--hint` 只用于路由，不用于提交。
- **不走原生 `dws report entry submit`**，一律 `report_submit.py`。
- `--contents-file` 只用 cwd 相对路径（脚本已处理），不改成 `/tmp/...`。
- **dry-run 不是提交**；没明确确认不得加 `--confirmed`。
- 字段漂移 → 停下让用户重跑首次设置；`--to-user-ids` 非空。
- 同事复用：整包 clone 到 `~/.agents/skills/` 并软链到 `~/.codebuddy/skills/`，首次说"写日志"自动走设置；不用改代码。

## 错误恢复

看脚本返回 JSON 的 `reason`，对照 `references/report-contract.md`：
- `config_missing`→首次设置；`fields_drift`→刷新配置；`confirmation_required`→补 `--confirmed`；
- `profile_needs_selection`/`profile_not_found`→按 `candidates`/`prompt` 问用户或改 `--key`；
- `dws_bad_output`/`dws_errcode`→`dws auth status` 查登录，失效则 `dws auth login` 重扫。
更全边界见 `/Users/palop/.agents/skills/dingtalk-misc/references/report.md` 与 `/Users/palop/.agents/skills/dingtalk-shared/references/confirmation.md`。

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
     "schedule": {"enabled": false, "cron": "15 18 * * *", "profile": "daily",
                  "timezone": "Asia/Shanghai", "durable": true, "attendance_gate": "on-duty",
                  "prompt": "按 dingtalk-daily-report skill 写今日日报并提交（profile=daily；提交前先跑 attendance_gate.py，should_write=false 就跳过）"},
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

## 定时任务（先问清楚，再配置）

> 核心原则：**默认不开定时任务**；只要用户要开，必须先逐项问清并确认，再写 `schedule` 配置 + 建 cron。**宁可不写，也绝不瞎写。**
> 用 `CronCreate`（会话级任务：Agent 不在线/会话结束就不触发；循环任务 3 天自动过期）。不要写成 hook。

### 设置流程（触发词：设置定时日报 / 每天定时写 / 取消定时）

用户要求开定时任务时，**先问、确认、再配置**：

1. **问时间**：每天几点？（如 18:15 → `cron` = `15 18 * * *`，本地时区）。问是否只工作日（cron 加 `1-5`；但注意调休，推荐保留每天 + 考勤门禁兜底）。
2. **问写哪种日志**：多 profile 时用 `report_profile.py list` 列候选让用户选（复用路由）。
3. **问考勤门禁**（三档，把含义讲清楚）：
   - `off` 不检查 —— 周末/请假也照写（用户明确要才用）。
   - `on-duty`（默认）—— **有上班打卡才写**；周末/请假/未打卡自动跳过。涵盖大部分情况。
   - `approved`（严格）—— 有上班打卡**或**当天有出差/外出审批单才算上班；纯请假/无记录跳过。
4. **问是否跨重启保留**：`durable`（建议 true）。
5. **汇总确认**：把时间、日志类型、门禁档位、durable 写成一段话给用户，**明确确认后**才写 `schedule` 配置并调 `CronCreate`。
   - `CronCreate` 参数：`cron`=配置值；`prompt`=明确写"按 dingtalk-daily-report skill 写今日<类型>并提交（profile=<key>；提交前必须先跑 attendance_gate.py 判断今天该不该写，should_write=false 就跳过）"；`recurring:true`；`durable`=配置值。
   - 告知用户：任务 ID、触发时间、"仅本 Agent 会话在线时触发"、3 天过期需续期。
6. **取消**：`CronList` 查 → 问用户确认 → 删除任务 → 配置 `schedule.enabled=false`。

### 定时触发时（cron 的 prompt 引导）

1. **先跑门禁**：`python3 scripts/attendance_gate.py --date <今天>`。
2. `should_write:false` → **不写日报**，在会话里说明原因（如"今天无上班打卡，已跳过日报"），结束。**不要试图绕过门禁硬写。**
3. `should_write:true` → 走正常日常流程（路由 → 提炼 → dry-run → 确认/提交）。
4. 任何异常、查询失败 → **fail-closed 跳过**并告知（脚本已内置 fail-closed）。

## 规则 / 禁止

- **不编造正文**；信息不足写"无"或问用户。
- **多日志类型不猜**：路由 `needs_selection` 时按候选提问。
- **提交一律带 `--profile`**；`--hint` 只用于路由，不用于提交。
- **不走原生 `dws report entry submit`**，一律 `report_submit.py`。
- `--contents-file` 只用 cwd 相对路径（脚本已处理），不改成 `/tmp/...`。
- **dry-run 不是提交**；没明确确认不得加 `--confirmed`。
- **定时任务默认关闭**；开启必须逐项问清并确认；**默认带考勤门禁**；**宁可不写，不瞎写**，判断不了就跳过。
- 字段漂移 → 停下让用户重跑首次设置；`--to-user-ids` 非空。
- 同事复用：整包 clone 到 `~/.agents/skills/` 并软链到 `~/.codebuddy/skills/`，首次说"写日志"自动走设置；不用改代码。

## 错误恢复

看脚本返回 JSON 的 `reason`，对照 `references/report-contract.md`：
- `config_missing`→首次设置；`fields_drift`→刷新配置；`confirmation_required`→补 `--confirmed`；
- `profile_needs_selection`/`profile_not_found`→按 `candidates`/`prompt` 问用户或改 `--key`；
- `dws_bad_output`/`dws_errcode`→`dws auth status` 查登录，失效则 `dws auth login` 重扫。
更全边界见 `/Users/palop/.agents/skills/dingtalk-misc/references/report.md` 与 `/Users/palop/.agents/skills/dingtalk-shared/references/confirmation.md`。

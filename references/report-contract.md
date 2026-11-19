# dws report 契约速查（本 skill 依赖的精确接口）

所有命令都已在本机 `dws v1.0.68` 实测。出错时回来查这里，不要猜参数。

## 1. 登录态

- `dws auth login` 扫码登录；凭证加密存 macOS Keychain，自动刷新，无需重复扫码。
- `dws auth status` 看当前组织 / user_id / 过期时间。
- `dws profile list -f json` 看 profile（`corpId:userId`）。
- **raw `dws api` 不可用**：OAuth 登录的加密 token 不能调 raw API（`dws api` 需要自有应用 AppKey/AppSecret）。本 skill 只用封装好的 `dws report`。

## 2. 列模板

```bash
dws report template list -f json
```
返回：`{"items":[{"report_template_id":"...","report_template_name":"...","last_modified_time":...}]}`

## 3. 读模板字段

```bash
dws report template get --name "<精确模板名>" -f json
```
返回：`{"errcode":0,"result":{"report_template_id":"...","report_template_name":"...",
  "report_template_fields":[
    {"field_name":"一、今日学习的产品知识、技术开发知识","field_sort":1,"field_type":1},
    ...
  ]}}`

- `field_name` 是提交时 `contents[].key` 的唯一合法值（逐字一致）。
- `field_sort` → `contents[].sort`（脚本转成字符串）。
- `field_type` 1 = 文本 → `contents[].type = "1"`。
- 旧命令 `template detail --name` 仍可用但有 deprecated 警告；用 `template get`。

## 4. 提交日报（唯一写路径）

```bash
dws report entry submit \
  --template-id <TEMPLATE_ID> \
  --contents-file ./report.json \
  --to-user-ids <id1,id2,...> \
  [--to-chat] [--dry-run] -f json
```

- `contents-file`：**只能用当前工作目录内的相对路径**（如 `./x.json`），禁止 `/tmp/...` 等绝对路径。脚本会自动在 cwd 写 `./.daily-report-contents.build.json` 并在结束前删除。
- `contents`：JSON 数组，每项：
  ```json
  {"key":"<field_name>","sort":"1","content":"<markdown正文>","contentType":"markdown","type":"1"}
  ```
  - `key` 必须等于实时模板 `field_name`；缺失/多余字段都会被本 skill 拒绝（fields_drift）。
  - 编码后上限 10MB，超出需精简。
- `--to-user-ids`：**必填**，逗号分隔。无接收人时服务端仍返回成功，但日志对任何人不可见。本 skill 强制非空。
- `--to-chat`：可选，是否同时发到接收人单聊（配置 `to_chat`）。
- `--dry-run`：只预览参数，不提交；返回 `{"arguments":{...},"dry_run":true,"executed":false,"tool":"create_report"}`。
- **dws 的 `confirmation=not_required`，CLI 没有确认闸门**。本 skill 的确认闸门在 `report_submit.py --confirmed`，不要依赖 CLI，也不要加 `--yes`。

### 提交成功回读

```bash
dws report entry get --report-id <REPORT_ID> -f json
```
返回 `result.report_content[]{key,sort,type,value}`、`result.url`（钉钉跳转链接）。脚本用它校验字段写对没有。

## 5. 接收人解析（首次配置用）

```bash
dws aisearch person --query "张三" --dimension name -f json
dws aisearch person --query "13800138000" --dimension phone -f json
dws contact +lookup --name "张三" -f json
dws contact +by-mobile --mobile "13800138000" -f json
```
- 手机号形式的 userId（如 `13800138000`）按 `phone` 维度搜。
- 自定义短 id（如 `zhangsan`）通常解析不到标准结果——视为合法，warning 而非失败（用户手工提供）。
- 0 个或多个匹配都要让用户确认，不要静默替换。

## 6. 日志类型（profile）路由

v2 配置含 `profiles[]`，每种日志有自己的 template/receivers/content。脚本路由是纯确定性、无 dws：

```bash
python3 scripts/report_profile.py list
python3 scripts/report_profile.py select --hint "帮我写周报"
python3 scripts/report_profile.py select --key weekly
```

匹配分层（取最高层唯一命中）：显式 `--key` > 仅一个 profile > 精确(alias/name) > 子串(alias/name 出现在 hint，长度≥2，方向性) > default_profile > needs_selection。

输出形状：
- 选中：`{"ok":true,"action":"selected","key":"weekly","name":"周报","matched_by":"alias_substring","profile":{完整有效视图}}`
- 需询问：`{"ok":true,"action":"needs_selection","reason":"ambiguous|no_match","prompt":"...","candidates":[{key,name,aliases,receiver_count}]}`
- 坏 key：`{"ok":false,"reason":"profile_not_found","key":"bogus","candidates":[...]}`

**绝不猜**：`needs_selection` 时按 `prompt`/`candidates` 问用户，选定后用 `--key` 重跑。提交一律 `report_submit.py --profile <key>`。

## 7. 定时任务（CronCreate）

配置 `schedule` 块：`{"enabled":true,"cron":"15 18 * * *","profile":"daily","durable":true,"prompt":"..."}`。
- cron 是 5 段**本地时间**；6:15 = `15 18 * * *`。
- `CronCreate` 是**会话级**：Agent 不在线/会话结束即不触发（符合"不在线就不写"）；`durable:true` 写盘跨重启仍在本会话触发。
- 循环任务默认 **3 天自动过期**，需告知用户并可续期。用 `CronList` 查看/取消。

## 8. 错误恢复

| reason | 含义 | 处理 |
|---|---|---|
| `config_missing` | 无配置 | 走 SKILL.md 首次设置 |
| `fields_drift` | 配置字段与实时模板不符 | 重跑首次设置刷新配置 |
| `confirmation_required` | 真提交无 `--confirmed` | 展示 dry-run 预览 → 用户确认 → 加 `--confirmed` 重跑 |
| `profile_needs_selection` | 多类型意图不清 | 按 payload.prompt/candidates 问用户，选定后 `--key` 重跑 |
| `profile_not_found` | `--key` 不存在 | 用 `report_profile.py list` 查合法 key |
| `dws_bad_output` / `dws_errcode` | dws 报错 | 看 payload.error / errcode，查 `dws auth status` 登录态，必要时 `dws auth login` |
| `template_no_fields` | 模板无字段 | 确认模板名精确、当前用户可用该模板 |

更全的边界规则见：
- `/Users/palop/.agents/skills/dingtalk-misc/references/report.md`
- `/Users/palop/.agents/skills/dingtalk-shared/references/confirmation.md`

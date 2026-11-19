# dingtalk-daily-report

一个 CodeBuddy Skill：把当天和 AI 的对话按钉钉日志模板提炼成日报，并通过官方 `dws` CLI 提交。

基于 **harness（分而治之）思想**：确定性的流程（路由、模板发现、字段映射、拼 JSON、调 CLI、提交、校验）固化成脚本；只有"把对话提炼成正文"和"意图不清时提问"交给 AI。

## 特性

- **多日志类型（profile）**：日报 / 周报 / 出差日志等，每种有独立的模板、接收人、提炼策略、确认偏好。
- **意图路由**：说"写周报"自动选周报；说"出差日报"这种歧义会**列候选让你选，不猜**。
- **确认闸门**：统一先 `dry-run` 预览 → 你确认 → 才真实提交（dws 原生无确认机制）。
- **提炼策略可配**：看最近几条会话 / 几天、今天没内容是否回退前几天、自定义要求。
- **定时可选**：可配每天定时自动写（会话级 cron，Agent 不在线就不触发）。
- **可分享**：配置与逻辑分离，代码里不含任何个人 userId / 模板，同事装完走首次配置即可用。

## 文件结构

```
dingtalk-daily-report/
├── SKILL.md                       # Skill 定义：触发词、首次设置、日常流程、规则
├── references/report-contract.md  # dws report 接口契约速查
├── scripts/
│   ├── common.py                  # 配置(v1/v2)、profile 路由、dws 封装、contents 构建
│   ├── report_profile.py          # 日志类型路由（list / select）
│   ├── report_inspect.py          # 模板/字段发现（只读）
│   ├── config_validate.py         # 配置校验 + 接收人解析（只读）
│   └── report_submit.py           # 唯一写路径：dry-run / 确认闸门 / 提交 / 回读
```

## 前置条件

- Node.js（用于 `npm install -g dingtalk-workspace-cli`）或 Go 1.25+（源码构建）
- 一个可用的钉钉账号，且所在组织允许通过 dws CLI 访问（个人扫码登录即可，普通员工无需管理员审批；若提示需审批则找管理员开一下 CLI 访问）

---

## 安装提示词（给 AI 用）

> **下面是给智能体（CodeBuddy / Claude Code / Cursor 等支持 Skill 的工具）的自然语言指令。复制整段发给你的 AI，它会自动完成安装，你只需在第一步扫码登录、最后按提示回答几个配置问题。**

```
请帮我安装一个叫 dingtalk-daily-report 的 CodeBuddy Skill，按下面步骤操作：

【第 0 步：安装钉钉 CLI 并登录】
1. 检查本机是否已装钉钉 CLI：运行 `dws --version`。
2. 若未安装，运行 `npm install -g dingtalk-workspace-cli` 安装（Node 环境）；
   安装后再次 `dws --version` 确认。
3. 运行 `dws auth login`，会弹出浏览器/二维码，我会用钉钉扫码授权。
   登录完成后用 `dws auth status` 确认 authenticated=true。
   如果提示"组织未开启 CLI 访问"，告诉我，我会去找管理员开通后重试。

【第 1 步：拉取 Skill 代码】
4. 把这个仓库克隆到 Skill 目录（真实目录放 ~/.agents/skills/）：
   git clone https://github.com/Palopies/dingtalk-daily-report.git ~/.agents/skills/dingtalk-daily-report
   如果目标目录已存在，先问我是否覆盖。
5. 建立软链，让 CodeBuddy 能识别这个 Skill：
   cd ~/.codebuddy/skills && ln -s ../../.agents/skills/dingtalk-daily-report dingtalk-daily-report
   （若 ~/.codebuddy/skills 不存在先 mkdir -p）
6. 确认软链生效：ls -la ~/.codebuddy/skills/dingtalk-daily-report 能解析到目录，
   且能看到 SKILL.md。必要时重启我（AI 工具）让 Skill 被重新加载。

【第 2 步：首次配置】
7. 现在对我说"写日报"来触发首次配置流程。按 SKILL.md 的指引，依次问我：
   - 要配置几种日志类型（如日报/周报/出差日志），每种的名称、key、别名（平时用哪个词指它）；
   - 每种用哪个钉钉日志模板（先用 `python3 scripts/report_inspect.py list-templates` 列出候选给我选，
     再用 get-fields 读字段给我核对）；
   - 每种的接收人（姓名 + userId，可用 `config_validate.py --resolve-receivers` 帮我核对）；
   - 每种的提炼策略（看最近几条会话/几天、今天没内容是否回退前几天、自定义要求）；
   - 是否每次提交前都要我确认；
   - 只说"写日志"时默认写哪种。
8. 把配置写到 ~/.dingtalk-daily-report/config.json，并设权限：
   chmod 700 ~/.dingtalk-daily-report && chmod 600 ~/.dingtalk-daily-report/config.json
9. 运行 `python3 scripts/config_validate.py` 校验，直到 ok:true。
   最后问我要不要设置每天定时自动写日志；要的话用 CronCreate 设置。

【第 3 步：验收】
10. 用 `dws report template list` 和一次 `report_submit.py --dry-run`（不真实提交）验证整条链路通畅，
    把 dry-run 预览结果给我看，确认字段、接收人无误即可。

注意：整个过程中不要真实提交日报（不要加 --confirmed），除非我明确说"提交"。
```

---

## 日常使用

装好之后：

- **写日报** / **写周报** / **写出差日志** → AI 自动路由到对应类型，从当天对话提炼内容 → 先预览 → 你确认 → 提交
- **写日志**（不指定类型）→ 配了默认则用默认，否则 AI 列出候选让你选
- 说"改一下提炼策略 / 加个日志类型 / 设置定时日报" → 走对应配置流程

## 安全说明

- 代码里不含任何个人 userId、模板 ID、接收人信息，这些都在首次配置时写入本机 `~/.dingtalk-daily-report/config.json`（权限 600），不会进入仓库。
- 钉钉登录凭证由 `dws` 加密存在系统 Keychain，Skill 不接触、不存储。
- 提交前强制 dry-run 预览 + 明确确认，避免误发。

## 许可

MIT

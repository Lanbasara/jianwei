# 探源 Tanyuan

一份 Skill + `ty` CLI，供 Claude Code、Codex、DSH 使用。用户选择哪个工作区，就安装到哪个工作区；三个宿主共用同一本体。

## 安装到你选择的工作区

需要 macOS 或 Linux、Python 3.10+。先创建或选择一个工作区，然后在解压后的交付包目录执行（把示例路径换成你的工作区）：

```bash
python3 install.py --workspace /absolute/path/to/your-workspace --host codex dsh
```

`--workspace` 必填，不设默认位置。`--host` 可选 `claude`、`codex`、`dsh` 中的一个或多个。安装器只在指定工作区写入以下内容，不修改用户级技能目录或全局 PATH：

```text
your-workspace/
├── .tanyuan/
│   ├── plugin/skills/tanyuan/   # Skill、CLI、独立 Python 环境
│   ├── bin/ty                  # 本工作区的 CLI 入口
│   └── cache/                  # 使用时产生的缓存
├── .agents/skills/tanyuan      # 选择 codex 时创建
├── .dsh/skills/tanyuan         # 选择 dsh 时创建
├── .claude/skills/tanyuan      # 选择 claude 时创建
└── .env.example               # 无密钥的配置模板
```

默认在该工作区创建独立 Python 环境，从 PyPI 安装 requirements.txt 中固定版本的 requests 和 pypdf。离线环境可加 `--python /absolute/path/to/venv/bin/python`，复用已有依赖的 Python；该解释器必须保留。pypdf 按需加载，缺少它时本地 PDF 提取报告依赖错误，其他工具仍可使用。

安装是复制交付本体，不链接开发仓库；安装完成后可以移走解压包。相同版本可重复运行。已有其他同名文件会报冲突，不覆盖；不同版本也不会自动覆盖旧安装。

安装后在所选工作区启动新的宿主会话。Codex 中将该目录作为项目工作区，DSH 在该目录启动。agent 可以从技能目录调用 `bin/ty`；你也可以在工作区根目录执行 `./.tanyuan/bin/ty`。

## 在工作区配置 .env

在所选工作区把 `.env.example` 复制为 `.env`，用文本编辑器填写自己使用的数据源凭据：

```bash
cp .env.example .env  # 仅在尚无 .env 时执行
chmod 600 .env
```

`TUSHARE_TOKEN`、`DEEPSEEK_API_KEY`、`TAVILY_API_KEY` 按需填写；`SEC_CONTACT` 是 SEC 请求使用的联系标识。空着的凭据不会启用相应数据源。不要提交 `.env` 或把密钥发到聊天中。交付包只有空模板，不含任何真实 `.env`。

读取顺序：进程环境变量优先；指定 `TANYUAN_ENV_FILE` 时读取该文件，否则读取安装工作区的 `.env`。不查找用户全局配置，也不向父目录搜索。即使 CLI 从子目录调用，仍读取安装工作区的 `.env`。缓存默认在该工作区 `.tanyuan/cache`，可显式通过 `TANYUAN_CACHE` 改位置。

## 使用

在所选工作区根目录执行：

```bash
./.tanyuan/bin/ty --help
./.tanyuan/bin/ty help config
./.tanyuan/bin/ty help response
./.tanyuan/bin/ty --version
./.tanyuan/bin/ty tools
./.tanyuan/bin/ty describe market.quote
./.tanyuan/bin/ty call market.quote --json '{"codes":["600036"]}'
```

让 agent 使用时，可以直接说：“使用 tanyuan 查询数据，将报告写到当前工作区的 reports 目录，并注明来源和日期。”宿主需要允许本地命令执行和数据源网络访问。

成功响应读取 `.data`、`.provenance`，失败读取 `.error`。业务日期与取得时间不同。详细规则见 `SKILL.md` 与 `references/`。`ty doctor` 会联网排障，可能调用计费数据源。`ty garden` 用于源清单维护，写入动作需用户批准。

基金与 ETF 使用 `fund.*` 工具，先通过 `fund.resolve` 确认份额身份。Tushare 提供数字，公开来源补充净值、披露持仓和报告；接口权限与覆盖以实际响应为准。公开 PDF 可通过 `fetch.url` 的 `provider=local` 本地提取；正文缓存与旧提取方式隔离，使用 `document_version` 续读。原 PDF、分页文字和元数据存放在工作区缓存的 documents/，上限 512 MiB，超限清理较早版本；既有 blob 清理命令同时清理它。文本提取不包含 OCR，不保证复杂表格和图片完整还原。

货币基金的每万份收益、七日年化以独立字段返回，不能当作普通基金净值；固定净值不代表实际零收益。每次计算先检查返回的类型、覆盖与可计算状态。

交付包内的 Claude Code / Codex 原生插件清单也引用同一 `skills/`；日常按上述工作区安装即可，不必重复注册插件。交付包不含题库、标准答案、评测脚本或开发报告。

## 帮助与纠错

`ty --help`、`ty <命令> --help`、`ty help garden check` 均离线返回 JSON 帮助，退出码为 0。`ty help <工具名>` 或 `ty call <工具名> --help` 同时展示工具 schema。帮助不检查凭据、不执行查询或清理。

错误响应包含下一步 `hint`；参数校验可能附 `details.expected`、候选参数和帮助入口。`retryable` 表示原参数是否适合退避后重试，修正参数后可重新调用。`ty help errors` 提供完整分类。

0.4.0 收紧参数边界：未知/多余参数、重复 JSON 键或参数键、冲突参数、非有限数和无效日期会明确失败；不会用后值覆盖前值。日期参数的 `format: date` 接受 YYYY-MM-DD 或 YYYYMMDD。`doctor` 任一检查失败时退出非零；`garden --report/--out` 不再覆盖已有文件。

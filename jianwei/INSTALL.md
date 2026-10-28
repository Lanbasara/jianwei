# Jianwei 0.2.9 安装与自行验收

当前含 setup、tanyuan、research、portfolio、advise、reflect、report 与 research-then-report。不需要另装 tanyuan，也不需要克隆 huoguang。

## Claude Code

以下命令须等 `jianwei-marketplace` 中的 Jianwei 条目发布 0.2.9 后使用：

```bash
claude plugin marketplace add Lanbasara/jianwei-marketplace
claude plugin install jianwei@jianwei --scope user
claude plugin list
```

在任意任务目录启动新会话。技能为 `/jianwei:setup`、`/jianwei:tanyuan`、`/jianwei:research`、`/jianwei:portfolio`、`/jianwei:advise`、`/jianwei:reflect`、`/jianwei:report`、`/jianwei:research-then-report`。已有会话按宿主提示重新加载或新开会话。用户级安装在本机各项目可用，项目设置仍可覆盖是否启用。

## Codex

以下命令须等 `jianwei-marketplace` 中的 Jianwei 条目发布 0.2.9 后使用：

```bash
codex plugin marketplace add Lanbasara/jianwei-marketplace
codex plugin add jianwei@jianwei
codex plugin list
```

桌面端也可在插件页面添加 GitHub marketplace `Lanbasara/jianwei-marketplace`，从该来源安装 Jianwei。命令行安装后，桌面端若未更新列表，请刷新或重启，再建立新会话。

在任意工作区说：“使用 Jianwei 插件研究……并生成 HTML 研报”，agent 会使用 research-then-report，先完整执行 research，再委派通用子智能体执行 report，并由主智能体交付成稿。也可以在技能选择器中选择 Jianwei 下的相应 skill；显式调用符号以实际客户端列表为准。

CLI 语法针对本机 **0.163.0-alpha.11.2** 的帮助核对。若旧版没有 `codex plugin add`，升级客户端或使用提供插件功能的桌面版本。

## DeepSeek Harness（DSH）

**Desktop（推荐：市场安装）**：打开「插件 → 发现 → 添加索引源」，填入完整地址：

```text
https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json
```

等市场中的 Jianwei 条目发布 0.2.9 后，在见微市场中选择 `jianwei`，确认版本并安装、启用，按宿主提示重载或重启。然后在同一 profile 的任意工作目录新建任务。若没有「发现」页，先在实验性插件中启用自定义插件市场；`v0.2.9` tag 发布后，也可通过「添加插件」直接输入 `git+https://github.com/Lanbasara/jianwei.git#v0.2.9&path:/plugin`。

此地址是 DSH 原生 format 1 市场索引，不是 npm registry，也不是 GitHub 仓库页面。索引把版本映射到固定 Git commit，由 DSH 生成安装地址。当前未发布 npm 包，请勿输入裸包名 `jianwei` 从 npm 安装。

**CLI**：使用你实际运行的 profile，下面以 `web` 为例：

```bash
dsh plugin --profile web add 'git+https://github.com/Lanbasara/jianwei.git#v0.2.9&path:/plugin'
dsh web
```

若 shell 没有 `dsh` 但装了 Desktop，使用应用内插件管理。不要重置已有 profile；CLI 与 Desktop 的 profile 不是可以随意互换的名称。

技能名是 **`jianwei-setup`**、**`jianwei-tanyuan`**、**`jianwei-research`**、**`jianwei-portfolio`**、**`jianwei-advise`**、**`jianwei-reflect`**、**`jianwei-report`**、**`jianwei-research-then-report`**，可从技能入口选择或直接要求 agent 使用。包只注册技能，不更换模型、agent preset、多 agent 工具或权限。接口以 DSH **0.2.1-rc.3** 核查；其他 profile 需分别安装。

Git 包包含准备好的 ESM 注册文件，没有 prepare/postinstall 或 npm 构建步骤。Python 依赖在下面的显式初始化中准备。

包声明 DSH 范围 `>=0.2.1-rc.3 <0.3.0`。当前 candidate 仅通过本地 DSH registration contract；正式发布前必须再以 0.2.1-rc.3 的市场校验器验证索引与 `plugin/` 子目录安装目标，并完成真实安装验收。范围内其他版本未逐个实测，不代表全版本验收。

## 当前安装副本的首次配置

安装包可见不代表 Python 数据环境已经准备好。建议在宿主里发：

> 使用 Jianwei 的 setup skill 检查并准备当前安装副本。保持当前工作目录；只准备隔离 Python 环境和空白凭据文件，告诉我写入位置，不要查询数据，也不要索取或显示密钥。

初始化需要 Python 3.10+，默认只在当前 Jianwei 安装副本的 `.runtime/` 内建立环境、缓存和 `config.env`，从 `https://pypi.org/simple` 安装固定依赖；不写任务目录、全局 PATH 或系统应用目录。如果当前安装副本不可写，setup skill 会请用户明确选择一个可写的绝对路径作为 `JIANWEI_HOME`。它不注册数据账户、不充值、不验证付费接口。

也可以手工操作。请由宿主定位安装目录：tanyuan 的路径形如 `<插件根>/skills/tanyuan/SKILL.md`。

```bash
# 替换为实际安装路径；保持当前任务目录。
python3 "/absolute/path/to/installed/jianwei/scripts/setup.py"

# 或使用已有凭据文件（不复制密钥）：
python3 "/absolute/path/to/installed/jianwei/scripts/setup.py" \
  --env-file "/absolute/path/to/your/private.env"
```

在自己的编辑器中填写输出路径所指的文件。全功能需要用户自行购买或申请并配置 `TUSHARE_TOKEN`、`TAVILY_API_KEY`、`DEEPSEEK_API_KEY`，为 SEC 请求填写联系标识 `SEC_CONTACT`，且账户具有相应接口权限。不要把密钥发到聊天或提交到 Git。重复初始化会保留文件。若已有符合 requirements.txt 的 Python，可追加 `--python /absolute/path/to/python`，避免下载依赖。

换工作目录无需重新安装。可显式用 `TANYUAN_ENV_FILE` 切换某次调用的配置，用 `TANYUAN_PYTHON` 选解释器。若自定义 `JIANWEI_HOME`，初始化与执行及后续新会话都必须由宿主提供同一变量；setup 不会擅自修改 shell 或宿主配置。可用 `python3 "<插件根>/scripts/setup.py" --status` 离线检查 Python 与四项凭据名称，只返回状态而不显示值。

## 自行验收

选择开发仓之外的空目录，再选择另一个已有工作目录，各开一次新任务。

1. **发现与运行**：八项技能可见；setup 能解释当前状态，`ty --version` 返回 tanyuan 版本，`ty tools` 能列出能力；第二个任务目录不再安装依赖。
2. **取数**：用已配置服务完成熟悉的小查询，检查标的、时间、单位和来源；缺权限或服务失败应明确报告。
3. **研究与协作**：给出值得分工的问题，要求宿主原生子 agent 调查，另一个未承担原论证的 agent 复核关键结论。检查实际执行记录及有出处的自然语言底稿，不只看最终文字宣称“已复核”。
4. **研报衔接**：要求研究并生成 HTML 研报，确认 agent 选择 research-then-report，先完整执行 research，再委派通用子智能体执行 report 并接收成稿；生成的 HTML 可以独立打开。
5. **产物位置**：底稿、研报与附件留在任务目录；Python 环境、默认凭据模板和缓存只在当前插件副本的 `.runtime/`，任务目录没有 `.tanyuan` 缓存。
6. **投资建议**：用已有研究、外部材料和相关持仓背景分别提出选择，检查 advise 是否能给出有依据的建议、按需提问并保存可追溯记录。变更关键证据或目标，观察建议是否相应调整；保持关键条件不变，检查它是否能维持仍然成立的判断。建议质量需由人结合具体情境审阅，离线构建检查不证明模型行为。
7. **组合分析**：分别提供实际持仓与假设方案，检查 portfolio 能否解释底层重叠、共同收益与风险来源，并在一致口径下比较调整影响。以局部更新接续旧快照，确认沿用信息保留原时点、未知仍为未知，假设方案没有变成用户实际持仓。结论应回答组合问题，并可供 advise 复用。
8. **复盘**：分别从外部市场材料和已有投资经历提出问题，检查 reflect 能否解释关键变化、区分当时依据与后来发展，并说明发现的具体意义。正式分析保存为 case，同一任务的补充沿用成果；历史原稿保持可追查，局部材料不被扩成完整行为或业绩，个案发现不自动写入默认加载的经验规则。

示例：

> 使用 Jianwei 的 research，研究我指定公司的最近一期经营变化及其投资含义。核对披露时点，按问题安排独立调查，对关键数字和主要推断独立复核。用自然语言 Markdown 给出结论、证据、反证与缺口，保存在当前目录。

research 可使用 tanyuan，也可自行 Web 搜索。如果宿主禁用了子 agent，应明确披露独立复核的缺口。

research-then-report 用于从投资问题到最终 HTML 研报的完整流程。report 只承接已经完成的 research；在没有研究上下文的新会话中单独使用时，需要明确指定已有研究产物。

可以直接要求：“使用 advise，结合这份材料，帮我判断是否应调整这项投资，解释你的建议及其取舍。”不要求先做 research，也不要求先提供完整持仓；是否补充研究或背景取决于当前选择。建议保存在日期目录的 `decision.md`，具体文件职责见 [WORKSPACE.md](WORKSPACE.md)。

也可以独立要求：“使用 portfolio，分析这组投资主要依赖什么、是否存在重复暴露，以及这个调整方案会改变什么。”输入可以来自对话或现有材料，实际与假设需区分；分析保存到 case。`portfolio.md` 只承接用户提供的实际持仓背景，收到更新时按[快照写法](references/portfolio-snapshot.md)维护。

也可以要求：“使用 reflect，复盘这段市场变化，解释重要转折和预期差异，对后续研究有什么意义。”或指定一项旧分析、建议或投资经历。无需先有持仓、交易记录或上次复盘；agent 按当前问题渐进读取材料，形成可独立理解、引用有据的分析。

## 升级与卸载

- Claude：在原生插件页面刷新 `jianwei` 市场并更新插件。卸载：`claude plugin uninstall jianwei@jianwei --scope user`。
- Codex：`codex plugin marketplace upgrade jianwei` 刷新目录，再 `codex plugin add jianwei@jianwei` 安装目录指定版本；卸载用 `codex plugin remove jianwei@jianwei`，选项以当前 `--help` 为准。
- DSH Desktop 0.2.1-rc.3：刷新「发现」中的见微市场。若显示「有更新」，先在「已安装」卸载 Jianwei，再回市场安装推荐版本。当前第三方插件的更新提示不是一键升级按钮；用户不用修改版本号，索引源 URL 保持不变。卸载后若提示重启，先重启再安装。已有 0.1.0 的用户也按此操作。CLI 可先 `dsh plugin --profile web remove jianwei`，再执行上面的安装命令；以实际 profile 为准。未来客户端能力以其界面为准，不以官方 master 的未发布功能作为保证。

插件升级或依赖清单变化后，对新的安装副本重新运行 setup。默认 `.runtime/` 随旧安装副本一起移除；需要跨升级保留凭据时，使用 `--env-file` 指向用户自己管理的私密文件。任务研究文件始终保留。

已有独立 tanyuan 安装可先并存，明确指定 Jianwei 中的技能。不会自动覆盖或卸载旧 skill。

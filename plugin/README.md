# 见微 · Jianwei

一次安装，获得探源数据能力、专业投资研究、组合分析、投资建议、复盘与 HTML 成稿 skills。支持 Claude Code、Codex、DeepSeek Harness（DSH）。

当前版本 **0.3.1**，包含 **setup**、**tanyuan 0.5.0**、**research**、**portfolio**、**advise**、**reflect**、**report** 与 **research-then-report** 八项 skill。setup 引导准备本地依赖与凭据；tanyuan 提供 ty CLI、数据能力和参考资料；research 负责调查、分析与独立复核；portfolio 分析组合的收益来源、风险关系与调整影响；advise 围绕当前投资选择比较方案并提出建议；reflect 复盘市场与投资经历，为后续投资增进理解；report 将已有研究、分析或建议忠实写成独立可读的 HTML；research-then-report 在研究完成后委派通用子智能体执行 report。研究使用宿主原生多 agent，也可自由使用 Web 等工具。

advise 可直接承接用户问题、已有研究或外部材料，按需要考虑持仓背景、补充研究并与用户对齐。研究保持独立判断，建议的具体程度由问题与依据决定。成果按[工作区约定](WORKSPACE.md)沉淀；建议与用户实际作出的选择分别记录，供后续任务继续理解。

portfolio 可独立分析实际持仓、局部账户或假设配置，也可为 advise 提供整体影响的依据。它不要求完整持仓表或先做 research；从问题出发分析重叠、共同依赖、资产角色及方案差异。组合分析保存在 case，用户提供的实际持仓背景按[快照写法](references/portfolio-snapshot.md)记录，便于后来任务继续使用。

reflect 可从市场事件、投资经历、旧研判或外部材料开始，解释结果的形成，审视相关认识与选择；调查、组合分析和当前建议按问题需要组合。正式复盘按分析任务保存到 case，后续讨论沿用已有成果。历史按需查阅，复盘发现保留为有条件的分析，不自动形成默认加载的经验规则。

report 以专业编辑与写作者的身份承接已有成果，根据材料、读者与用途自主决定表达，保留原成果的性质、判断强度、条件与证据关系。需要 HTML 时可以在分析或建议完成后接续，也可以独立指定已有产物成稿；原有 research-then-report 流程继续用于研究并形成研报。成稿位置和来源记录遵循[工作区约定](WORKSPACE.md)。

## 安装

完整三端步骤、首次配置、升级与卸载见 **[INSTALL.md](INSTALL.md)**。

专属 marketplace：<https://github.com/Lanbasara/jianwei-marketplace>，市场名 **jianwei**。

以下 Claude Code 与 Codex 的 marketplace 命令须等 marketplace 中的 Jianwei 条目发布 0.3.1 后使用。DSH 直接安装命令须等 `v0.3.1` tag 发布后使用。

```bash
# Claude Code：用户级，适用于本机各项目
claude plugin marketplace add Lanbasara/jianwei-marketplace
claude plugin install jianwei@jianwei --scope user

# Codex
codex plugin marketplace add Lanbasara/jianwei-marketplace
codex plugin add jianwei@jianwei

# DSH CLI：使用实际 profile；下面以 web 为例
dsh plugin --profile web add 'git+https://github.com/Lanbasara/jianwei.git#v0.3.1&path:/plugin'
```

DSH Desktop 推荐在「插件 → 发现 → 添加索引源」添加：

```text
https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json
```

marketplace 中的 Jianwei 条目发布 0.3.1 后，再从见微市场安装。索引由我们维护，Git commit 和 `plugin/` 子目录由 DSH 自动选择，无需用户编辑版本地址。0.2.1-rc.3 的第三方插件升级仍需卸载后从市场重装；当前没有发布 npm 包。

DSH 其技能名为 `jianwei-setup`、`jianwei-tanyuan`、`jianwei-research`、`jianwei-portfolio`、`jianwei-advise`、`jianwei-reflect`、`jianwei-report`、`jianwei-research-then-report`，避免占用通用名称。Claude 使用 `/jianwei:setup`、`/jianwei:tanyuan`、`/jianwei:research`、`/jianwei:portfolio`、`/jianwei:advise`、`/jianwei:reflect`、`/jianwei:report`、`/jianwei:research-then-report`。Codex 可自然语言指定 Jianwei 插件的相应 skill，以实际技能列表为准。用户要求从投资问题开始研究并形成最终研报时，agent 使用 research-then-report；它会先完整执行 research，再委派通用子智能体执行 report，并由主智能体交付成稿。

## 首次准备与任意目录使用

需要 Python 3.10+。优先让宿主使用 Jianwei 的 setup skill；它会定位当前安装副本、检查现状，并在失败时给出针对性提示。也可保持当前任务目录手工运行：

```bash
python3 /absolute/path/to/installed/jianwei/scripts/setup.py
```

初始化默认只在当前 Jianwei 安装副本的 `.runtime/` 内准备隔离 Python 环境、缓存和权限为 600 的空白 `config.env`，从 PyPI 安装固定版本依赖；不写当前项目、不修改全局 PATH，也不创建系统级应用目录。在本地编辑所需凭据，不要粘贴到聊天。也可用 `--env-file /absolute/path/to/private.env` 选用已有文件，不会复制其中的值。重复初始化不覆盖配置，不查询数据或调用模型。

`ty` 缺少环境时会输出该安装副本的确切初始化命令。可用 `--python /absolute/path/to/python` 复用已安装精确依赖版本的解释器。日常研究调用不自动下载依赖。

- 插件从自身位置定位程序；不需要进入插件目录或克隆开发仓。
- Python 环境按依赖摘要管理，并与当前插件安装副本一起收纳；宿主替换安装副本后重新运行 setup。需要跨升级保留凭据时，用 `--env-file` 指向用户自己管理的私密文件。
- `TANYUAN_PYTHON`、`TANYUAN_ENV_FILE` 显式设置优先；进程凭据变量优先于文件内容。
- 缓存默认位于当前安装副本的 `.runtime/cache`，可通过 `TANYUAN_CACHE` 指定其他绝对路径；只有用户要求的底稿和附件进入任务工作区。
- 当前安装副本不可写时，setup skill 会请用户明确选择一个可写的绝对路径作为 `JIANWEI_HOME`，不会自行改写到应用目录或用户项目；初始化与运行须保持同一选择。
- 自定义 `JIANWEI_HOME` 不会修改 shell 或宿主配置；新会话必须继续由宿主提供同一变量。可运行 `scripts/setup.py --status` 离线查看 Python 和四项凭据名称的状态，输出不包含密钥值。

## 产品边界

发布仓由独立维护的 tanyuan 与 Jianwei 源码装配。原始 `bin/ty` 保存在 `bin/ty-upstream`；发行入口只选择 Python 与配置，不改取数实现。`manifest.json` 记录版本、文件摘要及组件来源。不要手工修改生成的发布副本。

测试、评测、实验和开发报告均在交付物之外。没有自建多 agent 调度运行时，当前没有附加开源许可声明。

发布前检查构建、组件一致性、跨目录 CLI、插件清单与 DSH 注册接口。真实宿主内的模型调用、数据服务与研究质量仍需按 INSTALL.md 的场景自行验收；它们依赖宿主权限、账户与上游状态。

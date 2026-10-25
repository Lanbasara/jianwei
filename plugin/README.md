# 见微 · Jianwei

一次安装，获得探源数据能力与专业投资研究 skills。支持 Claude Code、Codex、DeepSeek Harness（DSH）。

当前版本 **0.2.0**，包含 **setup**、**tanyuan 0.5.0** 与 **research** 三项 skill。setup 引导准备本地依赖与凭据；tanyuan 提供 ty CLI、数据能力和参考资料；research 负责调查、分析与独立复核。研究使用宿主原生多 agent，也可自由使用 Web 等工具。底稿始终是自然语言文档。

`report` 尚未交付，完成后作为同一插件内的 skill 加入。

## 安装

完整三端步骤、首次配置、升级与卸载见 **[INSTALL.md](INSTALL.md)**。

专属 marketplace：<https://github.com/Lanbasara/jianwei-marketplace>，市场名 **jianwei**。

以下 Claude Code 与 Codex 的 marketplace 命令须等 marketplace 中的 Jianwei 条目发布 0.2.0 后使用。DSH 直接安装命令须等 `v0.2.0` tag 发布后使用。

```bash
# Claude Code：用户级，适用于本机各项目
claude plugin marketplace add Lanbasara/jianwei-marketplace
claude plugin install jianwei@jianwei --scope user

# Codex
codex plugin marketplace add Lanbasara/jianwei-marketplace
codex plugin add jianwei@jianwei

# DSH CLI：使用实际 profile；下面以 web 为例
dsh plugin --profile web add 'git+https://github.com/Lanbasara/jianwei.git#v0.2.0&path:/plugin'
```

DSH Desktop 推荐在「插件 → 发现 → 添加索引源」添加：

```text
https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json
```

marketplace 中的 Jianwei 条目发布 0.2.0 后，再从见微市场安装。索引由我们维护，Git commit 和 `plugin/` 子目录由 DSH 自动选择，无需用户编辑版本地址。0.2.1-rc.3 的第三方插件升级仍需卸载后从市场重装；当前没有发布 npm 包。

DSH 其技能名为 `jianwei-setup`、`jianwei-tanyuan`、`jianwei-research`，避免占用通用名称。Claude 使用 `/jianwei:setup`、`/jianwei:tanyuan`、`/jianwei:research`。Codex 可自然语言指定“Jianwei 插件的 setup/tanyuan/research”，以实际技能列表为准。

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

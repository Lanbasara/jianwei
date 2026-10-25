# 见微 · Jianwei

一次安装，获得探源数据能力、专业投资研究与 HTML 研报 skills。支持 Claude Code、Codex、DeepSeek Harness（DSH）。

当前版本 **0.1.0**，包含 **tanyuan 0.5.0** 的 skill、ty CLI、参考资料，**research** 的调查、分析与独立复核能力，**report** 的忠实编辑与 HTML 研报写作能力，以及依次调用二者的 **research-then-report** 流程。研究使用宿主原生多 agent，也可自由使用 Web 等工具；report 只承接已经完成的研究，不继续调查。

## 安装

完整三端步骤、首次配置、升级与卸载见 **[INSTALL.md](INSTALL.md)**。

专属 marketplace：<https://github.com/Lanbasara/jianwei-marketplace>，市场名 **jianwei**。

```bash
# Claude Code：用户级，适用于本机各项目
claude plugin marketplace add Lanbasara/jianwei-marketplace
claude plugin install jianwei@jianwei --scope user

# Codex
codex plugin marketplace add Lanbasara/jianwei-marketplace
codex plugin add jianwei@jianwei

# DSH CLI：使用实际 profile；下面以 web 为例
dsh plugin --profile web add github:Lanbasara/jianwei#v0.1.0
```

DSH Desktop 推荐在「插件 → 发现 → 添加索引源」添加：

```text
https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json
```

从见微市场安装 Jianwei。索引由我们维护，Git commit 由 DSH 自动选择，无需用户编辑版本地址。0.2.1-rc.3 的第三方插件升级仍需卸载后从市场重装；当前没有发布 npm 包。

DSH 其技能名为 `jianwei-tanyuan`、`jianwei-research`、`jianwei-report`、`jianwei-research-then-report`，避免占用通用名称。Claude 使用 `/jianwei:tanyuan`、`/jianwei:research`、`/jianwei:report`、`/jianwei:research-then-report`。Codex 可自然语言指定 Jianwei 插件的相应 skill，以实际技能列表为准。用户要求从投资问题开始研究并形成最终研报时，agent 使用 research-then-report；它会在同一主对话中先完整调用 research，再调用 report。

## 首次准备与任意目录使用

需要 Python 3.10+。从宿主提供的 tanyuan SKILL.md 位置定位插件根目录，保持当前任务目录运行：

```bash
python3 /absolute/path/to/installed/jianwei/scripts/setup.py
```

初始化在 `~/.local/share/jianwei/` 准备 Python 环境，从 PyPI 安装固定版本依赖，并创建权限为 600 的空白 `config.env`。在本地编辑所需凭据，不要粘贴到聊天。也可用 `--env-file /absolute/path/to/private.env` 选用已有文件，不会复制其中的值。重复初始化不覆盖配置，不查询数据或调用模型。

`ty` 缺少环境时会输出该安装副本的确切初始化命令。可用 `--python /absolute/path/to/python` 复用已安装精确依赖版本的解释器。日常研究调用不自动下载依赖。

- 插件从自身位置定位程序；不需要进入插件目录或克隆开发仓。
- Python 环境与宿主插件版本缓存分开；按依赖摘要管理，升级保留配置。
- `TANYUAN_PYTHON`、`TANYUAN_ENV_FILE` 显式设置优先；进程凭据变量优先于文件内容。
- 缓存默认位于调用目录 `.tanyuan/cache`，可通过 `TANYUAN_CACHE` 指定其他绝对路径；底稿和附件放任务工作区。
- `JIANWEI_HOME` 可指定其他绝对路径，初始化与运行须保持一致。首次写用户环境遵循宿主权限；只读工作区需要另选可写的缓存与输出位置。

## 产品边界

发布仓由独立维护的 tanyuan 与 Jianwei 源码装配。原始 `bin/ty` 保存在 `bin/ty-upstream`；发行入口只选择 Python 与配置，不改取数实现。`manifest.json` 记录版本、文件摘要及组件来源。不要手工修改生成的发布副本。

测试、评测、实验和开发报告均在交付物之外。没有自建多 agent 调度运行时，当前没有附加开源许可声明。

发布前检查构建、组件一致性、跨目录 CLI、插件清单与 DSH 注册接口。真实宿主内的模型调用、数据服务与研究质量仍需按 INSTALL.md 的场景自行验收；它们依赖宿主权限、账户与上游状态。

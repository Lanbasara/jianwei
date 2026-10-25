# Jianwei 0.1.0 安装与自行验收

当前含 tanyuan + research，暂不含 report。不需要另装 tanyuan，也不需要克隆 huoguang。

## Claude Code

```bash
claude plugin marketplace add Lanbasara/jianwei-marketplace
claude plugin install jianwei@jianwei --scope user
claude plugin list
```

在任意任务目录启动新会话。技能为 `/jianwei:tanyuan`、`/jianwei:research`。已有会话按宿主提示重新加载或新开会话。用户级安装在本机各项目可用，项目设置仍可覆盖是否启用。

## Codex

```bash
codex plugin marketplace add Lanbasara/jianwei-marketplace
codex plugin add jianwei@jianwei
codex plugin list
```

桌面端也可在插件页面添加 GitHub marketplace `Lanbasara/jianwei-marketplace`，从该来源安装 Jianwei。命令行安装后，桌面端若未更新列表，请刷新或重启，再建立新会话。

在任意工作区说：“使用 Jianwei 插件的 tanyuan，先检查 ty 版本，再研究……”，或在技能选择器中选 Jianwei 下的 research。显式调用符号以实际客户端列表为准。

CLI 语法针对本机 **0.163.0-alpha.11.2** 的帮助核对。若旧版没有 `codex plugin add`，升级客户端或使用提供插件功能的桌面版本。

## DeepSeek Harness（DSH）

**Desktop**：在当前 profile 的 Plugins / 插件管理页面安装外部 bundle，输入：

```text
github:Lanbasara/jianwei#v0.1.0
```

确认包名 `jianwei` 并启用，按提示重载或重启，然后在该 profile 新建其他工作目录的任务。若界面提供 Git URL 输入，也可使用 `https://github.com/Lanbasara/jianwei.git#v0.1.0`。

**CLI**：使用你实际运行的 profile，下面以 `web` 为例：

```bash
dsh plugin --profile web add github:Lanbasara/jianwei#v0.1.0
dsh web
```

若 shell 没有 `dsh` 但装了 Desktop，使用应用内插件管理。不要重置已有 profile；CLI 与 Desktop 的 profile 不是可以随意互换的名称。

技能名是 **`jianwei-tanyuan`**、**`jianwei-research`**，可从技能入口选择或直接要求 agent 使用。包只注册技能，不更换模型、agent preset、多 agent 工具或权限。接口以 DSH **0.2.1-rc.3** 核查；其他 profile 需分别安装。

Git 包包含准备好的 ESM 注册文件，没有 prepare/postinstall 或 npm 构建步骤。Python 依赖在下面的显式初始化中准备。

## 共同的首次配置（仅一次）

安装包可见不代表 Python 数据环境已经准备好。建议在宿主里发：

> 使用 Jianwei 的 tanyuan skill。保持当前工作目录，先运行它的 bin/ty --version；若提示缺少环境，按提示运行本插件的 scripts/setup.py。只准备环境和空白凭据文件，告诉我文件位置，不要查询数据，也不要索取或显示密钥。

初始化需要 Python 3.10+，在 `~/.local/share/jianwei/` 建立环境，从 `https://pypi.org/simple` 安装固定依赖，并创建 `config.env`。如果宿主要求写用户目录的许可，按提示处理。它不注册数据账户、不充值、不验证付费接口。

也可以手工操作。请由宿主定位安装目录：tanyuan 的路径形如 `<插件根>/skills/tanyuan/SKILL.md`。

```bash
# 替换为实际安装路径；保持当前任务目录。
python3 "/absolute/path/to/installed/jianwei/scripts/setup.py"

# 或使用已有凭据文件（不复制密钥）：
python3 "/absolute/path/to/installed/jianwei/scripts/setup.py" \
  --env-file "/absolute/path/to/your/private.env"
```

在自己的编辑器中填写输出路径所指的文件，只配置需要的服务。不要把密钥发到聊天或提交到 Git。重复初始化会保留文件。若已有符合 requirements.txt 的 Python，可追加 `--python /absolute/path/to/python`，避免下载依赖。

换工作目录无需重新安装。可显式用 `TANYUAN_ENV_FILE` 切换某次调用的配置，用 `TANYUAN_PYTHON` 选解释器。若自定义 `JIANWEI_HOME`，初始化与执行应保持一致。

## 自行验收

选择开发仓之外的空目录，再选择另一个已有工作目录，各开一次新任务。

1. **发现与运行**：两项技能可见；`ty --version` 返回 tanyuan 版本，`ty tools` 能列出能力；第二个目录不再安装依赖。
2. **取数**：用已配置服务完成熟悉的小查询，检查标的、时间、单位和来源；缺权限或服务失败应明确报告。
3. **研究与协作**：给出值得分工的问题，要求宿主原生子 agent 调查，另一个未承担原论证的 agent 复核关键结论。检查实际执行记录及有出处的自然语言底稿，不只看最终文字宣称“已复核”。
4. **产物位置**：底稿与附件留在任务目录，插件目录没有研究产物、venv 或凭据。

示例：

> 使用 Jianwei 的 research，研究我指定公司的最近一期经营变化及其投资含义。核对披露时点，按问题安排独立调查，对关键数字和主要推断独立复核。用自然语言 Markdown 给出结论、证据、反证与缺口，保存在当前目录。

research 可使用 tanyuan，也可自行 Web 搜索。如果宿主禁用了子 agent，应明确披露独立复核的缺口。

## 升级与卸载

- Claude：在原生插件页面刷新 `jianwei` 市场并更新插件。卸载：`claude plugin uninstall jianwei@jianwei --scope user`。
- Codex：`codex plugin marketplace upgrade jianwei` 刷新目录，再 `codex plugin add jianwei@jianwei` 安装目录指定版本；卸载用 `codex plugin remove jianwei@jianwei`，选项以当前 `--help` 为准。
- DSH：在插件页面更新 Git 版本；CLI 把 tag 改成新版本重新 add。卸载示例：`dsh plugin --profile web remove jianwei`。固定 tag 不自动追踪 main。

依赖清单变化后重新执行新版本的 setup.py。用户环境按依赖摘要隔离，凭据保留。共用的 `~/.local/share/jianwei/` 不由单个宿主卸载时自动删除；确认不再使用后可自行清理。任务研究文件始终保留。

已有独立 tanyuan 安装可先并存，明确指定 Jianwei 中的技能。不会自动覆盖或卸载旧 skill。

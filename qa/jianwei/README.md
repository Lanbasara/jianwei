# Jianwei 发布验证（不随插件交付）

离线验证：

```bash
# 指向 Python 3.10+，并已安装 tanyuan/requirements.txt 中的精确依赖版本。
export JIANWEI_TEST_PYTHON=/absolute/path/to/prepared/python
PYTHONDONTWRITEBYTECODE=1 "$JIANWEI_TEST_PYTHON" -m unittest discover -s qa/jianwei -v
python3 scripts/build_jianwei.py --out /tmp/jianwei-check
node qa/jianwei/check_dsh.mjs /tmp/jianwei-check
diff -qr plugin /tmp/jianwei-check
python3 qa/run.py --suite marketplace-contract
```

`test_distribution.py` 检查源码分层与 adapter 清单、三端身份和技能集合一致、组件与原文一致、发布边界、未配置时的明确提示、跨目录调用、配置优先级、凭据保留以及链接目录拒绝。配置测试仅使用临时假值。`check_dsh.mjs` 检查注册接口、命名空间、正文和资源路径，不启动 DSH 或模型。

整包由 `scripts/build_jianwei.py` 生成到一个尚不存在的临时路径；构建拒绝覆盖已有输出。仓根 `plugin/` 是受追踪的已验证发布树，禁止直接编辑或原地覆盖。先验证 fresh build，再比较并替换 `plugin/`。测试和开发材料不进入发布包。

本 slice 包含离线单元测试、产品构建、DSH 注册契约和 Marketplace 本地
生成契约；不验证宿主模型会话、联网数据源或付费 API。

## Marketplace 本地 tracer

`scripts/build_jianwei_marketplace.py` 只从给定 Git ref 的
`plugin/package.json` 与 `plugin/manifest.json` 读取发布事实，不读取工作区
文件，也不回退到仓库根部的 v0.1.1 兼容包。builder 会把 commit 中完整的
`plugin/` 文件集合、普通 blob 类型和逐文件 SHA-256 与 manifest 对齐；链接、
漏记、多记或内容漂移均拒绝。它先在输出同一真实父目录的私有 staging 目录
完成全部写入，再使用平台原子 no-replace rename 一次发布完整目录；安全原语
不可用时直接失败。异常会清理 staging，且不覆盖既有或竞态出现的目标。

它生成 Claude、Codex 和 DSH 三份本地目录；新版本固定完整 commit 与
`plugin/` 子目录。可用 `--previous-index` 显式导入已经发布的 DSH format 1
历史。历史版本必须严格降序，latest 必须是最高稳定版本，并逐条回查同一本地
Git 仓库中的 package、engine、commit 时间和（对 plugin 布局）完整 manifest。
根布局只允许 0.1.0/0.1.1；0.2.0 起必须使用 `plugin/`。

`marketplace-contract` 使用临时本地 Git 仓库覆盖 ref、版本、manifest、
三端 source、历史追加和拒绝路径，不联网，不调用宿主 CLI。外部官方 DSH
validator 仍由 `check_market.mjs` 作为人工发布检查接入，不进入 PR gate；
Claude 本地严格校验和真实宿主安装也属于发布验证，而非本 suite 的承诺。

## Source release tracer

`scripts/build_jianwei_release.py` 从已提交的 `<commit>:plugin` 生成确定性
`jianwei-plugin.zip`，同时从本地 `jianwei-marketplace` checkout 的 committed
`HEAD:dsh/index.json` 追加并生成 Marketplace candidate ZIP。它不读取两边的
worktree 内容；`release.json` 固定 Jianwei commit、Marketplace base commit、
index digest 和两个 ZIP 的 digest。`workflow_dispatch` 总是使用完整 SHA，
只能生成 `official=false` 的预演产物；正式 `vX.Y.Z` tag 还必须匹配插件版本、
匹配 workflow commit，并属于 `origin/main` 历史。

`.github/workflows/release.yml` 把完整 release QA 和纯净产物构建分为两个
`contents:read` job；clean build 使用 fresh checkout，不安装第三方依赖，上传产品
和 Marketplace candidate 供审查，但不修改任何远程仓库。publish job 只有在
tag push 且 repository configuration variable `JIANWEI_RELEASE_ENABLED` 精确为 `true` 时进入
`source-release` environment，随后校验 checksum、生成 attestation，并只把
产品 ZIP、`release.json` 和 `SHA256SUMS` 发布到 GitHub Release；Marketplace
ZIP 明确不发布、不自动提交、不创建 PR、不合并。

启用远程发布前，必须先由管理员创建受保护的 `source-release` environment，
配置 required reviewers 和 tag ruleset，并确认不可绕过的审核行为；若仓库设置
支持 GitHub Immutable Releases，还应由管理员人工启用。完成这些保护后才可把
repository configuration variable `JIANWEI_RELEASE_ENABLED` 设为 `true`。
environment-level variable 不能替代该 repository variable，因为 job 启动与审批前
无法依赖它。
默认未配置或不等于 `true` 时，tag workflow 也只构建验证产物。

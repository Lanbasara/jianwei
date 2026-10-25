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

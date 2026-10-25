# Jianwei 发布验证（不随插件交付）

离线验证：

```bash
# 指向 Python 3.10+，并已安装 tanyuan/requirements.txt 中的精确依赖版本。
export JIANWEI_TEST_PYTHON=/absolute/path/to/prepared/python
PYTHONDONTWRITEBYTECODE=1 "$JIANWEI_TEST_PYTHON" -m unittest discover -s qa/jianwei -v
python3 scripts/build_jianwei.py --out /tmp/jianwei-check
node qa/jianwei/check_dsh.mjs /tmp/jianwei-check
diff -qr plugin /tmp/jianwei-check
```

`test_distribution.py` 检查源码分层与 adapter 清单、三端身份和技能集合一致、组件与原文一致、发布边界、未配置时的明确提示、跨目录调用、配置优先级、凭据保留以及链接目录拒绝。配置测试仅使用临时假值。`check_dsh.mjs` 检查注册接口、命名空间、正文和资源路径，不启动 DSH 或模型。

整包由 `scripts/build_jianwei.py` 生成到一个尚不存在的临时路径；构建拒绝覆盖已有输出。仓根 `plugin/` 是受追踪的已验证发布树，禁止直接编辑或原地覆盖。先验证 fresh build，再比较并替换 `plugin/`。测试和开发材料不进入发布包。

本 slice 只包含离线单元测试、产品构建和 DSH 注册契约检查；不验证宿主模型会话、联网数据源或付费 API。

## 后续范围：Marketplace

Marketplace 生成、子目录来源适配、远端索引校验和真实安装尚未迁入本 slice。这里不提供不可运行的发布命令；相关脚本、固定版本校验器和隔离安装证据将在后续 slice 一并迁移后再记录。

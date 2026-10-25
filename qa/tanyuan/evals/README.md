# 历史题库与诊断判分

本目录不属于交付产品。已有题库已向开发者曝光，只可用于回归；不能作为未见题能力的证据。`golden.jsonl` 与原始证据保持原字节，本轮没有改答案以追求分数。

## 保留与停用

- `golden.jsonl`：原有题目、真值、证据与隔离标记，I-03 继续隔离。
- `score.py`：诊断工具，输出始终为 `provisional=true`、`acceptance_valid=false`。保留数值，明确规则的语义限制；元数据记录题库、判分器及输入答案哈希。
- `verify_golden.py`：结构、证据文件存在性与数值字面回溯检查，不证明指标语义。
- `examples/ty_adapter.py`：旧 adapter 已撤下，执行会明确报错。按题号拼事实、故障时填预定日期不能当作真实工具能力。
- `build_golden.py` / `fetch_truth.py`：历史生成过程保留供审查，命令入口冻结；禁止当前上游数据覆盖旧 as_of。
- `selftest/test_judges.py`：判分原语的确定性自测，不是 agent 验收。历史分数、模拟答案和本机运行记录未迁入本仓。

## 使用

从仓库根运行：

```bash
.venv/bin/python qa/tanyuan/security_path_tests.py
export JIANWEI_QA_HOME=/absolute/path/to/local/golden
.venv/bin/python qa/tanyuan/check_frozen.py
.venv/bin/python qa/tanyuan/evals/verify_golden.py
.venv/bin/python qa/tanyuan/evals/selftest/test_judges.py
.venv/bin/python qa/tanyuan/evals/score.py --answers /path/to/answers.jsonl --out reports/diagnostic-score.json
```

`JIANWEI_QA_HOME` 只指向 golden 根目录，不指向仓库；必须是显式的绝对已存在目录。raw 证据可能受第三方再分发限制，只保留在本机。缺少该变量时，依赖证据的冻结/回溯命令会明确失败；普通离线单元测试和判分器原语自测不需要它。

`--runner` 仍支持 stdin 题目、stdout 答案的外部程序协议。仅发送 qid、question、category、as_of，不传答案或 judges；这不限制子进程能读取哪些本地文件。独立验收应先在只挂载交付包的宿主环境运行，再在评测侧读取答案。

新快照和新题库应在独立位置采集、审核并记录时点与判据版本，不要恢复旧脚本原地刷新。旧分数不能直接与新版本比较；报告中的数字应来自保留的机器输出并注明诊断性质。

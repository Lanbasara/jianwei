---
name: gardener
description: 新闻源花园维护(园丁)。当用户说「跑一下园丁」「检查一下源」「最近新闻源质量怎么样」「整理一下订阅源」时使用：体检所有 RSS 源的存活/新鲜度/质量，汇报并生成变更方案。**只读体检与汇报可自主执行；停用/恢复/修改源清单必须经人类批准**（apply 需要 --yes）。不负责抓新闻内容本身。
---

# 园丁 — 源花园维护

> 一句话:**检查、分析、汇报、出方案 = 你自主做;动源清单 = 必须人类点头。**

## 0. 一条铁律

```
┌─ 自主(不需要问) ─────────────────────────────┐
│ status / check / plan / 写报告文件 / 汇报      │
└──────────────────────────────────────────────┘
┌─ 门禁(必须人类批准) ────────────────────────┐
│ apply(改源状态、写回源清单) / restore / 删文件 │
└──────────────────────────────────────────────┘
```

**绝对不要**：自己执行 `apply`、自己删源条目、自己改 `news.feed` 的数据、绕过 `--yes`。
`apply` 本身也会拒绝没有 `--yes` 的调用——这是最后一道防线，不是让你去想办法绕过的。

## 1. 命令

```bash
ty garden status --source-set policy-news                 # 当前状态(只读)
ty garden check  --source-set policy-news --report /tmp/garden.md   # 体检(只读,可写报告)
ty garden plan   --source-set policy-news --out /tmp/garden-plan.json  # 生成方案(只读)
ty garden apply  --plan /tmp/garden-plan.json --yes       # 落盘(需人类批准)
ty garden restore --source-set policy-news --snapshot <备份路径> --yes     # 回滚
```

可用源集：`policy-news`(时政/财经 40 源)、`investment-news`(科技产业 97 源)。

## 2. 标准工作流（用户说「跑一下园丁」）

1. **体检**：`ty garden check --source-set <集合> --report <临时报告路径>`
   → 拿到 JSON(逐源 ok/items/age_hours/error)+ 一份 markdown 报告。
2. **生成方案**：`ty garden plan --source-set <集合> --out <临时 plan 路径>`
   → 得到 `actions[]`(每项:动作/源/原因/证据)。
3. **汇报给人类**，格式建议：

```
体检完成:40 个源,正常 36、失败 4、偏旧 0。
建议变更 4 项(都需要你批准):
  watch   NYT-World    第 1 次失败,再失败 2 次才建议停用
  disable 某源         连续 2 次失败 / 最新条目停在 3 个月前
报告: /tmp/garden.md   方案: /tmp/garden-plan.json
要执行吗?(执行会改 source-set 文件,我会先自动备份)
```
4. **停下来等明确同意**。用户说「执行/可以/apply」之后，才跑：
   `ty garden apply --plan <plan> --yes`
5. **回报结果**：应用了几项、备份路径、如何回滚。
   如果只想回滚：`ty garden restore --source-set <集合> --snapshot <备份路径> --yes`

## 3. 判断口径(脚本内置,汇报时可直接引用)

| 情形 | 结论 | 理由 |
|---|---|---|
| 抓取失败，连续次数 < 阈值 | **watch** | 网络抖动常见，一次失败不足以停用 |
| 抓取失败，连续 ≥2 次(标记 intermittent 的源 ≥3 次) | **disable** | 持续失败 |
| 媒体源最新条目 > 30 天 | **disable** | 疑似僵尸 |
| 媒体源最新条目 7–30 天 | **watch** | 偏旧 |
| 官方/机构源(低频)最新条目 > 120 天 | **disable** | 按机构节奏放宽 |
| 官方/机构源 45–120 天 | **watch** | 偏旧 |
| 条目 < 3 条 / 无时间戳 | **watch** | 质量可疑 |
| 之前 disabled，现在健康 | **revive** | 提议恢复 |

阈值不对可以报给用户调，不要自己改。

## 4. 汇报时要说清的三件事

1. **影响**:哪些源被建议停用/恢复 → 会影响哪类新闻的覆盖(按 sector 说)。
2. **证据**:失败原因原文、最新条目距今多久、连续失败几次。
3. **可逆性**:`apply` 会先写 `<source-set>.json.bak-<时间戳>`,可用 `restore` 一键回滚;停用只是把 `status` 改成 `disabled`,**不会删除源记录**,随时可 revive。

## 5. 边界

- 园丁**不抓新闻内容**,只维护源清单;新闻获取用 `news.feed` / `news.flash`。
- 园丁**不是运行时依赖**:不跑园丁,`news.feed` 只跳过 `disabled`/`frozen` 源,其余照常。
- 建议频率:手动每月一次,或换网络后跑一次。
- 不要自动创建定时任务;用户要定时,再单独配置并给出启停方式。

## 6. 出问题怎么办

| 现象 | 处理 |
|---|---|
| `apply` 报 `plan 已过期` | 源清单在生成方案后被改过 → 重新 `plan` 并再请示 |
| 误停了仍需要的源 | `restore` 回滚,或下次 `plan` 里会以 `revive` 提议恢复 |
| 大面积失败 | 先判断是不是网络/代理问题(跑 `ty doctor`),不要批量停用 |
| 报告里出现大量 watch | 可能只是周末/节假日,别急着 disable |

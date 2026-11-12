# ADR 0002：按需组合分析与讲解

- 日期：2026-11-12
- 状态：接受

## 背景

[独立讲解能力](0001-investment-explain.md)解决如何向用户讲清 AI 已形成的分析结果。用户还需要一次请求完成分析与最终讲解，同时保持专业能力与表达能力隔离，不让所有分析默认多经过一个步骤。

## 决定

沿用 [research-then-report](../../jianwei/skills/research-then-report/SKILL.md) 的轻量组合方式，提供 [research-then-explain](../../jianwei/skills/research-then-explain/SKILL.md)、[advise-then-explain](../../jianwei/skills/advise-then-explain/SKILL.md) 和 [reflect-then-explain](../../jianwei/skills/reflect-then-explain/SKILL.md)。先按原能力的完成条件结束分析，再委派通用子智能体读取并执行 explain，主智能体直接向用户交付。完整分析、必要依据与用户语境通过任务交接，不另建状态协议或要求用户再次发起请求。

这些是按需选择的组合入口，不修改 research、advise、reflect、explain 或现有研究成稿组合的职责与正文。已有分析只需讲解时，直接使用 explain，不重复分析；格式、图形选择与忠实性由 explain 维护，不复制进三个组合。

## 复盘组合的价值与限度

复盘本身负责原因辨析和评价；讲解负责让用户看清这些已形成的判断。复杂复盘可能同时涉及事件经过、当时依据、事后解释与认识变化，独立讲解有可复用的理解价值。简单复盘仍可直接由 reflect 交付，不因为新增组合而要求每次委派、绘图、提炼通用教训或产生当前买卖建议。

离线检查覆盖技能注册、原文保真、组合引用及交付完整性，不证明模型实际执行了串联或讲解质量改善。

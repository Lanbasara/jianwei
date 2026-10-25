"""探源 (Tanyuan) — AI 投资系统的数据底层。

设计约束(见 docs/interface-spec.md):
- 对外只有 CLI + 单 JSON 信封;
- 每个值带出处(source/as_of/grade);
- 失败分类型、可行动,绝不静默降级。
"""
__version__ = "0.5.0"

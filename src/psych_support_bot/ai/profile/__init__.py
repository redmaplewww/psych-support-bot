"""自进化用户画像（profile memory module）。

画像不是静态标签表，而是 belief 流：提取（带证据）→ 冲突消解 →
质询验证 → 升级/证伪。本包承载画像侧的锚点表与提取器；
决策约束见 docs/technical/PROFILE_DECISIONS.md，
维度与词锚设计见 docs/plans/profile-memory-knowledge.md。
"""

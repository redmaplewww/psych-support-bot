# 画像记忆模块 · 知识库评估知识提炼

> 状态：设计输入 · 2026-09-12 提炼
> 目标读者：画像记忆模块（自进化用户画像）的设计与实现者
> 知识来源：`ai/knowledge/`（cbt / act / dbt / sfbt_mi / psychoeducation / foundations / crisis / index）
> 关联域模型：`domain/assessments/questionnaires.py`、`domain/assessments/service.py:severity_for_score`、`ai/memory_modules.py`
> 定位：本文不是画像系统的总体设计（分层 L1–L4、belief 流、质询闭环见对话纪要），只回答一个问题——
> **知识库里已有的评估知识，哪些可以直接转化为画像的维度、取值空间、证据锚点和红线？**

## 0. 结论摘要

- 知识库已经为画像系统准备好了四类可直接复用的资产：
  **闭集主题词表**（`TOPIC_KEYWORDS` 20 主题，中英双语）、
  **量化分带判据**（PHQ-9/GAD-7/ISI 严重度阈值 + 2 周持久性判据）、
  **机制词锚**（ACT typical_phrases、安全行为清单等"可观察语言信号"）、
  **动机语言标记**（MI change talk 7 分类）。
- 画像维度建议定为 8 个（D1–D8），每个维度都有知识库侧的闭集取值或词锚，不凭空发明分类。
- 提取器读取面必须遵守既有隔离规则：**只读用户原话（user_history_text）**，
  绝不把知识渲染文本、记忆模块渲染文本当成用户状态证据。
- 红线条目（诊断、人格标签、医疗风险）在提取契约层面直接禁止，不靠 prompt 提醒。

## 1. 知识盘点：每类知识对画像的价值

| 来源 | 内容 | 对画像的价值 |
|---|---|---|
| `index.py:TOPIC_KEYWORDS` | 20 主题 × 中英关键词闭集 | **D1 关注主题的取值空间**，现成的证据检测词表 |
| `psychoeducation.py` | 7 个科普模块 + 正常/临床分界 | D2 严重度判据来源；"何时该关注"的结构化启发式 |
| `domain/assessments/*` | PHQ-9/GAD-7/ISI 条目 + 分带 | D2 量化轴；**条目本身就是文本信号清单**（§3） |
| `foundations.py` | 11 篇基础机制短文 | D3 维持机制信念的依据；含 NSSI/物质/进食的功能模型 |
| `cbt.py:COGNITIVE_DISTORTIONS` | 11 种认知歪曲（名称/例句/挑战语） | D3 思维模式维度的闭集词锚（有严格红线，见 §7） |
| `act.py:ACT_CORE_PROCESSES` | 6 大过程 + typical_phrases | **现成的机制词锚库**：控制挣扎、融合、回避的经验性语言标记 |
| `dbt.py` | 4 模块技能 + opposite_action 等 | D4 干预技能库；情绪命名能力可作画像信号 |
| `sfbt_mi.py` | 刻度问句/例外问句/应对问句/change talk | D5 动机与阶段判定；**刻度值是现成的画像数值轨** |
| `crisis.py` | 4 级风险 + 安全计划 6 节 + 保护因子清单 | D7 保护因子 schema；红线条目的权威来源 |
| `practice_guidance.py` | 分模式指导原则 | D6 偏好维度的行为判据（提问预算、技能给予条件） |
| `learning_notes.json`（8 篇） | 外部语料合成的主题笔记 | 补充主题覆盖，非画像主源 |

## 2. 画像维度设计（D1–D8）

每个维度给出：取值空间（闭集优先）、证据锚点、层级归属（L1 用户自述 / L2 已确认演化状态 / L3 情境信号 / L4 待验证假设）、更新与衰减。

### D1 关注主题（concerns）

- **取值空间**：`TOPIC_KEYWORDS` 的 20 个键（anxiety / panic / depression / sleep / ocd / burnout / grief / anger / procrastination / rumination / self_worth / relationships / stress / motivation / social_anxiety / ptsd / eating_disorder / nssi / substance_use / relaxation）+ 自由文本补充槽（用户手填、放 L1）。
- **证据锚点**：主题关键词命中（`detect_topics` 已有实现与评分逻辑，提取器直接复用 `_normalize_text` / `_contains_keyword`）+ LLM 判断；同一主题需 ≥2 个不同会话的证据才可升 L2。
- **层级**：L2（已确认主诉）/ L4（单次会话新出现）。
- **衰减**：90 天无新证据 → 降权排序，不删除。
- **注意**：relaxation 是意图不是困扰主题，允许与其他主题共存。

### D2 严重度与功能状态（severity/functioning）

- **取值空间**：量表分带（枚举沿用 `severity_for_score`：minimal/mild/moderate/moderately_severe/severe + ISI 专有 none/subthreshold）+ 文本信号档位（无/疑似/持续）。
- **证据锚点**：量表记录走既有 AssessmentMemoryModule（勿重复建通道）；文本信号映射见 §3。
- **层级**：量表轨属 L3（TTL 跟随记录）；文本推断属 L4。
- **判据**：知识库给出的临床关注启发式（`normal_vs_clinical_distress`）可直接编码为提取器规则——
  持续 ≥2 周、显著损害功能、跨情境弥散、无明确诱因或诱因已消而症状存续、回避成为主要应对。
  满足 ≥2 条时该 D4/L4 条目提升质询与建议就医的优先级。

### D3 维持机制与思维模式（mechanisms）— 干预价值最高

- **取值空间**（闭集，锚点全部来自知识库原文）：
  - 回避/安全行为维持焦虑：过度预演、少说话、回避眼神接触、反复确认对方态度、提前离场（`foundations.py:social_anxiety_basics`）
  - 行为退缩减少正强化（depression_overview / behavioral_activation）
  - 反刍式"假问题解决"（rumination guide）
  - 睡眠努力/条件性觉醒（insomnia_overview：越努力越睡不着）
  - 对躯体感觉的灾难化解读（panic_attacks guide 的 anxiety sensitivity）
  - 拖延 = 情绪调节问题（procrastination guide：逃避任务引发的厌恶情绪）
  - 控制挣扎（ACT acceptance 的 typical_phrases："I need to get rid of this anxiety before I can move forward" 类）
  - 认知融合（defusion phrases："My thought says I am worthless, so I must be" 类）
  - 漂泊/自我批评式内在对话（self_criticism_and_shame）
  - 讨好-攻击-退缩交替的关系模式（relationship_conflict）
  - 认知歪曲 11 分类（`COGNITIVE_DISTORTIONS`，仅作分析参考，见 §7 红线）
- **层级**：一律先进 L4；经用户确认或 ≥3 次跨会话一致证据才升 L2。
- **为什么值钱**：干预知识按机制组织（回避→暴露、退缩→行为激活、融合→解离、控制→意愿）。
  画像里一条确认的机制信念，直接决定 response_generator 该选哪个学派的哪条路径——
  这是"个性化"的最大杠杆，也是质询闭环最该花预算的维度。

### D4 干预响应（intervention response）

- **取值空间**：练习标签闭集（`practice-pointer` 索引与练习库 tag：thought_record_full / behavioral_activation / tipp_full / values_card_sort / dear_man_assertion …）× 效果三元值（有效/无感/反感）。
- **证据锚点**：PracticeSessionRecord / ExerciseRecord（完成事实）+ 完成后对话中的语言反馈（"有用" vs "试了但更糟"）。
- **层级**：L2；按学派聚合出"体感类（TIPP/身体扫描）对 TA 比认知类（思维记录）更易上手"这类聚合信念。
- **更新**：每条新记录触发；效果信念 ≥2 次一致证据才稳定。

### D5 动机与改变阶段（readiness）

- **取值空间**：MI change talk 7 分类（DESIRE / ABILITY / REASON / NEED / COMMITMENT / ACTIVATION / TAKING_STEPS）+ sustain talk + SFBT 刻度值（现状/动机/信心 0–10）。
- **证据锚点**：`MI_TOOLS.change_talk.types` 的句式（"I wish…" / "I could…" / "I will…" / "I have been…"）直接可作双语词锚扩展；
  刻度问句历史数值是现成时间轨（`scaling_questions.variations` 四类）。
- **层级**：刻度值与近期语言标记属 L3；稳定的阶段画像属 L2。
- **判据**：sustain talk 是矛盾不是抵抗（`sustain_talk` 原文）——**提取器禁止输出"用户抗拒/不配合"类信念**，只允许"处于矛盾期，双方并存的语句如下"。

### D6 沟通与干预偏好（preferences）

- **取值空间**：提问容忍度（沿用 no_question_mode / loop_hint 既有信号）、直接-温和、认知-躯体偏好、被验证风格（OARS 中哪类回应获得正向继续）。
- **证据锚点**：practice_guidance 的分模式规则（assessment 最多 1-2 个追问、intervention 仅在用户明确想要时给技能）既是生成约束也是偏好观测点；
  对话内练习接受/拒绝事件（practice_route 裁决结果）。
- **层级**：L1（用户面板自述优先覆盖）+ L2（观测值，用户自述冲突时以用户为准）。

### D7 保护因子与资源（protective factors）

- **取值空间**：安全计划六节即现成 schema（`SAFETY_PLAN_TEMPLATE.sections`）：
  预警信号 / 自助应对策略 / 可求助的人 / 专业联系人 / 限制手段获取 / 活下来的理由；
  外加 `CRISIS_RISK_LEVELS.elevated` 明确列出的保护因子三件套：社会支持、活着的理由、应对技能；
  SFBT 例外问句产出（"问题较轻的时段发生了什么"）是保护因子的自然语言来源。
- **层级**：L1/L2，**知情同意门控**——只在用户参与安全计划流程或明确同意时写入，
  面板可见可改，删除走既有 `/v1/me` 删除链路。
- **注入策略**：本维度是画像里唯一允许在风险相关语境被引用的软上下文，
  但仍不参与 risk_classifier 判定（安全通道隔离不变）。

### D8 边界与负记忆（boundaries / negative memory）

- **取值空间**：用户声明的不谈话题；被否决的假设（deny 事件）；禁止复发条目。
- **规则**：用户否认过一次的 belief 永不质询第二次、永不复活（含换措辞复活——对 belief key 而非文本去重）。
- **层级**：L1/L2，最高注入优先级（比任何正画像都先渲染）。

## 3. 筛查条目 → 文本信号映射

量表的 23 个条目本身就是**免费对话中可观察信号的清单**。提取器在无量表记录时，
用同一套条目聚类从文本中产出 L4 档位的信号（明确标注"非量表分"）：

| 条目簇 | 来源量表 | 文本信号示例（中） | 档位判据 |
|---|---|---|---|
| 快感缺失+低落 | PHQ-1/2 | 提不起劲、没意思、情绪低落、绝望 | 出现频次 + 跨会话持续 |
| 睡眠三型 | PHQ-3 / ISI 1-3 | 入睡困难、半夜易醒、早醒 | 三型分开记，干预路径不同（刺激控制/睡眠限制） |
| 躯体-能量 | PHQ-4/5 | 疲惫、没胃口/吃太多 | 单独出现优先查躯体/作息，不急着心理归因 |
| 自评价 | PHQ-6 | 我很没用、让自己失望 | 与 D3 self_criticism 联动 |
| 认知功能 | PHQ-7/8 | 看不进去、注意力涣散、迟钝/坐立不安 | 与 TOPIC_KEYWORDS procrastination 词表重叠，注意归并 |
| 担忧三联 | GAD-1/2/3 | 控制不住担心、各种事都担心、很难放松 | "控制不住"是核心词锚（对应 GAD 本质定义） |
| 警觉-烦躁 | GAD-5/6/7 | 坐立不安、易怒、好像要出事 | 与易激惹/愤怒主题归并 |
| 睡眠影响与苦恼 | ISI 4-7 | 对睡眠满意吗、白天影响、越想越烦 | 影响轴与苦恼轴分开（ISI 设计本意） |

**硬规则**：PHQ-9 第 9 条（自伤想法）**永远不进画像层**。文本中出现即触发既有风险通道
（risk_classifier / RiskEvent），画像层对该信号只记录"已发生风险事件"这一事实存在与否，
不存内容、不做档位。同理 ISI/PHQ 的任何条目文本都不得在情绪扫描通道被当成用户情绪
（沿用 `memory_modules.py` 头注释的隔离原则——本规则对画像提取同样生效）。

**子型入 value 不入 key**（fixture 复核时定稿）：睡眠三型等信号变体以 `value_json`
区分（如 `{"signal": "onset" | "maintenance" | "early_wakening"}`），belief key 保持
`sleep` 等稳定闭集成员——key 空间膨胀会破坏"换措辞复活"去重与展示词典映射。

## 4. 词锚库的组织建议

提取器需要一份**双语锚点表**（`knowledge → 画像锚点` 的编译产物），建议随代码维护为数据文件：

> 已落地：`ai/profile/anchors.py`（live-bind 知识原文 + 中文等义扩展，
> 一致性单测 `tests/unit/test_profile_anchors.py` 钉住绑定不脱钩）。

1. ACT `typical_phrases`（5 过程共 15 条）→ D3 控制挣扎/融合/反刍/自我概念融合锚点 + D5 等待准备状态（英文原句 live-bind + 中文等义扩展）
2. `social_anxiety_basics` 安全行为清单（5 条）→ D3 回避维持锚点（双语本表编写，en 词形钉在源文本上）
3. MI change talk 句式（7 类动词短语）→ D5 锚点
4. `COGNITIVE_DISTORTIONS` 的 examples（33 条例句）→ D3 参考锚点（仅用于识别，条目输出见 §7）
5. `TOPIC_KEYWORDS` 全表 → D1 锚点（直接 import，不复制）
6. `GAD-7 条目 2`（"无法停止或控制担忧"）→ D2 担忧失控锚点

原则：**锚点表是知识库的编译视图，不是第二个事实源**。知识文件更新时锚点表可重新生成；
锚点只负责"降低 LLM 提取的漏检率"，最终判定仍由提取器 LLM 给出并附 message_id 证据。

## 5. 提取器输出契约（草案）

```json
{
  "claims": [
    {
      "dimension": "D3",
      "key": "avoidance_maintenance.social",
      "claim_zh": "社交场合的回避似乎在维持焦虑",
      "value": { "mechanism": "avoidance", "target": "social" },
      "evidence_message_ids": [1204, 1219],
      "confidence": 0.45,
      "relation": "supports",
      "target_belief_id": null
    }
  ]
}
```

- 单次提取 ≤3 条 claim（预算与单 worker 约束，见 §6）；无新证据输出空数组。
- confidence ≥0.7 且跨会话证据 ≥2 → 允许进入 L4 质询候选池；L2 只能来自用户确认或程序硬证据（量表/记录）。
- 冲突处理：`relation: "contradicts"` 指向已有 belief → 旧条目降级回 L4 并附双向证据，不覆盖。
- 输出侧硬过滤（代码而非 prompt）：dimension 不在 D1–D8 闭集内、value 含诊断词表命中、
  D7 未经同意门控 → 一律丢弃并记计数埋点（只记动作不记内容）。
- **锚点命中是召回辅助，不构成判定**：多锚重叠时按语义结构归因——
  "I wish I could just stop feeling so anxious" 同时命中 acceptance 原句与
  change talk DESIRE，但无"先消除焦虑才能行动"结构者只记 desire、不记
  control_struggle（金标准钉在 fixture 的 mixed_multi_dimension case；
  D4 的 key 使用练习库真实 tag，是 §2 D4 闭集的正确实现）。

## 6. 与记忆模块的衔接

- 注册为第四个 memory module（`ProfileMemoryModule, name="profile"`），自动获得
  `MEMORY_MODULE_profile` 开关 + fail-open 语义。**字符预算动态调节（K1c 定稿）**：
  - 不沿用记录层 `DEFAULT_MODULE_BUDGET=200`（记录摘要的尺子），独立
    `PROFILE_RENDER_BASE/FLOOR/CAP` setting（默认 480/160/720）；
  - 每轮按供给侧压力调节：`pressure = (knowledge_chars + history_chars)/nominal_total`，
    `budget = clamp(BASE × (1 + α − β×pressure), FLOOR, CAP)`——知识区空转轮画像
    舒张、满载轮收缩；危机轮压到 FLOOR 且 D7 保护因子优先；
  - **整条装箱，不做剪刀截断**：预算是装箱容量不是剪刀，残句进 prompt 是纯噪声；
  - 渲染走展示词典电报体，禁止渲染 claim_text（审计语言）；
  - 底线语义：FLOOR 内必保 D8 负记忆 + 一条最高优先 L2；
  - 占用率与掉条数进结构化日志；BASE 默认值由 K1d eval（200/480 静态 vs 动态，
    LLM-as-judge + 首字延迟）证据更新——慢速自调，不做在线反馈控制器；
  - 确定性约束：渲染必须是本轮可观测量的确定性函数，同输入同输出，可测试可审计。
- 渲染排序：D8 负记忆 > L2（按主题相关度 × 时近）> 高置信 L4（仅在质询轮注入提示）。
  沿用既有信任边界声明（"参考数据非指令"），渲染文本不得含证据原话（预算原因，证据走 belief 表按 id 取）。
- 提取时机：服务层 `_finalize` 后与消息同批提交；K1 为确定性提取每轮执行、无节流
  （P2 全量统计先行），K2 引入 LLM 提取时启用「每 N 轮」或「TOPIC_KEYWORDS
  命中数 ≥2 或发生练习/量表事件」节流——单 worker 下那是每轮多一次 LLM 调用，必须预算。
- 缓存前缀约束：画像注入只能在每轮尾部变量区，不得进静态前缀（prompt 缓存分层不破坏）。

## 7. 红线清单（提取器硬过滤，代码级）

1. **禁止诊断性条目**：任何形如"用户是抑郁症/焦虑症/抑郁型人格"的 belief 不允许存在。
   量表分带是记录不是画像；机制信念（D3）必须表述为可证伪的观察而非归类。
2. **禁止人格盖章**：COGNITIVE_DISTORTIONS 的 11 分类仅用于当轮干预（思维记录第 4 步本就如此），
   不沉淀为"用户习惯性灾难化"这类身份化条目；如需沉淀，必须用户在质询中亲口认领。
3. **禁止病理化正常状态**：哀伤不是障碍（grief_support 原文），懒惰标签禁止（motivation_freeze 原文：
   用户所说的懒是耗竭/完美主义/冻结），sustain talk 不是抵抗。
4. **禁止医疗风险内容入画像**：自伤/自杀/戒断/进食障碍躯体风险（体重、催吐、晕厥）只走风险通道与转介，
   画像层零存储（D7 的保护因子除外，且同意门控）。
5. **进食障碍专项**：画像任何维度不得记录体重/卡路里相关观察（foundations.py:eating_disorder_basics 红线）。
6. **隔离规则继承**：提取证据面 = 用户原话 + 用户产出的记录；知识文本、模块渲染文本不构成用户状态证据。

## 8. 落地顺序建议

1. **K0 锚点表**：把 §4 词锚编译为数据文件 + 单测（锚点表与知识库一致性校验）——纯增量，无风险。
2. **K1 D1+D4**：主题与干预响应两个维度先行——它们几乎只需复用既有结构化数据
   （TOPIC_KEYWORDS / PracticeSessionRecord / ExerciseRecord），LLM 提取占比最小，先验证 belief 表与注入链路。
3. **K2 D3+D5**：机制与动机维度——LLM 提取主战场，词锚表在这里兑现价值；质询闭环接入 question_strategy。
4. **K3 D7+D8**：保护因子（同意门控）与负记忆（否决闭环），配合画像面板可视化。

## 9. 顺手发现的知识库索引问题（与本任务无关但建议记入 backlog）

- `cbt.py:203` 练习键 `" downward_arrow"` 带前导空格（exercise_id 字段本身是干净的），
  导致 `index.py:exercise_topic_map.get(" downward_arrow")` 落空，该练习在知识索引中主题退化为默认 stress。
- `exercise_topic_map` 未覆盖 `exposure_hierarchy / worst_best_realistic / cost_benefit_analysis /
  self_compassion_letter`（CBT）及 `defusion_tunnel / defusion_sing / observing_self / willingness_choice`（ACT），
  这些条目在索引中全部按 `("stress",)` 兜底，主题检索命中率受损。

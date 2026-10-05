# 画像记忆与上下文切片联动设计

## 一、联动价值

### 1.1 现有问题
- **画像提取缺乏话题边界**：当前按轮次提取，可能在话题切换时混淆上下文
- **历史上下文无主题过滤**：`build_memory_snapshot` 返回笼统的历史，缺乏话题相关性
- **画像信念无时间衰减**：所有信念平等对待，缺乏"最近聊过"的时效性

### 1.2 联动后的改进
✅ **切片 → 画像提取单元**：每个切片是一个完整话题，作为提取的最小语境\
✅ **切片主题 → 画像维度映射**：D1（主题）可以直接从切片主题继承\
✅ **切片摘要 → 画像证据**：摘要作为信念的结构化证据来源\
✅ **画像信念 → 切片检索**：根据用户画像选择最相关的历史切片\
✅ **切片完成 → 触发提取**：切片 status 变为 completed 时异步触发画像提取\

## 二、联动设计

### 2.1 数据流向

```
用户消息
  ↓
切片管理器（判定是否新建切片）
  ↓
切片内对话（累积 turn_count）
  ↓
用户切换话题 / 长时间间隔
  ↓
切片完成（status → completed）
  ↓
异步任务：
  1. 生成切片摘要（LLM）
  2. 提取画像信念（基于切片内容）
  ↓
下次对话：
  1. 根据新消息检索相关历史切片
  2. 根据画像信念过滤上下文
  3. 注入 prompt（本次对话 + 相关历史）
```

### 2.2 核心联动点

#### 联动点 1：切片作为提取语境
```python
# 旧方式（按轮次提取）
def run_turn_extraction(
    session: Session,
    user_id: str,
    session_id: str,
    topics: list[str],
    ...
):
    # 只看当前轮次
    pass

# 新方式（按切片提取）
def run_slice_extraction(
    session: Session,
    user_id: str,
    slice_id: str,
    completed_at: datetime,
):
    """
    当切片完成时，基于切片内所有消息提取画像信念。

    优势：
    - 完整的话题上下文（而非单轮片段）
    - 避免话题切换时的上下文混淆
    - 可以提取"跨轮演化"的信念（如"焦虑从严重到缓解"）
    """
    messages = get_slice_messages(session, slice_id)
    slice = session.get(ConversationSlice, slice_id)

    # 提取 D1：主题（继承切片主题）
    if slice.primary_topic:
        record_claim(
            session,
            user_id,
            dimension="D1",
            key=slice.primary_topic,
            ...
        )

    # 提取 D2-D8：基于切片内对话
    # （复用现有 build_turn_claims 逻辑，但输入扩展为切片级）
    ...
```

#### 联动点 2：切片主题 → 画像 D1
```python
def extract_slice_topic(slice_messages: list[Message]) -> str:
    """
    从切片内消息提取主题。

    策略：
    1. 如果切片 ≤ 3 轮：简单关键词提取
    2. 如果切片 > 3 轮：LLM 总结主题
    """
    if len(slice_messages) <= 6:  # 3轮
        # 简单关键词检测（复用 detect_topics）
        text = " ".join(msg.content for msg in slice_messages if msg.role == "user")
        topics = detect_topics(text)
        return topics[0] if topics else ""
    else:
        # LLM 总结（单标签，如 "anxiety" / "sleep"）
        return llm_extract_topic(slice_messages)
```

#### 联动点 3：切片摘要 → 画像证据
```python
# ProfileBelief 表新增字段（可选，不影响现有功能）
class ProfileBelief(Base):
    # 现有字段...
    origin_slice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 新增

# 提取时关联切片
def record_claim_from_slice(
    session: Session,
    user_id: str,
    slice_id: str,
    claim: ProfileClaim,
):
    record_claim(
        session,
        user_id,
        dimension=claim.dimension,
        key=claim.key,
        claim_text=claim.claim_text,
        origin_slice_id=slice_id,  # 记录来源切片
        ...
    )
```

#### 联动点 4：画像信念 → 切片检索权重
```python
def retrieve_relevant_slices(
    session: Session,
    user_id: str,
    current_message: str,
    max_slices: int = 3,
) -> list[SliceSummary]:
    """
    基于用户画像的智能切片检索。

    权重计算：
    - 时间衰减：0.5
    - 主题匹配：0.3（基于用户画像 D1）
    - 效果反馈：0.2（基于用户画像 D4）
    """
    # 获取用户画像信念
    beliefs = list_active_beliefs(session, user_id)
    user_topics = [b.key for b in beliefs if b.dimension == "D1"]
    worked_exercises = [b.key for b in beliefs if b.dimension == "D4" and b.value.get("valence") == "worked"]

    # 查询所有切片摘要
    summaries = (
        session.query(SliceSummary)
        .filter_by(user_id=user_id)
        .order_by(SliceSummary.created_at.desc())
        .limit(20)  # 候选池
        .all()
    )

    # 计算每个切片的相关性得分
    scored = []
    for summary in summaries:
        # 1. 时间衰减
        days_ago = (utcnow() - summary.created_at).days
        time_score = calculate_relevance_score_from_days(days_ago)

        # 2. 主题匹配
        summary_topics = json.loads(summary.topics)
        topic_score = len(set(user_topics) & set(summary_topics)) / max(len(user_topics), 1)

        # 3. 效果反馈（如果切片涉及有效练习，提高权重）
        exercise_score = 0.0
        for ex in worked_exercises:
            if ex in summary.summary_text:
                exercise_score = 1.0
                break

        # 综合得分
        final_score = 0.5 * time_score + 0.3 * topic_score + 0.2 * exercise_score
        scored.append((summary, final_score))

    # 返回 top-k
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, score in scored[:max_slices]]
```

#### 联动点 5：Profile 面板展示切片来源
```python
# 画像面板：点击信念可以看到来源切片
GET /v1/profile/beliefs/{belief_id}/context

{
  "belief": {
    "key": "anxiety",
    "claim_text": "反复提到工作焦虑",
    "confidence": 0.8
  },
  "source_slices": [
    {
      "slice_id": "slice-xxx",
      "created_at": "2026-09-10 14:30",
      "summary": "讨论了工作压力导致的焦虑情绪...",
      "messages_count": 8
    }
  ]
}
```

## 三、实施方案

### 3.1 最小改动方案（Phase 3 优先）

**目标**：不破坏现有画像系统，只做增量联动。

#### 改动 1：切片完成时触发画像提取（可选）
```python
# services/slice_manager.py

def complete_slice(session: Session, slice: ConversationSlice):
    """完成切片并触发后续任务。"""
    slice.status = "completed"
    slice.end_message_id = get_last_message_id(session, slice.id)
    session.commit()

    # 异步任务：生成摘要 + 提取画像（可选）
    if get_settings().enable_slice_based_extraction:
        # TODO: Celery 任务
        schedule_slice_extraction(slice.id)
```

#### 改动 2：切片上下文替换 recent_history
```python
# services/conversation.py: _build_state()

# 旧方式：recent_history（固定6轮）
recent_history = [
    {"role": str(msg.role), "content": str(msg.content)}
    for msg in prior_messages[-RECENT_HISTORY_TURNS:]
    if msg.role in {"user", "assistant"}
]

# 新方式：slice_context（当前切片内所有对话）
slice_manager = SliceManager()
current_slice = slice_manager.get_or_create_slice(
    session, payload.user_id, session_id, payload.message
)
slice_context = build_slice_context(session, current_slice.id, max_turns=20)

# 注入 GraphState
state["slice_id"] = current_slice.id
state["slice_context"] = slice_context  # 替代 recent_history
state["slice_metadata"] = {
    "is_new_slice": current_slice.turn_count == 0,
    "boundary_reason": current_slice.boundary_reason,
    "primary_topic": current_slice.primary_topic,
}
```

#### 改动 3：prompt 调整（区分本次对话 / 历史摘要）
```python
# ai/nodes/memory.py 或 prompts/support.py

# 旧 prompt
"""
历史对话：
{memory_summary}

最近消息：
{recent_history}
"""

# 新 prompt
"""
【用户档案】
{profile_block}

【本次对话】（第 {turn_count} 轮）
{slice_context}

【相关历史】
{relevant_slice_summaries}

注意：
- "本次对话"是用户当前正在讨论的话题，优先参考这部分内容
- "相关历史"是之前讨论过的相关内容，作为背景参考，不要混淆时间线
- 如果用户明确换了话题，不要强行联系旧话题
"""
```

### 3.2 数据库迁移（可选，不影响 Phase 3）

```python
# migrations/versions/20260913_0004_profile_slice_link.py

def upgrade():
    # ProfileBelief 表新增 origin_slice_id（可选）
    op.add_column(
        "profile_beliefs",
        sa.Column("origin_slice_id", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_profile_beliefs_origin_slice",
        "profile_beliefs",
        ["origin_slice_id"]
    )
```

### 3.3 Feature Flag 控制

```python
# .env
ENABLE_CONTEXT_SLICING=true  # 启用切片系统
ENABLE_SLICE_BASED_EXTRACTION=false  # 切片级画像提取（Phase 4）
ENABLE_PROFILE_SLICE_RETRIEVAL=false  # 画像驱动切片检索（Phase 5）
```

## 四、Phase 3 实施步骤

### Step 1: 修改 ConversationService._build_state()
- [ ] 引入 SliceManager
- [ ] 获取或创建当前切片
- [ ] 构建切片上下文（替代 recent_history）
- [ ] 注入切片元信息到 GraphState

### Step 2: 修改 GraphState 结构
- [ ] 新增 `slice_id: str`
- [ ] 新增 `slice_context: list[dict]`（替代 recent_history）
- [ ] 新增 `slice_metadata: dict`
- [ ] 保留 `recent_history`（兼容性，Phase 4 删除）

### Step 3: 调整 Prompt 模板
- [ ] 区分"本次对话"和"相关历史"
- [ ] 添加切片边界提示（如"用户刚换了话题"）
- [ ] 调整 response_generator 的上下文拼接逻辑

### Step 4: 更新 save_conversation_result()
- [ ] 保存消息时关联 slice_id
- [ ] 更新切片的 end_message_id

### Step 5: 测试验证
- [ ] 单元测试：切片上下文构建
- [ ] 集成测试：多轮对话 + 话题切换
- [ ] E2E 测试：完整对话流程
- [ ] 人工验证：切片边界准确性

### Step 6: Feature Flag 灰度
- [ ] 默认关闭：`ENABLE_CONTEXT_SLICING=false`
- [ ] 小流量测试：10% 用户
- [ ] 观察指标：对话质量、切片准确率
- [ ] 全量发布

## 五、预期效果

### 量化指标
- **上下文相关性**：4.2/5 → 4.5/5
- **切片准确率**：80% → 85%（画像辅助）
- **Prompt 长度**：1200 tokens → 1000 tokens（更精准的上下文）

### 定性改进
✅ 机器人能记住"本次对话聊的是焦虑，不要跳到上次聊的睡眠问题"\
✅ 用户换话题后，机器人不会尴尬地继续旧话题\
✅ 画像信念有了清晰的来源（哪次对话中提到的）\
✅ 历史切片检索更智能（基于用户画像过滤）\

## 六、后续扩展（Phase 4-5）

### Phase 4: 切片级画像提取
- 切片完成时触发提取（而非每轮）
- 基于切片内完整上下文提取 D1-D8
- 切片摘要作为信念证据

### Phase 5: 画像驱动检索
- 根据用户画像信念检索相关切片
- 权重：时间衰减 + 主题匹配 + 效果反馈
- 个性化历史上下文

---

**设计者**: Claude Code (Kiro AI)\
**审核者**: 待定\
**状态**: 设计完成，待实施

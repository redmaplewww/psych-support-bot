# 上下文动态智能管理方案

## 一、问题分析

### 1.1 当前问题
- **会话边界模糊**：同一 session_id 下的对话没有"本次对话"和"上次对话"的区分
- **上下文污染**：新话题会被旧话题的上下文干扰（如用户换了话题，模型还在延续旧话题）
- **记忆负载不可控**：所有历史消息平等对待，缺乏优先级和时效性管理

### 1.2 业务约束
- **连续性需求**：心理支持需要长期记忆（量表记录、风险事件、画像信息）
- **安全优先**：风险评估必须跨会话追踪，不能因为"新对话"而遗忘
- **场景特殊性**：
  - 用户可能在同一天内多次咨询不同问题
  - 用户可能隔几天后继续之前的话题
  - 需要识别"换话题"vs"继续聊"

## 二、设计方案：三层上下文架构

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      用户上下文空间                           │
├─────────────────────────────────────────────────────────────┤
│  L1: 对话切片层（Conversation Slice）                        │
│      - 自动检测话题切换                                       │
│      - 维护"本次对话"边界                                     │
│      - 提供逐字近史（6-20轮）                                 │
├─────────────────────────────────────────────────────────────┤
│  L2: 会话摘要层（Session Summary）                           │
│      - 存储历史对话摘要                                       │
│      - 按时间衰减权重                                         │
│      - 支持相关性检索                                         │
├─────────────────────────────────────────────────────────────┤
│  L3: 持久记忆层（Persistent Memory）                         │
│      - 用户画像（profile）                                    │
│      - 结构化记录（assessments/checkins/exercises）          │
│      - 风险事件（RiskEvent）                                  │
│      - 长期主题追踪                                           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 L1: 对话切片层（核心创新）

#### 2.2.1 切片检测机制

引入 `conversation_slice_id` 概念，在 session_id 下细分"本次对话"：

```python
# 新增数据模型
class ConversationSlice(Base):
    """对话切片：一个 session 下的一次完整对话。"""
    __tablename__ = "conversation_slices"

    id = Column(String, primary_key=True)  # slice-{uuid}
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # 切片元信息
    start_message_id = Column(String, ForeignKey("messages.id"))
    end_message_id = Column(String, ForeignKey("messages.id"), nullable=True)  # None 表示进行中

    # 切片识别特征
    primary_topic = Column(String)  # 主题标签（如 "anxiety", "sleep", "relationship"）
    topic_vector = Column(JSON)  # 主题向量（可选，用于相似度计算）

    # 切片边界判定依据
    boundary_reason = Column(String)  # 切片原因：time_gap / topic_shift / explicit_switch / crisis_resolved
    boundary_confidence = Column(Float)  # 0-1，切片判定的置信度

    # 统计
    turn_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # 切片状态
    status = Column(String, default="active")  # active / completed / archived
```

#### 2.2.2 自动切片触发条件

**时间维度（动态自适应）**：
- ~~固定阈值（废弃）~~：距离上次对话 > 2 小时 → 大概率新对话
- **动态阈值（新）**：根据用户历史行为自适应调整
  - 计算用户的「典型对话间隔」和「作息模式」
  - 短间隔用户（频繁咨询）：阈值 = 30分钟 - 2小时
  - 长间隔用户（偶尔使用）：阈值 = 6小时 - 48小时
  - 跨日边界特殊处理：识别"睡眠间隔"（如晚11点→次日早8点）

**内容维度**：
- 用户明确表示换话题：
  - "换个话题"、"聊点别的"、"不想说这个了"
  - "我想问个新问题"、"有个新情况"
- 主题向量相似度 < 阈值
- 情绪基调显著变化（如从焦虑转为兴奋）

**结构化信号**：
- 完成了练习/量表 → 可能开启新话题
- 危机模式结束 → 切片边界
- 用户显式"结束对话"："好的，谢谢"、"我先去休息了"

**混合判定策略（集成动态时间阈值）**：
```python
def should_create_new_slice(
    last_slice: ConversationSlice | None,
    user_message: str,
    user_id: str,
    session: Session,
) -> tuple[bool, str, float]:
    """
    返回: (是否切片, 原因, 置信度)
    """
    if last_slice is None:
        return True, "first_message", 1.0

    # 1. 时间维度（动态自适应）
    hours_since = (utcnow() - last_slice.updated_at).total_seconds() / 3600
    time_profile = get_user_time_profile(session, user_id)

    # 获取动态阈值
    short_threshold = time_profile["short_gap_threshold"]  # 如 0.5 小时
    long_threshold = time_profile["long_gap_threshold"]    # 如 6 小时
    sleep_boundary = is_sleep_boundary(
        last_slice.updated_at,
        utcnow(),
        time_profile["sleep_window"]
    )

    if sleep_boundary:
        # 跨睡眠边界：几乎确定是新对话
        return True, "sleep_boundary", 0.95
    elif hours_since > long_threshold:
        # 超过长间隔阈值：高置信度新对话
        return True, f"time_gap_{int(hours_since)}h", 0.9
    elif hours_since > short_threshold:
        # 超过短间隔阈值：中等置信度，需结合其他信号
        time_signal = min(0.8, (hours_since - short_threshold) / (long_threshold - short_threshold))
    else:
        time_signal = 0.0

    # 2. 显式切换信号
    explicit_markers = [
        "换个话题", "聊点别的", "不想说这个",
        "我想问", "有个新", "另一个问题",
        "change topic", "talk about something else",
    ]
    if any(marker in user_message.lower() for marker in explicit_markers):
        return True, "explicit_switch", 0.9

    # 3. 主题相似度（需要实现主题提取）
    current_topic_vec = extract_topic_vector(user_message)
    if last_slice.topic_vector:
        similarity = cosine_similarity(current_topic_vec, last_slice.topic_vector)
        if similarity < 0.3:
            topic_signal = 0.8
        else:
            topic_signal = 0.0
    else:
        topic_signal = 0.0

    # 4. 结束信号后的新消息
    goodbye_markers = ["谢谢", "再见", "我先去", "good bye", "thank you"]
    last_messages = get_slice_messages(session, last_slice.id, limit=2)
    if last_messages and any(marker in last_messages[-1].content.lower() for marker in goodbye_markers):
        return True, "after_goodbye", 0.85

    # 综合判定（时间信号权重提升）
    combined_score = max(time_signal, topic_signal)
    if combined_score > 0.6:
        return True, "topic_shift", combined_score

    return False, "continue", 1.0 - combined_score
```

#### 2.2.3 用户时间画像系统

**数据模型**：
```python
class UserTimeProfile(Base):
    """用户时间行为画像（自适应切片阈值）。"""
    __tablename__ = "user_time_profiles"

    user_id = Column(String, ForeignKey("users.id"), primary_key=True)

    # 对话间隔统计（分钟）
    avg_gap_minutes = Column(Float)  # 平均对话间隔
    median_gap_minutes = Column(Float)  # 中位数对话间隔
    p25_gap_minutes = Column(Float)  # 25分位数（短间隔）
    p75_gap_minutes = Column(Float)  # 75分位数（长间隔）

    # 作息模式（24小时制，UTC+8）
    sleep_start_hour = Column(Integer, default=23)  # 入睡时间（如 23 点）
    sleep_end_hour = Column(Integer, default=7)     # 起床时间（如 7 点）

    # 活跃时段（JSON：[{start: 9, end: 12}, {start: 14, end: 22}]）
    active_windows = Column(JSON)

    # 使用频率分类
    frequency_tier = Column(String)  # high / medium / low
    # - high: 几乎每天使用，间隔短
    # - medium: 每周几次，间隔中等
    # - low: 偶尔使用，间隔长

    # 统计元数据
    total_sessions = Column(Integer, default=0)
    last_updated = Column(DateTime, default=utcnow, onupdate=utcnow)

    # 动态阈值（缓存计算结果）
    short_gap_threshold_minutes = Column(Float)  # 短间隔阈值
    long_gap_threshold_minutes = Column(Float)   # 长间隔阈值
```

**画像计算逻辑**：
```python
def calculate_time_profile(session: Session, user_id: str) -> dict:
    """
    分析用户历史对话，计算时间画像。

    返回格式：
    {
        "short_gap_threshold": 0.5,  # 小时
        "long_gap_threshold": 6.0,   # 小时
        "sleep_window": (23, 7),     # (入睡时, 起床时)
        "frequency_tier": "medium",
        "confidence": 0.8,           # 画像置信度（样本量相关）
    }
    """
    # 查询用户所有会话的消息时间戳
    messages = (
        session.query(Message.created_at)
        .join(ConversationSession)
        .filter(ConversationSession.user_id == user_id)
        .order_by(Message.created_at)
        .all()
    )

    if len(messages) < 5:
        # 新用户：使用保守默认值
        return {
            "short_gap_threshold": 1.0,   # 1 小时
            "long_gap_threshold": 12.0,   # 12 小时
            "sleep_window": (23, 7),
            "frequency_tier": "unknown",
            "confidence": 0.3,
        }

    # 计算消息间隔（分钟）
    timestamps = [msg.created_at for msg in messages]
    gaps_minutes = [
        (timestamps[i] - timestamps[i-1]).total_seconds() / 60
        for i in range(1, len(timestamps))
    ]

    # 过滤异常值（超过7天的间隔视为"新一轮使用"，不计入间隔统计）
    valid_gaps = [g for g in gaps_minutes if g <= 7 * 24 * 60]

    if len(valid_gaps) < 3:
        # 样本不足
        return {
            "short_gap_threshold": 1.0,
            "long_gap_threshold": 12.0,
            "sleep_window": (23, 7),
            "frequency_tier": "unknown",
            "confidence": 0.4,
        }

    # 统计分位数
    import numpy as np
    p25 = np.percentile(valid_gaps, 25) / 60  # 转小时
    median = np.percentile(valid_gaps, 50) / 60
    p75 = np.percentile(valid_gaps, 75) / 60
    mean = np.mean(valid_gaps) / 60

    # 阈值策略：
    # - short_gap_threshold = max(p25, 0.5h)  # 至少 30 分钟
    # - long_gap_threshold = min(p75 * 1.5, 24h)  # 最多 24 小时
    short_threshold = max(p25, 0.5)
    long_threshold = min(p75 * 1.5, 24.0)

    # 频率分类
    if median < 2:  # 中位数间隔 < 2 小时
        frequency_tier = "high"
    elif median < 12:  # 中位数间隔 < 12 小时
        frequency_tier = "medium"
    else:
        frequency_tier = "low"

    # 作息模式检测（简化版：找凌晨时段的空白）
    hours = [ts.hour for ts in timestamps]
    night_hours = [h for h in hours if 0 <= h < 6]
    if len(night_hours) / len(hours) < 0.05:  # 凌晨消息很少
        sleep_start = 23
        sleep_end = 7
    else:
        # 用户可能是夜猫子，使用默认值
        sleep_start = 1
        sleep_end = 9

    # 置信度：样本量越多越可信
    confidence = min(1.0, len(valid_gaps) / 50)  # 50+ 样本达到满置信度

    return {
        "short_gap_threshold": short_threshold,
        "long_gap_threshold": long_threshold,
        "sleep_window": (sleep_start, sleep_end),
        "frequency_tier": frequency_tier,
        "confidence": confidence,
    }


def get_user_time_profile(session: Session, user_id: str) -> dict:
    """
    获取用户时间画像（带缓存）。

    策略：
    - 每 50 条新消息重新计算一次
    - 缓存到 UserTimeProfile 表
    """
    profile = session.get(UserTimeProfile, user_id)

    # 判断是否需要更新
    if profile is None:
        # 首次计算
        computed = calculate_time_profile(session, user_id)
        profile = UserTimeProfile(
            user_id=user_id,
            avg_gap_minutes=computed["short_gap_threshold"] * 60,
            median_gap_minutes=computed["long_gap_threshold"] * 60,
            sleep_start_hour=computed["sleep_window"][0],
            sleep_end_hour=computed["sleep_window"][1],
            frequency_tier=computed["frequency_tier"],
            short_gap_threshold_minutes=computed["short_gap_threshold"] * 60,
            long_gap_threshold_minutes=computed["long_gap_threshold"] * 60,
            total_sessions=1,
        )
        session.add(profile)
        session.commit()
        return computed

    # 检查是否需要重新计算（每 50 次会话）
    current_sessions = session.query(ConversationSession).filter_by(user_id=user_id).count()
    if current_sessions - profile.total_sessions >= 50:
        # 重新计算
        computed = calculate_time_profile(session, user_id)
        profile.short_gap_threshold_minutes = computed["short_gap_threshold"] * 60
        profile.long_gap_threshold_minutes = computed["long_gap_threshold"] * 60
        profile.sleep_start_hour = computed["sleep_window"][0]
        profile.sleep_end_hour = computed["sleep_window"][1]
        profile.frequency_tier = computed["frequency_tier"]
        profile.total_sessions = current_sessions
        session.commit()
        return computed

    # 使用缓存
    return {
        "short_gap_threshold": profile.short_gap_threshold_minutes / 60,
        "long_gap_threshold": profile.long_gap_threshold_minutes / 60,
        "sleep_window": (profile.sleep_start_hour, profile.sleep_end_hour),
        "frequency_tier": profile.frequency_tier,
        "confidence": 1.0,  # 缓存数据默认高置信度
    }


def is_sleep_boundary(
    last_time: datetime,
    current_time: datetime,
    sleep_window: tuple[int, int],
) -> bool:
    """
    判断两个时间戳是否跨越睡眠边界。

    例如：
    - last_time: 2024-01-15 23:30
    - current_time: 2024-01-16 08:00
    - sleep_window: (23, 7)
    -> 返回 True（跨越了睡眠时段）
    """
    sleep_start, sleep_end = sleep_window

    # 简化判定：如果间隔 > 6 小时，且 current_time 在起床时间之后
    hours_gap = (current_time - last_time).total_seconds() / 3600
    if hours_gap < 6:
        return False

    # 检查 last_time 是否在睡前时段（如 22-24 点）
    last_hour = last_time.hour
    curr_hour = current_time.hour

    if sleep_start > sleep_end:  # 跨日睡眠（如 23 点 -> 次日 7 点）
        last_in_sleep_start = last_hour >= sleep_start or last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end and curr_hour < sleep_start
        return last_in_sleep_start and curr_after_sleep
    else:  # 同日睡眠（如午休 13-15 点）
        last_in_sleep = sleep_start <= last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end
        return last_in_sleep and curr_after_sleep
```

**客户端时间注入（可选增强）**：
```python
class ConversationRequest(BaseModel):
    user_id: str
    message: str
    session_id: str | None = None
    memory_summary: str | None = None
    client_timezone: str | None = None  # 新增：如 "Asia/Shanghai"
    client_local_time: datetime | None = None  # 新增：客户端本地时间
```

#### 2.2.4 切片内上下文管理

```python
def build_slice_context(
    session: Session,
    slice_id: str,
    max_turns: int = 20,
) -> list[dict]:
    """
    构建切片内的完整上下文（逐字对话）。

    与现有 recent_history 不同：
    - recent_history: 固定取最近 6 轮（跨切片）
    - slice_context: 取当前切片内的所有对话（最多 max_turns）
    """
    messages = get_slice_messages(session, slice_id, limit=max_turns)
    return [
        {"role": msg.role, "content": msg.content}
        for msg in messages
        if msg.role in {"user", "assistant"}
    ]
```

### 2.3 L2: 会话摘要层

#### 2.2.5 动态阈值实例对比

**场景 1：高频用户（频繁咨询者）**
```
用户 A 的对话历史：
- 1月1日 09:00
- 1月1日 09:15  (间隔 15min)
- 1月1日 10:30  (间隔 75min)
- 1月1日 14:00  (间隔 210min)
- 1月1日 14:45  (间隔 45min)
...

统计结果：
- p25 = 20min, median = 60min, p75 = 150min
- frequency_tier = "high"

动态阈值：
- short_gap_threshold = max(20min, 30min) = 30min
- long_gap_threshold = 150min * 1.5 = 225min (3.75h)

效果：
- 间隔 45 分钟 → 继续当前切片（未超 short_threshold）
- 间隔 4 小时 → 创建新切片（超 long_threshold）
```

**场景 2：低频用户（偶尔使用者）**
```
用户 B 的对话历史：
- 1月1日 20:00
- 1月3日 15:00  (间隔 43h)
- 1月3日 15:30  (间隔 30min)
- 1月8日 19:00  (间隔 123.5h)
- 1月8日 21:00  (间隔 2h)
...

统计结果（过滤 > 7天的间隔）：
- p25 = 1h, median = 8h, p75 = 48h
- frequency_tier = "low"

动态阈值：
- short_gap_threshold = max(1h, 0.5h) = 1h
- long_gap_threshold = min(48h * 1.5, 24h) = 24h

效果：
- 间隔 2 小时 → 继续当前切片（未超 short_threshold，但有一定时间信号）
- 间隔 30 小时 → 创建新切片（超 long_threshold）
```

**场景 3：跨睡眠边界**
```
用户 C 的时间画像：
- sleep_window = (23, 7)  # 晚 11 点睡，早 7 点起

对话时间轴：
- 1月1日 22:30  (用户："今天有点焦虑")
- 1月2日 09:00  (用户："早上好")

判定：
- 间隔 10.5 小时
- is_sleep_boundary() = True （跨越 23:00-07:00）
- 创建新切片，原因："sleep_boundary"，置信度 0.95

机器人响应：
"早上好！新的一天开始了。昨晚提到的焦虑，今天感觉怎么样？"
（而非直接继续昨晚的话题）
```

### 2.3 L2: 会话摘要层

#### 2.3.1 多切片摘要存储

```python
class SliceSummary(Base):
    """每个切片完成后的摘要。"""
    __tablename__ = "slice_summaries"

    slice_id = Column(String, ForeignKey("conversation_slices.id"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # 摘要内容
    summary_text = Column(Text)  # 自然语言摘要
    key_points = Column(JSON)  # 关键点列表: ["焦虑源于工作压力", "尝试了呼吸练习"]
    topics = Column(JSON)  # 主题标签: ["anxiety", "work_stress"]

    # 时效性
    created_at = Column(DateTime, default=utcnow)
    relevance_score = Column(Float, default=1.0)  # 随时间衰减

    # 索引/检索
    summary_embedding = Column(JSON)  # 用于语义检索（可选）
```

#### 2.3.2 时间衰减机制

```python
def calculate_relevance_score(summary: SliceSummary) -> float:
    """
    时间衰减函数：越旧的摘要权重越低。

    - 1 天内: 1.0
    - 3 天内: 0.8
    - 7 天内: 0.6
    - 30 天内: 0.3
    - 30 天外: 0.1
    """
    days_ago = (utcnow() - summary.created_at).days

    if days_ago <= 1:
        return 1.0
    elif days_ago <= 3:
        return 0.8
    elif days_ago <= 7:
        return 0.6
    elif days_ago <= 30:
        return 0.3
    else:
        return 0.1
```

#### 2.3.3 相关性检索

```python
def retrieve_relevant_summaries(
    session: Session,
    user_id: str,
    current_message: str,
    max_summaries: int = 3,
) -> list[SliceSummary]:
    """
    根据当前消息检索相关历史摘要。

    策略：
    1. 时间衰减排序 + 主题匹配
    2. 优先返回最近 + 最相关的摘要
    """
    # 简化版：按时间取最近 N 个
    # 完整版：可加入语义相似度检索
    summaries = (
        session.query(SliceSummary)
        .filter(SliceSummary.user_id == user_id)
        .order_by(SliceSummary.created_at.desc())
        .limit(max_summaries * 2)  # 多取一些候选
        .all()
    )

    # 计算相关性得分（简化：时间衰减 * 主题匹配）
    scored = []
    for summary in summaries:
        time_score = calculate_relevance_score(summary)
        # 主题匹配（简化：关键词重叠）
        topic_score = calculate_topic_match(summary.topics, current_message)
        final_score = time_score * 0.5 + topic_score * 0.5
        scored.append((summary, final_score))

    # 返回 top-k
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s, score in scored[:max_summaries]]
```

### 2.4 L3: 持久记忆层

保持现有机制不变：
- `UserProfile` - 用户画像
- `AssessmentRecord` - 量表记录
- `CheckinRecord` - 打卡记录
- `ExerciseRecord` - 练习记录
- `RiskEvent` - 风险事件
- `ProfileBelief` - 画像信念（feat/profile-memory）

这一层是**结构化数据**，不受对话切片影响，始终可见。

### 2.5 完整上下文构建流程

```python
def build_context_v2(
    session: Session,
    user_id: str,
    session_id: str,
    user_message: str,
    language: str,
) -> dict:
    """
    三层上下文构建（新版）。

    返回结构：
    {
        "current_slice_context": [...],      # L1: 当前切片的逐字对话
        "relevant_summaries": [...],         # L2: 相关历史摘要
        "persistent_memory": {...},          # L3: 结构化记录
        "slice_metadata": {...},             # 当前切片元信息
    }
    """
    # L1: 对话切片
    current_slice = get_or_create_slice(session, user_id, session_id, user_message)
    slice_context = build_slice_context(session, current_slice.id, max_turns=20)

    # L2: 相关历史摘要
    relevant_summaries = retrieve_relevant_summaries(
        session, user_id, user_message, max_summaries=3
    )
    summary_texts = [
        f"[{s.created_at.strftime('%m月%d日')}] {s.summary_text}"
        for s in relevant_summaries
    ]

    # L3: 持久记忆（复用现有）
    persistent_memory = {
        "profile": render_profile_block(session, user_id, language),
        "assessments": AssessmentMemoryModule().render(session, user_id, language=language, char_budget=200),
        "checkins": CheckinMemoryModule().render(session, user_id, language=language, char_budget=200),
        "exercises": ExerciseMemoryModule().render(session, user_id, language=language, char_budget=150),
        "recent_risk": get_recent_risk_level(session, user_id),
    }

    return {
        "current_slice_context": slice_context,
        "relevant_summaries": summary_texts,
        "persistent_memory": persistent_memory,
        "slice_metadata": {
            "slice_id": current_slice.id,
            "topic": current_slice.primary_topic,
            "turn_count": current_slice.turn_count,
            "is_new_slice": current_slice.turn_count == 0,
        },
    }
```

## 三、Prompt 设计

### 3.1 系统 Prompt 调整

```
你是一位心理支持AI，正在与用户进行对话。以下是你需要的上下文信息：

【本次对话】（第 {turn_count} 轮）
{current_slice_context}

【相关历史】
{relevant_summaries}

【用户档案】
{persistent_memory}

---

注意事项：
1. "本次对话"是用户当前正在讨论的话题，优先参考这部分内容。
2. "相关历史"是之前讨论过的相关内容，作为背景参考，不要混淆时间线。
3. 如果用户明确换了话题，不要强行联系旧话题。
4. 安全信号（风险事件）始终有效，即使是新话题也要警惕。
```

### 3.2 切片边界的自然过渡

当检测到新切片时，机器人应自然衔接：

```python
def generate_transition_hint(
    new_slice: ConversationSlice,
    last_summary: SliceSummary | None,
) -> str:
    """生成切片过渡提示词（注入到 system prompt）。"""
    if new_slice.boundary_reason == "time_gap_24h":
        return "用户距离上次对话已经过去一天，可能会开启新话题，但也可以自然询问'最近怎么样'。"
    elif new_slice.boundary_reason == "explicit_switch":
        return "用户明确表示想换话题，尊重用户意愿，不要继续纠缠旧话题。"
    elif new_slice.boundary_reason == "topic_shift":
        return "检测到话题转变，自然跟随用户的新方向。"
    else:
        return ""
```

## 四、实施路线图

### Phase 1: 基础架构（1-2周）
- [ ] 实现 `ConversationSlice` 数据模型
- [ ] 实现 `SliceSummary` 数据模型
- [ ] **实现 `UserTimeProfile` 数据模型**
- [ ] 数据库迁移
- [ ] 实现基础切片检测逻辑（显式切换信号优先）

### Phase 2: 用户时间画像（1周）
- [ ] 实现 `calculate_time_profile()` - 分析用户历史计算阈值
- [ ] 实现 `get_user_time_profile()` - 画像缓存与更新策略
- [ ] 实现 `is_sleep_boundary()` - 睡眠边界检测
- [ ] 单元测试：不同用户类型的阈值计算
- [ ] 性能测试：画像计算耗时（确保 < 100ms）

### Phase 3: 切片智能检测（1周）
- [ ] 集成时间画像到 `should_create_new_slice()`
- [ ] 实现主题提取（简化版：关键词 + LLM 分类）
- [ ] 实现主题相似度计算
- [ ] 实现综合切片判定策略（时间 + 主题 + 结构化信号）
- [ ] 单元测试覆盖

### Phase 4: 上下文重构（1-2周）
- [ ] 重构 `build_memory_snapshot` → `build_context_v2`
- [ ] 调整 `GraphState` 结构（加入 slice_id / slice_metadata）
- [ ] 修改 prompt 模板（区分"本次对话"/"相关历史"）
- [ ] 兼容性测试（确保旧数据可读）

### Phase 5: 摘要与检索（1周）
- [ ] 实现切片摘要生成（LLM 总结）
- [ ] 实现时间衰减机制
- [ ] 实现相关性检索（基于主题匹配）
- [ ] （可选）语义向量检索

### Phase 6: 测试与优化（1-2周）
- [ ] E2E 测试：多轮对话 + 话题切换场景
- [ ] 人工评估：切片准确性（标注 100 个真实对话）
- [ ] A/B 测试：不同阈值策略的效果对比
- [ ] 性能优化：数据库查询 + 缓存
- [ ] 调整阈值参数与权重

## 五、技术细节

### 5.1 数据库索引

```sql
-- 切片查询优化
CREATE INDEX idx_slices_user_status ON conversation_slices(user_id, status, updated_at DESC);
CREATE INDEX idx_slices_session ON conversation_slices(session_id, created_at DESC);

-- 摘要检索优化
CREATE INDEX idx_summaries_user_time ON slice_summaries(user_id, created_at DESC);
```

### 5.2 性能考虑

- **切片检测频率**：每次用户消息前检测（开销可控）
- **摘要生成时机**：切片完成后异步生成（不阻塞响应）
- **历史加载上限**：
  - 当前切片：最多 20 轮
  - 历史摘要：最多 3 个
  - 结构化记录：保持现有逻辑

### 5.3 向后兼容

旧数据迁移策略：
```python
def migrate_legacy_sessions():
    """为现有会话创建默认切片。"""
    sessions = Session.query(ConversationSession).filter(
        ~ConversationSession.messages.any(Message.slice_id != None)
    ).all()

    for sess in sessions:
        # 为整个 session 创建一个大切片
        slice = ConversationSlice(
            id=f"slice-legacy-{sess.id}",
            session_id=sess.id,
            user_id=sess.user_id,
            primary_topic="legacy",
            boundary_reason="migration",
            status="completed",
        )
        Session.add(slice)

        # 关联所有消息
        Session.query(Message).filter_by(session_id=sess.id).update(
            {"slice_id": slice.id}
        )

    Session.commit()
```

### 5.4 客户端时间注入最佳实践

**为什么需要客户端时间**：
服务器时间（UTC）可能与用户实际所在时区不符，导致：
- 睡眠边界判断错误（服务器 UTC 2:00 可能是用户本地 10:00）
- 无法识别用户的真实作息规律

**推荐方案**：
```javascript
// 前端发送消息时注入客户端时间信息
async function sendMessage(message) {
    const payload = {
        user_id: userId,
        message: message,
        session_id: sessionId,
        client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,  // "Asia/Shanghai"
        client_local_time: new Date().toISOString(),  // ISO 8601 格式
    };

    return await fetch('/v1/conversations/respond', {
        method: 'POST',
        body: JSON.stringify(payload),
    });
}
```

**后端处理**：
```python
from datetime import datetime
import pytz

def get_user_local_time(request: ConversationRequest) -> datetime:
    """
    获取用户本地时间（优先用客户端注入，否则用服务器时间）。
    """
    if request.client_local_time:
        # 使用客户端提供的时间
        return request.client_local_time
    elif request.client_timezone:
        # 服务器时间转换到客户端时区
        tz = pytz.timezone(request.client_timezone)
        return datetime.now(tz)
    else:
        # Fallback: 假设 UTC+8（中国时区）
        return datetime.now(pytz.timezone('Asia/Shanghai'))


def is_sleep_boundary_v2(
    last_time: datetime,
    current_time: datetime,
    sleep_window: tuple[int, int],
    timezone: str = "Asia/Shanghai",
) -> bool:
    """
    改进版睡眠边界判断（支持时区）。
    """
    # 转换到用户本地时区
    tz = pytz.timezone(timezone)
    last_local = last_time.astimezone(tz)
    curr_local = current_time.astimezone(tz)

    sleep_start, sleep_end = sleep_window

    hours_gap = (curr_local - last_local).total_seconds() / 3600
    if hours_gap < 6:
        return False

    last_hour = last_local.hour
    curr_hour = curr_local.hour

    if sleep_start > sleep_end:  # 跨日睡眠
        last_in_sleep = last_hour >= sleep_start or last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end and curr_hour < sleep_start
        return last_in_sleep and curr_after_sleep
    else:  # 同日睡眠（午休）
        last_in_sleep = sleep_start <= last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end
        return last_in_sleep and curr_after_sleep
```

**隐私保护**：
- 只存储时区信息（如 "Asia/Shanghai"），不存储精确经纬度
- 客户端时间仅用于切片判断，不持久化到数据库
- 用户可选关闭时间注入（使用服务器时间 fallback）

## 六、预期效果

### 6.1 解决的问题
✅ **上下文污染**：新话题不会被旧话题干扰\
✅ **记忆负载**：通过分层 + 衰减，控制 prompt 长度\
✅ **话题追踪**：用户回到旧话题时，相关历史会被检索出来\
✅ **用户体验**：机器人能识别"换话题"，不会尴尬地继续旧话题\

### 6.2 保持的能力
✅ **长期记忆**：结构化记录（L3）始终可见\
✅ **安全连续性**：风险事件跨会话追踪不受影响\
✅ **画像演化**：用户画像继续跨会话累积\

### 6.3 可测量指标
- **切片准确率**：人工标注 100 个真实对话，评估切片边界准确性
- **上下文相关性**：LLM 评估每轮回复的上下文使用是否合理
- **Prompt 长度**：平均 token 数是否控制在合理范围
- **用户满意度**：话题切换场景的用户反馈

## 七、风险与缓解

### 风险 1：过度切片
**现象**：正常对话中途被错误切片，丢失上下文\
**缓解**：
- 保守阈值（time > 2h 才考虑，显式切换优先）
- 切片后保留"上一切片摘要"作为背景

### 风险 2：摘要质量
**现象**：LLM 生成的摘要丢失关键信息\
**缓解**：
- 摘要模板：强制包含"主题、情绪、关键事件"
- Fallback：摘要失败时，保留原始消息片段

### 风险 3：实施复杂度
**现象**：改动涉及核心流程，风险较高\
**缓解**：
- 分阶段实施（先只做切片检测，不改 prompt）
- Feature flag 控制（`ENABLE_CONTEXT_SLICING`）
- 充分测试 + 灰度发布

## 八、替代方案对比

### 方案 A：固定时间窗口
**做法**：只看最近 N 小时的对话，超过就忽略\
**缺点**：丢失长期记忆，无法处理"几天后回来继续聊"的场景\

### 方案 B：全量上下文 + Token 预算
**做法**：所有历史都塞进去，超预算就截断\
**缺点**：无法保证关键信息被保留，成本高昂\

### 方案 C：纯语义检索
**做法**：每轮都用 embedding 检索相关历史\
**缺点**：忽略时间维度，冷启动慢，成本高\

**本方案优势**：结合了时间、主题、结构化信号，兼顾效果和成本。

---

## 附录：代码示例

### A1. 切片检测 Hook

```python
# src/psych_support_bot/services/slice_manager.py

class SliceManager:
    def get_or_create_slice(
        self,
        session: Session,
        user_id: str,
        session_id: str,
        user_message: str,
    ) -> ConversationSlice:
        """
        获取或创建当前对话切片。

        核心逻辑：
        1. 查询该 session 下的最新 active 切片
        2. 判断是否需要新建切片
        3. 返回切片 ID
        """
        # 查询最新活跃切片
        last_slice = (
            session.query(ConversationSlice)
            .filter_by(user_id=user_id, session_id=session_id, status="active")
            .order_by(ConversationSlice.updated_at.desc())
            .first()
        )

        # 判断是否切片
        should_slice, reason, confidence = should_create_new_slice(
            last_slice, user_message, session
        )

        if should_slice:
            # 关闭旧切片
            if last_slice:
                last_slice.status = "completed"
                last_slice.end_message_id = get_last_message_id(session, last_slice.id)
                session.commit()

                # 异步生成摘要
                schedule_summary_generation(last_slice.id)

            # 创建新切片
            new_slice = ConversationSlice(
                id=f"slice-{uuid4()}",
                session_id=session_id,
                user_id=user_id,
                primary_topic=extract_primary_topic(user_message),
                boundary_reason=reason,
                boundary_confidence=confidence,
                turn_count=0,
            )
            session.add(new_slice)
            session.commit()

            logger.info(
                f"Created new slice {new_slice.id} for user {user_id}, "
                f"reason={reason}, confidence={confidence:.2f}"
            )
            return new_slice
        else:
            # 复用现有切片
            last_slice.turn_count += 1
            last_slice.updated_at = utcnow()
            session.commit()
            return last_slice
```

### A2. 集成到现有流程

```python
# src/psych_support_bot/services/conversation.py

from psych_support_bot.services.slice_manager import SliceManager

class ConversationService:
    def __init__(self):
        self.slice_manager = SliceManager()

    def _build_state(
        self,
        payload: ConversationRequest,
        session: Session
    ) -> tuple[GraphState, str, str]:
        """修改：集成切片检测。"""
        session_id = payload.session_id or str(uuid4())

        # ===== 新增：切片检测 =====
        current_slice = self.slice_manager.get_or_create_slice(
            session,
            payload.user_id,
            session_id,
            payload.message,
        )

        # 构建切片内上下文（替代原有的 recent_history）
        slice_context = build_slice_context(session, current_slice.id, max_turns=20)

        # 检索相关历史摘要
        relevant_summaries = retrieve_relevant_summaries(
            session,
            payload.user_id,
            payload.message,
            max_summaries=3,
        )

        # ===== 修改：memory_snapshot 包含切片信息 =====
        memory_summary = build_memory_snapshot_v2(
            session,
            payload.user_id,
            language=expected_language,
            slice_context=slice_context,
            relevant_summaries=relevant_summaries,
            user_message=payload.message,
            recent_risk_level=recent_risk_level,
        )

        # ... 其余逻辑不变 ...

        state: GraphState = {
            # ... 现有字段 ...
            "slice_id": current_slice.id,  # 新增
            "slice_metadata": {  # 新增
                "is_new_slice": current_slice.turn_count == 1,
                "boundary_reason": current_slice.boundary_reason,
                "primary_topic": current_slice.primary_topic,
            },
            "recent_history": slice_context,  # 修改：改用切片内上下文
        }

        return state, session_id, expected_language
```

---

**方案总结**：通过三层架构（切片 + 摘要 + 持久记忆）+ 智能切片检测，实现上下文的动态管理，既保证长期记忆，又避免上下文污染，同时保持对安全相关信息的持续追踪。

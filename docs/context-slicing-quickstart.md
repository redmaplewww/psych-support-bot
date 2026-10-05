# 上下文切片系统快速启动指南

## 什么是上下文切片？

上下文切片系统自动将一个长对话按话题切分成多个"切片"，每个切片是一个完整的话题对话。

**问题**：
```
用户：我最近压力很大（话题1：压力）
机器人：...（回复压力）
用户：主要是工作压力（继续话题1）
机器人：...
用户：好的谢谢。换个话题，我想聊睡眠（话题2：睡眠）
机器人：...（如果没有切片，可能还在谈压力）
```

**解决**：
- ✅ 自动检测"换个话题"，创建新切片
- ✅ 机器人只看当前切片的上下文，不会混淆话题
- ✅ 用户离开12小时后回来，自动创建新切片

---

## 快速启动（3步）

### 1. 启用切片系统（.env，三级灰度）

```bash
ENABLE_CONTEXT_SLICING=true           # P3 切片本体：话题边界 + 上下文隔离
ENABLE_SLICE_BASED_EXTRACTION=true    # P4 切片完成联动：摘要 + 主题继承 + 提取溯源
ENABLE_PROFILE_SLICE_RETRIEVAL=true   # P5 画像驱动检索：【相关历史】背景块注入
```
三者默认全关；P4/P5 依次依赖上一层（只开 P5 而无 P4 摘要产出时静默跳过）。
P4 在每次话题边界轮多一次摘要 LLM 调用（fail-open，LLM 不可用时降级确定性拼接）。

### 2. 重启服务

```bash
# 开发环境
uvicorn psych_support_bot.app:app --reload

# 生产环境
systemctl restart psych-bot
```

### 3. 验证

查看日志，应该看到：
```
INFO Context slicing enabled: slice_id=slice-xxx, is_new_slice=True, reason=first_message
```

---

## 工作原理

### 切片触发条件（优先级从高到低）

1. **显式切换**（置信度 0.9）
   - 关键词："换个话题"、"聊点别的"、"不想说这个"、"我想问"
   - 示例：用户说"好的谢谢。换个话题，我想聊睡眠"

2. **睡眠边界**（置信度 0.95）
   - 间隔 > 6小时 + 跨越用户睡眠时段（默认23点-7点）
   - 示例：用户23:30说"我先睡了"，第二天8:00回来

3. **长时间间隔**（置信度 0.9）
   - 超过用户的 long_gap_threshold
   - 高频用户：3小时
   - 中频用户：24小时
   - 低频用户：24小时

4. **告别后重连**（置信度 0.85）
   - 上轮消息包含："谢谢"、"再见"、"我先去"
   - 示例：用户说"好的谢谢，我先去忙了"，1小时后回来

### 动态时间阈值

系统自动分析用户的对话频率，调整阈值：

| 用户类型 | median 间隔 | short_gap | long_gap | 说明 |
|---------|------------|-----------|----------|------|
| 高频用户 | < 2h | 0.5h | 3h | 每天多次对话 |
| 中频用户 | 2h - 12h | 2h | 24h | 每天1-2次 |
| 低频用户 | ≥ 12h | 12h | 24h | 每周几次 |
| 新用户 | 未知 | 1h | 12h | 保守默认值 |

---

## 监控与调试

### 查看用户的切片

```python
from psych_support_bot.infra.db.models import ConversationSlice
from psych_support_bot.infra.db.session import get_db_session

session = next(get_db_session())
slices = session.query(ConversationSlice).filter_by(user_id="user-123").all()

for s in slices:
    print(f"切片 {s.id}: {s.boundary_reason}, 轮数={s.turn_count}, 状态={s.status}")
```

### 查看用户时间画像

```python
from psych_support_bot.services.time_profile import get_user_time_profile

profile = get_user_time_profile(session, "user-123")
print(f"频率类型: {profile['frequency_tier']}")
print(f"短间隔阈值: {profile['short_gap_threshold']} 小时")
print(f"长间隔阈值: {profile['long_gap_threshold']} 小时")
```

### 日志关键词

搜索日志中的关键词：
```bash
# 切片创建
grep "Context slicing enabled" logs/app.log

# 切片边界
grep "boundary_reason" logs/app.log

# 时间画像更新
grep "calculate_time_profile" logs/app.log

# P5 检索命中（画像驱动相关历史）
grep "Slice retrieval" logs/app.log
```

---

## 常见问题

### Q1: 切片太频繁，用户每次都要重新解释背景？

**A**: 降低敏感度（未来 Phase 4 实现）：
```python
# 调整阈值倍数
settings.slice_time_multiplier = 1.5  # 默认 1.0
```

### Q2: 切片不够频繁，话题混在一起？

**A**: 提高敏感度：
```python
settings.slice_time_multiplier = 0.7  # 更激进
```

### Q3: 如何回退到旧系统？

**A**: 关闭 Feature Flag：
```bash
ENABLE_CONTEXT_SLICING=false
```
重启服务即可，数据不会丢失。

### Q4: 旧数据怎么办？

**A**: 旧消息的 `slice_id` 为 NULL，不影响读取。新系统自动处理。

### Q5: 性能影响？

**A**:
- 切片检测：~10ms（基于规则和关键词）
- 时间画像计算：~50ms（首次）/ ~5ms（缓存）
- 切片上下文构建：~20ms（查询 + 格式化）
- **总增量**：~80ms（首次）/ ~35ms（缓存后）

---

## 演示

运行演示脚本查看切片效果：

```bash
python scripts/demo_context_slicing.py
```

输出示例：
```
场景 1: 首次对话
✓ 切片已创建:
  - slice_id: slice-xxx
  - boundary_reason: first_message
  - confidence: 1.0

场景 2: 继续同话题
✓ 是否为同一切片: 是

场景 3: 用户说'换个话题'
✓ 新切片已创建:
  - boundary_reason: explicit_switch
  - confidence: 0.9
  - 旧切片 status: completed
```

P4/P5 联动端到端演示（溯源 → 摘要 → 检索注入，离线确定性路径）：

```bash
python scripts/demo_slice_profile_linkage.py
```

---

## 下一步

- 启用切片系统（`ENABLE_CONTEXT_SLICING=true`）
- 小流量测试（10% 用户）
- 观察日志和指标
- 根据反馈调整参数
- 全量发布

---

## 相关文档

- [完整设计方案](./context-management-design.md)
- [Phase 1 总结](./context-slicing-phase1-summary.md)
- [Phase 3 总结](./context-slicing-phase3-summary.md)
- [Phase 4/5 总结（切片×画像双向联动）](./context-slicing-phase45-summary.md)
- [画像联动设计](./profile-slice-integration.md)

---

**问题反馈**: 在项目 Issues 中提交
**更新时间**: 2026-09-13

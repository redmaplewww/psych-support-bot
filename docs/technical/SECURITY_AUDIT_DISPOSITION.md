# 安全审计处置记录

## Mimosa deep scan `scan-2026-09-11T07-45-42.011Z-c02b1f85311d`

seal: `sha256:24cbfd98bc885d7a691e3d2ec8400873ed2fa4e8ca84b2b2b43e1ab4e0cb54d0`。
静态分析（无运行时验证），两条 business-logic 候选经人工核查处置如下。

### F1（MEDIUM）「敏感操作未观察到角色或权限检查」— 确认为真，已修

- 位置：`api/routes/conversation.py` `GET /{session_id}/messages`。
- 核查结论：无 require_auth、查询不带归属条件；`messages` 表无 user_id 列，
  归属只在 `sessions` 表。AUTH_ENABLED=true 部署下未认证可读任意会话对话原文。
- 连带发现：`respond`/`respond_stream` 接受客户端自带 session_id 且不校验归属，
  历史原文进 LLM 上下文，可借"复述上文"外泄——比直读更隐蔽，一并修复。
- 修复：commit `fcd6130`。require_auth + 仓库层 `session_belongs_to`
  （sessions.user_id 绑定），越权一律 404（防会话枚举）；游客模式行为不变。
  回归测试 `tests/unit/test_conversation_ownership.py`（8 例）。

### F2（HIGH）「资源标识来自请求，未观察到所有权绑定」— 误报（by design）

- 位置：`api/routes/exercises.py` `GET /{exercise_tag}/intro`。
- 核查结论：`exercise_tag` 是硬编码练习内容目录的字典键
  （`ai/tools/exercises.py:get_exercise_by_tag`），无 DB、无 user_id、
  响应不含任何用户数据；查无此键 404。不存在"未被所有权绑定的用户资源"。
  对照：同文件 `/records/{record_id}` 对真实用户资源有正确绑定
  （`exercise_repositories.get_exercise_record_by_id` 双条件过滤）。
- 边界记录：若未来给 intro 加个性化内容（如练习进度），必须改用
  `/records` 同款模式（request_user_id + 仓库层 user_id 过滤）。

## 遗留

- 依赖 advisory：扫描命中 7 包 / 24 条离线通告，产物无逐包明细，待有网
  环境对 `uv.lock` 逐条核对（低优先）。
- 测试顺序耦合教训：monkeypatch service **实例**属性会在 teardown 把类方法
  固化为实例属性，遮蔽后续测试的类级补丁——服务 mock 一律打类
  （见 test_conversation_ownership.py 注释）。

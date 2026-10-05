"""画像系统常量：集中管理所有阈值和参数。

设计原则：
- 所有阈值有名字和注释，禁止魔法数字散落在代码中
- 阈值按功能分组（置信度/渲染/时间/数量）
- 改动阈值时只需改这一处，全局生效
"""

# ── 置信度阈值 ──────────────────────────────────────────────────────

# 基础信号阈值：低于此值的信念不参与任何渲染/检索/好奇心判断。
# 对应约 1 次支持证据（0.3 初始 + SUPPORT_GAIN 0.15 ≈ 0.45）。
PROFILE_SIGNAL_THRESHOLD = 0.4

# 低置信度模式阈值：高于此值但低于渲染阈值的 D3/D5 信念，
# 触发好奇心注入（"我隐约感觉到一些模式"）。
LOW_CONFIDENCE_PATTERN_FLOOR = 0.35

# 低置信度模式上限：高于此值的信念已过渲染水位，不再需要好奇心。
LOW_CONFIDENCE_PATTERN_CEIL = 0.5

# L4 渲染水位：L4 信念置信度 >= 此值才进入提示词。
# 约 2 次跨轮证据（0.4 + 0.15 = 0.55）。
L4_RENDER_THRESHOLD = 0.55

# 质询候选水位：>= 此值且跨会话证据 >= 2 才允许被质询。
L4_QUESTION_THRESHOLD = 0.7

# 置信度上限。
CONFIDENCE_CEILING = 0.95

# 每次支持证据的增益。
SUPPORT_GAIN = 0.15

# ── 好奇心注入 ──────────────────────────────────────────────────────

# D6 语气指令注入的最低置信度。
D6_TONE_THRESHOLD = PROFILE_SIGNAL_THRESHOLD

# D5 位置摘要的最低置信度。
D5_POSITION_THRESHOLD = PROFILE_SIGNAL_THRESHOLD

# 好奇心信号的最低置信度（needs_clarification）。
CURIOSITY_SIGNAL_THRESHOLD = PROFILE_SIGNAL_THRESHOLD

# 低置信度模式信号的置信度范围。
CURIOSITY_LOW_CONF_RANGE = (LOW_CONFIDENCE_PATTERN_FLOOR, LOW_CONFIDENCE_PATTERN_CEIL)

# ── 时间常量 ────────────────────────────────────────────────────────

# 信念活跃度半衰期：60 天无新证据，活跃度减半。
ACTIVITY_HALF_LIFE_DAYS = 60.0

# 时间标注分段（天）。
TIME_LABEL_ACTIVE_DAYS = 7  # 7天内标注"活跃"
TIME_LABEL_WEEKLY_DAYS = 30  # 30天内标注"N周前"
TIME_LABEL_MONTHLY_DAYS = 90  # 90天内标注"N月前"，超过不标注

# 生活事件过期天数。
LIFE_EVENT_EXPIRY_DAYS = 30

# 生活事件初始置信度（单次出现，低于通用信号阈值）。
LIFE_EVENT_INITIAL_CONFIDENCE = 0.3

# 目标初始置信度（用户显式表达，高于生活事件）。
GOAL_INITIAL_CONFIDENCE = 0.5

# ── 行为信号 ────────────────────────────────────────────────────────

# VAD 停顿检测：说话中沉默超过此值计为一次停顿（毫秒）。
VAD_PAUSE_THRESHOLD_MS = 500

# VAD 过短发言：说话时长低于此值且有文字内容，触发"说得很快"信号（毫秒）。
VAD_BRIEF_SPEECH_MS = 2000

# VAD 多次停顿：停顿次数 >= 此值且说话时长 > 5秒，触发"停了好几次"信号。
VAD_HESITATION_PAUSE_COUNT = 3
VAD_HESITATION_MIN_SPEECH_MS = 5000

# 回复延迟：超过此值视为有意义的停顿（秒）。
RESPONSE_DELAY_MIN_SECONDS = 300  # 5 分钟
RESPONSE_DELAY_MAX_SECONDS = 3600  # 60 分钟

# 消息长度突降：比近期均值短于此比例视为异常。
MESSAGE_LENGTH_DROP_RATIO = 0.4

# 深夜来访默认窗口（无 UserTimeProfile 时的回退值）。
DEFAULT_SLEEP_WINDOW = (23, 5)

# ── 数量限制 ────────────────────────────────────────────────────────

# 每轮最大 claim 数。
MAX_CLAIMS_PER_TURN = 3

# 每条信念最大证据引用数。
MAX_EVIDENCE_REFS = 8

# D8 负记忆最大渲染数。
MAX_D8_ITEMS = 5

# 生活事件最大渲染数。
MAX_LIFE_EVENTS = 3

# K3 综合引擎最大输入信念数。
K3_MAX_BELIEFS = 30

# K3 最大切片摘要数。
K3_MAX_SUMMARIES = 5

# 打卡趋势检测的最小天数。
CHECKIN_TREND_MIN_DAYS = 3

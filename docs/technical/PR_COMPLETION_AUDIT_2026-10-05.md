# 开放 PR 与本地提交核对（2026-10-05）

核对目标：[redmaplewww/psych-support-bot 开放 PR](https://github.com/redmaplewww/psych-support-bot/pulls)。
读取了全部 11 个开放 PR 的描述、评论、review 与提交尖端，并刷新 origin/fork。
开始时本地分支为 feat/privacy-identity，HEAD=e1b53ce；上游 main=255169f。
本轮交付分支为 codex/complete-open-prs，以现有本地完整功能链为基础。

## 逐项状态

“已覆盖”指本轮分支包含该 PR 的功能，不表示已合入上游 main。

| PR | 内容 | 核对及处置 |
| --- | --- | --- |
| [#2](https://github.com/redmaplewww/psych-support-bot/pull/2) | 评估期间非数字消息 | 旧修复已进入功能链；后续由问卷状态机细化为作答、情绪暂停、危机优先。相关集成回归覆盖。 |
| [#3](https://github.com/redmaplewww/psych-support-bot/pull/3) | 前端四个 Tab | 已覆盖；后续问卷、记录、趋势和语音功能叠加在同一前端上。原作者评论确认此前已合入 dev。 |
| [#21](https://github.com/redmaplewww/psych-support-bot/pull/21) | grounding 多槽及去重 | 已通过 f0327d2 纳入；原 PR 的 b85ecb2 是另一提交身份，无需再应用相同功能。 |
| [#22](https://github.com/redmaplewww/psych-support-bot/pull/22) | 护栏、Prompt 分层、巡检 | PR 尖端为本地祖先。补齐安全审计中的依赖在线核对，升级命中依赖并复查。 |
| [#23](https://github.com/redmaplewww/psych-support-bot/pull/23) | 知识检索与渲染统一 | PR 尖端为本地祖先；保留其方案 B 的语料组织与门面分工。 |
| [#24](https://github.com/redmaplewww/psych-support-bot/pull/24) | 逐字近史与回复形态 | PR 尖端为本地祖先。本轮补齐会诊专家及同步/流式最终综合的逐字近史传递。 |
| [#25](https://github.com/redmaplewww/psych-support-bot/pull/25) | 连续性意图 | 原四个提交尚未接入。移植最终版本并适配当前流式、练习与切片结构；保留作者 huangjun 归属。分类仅用于状态/日志，依照 PR 最后修订不将标签变成 Prompt 指令。 |
| [#26](https://github.com/redmaplewww/psych-support-bot/pull/26) | 分页问卷、整卷提交 | 已由 #27 及后续链完整覆盖，PR 尖端为本地祖先。 |
| [#27](https://github.com/redmaplewww/psych-support-bot/pull/27) | 语音与可靠性 | PR 尖端为本地祖先。#30 修复 TTS 初始轮代次未初始化造成的收轮 Promise 挂起；真机预滚首词测试、线上 403 身份自愈验证仍未完成。 |
| [#28](https://github.com/redmaplewww/psych-support-bot/pull/28) | 画像、切片及隐私 | 当前 GitHub 尖端 92ed5bf 为本地祖先；包含 #29 的 cherry-pick。现有 6d701ec/e1b53ce 的流式 STT、画像/Beta/隐私工作已经推到 fork，但尚未由该上游 PR 尖端覆盖，本轮交付分支包含它们。 |
| [#29](https://github.com/redmaplewww/psych-support-bot/pull/29) | 画像智能调度 | git cherry 判定原 9fa76fc 的补丁已存在；无需重复应用。#30 补齐固定输出的 8 轮纵向调度契约测试并修复消息排序 flake；真实 LLM 多轮质量/成本/延迟评测和线上一周 gate 核对仍未完成。 |

开放 PR 是累积功能链，不能把每个 PR 相对 main 的整份 diff 重复应用。
#29 描述仍建议等待 #28，但 #28 已含它的 cherry-pick，属于描述滞后。
本轮通过 [#30](https://github.com/redmaplewww/psych-support-bot/pull/30) 提供合并入口，
并更新 #27 描述、通过 #29 评论同步完成证据与未完成项（当前账号无权编辑 #29 描述）。补齐实现仅在 #30 分支，
不表示原 PR 分支已更新或上游已合并；旧 PR 保持开放。

## 冗余 PR 整理

用户最新确认仅清理旧入口 #3、#21、#25；#2 与 #29 均保留，撤回其原关闭建议。
功能由 #30 承接，#25 为适配后的实现，#2/#3/#21 已进入并演进于功能链。
#29 保留为智能调度独立审阅入口；其补丁与 #28 等价，合并前需核对重复应用。
保留 #22 → #23 → #24 → #26 → #27 → #28 → #30 的阶段审阅链。
当前上游 main 仍为 255169f，尚未合入承接功能。

实际权限检查：Echo-Smith 对上游及两个来源 fork 均只有 pull 权限。
关闭 #29 的 API 操作被 GitHub 的 ClosePullRequest 权限检查拒绝；其他四个 PR
也由其他作者创建。browser-skill 实测浏览器仍登录 Echo-Smith，#2/#29 页面没有关闭按钮。
待清理的三个 PR 尚未关闭，来源分支没有删除，已分别发布覆盖关系与关闭建议，
等待有权限的维护者执行；#2/#29 继续保持开放。

- [#2 保留说明](https://github.com/redmaplewww/psych-support-bot/pull/2#issuecomment-5986800108)
- [#3 关闭建议](https://github.com/redmaplewww/psych-support-bot/pull/3#issuecomment-5986800334)
- [#21 关闭建议](https://github.com/redmaplewww/psych-support-bot/pull/21#issuecomment-5986800556)
- [#25 关闭建议](https://github.com/redmaplewww/psych-support-bot/pull/25#issuecomment-5986800788)
- [#29 保留说明](https://github.com/redmaplewww/psych-support-bot/pull/29#issuecomment-5986801007)

未完成验收已迁入独立 issue，#27/#30 描述同步了链接：
[语音真机与线上身份恢复 #31](https://github.com/redmaplewww/psych-support-bot/issues/31)、
[Psy 记忆真实模型纵向评测与一周 gate 核对 #32](https://github.com/redmaplewww/psych-support-bot/issues/32)。
创建跟踪项不代表验收完成；Mirror 保持独立。

## 本轮补齐与修复

1. 连续性分类接入当前状态、流式生成签名及日志；切片历史可供分类。
   危机短路和活跃练习路由保持优先，增加回归覆盖。
2. 会诊专家、非流式综合、流式综合均携带同一逐字近史，使用标准消息角色。
   测试验证两位专家和综合三次调用的消息序列，并验证流式增量无回退。
3. 8 轮固定数据通过真实提取与数据库路径比较：旧调度调用 7 次，新调度 1 次；
   新机制在第 2 轮捕获，保持 L4。危机和关闭记忆时零模型调用、零语义写入。
   模型输出固定，因此该结果是调度契约证据，不是实际模型质量评测。
4. 最近用户消息按 created_at、id 倒序，同时间戳仍按插入顺序稳定返回。
5. TTS 初始状态初始化轮代次，使首次收轮超时与 round_end 能正确完成。
6. 记忆开关在初始和动态状态文案中说明暂停保留数据、独立清除入口。
7. 修复当前 lint 的未使用导入/导入排序及格式检查失败。
8. 对锁文件逐版本完成 OSV 检查及依赖更新：旧锁 97 包中 14 包命中；
   新锁 98 包已知命中 0。详见 [依赖记录](DEPENDENCY_AUDIT_2026-10-05.md)。

## Psy 记忆系统范围

Psy 与 Mirror 保持独立。本 PR 保留 Psy 自身画像/记忆设计、智能调度、上下文切片、
回复上下文注入和隐私生命周期实现，不引入 Mirror 项目依赖。
此前误纳入的 9 份 Mirror RFC/规格文档已从 PR 最终差异中移除，原稿留在本地且不跟踪。
Mirror 的独立工程计划不计入 Psy 的完成项或后续任务。

## 验证记录

- 初始 CI 后端范围：935 passed。
- 补齐后、升级前的全部单元/集成及切片测试：1079 passed。
- 依赖升级后的全部单元/集成及切片测试：1079 passed（217.34 秒）。
- 最后会诊近史补齐的相关回归：45 passed（含新增的同步/流式 2 例）。
- 最终 CI 范围：955 passed（13.59 秒，包含最后补齐的会诊近史测试）。
- 前端模块：32 passed；由首次收轮挂起及隐私文案失败修复后获得。
- 静态风险/检索及 judge 测试：35 passed、3 xfailed。
  三个 xfail 是词表外 depression/sleep/grief 表达的已有纯关键词检索缺口；
  当前生产知识通道还接收 LLM 语义 topics，本轮未将固定 topics 注入冒充词表修复。
- Ruff check、Ruff format --check、git diff --check 通过。
- 升级后存在一个非阻断测试工具提示：Starlette 将逐步弃用 TestClient 的 httpx 后端；
  当前兼容路径测试通过，迁移测试后端不属于运行时漏洞修复。
- 测试使用 /tmp 隔离 SQLite 数据库；全量升级迁移成功，Langfuse 导出关闭。
  真实供应商采样质量、物理麦克风和生产部署未由上述离线测试证明。

## 后续条件与提交边界

- 上游 main 仍等待维护者 review/合并：当前 GitHub 权限为 pull=true、push=false。
  本轮提交通过 fork 分支及上游 PR 交付。
- 真实 LLM 的 B/C 层漂移、线上一周调参和真机语音体验需要对应运行证据，
  不以离线调度测试或旧 PR 描述追认为完成。
- VOICE_DECISIONS 的 D2 限流是已记载的条件性待决事项；D1.1 是扩 worker 的前置工作。
  本轮没有变更部署规模、采样预算或上线配置。

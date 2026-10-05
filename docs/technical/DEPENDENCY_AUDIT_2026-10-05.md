# 依赖漏洞核对（2026-10-05）

已完成 SECURITY_AUDIT_DISPOSITION 中遗留的在线锁文件核对。使用 OSV 官方
[批量查询 API](https://github.com/google/osv.dev/blob/master/docs/api/post-v1-querybatch.md)，
对 uv.lock 的每个 registry 包按 PyPI 名称和精确版本查询；包括运行与开发依赖。

- 升级前：97 个包，14 个包命中，合计 132 条数据库记录。
  GHSA 与 PYSEC 可指向同一漏洞，记录数不等于独立漏洞数。
- 升级后：98 个包，已知漏洞命中 **0**；无未取完的分页。
- 直接依赖在 pyproject.toml 提高最低修复版本；间接依赖通过 uv.lock 固定。
- 保留 OpenAI SDK 2.31.0；采用满足修复要求的较小升级，避免 SDK 3.x 迁移。
- 查询包清单、前后版本、原命中 ID 和锁文件 SHA-256 见
  [机器可读记录](DEPENDENCY_AUDIT_2026-10-05.json)。此结果对应该锁文件与查询日期。

| 包 | 升级前 | 已验证版本 |
| --- | --- | --- |
| anyio | 4.13.0 | 4.14.2 |
| click | 8.3.2 | 8.3.3 |
| idna | 3.11 | 3.15 |
| langchain-core | 1.2.28 | 1.3.3 |
| langchain-openai | 1.1.12 | 1.1.14 |
| langgraph-checkpoint | 4.0.1 | 4.1.1 |
| langgraph-sdk | 0.3.13 | 0.3.15 |
| langsmith | 0.7.30 | 0.8.18 |
| mako | 1.3.10 | 1.3.12 |
| pydantic-settings | 2.13.1 | 2.14.2 |
| pyjwt | 2.13.0 | 2.15.0 |
| pypdf | 6.10.0 | 6.19.0 |
| starlette | 1.0.0 | 1.3.1 |
| urllib3 | 2.6.3 | 2.8.0 |

复核方法：读取锁文件中 source.registry 包，将 package.name、ecosystem=PyPI、
version 发送到 POST https://api.osv.dev/v1/querybatch；按请求顺序核对每个 results，
若出现 next_page_token 须继续查询。仅包名称和版本离开本机，不包含项目代码或用户数据。

PyJWT 的 [options 字典复用通告](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-gvp8-978c-rx2q)
未列 fixed 事件，但 last_affected=2.13.0；升级为 2.15.0 后重新查询无命中。
项目自身的 decode 调用每次构造独立 options 并固定 algorithms、issuer 与 audience。

验证：uv sync --locked 安装通过；升级后的后端与静态安全评估结果见
[PR 核对汇总](PR_COMPLETION_AUDIT_2026-10-05.md)。

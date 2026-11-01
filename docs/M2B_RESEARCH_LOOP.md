# M2-B 最小 Generator–Critic 研究闭环

## 运行

本阶段只运行主信号生成、审查和有限修复，不调度 WQ 回测、不正式提交 Alpha。
优先复用 M2-A：Runtime / Backend / Role / Task / Attempt / strict_json / AgentLedger
均未重构。CLI 默认两个独立名称的可信 Mock Backend，每次执行仍创建新实例。

```powershell
uv run --offline alpha-platform study run --input configs/study-mock.json --ledger study-demo.sqlite3 --scenario repair_pass --output study-demo.json
uv run --offline alpha-platform study show --ledger study-demo.sqlite3 --study-id STUDY_ID
uv run --offline alpha-platform observe study --ledger study-demo.sqlite3 --study-id STUDY_ID --output study-observation.json
uv run --offline alpha-platform agent probe --backend all
```

初始输入含 schema_version、signal_id、sources、policy、evidence_catalog。
示例已提供完整格式；信号身份在同一账本中唯一。相同 signal_id 不可重新开始，已有 Study
不可通过换后端继续投票；再次运行同一演示输入会拒绝，不能自动覆盖历史。
研究默认账本 `agent_tasks.sqlite3`，禁止使用 M1/M1.5 业务数据库。`--db` 不选择研究账本。
输出为现有安全机制保护的 JSON，大小上限 256 KiB，不覆写数据库或其 WAL/SHM。
Mock 演示事实见 `examples/research_loop/repair-demo.json`，不代表真实模型或经济验证。

## 角色与决策

1. Controller 冻结批准资料、角色路由和预算，创建 v1 Generator Task。
2. 复用 Runtime 校验结构化契约、角色、版本、digest、Attempt；执行错误单独记录。
3. 确定性 Validator 检查引用、批准字段、操作符和原 FastExpr 子集。
   提案必须引用至少一条批准资料，表达式字段并集必须等于声明字段。
   不支持的语法显式拒绝，不根据收益高低放宽。
4. 保存不可变 SignalVersion，创建独立 Critic Task。Critic 只得当前正式提案、原始目标、
   批准元数据和证据，没有 Generator 私有会话、草稿、历史工具记录或之前的审查意见。
5. PASS 在所有必要审查锁定后 APPROVED；UNCERTAIN 为 NEEDS_EVIDENCE；不可修复 FAIL
   为 REJECTED。PASS 只表示未发现本轮标准下的阻断缺陷，不证明预测能力。
6. 可修复 FAIL 可以产生 vN+1。仅非 blocking 且类别为 mechanism / expression /
   clarity / evidence_reference 的缺陷允许修复，其余保守拒绝。
7. 新版先独立审查并提交锁定结果，再创建新的 Critic Task，提供正式旧版、正式缺陷和
   Controller 计算的变更字段清单做复验。多个旧失败版本全部逐一复验，不能只看最后一版。
8. 执行失败状态为 EXECUTION_FAILED，不生成研究 FAIL。预算/总时限耗尽为 LIMIT_REACHED；
   调用方取消为 CANCELLED。不会自动重试、切换后端或发起回测。

典型修复闭环需要 5 次调用：生成 v1、审查 v1、修复 v2、独立审查 v2、复验 v1 缺陷。
默认修复上限 1、调用上限 8、单任务 30 秒、整轮 180 秒；可在规定范围内配置。
每任务 max_attempts=1，Agent 调用分配单位不是 token 或金钱费用；所有失败和不支持调用
也消耗已授权额度。证据请求另计，模拟预算完全不接触。

## Context 必要扩展与安全

在原 ContextBuilder 增加可选 workflow 白名单对象，仅用于 Generator repair 或 Critic
regression。对象包含 phase、previous_proposal、review、changes；旧版本必须小于当前版本，
身份一致且旧审查为正式 FAIL。既有无 workflow 的 M2-A Context、digest 与角色行为不变。
Runtime 仍重建 Context 并校验精确 digest，额外私有字段会拒绝。

修复阶段只提供正式提案与正式缺陷，不恢复私有分析。新版独立审查阶段完全不提供 workflow，
只有独立结果锁定之后才创建复验 Context。数据库再次检查批准所需的独立/复验结果。
另修复原 approved_text 对 tuple 不递归的缺口，避免 dataclass 元组字段漏过敏感文字检查。

Backend 接口不接收数据库、Controller 或 Simulation Adapter。Mock 属可信进程内代码，
逻辑隔离测试不是操作系统沙箱；不允许把任意未隔离外部 Agent 当成可执行 Backend。
同一角色路由冻结，无自动品牌投票。新建不同 signal_id 的语义重复研究尚无全局去重，
身份保护不应被描述成能阻止有数据库/CLI 管理权限的人刻意复制研究。

## 最小 Evidence Gateway

仅路由输入中明确批准的结构化 evidence_catalog，不读取文件、WQ、网络或向量数据库。
沿用现有 Finding：Critic 返回 UNCERTAIN，category=evidence_request，description 必须是
精确证据标识符，如 `e:additional`。这不是自由文本检索指令，非法标识符拒绝。
所有资料 ref 使用 `e:` 命名空间，不能与内部审计/任务引用冲突；同名原始资料不可换内容。

Gateway 根据 Controller 的已确认 Critic Task/Attempt、当前版本、provided_evidence scope
和独立预算授权，记录 AVAILABLE / UNAVAILABLE。重复同一任务的同一请求只记费一次，
缺失证据不是已存在的 source。资料标为“批准输入，未独立核验”，不能直接证明经济论断。
即使返回 AVAILABLE，Study 仍 NEEDS_EVIDENCE，本轮不自动继续、不暗中新增审查或改判。

## 持久化与可观测

AgentLedger schema v1 不变。新增 loop_schema v1 管理附加 signal_studies / signal_tasks /
signal_versions / signal_reviews / signal_events / signal_evidence_requests。SQLite 事务负责
调用配额、版本 CAS、结果关联和审计原子性；版本/审查有唯一键与禁更新/删除触发器。
研究结果必须来自当前已确认 Agent result，旧 Attempt 不能覆盖。

Research Summary 使用只读连接和一个一致快照，复用既有 sanitize、引用闭包检查和安全导出。
关联 Study、Version、Review、Task、Attempt、Result、研究审计与批准资料。
记录生成授权、确定性校验、版本创建、审查锁定、修复、证据请求和终态；不合成执行事件。
`observe study` 与 `study show` 同源。不扩展 Replay 节点，不重构 HTML Player，不启动服务。

## 实际能力边界

本机 Codex 已安装、本地认证与 help 参数可观察；宿主物理隔离未验证，execute 保持
UNSUPPORTED。Claude Code 未安装，未自动安装。DSH 仅保留注册槽，无假定协议。
没有真实模型生成/审查成功记录，只有 Mock 端到端与真实本地预检。

无自动恢复、证据追加后续跑、后台 Worker、跨进程租约、分布式服务、Seed Reservoir、
func1/func2、MCP/RAG 或自动真实回测。异常进程退出可能留下 RUNNING 授权，需要人工检查。
Python 协作式超时不等同于可抢占阻塞代码/OS 进程，数据库锁等待也不完全包含于执行超时。
长 Context 或大导出会明确拒绝，不静默删掉证据或弱化隔离。

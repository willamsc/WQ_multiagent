# M2-A Agent Interoperability Foundation

这是多 Agent **兼容基础层**，不是自动研究系统。M1 的模拟工作流、预算、去重、
UNKNOWN 和回放行为保持原样。新增模块位于 `src/alpha_platform/agent_interop/`。

## 分层和使用

`GeneratorRole / CriticRole → AgentRuntime → Backend Adapter`。

- Role 使用独立协议：MainSignalGenerator.generate、IndependentCritic.review。
  Generator 成功输出 MainSignalProposal；Critic 成功输出 ReviewResult。
- Runtime 管固定任务身份、尝试、能力门控、取消/超时、输出验证、结果提交。
- Backend 只得到 AttemptRequest（TaskEnvelope、attempt_id、批准 Context），
  不得到 Controller、Ledger 或 Simulation Adapter 句柄。
- 旧 `agents.py` 是兼容保留的 M1 接口，不将 M2 DTO 塞进旧 AlphaCandidate。

在项目根目录运行两个独立 Mock 任务：

```powershell
uv run --offline alpha-platform agent run --role generator --input configs/agent_generator_mock.json --config configs/agent_backends_mock.json --ledger agent_tasks.sqlite3
uv run --offline alpha-platform agent run --role critic --input configs/agent_critic_mock.json --config configs/agent_backends_mock.json --ledger agent_tasks.sqlite3
uv run --offline alpha-platform agent show --task-id sample-critic-v1 --ledger agent_tasks.sqlite3
```

输入中的 task_id 是不可变逻辑身份，已成功完成的任务不重复执行；改变目标、版本、
Context 或限额必须使用新的 task_id。`--output result.json` 可保存安全摘要，退出码：
0 成功/只读查询，1 输入或账本错误，2 调用未成功（含 UNSUPPORTED/TIMEOUT）。
Task COMPLETED 不表示研究通过；Critic verdict=FAIL 也可以是执行 SUCCEEDED。

可复现演示只把生成方**正式结果**交给 Critic，不包含任何私有会话：

```powershell
uv run --offline python scripts/demo_agent_interop.py
```

结果在 `examples/agent_interop/demo-summary.json`，两个独立 task/attempt/context/result
引用均可追踪。没有信号准入、修复、回测或自动循环。示例只用于离线契约验证。

## 契约与 Context

TaskEnvelope schema v1 固定 task_id、role、Subject(signal_id,signal_version)、
context_version/digest、允许的证据范围、必需能力和资源限额。result_schema 由角色
确定，不能由后端自报更改。每个输出还必须回传本次 attempt_id 和所有绑定字段。

Runtime 拒绝额外/缺失字段、未知枚举、布尔冒充整数、重复 JSON key、NaN/Infinity、
超长输出、外来版本/角色/Context/Attempt、未批准的字段或证据引用。PASS 不得包含
major/blocking 缺陷；FAIL 必须有明确缺陷。结构化校验不证明经济机制成立，也不替代
M1 表达式 Validator 或未来 M2-B 的研究准入。

ContextBuilder 只选 objective、受限 settings、field_metadata、授权 evidence；
Critic 额外得到精确版本的正式 proposal。其他共享源字段（草稿、私有历史、旧意见等）
不进入 Context；正式 proposal 内的未知字段则拒绝。它不读取任意路径、不开放环境、
数据库或工具。字段元数据和引用是调用方批准的输入，不意味着已经平台核实。

Context 用不可变 canonical JSON 保存，digest 覆盖角色、Subject、政策版本、证据范围
与内容。每次执行重新按同一白名单构建验证，即使伪造 Context 有自洽 digest，也不能
夹带私有字段。修改原输入或读取所得副本，不会改变已冻结 Context。

## 配置与扩展

配置仅为 `schema_version=1` 和完整 generator/critic → 注册后端名称映射。
默认 generator=mock、critic=mock_alt。JSON 不能导入 Python 模块、传 shell 命令、
声明隔离已验证，或关闭 Runtime 的基础能力要求。

BackendRegistry 接受可信应用代码注册的工厂，每个 Attempt 新建 Adapter；重用仍存活
的同一个 Adapter 实例会被拒。DSH 仅是保留注册槽，没有 DSH CLI/API 客户端、协议猜测
或执行代码。未来确认身份与隔离能力后，可通过相同 Protocol 实现、测试再注册。

默认必需能力：structured_result、bounded_execution、scoped_context、fresh_session。
可以增加 host_isolation 要求；不满足就 UNSUPPORTED，不静默降级。能力声明是可信
Adapter 的契约，并非对任意恶意 Python 插件的防护。当前真实工具均被明确阻断。

## 尝试、超时和账本

AgentLedger 使用独立 SQLite 文件；显式 initialize 创建版本 1 的 agent_tasks、
agent_attempts、agent_results、agent_events。发现 research_runs 表即拒绝初始化，
不往 M1 schema 追加 Agent 表。旧 M1 CLI 不创建 Agent 账本。

任务内容不可变；当前 attempt 指针、结果和审计在本地事务内写入。只有当前授权的
RUNNING Attempt 可以完成任务；迟到 Attempt 被标 STALE，不能覆写结果。已完成结果
不重复结算；audit 失败会回滚结果与终态。`supersede=True` 是可信调用方显式替换
未完成 Attempt 的库接口，后端不能自行调用。没有自动重试/恢复策略或 Worker lease。

ResourceLimits：超时 (0,300] 秒、最多 1～10 次尝试、输出 256～262144 字节；
max_evidence_requests 目前必须为 0，因为 A 阶段没有证据请求网关。
Agent 的 allocated_attempts 统计**已分配尝试**，包含门控阻断，不是模型账单。
模型 Token/费用没有来源就为 null；Simulation budget 从不读取或扣减。

Runtime 超时后不等待取消抵抗的协程，迟到值被丢弃。cancel_event 返回 CANCELLED；
取消调用方 asyncio Task 会先记 CANCELLED，再按 Python 约定抛出 CancelledError。
这些保障针对可信、可让出事件循环的 Mock/Adapter；不能物理终止任意阻塞 Python
代码或 OS 进程，也不覆盖所有账本锁等待时间。这也是本机真实执行未获启用的原因。

## 真实后端与权限边界

```powershell
uv run --offline alpha-platform agent probe --backend all
uv run --offline alpha-platform agent smoke --backend codex
uv run --offline python scripts/probe_agent_backends.py
```

probe 仅检查本地版本/help/已知认证状态，原始 CLI 输出与 auth 文件不导出。
smoke 目前只返回门控诊断，exit 2；**不执行真实研究**。选择
`configs/agent_backends_external_reserved.json` 也不能绕过隔离门。

本机实际记录见 `examples/agent_interop/backend_probe.json`：Codex CLI 已安装且本地
报告已登录，help 有 output-schema；未测试联网凭证有效性/额度；没有已验证的宿主
隔离器。Claude Code 已删除、未在 PATH 找到，本轮未安装。两者真实任务均未运行。

CodexBackend/ClaudeCodeBackend 是预检 + fail-closed 兼容入口，并非可用的模型调用
实现；execute 不会启动进程。未来仍需完成可验证的工具/网络/文件/凭证隔离、实际
CLI 启动参数及输出映射测试，再启用真实后端。cwd、Prompt、Schema、只读写保护
不能证明 Critic 读不到 Generator 私有资料。不得通过配置声称生产级隔离。

Mock 是可信进程内代码：逻辑白名单和越权请求测试通过，**不等于 OS 物理隔离**。
项目源代码权限与“研究 Agent”权限严格是两回事。本轮没有宿主 sandbox/ACL 改造。

Codex 参考：[官方非交互说明](https://learn.chatgpt.com/docs/non-interactive-mode)。实际
安装版本/help 以预检记录为准；不根据文档臆造 Claude Code 或 DSH 的命令。

## 审计与安全

`observability/agent_summary.py` 用只读一致快照导出独立 AGENT_INTEROP 摘要，关联
agent_task / agent_attempt / agent_context / agent_result / agent_audit 引用。引用闭包
可验证，原始批准资料标 APPROVED_INPUT_NOT_INDEPENDENTLY_VERIFIED。未给旧 Replay
添加新节点，不把可选 Trace 作为 Agent 任务事实源。

只存批准 Context 和通过校验的正式结果；不存私有历史、原始后端响应、异常全文或
隐藏推理。复用现有脱敏规则，疑似凭证内容在入库前拒绝；规则保守，会误拒部分含
credential/token 等词的普通文字，不能识别任意未标注秘密。JSON 导出有大小限制，
原子写入并拒绝覆盖 SQLite、WAL/SHM，包括伪装成 .json 的 SQLite 文件。

以上为 M2-A 基础层范围。后续授权的 M2-B 现已实现最小主信号版本、有限修复、两阶段
复审、审批和批准目录网关，见 [M2-B 说明](M2B_RESEARCH_LOOP.md)。M1.5 真实回测是另一
独立入口，不会自动交给研究 Agent。尚无仲裁、func1/func2、RAG/MCP、Agent 流程前端或
生产级恢复；真实 Agent 权限隔离要求不因新增研究流程放宽。

# WorldQuant Alpha Research Platform — M2 多 Agent 研究系统开发规格

**版本**：v0.1（架构与开发基线）  
**日期**：2026-10-31  
**状态**：待 Codex 执行；**仅授权首先实施 M2-A**  
**范围**：M2-A～M2-D 的整体蓝图、模块契约、迁移策略、测试及验收门槛。本文不是对现有源码的独立验收，也不声明 Codex/Claude Code/DSH 已接通。

## 0. 执行摘要与决策冻结

采用 **Workflow-Centric** 架构。Python Controller 唯一负责研究状态迁移、准入、预算分配、实验调度；专业 Agent 负责认知任务，不拥有数据库业务写入或回测执行权。采用 **Role → Runtime → Backend Adapter** 三层划分，优先 Codex 生成主信号，Claude Code 为独立 Critic，DSH 保留后端插槽，身份及接入方式未确认之前不编写假接口。

**核心开发约束**：先让两个真实 Agent 在受控契约下生成—审查一个主信号，再实现完整 Seed Reservoir、func1/func2。首个开发批次只做 M2-A，后续阶段必须经测试和审查单独启动。

### 已确定设计

- Generator 提出版本化的 **Main Signal** 经济假设，区别于衍生 **AlphaCandidate** 表达式。
- Critic **独立审查**：收到正式提案、原始任务、必要证据；不接收 Generator 私有会话、草稿和工具历史。
- Generator 修复形成新 SignalVersion；先独立复审新版本、锁定结果，再复验旧缺陷及版本差异。
- Critic 可向 Evidence Gateway 提出受控证据请求；Gateway 使用结构化字段检索 / 未来 MCP、RAG，按权限、来源、预算和时效审核。
- Logical Task 与物理 Execution Attempt 分离；可替换后端但不得绕过角色独立性政策。复用已授权且仍有效的原始证据，不复用上个 Attempt 的私有分析。
- 多 Critic 分歧按**具体论断**仲裁；先确定性校验，再证据检索，必要时有限实验；无新证据时保持 UNCERTAIN，禁止无限投票。
- Critic 可提出实验，Controller 审核，Simulation Executor 执行。实验结果、争议裁决、Signal 准入三套状态互不混用。
- 允许有非阻断性经济机制争议的 Signal **CONDITIONAL** 进入 func1：预定义验证目标、有限预算、停止/晋级门槛；数据合规、时点泄漏及权限风险不得条件放宽。达到预设 func1 指标且稳健性证据满足要求时，可申请有限 func2 预算；未解决争议继续保留。
- M1 的模拟执行与持久化是事实源，M1-O.1 是只读观测/回放；Agent 诊断和 UI 不能控制业务状态。

## 1. 现状与兼容性基线

按项目交付记录：M1 已有 Python Controller、Mock Generator、单候选工作流、受控 AST Validator、SQLite schema v1、Simulation Adapter、预算/去重/UNKNOWN 保护；定向修复后测试数为 61。M1-O 事件观测及 M1-O.1 中文历史回放交付后共报告 117 项测试通过。上述属于交付报告，不代替对当前工作树的独立验证。

M1 `AgentRuntime` 目前返回 `AlphaCandidate`；CLI 直接装配 MockGenerator；**没有通用 Critic 接口、Agent 后端选择、完整结构化输出校验或研究多版本审批**。M2 应以增量兼容方式添加新的抽象，旧 M1 CLI、表和 117 项基线测试应保持有效。施工前 Codex 必须对实际源码核验接口、Schema、运行命令和测试总数，差异写入 M2-A 基线审计。

**禁止**为了加 M2 直接修改旧状态迁移、预算扣减、去重指纹规则、提交超时 UNKNOWN 处理；不得把原有 M1 的单候选 `Signal` 当作已完整实现的主信号库。旧业务 schema v1 应优先保持；新实体采用显式、可迁移且版本化的扩展，不覆盖原始历史。

## 2. 整体逻辑架构

```text
Research CLI / Config
        │
        ▼
Python Workflow Controller ───── Admission & Budget Policy
        │                                  │
        ├── Role Services                 │
        │    ├── MainSignalGenerator       │
        │    ├── IndependentCritic         │
        │    ├── SignalRepair              │
        │    └── (future) Distiller         │
        │            │                     │
        │            ▼                     │
        │     Context Builder & Role Policy│
        │            │                     │
        │            ▼                     │
        │      Unified Agent Runtime       │
        │         ├── Mock Adapter         │
        │         ├── Codex Adapter        │
        │         ├── Claude Code Adapter  │
        │         └── DSH Adapter (TBD)    │
        │            │
        │         Evidence Gateway ── Metadata / History / MCP-RAG (later)
        │
        ├── Seed Reservoir → func1 → func2
        │           │
        ├── Validator / Fingerprint / Simulation Executor (M1)
        │
        ▼
Persisted Research Facts / Attempts / Review Evidence
        │
        └────────── Observatory & Replay (read-only)
```

**边界**：Role 决定任务输入/输出及专业规则；Runtime 管理执行尝试、隔离、超时/取消、资源与状态；Adapter 只封装某后端的启动/事件/结果细节；Controller 掌握业务动作批准权。Context Builder 执行 Controller 授权的 Context Policy，不把“查文件”的自主权下放给 Adapter。

## 3. 分层契约（接口优先于框架）

### 3.1 Role Contract

面向业务定义不同的版本化请求/结果：

| 角色 | 输入最小集 | 成功业务输出 | 不能做 |
|---|---|---|---|
| Generator | 研究设置、字段清单、种子/目标、许可证据 | MainSignalProposal vN（假设/字段/机制/表达式建议/证据） | 自行批准、直接回测、改 DB |
| Critic | 任务目标、特定 SignalVersion、必要原始元数据与证据 | ReviewResult（PASS/FAIL/UNCERTAIN、缺陷 IDs/严重程度/来源） | 查看私有生成会话、改提案、直接提交回测 |
| Repair | 特定 SignalVersion、正式缺陷集合、允许变更范围 | 新的 SignalVersion + changelog | 原地覆盖旧版、修改正式 Critic 记录 |
| Diagnostic（后续） | 故障分类、脱敏证据 | 诊断建议 | 自行执行危险操作或清理 UNKNOWN |

PASS 表示“按当前审查标准未发现阻断性缺陷”，**不是收益真实性证明**；UNCERTAIN 表示证据不足，不能默认为 FAIL 或 PASS。

### 3.2 通用 TaskEnvelope 与 TaskOutcome（概念 Schema）

```json
{
  "schema_version": 1,
  "task_id": "TASK-071",
  "role": "critic",
  "subject": {"signal_id": "SIG-071", "signal_version": 2},
  "context_version": "CTX-004",
  "context_digest": "sha256:<digest>",
  "allowed_evidence_scopes": ["field_metadata", "approved_experiment"],
  "required_capabilities": ["bounded_execution", "structured_result"],
  "resource_limits": {"timeout_seconds": 300, "max_evidence_requests": 4},
  "result_schema": "review_result_v1"
}
```

```json
{
  "schema_version": 1,
  "task_id": "TASK-071",
  "attempt_id": "ATT-002",
  "backend": "claude_code",
  "execution_status": "SUCCEEDED",
  "validated_result_ref": "review:107",
  "usage": {"elapsed_ms": 12400},
  "diagnostics": {"safe_error_code": null}
}
```

执行状态（SUCCEEDED/TIMEOUT/CANCELLED/FAILED/INVALID_OUTPUT）与研究状态（PASS/FAIL/UNCERTAIN）分别建模。Backend 原始输出只能在通过格式与语义校验后进入业务结果；原始秘密、隐藏推理或未经筛选的工具轨迹不进公共观测导出。

### 3.3 Capability 与 Backend Adapter

能力声明包括：结构化输出可用性、工具访问限制、沙箱/工作区隔离、取消/超时、会话恢复、执行事件、用量计量。角色按**硬性能力条件**选择后端；不满足必需安全隔离就拒绝，而不是静默降级。适配器仅负责具体工具的 CLI/SDK/API 参数、权限映射、输出解析及标准化错误。

**注意**：结构化 Schema 只能约束结果，不代表进程可安全执行。Codex 或 Claude Code 作为“研究 Agent”时，必须与拥有开发权限的 Codex 区分：仅授权研究用工作区、工具/网络/文件权限；不能暴露 SQLite 写权限、WorldQuant 凭证、Simulation Adapter 写入口或 Generator 私有资料。若宿主无法强制这些边界，则真实后端测试应降为人工隔离演示，不得声称达到生产级独立审查。

### 3.4 Context Builder 与独立审查

- Controller 授权 SignalVersion、ContextPolicyVersion、EvidenceScope 和预算。
- Role 定义必需字段与审查标准；Builder 从明确的只读来源组装，固定 digest 与引用。
- Critic 允许阅读正式提交的经济机制解释与提案引用；**默认不提供** Generator 的会话/工具记录、草稿、历史私有搜索路径。
- 物理隔离文件系统、进程和工具权限；只写 Prompt 不构成隔离。
- 修复后的 v2 先独立审查并锁定结果，再提供 v1 正式缺陷与 v1→v2 差异进行复验；两阶段分别留痕。

## 4. Evidence Gateway（未来分段实施）

Gateway 是**确定性授权和证据路由服务，不是新的 Agent**。可先从本地字段元数据/结构化历史读取开始，再添加 MCP、RAG；MCP 是接入接口，RAG 是检索机制，不必先安装向量数据库。

统一 EvidenceRequest 绑定 task_id、attempt_id、授权角色（服务端绑定，不能信任 Agent 自报）、subject version、requested scope、查询参数、预算与超时。EvidenceResponse 记录来源、版本、检索时间、完整性、适用时效、证据 ref 和内容摘要。历史事实与当前有效证据区别存储；资料可追溯≠经济结论已被证实。

Agent 可以请求额外证据，但不拥有不受限的项目/数据库读取权限。短等待可使用后端受控工具调用；长等待可结束 Attempt，逻辑任务进入 WAITING_EVIDENCE；证据就绪后依固定 ContextVersion 恢复（会话或重启均可）。恢复时复用合法原始证据及平台任务状态，不继承旧 Attempt 的未提交结论或私有推理；若新后端破坏 Generator/Critic 分离规则则拒绝切换。

## 5. 主信号、审查与实验的领域语义

### 5.1 研究对象与版本

- `MainSignal`：稳定研究身份、研究类别、数据来源和生命周期。
- `SignalVersion`：不可变提案快照（经济假设、数据字段、机制解释、预期方向、风险、证据引用、可能的 Alpha 表达式）；每次 Repair 创建新版本。
- `AlphaCandidate`：由主信号衍生的可执行表达式与 WorldQuant Settings；一条主信号可拥有多个变体。
- `ReviewTask/ReviewFinding`：绑定精确 SignalVersion、ContextVersion、ReviewPhase、证据和执行尝试。
- `Dispute/VerificationTask`：争议论断和可检验问题；不等于整份审查的 PASS/FAIL。
- `ExperimentProposal/ExperimentResult`：先固定假设、证伪标准、样本与预算，再执行；实验完成不等于论断成立。

### 5.2 状态机分离

| 对象 | 最小候选状态 | 关键约束 |
|---|---|---|
| Agent Task | CREATED / RUNNING / WAITING_EVIDENCE / COMPLETED / FAILED / CANCELLED | Attempt 独立；迟到旧 Attempt 不能覆盖当前结果 |
| Review | PENDING / INDEPENDENT_REVIEWED / REGRESSION_REVIEWED / RESOLVED | 独立复审结果必须先锁定 |
| Dispute | OPEN / SUPPORTED / REFUTED / UNCERTAIN | 结论只适用于特定论断及证据范围 |
| Experiment | PENDING / RUNNING / COMPLETED / FAILED / UNKNOWN | 完成是执行状态，不表示绩效通过 |
| Signal Admission | APPROVED / CONDITIONAL / NEEDS_EVIDENCE / NEEDS_REPAIR / REJECTED | 合规硬门控不能被高 Sharpe 抵消 |
| Signal Exploration | ACTIVE / PAUSED / ARCHIVED | 重新激活需要新证据与小额验证预算 |

状态名是设计候选，不应在 M2-A 为未来全部状态建立空表或死代码。具体迁移通过 M2-B/C 测试最终收敛。

### 5.3 仲裁与准入

两个 Critic 发生分歧时，提取具体 Claim 和所缺证据，优先静态规则/字段时点检查，再授权检索，最后才考虑有预注册门槛的有限实验。必须限制轮数和预算。若证据无新增则 UNCERTAIN；不以多数投票或品牌优先级替代证据。

实验建议由 Critic 提出，Controller 按预算/去重/合规/预注册标准批准，由 Simulation Executor 执行。原始指标不可改写。主信号准入独立于局部实验结果：任何未解除的未来泄漏或其他阻断性问题均不得进入后续阶段。非阻断性经济争议可 CONDITIONAL 准入 func1，记录 `verification_objectives`、`unresolved_disputes`、`budget_cap`、`stop_conditions`、`promotion_conditions`。func1 表现好但争议未消失，可经独立稳健性与既定晋级门控申请**有限** func2 预算，不自动批准或正式提交。

## 6. M2 研究工作流目标（M2-C）

```text
Seed Reservoir (versioned hypotheses)
  → Generator / Independent Critic / Repair limited loop
  → Admission (NORMAL / CONDITIONAL / REJECT / EVIDENCE NEEDED)
  → func1 Alpha Swarm (breadth, staged pruning, budget cap)
  → Gate & Robustness (multi-dimensional potential, not Sharpe only)
  → func2 Deep Refinement (bounded local exploration)
  → Candidate Archive / Review / Future Submission Policy (not in M2)
```

AEGIS 的 Step 机制是研究参考，不应直接把帖子中的阈值当作已经验证的本项目常数；门槛由配置管理、在模拟数据和真实平台限制核验后调整。条件研究每项升级都保留独立证据、样本外/稳健性评价及多重试验风险记录。失败实验作为事实留存；Knowledge Distiller（M3）只基于证据标记写出知识，不将猜想转成事实。M2 中不执行正式 Alpha Submission。

## 7. Persistence 与 Observability

### Persistence

- 原 M1 `Run/Job/Budget/Audit` 仍作为模拟业务事实源，保留 schema v1 与去重/UNKNOWN 约束；新研究记录采用最小增量 Schema 和显式迁移。
- AgentTask、Attempt、SignalVersion、Review、EvidenceRef 和 Experiment 的历史必须不可变或可审计；旧版本不可原地覆盖。
- 同一 Logical Task 允许多个 Attempt，但只有当前授权 Attempt 可提交最终结果，采用事务/版本检查防迟到写入；任务完成、审查版本绑定、预算预留必须有原子一致性语义。
- Agent 调用预算、Evidence 预算、Simulation 预算分开核算；UNKNOWN 远程任务不可自动重发或退款。
- 记录安全的失败码和指针，不写明文凭证、敏感原始响应或隐藏模型推理。

### Observatory / Replay

- 复用 M1-O.1 只读快照/诊断/中文回放路径；Agent 执行尝试、证据请求、审查结论、修复版本、实验审批应有独立关联 ID。
- 可观测事件属于真实已提交的事实或明确标记的诊断推断；默认视图中文，保留稳定英文事件码。
- M2-A 先验证可记录任务身份与结果引用，不要求先改 Replay 前端；M2-D 再设计 Agent 流程的中文回放节点。
- 不能把观测子系统的可选 Trace 当作预算/业务状态事实源；缺失 Trace 不得虚构执行经过。

## 8. 分阶段开发与验收

### M2-A — Agent Interoperability Foundation（**唯一当前授权实施阶段**）

**目标**：角色与执行解耦，可在测试环境切换 Mock Backend；为 Codex/Claude Code 做受控、可配置的真实适配验证，不启动研究闭环或 func1/func2。

**任务顺序**：

A0. 审计真实 M1 `agents.py`、CLI、Config、Persistence、Observatory 入口及测试基线，写 `docs/M2A_BASELINE_AUDIT.md`，确认迁移影响。  
A1. 定义最小版本化 TaskEnvelope/ExecutionOutcome、Generator Proposal 与 Critic Result；错误分类、Task/Attempt 标识、Capability Policy；将执行结果与研究判断分离。  
A2. 实现 Runtime、Mock Backend、Role Adapter 和严格结果验证；保留原 `MockGenerator` / M1 命令行为。  
A3. 增加受限 Context Builder、角色视图/证据范围白名单、digest 与独立性测试。  
A4. 配置装配（role→backend），验证未知后端、能力不足、结构化格式异常、超时、取消和迟到 Attempt 不会成为成功业务结果。  
A5. 编写 Codex/Claude Code **可选** Adapter 及带门控的 smoke test（仅在本地明确安装、授权、认证和沙箱验证后运行；无凭证的离线测试必须全绿）。若 CLI/SDK 不具备可强制的安全隔离，标记 UNSUPPORTED 并保留 Mock，不提供危险的默认执行。DSH 仅预留注册入口。  
A6. 增加安全审计摘要与 Observatory 关联 ID；文档及回归测试。

**验收门槛**：

1. 原 M1、M1-O、M1-O.1 CLI 与全部现存测试保持兼容；不改变旧业务状态/预算/指纹/UNKNOWN 语义。
2. 使用 **Mock 角色→统一 Runtime→Mock Backend** 可完成一次生成任务和一次独立审查任务，输出符合各自契约；不能把 Critic Result 误当 AlphaCandidate。
3. 每个任务记录固定 subject version、context digest、role、backend 与 attempt；错误/超时/畸形输出不会被当成 FAIL 或 PASS。
4. 独立 Critic 无 Generator 私有历史；模拟越权请求被拒，物理权限控制局限如实在文档披露。
5. 可配置不同 Backend；未安装/未授权真实 Agent 时项目离线可用。Codex/Claude Code 实际接入成败有单独可复现记录，不假定已经完成。
6. pytest、ruff 与静态检查（按项目已有设置）全部通过；提交交付报告、已知局限、变更清单。

**明确不做**：Seed Reservoir、func1/func2、自动修复、MCP/RAG、Agent 自由文件读写、真实 WorldQuant API、生产级 Worker、正式 Alpha 提交；不为想象中的 DSH 伪造客户端。

### M2-B — Generator–Critic Research Loop（单独授权）

输入：M2-A 已通过验收。实现 MainSignal 版本、独立 Critic 审查、有限修复、二阶段 v2 复审、必要证据请求最小网关、状态/审查记录。先使用两个 Mock Backends 端到端测试，再验证 Codex Generator / Claude Code Critic 的受限独立运行。验收包含：v1→v2 不覆盖、旧版审查不可套用、审查超时不当作研究 FAIL、修复次数有上限、物理隔离验证与审计。

### M2-C — Seed Reservoir / func1 / func2（单独授权）

输入：M2-B 稳定。主信号 Seed 库、生命周期、变体群、分层预算、阶段剪枝、条件准入、晋级与实验记录；以 Mock Simulation 覆盖正常/失败/预算不足/UNKNOWN/并发冲突、暂停与重新激活。真实 WorldQuant API 属独立 M1.5 授权，不与 M2-C 强耦合。

### M2-D — Knowledge & Replay Integration（单独授权）

输入：M2-B/C 可运行。审查/证据/修复/实验图谱延伸到 Observatory，中文回放与 Agent Diagnostic 同源；只增加最小结构化 Evidence 检索与历史记录。复杂向量 RAG、论坛爬取、Knowledge Distiller 的完整知识生产属于 M3，除非单独授权。

## 9. 测试矩阵与工程质量底线

| 类型 | 必测场景 |
|---|---|
| 契约 | Generator 与 Critic 不同 Schema；错误字段、版本冲突、未知枚举、非有限数值拒绝 |
| 后端 | Mock 可离线；缺少可选 CLI 不影响核心；能力不足明确拒绝；超时、取消、非零退出、非法 JSON |
| 隔离 | Critic 无 Generator 私有文件/会话；不信任 Agent 自报角色；不能绕过工作区读凭证或 DB 写权限 |
| 一致性 | 冻结 SignalVersion/ContextVersion；迟到 Attempt 不覆盖；同一审查任务不可被重复结算 |
| 预算 | Agent/Evidence/Simulation 分账；同一逻辑任务重试不自动重复模拟扣费 |
| 审查 | 模型调用失败≠研究 FAIL；修复产生新版本；独立复审锁定后再复验 |
| 仲裁 | 论断级证据；硬性违规不被 Sharpe 覆盖；无新增证据停止仲裁 |
| 可观测 | 任务/尝试/证据 refs 可追溯；旧回放可读；敏感字段与原始秘密不导出 |
| 回归 | 现有全套 M1/M1-O/M1-O.1 测试无回退；pytest/ruff 均通过 |

不强制在 M2-A 一次实现矩阵的全部 M2-B/C 项：对应阶段实施时逐项兑现，拒绝用空接口和永远跳过的测试假装功能完成。

## 10. 主要风险及处理策略

1. **Agent CLI 权限过大**：Prompt / JSON Schema 不是沙箱；仅在经验证的隔离中运行研究代理，否则留在人工/Mock 模式。
2. **不同后端结构化输出差异**：通过 Adapter 解析校验；不支持时拒绝或明确的受控降级，不把自由文本强行当可信 JSON。
3. **审查独立性被工作区破坏**：Critic 不继承 Generator 私有文件；Backend 切换须符合角色独立性政策。
4. **任务恢复语义被夸大**：只能恢复逻辑工作及授权证据，不能宣称复原隐藏推理；旧 Attempt 结果保留审计但拒绝迟到提交。
5. **研究质量被高 Sharpe 绑架**：硬性数据/时点/权限门控优先，预注册实验标准、有限预算与样本外验证。
6. **复杂度过早膨胀**：不采用多 Agent 框架作为先决条件；无需新服务即可进行 M2-A Mock 开发。
7. **兼容旧 M1**：适配器渐进迁移、增量表、回归测试；Observatory 只读事实源。

## 11. Codex 执行指令（直接复制）

> 请阅读 `docs/M2_MULTI_AGENT_RESEARCH_SPEC.md` 及现有 `README.md`、`docs/ARCHITECTURE.md`、M1-O 和 M1-O.1 文档与真实源码。现在**只实施 M2-A（A0→A6）**：首先审计当前工作树，随后建立版本化 Role/Task/Attempt 契约、通用 Agent Runtime、Mock Backend、受控 Context Builder、配置装配与测试。保持 M1 已验证的研究状态、预算、去重和 UNKNOWN 逻辑不变；旧 CLI 与原测试必须持续通过。不得提前实现 M2-B/C/D、M1.5、真实 WorldQuant 回测、RAG、Agent 自由工具权限或自动修复。真实 Codex/Claude Code Adapter 属可选隔离 smoke test：只有已确认 CLI/SDK、权限、认证和工作区约束之后才运行；不满足则明确标记 UNSUPPORTED 而不假定完成。DSH 暂留扩展接口。每步提交可复现测试，最终提供文件变更、架构取舍、权限隔离证据、通过的测试数、遗留风险与下一阶段进入条件。不需要因为实现过程中的局部细节重新征求架构许可，但不要自行扩大范围。

## 12. 完成定义与后续授权门槛

M2-A 的 Done 指的是：**具备可测试、可替换、受约束的认知任务接口**，而不是已经完成多 Agent 自动研究。M2-B 的启动应以 M2-A 验收和至少 Mock Generator+Critic 单任务完整契约通过为条件；真实后端可以作为独立接入验证，不得用无凭证情况阻断离线工程。

之后遵循“开发 → 测试 → 正确性评审 → 可视化复盘 → 迭代”，每次仅实施当前授权阶段。任何新问题优先由真实测试驱动，而不是预先穷尽设计。

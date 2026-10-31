# M2-A Agent Interoperability Foundation 交付报告

2026-10-31，仅实施 A0 → A6，没有进入 M2-B～D。

## 实施顺序与文件

| 阶段 | 交付与阶段验证 |
|---|---|
| A0 | `M2A_BASELINE_AUDIT.md`，核对旧接口/schema/CLI，原 117 项通过 |
| A1 | contracts.py：版本化 Task/Outcome、Subject、资源、两类 Role 结果和严格 JSON；12 项通过 |
| A2 | runtime/backends/roles/ledger：Mock、统一 Runtime、独立角色、事务账本；累计 21 项通过 |
| A3 | context.py：批准角色视图、scope/digest、独立性与伪造 Context 校验；累计 27 项通过 |
| A4 | config.py、生命周期与版本 CAS：配置、能力、超时、取消、迟到、重复结算；累计 37 项通过 |
| A5 | optional_cli.py、probe 脚本：可选后端阻断门与实际预检；阶段 7 项门控测试通过 |
| A6 | agent CLI、只读安全审计摘要、样例、完整回归、文档及开发记录 |

新增源码在 `src/alpha_platform/agent_interop/`，只读摘要为
`src/alpha_platform/observability/agent_summary.py`。旧 CLI 仅增加独立 agent 子命令入口。
原 agents.py、domain.py、workflow.py、persistence.py、simulation.py、pyproject.toml、
uv.lock 无改动；没有依赖、核心业务 schema、模拟计费/去重或 UNKNOWN 策略改变。

样例与脚本：configs/agent_*；scripts/demo_agent_interop.py、probe_agent_backends.py；
examples/agent_interop 下保存安全 JSON，SQLite 文件按已有规则不纳入 Git。

## 最终验证

- `uv run --offline pytest -q --tb=short`：**171 passed**（原 117 + 新 54）。
- `uv run --offline ruff check .`：**All checks passed**。
- `uv run --offline ruff format --check .`：**全部格式检查通过**。
- Mock 实际演示：Generator SUCCEEDED，Critic SUCCEEDED / UNCERTAIN；仅正式提案交接。
- 实际 CLI 选择 Codex 的任务：exit 2，UNSUPPORTED / HOST_ISOLATION_UNVERIFIED，
  无 validated_result_ref，未启动模型或 Simulation；记录在 codex-blocked-task.json。
- 成功结果/审计事务故障注入会回滚；可只重试本地 finalize，重复结算无第二份结果。
- 两种取消方式、抵抗取消后迟到返回、显式 supersede 旧 Attempt、私有 canary、越权
  scope、Context/版本/角色伪造、错格式、密钥样式输出保护、只读摘要与引用闭包均验证。

发现并修复真实集成问题：dataclass 正式结果含 tuple，交接 Critic JSON 契约需要统一
wire 数组表示；ContextBuilder 现标准化后验证，已加生成结果交接回归。

## 真实验证与预留的区别

| 能力 | 实际结果 | 未验证／未实现 |
|---|---|---|
| Mock Generator + Critic | 已离线执行，两类结果/角色视图/引用落库通过 | 不代表经济假设或收益成立 |
| Context 隔离 | 逻辑白名单、快照不可变、digest、私有 canary、scope 拒绝通过 | 不隔离任意恶意 Python 插件的 OS 权限 |
| Runtime | 状态分离、限额、超时/取消、版本与迟到 fencing 通过 | 无生产进程强杀、Worker lease 或自动恢复 |
| Codex | 实测 0.164.0-alpha.11.1；本地报告已登录；help 支持结构化输出参数 | 未验证联网凭证有效性/配额；未执行真实生成；无经验证的宿主隔离器 |
| Claude Code | 本机未安装，与用户此前删除的说明一致 | 未安装、未认证、未执行真实 Critic；未猜测 auth/研究命令 |
| DSH | 仅保留注册槽；选择时明确拒绝 | 无身份、协议、客户端或真实测试 |
| 观察器关联 | 只读 Agent task/attempt/context/result/audit 摘要和引用闭包 | 未添加 M2-D 前端节点 |

安装/认证原始输出不导出；预检证据在 backend_probe.json。执行门未通过时不会因为
CLI 存在或账号登录而放开研究进程。可选真实 Adapter 当前是受控预留，不是假接通。

## 取舍与剩余风险

独立 Agent 账本版本 1 隔离于 M1；不可变内容与事务检查防串版本、重扣和迟到覆盖。
Agent allocated_attempts 是已分配尝试计数，包含失败/门控，不是费用；没有数据的
Token/成本为 null，模拟预算从未访问。正式结果契约不是完整 Signal 库或表达式校验。

外部 CLI 尚无可强制的文件/工具/网络/凭证隔离；Prompt/cwd/Schema 不构成隔离。
真实 execute 因而保持 UNSUPPORTED，后续必须另做受限运行器和真实格式适配验证。
可信 Mock/Python 插件的能力声明不是防恶意宿主代码的安全边界。

账本故障或进程死亡可留下 RUNNING Agent Task；没有自动恢复/补偿。超时只保证
合作式异步执行的 Runtime 不接纳迟到值，不能物理停止任意阻塞代码。脱敏是保守规则，
不是任意秘密识别器。未知 DSH、M2-B/D 研究循环、证据网关和前端不通过空接口假装完成。

下一阶段条件：先审查 M2-A；M2-B 另行授权。当前可用 Mock 验证生成/独立审查任务
契约，但不得声称已实现 Codex Generator + Claude Code Critic 的真实研究闭环。

命令和边界详见 [AGENT_INTEROP.md](AGENT_INTEROP.md)。

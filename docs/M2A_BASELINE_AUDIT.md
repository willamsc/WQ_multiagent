# M2-A 基线审计（A0，2026-10-31）

实施前实际验证：`uv run --offline pytest -q` **117 passed**。
工作树原先仅有新规格 `docs/M2_MULTI_AGENT_RESEARCH_SPEC.md` 未跟踪。

## 事实

- `agents.py` 的 AgentRuntime/Generator/MockGenerator 是 M1 专用，返回 AlphaCandidate；
  CLI execute 直接装配 MockGenerator。保留原接口、默认行为与调用链。
- ResearchConfig 管模拟预算/Mock 场景；workflow.py 和 persistence.py 共同管理
  Run/Job/预算/去重/UNKNOWN。核心 schema v1 不适合混入 Agent 尝试计数。
- Observatory 使用独立只读快照；Replay 只投影已有审计。M2-A 不增加前端研究节点。
- 可复用 `observability/safety.py` 的脱敏规则，以及只读/证据引用方式。
- 本机 `codex --version` 实际输出 `codex-cli 0.164.0-alpha.11.1`；help 确认 exec、
  output-schema、read-only sandbox、ephemeral、ignore-user-config 等开关存在。
  CLI 在初始化自身全局临时目录时出现权限警告；此时没有运行任何研究任务。
- PowerShell Get-Command/where 未找到 Claude Code。DSH 的身份/协议未确认。
  安装、认证、研究任务授权、物理隔离是不同事实，不能相互代替。

## 增量方案

新增 `agent_interop/`：Role → Runtime → Backend Adapter；旧 agents.py 不改名也不替换。
TaskEnvelope/Outcome、正式提案/审查结果分别版本化；Context digest 绑定角色与精确
subject version。Runtime 和账本掌握 task/attempt，Backend 只获得批准的 Context。

新增独立 Agent SQLite 文件，版本化最小 task/attempt/result/audit 表。初始化拒绝
将已有 M1 业务数据库误当 Agent 账本，不增加 Seed/Repair/Dispute/Experiment 空表。
Agent 尝试计数与模拟预算完全分离。任务身份不原地改变；当前 Attempt CAS 拒绝迟到写入。

Mock 适配器是可信进程内测试实现，没有工具或文件读取接口，但不是 OS 安全沙箱。
Context 逻辑隔离和 Mock 越权请求测试不等同于隔离任意恶意 Python 插件。
真实后端必须满足可强制的宿主隔离；当前不将 cwd、Prompt、Schema 或 read-only
写保护当作足够的私有资料读取隔离。无法验证则 UNSUPPORTED，不能运行真实研究。

配置只选择已注册适配器，不从 JSON 动态导入代码或接受任意 shell 命令；DSH 仅保留
注册槽。A5 再执行安全安装/认证状态预检，记录阻断项；不复制/输出 auth 文件或密钥。

## 不实施

M2-B 生成审查修复闭环、主信号库、证据网关、仲裁、func1/func2、MCP/RAG、真实模拟、
生产进程隔离器和 Agent 回放前端。A6 仅提供 task/attempt/context/result 的安全关联摘要。

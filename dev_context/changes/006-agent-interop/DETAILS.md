# 006 · M2-A Agent Interoperability Foundation（2026-10-31）

按 A0～A6 实施。新增 agent_interop 包：Task/Outcome/Proposal/Review 契约、Role、
Runtime、Mock、Context Builder、配置、独立 SQLite 任务账本与可选真实 CLI 门控。
角色/subject version/context digest/attempt 全绑定，输出严格校验；迟到 Attempt
不能覆盖当前结果；Agent 计数与 Simulation budget 分开。没有新依赖。

增加 agent run/show/probe/smoke 命令、只读 Agent 审计引用摘要和两个脚本。只对
旧 CLI 增加入口；未修改旧 agents.py、domain、workflow、persistence、simulation。
没有 M2-B～D、主信号库、自动修复、Gateway、RAG、真实回测或 Replay 新节点。

实际验证：171 项测试通过（原 117 + 新 54），ruff check 与 format --check 通过。
Mock 演示 Generator SUCCEEDED，独立 Critic SUCCEEDED / UNCERTAIN。发现并修复
dataclass tuple → JSON array 交接问题，有回归。超时/取消/迟到/重复结算/事务回滚、
Context canary/范围拒绝/伪造版本、真实后端阻断等有测试。

真实预检：Codex CLI 0.164.0-alpha.11.1，本地报告已登录；未测试联网有效性/配额，
无已验证宿主隔离，未执行真实生成。Claude Code 已删除、未安装，未重新安装。
DSH 仅保留注册槽。实际 CLI Codex 路由返回 UNSUPPORTED/HOST_ISOLATION_UNVERIFIED，
无模型/模拟调用。Mock 逻辑隔离不能冒充恶意 Python/OS 物理隔离。

用户随后明确授权提交 GitHub，本轮按普通提交/推送同步 origin/main，不强制覆盖远程。
最新 commit hash 以 Git 历史为准，避免在提交内自引用自身 hash。
详细风险、文件和证据：[M2A_DELIVERY](../../../docs/M2A_DELIVERY.md)、
[基线](../../../docs/M2A_BASELINE_AUDIT.md)、[操作说明](../../../docs/AGENT_INTEROP.md)。
安全样例：examples/agent_interop/{demo-summary,backend_probe,codex-blocked-task}.json。

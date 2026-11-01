# M2-B 源码基线核查

日期：2026-11-01。用户本轮明确授权 M2-B，优先于 M2 总规格中历史性的“仅 M2-A”批次限制。
当前工作树包含尚未提交的 M1.5，保留其内容，不重置、不混用模拟与 Agent 预算。

| 检查 | 真实实现 | 结论 |
|---|---|---|
| Runtime / Backend | agent_interop/runtime.py、backends.py，异步有限执行、能力门控、新实例 | PASS，复用 |
| 独立角色契约 | contracts.py、roles.py，MainSignalProposal / ReviewResult | PASS，复用 |
| 结构化输出 | strict_json、Runtime._validate、AgentLedger.finish 再校验身份与结果 | PASS，复用 |
| Context | ContextBuilder 白名单、digest、Runtime 重建校验；Critic 只得正式提案 | PASS，复用 |
| Task / Attempt | 不可变任务、current_attempt_id、迟到隔离、单任务唯一结果 | PASS，复用 |
| 主信号与审批 | 尚无版本库、修复循环和审批 Controller | M2-B 必要增量 |
| 修复/复验输入 | 原 Context 不支持正式旧版与缺陷，不应借私有会话传递 | 必要的可选 Context 扩展 |
| 真实后端 | optional_cli.py 仅预检；宿主隔离未验证，不能真实执行 | RISK，继续 fail closed |
| M1/M1.5 | 原 Controller/SQLite/UNKNOWN 与 WQ Adapter 为独立执行路径 | 本轮不修改 |

M2-B 扩展放在独立 Agent 账本，通过扩展版本表管理，不迁移旧业务数据库。
Observer 复用 agent_summary 的只读证据/脱敏/导出，不重构历史 Replay。
输入资料仍是明确批准的结构化材料，不等于已核验的经济事实。
基线和最终测试、真实 CLI 预检以 M2B_DELIVERY.md 的实际记录为准。

核查后确认一个必要安全缺陷：approved_text 原来只递归 list/dict，未检查 tuple，
而正式提案 dataclass 中有 tuple 字段。已增量修复并用正式 Critic Context 的敏感字段
回归测试验证。受控 workflow 扩展仍由原 Runtime 重建和 digest 绑定，没有绕过隔离。
实际基线 211 passed；最终 244 passed，详见交付报告。

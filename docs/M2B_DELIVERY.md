# M2-B 正式交付报告

日期：2026-11-01。范围：最小 Generator–Critic 研究闭环，未启动 M2-C/D。
使用与限制详见 [M2B_RESEARCH_LOOP.md](M2B_RESEARCH_LOOP.md)。

## M2-A 基础核实

[源码审计](M2B_BASELINE_AUDIT.md)确认通用 Runtime、Backend、独立角色、严格 JSON/结果
校验、Context 绑定、能力门控和 Task/Attempt 版本关联均已存在，直接复用。
必要增量仅为受控正式 repair/regression Context，以及 tuple 敏感字段递归检查修复；
未重构 Runtime/Backend/AgentLedger，也未修改 M1/M1.5 状态、预算、指纹或事务代码。
开发基线实际完整测试 **211 passed**（25.35 秒），包括前轮未提交的 M1.5。

## 实现文件

| 文件 | 内容 |
|---|---|
| research_loop/controller.py | 唯一研究编排、版本、两阶段复审、审批、修复/调用/时间限制 |
| research_loop/store.py | Agent 账本扩展、不可变版本/审查、事务与当前结果校验 |
| research_loop/validation.py、policy.py | 确定性校验、保守修复规则、资源上限 |
| research_loop/evidence.py | 批准结构化目录的最小网关，版本/角色/额度授权，不执行外部工具 |
| research_loop/mock.py、cli.py | 可复现 Mock 场景、study run/show |
| agent_interop/context.py | 兼容的可选正式 workflow 视图、tuple 检查 |
| observability/research_summary.py、cli.py | 一致只读快照、证据关联、observe study、安全导出 |
| configs/study-mock.json | 完整输入和独立预算示例 |
| tests/test_research_loop.py | 本阶段验收与事务/隔离回归 |

没有新依赖，没有模拟数据库迁移。开发文档和分层上下文同步更新。

## 运行与测试证据

Mock CLI 实际执行 repair_pass：**APPROVED，2 个版本、3 份锁定审查、5/8 次调用**。
实例见 [实际 Mock 导出](../examples/research_loop/repair-demo.json)。
跨进程 study show / observe study 从同一只读事实源返回版本与审查，无须保留原 Python 对象。

自动化覆盖正常通过、阻断拒绝、UNCERTAIN、修复通过、次数耗尽、复验 FAIL/UNCERTAIN、
超时、无效 JSON、版本不匹配、旧 Attempt 防护、超时迟到不变为结论、Context 伪造、
私有资料剔除、tuple 敏感字段、调用/总时限、证据预算/幂等、跨进程查询、CLI 安全导出、
多轮所有旧缺陷复验、审批前置门控、版本/审计回滚、M1 预算与 schema 不变。
最终完整 `uv run --offline pytest -q --tb=short`：**244 passed，36.41 秒**，
其中原有 211 项全部通过，新增 33 项。
`uv run --offline ruff check .`：**通过**。
`uv run --offline ruff format --check .`：**通过，98 files already formatted**。
`git diff --check` 通过（只有既有 Windows LF/CRLF 提示）。

## 真实 Agent 验证

真实本机预检：[backend_probe.json](../examples/research_loop/backend_probe.json)。

| 后端 | 实际验证 | 未验证/阻断 |
|---|---|---|
| Codex | PATH 安装，0.164.0-alpha.11.1；本地已登录；exec help 有 output-schema | 未验证网络凭证/模型调用/实际结构化响应；宿主隔离未验证，UNSUPPORTED |
| Claude Code | PATH 未找到 | 未安装，不自动重装；未执行研究 |
| DSH | 可替换注册插槽 | 身份/协议未确认，没有客户端或真实调用 |

真实模型研究调用 **0 次**。本机开发代理拥有项目权限，不等于可作为受隔离研究 Agent；
未通过 Prompt、cwd 或配置标记假装物理隔离。固定真实路由的 Controller 也会记录
UNSUPPORTED 执行失败，不能改用 Mock 冒充实际调用成功。

## 剩余风险与建议

- Mock 只验证工程契约，APPROVED 不证明经济假设有效。
- 只有逻辑 Context 隔离；启用真实研究前需可复现的工作区、文件、网络、凭证和数据库隔离。
- 不支持中断自动恢复、NEEDS_EVIDENCE 追加后续跑或人工状态覆盖；进程退出后的 RUNNING
  需要核查，不能通过切换后端改判。Task 注册与研究授权分属两个短事务，退出可能留下
  未执行的 CREATED Task；实际 Backend 调用始终先有研究关联和预算授权。
- Gateway 是精确标识符的批准目录路由，不是检索系统；输入资料未独立核验。
- 全局语义去重、模型用量/费用实测、分布式 Worker 和研究流程中文播放器均未实现。
- 原 M1/M1.5 遗留限制不在本轮扩大修复；真实回测不会由 M2-B 自动触发。

下一步先审查本阶段真实结果与权限边界，再单独验证隔离后的 Codex/Claude 生成–审查。
未获得进一步授权前不启动 Seed Reservoir、func1/func2 或 M2-C/D。

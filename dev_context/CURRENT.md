# 当前项目状态

更新：2026-11-01。

- 已完成 M1、正确性审查、M1-O、M1-O.1、M2-A 和 **M2-B 最小研究闭环（Mock 验收）**。
  最新全量 **244 passed**，ruff check/format --check 通过。M1.5 验收边界仍见
  [M1.5 报告](../docs/M15_DELIVERY.md)，本轮未扩大其真实回测验收。
  用户已授权将 M1.5 与 M2-B 改动提交并推送 GitHub main；提交与同步状态以实际 Git 为准。
- 已将项目首次推送到 `https://github.com/willamsc/WQ_multiagent.git` 的 main 分支，
  初始代码提交 `150b992`；分层开发记录已建立。后续状态记录提交以 `git log` 和
  远程 main 为准。同步详情见 [005](changes/005-github-context/DETAILS.md)。
- 核心 schema v1；可选 Trace 为独立附加表。业务图、预算、指纹和提交规则不随
  Viewer/Replay 改变。数据库仍是业务事实来源。
- 回放基于已记录事件；最终快照不注入历史帧；跨源同秒顺序不确定，没有 span ID
  就不猜配对。UNKNOWN 保留预留，FAILED Run + POLLING Job 冻结待人工核查。
- M2-A：新 `agent_interop/` 与独立 Agent SQLite；旧 M1 agents.py/业务状态不变。
  Mock 生成与独立审查成功；Codex 本地 CLI 已安装且报告已登录，但无经过验证的
  宿主隔离，真实研究执行 UNSUPPORTED。Claude Code 此前已删除、未安装；DSH 仅槽位。
- M2-B：research_loop 复用 M2-A Runtime，Agent 账本附加表管理不可变主信号版本、独立
  审查/旧缺陷复验、有限修复、调用/时间/证据预算。独立 v2 先锁定再提供旧版缺陷。
  Mock CLI 实测 APPROVED（2 版、3 审查、5 次调用），跨进程 study show / observe study 可查。
  Context 仅补可选正式 workflow 和 tuple 敏感检查；未修改 M1/M1.5 核心状态和预算。
  真实预检仍只有 Codex 安装/本地认证/help 证据，真实研究 0 次；未实现物理隔离或自动恢复。
- M1.5：独立 wq 包复用原 Controller/预算/审计。8×6、8×10 和滚动补位离线验证。
  修复凭证外层引号解析后真实认证 201；仅提交 1 个 Alpha，远端 COMPLETE 并取得真实指标。
  原本地任务因 testPeriod 未回显被阻断，保留 FAILED/UNKNOWN，不自动改写历史或重提。
  现允许该字段不回显并记录证据缺口，其他设置仍严格核对；wq inspect 可只读核查原任务。
  未验收真实自动闭环的完整成功状态，未实测批量额度/速度；网络存在间歇故障。
- 未实施：实时服务、M2-C/D、Seed Reservoir、func1/func2、MCP/RAG、多 Worker/自动对账恢复、正式 Alpha 提交。
  文档站本轮访问失败；论坛 JSON 尚未接入业务。testPeriod 省略默认值未验证，暂保留请求。
- 论坛原始文件 `WQP_CommunityState_20261027_223334.json` 约 172 MiB，只留本地。
  `.venv`、`.uv-cache`、数据库和 `.observatory-browser` 均忽略，不推送。

## 按需阅读

- 规格/总体设计：[README](../README.md)、[架构](../docs/ARCHITECTURE.md)。
- 当前交付：[M1-O.1 报告](../docs/M1O1_DELIVERY.md)、[中文回放说明](../docs/REPLAY_STUDIO.md)。
- 最新增量：[M2-A 报告](../docs/M2A_DELIVERY.md)、[Agent 操作/权限说明](../docs/AGENT_INTEROP.md)。
- 研究闭环：[M2-B 交付](../docs/M2B_DELIVERY.md)、[操作/限制](../docs/M2B_RESEARCH_LOOP.md)。
- 回测接入：[M1.5 说明](../docs/M15_WQ_SIMULATION.md)、[实际验证](../docs/M15_DELIVERY.md)。
- 入口：`src/alpha_platform/cli.py`；业务编排 `workflow.py`；存储 `persistence.py`；
  观察与回放 `observability/`；浏览器验收脚本 `scripts/check_replay_browser.py`。
- 测试：`uv run --offline pytest -q`；静态检查：`uv run --offline ruff check .`、
  `uv run --offline ruff format --check .`。
- 六组样例：`examples/observatory/manifest.json`；浏览器结果：
  `examples/replay-browser-qa/results.json`。容量/XSS HTML 可由脚本重新生成。

# 当前项目状态

更新：2026-10-31。

- 已完成 M1、正确性审查、M1-O、M1-O.1 和 **M2-A 兼容基础层**；最近一次全量
  验证为 **171 passed**，ruff check/format --check 通过。用户已明确授权将 M2-A
  同步至 GitHub main，本轮纳入提交交付；最新提交与同步状态以实际 Git 为准。
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
- 未实施：实时服务、M1.5、真实 WorldQuant API、M2-B～D 研究流程、RAG、多 Worker 恢复。
  用户提供的平台文档地址和论坛 JSON 尚未接入业务。
- 论坛原始文件 `WQP_CommunityState_20261027_223334.json` 约 172 MiB，只留本地。
  `.venv`、`.uv-cache`、数据库和 `.observatory-browser` 均忽略，不推送。

## 按需阅读

- 规格/总体设计：[README](../README.md)、[架构](../docs/ARCHITECTURE.md)。
- 当前交付：[M1-O.1 报告](../docs/M1O1_DELIVERY.md)、[中文回放说明](../docs/REPLAY_STUDIO.md)。
- 最新增量：[M2-A 报告](../docs/M2A_DELIVERY.md)、[Agent 操作/权限说明](../docs/AGENT_INTEROP.md)。
- 入口：`src/alpha_platform/cli.py`；业务编排 `workflow.py`；存储 `persistence.py`；
  观察与回放 `observability/`；浏览器验收脚本 `scripts/check_replay_browser.py`。
- 测试：`uv run --offline pytest -q`；静态检查：`uv run --offline ruff check .`、
  `uv run --offline ruff format --check .`。
- 六组样例：`examples/observatory/manifest.json`；浏览器结果：
  `examples/replay-browser-qa/results.json`。容量/XSS HTML 可由脚本重新生成。

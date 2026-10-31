# 当前项目状态

更新：2026-10-31。

- 已完成 M1、正确性审查、M1-O、M1-O.1；最近一次全量验证为 **117 passed**，
  `ruff check .` 和 `ruff format --check .` 通过。新增上下文文档没有改动业务源码。
- 当前任务：首次提交到 `https://github.com/willamsc/WQ_multiagent.git`，以及建立
  分层开发记录。推送状态见 [005 详情](changes/005-github-context/DETAILS.md)。
- 核心 schema v1；可选 Trace 为独立附加表。业务图、预算、指纹和提交规则不随
  Viewer/Replay 改变。数据库仍是业务事实来源。
- 回放基于已记录事件；最终快照不注入历史帧；跨源同秒顺序不确定，没有 span ID
  就不猜配对。UNKNOWN 保留预留，FAILED Run + POLLING Job 冻结待人工核查。
- 未实施：实时服务、M1.5、真实 WorldQuant API、M2 研究流程、RAG、多 Worker 恢复。
  用户提供的平台文档地址和论坛 JSON 尚未接入业务。
- 论坛原始文件 `WQP_CommunityState_20261027_223334.json` 约 172 MiB，只留本地。
  `.venv`、`.uv-cache`、数据库和 `.observatory-browser` 均忽略，不推送。

## 按需阅读

- 规格/总体设计：[README](../README.md)、[架构](../docs/ARCHITECTURE.md)。
- 当前交付：[M1-O.1 报告](../docs/M1O1_DELIVERY.md)、[中文回放说明](../docs/REPLAY_STUDIO.md)。
- 入口：`src/alpha_platform/cli.py`；业务编排 `workflow.py`；存储 `persistence.py`；
  观察与回放 `observability/`；浏览器验收脚本 `scripts/check_replay_browser.py`。
- 测试：`uv run --offline pytest -q`；静态检查：`uv run --offline ruff check .`、
  `uv run --offline ruff format --check .`。
- 六组样例：`examples/observatory/manifest.json`；浏览器结果：
  `examples/replay-browser-qa/results.json`。容量/XSS HTML 可由脚本重新生成。

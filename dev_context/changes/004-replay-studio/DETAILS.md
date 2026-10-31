# 004 · M1-O.1 中文历史回放（2026-10-31）

按 R0～R5 实现 Replay Builder、纯 State Projector、共享 ReplayBundle 和中文离线
HTML Player。支持播放/暂停/重播、步进、滑块、速度、异常导航、节点/证据检查。
HTML 使用 Python 增量投影，不维护另一套 JS 业务规则；初态不含最终快照。

保留源内顺序，同秒跨源顺序明确不确定；无 span ID 不猜配对；截断后从未知重新
投影；引用缺失与污染 Trace 明确警告。零新依赖，未修改核心业务模块或数据库。

117 项测试通过；ruff 检查通过。Chrome 对六个真实 Mock 样例逐帧比对 Python，
并验证交互、XSS、键盘、空历史、手机视口和 2000 帧人工容量夹具。页面外部请求为零。
容量夹具不是研究事实。样例 re-export 不执行新的 Mock。

源码：`observability/replay.py`、`projector.py`、`replay_html.py`、`replay_player.js`；
增量改动 `queries.py`、`diagnostics.py`、`cli.py`；脚本与四个回放测试文件。

完整报告：[M1O1_DELIVERY](../../../docs/M1O1_DELIVERY.md)；
[基线](../../../docs/M1O1_REPLAY_BASELINE.md)；[中文说明](../../../docs/REPLAY_STUDIO.md)。

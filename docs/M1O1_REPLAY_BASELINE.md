# M1-O.1 回放基线核查（R0）

2026-10-31，依据实际源码核查；改动回放代码前执行全量测试：**86 passed**。
不改 M1 业务模块或数据库 schema。M1-O 原规格实际位于仓库根目录，现行实现
说明为 `docs/OBSERVATORY.md`。

## 实际输入与约束

- `queries.ObservatoryReader`：SQLite URI `mode=ro`、`query_only=ON`、2 秒 busy
  timeout；一次 BEGIN/rollback 包围全部 SELECT。读取上限：2000 audit、1000 trace、
  2000 attempts、100 jobs；默认返回最近 200 个混合事件。审计 ID 和 Trace ID
  属于两个独立序列，不能跨源比较大小来确认因果。
- `research_events`：整数 ID、run/job ID、event_type、payload_json、UTC 秒级 at。
  有 GENERATED、VALIDATED、队列/预算/提交/查询/终态等事件；提交意图不含 attempt ID。
  同一事务可能记录多个事件，回放事件前缀不是每帧独立提交的数据库截图。
- 可选 `observability_traces`：独立自增 ID、run/job/candidate ID、节点、事件、
  毫秒 UTC occurred_at、level、outcome、受限 payload；**没有 span ID**。不能用
  最近同名 start/end 猜配对。completion 自带的 duration_ms 可以作为该条记录的
  数值展示，但不用于推算未记录的时间或建立跨源顺序。
- `traces.py`：内存最多 256 条，运行结束后独立连接 flush；崩溃可丢缓冲；
  最多保留 7 天/10000 条。off 无额外记录，light 无表达式详情，full 有受限片段。
  物理入库顺序不是事件发生顺序。不得通过回放读取触发 flush/清理。
- M1 状态图仍由 Controller/Repository 管理；Run/Job/budget/results/attempt
  outcome 是当前业务快照。它们可用于终态对照，**不能注入历史早期帧**。
  最终预算总额、最终指标、最终 attempt outcome 不具备逐帧记录。
- 现有 `graph.py` 生成最终图，包含业务终态补充和派生节点；不得作为 projector 初始图。
- `diagnostics.py`：默认 256 KiB，上限 4 MiB，检查证据闭包；输出使用同目录临时
  文件 + replace，保护数据库及 WAL/SHM；回放复用 sanitizer/引用检查/写出保护，
  但使用独立 Replay schema，不能让旧 exporter 的尾部裁剪伪装完整回放。
- 原 HTML 对文本转义，JS 是常量，无网络；新播放器嵌入 JSON 需额外逃逸 `<`、
  `</script>`、U+2028/U+2029。交互验收使用已有 Chrome、项目内独立 profile，
  通过 stdlib DevTools 客户端控制，无浏览器/依赖下载。

## 可复原矩阵

| 可严格复原的记录 | 只能显式推导 | 根本无法知道 |
|---|---|---|
| 单源 row ID 顺序及原始事件内容摘要 | 稳定跨源展示顺序，不代表真实全序 | 同秒 audit 与 trace 的精确先后 |
| audit 中 Run/Job 迁移、提交意图及拒绝原因 | 审计路径所支持的逻辑节点进度 | 外部可见的每次事务提交瞬间（无事务 ID） |
| 已记录的 Trace 开始/结束/异常和自带耗时 | 根据 M1 v1 规则推导的预算初值、增减 | 没有 Trace 的精确调用耗时/等待时间 |
| UNKNOWN 与 FAILED Run + POLLING Job 的审计前缀 | 路径暗示 gate 已计算，标 INFERRED | 无 ID 的并发 span 配对/丢失缓冲内容 |
| 引用对应行的脱敏、截断摘要 | 截断后仅能显示部分过程，预算绝对值可能未知 | 原始认证、模型隐藏推理、未存储请求/响应 |

## 六个现有样例

`examples/observatory/manifest.json` 指向真实生成的 Mock Run。成功、校验拒绝、
重复、预算耗尽、提交 UNKNOWN、持久化失败均有 audit；此前样例开启 full Trace。
成功可记录到终态，但有同秒跨源歧义；拒绝/重复/预算不足没有真实入队或提交；
UNKNOWN 无 receipt，不能制造 remote ID；落库失败仍是 FAILED Run + POLLING Job。
样例仅展示 Mock 执行，指标不是真实 Alpha 绩效。

## R1～R5 实施契约

复用同一只读 snapshot，新增 replay 专用规范化元数据与污染降级；旧命令默认
行为不变。Replay Builder 固定事件 1 起始序号，初始视图游标 0；保持源内序，
跨源排序标不确定，不猜 span。Projector 是纯事件 reducer，初态不含终态事实。
JSON 保存增量状态，HTML 用同一 Python 结果重放，不另写 JS 业务规则。无事件时
不造帧。最终业务快照只放独立标识面板，不能用于填补历史。

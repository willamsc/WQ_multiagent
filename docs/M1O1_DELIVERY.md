# M1-O.1 Research Replay Studio 开发交付报告

日期：2026-10-31。按 R0 → R5 顺序实施；无新增依赖、业务表或实时服务。
没有修改 M1 的状态图、预算、指纹、提交、模拟或审计事务行为。
没有调用真实 WorldQuant API、诊断 Agent 或 M2 研究流程。

## 阶段与变更

| 阶段 | 实际交付 |
|---|---|
| R0 | `docs/M1O1_REPLAY_BASELINE.md`，源码事实与可复原矩阵；原 86 项测试通过 |
| R1 | `observability/replay.py`：源内保序、稳定合并、同秒歧义、损坏证据与截断标记；先完成 4 项构建器测试 |
| R2 | `observability/projector.py`：无终态输入的纯 reducer、任意帧重算和紧凑 delta；阶段 8 项测试通过 |
| R3 | 同一 `build_replay_bundle` 提供 JSON/HTML 数据、证据闭包、限额、终态独立对照；阶段 16 项测试通过 |
| R4 | `replay_html.py`、`replay_player.js`：中文安全 DOM 播放器与 CLI，后续浏览器实测通过 |
| R5 | 六组真实样例、Chrome 交互 QA、容量/隐私/损坏数据测试、中文说明与交付记录 |

现有文件的增量改动：

- `queries.py` 新增 for_replay 只读入口选项，沿用同一快照；保留源读取上限，验证
  诊断字段类型/数值/结果一致性，损坏记录显式跳过和警告。旧命令走原默认路径。
- `diagnostics.py` 的证据引用闭包检查支持 projector 的 refs；原子写出与数据库
  sidecar 保护复用，无新文件写出旁路。
- `cli.py` 新增 observe replay（默认 HTML，--format json 导出同源数据）；不允许
  CLI 静默过滤历史，筛选只在播放器列表里进行。
- `scripts/generate_observatory_samples.py` 为六组样例添加 replay_html/replay_json；
  --replay-existing 只读既有 Run，不再运行 Mock。
- `scripts/check_replay_browser.py` 使用纯 stdlib CDP 客户端进行真实 Chrome 验收；
  只使用临时本机 DevTools，不是产品服务。profile、截图、结果均在项目内。
- 更新 README、ARCHITECTURE；新增 `docs/REPLAY_STUDIO.md`。
- 新增 test_replay_builder.py、test_projector.py、test_replay_bundle.py、test_replay_html.py。

## 验证结果

- `uv run --offline pytest -q`：**117 passed**，包含原 86 项及 31 项新用例。
- `uv run --offline ruff check .`：**All checks passed**。
- `uv run --offline ruff format --check .`：**39 files already formatted**。
- 六个真实 Mock 历史场景均通过 Chrome 交互验证：初态隔离、单步、事件跳转、
  节点与证据检查、筛选不改变投影、拖动进度、调速、播放暂停、末帧、异常导航。
- 六组每一帧的 JS 状态与 Python projector 结果逐帧相等；同一游标反复跳转不重复扣计数。
- 另测重播、键盘、手机视口、空历史、XSS、无外部请求。2000 帧人工容量夹具仅渲染
  最多 100 条列表，最终一轮本机跳转末帧约 **3.7 ms**，仅为该机器测量，不是性能承诺。
- 缺 Trace、损坏 JSON、异常字段类型、非有限/极端数值、缺失 source_ref、事件与
  字节裁剪、最小/最大预算及数据库只读/输出保护均有自动测试。
- pytest 使用此前授权的提权执行以访问项目内测试临时目录；未下载工具、浏览器或模型。

浏览器结果：`examples/replay-browser-qa/results.json`。
截图：success-initial.png、success-paused.png、success-final.png、unknown-final.png、
persistence-failure-final.png、success-mobile.png 等；已检查桌面初态、暂停、失败与移动布局。

## 实际回放效果

直接打开 `examples/observatory/success-replay.html`：初态游标 0，所有节点为
“尚未观察到”，Run/Job/预算不含终态值；随后只应用已有历史事件。

`unknown-replay.html` 最后保留预留 1 / 消耗 0（推导），不制造远程 ID；
`persistence-failure-replay.html` 保留研究失败、任务查询中、消耗 1，并明确显示
查询调用状态未知与人工核查提示，不将冻结任务当作仍在执行。

六组帧数：成功 29、校验拒绝 9、重复 11、预算耗尽 11、UNKNOWN 17、持久化失败 30。
数据均从已有样例数据库重读，没有为回放重新执行研究。JSON/HTML 使用同一 bundle；
原有静态报告和诊断 JSON 也继续保留。

## 历史真实性与限制

1. 审计为秒级、Trace 为毫秒级。同秒跨源只提供稳定展示顺序；没有精确因果全序。
   快速 Mock 的业务审计可能先回放到终态，再展示同秒诊断，界面持续提示这种歧义。
2. 没有 span/事务 ID，不匹配同名开始/结束，不声称每个事件都是单独提交快照；
   播放是固定 UI 节奏，不是真实 Wall Clock。
3. 业务最终行只用于独立最终对照，不能回填早期节点。预算数值和路径隐含节点用
   INFERRED 标记；没有前缀时保持未知。截断后重新投影，不偷带隐藏状态。
4. 没有审计/Trace 时不制造事件；丢失缓冲、过期内容、原始敏感请求/响应和隐藏
   推理无法补回。损坏或类型异常的诊断被拒绝，不显示成成功。
5. JSON 默认 256 KiB、事件默认 200；最多 2000 帧、4 MiB，HTML 最多 6 MiB。
   无法保持格式与引用闭包时失败，不输出看似完整的半份故事。
6. 脱敏仍是保守的允许字段与模式过滤，不能识别任意未标注秘密。产品没有真实
   模型、平台接口或任何恢复/退款按钮；未开展 M1-O.2、M1.5、M2～M4。

容量、空历史、XSS HTML 位于 QA 目录，明确为人工测试夹具，不声称是真实研究历史。
下一步如需要更准确的未来回放，可另行规划 span/transaction 关联和更细精度采集；
本轮没有据此改写历史或改动采集行为。

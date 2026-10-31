# M1-O.1 中文历史回放工作台

工作台只读取已经提交的 M1 审计与诊断证据，不会重新运行研究、调用模型或 Adapter，
不会修改数据库、扣款、退款、恢复 UNKNOWN，也不启动实时服务。

## 使用

在项目根目录执行：

```powershell
# 中文离线播放器（默认 HTML）
uv run --offline alpha-platform observe replay --run-id RUN_ID --output run-replay.html

# 同源机器可读数据
uv run --offline alpha-platform observe replay --run-id RUN_ID --format json --output run-replay.json

# 指定数据库，--db 放在子命令之前
uv run --offline alpha-platform --db examples/observatory/samples.sqlite3 observe replay --run-id RUN_ID --output run-replay.html
```

需要采集未来运行的可选诊断时，可以继续使用原有 `research run --trace light/full`；
这会运行新的 Mock 研究，和纯读取的 `observe replay` 是不同操作。
没有开启 Trace 的历史研究仍能回放已记录的审计，不会补造缺失的调用。
原 `observe show/timeline/graph/inspect/export/report` 命令保持原行为。

## 播放方式

双击 HTML，无需网络。页面初始在游标 0，历史 Run/Job/预算均为未知，节点显示
“尚未观察到”；这不表示实际尚未发生。最终查询快照只在独立折叠面板里作对照。

- 播放／暂停，前后单步，回到开头／最后，拖动滑块或点击事件定位。
- 速度支持 0.5×、1×、2×、4×；1× 每帧 800 毫秒，是演示节奏，不是真实等待时间。
- 到末尾自动停止；再点“重播”从初始视图播放。切换页面可见性或离开页面会暂停。
- “上／下一个异常”仅跳到真实存在的异常事件。异常累计显示截至游标已观察到的
  条件；早期帧不包含后来的异常。曾发生的诊断调用异常不等于最终研究失败。
- 节点按钮打开该帧节点检查器；“查看证据”打开脱敏摘要与原始来源引用。
- 筛选节点或异常只影响左侧列表，不改变全量帧索引、投影或预算计数。
- 焦点在页面主体时：空格播放／暂停，左右箭头步进，Home/End 跳转。
  控件本身可用 Tab 聚焦、键盘操作。长时间线窗口最多渲染 100 条，不创建 2000 个按钮。

## 历史真实性

`Replay Builder → State Projector → ReplayBundle` 是唯一共享数据路径。
HTML 不自行推测业务流程，只应用 Python 导出的增量状态；JSON 与 HTML 来自同一次
只读一致快照。事件序号从 1 开始，游标 0 是初始视图，不是伪造事件。

| 信息 | 回放处理 |
|---|---|
| 审计源内部 row ID 顺序 | 保留，不用时间排序打乱源内顺序 |
| 同秒审计与 Trace | 稳定合并并提示顺序不确定，稳定不代表已确认因果 |
| 源内时间戳回退 | 保留记录顺序并警告，不伪造时间 |
| Trace 开始／结束 | 没有 span ID，不做最近同名配对；只展示单条已记录的耗时 |
| 已生成、校验、入队、提交、查询、终态审计 | 按游标前缀投影，不调用最终图来填节点 |
| 预算 | M1 v1 创建初值和审计路径增减仅标“推导”；总额不从最终表提前回填 |
| 丢失早期事件 | 从未知初态重新投影，不携带被删前缀的隐藏 checkpoint |
| 最终业务行、指标、尝试最终 outcome | 只在最终查询快照中展示，绝不用于构造历史成功事件 |
| 历史原始输入／输出未采集或未导出 | 显示无法回放，技术详情仅提供现有允许字段摘要 |
| 缺失来源引用 | 保留显式 missing_reason，本帧不用于确认状态 |
| 损坏／类型异常的 Trace | 跳过并提示，不投影为成功 |

同一事务会记录多个审计事件，因此本工具是**事件前缀投影**，不是每帧独立提交的
数据库截图，也不是屏幕录制。业务状态、逻辑节点状态和诊断调用状态不是同一概念。
更弱的诊断不会推翻已记录的业务迁移。流程箭头是预期拓扑或明确标记的展示次序推导。

FAILED Run + POLLING Job 会同时保留两个原始历史状态，并显示人工核查异常。
查询逻辑节点保守显示“状态未知／推导”：POLLING 是冻结保留的任务状态，不证明
此时还有调用在执行。UNKNOWN 不会制造远程 ID，也不会释放预留或重发。

## 数据与边界

- 新增 ReplayBundle schema v1，与旧诊断 JSON v1、核心数据库 v1、指纹版本分别独立。
  `frames` 为记录事件，`frame_states` 是按节点增量更新和根字段替换；`initial_state`
  不包含终态。`final_snapshot/final_comparison` 与历史投影明确分离。
- 所有历史变化有证据引用；图状态、预算、冻结状态等推导有依据说明。
  `evidence` 构成来源闭包；来源未取得时有 `missing_reason`，不静默留下悬空引用。
- 默认最近 200 条事件，`--max-events` 为 1～2000；底层读取上限仍是 M1-O 的
  2000 audit / 1000 Trace / 2000 attempts / 100 jobs。读取达到上限会警告。
- `--max-bytes` 默认 262144，上限 4194304，最小 8192。超限时逐步删去较早帧并
  重新从未知投影，标记 EXPORT_TRUNCATED、遗漏数量和各源序号范围。
  无法保留合法闭包则清晰失败，不写半截 JSON。摘要截断另有 PAYLOAD_TRUNCATED。
- HTML 上限 6 MiB；事件大对象、原始认证、原始 Adapter 响应、隐藏推理不导出。
  使用原有脱敏与允许字段规则；HTML 数据通过安全 JSON 逃逸嵌入，JS 仅使用安全
  DOM/textContent，使用常量脚本哈希 CSP，不允许外部请求。
- 不新增数据库表或依赖，不改业务状态图、预算规则、指纹、提交和审计事务语义。
  数据库用 mode=ro + query_only + 同一读取事务，不迁移、不 flush、不执行清理。
  原子文件输出继续保护 SQLite 及 WAL/SHM。

## 实际样例与验收

`examples/observatory/manifest.json` 新增 replay_html/replay_json，六组均从现有真实
Mock Run 读取：success、validation-rejected、duplicate、budget-exhausted、unknown、
persistence-failure。模拟指标不是真实 Alpha 绩效。

仅重新导出现有样例，不执行 Mock：

```powershell
uv run --offline python scripts/generate_observatory_samples.py --replay-existing
```

新建研究样例（会运行 Mock），应指定新目录；原脚本拒绝覆盖已有样例数据库：

```powershell
uv run --offline python scripts/generate_observatory_samples.py --output examples/new-observatory
```

自动浏览器验收（需要本机已有 Chrome，可用 --chrome 指定；不下载浏览器或第三方库）：

```powershell
uv run --offline python scripts/check_replay_browser.py
```

该脚本临时使用本机 DevTools 端口进行 QA，不是产品 HTTP 服务；结束即关闭浏览器。
profile 保存在 `.observatory-browser`；结果和截图在 `examples/replay-browser-qa`。
六组逐帧和 Python 结果比对；实际操作播放/暂停/重播/步进/滑块/速度/异常/证据；
另测键盘、移动端、无历史、XSS、2000 帧容量及页面外部请求为零。
空历史、容量、XSS 页面明确为人工 QA 夹具，不声称是真实研究历史。

手工复核建议：先打开 success-replay.html，确认初始节点无成功状态；播放并暂停、
倒退、拖动，再检查证据；打开 unknown 和 persistence-failure，确认预留、冻结状态
及缺口提示；折叠最终快照，确认它不会影响历史节点。

## 已知限制

审计只有秒级分辨率，快速 Mock 的多数业务事件发生在同一秒；因此业务审计可能先
展示到终态，随后展示同秒诊断。这是诚实的稳定展示次序，不是假装精确的录屏。
没有 span/事务 ID，不能承诺还原跨源因果或每个提交时刻；没有 Trace 或已丢失的
内容不能补回。界面不提供真实时间间隔播放。最终快照可以与部分历史无法对齐，
只报告差异/无法重建，不自我修复。未实施 M1-O.2 实时采集、M1.5 或 M2～M4。

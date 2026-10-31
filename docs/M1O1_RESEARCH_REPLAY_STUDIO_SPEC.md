# WorldQuant Alpha Research Platform — M1-O.1 Research Replay Studio 开发规格

> 版本：v0.1（2026-10-31）  
> 状态：待 Codex 实施  
> 前置里程碑：M1（61 tests）及 M1-O（86 tests）已由开发方报告通过  
> 实施目标：**中文、交互式、只读、可验证的历史执行回放**  
> 范围：仅 M1 单候选 Mock 研究闭环；不启动 M1.5/M2–M4。

## 0. 本文定位、输入事实与执行边界

本文是对 `docs/M1O_RESEARCH_OBSERVATORY_SPEC.md` 的**增量开发规格**，不是替换文件；以 M1-O 实际代码和 `docs/OBSERVATORY.md` 为准核实接口。用户已经确认 M1-O 可以输出英文静态离线 HTML、诊断 JSON、事件/审计证据，并已通过 86 项测试（均为交付报告所述，本文未独立审计仓库源码）。

### 0.1 用户诉求

1. 人类界面必须面向中文使用者，解释性内容使用简体中文。
2. 允许像观看录屏一样播放、暂停、倒退、单步、拖动时间线，**在每一历史时刻显示当时能够被证据支持的状态**。
3. 通过动态流程图、事件时间线、节点输入输出引用、异常定位辅助研发和测试；不是只浏览最后的业务总结。
4. 本地按需生成/打开报告，不自动常驻服务。
5. 导出同源、结构化的机器可读回放数据，为后续 Codex/诊断 Agent 定位缺陷提供证据，当前**不调用 Agent**。

### 0.2 非目标（不得越界）

- 不重新执行 Alpha、LLM、WorldQuant API 或 Mock；不重新扣预算，不“修复”数据库。
- 不开发 HTTP server / WebSocket / 后台持续采集器 / 多用户系统 / 登录权限。
- 不引入新依赖、前端框架、CDN、网络资源或下载模型。
- 不实现“实时追踪”及进程中间增量 flush；这是后续 M1-O.2 候选工作。
- 不修改现有业务状态图、预算、去重、提交策略、M1 schema v1 或审计事务语义。
- 不承诺复原 M1-O 未记录的精确调用顺序、原始内容、真实等待时间和过往历史耗时。

### 0.3 关键工程原则

**事实优先级：业务已提交事实 > 强约束审计 > 可选诊断 Trace > 明确标记的投影。** 注意“业务已提交事实”常常是**最终快照**，不可把它冒充历史帧当时的事实。业务表用于验证和终态对照；历史过程主要来自带可追溯来源的历史事件。

所有时间帧、图上的节点和转移均必须对应可解析的 `source_ref`，或明确标识为 `INFERRED` / `UNKNOWN`。不能用补动画掩盖数据空洞。

## 1. 交付成果与范围

| ID | 交付 | 必须结果 |
|---|---|---|
| R0 | 基线能力审计 | 确定现存 audit/trace 的字段、提交时机、时间精度、排序与缺失边界；记录文档 |
| R1 | Replay Builder | 从现有只读快照生成规范化、确定性的有序回放事件 |
| R2 | State Projector | 通过事件增量投影各帧节点、任务、证据与异常状态，支持任意帧访问 |
| R3 | Replay Bundle JSON | 稳定 schema、来源闭包、截断与不确定性说明、可供 Agent 读取 |
| R4 | 中文 HTML Player | 无网络依赖、打开即用，播放/暂停/前后单步/跳转/调速/异常导航/证据检查 |
| R5 | 示例、测试和文档 | 六个 M1-O Mock 场景、完整回归、中文操作说明与开发交付报告 |

**最小验收叙事**：用户执行 `observe replay --run-id RUN_ID --output run-replay.html`（命令名最终需核对现有 CLI 约定），双击文件，选择播放；流程从创建→候选生成→校验→入队→提交→查询→结果门控按有证据的事件逐步变化；在 `UNKNOWN` 或 `FAILED + POLLING` 时能定位异常、查看来源，不发生任何数据库写入或外部请求。

## 2. 与现有 M1-O 的架构关系

```text
M1 committed facts / audit / optional observability_traces
                          │
                 Existing read-only queries
                          │  same read transaction / bounded snapshot
                          ▼
                   Normalized Evidence
                          │
                  Replay Builder (new)
                          │ ordered frames + uncertainty
                          ▼
                  State Projector (new)
                          │ frame states + diff + source refs
                          ├─────────────────┐
                          ▼                 ▼
                 Replay JSON bundle    Chinese HTML Player
                          │                 │
                  Codex / diagnostic    Human inspection
                     (read only)        (local browser)
```

- 优先扩展 `src/alpha_platform/observability/`，不让 `workflow.py`、`persistence.py` 依赖 replay。
- 从 `queries.py` 提取/复用**同一个已提交一致性快照**，避免 Replay JSON 与 HTML 使用不同查询时点。
- `graph.py` 现有最终图仍可保留，但**不能**直接把最终状态填入每一回放帧。
- `diagnostics.py` 的脱敏、证据引用及字节预算约束必须继承；不得另写一套弱化的安全过滤。
- `html_report.py` 可扩展或封装 `replay_html.py`，由 Codex 根据代码结构决定；禁止用大规模无关重构换取 UI。
- 无新增业务数据库表是默认方案。必要时只调整可选 Trace 的未来记录字段，必须另写动机与兼容测试；**不可声称修补历史空缺**。

## 3. R0 — 实施前基线审计（必须先完成）

在动代码前核查并写入 `docs/M1O1_REPLAY_BASELINE.md`：

1. `queries.py` 的 committed snapshot 获取方式、数据库只读属性、busy timeout、events/jobs/attempts 上限、`source_ref` 解析；
2. audit 与 optional trace 的字段、关联 ID（run/job/candidate/attempt）、时间分辨率、相同时间内排序规则；
3. `traces.py` 实际 emit/flush 时机和缓冲区丢失、retention、trace off/light/full 的区别；
4. 状态转换图、可访问的历史审计事实、哪些只是最终 `runs/jobs` 快照；
5. 现有 `diagnostics.py` 的 JSON 字节预算、证据闭包与导出文件保护策略；
6. 当前 HTML 的 XSS 防护、静态 JS 能否在无网络情况下加载、本地浏览器验证路径；
7. 六个示例真实可追溯事件分别具备哪些 replay 信息，是否可能只有终态及少量审计。

输出“可严格复原 / 只能推导 / 根本无法知道”的三列表，尤其检查不同事件源之间**同一秒内并无可信全序**。原有源内自增 row ID 可提供**源内顺序**，不能当成跨源因果顺序。

审计发现与本规格冲突时，Codex 应遵守“不编造事实”，记录限制并采取保守表现；不得偷偷更改 M1 核心行为。

## 4. Replay 数据契约

建议新增 `observability/replay.py`、`observability/projector.py`；如模块命名与现有结构冲突，可作合理调整并记录。

### 4.1 EventFrame v1（规范化事件）

```json
{
  "schema_version": 1,
  "frame_id": "frame:000012",
  "ordinal": 12,
  "occurred_at": "2026-10-31T08:30:00Z",
  "time_precision": "second",
  "order_quality": "SOURCE_ORDER_ONLY",
  "node_id": "simulation",
  "event_kind": "SIMULATION_SUBMITTED",
  "label_zh": "回测请求已提交",
  "effect": "NODE_COMPLETED",
  "subject": {"run_id": "...", "job_id": "...", "candidate_id": "..."},
  "evidence_refs": ["audit:42"],
  "evidence_level": "RECORDED",
  "payload_summary": {"result": "已提交", "remote_id_present": true},
  "warnings": []
}
```

以上字段仅是目标契约示例，事件种类须依据实际 M1-O 记录映射，不可凭示例创造回测事实。建议字段语义：

- `frame_id`：同一次导出稳定编号，不要求与数据库天然 ID 相同。
- `ordinal`：严格单调、0 起始或 1 起始均可，但 schema 必须固定。
- `occurred_at`：原始时间；缺失则 `null`，不能造时间戳。
- `time_precision`：`second` / `millisecond` / `microsecond` / `unknown` 等真实精度。
- `order_quality`：`CONFIRMED` / `SOURCE_ORDER_ONLY` / `TIE_UNCERTAIN` / `UNKNOWN`。
- `node_id`：稳定英文机器 ID；屏幕显示中文映射。
- `evidence_level`：`RECORDED` / `INFERRED` / `UNKNOWN`；推导必须附 `inference_reason` 与依据。
- `payload_summary`：脱敏、允许字段、字节和深度受限；不能直接塞原始 Trace 或 Adapter 响应。

### 4.2 有序回放与因果边界

1. **优先使用真实显式序列/因果关联**（如单源 row ID、attempt 与 job 关联），不构造虚假的跨源 happens-before。
2. 跨源时间戳可用于展示排序，但同一时间精度内存在冲突时必须显式警告；可以选择稳定展示次序以便重复播放，**稳定 ≠ 已确认真实发生顺序**。
3. 对 start/end 配对，只有存在可靠 ID 或可验证的配对键时才匹配；不以“最近一个同名事件”猜测并发 span。
4. 事件缺失时不合成“Validator 通过”或“Agent 完成”等成功事件；若最终态暗示某流程已发生，只能渲染为推导状态并标记来源、不能附虚假的步骤耗时。
5. 终态（业务快照）只在查看“最终快照”或用于一致性验证时可用；不能提前泄露到早期帧。
6. 如果 trace 结束 flush 导致同一事件物理入库时间晚于业务审计发生时间，采用原本的事件时间与明确时间来源，绝不能按入库时间冒充调用顺序。

### 4.3 `ProjectedFrame` v1

```json
{
  "ordinal": 12,
  "active_node": "simulation",
  "node_states": {
    "generator": {"state": "COMPLETED", "basis": "RECORDED", "refs": ["trace:8"]},
    "validator": {"state": "UNKNOWN", "basis": "UNKNOWN", "refs": []},
    "simulation": {"state": "SUBMITTED", "basis": "RECORDED", "refs": ["audit:42"]},
    "result_gate": {"state": "NOT_OBSERVED_YET", "basis": "RECORDED", "refs": []}
  },
  "transitions": [
    {"from": "queue", "to": "simulation", "basis": "INFERRED", "refs": ["audit:42"]}
  ],
  "anomalies_at_frame": [],
  "data_gaps": ["validator completion trace is unavailable"]
}
```

注意 `NOT_OBSERVED_YET` 表示“截至当前游标没有看到证据”，不是证明“实际尚未发生”。UI 必须把两种含义区分清楚。

### 4.4 投影算法约束

- 采用 `initial_state + apply(frame)` 纯函数/无副作用 reducer；绝不能查询最终业务状态来回填历史中间帧。
- 状态词汇明确限定：`NOT_OBSERVED_YET`, `ACTIVE`, `COMPLETED`, `REJECTED`, `FAILED`, `WAITING`, `UNKNOWN`，必要时增加 `INFERRED_*` 或 `SUBMITTED`，但不得和 M1 原生 Run/Job 状态混淆。
- 回退/任意跳转应确定性复算或使用已验证的间隔快照缓存；不通过反向修改数据库。
- 同一帧重复播放、暂停恢复、拖动跳转得到相同状态；不得对同一事件重复增加计数。
- `anomalies` 应仅由截至游标有证据的条件产生；终态才出现的异常不得在早期帧提前出现。
- 对 `UNKNOWN`、`FAILED run + POLLING job`、预算变化、提交意图、重试均保留来源引用；不推断 remote ID。
- 单步帧粒度默认按**观测到的事件**，不是固定每秒插值，不伪造中间帧。

### 4.5 ReplayBundle v1

必须至少包括：

```json
{
  "schema_version": 1,
  "run_id": "...",
  "generated_at": "...",
  "locale": "zh-CN",
  "mode": "HISTORICAL_REPLAY",
  "frames": [],
  "frame_states": [],
  "topology": {"nodes": [], "edges": []},
  "evidence": {},
  "final_snapshot": {},
  "warnings": [],
  "limits": {"truncated": false, "event_limit": 200, "byte_limit": 262144}
}
```

- JSON 的稳定机器键、ID、枚举保留英文；仅人类 label/description 使用中文。
- 所有 `evidence_refs` 必须在同一 bundle 中可解析，或带 `missing_reason` 明确不可解析；禁止悬空引用静默通过。
- 使用现有 `safety.py` 进行内容缩减/脱敏。复用现有原子写入、防覆盖数据库及侧车文件规则。
- 达到 `--max-events` / `--max-bytes` 时标记 `EXPORT_TRUNCATED` 和丢失范围；若无法保证 JSON 闭包/格式，则清晰失败，不能静默裁剪成看似完整的故事。
- 允许 `frames` 少于既有 audit/trace 记录，但需要在 `warnings`/`limits` 中解释过滤、采样和截断。
- 同一持久化快照与相同导出选项下，忽略 `generated_at` 等导出元数据，frames 和 projection 必须 byte-stable 或结构等价稳定。

## 5. R2/R3 — 中文 HTML 回放工作台

### 5.1 本地化规范

- 默认 `zh-CN`、`<html lang="zh-CN">`、页面标题“Alpha 研究执行回放”。
- 所有用户可见的导航、节点名称、状态、警告、按钮、空状态、时间线详情和帮助文本中文化。
- 原始机器 ID / event enum / source_ref 可在“技术详情”中保留英文，便于 Codex 排查。
- 英文底层异常代码给出中文解释；不存在已知解释时显示“未知错误代码：CODE”，不杜撰原因。
- 本地显示时间需清晰标出时区（建议显示 UTC 或明确指定时区）；UTC 原始时间保持在技术详情。
- 基本键盘与无障碍：控制按钮可聚焦、有 aria-label；避免仅靠颜色表达状态；缩小屏幕仍可使用。

### 5.2 页面布局（桌面优先）

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│ Alpha 研究执行回放  Run ID  | 历史/证据缺口提示 | 最终态 | 导出信息            │
├───────────────────────────────────────────────────────────────────────────────┤
│ 控制器 ─→ 候选生成 ─→ 校验 ─→ 去重/预算 ─→ 任务队列 ─→ 模拟执行 ─→ 结果门控 │
│           当前节点高亮 / 经过状态 / 失败标记 / 数据流箭头                      │
├─────────────────────────┬─────────────────────────────────────────────────────┤
│ 事件时间线              │ 当前帧检查器：输入/输出摘要、依据、状态变化、证据    │
│ [异常] / [全部]         │ 不可观测信息、原始事件类型/source_ref（可展开）     │
│  点击任意事件跳转       │                                                     │
├─────────────────────────┴─────────────────────────────────────────────────────┤
│ [回到开头] [上一步] [播放/暂停] [下一步] [最后] 速度 0.5×/1×/2×/4×         │
│  ●━━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  帧 12 / 37       │
└───────────────────────────────────────────────────────────────────────────────┘
```

节点图是**研究工作流拓扑**，不是 CPU/函数调用分析器。对于未执行或没有证据的节点，必须使用“尚未观察到”而非“必定未执行”。

### 5.3 播放行为

- 初始打开建议停在 `frame 0`（开始/初始视图），不得默认直接展示终态覆盖全部节点。
- 播放 / 暂停 / 重播；单步前进/后退；跳转开始/末尾；滑块拖动到任意帧；事件列表点击定位。
- 速度 0.5×、1×、2×、4×。**播放速度是 UI 演示节奏**，非真实 Wall Clock 的承诺。
- 默认按每帧固定展示间隔（例如 800ms / 1×）；可选“按真实记录时间间隔播放”，仅在精度足够且明确警告压缩/截断条件时启用，不是首版必做。
- 页面切换/暂停时清理定时器，避免多个并发播放循环或跳帧。末尾停住，除非用户明确选择重播。
- 帧 0 / 空事件：仍可显示 Run ID、最终快照（明确独立标签）和数据不足说明，但不可凭最终快照生成回放。
- 在异常事件自动突出显示相关节点；提供“上一个异常 / 下一个异常”跳转，且跳转只针对已有证据的异常帧。
- 事件筛选只影响列表可见项，不能用过滤后的事件子集重新投影业务状态；时间游标仍使用原始全量帧索引。

### 5.4 节点与证据检查器

点击节点或事件显示：

1. 当前帧状态、可信度（已记录 / 推导 / 信息不足）。
2. 最近状态变更、source_ref、时间精度与排序不确定说明。
3. 输入/输出**允许列表摘要或引用**；未记录则明确写“未采集，无法回放”。
4. 本帧状态差异（与上一帧相比），及 Run/Job/预算关系（只有历史证据支持时才显示为历史事实）。
5. 相关异常、下一步可能检查的**只读证据链接**；不提供“重试、恢复、退款、取消、提交”的操作按钮。

**安全**：所有字符串按 textContent/安全 DOM API 渲染；不把研究表达式、trace 文本、JSON 内容直接插入 HTML。嵌入 JSON 时按已有安全序列化策略逃逸 `</script>`、`<`、Unicode 行分隔符等。严格限制 HTML 文件大小/节点数以免卡顿。离线运行不依赖任何网络。

### 5.5 可观察数据缺口提示

页面必须能解释：

- `trace off`、旧 run、进程死亡时尚未 flush 的诊断缓冲可能缺失。
- audit 仅秒级时间精度，跨源同秒事件的实际先后不能保证。
- 此回放基于事件，不是屏幕录制，也不是重新运行。
- 未记录的原始 LLM Prompt / 模型隐式推理 / Adapter 原始响应不会出现在检查器中。
- 截断发生时突出“部分事件未导出：当前回放不完整”，不得把缺失流程涂成成功。

## 6. CLI、文件输出与兼容性

建议（以当前 CLI 实际调用结构校准）：

```powershell
# 生成具有完整回放交互的离线中文 HTML
uv run --offline alpha-platform observe replay --run-id RUN_ID --output run-replay.html

# 导出同源的结构化回放数据（给 Agent/Codex）
uv run --offline alpha-platform observe replay --run-id RUN_ID --format json --output run-replay.json

# 指定数据库：--db 的实际位置沿用现有约定
uv run --offline alpha-platform --db path/to/db.sqlite3 observe replay --run-id RUN_ID --output run-replay.html
```

如果现有 `observe report` 已与 HTML 逻辑高度耦合，允许复用其参数或新增 `--interactive-replay`，但需保留旧 `observe report` 的既有行为，文档提供最终准确命令；禁止随意破坏 `observe show/timeline/graph/inspect/export/report`。

要求：

- `replay` 读取 `mode=ro` + `query_only` + consistent snapshot；**不触发 migrate、表初始化、flush、retention 删除或任何写**。
- 文件输出路径使用现有 protected output / atomic write 机制，不覆盖业务 SQLite 与 sidecar。
- 报告独立打开；离线 JavaScript 仅负责 UI 状态控制，不访问网络/本地数据库、不读取浏览器凭证。
- 提供 `--max-events` / `--max-bytes` 兼容现有观测上限；必要时增加 HTML 大小上限和清晰错误。
- 与 M1-O 的诊断 JSON 对同一底层 snapshot 的事实、event IDs、source_ref 一致；允许为回放新增 schema，不静默更改旧 schema v1 意义。

## 7. 对 Agent 可读诊断的要求

人类 HTML 与机器 Replay JSON 必须调用同一个 `build_replay_bundle`（或者等价共享纯数据构建逻辑）。不要让 JS 自己从可视化图案反推出事实。

Agent 可在后续拿到：

- `frame_id`, `ordinal`, `event_kind`, `node_id`；
- 每帧可确定的状态变更和证据等级；
- `evidence_refs` 与证据闭包；
- 明确的排序不确定/缺失/截断声明；
- 异常首次出现的帧及相关上下游节点；
- 最终业务快照与历史投影的差异（若不一致，只报告，不自我修复）。

禁止生成“LLM 猜测出来的根因”并将其标记为事实；可选纯规则的 `diagnostic_hints` 需列明规则 ID 和证据，不制造因果。此阶段**不接入自动诊断 Agent，也不消费 token**。

## 8. 安全、可靠性和性能预算

### 8.1 安全与只读

- 不泄露 API key、Authorization 头、环境变量凭证、原始敏感响应或隐藏模型推理。
- 全部导出再过统一 sanitizer；对用户可控文本、表达式、异常字符串、JSON、HTML 注入进行测试。
- JS 不包含 `fetch`、WebSocket、XHR、CDN、第三方脚本/样式；无 analytics beacon。
- 只读数据库连接、导出路径保护和缺表兼容均延续 M1-O。
- 报告不能修改任何 Run/Job、预算、审计、模拟与追踪记录。

### 8.2 大小上限

沿用 M1-O 当前上限作为基线：默认最近 200 事件，CLI 最多 2000；证据 bundle 默认约 256 KiB，上限约 4 MiB；Trace 读取上限、字符串长度、层级深度与 sanitizer 已有限制。Replay 可能增加每帧状态大小，应控制空间增长：

- 允许 JSON 输出保存 `events + compact deltas + optional checkpoints`，而非无界复制所有节点状态；可由前端确定性投影，须与 Python projector 有一致性测试。
- HTML 在 2000 帧时仍应可操作；优先事件列表虚拟化/分批 DOM 更新或简单限制可见数量，不引入框架。
- 如果为控制大小截断，必须保留证据闭包并声明缺口；不允许静默扔掉失败原因。
- 在相同输入下生成相同有序事件和状态（不含导出时刻等非业务元数据）。

### 8.3 错误与降级

- 没有可回放事件：显示中文“没有足够的历史事件，无法逐步回放”，允许打开最终报告/证据。
- 不支持的旧数据库：安全报错/只读降级，不自动升级。
- 缺失 source_ref：明确显示“证据缺失”，不隐藏。
- trace 数据污染或异常格式：拒绝/跳过且警告，不令报告产生虚假的成功状态。
- 生成失败不影响原 M1/M1-O 研究流程；不加旁路回调到业务代码。

## 9. 必须覆盖的测试矩阵

| 类别 | 具体场景 | 通过条件 |
|---|---|---|
| 基线兼容 | 原 86 项用例 | 无回归，`observe` 旧命令行为不变 |
| 中文本地化 | 标题、控件、节点、异常、空态 | 中文显示，原始枚举仍可查 |
| 确定性 | 同库同参数重复生成；跳转前后再跳回 | 事件顺序、帧状态相同 |
| 历史隔离 | 早期帧 vs 最终 COMPLETED run | 不提前展示终态成功 |
| 事件缺失 | trace off、旧 run、无 generator span | 不补造节点输出/完成事件；标识未知 |
| 排序歧义 | 跨源同秒时间戳，可靠源内序 | 稳定可播放，显示不确定标签 |
| 回放控制 | 首帧/末帧、播放暂停、步进、滑块、速度 | 控制行为与状态一致；无重复 timer |
| 异常定位 | validation rejected / budget exhausted / UNKNOWN | 定位到真实异常帧及源证据 |
| Run/Job 冲突 | FAILED + POLLING | 展示异常与预算，无自动恢复 |
| 多种流程 | success、duplicate、persistence-failure | 六个既有 Mock 场景均有合理回放或明确缺口 |
| 证据闭包 | missing/trimmed `source_ref` | 均可解析或显式 missing_reason |
| 故障注入 | malformed trace、超限 payload、损坏 JSON | 安全失败/警告，不误报业务状态 |
| XSS/隐私 | `</script>`、HTML 事件属性、密钥样式文本 | 不执行注入，不泄露 secret |
| 只读 | DB 只读、缺 trace 表、mtime/hash 对照 | 不触发迁移/写库，不影响 M1 任务 |
| 限额 | 2000 事件、最小/最大字节预算 | 有边界、截断说明、有效文件或清晰报错 |
| 浏览器 | Chrome headless 本地离线打开 | 交互控件可用、无网络依赖、截图人工复核 |

**测试禁止只断言生成了 HTML 字符串**。至少包含一组通过浏览器脚本/现有 Chrome headless 能验证的播放、暂停、单步和节点检查行为；若环境不能自动控制浏览器，提供可复现的手工验收步骤并如实报告未自动验证的部分。

### 9.1 UI 手动验收清单

1. 打开成功样例，页面全中文。初始停在起始帧，没有把最终节点提前显示为成功。
2. 点击播放，节点依次变化；中途暂停、倒退一步、改变速度、拖到中间、继续播放，状态均一致。
3. 点击任意事件，右侧显示该帧的中文描述、证据来源、可靠性；切换技术详情可看到原始 event/source_ref。
4. 打开 UNKNOWN 样例，检查没有捏造 remote ID，预算/提交意图依据明确；跳转异常有效。
5. 打开 persistence-failure 样例，显示 FAILED Run + POLLING Job、记录缺口与人工检查说明；无恢复/扣款按钮。
6. 打开 trace off 旧 Run，展示历史不足而不是伪造完整回放。
7. 断网且禁用 CDN 后仍可以使用全部播放操作。
8. 注入 `<script>` 文本作为事件数据，界面只显示文本，绝不执行代码。

## 10. 任务拆解与交付门槛

### R0 — Baseline & contract

**改动**：`docs/M1O1_REPLAY_BASELINE.md`；确认输入字段和 API。  
**验收**：出具历史可复原矩阵，标识审计、trace、终态快照差异。不修改业务模块。

### R1 — Replay Builder

**改动**：新增只读 Replay Builder 和 schema / 来源映射。  
**验收**：确定性、同秒歧义、缺失/截断、证据引用测试通过。

### R2 — State Projector

**改动**：纯投影器与状态 delta/帧浏览接口。  
**验收**：跳转与线性回放一致；早期帧无终态泄漏；UNKNOWN / FAILED+POLLING 测试通过。

### R3 — Machine Replay Export

**改动**：JSON bundle、CLI export 与现有 sanitization/output guard 集成。  
**验收**：JSON 稳定、闭包、字节上限、脱敏、只读测试通过。

### R4 — Chinese Replay HTML

**改动**：中文 UI、工作流图、时间线、检查器、播放控件；集成 CLI。  
**验收**：本地浏览器打开可交互，不依赖外网；旧 `observe report` 不变。

### R5 — Samples, browser QA, docs

**改动**：扩展 `scripts/generate_observatory_samples.py`、中文 `docs/REPLAY_STUDIO.md`、README 说明、自动/手工验收证据。  
**验收**：至少六组示例可打开；完整 pytest/ruff pass；列出实际支持和历史不可复原的信息。

每个阶段都应做到**可测、可提交、易回滚**，不要等所有前端完成才测试事件真实性。

## 11. 交付清单

Codex 最终必须提交：

1. `docs/M1O1_REPLAY_BASELINE.md`：源码事实核查与数据缺口清单。
2. Replay Builder / Projector / schema（实际模块名在交付报告说明）。
3. 中文离线交互 HTML Viewer、CLI 命令和 Replay JSON 导出。
4. 基于六个 Mock 场景的可复现本地示例及 manifest，不能假造历史。
5. 对应自动化测试、至少一个浏览器交互验证结果和截图（若运行环境支持）。
6. 中文使用文档 `docs/REPLAY_STUDIO.md` 与 README 更新。
7. 变更清单、测试结果、已知限制和下一步建议。

禁止将 `examples` 的模拟指标称作真实 Alpha 绩效。

## 12. Definition of Done（完成定义）

只有满足以下条件才算 M1-O.1 完成：

- [ ] M1 原业务流程和全部 M1-O 测试保持通过；无业务状态图/预算规则变化。
- [ ] 全中文离线回放，支持播放、暂停、前后单步、跳转、调速、异常定位。
- [ ] 状态随游标变化，不能提前泄露最终态；“已记录/推导/未知”明显区分。
- [ ] 能查看每帧节点输入输出允许字段摘要、状态变化与可追溯证据。
- [ ] 机器 Replay JSON 和人类 HTML 来自同一可信数据源，证据引用闭包成立。
- [ ] 旧库/trace off/同秒歧义/丢失缓冲/截断都诚实表达。
- [ ] 无数据库写、外部调用、敏感泄漏和 XSS；保留导出路径保护。
- [ ] 六个离线场景可复现，测试及浏览器交互验收完成。
- [ ] 完整报告：`uv run --offline pytest -q`、`ruff check .`、`ruff format --check .`，报告准确数量。

## 13. 可直接发送给 Codex 的实施指令

> 请阅读 `docs/M1O1_RESEARCH_REPLAY_STUDIO_SPEC.md` 及现有 `docs/M1O_RESEARCH_OBSERVATORY_SPEC.md`、`docs/OBSERVATORY.md` 和源代码。  
> 正式启动 **M1-O.1：Research Replay Studio**。严格按 R0→R5 顺序实施，首先完成实际仓库基线审计。  
> **核心优先级：真实可追溯的历史状态回放 > 中文可用性 > UI 动画效果。**  
> 保持只读、离线、零新依赖，不得改动 M1 业务行为，不做实时 WebSocket、真实 API、M2 任务或自动恢复。  
> 特别注意同秒跨源排序不确定、Trace 缺失、终态提前泄漏、source_ref 闭包、XSS/脱敏与文件写出保护。  
> 增加回归测试与至少一项真实浏览器交互验证，最后输出变更文件、测试结果、限制与下一步工作。  
> 如实际源代码能力不足以支持某种历史动画，不得模拟未记录事件；明确呈现“无法复原”，并在交付报告说明。

---

**版本关系**：M1 → M1-O（已交付，静态可视化与诊断）→ **M1-O.1（本文件，中文历史回放）** → 后续可选 M1-O.2（实时增量追踪）→ M1.5（真实 API，需另行核验文档与授权）→ M2–M4。后续路线不因本阶段自动启动。

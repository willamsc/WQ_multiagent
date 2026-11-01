# M1.5 人工回测与滚动批次

## 范围和协议证据

本次授权：人工触发 WQ 回测最小闭环，借鉴用户程序的多 Alpha × 多批次方法。
不增加 M2-B、研究策略、正式 Alpha Submission、MCP 服务或依赖。

仅阅读用户 `Alpha Machine.ipynb` 源单元和 `machine_lib.py`，没有复制、导入或运行。
Notebook 实际使用过 8×6、10×8、10×7；用户本轮明确要求支持 8×6、8×10。
参数表示每次请求 Alpha 数 × 同时在途批次工作槽，**不是平台额度证明**。
参考程序提供了 POST `/authentication`、POST `/simulations` 对象/数组、Location、
父任务 children、子任务 alpha、GET `/alphas/{id}` 的协议依据。
文档站 https://wqapi.hualabtech.com/ 本轮访问失败，不能声称已核验其接口规格。

## 模块边界

- `wq/config.py`：完整显式 settings 和一致性验证。本地字段/操作符白名单为人工声明，
  不是已从 WQ 核验的元数据。不支持的 FastExpr 仍由原 Validator 显式拒绝。
- `wq/transport.py`：stdlib HTTPS、内存 Cookie、请求超时、禁重定向、官方来源检查、
  响应大小限制。Basic 凭证仅用于固定官方认证端点；不保存 cookie 文件。
- `wq/adapter.py`：单任务协议、Retry-After、状态与有限 JSON 结果归一化。
- `wq/multi.py`：短窗口合并请求；不足一批仍提交，单个尾任务使用对象。
  短 HTTP 请求串行发送，但多个远端模拟批次同时运行。
- `wq/runner.py`：人工候选、受限批次工作槽、滚动补位；调用原 Controller。
  Adapter 不写数据库、不管理预算，不包含 Generator/Critic 自动研究逻辑。

每个 Alpha 独立 Run/Job，phase 为 M1.5，沿用状态、预算、事务与审计；schema 不迁移。
预算单位是一个 Alpha 回测接纳次数，不代表平台金钱费用。
指纹算法增加可选 backend identity，旧默认值不变；真实身份包括全部回测 settings。
完成标签为 LOCAL_RESEARCH_GATE，不将真实结果显示成 Mock 或正式提交资格。

## 效率和正确性

原程序等待一整组批次完成后才提交下一组；这里允许空出的批次工作槽立即补入下一批。
8×6 配置最多 6 个工作槽，每批最多 8 个候选；8×10 同理。去重、拒绝和限流可能形成
不足 8 个的实际请求，不保证请求总是装满。配置不能替代账户额度；其他进程共享账号时
需人工协调，不提供跨进程全局限流。

不按 children 顺序分配结果。读取子任务对应 Alpha 的表达式 AST 和回显 settings，
唯一匹配后才归档。真实接口验证发现模拟详情和 Alpha 详情均未回显 testPeriod，
因此只对这个已确认缺失的字段豁免回显要求，并记录 WQ_TEST_PERIOD_NOT_ECHOED。
若返回了不同的 testPeriod，或其他设置缺失/不同，仍拒绝结果。请求中的完整设置仍参与
指纹与审计；不声称已验证远端实际采用的测试期。证据缺口也进入 Read Model。
缺失身份、重复匹配、异常 JSON、不支持状态和非有限指标均保守停止。
父任务完成结果与子结果共享缓存；正常 2 个 Alpha 批次只需 1 次 POST + 5 次 GET。
不根据缓存补造历史事件。没有实测真实加速倍数。

提交超时、非 201、缺失或不安全 Location 不重 POST，原 Controller 标 UNKNOWN。
仅明确 429 可按 Retry-After 和 max_submit_attempts 有限重试，会话级冷却阻止其他 HTTP
请求穿透。GET 的 429 或瞬时网络错误最多重试 3 次，不将只读重试扩展到 POST。
Retry-After 支持秒和 HTTP 日期；非法或超过
300 秒停止操作，不缩短后强行重试。
接纳前 UNKNOWN 保留预留；明确回执后计 used_budget，查询未知不重复计费。
UNKNOWN 后停止补入新批次，已接纳批次继续收尾。CLI 显示 not_started_count；未开始候选
不伪造 Run 或执行事件。

持久化回执含父 URL、表达式和完整 settings，新 Adapter 可查询原任务，不依赖进程内数组
下标。查询能力不等于自动恢复或业务对账；终态仍冻结供人工核查。

## 使用

项目根目录 `.env.wq` 已被忽略，只填写以下两项；支持无引号或成对的单/双引号：

```text
WQ_USERNAME=你的账号
WQ_PASSWORD=你的密码
```

文件仅去掉成对外层引号，不转义、不插值、不删除引号内空格。同名环境变量优先于文件，
其值按原样使用。不在命令参数或聊天中填写密码。
认证不通过或需要人工验证时停止，不自动重新登录或绕过验证。

```powershell
# 仅认证
uv run --offline alpha-platform wq probe
# 首次真实联调：先确认地区、股票池、字段和操作符属于本账户权限
uv run --offline alpha-platform --db examples/wq-live.sqlite3 wq run --config configs/wq-live-example.json --expressions examples/wq-single.json --max-simulations 1 --batch-size 1 --concurrent-batches 1
# 确认批量权限之后：input.json 为最多 48 个表达式的 JSON 数组
uv run --offline alpha-platform --db research.sqlite3 wq run --config configs/wq-live-example.json --expressions input.json --max-simulations 48 --batch-size 8 --concurrent-batches 6
# 8×10：改为 --max-simulations 80 --batch-size 8 --concurrent-batches 10
uv run --offline alpha-platform --db examples/wq-live.sqlite3 research show RUN_ID
# 只读核查原远端任务，不修改冻结的本地状态/预算、不重新提交
uv run --offline alpha-platform --db examples/wq-live.sqlite3 wq inspect --run-id RUN_ID
uv run --offline alpha-platform --db examples/wq-live.sqlite3 observe replay --run-id RUN_ID --output examples/wq-replay.html
```

max-simulations 是整个输入的硬上限，不是 HTTP 次数；输入在认证前完成本地校验，
超限直接拒绝。每个 Alpha 的 budget_limit 默认为 1，设 0 拒绝提交。
默认 trace light，复用原缓冲、脱敏和截断机制，可关闭或设 full。

testPeriod 是测试/验证期配置，与批量大小或执行耗时无关。本轮请求使用参考程序的 P0Y；
没有验证省略参数后的服务端默认值，故没有擅自从请求删除，也不将它等同于“全部历史”。
其精确研究语义及后续训练/验证策略尚未纳入本轮真实验收。

## 限制

- 未实测真实批量额度和吞吐量；示例 settings 不保证所有账户有权限。
- 子任务失败且没有表达式身份时保守 UNKNOWN，不猜测归属；部分子结果异常可能影响
  整个父任务的身份解析，没有自动修复/重提。
- 远端接纳与本地回执落库之间进程退出仍有证据窗口；SUBMITTING/UNKNOWN 人工核查。
  没有后台 Worker、自动恢复或人工结算写入口。
- 不自动实现认证交互、人脸验证、session 恢复；线程取消不代表远程取消回测。
- 回放只展示已记录单 Alpha 事实，没有虚构批次时间线或服务端耗时。

实际验收见 [交付记录](M15_DELIVERY.md)。

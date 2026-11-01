# 008 · M2-B 最小 Generator–Critic 研究闭环

用户正式授权 M2-B。先核对 M2-A 源码与 211 项全量基线，不重构已存在 Runtime/Backend/
角色/AgentLedger。保留前轮尚未提交的 M1.5，不修改其核心状态/预算/去重。

新增 research_loop：Controller、策略/确定性校验、不可变版本/审查、有限修复、二阶段
独立审查与全部旧缺陷复验、最小批准目录 Evidence Gateway。附加表位于 AgentLedger，
原业务数据库与 schema 不变。研究执行错误和研究 FAIL 分开，路由冻结，无自动回测。

ContextBuilder 必要增加受控 repair/regression 正式资料视图；修复 tuple 不递归检查
敏感字段的问题。新版独立 Critic 不得看到旧审查，复验前独立结果必须先锁定。
新增 study run/show、observe study，复用只读证据、脱敏与安全导出，无 Replay 重构。

实际 Mock 演示 APPROVED，2 版本/3 审查/5 调用；真实 CLI 预检 Codex 已安装/本地登录但
隔离未验证，Claude 未安装，DSH 未确认；真实研究 0 次，不伪造成功。
最终检查与限制见 [交付报告](../../../docs/M2B_DELIVERY.md)，
操作见 [研究闭环](../../../docs/M2B_RESEARCH_LOOP.md)。
基线详见 [核查](../../../docs/M2B_BASELINE_AUDIT.md)。不启动 M2-C/D。

最终完整 pytest **244 passed（原 211 + 新 33）**，36.41 秒；ruff check 通过，
ruff format --check 通过（98 文件）；git diff --check 无空白错误。
原 Attempt 迟到 fence 测试继续通过，研究层另测超时迟到不写审查、调用预算不可追加，
多轮所有旧缺陷必须复验、批准门控、版本与审计原子回滚及跨进程 CLI 查询。
开发验收时未提交或推送 Git，没有读取真实 WQ 凭证或发起 WQ 回测。
用户随后授权同步当前 M1.5 与 M2-B 改动至 GitHub main；遵循普通提交/推送，保留远程历史。
实际提交号与同步结果以 Git 为准。

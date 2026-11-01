# 开发上下文索引

用途类似 skill 的渐进加载：先读本页及 [当前状态](CURRENT.md)，需要细节时再进入
下级目录。它是仓库开发记忆，不是安装到全局的 Codex skill。

| 记录 | 简要说明 | 详情 |
|---|---|---|
| 001 · M1 | 实现 Mock 单候选闭环、预算、校验、SQLite、CLI；30 项测试通过 | [详情](changes/001-m1/DETAILS.md) |
| 002 · 正确性审查 | 修复并发接纳、UNKNOWN 去重、终态写入、校验和 Adapter 问题；61 项通过 | [详情](changes/002-correctness-review/DETAILS.md) |
| 003 · M1-O | 只读观察器、可选 Trace、JSON/离线 HTML；86 项通过 | [详情](changes/003-observatory/DETAILS.md) |
| 004 · M1-O.1 | 中文历史回放、纯投影器、浏览器验收；117 项通过 | [详情](changes/004-replay-studio/DETAILS.md) |
| 005 · 仓库与上下文 | 建立分层开发记录，源码与文档首次推送 GitHub main | [详情](changes/005-github-context/DETAILS.md) |
| 006 · M2-A | 统一 Runtime、独立角色/Context、Mock 后端、版本与尝试账本；171 项通过，真实 CLI 受隔离门控阻断 | [详情](changes/006-agent-interop/DETAILS.md) |
| 007 · M1.5 | 人工 WQ 回测、8×6/8×10 离线验证和滚动补位；修复认证引号/结果缺字段兼容，真实单任务已完成但本地冻结未改写 | [详情](changes/007-wq-simulation/DETAILS.md) |
| 008 · M2-B | 主信号版本、独立审查、有限修复/二阶段复验、证据网关及只读观察；244 项通过，真实模型仍受门控 | [详情](changes/008-research-loop/DETAILS.md) |

后续记录沿用 `changes/009-简短主题/DETAILS.md`。每条索引控制在一两句话；详情
记录背景、改动文件、取舍、实际验证、风险、后续事项和可用的提交号。

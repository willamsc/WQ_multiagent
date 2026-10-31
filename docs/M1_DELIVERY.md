# M1 交付记录

完成范围：无需外网或 WorldQuant 凭证的单候选研究闭环，包含生成、验证、
入队、模拟执行、指标门控、持久化与跨进程查询。

## 文件

- `pyproject.toml`、`uv.lock`、`uv.toml`：Python/uv、src 布局、pytest/ruff、项目内缓存。
- `.gitignore`、`.env.example`：本地产物忽略与无凭证环境示例。
- `src/alpha_platform/domain.py`：六类领域模型、请求/回执、状态枚举与配置约束。
- `src/alpha_platform/persistence.py`：SQLite schema v1、初始化入口、事务、约束、审计、报告。
- `src/alpha_platform/simulation.py`：异步 Adapter Protocol 和确定性 Mock Adapter。
- `src/alpha_platform/validation.py`：受控 AST 子集、规范化、版本化 SHA-256 指纹。
- `src/alpha_platform/workflow.py`：Controller、去重、预算、提交意图、结果本地重试。
- `src/alpha_platform/agents.py`：独立 Agent Runtime / Generator 接口及 mock 实现。
- `src/alpha_platform/config.py`、`cli.py`、`__init__.py`：配置读取、CLI 与包入口。
- `configs/sample-gbr.json`：显式 mock 字段、算子与研究设置。
- `tests/test_m1.py`：端到端与故障注入测试。
- `README.md`、`docs/ARCHITECTURE.md`：操作说明和架构取舍；原始规格保留。

## 验证结果

- `uv sync --locked --offline`：通过。
- `uv run --offline pytest -q`：**30 passed**。
- `uv run --offline ruff check .`：通过。
- `uv run --offline ruff format --check src tests`：通过。
- CLI 子进程测试验证：init-db → research run → 新进程 research show → 重复拒绝。
- 故障覆盖：校验拒绝、预算不足、表达式去重、限流重试/耗尽、提交超时未知、
  延迟完成/轮询耗尽、远程失败、入队事务回滚、结果写入回滚和幂等重试、持续落库失败。
- Windows 沙箱阻止 pytest 临时目录访问，最终测试在授权提权下运行；临时数据库
  路径仍为项目内 `.pytest-tmp`。未使用真实 WorldQuant 服务。

## 取舍与遗留

使用标准库 dataclass、argparse、sqlite3、asyncio 和 JSON，无运行时第三方框架。
Controller 控制业务状态和预算，Runtime 仅返回候选，Validator 不执行表达式，
Adapter 仅负责模拟接口。报告来自数据库事实，假设与原始 mock 结果分开保存。
SQLite 时间戳采用 UTC；报告耗时为秒级精度。

单次 run 只处理一个候选。校验器不是完整 FastExpr；不支持完整算子类型系统。
UNKNOWN 保留预算，不自动重发。失败/未知任务的同指纹候选也会被阻止重复入队。
持续落库失败保留 POLLING job 与 FAILED run，需要人工检查。只支持重启查询，
没有自动中断恢复、远程对账或多 Worker。无正式 Alpha 提交入口。

后续需先另行授权并核实 M1.5 的 API 文档、元数据、凭证及权限，再考虑真实模拟；
M2～M4 的研究流程、RAG 和长期调度均未启动。没有下载 embedding 模型。

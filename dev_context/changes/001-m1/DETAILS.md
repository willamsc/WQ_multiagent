# 001 · M1 最小闭环（2026-10-30）

实现标准库 dataclass / argparse / sqlite3 / asyncio、uv src 布局和 pytest/ruff。
Controller 管业务与预算；Mock Runtime/Generator 产出候选；Validator 使用受控 AST，
不执行表达式；Mock Adapter 提供离线异步模拟；SQLite 记录任务、结果与审计。
CLI 支持 init-db、research run/show。配置使用 JSON，避免 YAML 运行时依赖。

初次交付 30 项测试通过，随后正确性审查继续修复，不能将初版视为当前完整保障。
代码主要在 `src/alpha_platform/`，测试为 `tests/test_m1.py`。

完整记录：[M1_DELIVERY](../../../docs/M1_DELIVERY.md)、
[ARCHITECTURE](../../../docs/ARCHITECTURE.md)。未实现 live 或 M2～M4。

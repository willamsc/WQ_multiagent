# 003 · M1-O 观察器（2026-10-30）

先完成实际 schema/事务核查，再建立只读一致快照、事件契约、证据引用、派生图、
异常规则、JSON 与离线 HTML。可选 Trace 使用有界缓冲与独立事务，不替代业务审计。
off/light/full 不改变业务状态；不启动常驻服务。新增 `observability/` 与测试。

86 项测试和 ruff 通过；已有 Chrome 检查离线呈现。Trace 在运行结束后 flush，
可因进程死亡而丢失；历史精度不足不能还原精确调用顺序。核心 schema 仍为 v1。

完整记录：[M1O_BASELINE_AUDIT](../../../docs/M1O_BASELINE_AUDIT.md)、
[M1O_DELIVERY](../../../docs/M1O_DELIVERY.md)、[操作说明](../../../docs/OBSERVATORY.md)。

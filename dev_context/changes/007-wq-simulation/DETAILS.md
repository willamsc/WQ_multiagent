# 007 · M1.5 回测接入与滚动批次

用户授权人工真实回测简单闭环，并学习外部 Notebook/library 的多 Alpha × 多批次，
明确要求 8×6、8×10。不启动 M2-B 或正式 Alpha 提交。

新增 wq 配置、stdlib HTTPS session、单任务/批量 Adapter、滚动调度与 CLI。
沿用预算、去重、UNKNOWN、结果保存和回放，无 schema 迁移或新增依赖。
Validator 增加可选 backend identity；旧默认 Mock 指纹不变。
增加本地研究门控标签和中文说明，避免真实结果被称为模拟或平台提交获批。

参考文件仅阅读，不复制、不运行、不使用其中凭证。认证读取已忽略的本机文件或环境变量，
HTTP body、Cookie、凭证不记入日志和结果。

细节：[说明](../../../docs/M15_WQ_SIMULATION.md)、
[交付验证](../../../docs/M15_DELIVERY.md)。最终全量 211 passed，ruff check/format --check 通过。
401 根因是本地 .env.wq 解析未去掉外层引号，已修复并验证官方认证 201。
实际只提交 1 个 Alpha，远端 COMPLETE，取得真实指标；本地原任务 FAILED/UNKNOWN
没有被改写。发现并修复 testPeriod 未回显被判不匹配，原请求设置仍留存，缺失标记为
证据缺口。新增只读 wq inspect，禁止重提或自动恢复冻结状态。
8×6/8×10 真实额度和速度未测量，不声称真实自动闭环已全部验收。
本轮用户询问删除 testPeriod，因省略时服务端默认值未验证且属于研究设置，暂保留请求
参数，仅解除对其必须回显的依赖。后续批量联调须另行明确额度与任务数量。

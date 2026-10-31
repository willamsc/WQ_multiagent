# 项目开发约定

开始任务时，先阅读 `dev_context/README.md` 和 `dev_context/CURRENT.md`；再按任务
需要选择对应变更详情和规格，不要一次装载全部历史或论坛原始 JSON。

每次有实质改动时，同步维护开发记录：

1. 在 `dev_context/changes/` 下新建按序号命名的目录，编写 `DETAILS.md`。
2. 在 `dev_context/README.md` 增加简短索引：做了什么、验证结果、详情链接。
3. 更新 `dev_context/CURRENT.md` 的当前状态、约束和未完成事项。
4. 架构实质变化同时更新 `docs/ARCHITECTURE.md`。规格与完整报告仍以 docs 为准，
   上下文记录引用它们，不大量复制，不将计划或未执行检查写成已完成事实。

使用 Python、uv、SQLite、pytest；保持 Controller / Runtime / Validator / Adapter
职责独立。新任务以用户明确授权的范围为准，不因路线图自行启动下一阶段。
缓存、虚拟环境、浏览器 profile 和下载文件尽量留在当前项目内；不提交密钥、
数据库、缓存、浏览器 profile、大型论坛原始数据或可再生成的容量/XSS 夹具。
Git 推送不得强制覆盖远程历史；遇到已有远程提交先检查并保留其内容。

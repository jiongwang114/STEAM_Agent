# Steam Agent 当前实现文档

本目录是当前代码的实现文档唯一来源。文档描述应以当前工作树代码、测试和
本目录的最新记录为准；历史设计和旧任务清单不作为当前行为依据。

## 阅读顺序

1. [后端与前端接口契约](BACKEND_UI_CONTRACT.md)：前后端边界和组件关系。
2. [API 契约](API_CONTRACT.md)：前后端接口、错误结构和 SSE 事件。
3. [后端开发与部署说明](BACKEND_DEPLOYMENT.md)：启动、备份、测试和后台任务。
4. [前端页面与实施设计](FRONTEND_DESIGN.md)：页面结构、交互和验收状态。
5. [记忆架构优化技术文档](MEMORY_ARCHITECTURE_OPTIMIZATION.md)：长期记忆、摘要和历史查询。
6. [实施进度记录](IMPLEMENTATION_PROGRESS.md)：按时间追加的完成内容、测试结果和遗留问题。

## 权威规则

- API 字段、状态码和 SSE 事件以 `API_CONTRACT.md` 为准。
- 部署命令、运行配置和恢复操作以 `BACKEND_DEPLOYMENT.md` 为准。
- 当前完成状态以 `IMPLEMENTATION_PROGRESS.md` 和代码、测试结果共同判断。
- 修改接口或用户可见行为时，必须同步更新对应文档和测试。
- 根目录 `docs/` 中的训练材料不属于运行时实现文档。

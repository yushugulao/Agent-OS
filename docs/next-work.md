# 双目标科研 Agent 平台维护规则

本文档用于约束双目标平台的日常维护。当前分支同时维护两个目标：

- 未改动 uCore 目标：内核保持原样，科研 Agent 平台运行在普通用户进程和普通文件之上。
- AgentOS-uCore 目标：科研 Agent 平台保持同一场景和输出契约，但关键阶段使用内核 Agent 服务。

两个目标必须持续可比较。新增功能时，先保证对象名、角色名、run id、状态文件和 Host Reader 页面保持一致，再处理两个目标内部实现差异。

## 宿主机平台同步规则

宿主机上的科研 Agent 平台仍可能增加可见对象、API 和测试主题。做较大改动前，应重新查看宿主机平台最近新增的可见能力，并判断是否需要同步到 plain target 和 AgentOS target。

需要重点保持的能力包括：

- workbench 文件校验：稳定 artifact 用 size/SHA256 校验，live summary 文件按存在性校验。
- 真实 CSV 科研任务：保留主指标摘要、多组数值字段和分组字段统计。
- workbench answer：优先使用生成的 `report_md`，并把报告 artifact 放在引用列表前部。
- workbench/project zip：长文件名冲突时避免包内重复条目。
- project 页面：读取已保存的 handoff、runbook、evidence、release、snapshot、reproducibility 记录。
- Host LLM Relay：默认模板模式可离线运行；显式配置云端模式时优先使用 DeepSeek v4 pro，密钥只由宿主机 Relay 读取。

uCore 目标不逐行复制宿主机 Python 实现，但必须保留评委可见的平台概念、RUN-042 故事和比较词汇。

## 未改动 uCore 目标维护要求

- Web/UI 状态文件覆盖首页、运行详情、Agent 详情、证据详情、项目、数据、artifact、对比、bio、lab resource、publication、knowledge、runtime、provenance、API payload、action 和 side-effect 页面。
- 真实 artifact 操作可见，包括源输入文件、中间产物、报告正文、日志、图表数据、证据包记录和包内文件检查。
- 数据流水线可见，包括输入扫描、dataset snapshot、preview、quality check、transform、dataset collection 和真实任务报告记录。
- 工作流运行器可见，包括 stage DAG、依赖检查、失败记录、重试决策、缓存记录和每阶段日志。
- Host LLM Relay 文件协议保持可读，包括请求队列、路由表、packet schema 检查、guard 检查、fallback 决策、响应文件、质量记录和 Reader 页面状态。
- Agent 协作记录可执行，包括 orchestrator、retriever、analyst、reviewer、writer、recovery、auditor 的消息、决策和确认记录。
- provenance view 和 query 作为普通文件输出，包括 timeline、图边、证据包、查询模板、执行记录、比较记录、导出记录和评审包。
- AgentCompare 直观说明 plain target 的弱点：重复扫描、状态靠约定、权限靠用户态、Context 不可信、路径重建成本高、失败恢复步骤多。

## AgentOS-uCore 目标维护要求

- RUN-042 主阶段应真实使用内核服务，不能变成“普通平台旁边跑 Agent 测试”。
- `rp_agentos_mainflow` 是增强目标的主流程证据文件，记录可信 Context、metadata 查询、事件通知、通用恢复动作、audit/provenance、权限拒绝、timeline、文件编辑租约、workbench 文件校验、证据包 provenance 和真实任务 Context。
- `rp_agentos_query`、`rp_agentos_recovery`、`rp_agentos_timeline`、`rp_agentos_collab_ack`、`rp_agentos_audit`、`rp_agentos_workbench`、`rp_agentos_package`、`rp_agentos_real_task`、`rp_agentos_conflict` 必须可被 Host Reader Compare 页面直接读取。
- 高价值科研平台阶段应优先接入通用 AgentOS 原语，包括 Context snapshot、文件 metadata 查询、事件等待、timeline 读取、ledger/provenance snapshot、通用动作与工件状态接口、role/capability 检查。
- 不为单个 demo 字符串写死内核逻辑。内核 helper 应能支持不同文件名、阶段、角色和 run id 的 Agent 工作流。

## Host Reader 维护要求

- Compare 页面同时展示 plain target 成本和 AgentOS target 替代路径。
- `Dual Target Overview`、`AgentOS Main Flow Kernel Stages`、`AgentOS Kernel Output Files` 等面板直接读取状态文件，让评委不必手动打开原始文件。
- action output、impact、delta 表与刷新后的 `rp_*` 文件绑定，而不是只显示宿主机请求。
- 真实任务页面展示 `report_md`、answer audit、数值统计覆盖、证据包状态和 duplicate-entry 检查。
- 双目标结果图表应来自真实运行摘要，默认由 `host_tools/summarize_dual_platform_results.py` 生成。

## 执行顺序

1. 查看宿主机科研 Agent 平台的可见对象和输出字段。
2. 对齐 plain target 与 AgentOS target 的状态文件名称、角色名称和场景。
3. 把固定计数改成对已有状态文件的主动读取和一致性检查。
4. 将增强目标中最能体现内核价值的阶段接入通用 AgentOS 服务。
5. 确认 Host Reader 能从提取出的状态文件展示两个目标。
6. 工作区稳定后，运行双目标构建、QEMU 验证和结果图表生成。

## 目标分离规则

未改动 uCore 目标不得加入 Agent syscall、Agent Context、内核文件 metadata 服务、Agent 事件队列或内核 LLM 网络能力。

AgentOS-uCore 目标可以使用和改进这些服务，但科研平台的公开场景必须继续与 plain target 可比较。最终形态应是同一科研 Agent 平台的两种实现：一种受限于用户态，一种在关键路径使用内核 Agent 支持。

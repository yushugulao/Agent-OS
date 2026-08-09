# 竞赛评审入口

AgentOS-uCore 面向 AI Agent workflow 提供内核身份、Context、结构化工具、实时文件属性事件、可信 IPC、批量资源域和可验证 fence。科研业务仍在用户态，plain/AgentOS 两目标运行同一工作流合同。

## 赛题映射

| 任务 | 评审重点 | 当前机制 |
| --- | --- | --- |
| 任务一 | Agent 进程与地址空间 | 可信 role/capability、Context 映射、immutable lifecycle、members/closing/gates |
| 任务二 | Agent 系统调用 | typed 工具协议、name/id 目录、batch、稳定错误码 |
| 任务三 | Context Path | 内核可信记录、cause/span/branch、查询/快照/rollback |
| 任务四 | 文件属性查询 | explicit volatile metadata、选择性索引、typed `ENTER/UPDATE/LEAVE`、resync |
| 任务五 | Agent Loop | event/watch/wait/heartbeat、可信 route、Evidence Ring、workflow fence |
| 任务六 | 综合应用 | plain/AgentOS 同科研工作流、状态一致性和绑定原始材料的 paired measurement |

完整映射见[要求追踪表](../agentos/requirements-traceability.md)。

## 三项重点增量

1. **Workflow Credit Domain**：借鉴 Linux CPU accounting/percpu/rstat 的批量思想，以 U/P/F credit 保持硬额度，并在 context switch、压力和 fence 精确 trim。
2. **Fence-Sealed Evidence Ring**：借鉴 Linux BPF ring buffer 的有序 reserve/commit/discard，普通成功只写一次 canonical event，critical 独立分区，fence 绑定 challenge/credit/metadata/gap 根。
3. **Agent Live-Query FS**：借鉴 Haiku BFS 显式属性、索引和 live query，只索引显式 volatile metadata，把 typed transition 直接投递到 Agent Context，并用 generation resync 处理有界丢失。

三项都是 clean-room、项目特定实现，没有复制/vendoring 上游源码、测试、数据、二进制或磁盘格式。详见 [NOTICE](../../NOTICE) 与[第三方及原创增量说明](third-party-and-originality.md)。

## 必须主动说明的限制

- metadata 不 autoscan、不写 catalog bank/journal、不支持 crash recovery；
- `PERSIST/AUTOSCAN` legacy flags 返回 `BAD_PARAM`；
- Evidence Ring 和 audit receipt 是 fence-sealed memory evidence，不是 disk durable；
- observe recovery syscall 为 tombstone，固定 `BAD_PARAM`；
- audit/timeline/provenance/ledger 兼容视图仍保留，不能称为完全删除；
- lifecycle 使用 members/closing/gates，不宣称多阶段 retirement；
- fence receipt 明确 partial coverage 与 volatile metadata；
- 当前 Guest 是单 Hart，Host 多 lane 不等于 SMP。

## 评审材料顺序

1. [根 README](../../README.md)：定位和快速入口。
2. [系统设计](../agentos/design.md)：三项机制与 workflow fence。
3. [ABI](../agentos/api.md)：320 字节 receipt、typed watch 和兼容 tombstone。
4. [安全加固](../agentos/security-hardening.md)：hard admission、generation、resync、fail closed。
5. [验证说明](../verification.md)：静态、构建、QEMU、paired 和 release 边界。
6. [正式证据索引](../../evidence/releases/INDEX.md)：只从冻结 bundle 阅读实测结果。

索引为空或对应 artifact 缺失时，应显示 unavailable。开发日志、示例 marker、静态测试通过数和 Dashboard 本身都不能替代 raw evidence。

## 建议演示顺序

| 时间 | 内容 | 关键证据 |
| --- | --- | --- |
| 0-3 分钟 | 可信 Agent、Context、tool batch | 身份/Context/tool Guest 输出 |
| 3-6 分钟 | Live Query 显式登记与 typed transition | query plan、ENTER/UPDATE/LEAVE、resync |
| 6-9 分钟 | Credit U/P/F 与 hard quota | resource snapshot、模型/Guest quota 拒绝 |
| 9-12 分钟 | Evidence Ring 与 workflow fence | challenge receipt、exact U、gap/root、retry |
| 12-15 分钟 | plain/AgentOS 同工作流对比 | 两侧原始日志、状态 compare、paired summary |

答辩时每项机制按“赛题问题、参考思想、AgentOS 特定实现、动态证据、限制”陈述，避免用功能通过数替代性能，也避免把端到端差异直接归因到单一模块。

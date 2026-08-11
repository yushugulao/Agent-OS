# AgentOS 安全加固

AgentOS 的安全目标是让身份、授权、资源和证据在同一 workflow lifecycle 内闭合。我们假设 Guest 用户态 Agent、文件内容、工具输出和模型建议都可能出错；内核只接受能够由当前身份、generation、schema 和资源状态共同验证的请求。

## 威胁模型

| 风险 | 处理方式 |
| --- | --- |
| 普通进程冒充 Agent | 可信映像与受控创建；身份由内核建立 |
| PID、slot 或 inode 复用 | lifecycle、object 和 handle generation 防止 ABA |
| 越权工具副作用 | role/capability、scope、schema、provenance 与 effect gate 联合检查 |
| 重复请求或过期响应 | 单调 request/correlation、terminal 状态与有界 replay 记录 |
| 资源预留后泄漏 | Workflow Credit Domain 与 teardown 精确结算 |
| live-query 丢事件 | typed watch generation 与显式 resync |
| 成功事件挤掉关键拒绝 | Evidence Ring 的普通区与 critical 区分离 |
| Host 或模型伪造业务结果 | Guest 执行工具并回灌真实 result；Host 只转发协议 |

系统不尝试在内核中判断自然语言真假、prompt injection 或模型质量。来自文件、工具、IPC 和模型的内容始终按不可信数据处理。

## 身份、权限与 scope

Agent 创建时，内核把以下状态绑定到进程：

- Agent id、control id、role 与 capability mask；
- workflow id、scope id 和 lifecycle generation；
- 可信映像与 exec policy；
- 资源账户、Context 区和事件路由。

普通 `fork` 不会获得新的可信 Agent 身份。worker 只能通过 `agent_worker_create()` 从当前 workflow 创建，能力不得超过授权集合。跨 Agent message 还要命中显式 route 和相同 active workflow。

所有对象都用 generation 验证当前 incarnation。旧 watch、合同、Task Channel、resource handle、LLM response 或 fence receipt 不能绑定到复用后的进程和 lifecycle。

## 结构化输入

V2 工具调用按目录 schema 检查 key、type、value size、字符串终止、重复参数和未知字段。`tool_id` 与 `tool_name` 必须一致。内核完成 copyin 后只使用私有副本，避免用户在检查后修改共享内存。

ENFORCE V3 在此基础上增加：

1. 当前 lifecycle 与 frozen contract；
2. 节点、attempt 与 predecessor 状态；
3. schema digest 和 canonical input fingerprint；
4. source Context sequence 与 producer identity；
5. deadline、retry/cancel policy；
6. provenance envelope 和资源包络。

任何一步失败都在 effect fence 前返回结构化原因。副作用开始后，cancel 只报告 too-late，不把已经发生的效果伪装成取消成功。

## Provenance

AgentOS 使用六类固定标签：

| 标签 | 含义 |
| --- | --- |
| `KERNEL_FACT` | 内核直接观测的事实 |
| `TRUSTED_USER_CONTROL` | 经过控制面绑定的用户决定 |
| `AGENT_DERIVED` | Agent 计算或汇总结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容或 metadata 派生数据 |
| `UNTRUSTED_TOOL_OUTPUT` | 工具或模型输出 |
| `CROSS_AGENT_DATA` | 跨 Agent 传播的数据 |

标签采用保守 OR 传播。读取文件、工具结果、IPC 和 Nexus TASK/artifact 不会因为再次哈希或由另一个 Agent 处理而清除不可信位。需要可信控制的副作用必须满足工具策略对 accepted labels 的要求。

provenance 是数据来源分类，不是内容真实性证明。payload SHA-256 证明字节一致，也不自动证明生产者可信或调用已获授权。

## 资源硬额度

Workflow Credit Domain 维护 `held = U + P + F`，并要求 held 不超过 account、class 和 global limit。F 是账户已持有的 free credit，P 是 reservation，U 已计入 live object；held 可以低于配置上限。创建失败退回 F，发布对象由析构归还，teardown 还会核对成员、活动操作和资源账户。

Tool Phase Credit Lease 只锁定已经计入 U 的 credit。它不能扩大额度，也不能用预测值掩盖真实对象。phase claim 使用 nonce，失败、发布和析构分别走唯一结算路径。

资源不足返回 `NO_SPACE` 或相应结构化状态，不通过丢弃证据、绕过 Context 或继续执行副作用来换取成功。

## Context 与 Evidence

Context 内核页对用户只读，发布使用单调 sequence 和一致性检查。用户 cache 与可信记录分页，不能覆盖 header、latest response 或历史窗口。

普通成功 Context 只进入一次 canonical Evidence Ring。拒绝、失败、fence 和回退记录进入关键路径，防止普通流量耗尽安全容量。ring rollover 会公开 dropped/gap，而不会把不完整窗口描述成完整历史。

workflow fence 只有在 metadata/live-query 已排空、credit 精确、evidence 已封存时才发布 receipt。receipt 绑定调用者 challenge、事件范围和 previous root；未达到同一 cut 时返回 `RETRY`。

当前 Evidence Ring 是启动周期内的有界内存证据，不是跨重启不可篡改日志、远程证明或外部时间戳服务。

## Live Query

Live Query 只接收显式登记的 volatile metadata。catalog 绑定 `dev + inode + incarnation`，文件删除和 inode 复用不会继承旧记录。查询结果同时携带 `fs_generation`、plan 和检查工作量。

typed watch 保存完整谓词并按 membership 变化产生事件。事件丢失、generation 变化或队列压力设置 `RESYNC_REQUIRED`；调用方必须重新查询完整状态再确认。系统不会把残缺增量静默当作当前全集。

旧 persist/autoscan flag 仅保留 ABI 数值，当前设置接口拒绝它们。内核不自动扫描任意磁盘内容。

## 事件与调度

事件队列总容量为 16，并保留内核与来源类别配额，防止单一外部来源占满队列。`agent_wake()` 只能注入允许的外部事件，不能伪造 file-query、policy-denied 或其他内核来源记录。

等待、取消和 heartbeat 都绑定当前 Agent incarnation。timer IRQ 只标记到期并唤醒匹配实体，重型回收与 Context/Evidence 提交在可调度 safe point 完成。该设计避免 IRQ 内释放页或调用 provider，但不提供不可中断睡眠的 wall-clock 完成上界。

workflow EEVDF 的配置有固定上下限。普通进程仍可运行，Agent 的 role、deadline 或事件压力不能绕过 workflow 预算。

## Task Channel

Task Channel 使用 single issuer、严格递增 request id、ring/slot/channel generation 和内核私有水位。内核在校验前复制完整 128 字节 SQE，CQ 对用户只读。水位异常进入 sticky resync；CQ full 只产生 backpressure，不覆盖未消费终态。

每个 accepted target 只发布一个 terminal CQE。cancel 有自己的 request id，但只引用目标，不产生第二个 CQE。effect fence 前可以取消，开始副作用后返回拒绝或 too-late。

当前 bridge 只有同步 null/NONE provider。`RESOURCE_IMPORT` fail closed，尚无用户 payload 或结果 resource backend；因此 typed handle 校验不应被写成已经交付的业务对象传输。

## Guest、Host 与模型

Guest 保存 system prompt、有界 whole-turn history、工具目录、round/correlation，验证模型建议并执行真实 V2 工具。Host daemon 只处理 owner-only 本地连接、QEMU 串口、TLS、API key 和 provider JSON 转换。

串口 frame 绑定 session、方向内单调 sequence、kind、decoded length 和 SHA-256。未知 kind、超长 frame、hash 不匹配、乱序或重复 frame 都 fail closed。审批绑定 session、turn、request、correlation、工具名、canonical 参数 digest、nonce 和有效期；超时或 controller 断开即拒绝。

API key 只从 Host 文件或环境变量读取，不进入 Guest、TASK、Context、命令行内容或日志。网络失败不会静默切换到 replay。

仓库保存的 console/Nexus 验收使用 digest-bound offline replay。replay 证明 wire、Guest 状态机和真实内核工具路径可以复验；它不证明 live provider 可用、模型质量或远程 exactly-once。

## Nexus 边界

Nexus `N1` TASK 是 Guest 用户态 envelope，通过内核 `MESSAGE` 发送。内核验证 route、workflow、capability、队列和 provenance，但不解析 TASK 业务状态。

Nexus artifact store 在 Guest 用户态校验 lifecycle、handle generation、kind、权限、producer/materializer、payload SHA-256 和 manifest SHA-256。它仍是 boot-volatile 的 workflow-scoped VFS 存储，不是内核 per-object ACL、签名工件库或跨重启持久服务。

Nexus approval 是 Guest gate，继续受内核 capability、scope 和 VFS 检查。MCP/A2A 模块只做 deterministic in-memory 对象映射，不提供 HTTP、OAuth/JWS、streaming 或外部互操作安全声明。

## 失败处理

| 条件 | 结果 |
| --- | --- |
| lifecycle 或 generation 过期 | `STALE` / `CONFLICT`，不迁移旧状态 |
| capability、scope 或 provenance 不满足 | `DENIED`，不开始副作用 |
| 参数、版本、结构尺寸错误 | `BAD_*`，不使用未验证字段 |
| credit、队列或表容量耗尽 | `NO_SPACE` / backpressure，保持已有对象 |
| watch 增量不完整 | `RESYNC_REQUIRED`，要求全量重查 |
| fence 尚未达到一致 cut | `RETRY`，不发布完整 receipt |
| 模型 frame 或审批绑定不匹配 | 拒绝该回合，不执行工具 |
| live provider 失败 | 结束为失败；不会伪造结果或自动改用 replay |

## 验证入口

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make agentos-console-check
make agentos-nexus-check
```

静态 checker 只证明源码合同；需要 Guest 行为时运行对应 QEMU 场景或固定 replay。完整层次见[验证说明](verification.md)。

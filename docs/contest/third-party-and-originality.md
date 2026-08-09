# 第三方来源与原创增量说明

本文帮助评委区分上游教学内核、公开设计思想、外部运行工具和本项目的 clean-room 增量。它是工程披露，不替代许可证正文，也不构成法律意见。最终范围以冻结 Git tree、`NOTICE` 和 release manifest 为准。

## 1. 直接衍生与分发内容

| 项目 | 作用 | 仓库关系 | 许可/依据 |
| --- | --- | --- | --- |
| LearningOS/uCore-Tutorial-Code-2025S | 教学内核、基础用户态和构建骨架 | 本仓库的直接衍生基础 | GPL-3.0；见根目录 `LICENSE`、`NOTICE` |
| LearningOS/uCore-Tutorial-Test-2025S | 教学测试基础 | 本仓库的直接衍生基础 | GPL-3.0；见 `NOTICE` |
| Linux `.clang-format` | 代码格式配置 | 根目录独立格式数据 | 保留文件内 `GPL-2.0` SPDX，不改写为 GPL-3.0 |

本项目源码采用 GPL-3.0，技术文档与展示材料采用 CC BY-SA 4.0，单文件另有声明时除外。

## 2. 运行环境，不随仓库 vendoring

QEMU、RISC-V GCC/binutils、GNU Make、Bash、Python、WSL/Linux 等由复现环境安装，不作为本项目源码 vendoring。正式证据记录实际路径、版本、命令和 hash。环境中出现这些程序不表示本仓库重新分发它们。

## 3. 概念级设计参考

### 3.1 Linux CPU accounting、percpu_counter 与 rstat

公开资料说明了把频繁计数保留在本地/批量状态、允许普通统计暂时滞后、在需要时同步的性能取舍。AgentOS 由此获得“延迟聚合”的概念启发。

本项目的 **Agent Workflow Credit Domain** 是独立实现：

- 本地单位不是 Linux CPU/进程组统计，而是 workflow exec/storage resource account；
- 每类资源显式维护 `used/pending/free`，hard admission 按 `U+P+F`；
- reserve/commit/cancel/release 在三态间移动，普通路径不更新一套额外全局 used；
- context switch、压力、close 和 fence 才 trim；
- workflow fence 要求 pending 为 0，并把 exact U 与 account generation/epoch 绑定到 digest。

参考链接见 `NOTICE`。没有复制 Linux `percpu_counter`/rstat 源码、测试或 ABI。

### 3.2 Linux BPF ring buffer

Linux BPF ring buffer 的共享有序 ring、`reserve/commit/discard` 和通知策略提供概念启发。

本项目的 **Fence-Sealed Evidence Ring** 是独立实现：

- 每 workflow 4 个计费页，48 ordinary + 16 critical；
- event 是 Agent Context/因果/授权字段，不是 BPF sample ABI；
- ordinary success 只写一次 canonical event，critical 另有兼容 ledger 投影；
- ticket gap、rollover、workflow generation、credit digest 与 metadata generation 进入 seal；
- controller challenge 和 previous root 在 workflow fence 上形成 SHA-256 root 链；
- receipt 明确 partial coverage 和 memory-only sealed 语义。

没有复制 BPF ring buffer 源码、BPF 程序、测试、map ABI 或二进制布局。

### 3.3 Haiku BFS 属性、索引与 live query

Haiku BFS 对显式文件属性、选择性属性索引和 live query 的描述提供概念启发。

本项目的 **Agent Live-Query FS** 是独立实现：

- 只有显式 `agent_file_meta_set()` 的 workflow 文件进入 volatile catalog；
- 索引只服务 status/stage/kind 等 Agent 字段；
- before/after 谓词产生 typed `ENTER/UPDATE/LEAVE`；
- 事件进入 Agent Context/queue，并受 capability、scope、lifecycle generation 限制；
- 有界增量丢失使用 generation `RESYNC_REQUIRED` 和 ACK；
- 不实现 BFS 磁盘属性格式、目录格式、journal 或 crash recovery。

没有复制 Haiku/BFS 源码、数据、测试、磁盘结构或二进制。

### 3.4 其他公开参考

Linux cgroup v2、blk-mq、wait queue 用于理解通用资源、I/O 和等待队列设计。AIOS 论文/项目只作为公开项目和评价维度参考，不复制其代码、数据或结果。

## 4. 本项目相对上游的主要增量

以下描述工程增量，不主张每个通用算法由本队首次发明：

1. 可信 Agent 映像、role/capability、Context 地址区与 fork/exec/exit 集成。
2. 名称/id 工具目录、typed KV、批量调用和稳定错误协议。
3. Context Path、cause/span/branch/control 归因、只读 mirror 和 rollback。
4. immutable workflow id/generation、`member_refcount + closing` 与 operation/departure/fence gates。
5. Workflow Credit Domain 的 U/P/F hard admission、批量预充、pressure trim 和 fence exact digest。
6. Fence-Sealed Evidence Ring 的 ordinary/critical 分区、canonical Context event、compat projection、gap 和 challenge root。
7. Agent Live-Query FS 的显式 volatile metadata、选择性索引、typed transition、Context event 与 resync。
8. 文件 scope/incarnation、worker 委派、可信 IPC、watch/wait/heartbeat 和 Agent 感知调度。
9. plain/AgentOS 同负载、Host 状态提取、预算门、原始材料绑定和离线 Dashboard。

## 5. 明确不作为当前原创能力宣称

- metadata 双 bank、journal、自动目录扫描和 crash-recovery catalog；
- 每个成功操作的 durable audit/timeline/provenance 多重写入；
- observation disk recovery；
- 多阶段 workflow retirement；
- BPF、BFS、Linux cgroup/rstat 的源码兼容或移植；
- AIOS 代码、数据或评价结果的再分发。

历史源码/ABI 名称可能仍为迁移参考或编号兼容存在，但生产对象清单和 dispatcher 决定当前能力。

## 6. clean-room 声明

上述 Linux/Haiku/AIOS 资料只用于理解公开设计原则。项目成员针对 uCore 和赛题合同重新定义数据结构、状态机、UAPI、错误语义、测试与实现。除已明确披露的 uCore 衍生基础和 `.clang-format` 外，三项新机制没有 vendoring 第三方源码、测试数据、生成数据、二进制、固件、磁盘镜像或文件系统格式。

名称“percpu/rstat-inspired”“BPF-ring-inspired”“BFS-live-query-inspired”只陈述概念谱系，不暗示上游认可、兼容性或代码复用。

## 7. 核验方式

- 用 Git 历史和 active production object 清单区分上游基础、当前实现与 retired reference source。
- 对每项机制按“问题、公开思想、AgentOS 特定数据结构、动态证据、限制”答辩。
- 运行 UAPI/module/budget checker，确认停产模块没有重新进入生产链接。
- 对提交包中的图片、字体、数据集、代码片段和生成式 AI 输出逐项确认来源与许可。
- 无法确认来源的材料在发布前移除，不自行推断为公有领域。

## 8. 版本边界

Git 历史中的 `9e8338a61ee73da12462dc8d8433e9e2f7dbbc4b` 是本仓库导入起点，不冒充上游原始 commit。最终源码提交、证据提交和 tag 由 [正式证据索引](../../evidence/releases/INDEX.md) 与 release manifest 记录，本文不复制易漂移的发布身份。

除 `NOTICE` 披露的 uCore 衍生基础、`.clang-format` 和运行环境外，当前仓库不声明其他直接 vendored 第三方代码。后续引入任何新素材必须同步更新 `NOTICE`、本页和相应许可证信息。

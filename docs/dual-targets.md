# 双目标 uCore 科研 Agent 平台说明

当前项目同时保留两个目标：共享基础安全加固、不含 AgentOS 扩展的 uCore 对照目标，以及 AgentOS-uCore。Task 6 在两侧固定相同的 70 项程序顺序、challenge 和结果契约；逐项源码收据当前为 28 项同源、42 项目标特定实现。因此这一层呈现完整平台差异，但不把总耗时归因给单一内核机制。

这里的“科研 Agent 流程”指项目内置的用户态示例负载。它模拟一次科研处理任务，包含数据准备、比对处理、结果分析、报告生成和归档交付五个环节，并由多个角色程序协作完成查询、分析、恢复、写作和审计。

## 目标 A：AgentOS-uCore

仓库根目录是增强版 AgentOS-uCore 目标：

- 内核：`os/`
- 文件系统镜像构建：`nfs/`
- 启动和辅助脚本：`scripts/`
- AgentOS 用户程序和科研平台程序：`user/`
- 状态查看工具、动作运行器、文件系统提取器、LLM Relay：`host_tools/`

这个目标在同一设定的模拟流程上使用内核 Agent 服务，包括 Agent 角色和能力、Agent Context、批量工具调用、Context Path、文件 metadata 查询、事件等待和唤醒、heartbeat、timeline、ledger/provenance、文件编辑租约和调度证据。该模拟流程包含数据准备、比对处理、结果分析、报告生成和归档交付五个环节；增强目标让这些环节直接进入内核能力路径，避免把 AgentOS 降成旁路测试程序。

科研流程的项目名、run id、阶段名称、失败原因和恢复策略由用户态程序写入。结构检查会扫描根目录 `os/`，防止这些示例常量变成内核默认业务。

## 目标 B：共享安全基底的 uCore 对照组

理解增强目标之后，再看 `baseline_ucore/` 会更清楚：它保留同一个科研平台负载，但去掉 AgentOS 内核服务，用普通 uCore 能力完成同样的流程。

不含 AgentOS 扩展的 uCore 对照目标位于 `baseline_ucore/`：

- 内核：`baseline_ucore/os/`
- 文件系统镜像构建：`baseline_ucore/nfs/`
- 启动和辅助脚本：`baseline_ucore/scripts/`
- 用户态科研 Agent 平台：`baseline_ucore/user/`

这个目标与主目标共享 syscall 用户输入防护、定向等待、syscall 内核工作预算、可恢复文件系统耗尽、块 owner map、安装级 PUBLIC 存储 principal、普通主体配额与系统保留量、内核栈保护、进程退出回收和进程域配额等通用安全机制，但不加入 Agent syscall、Agent Context、Agent 文件 metadata、Agent capability 或 Agent 事件队列。两个目标都把持久存储身份与短命进程资源域分离，并在挂载时从 qmap/dinode 重建 PUBLIC 用量；主目标基于 VFS 凭据进一步区分 PUBLIC/WORKFLOW/SYSTEM 三级存储水位，baseline 只保留普通与系统两级，不依赖任何 Agent 符号。科研 Agent 平台通过普通用户进程、普通文件、`fork/exec/wait`、`open/read/write/close` 等机制运行。它用于观察不使用 AgentOS 专属服务时，哪些工作依赖用户态约定、扫描和文件重建。

两个目标的通用内核工作预算都以线程 dispatch 建立 deadline/工作额度，由 syscall begin/end、timer pending 和 resumed 检测共同维护；console、pipe、exec 分页、fork 页表快照、FD_INODE 分块读写及 truncate detach/reclaim 使用同一安全点和资源生命周期协议。fork 的 VM snapshot 屏障只暂缓同进程 sibling，不阻塞其他进程。只有主目标额外把 Agent batch 接入该机制。双目标 `syscallfair_ucore` 使用纯 Guest 同一契约验证控制台长写、inode 大写入的内核重调度计数、短写与 observer 进展，以及截断回收；固定上界目录扫描与仅主目标可信 Agent 可达的 metadata raw I/O 仍是残余覆盖。

构建、运行和状态查看命令统一放在 [verification.md](verification.md) 中维护。

## 两个目标必须保持一致的内容

双目标比较成立的前提是输入和输出足够接近。下面这些约束用于保证比较对象集中在内核支持差异上。

两个目标应使用同一科研场景、同一核心对象名、同一角色名和相近的输出字段。输出内容包括：

- uCore 对照目标通过用户态文件和 Host 侧运行器完成科研平台流程。
- AgentOS-uCore 运行等价科研流程，但把可信 Context、metadata 查询、事件通知、失败恢复、权限控制、timeline 和 provenance 交给内核服务。
- 两个目标都输出可比较的 run 记录、artifact 记录、项目复核记录、交付记录、LLM Relay 记录、Agent 协作记录和 AgentCompare 记录。
- 双目标脚本会提取两个镜像中的 `rp_*` 状态文件，并自动对照状态文件集合和成功记录。plain target 已经完成的记录，AgentOS target 必须保留；AgentOS target 额外增加的内核证据单独计入。
- 双目标脚本还会用状态查看工具渲染两个目标的真实状态文件，并比较渲染摘要。两个目标应生成同一套查看入口；AgentOS target 可以多出内核证据状态和 API JSON，但不能少于 plain target。
- 增强目标可以增加内核可见证据和更快路径，但不能降低科研流程复杂度。

仓库外的 `research-agent-platform-userland` 是宿主机科研 Agent 平台原型。当前项目把它作为能力参照，不把 Python 平台复制进仓库。双目标检查由以下脚本完成：

| 检查脚本 | 检查内容 |
| --- | --- |
| `host_tools/check_host_platform_alignment.py` | 读取宿主机平台模块，检查 `baseline_ucore/` 与根目录 AgentOS-uCore 是否覆盖主要能力族。 |
| `host_tools/check_host_test_alignment.py` | 读取宿主机平台测试方法，把测试归入功能主题，并检查两个 uCore 目标是否保留对应状态证据。 |
| `host_tools/check_host_surface_alignment.py` | 读取宿主机 `api_server.py`，检查 API/action 路由规模是否映射到两个 uCore 目标。 |
| `host_tools/check_host_action_kind_alignment.py` | 检查每个宿主机 action 路由是否能映射成 seed kind，并确认 plain target 与 AgentOS target 都有真实运行程序处理。 |
| `host_tools/check_seeded_action_state.py` | 将 44 个预置请求分别送入两个 QEMU 目标，检查 `rp_input`、`rp_runner`、`rp_report_text`、`rp_artifact_manifest`、`rp_stage_dag`、`rp_llm_packets`、`rp_wfio`、`rp_usableproj`、`rp_studyproto` 等状态文件是否写入同一组关键结果。 |

`make dual-platform-run` 会把这些摘要交给页面工具，Compare 页面用它们呈现能力组、测试主题、Web/API/action 规模、预置 action 实际运行结果、plain target 证据和 AgentOS target 证据。

测试主题检查现在会同时读取 QEMU 运行后抽取出的 `rp_tests` 状态文件。这样可以确认宿主机测试主题对应的证据已经由两个 uCore 目标实际写出，并且能够被状态查看工具读取。

## 当前状态

前面说明了两个目标应该如何对齐；本节说明当前仓库已经做到的程度。

uCore 对照目标已经包含可由状态查看工具读取的一整套科研平台状态：Web/API 数据、动作运行器、artifact 记录、工作流记录、项目复核状态、Host LLM Relay、AgentCompare 和端到端 QEMU 路径。

AgentOS-uCore 目标已经把增强内核服务接入同一科研流程。入口 `rp_agentos_orch` 创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，随后运行完整 `rp_orch` 流程。主阶段会向 `rp_agentos_mainflow` 追加 11 个唯一、完整、有序的未验证 telemetry 阶段，并覆盖 12 类内核事实：可信 Context、通用依赖图与依赖驱动预取、metadata 索引查询、Agent 事件通知、通用动作提交与工件状态更新、ledger/provenance 观察、sentinel 越权恢复被拒绝、timeline 观察、文件编辑租约、workbench 文件校验、证据包 provenance、真实任务报告与答案审计。Guest 不再生成 Mainflow 通过回执；Host 从安全状态清单读取 11 个规范来源，逐项复验唯一 claim、预期成功状态和阶段字段，并计算 telemetry 与来源文件的完整 byte count/hash。任何 Guest `runtime_verified` 记录都 fail closed。

seeded reference 数据与 runtime 证据严格分层。目标相关 registry 为每个 reference 文件和 `(destination, anchor)` 记录登记唯一源码 owner，解析前剥除注释，并拒绝缺失、未知、重复、跨 owner 预发布和 runtime 身份冒充；Plain seeded 程序清单还要同时命中 seeded profile、QEMU 日志与 `rp_orch_timing` 的 orchestrator/launcher、程序顺序和摘要。状态清单不再提供路径权威，只能与目录内单层、非链接的 `rp_[a-z0-9_]+` 文件精确比对。当前 runner tick 仅允许 `unavailable/plain_runtime_cases_zero` 且没有对照行；恢复 measured 状态前必须先提供独立、非 reference 的可信 runtime producer 和逐字段 receipt。

状态查看工具会直接读取 `rp_agentos_mainflow` 和相关 `rp_agentos_*` 文件，并对照 plain target 的用户态成本与 AgentOS target 的内核替代路径。

## 开发约定

根目录是 AgentOS-uCore 主目标。增强内核能力放在根目录 `os/`、`user/` 和 `scripts/` 中，进入仓库后即可查看主要实现。

`baseline_ucore/` 只做无 AgentOS 服务的 uCore 对照。通用安全修复可以在两个目标中保持一致，但不能让 plain target 依赖 AgentOS syscall、Context、capability、文件 metadata 或事件服务。安全基底和 AgentOS 增量的完整分工见 [agentos/security-hardening.md](agentos/security-hardening.md)。

# 第三方来源与原创增量

AgentOS-uCore 以 LearningOS/uCore 教学内核为工程起点。我们保留其许可证与来源说明，并围绕 Agent workflow 重新设计内核对象、UAPI、状态机、测试和演示程序。

## 直接来源

| 来源 | 仓库中的作用 | 处理方式 |
| --- | --- | --- |
| LearningOS/uCore-Tutorial-Code-2025S | 教学内核、基础用户态和构建骨架 | 作为 GPL-3.0 衍生基础，来源记录在 [NOTICE](../../NOTICE) |
| LearningOS/uCore-Tutorial-Test-2025S | 教学测试基础 | 作为 GPL-3.0 衍生基础，来源记录在 [NOTICE](../../NOTICE) |
| Linux `.clang-format` | 根目录代码格式配置 | 保留文件内 GPL-2.0 SPDX，不改写许可证 |

仓库源码采用 [GPL-3.0](../../LICENSE)。技术文档与展示材料采用 [CC BY-SA 4.0](../../DOCUMENTATION_LICENSE.md)，单文件另有声明时遵循该文件声明。

## 我们借鉴了什么

公开系统和论文帮助我们确认通用设计方向。下表同时列出 AgentOS 的项目特定落点。

| 公开参考 | 借鉴的思想 | AgentOS-uCore 中的实现 |
| --- | --- | --- |
| Linux CPU accounting、`percpu_counter`、rstat、cgroup v2 | 本地累计、批量同步、资源域 | Workflow Credit Domain 的 `used/pending/free`、hard admission、pressure trim 与 fence exact digest |
| Linux BPF ring buffer | 有序 reserve/commit/discard、通知 | Fence-Sealed Evidence Ring 的 ordinary/critical 分区、ticket gap、challenge root chain |
| Haiku BFS attributes/index/live query | 显式属性、选择性索引、谓词变化通知 | boot-scoped metadata catalog、`status/stage/kind` 索引、typed transition 与 generation resync |
| Linux EEVDF | lag eligibility、virtual deadline、睡眠实体服务 | 以 workflow resource domain 为实体的 EEVDF 与 raw service-cycle accounting |
| io_uring SQ/CQ、WIT/WASI ownership | 双队列通信、owned/borrowed handle、异步对象词汇 | 16-slot SQ/CQ core、typed handle 和 terminal CQE 合同 |
| Murakkab、CaMeL、IPIGuard | 声明式 workflow、可信控制流、工具依赖图 | ENFORCE V3 冻结 DAG、provenance/effect gate、Tool Phase Credit Lease |
| MCP 2026-07-28、A2A v1 | Task、Context、Artifact 对象形状 | 用户态 deterministic object mapping prototype |
| AgentCgroup、AIOS | Agent 资源突发与系统评价维度 | 工作负载参数、性能采集维度与答辩对照 |

完整论文、规范和上游链接集中记录在 [NOTICE](../../NOTICE)。这些资料用于理解设计原则和协议对象，没有作为源码依赖导入。

## 我们实现了什么

相对教学内核，本项目的主要工程增量包括：

1. 可信 Agent 映像、role/capability、7 页 Context 地址区和进程生命周期集成。
2. Context Path、cause/span/branch/control 归因、六类 provenance 与只读 mirror。
3. 名称/id 工具目录、typed V2 RPC、ENFORCE V3 执行合同与顺序 compact batch。
4. immutable workflow `id + generation`、members/closing 与 operation/departure/fence gates。
5. Workflow Credit Domain、Tool Phase Credit Lease 和 workflow EEVDF。
6. Fence-Sealed Evidence Ring、challenge-bound fence 与 320 字节 receipt。
7. Agent Live-Query FS、typed watch、Context event 和 generation resync。
8. Agent Task SQ/CQ、可信 IPC/LLM correlation、交互控制台和 Nexus workflow。
9. Host checker、QEMU Guest 回归、双目标比较与 one-shot 逐样本性能管线。

这些机制的数据结构、状态转换、错误语义、UAPI 和测试均在本仓库内针对 uCore 与赛题要求实现。项目不声明 Linux、Haiku、EEVDF、io_uring、WIT/WASI、MCP 或 A2A 的通用概念由我们首次提出。

## 代码与协议边界

- Linux、Haiku、论文原型、AIOS、MCP/A2A SDK 和 Wasm/WASI 源码没有作为项目特定机制 vendoring。
- 仓库不包含这些项目的测试数据、二进制、固件、生成数据或磁盘格式。
- `percpu/rstat-inspired`、`BPF-ring-inspired`、`BFS-live-query-inspired` 等名称只说明设计谱系。
- Agent Task SQ/CQ 采用项目自己的 ABI；项目没有嵌入 io_uring。
- typed handle 使用 WIT ownership 词汇；项目没有嵌入 Wasm runtime。
- MCP/A2A 代码是用户态对象映射 prototype，不包含网络 server、streaming transport 或内核 adapter。

## 运行环境

QEMU、RISC-V GCC/binutils、GNU Make、Bash、Python、WSL/Linux 和绘图库由运行环境安装，不随仓库重新分发。仓库也不把外部模型服务、API key、视频或幻灯片作为构建依赖。

## 如何核验

1. 查看 [NOTICE](../../NOTICE)、[LICENSE](../../LICENSE) 和各文件 SPDX。
2. 通过 Git 历史区分教学内核基础与项目增量。
3. 对照[要求追踪表](../agentos/requirements-traceability.md)定位生产源码、UAPI 和验证入口。
4. 运行 [验证说明](../verification.md)中的构建、Host checker 和 QEMU Guest 测试。
5. 发布新代码、图片、字体、数据或协议适配时，同步更新 NOTICE 与本页。

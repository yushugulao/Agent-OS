<p align="center">
  <img src="docs/assets/agentos_logo.png" alt="AgentOS-uCore" width="680">
</p>

# AgentOS-uCore

AgentOS-uCore 是构建在 RISC-V uCore 上的 Agent 操作系统功能模块。我们把 Agent 身份、Context、结构化工具、文件状态、事件循环、资源控制和 workflow 调度纳入内核，再通过稳定 ABI 支撑多轮 Agent 与多 Agent 协作应用。

## 项目背景

传统进程接口围绕进程、文件和系统调用组织状态。Agent workflow 还要连续维护执行者身份、上下文因果、工具权限、文件语义、跨轮预算和多 Agent 任务关系。应用各自实现这些能力时，同一动作往往要经过多套身份、权限和状态协议，进程退出与 workflow 重启后的对象回收也难以统一。

我们以 uCore 的进程、虚拟内存、VFS、IPC、调度器和 VirtIO 为基础，为 Agent workflow 建立内核级生命周期。普通进程继续使用 uCore 原有接口，Agent 通过受控入口取得 role、capability 与 scope，并在同一个 `workflow id + generation` 下使用 Context、工具、Live Query、事件与资源服务。

## 产品架构

<p align="center">
  <img src="docs/figures/architecture/agentos_overview.png" alt="AgentOS-uCore 产品架构" width="960">
</p>

AgentOS 功能模块位于 uCore 基础内核之上。身份与生命周期提供统一的 workflow 域；Context 和来源标签记录多轮因果；结构化工具与执行合同约束副作用；Live Query 把文件状态接入事件循环；资源账户和 workflow EEVDF 管理跨进程服务量。用户态 ABI 将这些机制组合成长期运行的 Agent Loop 和多 Agent workflow。

完整的层次、内核接入点和执行流程见[产品架构](docs/architecture.md)。

## 核心模块

| 模块 | 主要机制 | 产品能力 |
| --- | --- | --- |
| 身份与 Context | 可信映像、role/capability/scope、workflow generation、7 页 Context | 创建受控 Agent，保存 cause/span/branch 与来源关系 |
| 工具执行 | 25 项工具目录、typed V2、ENFORCE V3、compact batch、Task SQ/CQ | 在副作用前检查 schema、权限、前驱、输入指纹和资源额度 |
| Live Query | 文件 metadata catalog、`status/stage/kind` 索引、typed watch | 按业务语义查询文件，并用 `ENTER/UPDATE/LEAVE` 驱动增量处理 |
| Workflow 运行时 | 事件队列、可信 IPC、heartbeat、Credit Domain、workflow EEVDF | 休眠等待事件，按 workflow 管理资源与 CPU 服务量 |
| 一致性切片与回收 | 执行记录、workflow fence、统一 teardown | 生成 workflow 一致性 receipt，并在 close 后按 generation 回收关联对象 |

模块设计分别见[身份与 Context](docs/modules/identity-context.md)、[工具执行](docs/modules/tool-execution.md)、[Live Query](docs/modules/live-query.md)和 [Workflow 运行时](docs/modules/workflow-runtime.md)。

## 关键性能

性能活动在 30 次独立 QEMU 启动中保存了 33 个原始输出文件、19 个 CSV 数据表和 7,498 行记录。

| 路径 | 实测结果 |
| --- | --- |
| Live Query workflow core | 16/16 个 traversal/indexed 配对由 indexed 取得更短延迟，中位配对加速比 `3.118x` |
| Catalog 参数网格 | 目录规模 `24/64/96` 与命中数 `1/2/4/8` 的 12 个参数格，中位加速比为 `1.164x-2.808x` |
| 16-op 工具传输 | Batch、Scalar V3、SQ/CQ 的中位延迟为 `561/2051/1620.5 us` |
| Workflow EEVDF | 504 次精确唤醒均在 `0-1 tick`，并发 1 至 4 的 Jain 中位数均不低于 `0.99998` |

实验参数、配对统计和图表见[性能结果](docs/performance.md)。

## 三步运行

Linux 或 WSL 环境需要 Bash、Git、GNU Make、Host C 编译器、Python 3、QEMU RISC-V 和 RISC-V GNU toolchain。Ubuntu 工具链通常使用 `riscv64-linux-gnu-` 前缀。

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

交互 Agent Loop、Nexus 多 Agent workflow、双目标运行和外部模型连接方法见[运行指南](docs/usage.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [产品架构](docs/architecture.md) | uCore 基础、AgentOS 内核模块、ABI 与运行主线 |
| [API](docs/api.md) | 系统调用、版本化结构体、状态码和调用顺序 |
| [安全机制](docs/security.md) | 身份、scope、generation、执行合同与资源检查 |
| [运行指南](docs/usage.md) | 环境、构建、QEMU、Console、Nexus 与双目标运行 |
| [测试](docs/testing.md) | 静态契约、Guest 回归、故障测试和复现入口 |
| [性能](docs/performance.md) | 实验设计、统计结果、图表和逐样本数据 |
| [决赛文档](决赛文档.pdf) | AgentOS-uCore 完整产品文档 |
| [项目视频与 PPT](项目介绍视频和ppt网盘链接.txt) | 演示视频与答辩材料的网盘入口 |

主要源码位于 `os/`，共享 ABI 与策略契约位于 `include/`，Guest 封装与应用位于 `user/`，Host 串口和模型协议接入位于 `host_tools/`。项目基于 LearningOS/uCore 开发，源码采用 [GPL-3.0](LICENSE)，文档采用 [CC BY-SA 4.0](LICENSE-DOCS)。

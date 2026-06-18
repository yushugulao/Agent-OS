# project61-agentOS-happylegend

## 项目信息

| 项目 | 内容 |
| --- | --- |
| 比赛 | 2026 年全国大学生计算机系统能力大赛-操作系统设计赛（全国）-OS 功能挑战赛道 |
| 选题编号 | project61 |
| 赛题名称 | 面向 AI 智能体的操作系统内核（Agent-OS） |
| 队伍名称 | happy-legend |
| 平台 Project ID | 39809 |
| GitLab 仓库 | https://gitlab.eduxiji.net/T2026106149911107/project3136859-388870 |
| 当前交付分支 | `uCore` |

## 项目简介

本项目围绕 Agent-OS 赛题，在 uCore 教学操作系统内核中实现面向 Agent 的内核支持层。当前系统能够识别 Agent 进程，提供结构化内核工具调用，维护 Agent 多轮工具调用历史，并扩展出文件元数据查询、事件等待/唤醒和多 Agent 故障恢复演示。

uCore 分支不是只做任务一至三的最小版本。当前交付以任务一、任务二、任务三为高性能底座，同时实现了任务四、任务五和任务六的可运行演示级能力。文档结构按旧版项目文档风格重构：README 负责快速运行和材料索引，主设计文档解释架构和关键决策，API/ABI 文档说明用户态与内核边界，验证文档给出可复现证据，分任务文档展开细节。

## 基底来源

当前分支以 uCore 教学操作系统代码为基础，并引入 uCore 用户测试目录结构。项目相关实现主要集中在：

- `os/agent.c`
- `os/agent.h`
- `os/proc.c`
- `os/syscall.c`
- `os/trap.c`
- `user/include/agent.h`
- `user/lib/syscall.c`
- `user/src/agentfinal_ucore.c`
- `user/src/agentbench_ucore.c`
- `user/src/labdemo_ucore.c`
- `user/src/agentsecurity_ucore.c`

`os/` 是内核目录，`user/` 是用户态程序与测试目录，`nfs/` 用于生成用户程序文件系统镜像。

## 赛题对应关系

| 赛题任务 | 项目目标 | 当前状态 |
| --- | --- | --- |
| 任务一：Agent 进程创建与地址空间设计 | 支持 Agent 进程概念、进程元数据和 Agent Context 地址空间 | 已完成增强实现 |
| 任务二：Agent 与内核结构化交互 | 支持结构化工具调用、工具表、结果返回和错误语义 | 已完成增强实现 |
| 任务三：上下文路径管理 | 记录、查询、快照、回滚 Agent 多轮调用历史 | 已完成增强实现 |
| 任务四：面向 Agent 查询优化的文件系统扩展 | 支持文件元数据表、属性查询、索引路径和依赖查询 | 已完成演示级增强实现 |
| 任务五：Agent Loop 内核运行机制 | 支持 watch、wait、wake、heartbeat 和事件投递 | 已完成演示级增强实现 |
| 任务六：综合演示与创新 | 用多 Agent 实验恢复场景串联任务一至五 | 已完成 `labdemo_ucore` 综合演示 |

需要明确：任务四当前实现的是内核文件元数据服务和索引查询，不是对真实磁盘目录的持续后台扫描；任务五当前实现的是可验证的事件等待与唤醒机制，不是完整平台级 Agent 调度系统。

## 构建与运行

已验证开发环境：WSL2 Ubuntu 26.04。

通用运行要求：

- Linux 环境；
- RISC-V GCC/binutils；
- QEMU riscv64；
- make；
- git。

当前验证使用 `riscv64-linux-gnu-` 工具链。

构建用户程序、文件系统镜像和内核：

```bash
cd project61-agentOS-happylegend-uCore
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

推荐使用脚本顺序运行最终测试：

```bash
bash scripts/run-agent-tests.sh
```

也可以分别以 init 进程方式运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

如果希望进入用户 shell，可以运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=usershell CHAPTER=agent
```

进入 shell 后可手动运行：

```sh
agentfinal_ucore
agentbench_ucore
labdemo_ucore
agentsecurity_ucore
```

## 最终测试入口

| 程序 | 定位 | 期望通过输出 |
| --- | --- | --- |
| `agentfinal_ucore` | 任务一至三功能验收，同时覆盖文件索引和事件自唤醒 | `agentfinal_ucore: passed` |
| `agentbench_ucore` | 任务一至五性能验证，包括 batch、direct context、snapshot、文件查询和 wait/wake | `agentbench_ucore: passed` |
| `labdemo_ucore` | 多 Agent 综合演示，普通 init 只启动 orchestrator，后续元数据初始化、事件注入和角色 Agent 创建都由 orchestrator 完成 | `labdemo_ucore: passed` |
| `agentsecurity_ucore` | 权限边界负向测试，覆盖普通进程直接写元数据/投事件、sentinel 伪造 recovery、真实 recovery 幂等恢复 | `agentsecurity_ucore: passed` |

`agentfinal_ucore` 预期输出包括：

```text
agentfinal_ucore: context size=16384 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: fifo oldest=65 latest=192 dropped=64
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: passed
```

`agentbench_ucore` 输出性能表，字段含义如下：

```text
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
```

`ticks` 会随 QEMU 和宿主机负载波动，评审时应关注测试是否通过、相对趋势是否符合设计，而不是固定绝对数值。

`labdemo_ucore` 会输出结构化演示事件，例如：

```text
agentos:event type=AGENT_CREATED role=orchestrator
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=ACTION role=recovery stage=align status=OK
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
```

`agentsecurity_ucore` 预期输出包括：

```text
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: passed
```

## 当前交付材料

| 材料 | 位置 |
| --- | --- |
| 文档索引 | [docs/README.md](docs/README.md) |
| 文档标准采用说明 | [docs/documentation-standard.md](docs/documentation-standard.md) |
| 主设计文档 | [docs/design.md](docs/design.md) |
| 赛题要求追踪表 | [docs/requirements-traceability.md](docs/requirements-traceability.md) |
| API 与 ABI | [docs/api.md](docs/api.md) |
| 验证与性能评估 | [docs/verification.md](docs/verification.md) |
| 测试内容详细说明 | [docs/testing-details.md](docs/testing-details.md) |
| 演示脚本 | [docs/demo-script.md](docs/demo-script.md) |
| 任务一细节附录 | [docs/task1-agent-process.md](docs/task1-agent-process.md) |
| 任务二细节附录 | [docs/task2-agent-call.md](docs/task2-agent-call.md) |
| 任务三细节附录 | [docs/task3-context-path.md](docs/task3-context-path.md) |
| 任务四细节附录 | [docs/task4-file-query.md](docs/task4-file-query.md) |
| 任务五细节附录 | [docs/task5-agent-loop.md](docs/task5-agent-loop.md) |
| 当前测试记录 | [docs/test-record.md](docs/test-record.md) |
| 源代码许可 | [LICENSE](LICENSE) |
| 文档与答辩材料许可 | [DOCUMENTATION_LICENSE.md](DOCUMENTATION_LICENSE.md) |
| 第三方声明 | [NOTICE](NOTICE) |

## 仍需补充

- 更复杂的真实目录后台扫描和持久化文件索引；
- 更完整的 Agent 调度策略、优先级和取消机制；
- 云端 LLM Gateway；
- 宿主机可视化大屏；
- 进展汇报幻灯片；
- 作品演示视频。

## 许可声明

本仓库作为参赛作品提交的源代码采用 [GPL-3.0](LICENSE) 许可。

本队伍原创的技术文档、答辩材料、汇报幻灯片和演示视频采用 Creative Commons Attribution-ShareAlike 4.0 International 许可，详见 [DOCUMENTATION_LICENSE.md](DOCUMENTATION_LICENSE.md)。

第三方来源和许可说明见 [NOTICE](NOTICE)。如后续引用、复制或改编非本队伍来源的代码、文档或公开项目内容，将在对应源码位置、文档位置、设计文档和答辩材料中明确标注来源、用途、授权信息及本项目的增量贡献。

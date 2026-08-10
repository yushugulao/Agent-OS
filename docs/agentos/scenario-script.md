# 现场演示脚本

现场演示从可用产品入口开始，不逐个运行内部合同测试。首次准备环境时先看
[Windows 快速开始](../windows-quickstart.md)和[竞赛交付说明](../contest/README.md)。

## 启动

```bash
make contest-demo TOOLPREFIX=riscv-none-elf-
```

该命令默认运行 4 个等量 AB/BA QEMU 样本，验证 traversal 与 indexed 路径得到
相同科研结果，并把逐样本串口日志、`summary.json`、`measurements.csv` 和
`report.md` 写入 `results/contest-demo/`。这些输出属于本次运行，文档不预填
尚未实测的数值。需要复核完整内核行为时运行：

```bash
make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenteval_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

完整 case 集由 QEMU runner 维护，本文不复制易漂移的数量。需要聚焦演示中的某条
路径时可设置 `AGENT_TEST_CASE` 直接运行对应 Guest 程序。

## 综合科研流程

串口场景可单独运行：

```bash
make run INIT_PROC=labdemo_ucore CHAPTER=agent LOG=error TOOLPREFIX=riscv-none-elf-
```

场景是一条 prepare、align、analyze、report、archive 科研流水线。可信
Orchestrator 创建 Sentinel、Investigator 和 Recovery，并为它们建立同一
workflow 内的最小消息路由：

1. Orchestrator 注册运行对象、文件属性和依赖。
2. Sentinel watch 失败状态，随后进入内核等待队列而不是忙轮询。
3. Orchestrator 注入 align 失败，文件状态变化发布为结构化事件。
4. Sentinel 查询索引和依赖关系；越权恢复被内核拒绝并留下审计记录。
5. Sentinel 沿授权端点把调查请求交给 Investigator，相关 provenance 一并传播。
6. Investigator 读取摘要与依赖，形成恢复计划和 Context 记录。
7. Investigator 唤醒 Recovery；Recovery 在能力与 workflow scope 同时匹配后提交动作。
8. 相同 correlation id 的第二次动作被识别为重复，不重复产生副作用。
9. Recovery 更新报告工件，最终状态变为 recovered。
10. Orchestrator 在本 workflow 内查询 timeline、audit 和 provenance，复核整条因果链。

### 观察点

| 入口 | 本次运行应观察的字段 |
| --- | --- |
| `make contest-demo` | 每个 AB/BA boot 的 traversal/indexed core 时间、检查记录数、读取字节数、相同结果 hash；汇总值见 `summary.json` 与 `measurements.csv` |
| `agentbench_ucore` | scalar/batch、mirror/query、scan/index 的样本数、tick 和工作量计数 |
| `agentloop_ucore` | heartbeat、事件入队、真实 sleep/wakeup、取消和无事件路径 |
| `agentsched_ucore` / `agent_eevdf_ucore` | Jain fairness、wakeup 分布、deadline miss、普通任务与 workflow 进展 |
| `agentfinal_ucore` | Context sequence、branch、rollback、FIFO 窗口，以及可信只读区与用户 cache 的不同权限 |
| `kernel-stack-check` | 真实调用图上的内核栈上界；它不是运行时内存或性能测量 |

串口出现 `labdemo_ucore: passed` 与 `labdemo_ucore: parent passed` 表示该次场景
完成；性能结论仍应读取该次实际运行的工作量、tick 和 Host 时间，并说明样本与单位。

## 定向讲解

需要展示单一机制时，优先选择下列程序，不再按内部 marker 逐行讲解：

| 程序 | 适合展示的机制 |
| --- | --- |
| `agentfinal_ucore` | Agent 身份、批量调用、Context、rollback、timeline |
| `agentfs_ucore` | inode 绑定、显式文件属性、内容摘要与当前启动周期索引 |
| `agentloop_ucore` | 事件队列、等待/唤醒、heartbeat 与取消 |
| `agentsched_ucore` | 角色调度、域级公平与普通任务进展 |
| `agentbench_ucore` | scalar/batch、mirror/query、scan/index 对照 |
| `agentscope_ucore` | workflow 隔离、撤销、配额与资源复用 |

示例：

```bash
make run INIT_PROC=agentbench_ucore CHAPTER=agent LOG=error TOOLPREFIX=riscv-none-elf-
```

安全拒绝、文件系统/VirtIO 故障和 teardown 竞争属于产品回归，不占用主演示叙事。
对应入口和测试边界见 [验证说明](verification.md) 与
[要求追踪表](requirements-traceability.md)。

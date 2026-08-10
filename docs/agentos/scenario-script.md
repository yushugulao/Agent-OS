# 现场演示脚本

现场演示从可用产品入口开始，不逐个运行内部合同测试。环境准备和 Windows 命令见
[竞赛交付说明](../contest/README.md)。

## 启动

```bash
make contest-demo
```

该命令运行评委可观察的本地演示。演示展示实际行为和测量值，不以“测试通过”
作为性能结论。需要复核完整内核行为时运行：

```bash
make agentos-test
```

完整 case 集由 QEMU runner 维护，本文不复制易漂移的数量。需要聚焦演示中的某条
路径时可设置 `AGENT_TEST_CASE` 直接运行对应 Guest 程序。

## 综合科研流程

串口场景可单独运行：

```bash
make run INIT_PROC=labdemo_ucore CHAPTER=agent LOG=error
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

| 页面或串口数据 | 说明 |
| --- | --- |
| syscall 数、scalar/batch 耗时 | 批量工具调用减少传统陷入成本 |
| scan/index 检查记录数 | 文件属性索引减少无关记录扫描 |
| p50/p90/p99 wait 与 goodput | 并发 Agent 的等待和完成能力 |
| Jain fairness、域级进展 | 高负载下普通进程和其他 workflow 不被饿死 |
| Context branch 与 rollback | 回滚创建新分支，不改写 provenance 历史 |
| MESSAGE、ACTION、ARTIFACT | IPC、授权动作和工件更新由同一 scope 串联 |
| resource/BSS/stack 指标 | 性能收益没有通过隐藏常驻内存或栈增长获得 |

串口出现 `labdemo_ucore: passed` 与 `labdemo_ucore: parent passed` 表示该次场景
完成；性能结论仍应读取该次实际运行的工作量、tick 和 Host 时间，并说明样本与单位。

## 定向讲解

需要展示单一机制时，优先选择下列程序，不再按内部 marker 逐行讲解：

| 程序 | 适合展示的机制 |
| --- | --- |
| `agentfinal_ucore` | Agent 身份、批量调用、Context、rollback、timeline |
| `agentfs_ucore` | inode 绑定、文件属性、索引与持久化 |
| `agentloop_ucore` | 事件队列、等待/唤醒、heartbeat 与取消 |
| `agentsched_ucore` | 角色调度、域级公平与普通任务进展 |
| `agentbench_ucore` | scalar/batch、mirror/query、scan/index 对照 |
| `agentscope_ucore` | workflow 隔离、撤销、配额与资源复用 |

示例：

```bash
make run INIT_PROC=agentbench_ucore CHAPTER=agent LOG=error
```

安全、掉电恢复、VirtIO 故障和 teardown 竞争属于产品回归，不占用主演示叙事。
对应入口和测试边界见 [验证说明](verification.md) 与
[要求追踪表](requirements-traceability.md)。

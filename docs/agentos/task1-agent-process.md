# 任务一：Agent 进程与地址空间

任务一在 uCore 进程模型上增加可信 Agent 身份、workflow 生命周期和
Agent Context 映射。总体架构见 [design.md](design.md)，稳定 ABI 见
[api.md](api.md)。本文只保留实现边界，不复制会随版本变化的结构体和预算数值。

## 身份与创建

Agent 角色来自内核验证过的不可变可执行映像，不接受用户传入的自声明权限。
普通进程不能创建 Orchestrator；新的 workflow 只能由可信 bootstrap factory
建立，并获得不可变 lifecycle id、generation、资源域和唯一 controller。

创建路径采用一项事务：验证映像和委派、预留资源、建立地址空间、安装身份，
最后才发布可调度进程。任一步失败都会取消预留并释放已经建立的页、文件引用和
生命周期成员关系。worker 创建和一次性 pipe 端点委派复用相同的身份边界，
不会把继续委派权自动传给后代。

## 地址空间

用户代码保持 RX，数据、栈和 Context 保持 RW+NX。Context 用户映射只是读取
镜像；可信历史保存在内核 shadow 中，完整请求、响应和来源归属位于按活跃
Agent 分配的私有 sidecar。普通进程和未使用 Agent 功能的进程不承担这部分
常驻内存。

内核将 mirror、shadow 和 sidecar 作为一个资源申请统一接纳和结算。Context
查询从 shadow 返回，用户直接修改镜像不能改变内核身份、历史或授权结果。

## 生命周期

workflow 状态沿 `ACTIVE -> CLOSING -> RETIRING -> RETIRED` 单向推进。
controller 退出或显式关闭会递增撤销 generation，阻止新能力、对象和 IPC
操作；成员在安全点观察撤销并进入统一 teardown。退出、强制撤销和失败回滚
最终都经过同一 teardown 状态机，集中释放线程、文件、存储、I/O、Context 和
观测资源，避免各子系统维护互相冲突的退出路径。

僵尸完成记录与 PCB 生命周期分离，因此父进程可以读取退出状态，而已退出
子进程不必长期占用完整进程槽。进程、线程和保留槽由通用资源控制器统一接纳，
普通资源域不能耗尽系统控制面容量。

## 验证

- `agenteval_ucore` 验证角色、地址空间、Context 和任务一功能闭包。
- `agentscope_ucore` 验证 workflow 隔离、委派和 generation 撤销。
- `workflow_teardown_race_ucore` 组合覆盖退出、阻塞 syscall 与资源结算竞争。
- `procreap_ucore` 验证完成记录、父进程拒绝 wait 和进程槽复用。
- `make kernel-budget-check` 约束 `struct proc`、BSS、栈和按需 Agent 状态。

正式命令和证据边界见 [verification.md](verification.md)。

# AgentOS 内核验证

本文给出当前架构的验证顺序和断言边界。功能与性能结果来自实际 Host/QEMU 测试。

## 1. 验证层次

| 层次 | 回答的问题 | 不能证明 |
| --- | --- | --- |
| focused checker | 关键调用顺序、生产对象边界、UAPI 布局是否漂移 | 真实 Guest 调度/设备行为 |
| Host 模型与 mutation test | U/P/F、ring、resync、fence 的不变量是否能拒绝变异 | RISC-V 二进制已运行 |
| cross build/link | 当前生产对象是否能编译链接，调用图栈是否安全 | syscall 运行语义 |
| QEMU Guest 专项 | fork/exec/IPC/VFS/event/fence 等动态行为 | 超出该场景的普遍结论 |
| paired/performance run | 双目标完整负载、工作量和实际测量 | 单个机制的因果贡献 |

## 2. 快速静态验证

```bash
python -B scripts/check-agent-uapi-layout.py
python -B scripts/check-agent-live-query-fs.py
python -B scripts/check-workflow-fence.py
bash scripts/check-agent-module-boundaries.sh
```

预期关注：

- fence request 56 字节、receipt 320 字节及字段 offset；
- `agent_run(count=0, FENCE)` 的唯一 syscall cut；
- lifecycle fence gate 在 metadata、credit、evidence seal 外层；
- live query 只接受显式 volatile metadata，typed watch 不回退字符串路径；
- kernel/user UAPI 布局一致，生产模块依赖没有越界。

## 3. 核心模型和 mutation tests

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
```

### 3.1 Credit Domain

断言包括：

- `held=U+P+F`，全局/账户 hard limit 在 refill 前验证；
- reserve/commit/cancel/release 只在 U/P/F 间移动；
- vector 失败无部分提交；
- 压力只能 trim F；
- context switch 离开账户执行 trim；
- fence 单锁 trim exec/storage、要求 P 为 0，并导出 exact U。

### 3.2 Evidence Ring

断言包括：

- 4 页、48 ordinary、16 critical 和 256 字节槽布局；
- reserve/fill/commit/discard 与 ticket 匹配；
- 早期 BUSY 隐藏后续发布；
- ordinary success 不写第二份 legacy Context；
- deny/authority 进入 critical 并保留兼容投影；
- gap、rollover、previous root、challenge、credit digest 进入 seal；
- retained internal retirement 不能冒充 workflow fence；
- receipt 只在显式 fence 后发布 `FENCE_SEALED`。

### 3.3 Live Query

断言包括：

- metadata 只通过普通 set 或显式 delete 进入当前启动周期 catalog；
- typed query 安装、生命周期绑定和 proc reuse 清理；
- before/after 导出 `ENTER/UPDATE/LEAVE`；
- unlink/content pending 带完整 incarnation；
- 队列失败生成单调 resync generation；
- ACK 不清除更新缺口；
- fence drain 拒绝未确认 resync。

### 3.4 Workflow fence

断言包括：

- controller/capability/request 验证；
- quiescence 顺序为 metadata/live-query、fs cut、credit exact、evidence seal；
- 失败不推进 fence sequence/root；
- receipt flags 明确 partial/exact/sealed/volatile；
- request id/challenge 幂等、conflict、stale 和 copyout retry cache。

## 4. 构建、模块和栈安全

```bash
make build TOOLPREFIX=riscv-none-elf-
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-stack-check TOOLPREFIX=riscv-none-elf-
```

`agent-module-check` 验证生产对象清单和模块依赖。`kernel-stack-check` 检查真实编译调用图上的线程栈和启动栈安全边界。源码行数、镜像大小基线和工具可执行文件身份不作为产品功能结论。

## 5. QEMU Guest 验证

开发阶段的统一入口：

```bash
make agentos-test TOOLPREFIX=riscv-none-elf-
```

Guest 专项应覆盖：

- Agent create/role/capability 与可信 exec；
- fork/exec/exit、member/closing 和 scope reuse；
- tool call/batch、Context push/query/rollback；
- 显式 metadata、index/scan 一致性、inode incarnation；
- typed live watch、event wait、route、timeout/cancel；
- resource hard quota、reservation rollback 和 teardown；
- workflow fence 的 receipt、retry 和拒绝路径。

## 6. 双目标与性能

plain uCore 与 AgentOS-uCore 运行同一用户态科研工作流合同。端到端 paired run 用于整体比较；Credit Domain、Evidence Ring 或 Live Query 的单项性能结论还需要同内核消融、工作量计数或专项 benchmark，不能从双目标总差异直接归因。

引用测量结果时应：

- 说明 target、seed、工具链、QEMU 和 Host 环境；
- 保留本次 Plain 与 AgentOS 的 Guest 输出和比较摘要；
- 报告样本数、单位、失败样本和聚合方法；
- 在 Host 侧复核状态一致性和测量计算；
- 缺失数据时不推导或补造数字。

直接命令见 [../verification.md](../verification.md)。调试时可以自由重跑；对外引用数字时清楚说明选用的是哪次实际运行即可。

## 7. 能力边界检查

验证还要确认文档中的边界与实际行为一致：

| 边界 | 可执行检查 |
| --- | --- |
| metadata 仅显式登记 | 普通文件 create 不增加 catalog；set/delete 绑定 scope 与 incarnation |
| catalog 属于当前启动周期 | receipt 设置 `METADATA_VOLATILE`，新启动由用户态重新登记 |
| Evidence 为有界内存事件 | ordinary success 只写一次 canonical ring event；critical 分区独立 |
| fence root 覆盖范围有限 | receipt 同时设置 `PARTIAL_COVERAGE` 与 `FENCE_SEALED` |
| lifecycle 状态机精简 | members/closing/operation/departure/fence gates 决定 cut 与回收 |

## 8. 结果解释

本地 checker 通过只说明对应静态合同成立，构建通过只说明当前目标可编译链接。功能结论要由实际 Guest 场景确认，性能结论要由实际负载和测量确认。内核 `Evidence Ring` 及 fence receipt 验证的是产品运行期安全语义，不是仓库发布工件。

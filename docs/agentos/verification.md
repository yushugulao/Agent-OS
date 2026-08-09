# AgentOS 内核验证

本文给出当前架构的验证顺序和断言边界。它不内嵌某次运行的通过数或性能数字；发布结果只从冻结 evidence bundle 读取。

## 1. 验证层次

| 层次 | 回答的问题 | 不能证明 |
| --- | --- | --- |
| source contract/checker | 关键调用顺序、生产对象边界、UAPI 布局是否漂移 | 真实 Guest 调度/设备行为 |
| Host 模型与 mutation test | U/P/F、ring、resync、fence 的不变量是否能拒绝变异 | RISC-V 二进制已运行 |
| cross build/link | 当前生产对象是否能编译链接，体积/栈预算是否满足 | syscall 运行语义 |
| QEMU Guest 专项 | fork/exec/IPC/VFS/event/fence 等动态行为 | Host 证据包真实性 |
| paired/full verification | 双目标完整负载、原始日志和环境绑定 | 超出 receipt/实验范围的普遍结论 |

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
- observe recovery dispatcher 固定 `BAD_PARAM`；
- retired metadata/observe disk 模块不进入生产对象清单。

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
- `DURABLE` 兼容名实际映射 `FENCE_SEALED`。

### 3.3 Live Query

断言包括：

- 显式 set 拒绝 `PERSIST/AUTOSCAN`；
- typed query 安装、生命周期绑定和 proc reuse 清理；
- before/after 导出 `ENTER/UPDATE/LEAVE`；
- tombstone/content pending 带完整 incarnation；
- 队列失败生成单调 resync generation；
- ACK 不清除更新缺口；
- fence drain 拒绝未确认 resync；
- retired store/scan/recovery 不在生产构建路径。

### 3.4 Workflow fence

断言包括：

- controller/capability/request 验证；
- quiescence 顺序为 metadata/live-query、fs cut、credit exact、evidence seal；
- 失败不推进 fence sequence/root；
- receipt flags 明确 partial/exact/sealed/volatile；
- request id/challenge 幂等、conflict、stale 和 copyout retry cache。

## 4. 构建、模块和预算

```bash
make build TOOLPREFIX=riscv-none-elf-
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-budget-check TOOLPREFIX=riscv-none-elf-
make kernel-stack-check TOOLPREFIX=riscv-none-elf-
```

`agent-module-check` 验证生产对象清单和模块依赖，避免仅因历史 `.c` 文件仍在树中就把停产能力算作当前实现。`kernel-budget-check` 只可按 active source/object 口径重基线，不应通过把 retired 参考源码重新加入生产统计或简单放宽上限来通过。

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
- workflow fence 的 receipt、retry 和拒绝路径；
- observe recovery/PERSIST/AUTOSCAN legacy 调用被拒绝。

测试程序若仍请求旧 flag 或旧 recovery 成功语义，必须先更新测试合同，不能为让旧用例通过而恢复停产机制。

## 6. 双目标与性能

plain uCore 与 AgentOS-uCore 运行同一用户态科研工作流合同。端到端 paired run 用于整体比较；Credit Domain、Evidence Ring 或 Live Query 的单项性能结论还需要同内核消融、工作量计数或专项 benchmark，不能从双目标总差异直接归因。

正式测量必须：

- 使用干净、已绑定提交；
- 固定 target order/seed/工具链和 Host 环境；
- 保留 Plain 与 AgentOS 原始 Guest/Host 日志；
- 按要求只执行指定次数的 QEMU，额外试跑不得混入正式 pair；
- 在 Host 侧重算状态、hash、样本和阈值；
- 失败或缺失时保持 unavailable。

具体流程见 [../verification.md](../verification.md) 和 `host_tools/` 的版本化合同。

## 7. 负面能力检查

验证不仅检查存在的能力，也检查以下声明必须为假：

| 禁止声明 | 可执行检查 |
| --- | --- |
| metadata 自动扫描普通目录 | 显式 set 合同、生产对象无 scan 调用 |
| metadata catalog crash recovery | 无 store/recovery 生产对象，receipt 带 `METADATA_VOLATILE` |
| 每个成功操作写 durable audit | ordinary success 只写 ring；disk observe 模块退役 |
| fence seal 等于磁盘 durable | UAPI/ABI 把 durability 状态命名为 `FENCE_SEALED` |
| observe recovery 可用 | syscall dispatcher 固定 `BAD_PARAM` |
| 多阶段 workflow retirement | lifecycle 状态以 members/closing/gates 为准 |

## 8. 发布判定

本地测试通过只说明当前工作树满足相应合同。发布判定还需要 release manifest、提交身份、原始工件、校验和与 semantic replay。文档示例行、Dashboard 或 Guest 自报 `passed` 不能独立构成发布证据。

# 构建、验证与证据边界

本文说明如何验证 plain uCore 对照目标与 AgentOS-uCore 增强目标。命令和清单会继续演进，最终以 Makefile、`ci/*.json` 和对应 checker 为准。发布状态只由 [正式证据索引](../evidence/releases/INDEX.md) 指向的冻结 bundle 决定。

## 1. 三条边界

1. **开发验证不等于发布证据**：工作树上的 build/test 只说明当前源码状态。
2. **内核 fence 不等于 Host 发布 receipt**：workflow fence 证明当前运行期的 partial evidence/credit/metadata cut；正式 bundle 还需提交、环境、原始日志和 checksum。
3. **paired 总耗时不等于单机制归因**：双目标回答端到端差异，Credit Domain/Evidence Ring/Live Query 的贡献需同内核消融或专项工作量证明。

## 2. 环境检查

Windows：

```powershell
.\scripts\check-windows-prereqs.ps1
```

Linux/WSL/项目工具链环境：

```bash
make doctor
```

正式证据应记录编译器、binutils、QEMU、Python、Bash、Host/WSL 版本、命令行和环境 hash。文档中的工具链前缀是本仓库当前推荐值，实际发布必须与 manifest 一致。

## 3. 无 QEMU 的快速闭环

```bash
python -B scripts/check-agent-uapi-layout.py
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py

make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-budget-check TOOLPREFIX=riscv-none-elf-
make build TOOLPREFIX=riscv-none-elf-
```

这些检查覆盖：

- active source/object 边界，retired disk metadata/observe 模块不进入生产链接；
- UAPI 大小/offset、fence request/receipt；
- U/P/F hard admission、批量 credit、trim 和 exact snapshot；
- Evidence Ring ordinary/critical、ticket order、gap、seal；
- explicit volatile metadata、typed live query 和 resync；
- lifecycle member/closing/gates 与 fence syscall cut；
- kernel source/text/BSS/stack 等预算。

若 checker 因架构切换失败，应更新 active inventory 和降低/重建合理 baseline，不应通过恢复停产模块或简单放宽硬预算规避。

## 4. AgentOS Guest 测试

```bash
make agentos-test TOOLPREFIX=riscv-none-elf-
```

Guest 套件的唯一 case 清单来自 `ci/kernel-budgets.json`。文档不复制易漂移的数量。测试主题包括 Agent 身份、tool、Context、scope/VFS、metadata/query、live event、IPC/wait、调度、资源、线程与 teardown。

架构切换后的强制负面用例：

- `AGENT_FILE_META_F_PERSIST` 返回 `BAD_PARAM`；
- `AGENT_FILE_META_F_AUTOSCAN` 返回 `BAD_PARAM`；
- observe recovery syscall 返回 `BAD_PARAM`；
- 普通文件 create 不会无显式 set 出现在 metadata query；
- typed watch 在缺口时要求 resync，旧 ACK 不清除新 generation；
- fence 非 controller/跨 lifecycle/有 active operation 时拒绝或 retry；
- receipt 明确 `METADATA_VOLATILE` 与 `PARTIAL_COVERAGE`。

旧测试若期待 metadata 双 bank、目录 autoscan 或 observation crash recovery 成功，测试合同需要迁移，不能据此恢复旧机制。

## 5. plain 与 AgentOS 双目标

```bash
make plain-platform-build TOOLPREFIX=riscv-none-elf-
make agentos-platform-build TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
```

`baseline_ucore/` 是共享安全修复但没有 AgentOS 子系统的对照，不是未修改上游。两侧运行同一用户态科研 action/state 合同，Host 从各自镜像和日志提取状态，再比较规范字段。

双目标验证应同时保留：

- 两侧 kernel/user build 身份；
- 两侧 QEMU command、退出状态和 Guest log；
- Host run receipt；
- 提取后的 state inventory、逐文件长度/hash；
- compare summary 和原始输入；
- target order/seed/timeout。

同一正式 pair 的命令计数必须预先固定。为调试增加的 QEMU 启动必须与正式 artifact 目录隔离，不能把更有利的一次挑选为正式样本。

## 6. Workflow fence 验收

fence 通过 `agent_workflow_fence()`，即 `agent_run(count=0, AGENT_RUN_F_FENCE)` 发起。Host/Guest 验证至少重算或检查：

- request version/size/id/challenge；
- lifecycle key、controller 权限和 fence sequence；
- receipt 固定 320 字节；
- flags 同时包含 partial coverage、credit exact、evidence sealed、metadata volatile；
- `resource_used[8]` 是 trim 后 U，P 为 0；
- credit digest 绑定 key、epoch、account key 和 U；
- previous root/root 链、ticket range、event/gap count；
- 相同 request id/challenge 的 retry receipt 字节一致；
- conflicting/stale id 和 failed fence 不推进 root。

receipt 不可用于以下主张：

- evidence 已落盘；
- metadata catalog 可重启恢复；
- 普通文件内容已进入 Merkle/SHA root；
- 所有 scheduler/Host 行为均被覆盖；
- receipt 本身已由外部密钥签名。

## 7. 正式评价流程

正式入口仍按仓库版本化工具执行：

```bash
export AGENT_TEST_DURATION_PROFILE=local-e3
make evaluation-doctor
make evaluation-smoke
make evaluation-run TOOLPREFIX=riscv-none-elf-
make evaluation-verify
make evaluation-kernel-cost TOOLPREFIX=riscv-none-elf-
make evaluation-full-verify TOOLPREFIX=riscv-none-elf-
make evaluation-dashboard
make evaluation-package
```

`evaluation-package` 之后只把新 bundle 和 `evidence/releases/INDEX.md` 提交为证据
提交 E。最后在包含 E 的干净 checkout 中显式指定包路径复核：

```bash
make evaluation-package-verify \
  EVALUATION_BUNDLE_DIR=evidence/releases/<bundle>
```

具体依赖和选项以 `make` 输出及 [评价方法](evaluation.md) 为准。原则保持不变：

1. 先冻结源码提交 C，并验证 clean tree；
2. 在受控环境执行指定 build/QEMU/Host 命令；
3. 原子发布 run summary、raw inventory 和 measurement receipt；
4. 从原始日志生成派生 CSV/JSON，保存来源 hash/行号/命令/commit/run id；
5. 生成 Dashboard，但 Dashboard 不反向成为数据源；
6. evidence 提交 E 只加入已验证 bundle/索引，并保持可审计 C 到 E 关系。

若现有正式采集器仍把已停产 metadata/observe disk recovery 当必需 artifact，应先迁移采集合同，再运行正式 campaign。不能生成伪 recovery 日志填补旧 schema。

## 8. 证据等级

| 等级 | 所需材料 | 可声称 |
| --- | --- | --- |
| Source | 源码、checker、mutation/model test | 设计/合同存在并可静态审计 |
| Build | 固定工具链成功编译链接、预算通过 | 当前 active production image 可构建 |
| Guest | 原始 QEMU log、退出状态、唯一 marker 和 Host parser | 指定 Guest 场景运行 |
| Paired | 两目标同合同、固定 order/seed、两侧 receipt/state | 指定端到端样本可比较 |
| Release | clean C、完整 raw、manifest、checksum、semantic replay、C到E历史 | 冻结 bundle 内声明可复验 |

低等级不能自动升级为高等级。特别是静态 `55/55`、Guest `passed` 或 workflow `EVIDENCE_SEALED` 都不是 Release 等级。

## 9. 性能阅读

发布性能表必须给出单位、样本数、聚合方法、失败样本、warm/cold 定义和来源 raw。建议分别观察：

- resource hot path 操作与 trim/fence 次数；
- ordinary Context 写入和 critical compatibility projection；
- scan/index 候选量、query ticks、live event/resync；
- fence latency、event/gap count 与 exact credit cut；
- paired end-to-end completion time/state parity。

“超过 plain 50%”只能对预先定义、方向明确、同负载的指标逐项判断。功能通过数、代码行和缺失样本不能换算为性能提升。

## 10. 清理与复现

```bash
make clean-workspace-dry-run
make clean-workspace
```

清理前先确认正式 raw 已进入隔离 artifact 目录。不要在正式 pair 中间运行会改写镜像、清空状态或增加 QEMU 次数的命令。

## 11. 当前架构负面声明

| 名称 | 当前事实 |
| --- | --- |
| metadata persistence/autoscan | 不支持，legacy flags 被拒绝 |
| metadata crash catalog | 不存在，catalog/index/watch 全部 volatile |
| observation recovery | syscall tombstone，固定 `BAD_PARAM` |
| durable audit receipt | 只有 fence-sealed 运行期语义；`DURABLE` 为兼容别名 |
| multi-stage retirement | 不作为当前 lifecycle；使用 members/closing/gates |
| full evidence coverage | 不提供；receipt 明确 partial coverage |

正式说明、演示口径和 Dashboard 文案都必须遵守这些边界。

# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。逐项流程见 [testing-details.md](testing-details.md)，原始输出样例见 [test-record.md](test-record.md)。

## 验证组织方式

AgentOS-uCore 的验证分三层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建。 |
| 专项测试 | `make agentos-test` 或 `bash scripts/run-agent-tests.sh` | 在 QEMU 中逐项运行 AgentOS 测试程序。 |
| 双目标验证 | `make dual-platform-run`、`make full-verify` | 运行普通 uCore 与 AgentOS-uCore 的同一科研平台负载，并生成对照结果。 |

专项测试只关注根目录 AgentOS-uCore 目标。双目标验证同时使用根目录目标和 `baseline_ucore/` 普通目标，详情见 [../verification.md](../verification.md)。

## 验证环境

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Linux / WSL2 Ubuntu |
| 工具链 | `riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-ld`、`riscv64-linux-gnu-objdump` |
| 虚拟机 | `qemu-system-riscv64` |
| 构建工具 | `make`、`bash`、`python3` |
| 默认模型路径 | 专项测试默认使用模板 LLM Relay，不访问云端模型 |

依赖检查入口：

```bash
make doctor
```

Windows 侧检查入口：

```powershell
.\scripts\check-windows-prereqs.ps1
```

## 构建命令

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
```

只需要构建用户态测试程序时：

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
```

## 专项测试入口

推荐直接运行：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

等价脚本入口：

```bash
bash scripts/run-agent-tests.sh
```

脚本会按顺序启动 QEMU，并运行以下测试程序：

| 测试程序 | 覆盖重点 | 通过标记 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、Context 映射、批量工具调用、Context Path、snapshot、rollback、用户 cache、timeline、provenance、Run Ledger。 | `agentfinal_ucore: parent passed` |
| `agentfs_ucore` | 真实 inode 绑定、`.agentmeta`、属性查询、索引查询、查询缓存、内容摘要、预取提示、文件删除清理。 | `agentfs_ucore: parent passed` |
| `agentscan_ucore` | 根目录自动扫描、自动 metadata 写入、文件创建和删除后的 metadata 更新。 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO 事件队列、watch/unwatch、timeout 睡眠、heartbeat、wait cancel、事件因果继承。 | `agentloop_ucore: parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录、公平性观测。 | `agentsched_ucore: parent passed` |
| `agentconflict_ucore` | 文件编辑租约、非持有者写入拒绝、版本提交检查、普通进程拒绝。 | `agentconflict_ucore: parent passed` |
| `agentllm_ucore` | 结构化 LLM 请求、Relay Agent 模板响应、LLM capability、完成事件、Context/timeline 记录。 | `agentllm_ucore: parent passed` |
| `agentbench_ucore` | 批量工具调用、Context 快照、文件查询 scan/index 对照、查询缓存、预取提示、timeout/heartbeat 计时观测。 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 综合场景中的性能入口，包装运行 `agentbench_ucore`。 | `labbench_ucore: parent passed` |
| `labdemo_ucore` | 多 Agent 科研恢复场景、文件查询、预取交接、消息唤醒、权限拒绝、恢复动作、audit、timeline、provenance。 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 普通进程拒绝、低权限 Agent 伪造拒绝、`.agentmeta` 保护、scoped action/artifact、全局审计权限、基础 mail/trace。 | `agentsecurity_ucore: parent passed` |

原始输出不在本文档重复展开，统一保存在 [test-record.md](test-record.md)。每个测试的流程和断言解释见 [testing-details.md](testing-details.md)。

## 覆盖关系

| 赛题任务 | 对应测试 |
| --- | --- |
| 任务一：Agent 进程与地址空间 | `agentfinal_ucore`、`agentsecurity_ucore` |
| 任务二：结构化工具调用 | `agentfinal_ucore`、`agentbench_ucore`、`agentsecurity_ucore` |
| 任务三：Context Path | `agentfinal_ucore`、`agentscan_ucore`、`labdemo_ucore` |
| 任务四：文件属性查询 | `agentfs_ucore`、`agentscan_ucore`、`agentbench_ucore`、`agentconflict_ucore` |
| 任务五：Agent Loop | `agentloop_ucore`、`agentsched_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| 任务六：综合场景 | `labdemo_ucore`、`labbench_ucore`、`make dual-platform-run` |

## 性能数据说明

性能数据分两类。

第一类是 QEMU 内专项微基准，由 `agentbench_ucore` 输出：

| 指标 | 含义 |
| --- | --- |
| `scalar_min/avg/max` | 多轮单次工具调用的 tick 观测。 |
| `batch_min/avg/max` | 多轮批量工具调用的 tick 观测。 |
| `scan_records` | 文件查询扫描路径触达的记录数。 |
| `index_records` | 文件查询索引路径检查的候选记录数。 |
| `file_digest_cache hits/misses` | 内容摘要缓存命中和未命中次数。 |
| `prefetch_records` | metadata 预取提示可见记录数。 |
| `event_wait_wake` | 事件等待/唤醒路径的 tick 观测。 |

第二类是双目标对照实验，由 `make dual-platform-run` 后的 `results/latest/` 生成：

| 文件 | 内容 |
| --- | --- |
| `summary.csv` | 双目标总体状态、状态文件数量和关键对照项。 |
| `runner-sweep.csv` | plain 与 AgentOS 在多个场景下的 tick 对照。 |
| `experiments/raw/*.csv` | 文件查询、Context/timeline、事件等待、并发写入、LLM Relay、失败恢复等实验原始数据。 |
| `experiments/experiment-stats.csv` | 每组实验的 min、avg、max、P50、P95。 |
| `charts/*.svg` | 从 CSV 生成的图表。 |

QEMU tick 会受到宿主机调度、终端输出和文件系统缓存影响，因此报告使用同一环境下的相对差异和结构化计数。文件查询看扫描数与候选数，Context/timeline 看重建步骤与 snapshot/query 成本，事件实验看轮询次数与 wait/wake 次数，并发实验看覆盖风险与租约拒绝结果。

## 基础兼容抽测

AgentOS-uCore 保留代表性 uCore 基础 syscall 抽测。CHAPTER=3 下的 `ch3_trace` 应输出：

```text
Test trace OK!
```

普通进程消息接口由 `agentsecurity_ucore` 中的 `mail_basic=1` 覆盖。该抽测说明 AgentOS 扩展没有破坏代表性基础 syscall 路径。

## 结果产物

专项测试通过后，QEMU 日志保留在对应脚本输出目录。双目标运行通过后，结果主要位于：

```text
/tmp/agentos-dual-platform/
results/latest/
```

`/tmp/agentos-dual-platform/` 保存 QEMU 日志、镜像提取状态文件和页面渲染结果。`results/latest/` 保存汇总 CSV、SVG 图表、Markdown 摘要和 HTML 导览页。`results/latest/` 是本机运行产物，默认不提交。

## 失败定位

| 现象 | 优先查看 |
| --- | --- |
| QEMU 长时间无输出 | `/tmp/agentos-dual-platform/seeded-action-state/*/ucore-run.log` |
| 构建失败 | `make agentos-user` 或 `make agentos-build` 的编译输出 |
| 某个专项测试失败 | [testing-details.md](testing-details.md) 中对应测试流程 |
| 双目标状态不一致 | [../verification.md](../verification.md) 的双目标验证章节 |
| 页面或图表缺失 | `host_tools/test_*.py` 和 `results/latest/` |

## 当前范围说明

| 方向 | 当前范围 |
| --- | --- |
| 文件扫描深度 | 自动扫描 uCore 根目录短文件名，文件对象 metadata 支持用户态显式写入和根目录自动发现。 |
| Agent 调度 | 验证角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel 和虚拟运行量。 |
| LLM Gateway | 内核提供结构化请求、响应事件、Context 和审计记录；云端访问由用户态或宿主机 Relay 完成。 |
| 页面和图表 | 内核输出 `agentos:event`、timeline、audit 和 provenance，宿主机工具负责转成页面和图表。 |
| 性能数据 | 当前采用同一 QEMU 环境下的 tick、扫描数、候选数、轮询数、拒绝数和重建步骤等相对指标。 |

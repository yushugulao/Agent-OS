# AgentOS 内核验证

本文给出当前架构的验证顺序和断言边界。功能与性能结果来自实际 Host/QEMU 测试。

## 1. 验证层次

| 层次 | 回答的问题 | 不能证明 |
| --- | --- | --- |
| focused checker | 关键调用顺序、生产对象边界、UAPI 布局是否漂移 | 真实 Guest 调度/设备行为 |
| Host 模型、mutation 与协议单测 | U/P/F、ring、resync、fence、relay frame、MCP/A2A 对象映射是否拒绝变异 | RISC-V 二进制或远程互操作已运行 |
| cross build/link | 当前生产对象是否能编译链接，调用图栈是否安全 | syscall 运行语义 |
| QEMU Guest 专项 | fork/exec/IPC/VFS/event/fence 等动态行为 | 超出该场景的普遍结论 |
| paired/performance run | 双目标完整负载、工作量和实际测量 | 单个机制的因果贡献 |

## 2. 快速静态验证

```bash
make agent-uapi-check TOOLPREFIX=riscv-none-elf-
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
python -B host_tools/test_guest_llm_relay.py
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
- V2 exploratory typed call、最多 64 项的顺序 compact batch、ENFORCE V3 contract-bound call，以及 Context push/query/rollback；
- 显式 metadata、index/scan 一致性、inode incarnation；
- typed live watch、event wait、route、LLM request/response correlation、timeout/cancel；
- resource hard quota、reservation rollback 和 teardown；
- workflow fence 的 receipt、retry 和拒绝路径。

任务二的三条调用线必须分别解释：V2 只接受 `uint64`/string typed 参数并压平到 `arg0/arg1/64-byte payload`；`agent_run()` 在一次 syscall 内顺序同步执行 compact `agent_op`，不是 typed-KV/non-blocking batch；只有启用 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 的 V3 才验证 frozen node/schema/predecessor/attempt/deadline/provenance envelope。Guest 报告的 `1/16/2 syscalls` 是入口调用点计数，不是内核路径 counter。

模型 loop 验证分三层，不能互相替代：

- kernel RPC：同 requester correlation 严格递增且只有成功投递才推进；pending 使用 120 秒 tick TTL；错误 lifecycle/relay/未请求 response 为 `DENIED`；容量为 `NPROC` 的有界 terminal history 在记录保留期把已消费 replay 标为 `STALE`、过期 response 标为 `TIMEOUT`，覆盖后旧 response 按 unmatched 返回 `DENIED`；成功 response 只消费一次；registry limit 与 event queue full 可区分；teardown 清理状态；
- Guest loop：Guest 自己保存 history/catalog/round、只接受 allowlist 中单个 `tool_use` 或 `final`、执行 typed V2，并把实际 Context/tool result 回灌；
- Host relay：API key/TLS 只在 Host，relay 不读 Guest 业务文件、不选或执行工具、不伪造 result；live 与 offline replay 都必须经过相同 QEMU 串口 frame/session/sequence/hash/round 边界。

offline replay 可验证协议和 loop 可复现性，但不能报告为 live 云模型实测。当前不把并行多工具、token streaming、自动重试/重定向、任意长上下文或 remote exactly-once 列入通过条件。

完整入口已经落到 Guest、Host 与 Make：

```bash
make agent-live-demo-check
make agent-live-demo
```

第一条只运行 `scripts/test-agent-live-loop.py` 与 Host relay 单测。第二条构建 `user/src/agentlive_ucore.c`，默认显式选择 replay provider，使用 `ci/agent-live-replay.jsonl` 让 6 轮响应经过 QEMU 串口。所有 provider 都必须采到 `discovery=1 rich_overlay=3`、`passed` 与唯一顶层 `parent passed`；默认 replay 还精确要求 `query_file=1 echo=1 send_message=1 approved=1`、`reject_unknown=1 reject_bad_args=1 reject_replay=0`、`transcript_turns=5 retained=5 dropped=0` 和 relay 的 `unknown=1 bad_args=1 replay=0 send_sink=1`。Guest 另输出 Context roundtrip/wait/heartbeat/rounds；其内部断言通过后才打印 `passed`。Windows xPack 环境可追加 `TOOLPREFIX=riscv-none-elf-`。

Host relay 的参数合同由 `python -B host_tools/guest_llm_relay.py --help` 给出：`--provider openai|anthropic|deepseek|replay` 必须显式选择，`--goal` 与 `--goal-file` 二选一，`--approve-tool NAME` 可重复。replay 还要求 `--replay-file`；真实 provider 才读取 Host key。`--api-key-file` 只传路径，relay 在内部有界读取单行 UTF-8 key，且与 `--api-key-env` 互斥；两者都未给出时使用 provider 默认环境变量。

当前工作区的 DeepSeek live 验证入口是：

```bash
AGENT_LIVE_PROVIDER=deepseek make agent-live-demo
```

该入口默认选择官方 `https://api.deepseek.com/chat/completions` 与 `deepseek-v4-flash`。默认目标要求模型按 `query_file("agentlive.note") -> echo(size, inode) -> final` 形成真实数据依赖；Host 还要求 `query_file=1 echo=1 send_message=0`、零拒绝、`transcript_turns=2 retained=2 dropped=0` 和 relay 零错误 marker，避免模型跳过任务也被误判为成功。显式覆盖 `AGENT_LIVE_GOAL` 时不套用这组场景专属 marker，只保留通用完成门。

Make 依次探测仓库外的 `../deepseek_api.txt` 与 `../计算机操作系统能力竞赛/deepseek_api.txt`，兼容竞赛目录内的普通 checkout 和同盘 release checkout；都找不到才让 relay 读取 `DEEPSEEK_API_KEY`。其他位置可显式设置 `AGENT_LIVE_API_KEY_FILE=/path/to/key.txt`。若改用环境变量，应令 `AGENT_LIVE_API_KEY_FILE=`，并可用 `AGENT_LIVE_API_KEY_ENV=ENV_NAME` 覆写默认名称。DeepSeek 请求关闭 thinking：当前 Guest whole-turn history 保存的是结构化 `tool_use`/`tool_result`，没有保存并回送供应商 `reasoning_content`，因此不能冒充符合 thinking-mode 的多轮协议。默认 replay 完全不访问网络；只有上述 live 命令实际完成并出现最终 Guest markers 后，才可报告为 DeepSeek 实测。

### 5.1 长驻交互控制台

交互控制台把一次性 `HELLO -> model/tool rounds -> GOODBYE` 扩展为同一 Guest/QEMU 内的多个用户 turn。验证分为三层：

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv-none-elf-
make agentos-console-deepseek TOOLPREFIX=riscv-none-elf-
```

- `agentos-console-check` 运行 Host frame/local socket/controller/observer 单测和 Guest 交互 loop 静态合同，不启动 QEMU，也不证明 provider 可用。
- `agentos-console-replay` 在一次 QEMU boot 内并发 attach observer，脚本化提交三个用户 turn 和七个模型请求，并要求每轮请求与 fixture 中的规范化 SHA-256 精确匹配。结构化 validator 检查成功的 `query_file`/`echo`/单次 `send_message`、deny 未执行副作用，以及 observer 收到 `waiting_llm`、fresh `context_timeline/kernel_timeline`、三个 `turn_complete` 和 `session_closed`。它是 offline 可复现验收，不是云模型结果。
- `agentos-console-deepseek` 是显式人工入口，不进入默认 CI。只有实际 API 往返、模型自主选择工具和 Guest 最终回答均发生后，才能报告为 DeepSeek live 演示。

长驻路径还必须检查以下不变量：

- Guest 拥有 transcript、tool catalog、下一步选择校验、V2 typed 执行和 result 回灌；Host CLI 只呈现事件并收集用户/控制/审批输入，daemon 只负责串口、TLS/provider 翻译和 owner-only 本地路由。
- `session_id`、递增 `turn_id`、`request_id` 和 `corr_id` 能将 CLI、串口、Context 与 observer 事件对应；final 只完成当前 turn，不退出 Guest。
- `send_message` 的批准或拒绝绑定当前 session/turn/request/correlation、工具名、规范化参数 digest、新鲜 nonce 与有效期。Host 人工审批等待上限为 25 秒，超时或 controller 断开即 deny。当前这是 Guest 用户态 gate，不是内核 capability 或 V3 approval token。
- `Ctrl-C` 取消当前 turn 而不杀死 session；`/quit` 才正常关闭。`/tools`、`/context`、`/status` 和 `/reset` 的响应来自 Guest control path，不能由 Host 用缓存业务数据冒充；其中 `/context` 报告 count、最旧/最新 sequence、dropped、provenance 及最新记录的 tool/status/result 摘要。
- observer 读取的是同一 Guest 进程线程调用 timeline、Context 和 agent-info 得到的 high-signal live snapshots，不是全量实时 trace。它不是独立安全边界，并会增加读取、调度和串口开销，因此不用于性能数字。

交互路径使用 V2 exploratory RPC 支持 result-dependent 选择；ENFORCE V3 仍是 frozen contract 的高保证执行模式。MCP/A2A 仍是 in-memory object prototype，控制台通过串口 model wire 运行，不能把此验收写成 MCP 互操作。完整操作见[交互控制台说明](interactive-console.md)。

## 6. 双目标与性能

plain uCore 与 AgentOS-uCore 运行同一 deterministic 用户态科研工作流合同。`labdemo_ucore` 使用固定 policy，不依赖 LLM；这样 paired run 才能比较等量工作。端到端 paired run 用于整体比较；Credit Domain、Evidence Ring 或 Live Query 的单项性能结论还需要同内核消融、工作量计数或专项 benchmark，不能从双目标总差异直接归因。可选 `agentlive` 只验证模型 loop，不替换这一性能基线。

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
| V2 与 V3 保证不同 | V2 可探索下一工具但不绑定 DAG；ENFORCE V3 拒绝未冻结 node/edge/schema/provenance |
| Task Channel 无业务 backend | provider 同步，只接受 null input/output `NONE`；`RESOURCE_IMPORT` fail closed，模型 wire 不走 SQ/CQ |
| Kernel RPC 不是模型循环 | 只验证 lifecycle/requester/relay/correlation 和一次消费；Guest 拥有 history/decision/execution |
| Host relay 权限最小化 | key/TLS/provider JSON 只在 Host；业务文件、工具选择/执行和 result 只在 Guest |
| replay 与 live 同 wire | 两者都经过 session/sequence/hash/round 边界；replay 结果明确标为 offline |
| MCP/A2A 仅对象 prototype | 单测只覆盖 deterministic in-memory 对象/Task 状态；不声称 server、streaming、互操作或内核 adapter |

## 8. 结果解释

本地 checker 通过只说明对应静态合同成立，构建通过只说明当前目标可编译链接。MCP/A2A in-memory 单测不证明网络互操作，relay 单测不证明 live provider 已调用，offline replay 也不等于云模型。功能结论要由实际 Guest 场景确认，性能结论要由实际负载和测量确认。内核 `Evidence Ring` 及 fence receipt 验证的是产品运行期安全语义，不是仓库发布工件。

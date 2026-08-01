# AgentOS 竞赛评价方法

本文定义实验分支的统一评价方法。目标不是挑选一个必然让 AgentOS 获胜的对照组，
而是在相同输入、预注册关键 outcome 相同和可复现环境下，分别回答三个问题：

1. AgentOS 的内核机制是否给 Agent 工作负载带来可归因的收益；
2. 这些收益能否在同结果的完整科研工作流中保留下来；
3. 引入机制后，内核 ELF、text、data 和 BSS 成本是多少。

评价结论只由本次运行的原始 Guest 日志、严格验证后的样本和配对统计产生。
页面、固定文本、历史截图和公式生成的数据均不能成为性能证据。
`agenteval_measurement_source_contract.py` 还以 token/control-flow 合同锁定四个 headline
的八条路径：`now_us()` 起点必须先于真实 file query、逐路径文件检查、tool call 或
Context 访问循环，`elapsed_us(start, now_us())` 必须位于循环之后；
path-traversal/index、metadata-scan/index、scalar/batch 和 syscall/direct 两个 variant
都在计时区内。常量、负载公式、移出真实调用或删去任一
variant 的 mutation 回归都会失败。每对 variant 使用彼此独立、预分配的结果缓冲和
measurement；两条计时路径全部结束后才允许遍历结果、校验语义、计算 hash 或打印
marker，避免第一条路径的后处理污染第二条计时窗口。源码 mutation 合同同时锁定这一
顺序、AB/BA 的真实调用顺序，以及 `print_sample()` 对 `duration_us` 的直接序列化；
计时后复合改写、常量替换和伪造 marker 顺序都会失败。
任务一至五的功能证据另由 `functional_acceptance_source_contract.py` 约束。该合同以
忽略空白和注释的 C token 为单位，版本化封闭从 launcher、五个任务实现到
semantic/hash/打印出口的已审查函数图；同时独立核对各任务关键 `agent_info`、
`tool_list/tool_call`、`agent_run/context_*`、文件创建与 metadata/query/digest、
事件等待/唤醒/心跳调用次数，以及动态输出到 `values[]`、semantic 和最终 receipt 的
唯一 def-use 边。删除真实调用、用常量替代内核结果、断开中间结果或伪造 receipt
出口，即使重新计算 token 指纹也会被结构 mutation gate 拒绝。合法的功能实现变更必须
显式升级合同版本并更新受审查函数指纹，不能在一次 formal campaign 中静默漂移。
`scenario_timing_source_contract.py` 以同样方式约束任务六的单调时钟、handoff、completion
和最终 workflow record。正式运行在首个 QEMU 前生成版本化
`measurement-source-receipt.json`，逐 boot 前后重新散列来源；run plan、package 内策略清单
覆盖的 Guest 测量源码和评价控制面快照必须与 source C 的 Git blob 一致。因而源码合同
不再只是 smoke 测试，替换 suite 或弱化统计门也不能通过重新计算包内 hash 来隐藏。
receipt v5 会分别记录 micro、Task 1-5 functional、functional compile
closure、scenario 与 policy 合同版本，避免把
功能数据流合同的升级隐藏在笼统的源码摘要中。
该 compile closure 还封闭 Guest 的完整 include-search-root 清单、Agent/UAPI 头文件、
syscall wrapper、RISC-V `ecall`、C 运行库输出端、Make 选择链、文件系统镜像生成链，
以及所有会返回 syscall 结果或向 console 写回执的内核源码。合同拒绝影子/预编译头、备用
GNUmakefile、头文件宏重定向、wrapper 常量返回和 `ecall` 替换；runner 与递归构建均用
`make -rR -f Makefile` 禁用内建重建规则并显式选择受审查文件，完整字节指纹则把其余
受管内核实现锁定到本次审查版本。`agent_eval` 只选择 `agenteval_ucore`，构建与 QEMU
辅助 Python 均使用 `-I -S`，避免同目录模块遮蔽标准库。预处理前还拒绝非 ASCII、控制字节、续行和备用 directive
token，并用 comment-obscured include、空格/NUL 续行等回归覆盖已知的 lexer/compiler 差异。
这一门禁的保证边界是当前仓库受管源码的预处理、调用和 def-use 闭包，以及已注册的典型
mutation；它不是针对任意恶意 C 混淆的形式化证明。编译器内建头文件、cc1、specs 等属于
外部工具链 TCB，正式运行记录现有工具身份和版本，但这里不声称完成供应链证明。
源码合同也不单独证明内核实现正确或 QEMU 确实执行成功；后两者仍分别依赖 Host 对动态
字段的独立复算、原始 Guest 日志和 campaign 的成功状态。没有这些运行证据时只能声明合同
就绪。

## 1. 与赛题的对应关系

| 赛题任务 | 动态评价 | 主要指标 |
| --- | --- | --- |
| 任务一：Agent 进程与 Context | 普通 launcher 先用题面入口 `agent_create()` 创建并核验 Sentinel，再创建正式 Orchestrator；检查 PCB 角色和可写 Context 映射；`context_access` 同语义消融 | challenge 绑定 PID、角色、兼容入口退出状态与 Context 布局回执；预注册操作数的总耗时、工作量和结果回执 |
| 任务二：结构化工具接口 | V2 `tool_list` 动态目录、三项 required core subset、三种真实 typed-KV 调用及纯未知名称、ID/name 错配、重复参数和 wrong-type 错误；`tool_batch` 消融 | 可扩展目录逐项 marker、core schema/响应/精确错误与诊断 hash 回执；scalar/batch 总耗时和操作数 |
| 任务三：Context Path | 6 次连续生产 `agent_run` 自动记录、syscall/直接映射一致性、rollback 后真实分支调用、clear 和超配额 FIFO | Host 重建 challenge 绑定工具记录/path parent 语义、分支 generation、淘汰计数；不从功能回执外推性能 |
| 任务四：文件系统查询 | 独立功能探针动态执行属性 set/query/delete、六条件 AND、摘要关键词模糊查询和真实内容 digest；`file_query_path_index` 用同一批真实文件比较逐路径 open/read/fstat/close 与热索引，`file_query_table_ablation` 另保留 metadata 固定表扫描消融 | challenge 绑定属性生命周期、结构化 inode 结果、内容摘要/digest 回执；分别列真实 fixture 数、逐一检查文件数、实际计费槽/索引链节点、每操作耗时和索引重建成本 |
| 任务五：Agent Loop | 两个 Agent 的延迟事件唤醒、真实 sleep/wakeup 计数、动态心跳、停止后 timeout | challenge 绑定端点/事件/等待计数；延迟窗口前后记录 wall tick、实际调度、vruntime 与 predicate loop，动态证明阻塞等待没有忙轮询 |
| 任务六：综合场景 | 同一 challenged 科研工作流的 Plain 路径和 AgentOS 路径 | Guest cold-start workflow 总时间、steady-stage/逐程序分解、关键 outcome，以及实际消费本轮 workflow 输出的 Context/tool/metadata/观测动态 receipt |

题面明确要求文件属性查询优于逐文件遍历并提供对比数据，因此
`file_query_path_index` 是发布包中 Task 4 的必备主实验。`file_query_table_ablation`
只解释索引相对固定容量 metadata 表扫描的机制效果，不能代替题面逐文件对照。
其他实验用于解释收益来自何种机制，并展示收益能否进入
完整场景，而不是用页面数量或功能数量替代性能。

每个受支持的 `agenteval_ucore` boot 都必须在全部计时区间结束后输出任务一至五
各一条严格 receipt。Host 逐字段重算 FNV 绑定，检查 challenge、普通/Agent PID
关系、版本化 V2 工具目录和 required core subset、精确错误码/诊断、连续工具记录的
Context 语义和 rollback 后分支调用/FIFO 状态、文件属性生命周期、
多条件结果顺序与唯一性、摘要关键词命中、内容 digest 与真实 inode 关系，以及事件
端点、sleep/wakeup、心跳停止语义，以及延迟等待跨越的内核 tick、实际调度次数、
vruntime 和 predicate loop 增量。该证据证明等待期间未反复调度，不把调度记账
夸大为硬件 CPU 周期或独立吞吐量结论。Task4 只在全部计时 marker 输出后回收三条性能
fixture 的资源槽；新功能 fixture 使用与性能 fid 区间不重叠的 challenge 派生 fid、名称、
run selector、summary、内容和 digest request，不复用旧对象身份。Host 逐字段重建四次
结构化查询语义并验证删除 generation 单调前进。功能回执不
读取 `duration_us` 或性能样本数值，Task 4 性能结论仍只来自
`file_query_path_index` 的独立 AB/BA marker。
缺失、重复、乱序、固定
伪值、失败状态或重算不一致都会使合同失败。只有预注册 boot 全部为 `supported`、
数量达到下限且每个 boot 的五类 receipt 均通过时，任务一至五功能状态才为 `pass`；
功能回执不会改变相应性能 benchmark 的统计门，也不会替代任务六场景证据。

`agenteval_ucore` 是专用于动态验收的 `BOOT_SEALED` 可信映像。它的 manifest
角色掩码严格限定为 `ORCHESTRATOR | SENTINEL`：普通 launcher 先通过题面字面入口
`agent_create()` 动态核验默认 Sentinel，随后顶层评价进程以 Orchestrator 运行；任务五
再用同一兼容入口创建 Sentinel，以验证真实的双 Agent 等待和唤醒。这里不使用
`ROLE_ALL`，也不授权 Investigator、Recovery 或 Artifact 角色；创建过程仍经过通用的
映像角色掩码和角色委派检查，不绕过角色与可执行映像的可信绑定。

## 2. 对照层次

评价不使用单一基线承担所有结论。

### 2.1 同内核机制消融

主要因果结论来自同一个 AgentOS 内核、同一个 Guest 程序和同一份数据：

- Task 4 主对照：逐一打开并检查 N 个预注册真实文件路径与已就绪索引；
- 解释性消融：固定容量 metadata 全表扫描与已就绪索引；
- scalar tool call 与 batch tool call；
- `context_query` 与 Context 映射直接读取。

busy poll/事件等待以及生命周期恢复仍是候选实验，不在当前预注册 suite 中。没有对应
原始样本时，它们只能显示为 `unavailable`，不能借用其他 benchmark 的结果。

每对样本必须具有相同的 workload fingerprint、operation count 和 result fingerprint。
Task 4 的两个实现消费同一份 challenge 绑定 fixture manifest 和同一有序目标序列；
文件体中的规范属性记录与 Agent metadata 由同一记录生成。用户 ABI 当前没有目录项
枚举接口，因此传统路径被准确限定为“对预注册 corpus 中全部 N 条路径逐一
open/read/fstat/close 并检查”，命中后仍检查剩余文件；页面不得把它扩大表述为任意目录
树遍历。只改变被评价的访问机制，避免把编译器、内核映像、fixture 或查询语义差异
归因给 AgentOS。

### 2.2 同场景 backend 对照

科研场景使用同一输入、阶段图和独立结果 oracle：

- Plain 目标运行 `rp_seed_orch` 的普通 uCore 工作流路径；
- AgentOS 目标运行 `rp_agentos_orch`，使用 Context、metadata、事件和审计机制；
- Host 按 challenge 独立导出预期工件，并要求两侧预注册的 rerun、stage、artifact、
  workflow identity 和 LLM response 等关键 outcome fingerprint 相同。

AgentOS Orchestrator 保留可信身份，由子 Orchestrator 完成 `rp_orch` 后读取本轮
`rp_input`、stage DAG/state、artifact 与 runner 输出，再用这些 challenge 派生值执行
Context、结构化工具、metadata 查询和 provenance 操作。v3 receipt 的字段由 Host challenge
oracle 在原始采集和序列化 report 复验两条路径上共同重算；固定回执跨 boot 重放会失败。

这里不宣称两侧“完整最终状态相同”。当前 oracle 只规范化并比较上述预注册业务
outcome；未登记的缓存、诊断文件和内核私有状态不属于等价结论。只有今后建立覆盖
完整业务状态的 canonical oracle，才可扩大该表述。

这一层衡量完整 AgentOS 栈在任务六场景中的 Guest workflow 执行效果，但不同内核、安全机制和
应用入口都属于处理，因此不能单独归因给某一个内核机制。具体机制的因果结论只来自
2.1 的同内核消融；两层结论在 Dashboard 中分开展示，不拼成一个总分。

### 2.3 系统成本与护栏

本评价分支提供可信双目标构建器，并在实际运行后报告 ELF 文件、text、data 和 BSS
四项双目标成本；同一可信 transcript 还运行 canonical kernel budget 与 user stack
checker，把 AgentOS 的 `struct proc` actual/limit 和最坏用户调用路径栈 actual/limit
绑定到 kernel-cost report、离线 summary 与 Dashboard。baseline_ucore 没有同口径的
canonical checker contract，因此这两项明确作为 AgentOS guardrail 展示，不构造
baseline delta。formal bundle 缺少任一成本、guardrail 或原始命令回执都会失败。
启动空闲页以及传统 fork/exec、pipe、顺序文件 I/O 仍由各自评价合同管理；所有成本
护栏继续与性能 claim 分离。

### 2.4 传统兼容路径开销

评价使用同一份 `evaluation_guest/compatbench.c`，分别由 Plain uCore 与 AgentOS-uCore
原样编译，测量 `fork/wait`、`fork/exec/wait`、pipe 往返和顺序文件 I/O。formal
协议固定为 7 个配对 boot；challenge 与 AB/BA 顺序由源码提交确定性派生，不允许按
中间结果提前停止。每个 boot 都重新 clean、build 并启动 QEMU，Host 还会重放 Guest
回执并要求两侧操作数和结果 checksum 完全一致。

这一组数据只回答“保留传统 uCore 编程接口需要多少兼容开销”，不评价 AgentOS 专属
功能，也不与机制微基准或科研场景拼成综合分。原始日志、源码、ELF、内核和文件系统
镜像必须随 formal bundle 一起离线复验；缺失任一目标或结果不等价都会使打包失败。

### 2.5 Task 6 资源稳定性

AgentOS 科研场景在 makespan 计时结束后运行独立资源稳定性阶段。只有 bootstrap 绑定、
非 Agent 的资源域管理员可以读取全局快照；普通 Agent 不能使用该观测入口。Host 为每个
替换 workflow 的 Guest 父进程从该 boot 的 challenge request id、workflow 序号和模式派生
非零 nonce，并显式传给探针。每个负载探针都把 nonce 用于子进程内存、临时文件和 metadata
负载；所有探针都把它写入报告及完整字段 guard。Host 独立重算 nonce 和 guard。因此，移植旧报告、跨 boot 重放报告
或只伪造一条“通过”状态都不能满足合同。

每个负载 workflow 都实际创建并回收子进程、物理页和文件对象，并执行真实 metadata
set/query/delete。探针同时读取 self-only lifecycle ABI，报告不可变 lifecycle
`(id,generation)`、VFS `scope_id`、I/O owner 和 EXEC resource-account
`(slot,generation)`；Host 要求这四种身份在替换序列中分别唯一，防止把同一个安全主体的
重复采样误写成多个 workflow。resource-account handle 只用于当前进程身份观测，不是可
转移的授权或账户级用量查询接口。

全局快照分别记录 ordinary/reserved 两类的 used/pending，以及 ordinary、reserved 和
stack-reserved 空闲物理页。Guest 对每个前后快照核对 `used == ordinary_used +
reserved_used`、`pending == ordinary_pending + reserved_pending`；原始分类值随证据序列化，
Host 再独立重算总量和增量，不能靠 ordinary/reserved 之间搬移计数维持相同总数。当前
认证平台是单核 RISC-V；快照在一个外层 `intr_save` 临界区内读取策略计数和空闲页，因而
表示单核、禁止本地中断下的一致切片。这不构成多核原子快照声明。

验收不仅限制每个 workflow 的局部前后差，还以第一个 workflow 的 before 与最后一个
terminal after 比较整个替换序列，并要求有界种类出现平台期或回收，而不是每轮在局部
上限内线性累积。平台期只能由负载 workflow 之间的不增长证明；若四轮负载持续增长，则
terminal after 必须严格低于最后一轮 load after，单纯相等不算回收。终端上限对整个序列
只应用一次；pending 分类必须在每个终止点归零，
空闲页则回到序列起点。`measured_mask` 仅声明当前策略配置且在全局快照中有真实计数器的
resource kind；它不声明逐 account 覆盖、rate/lease/debt 覆盖，也不证明未配置资源或任意
运行时长的全局无泄漏。该证据只支持同一次启动、预登记负载和终止探针下，对这些已配置
全局种类计数器的有界回收；它也不覆盖真实掉电后的介质状态。把稳定性阶段放在性能窗口
之外，可以避免为了证明回收而污染 Task 6 的 workflow 时间。

## 3. 实验纪律

### 3.1 独立样本

一次全新文件系统镜像上的 QEMU 启动是一个独立 boot。Guest 内部的多次操作只用于
得到该 boot 的稳健中位数，不能把内部循环冒充多个独立样本。发布结论至少需要
7 个有效配对 boot；formal 微基准、传统兼容路径和完整科研场景都恰好执行预注册的
7 个 boot，不允许依据中间结果改变样本数。

微基准 challenge 会编入该 boot 的 Guest 程序，因此各 boot 的原始输入镜像摘要按
设计应当不同。合同以相同 clean commit、suite 摘要、内核摘要以及“只归一化 challenge
和日志路径后完全相同”的构建命令约束同质性，同时逐一归档 challenge 专用输入镜像；
不能错误地要求这些镜像字节相同，也不能允许其他构建参数悄然变化。

### 3.2 顺序与缓存

- 每个 boot 内采用 AB/BA 或 ABBA 平衡顺序；跨目标启动顺序也必须平衡；
- `fresh boot`、`warm Guest paths`、`index absent`、`index ready` 和
  `result cache hit` 分开记录；
- 每个 N-file corpus 先在计时外完整预热传统路径，并单独记录索引准备；热索引计时前的
  重建属于 warmup，计时样本必须确认没有重建和结果缓存命中；
- 当前不控制宿主页缓存，只声明 fresh Guest 与索引状态，不把结果写成物理冷盘性能；
- 计时使用 Guest 单调微秒时钟，不把真实 0 修改为 1，不事后减去估算开销。

Task 4 主对照预注册 `N=8/24/48/96`，每个 inner pair 分别执行 `8/6/4/4`
次逻辑查询；操作数随 N 下降只用于约束正式 QEMU 总成本，并在首个 boot 前由 suite
冻结，不能依据中间结果调整。固定表消融继续使用 `N=24/64/96`、每 pair 16 次查询。
两者都保留 7 个 inner pair 和 7 个独立 boot，因此减少的是单次 boot 内重复 I/O，
不是统计独立样本数。

Guest 性能 `sample`/`diagnostic` marker schema v2 已冻结，字段必须按下列顺序出现；缺字段、增字段、重排、
重复键或空值都会被 Host 拒绝：

```text
sample: schema experiment load pair variant order cache operations dataset_size work_units records_examined result_items duration_us index_rebuild_records result_cache_hits workload_fingerprint result_fingerprint status
diagnostic: schema experiment load cache operations dataset_size work_units result_items duration_us index_rebuild_records result_cache_hits workload_fingerprint result_fingerprint status
```

`sample` 是实际计时记录。`load`/`dataset_size` 表示本轮注入的真实 fixture 数，不冒充
整个固定容量 catalog 的槽数。`file_query_path_index` 的传统路径必须精确报告
`work_units == records_examined == N * operations`；每个 work unit 都对应一次实际文件
open/read/fstat/close 和属性谓词检查。`file_query_table_ablation` 的强制扫描则必须精确为
`512 * operations`，它只说明固定 metadata 表内部的机制消融。索引侧的 `work_units`
来自内核实际计费槽/链节点，`records_examined` 是进入谓词匹配的候选记录；合同只要求
计数真实、有界且查询计划确为索引，不把“索引必须更快”或“工作量必须更少”写成证据
有效性条件。这样真实负结果会成为 `not_supported`，不会被伪装成坏样本而丢弃。
Host 还核对操作数、结果数、challenge 派生目标序列和两个语义指纹，并要求所有计时记录
的 `index_rebuild_records == 0` 且 `result_cache_hits == 0`。因此 `ready-index` headline
的就绪索引/无结果缓存护栏来自实际计时样本本身，不能用计时区外的准备诊断替代。

`diagnostic` 在对应的文件查询计时样本之前独立记录索引准备过程，只支持 readiness
披露。它同样绑定 `dataset_size`、`result_items`、`result_cache_hits`、workload/result
fingerprint，以及原始日志路径、行号、日志 SHA256 和 marker SHA256。每个已测量
`file_query_path_index` 和 `file_query_table_ablation` 负载在每个有效 boot 中都必须各有一条
诊断；summary 与 Dashboard 要覆盖全部负载，空 `diagnostics` 不能通过。诊断可披露
`cold-rebuild` 或 `ready`，但不参与
headline 的统计门，也不改变任务一至五的功能 receipt 语义。

suite 的 `execution_schedule` 另预注册同一 boot 内的物理 marker 顺序。文件 fixture 按
`8/24/48/64/96` 递增构造，因此主对照与消融在共享负载处交错执行；工具批量与 Context
实验随后执行。Host 直接按该清单重放顺序，清单必须覆盖每个实验/负载恰好一次并与
Guest dispatcher 一致，避免测试 fixture 按另一种顺序生成后掩盖真实验收失败。

### 3.3 配对与统计

Host 先计算每个 inner pair 的改善率，再在每个独立 boot 内取这些配对改善率的中位数：

- latency 越低越好：`(baseline - treatment) / baseline`；
- throughput 越高越好：`(treatment - baseline) / baseline`。

报告 boot 中位数、P95、配对改善率中位数及 percentile bootstrap 95% 描述区间；该
bootstrap 区间不作为 headline 推断门。每个 boot 只有在绝对改善严格大于 5 us 且相对
改善严格大于 5% 时才记为 joint-MCID win，等于阈值、缺失相对值或任一阈值未超过都
保守记为 non-win。headline 使用完整 boot 数上的精确单侧二项尾检验。
四个微基准 headline claim 在 suite 中预注册为同一假设族，使用 Bonferroni 控制
family-wise error rate `0.05`，所以每个 claim 的单侧阈值是 `0.05 / 4`。其中
`file_query_path_index` 是 Task 4 竞赛主 claim，`file_query_table_ablation` 是解释性
机制 claim；suite 的 `competition_claims` 显式绑定二者的不同职责。每个
claim 又是其全部预注册 load 的交集，任一 load 未过门就不能发布该机制优势。
任务六场景是独立的预注册主指标，不与四个机制 claim 重复计数。
每个实验还预注册最小基线计时窗口 20 us。只有样本数达标、结果等价、每个 load 的
joint-MCID 精确检验通过且全部 load 取交集时，claim 才是 `supported`。七个 boot 时
7/7 win 的单侧 p 值为 `1/128`，6/7 为 `1/16`，后者不能通过 `0.05/4`。
否则显示 `not_supported`、`unavailable` 或 `failed`，绝不补零、删除
失败样本或生成综合加权分数。

任务六的主指标是 Guest cold-start workflow makespan，而不是从首个子程序开始的
局部窗口。Plain 在 `rp_seed_orch` 的 `main` 真入口取得单调时钟；AgentOS 在
`rp_agentos_orch` 的 `main` 真入口、`agent_create_role` 之前取得同类时钟，因此
AgentOS 样本包含 Agent 创建、metadata init/seed、子 Orchestrator 中的完整 `rp_orch`，
以及完成后由 challenged workflow 输出驱动的结构化 tool、Context、timeline、
provenance、file query 和功能 receipt。起点与初始化完成时刻通过
factory 显式委派的一次性 pipe 跨越 workflow scope 和 exec；两端均为构建清单密封
映像，record 具有 magic/version、完整 phase mask 和完整性 guard，`rp_orch` 严格读取
一次并要求 EOF。现有 `rp_orch_timing` 继续保存逐程序明细，`steady_elapsed_ms` 保存
exec 后 orchestration 窗口，不能用它们替换 cold-start 主指标。

`rp_orch` 完成全部子程序后只把 start/ready/steady 回执交回 factory；AgentOS 外层
parent 必须依次完成 `waitpid`、completion pipe 校验、最终 inventory 与 acceptance
验证，才取得结束时间并写入严格的 `guest_workflow_timing_v3` record。Plain 侧同样在
最终本地验证后结束计时，映像准备和回收均在计时区外。Host 验证
entry/handoff/completion/phase mask、四个单调时间点、各阶段差值恒等式、AgentOS 初始化
窗口非零、steady 时间不小于逐程序区间之和，并用
QEMU 进程观测时间只做上界；QEMU/Host 启动时间不进入 Guest 指标。之后以每个独立 boot
的 `Plain workflow_elapsed_ms - AgentOS workflow_elapsed_ms` 为配对样本，报告绝对/相对
改善的 bootstrap 95% 描述区间；bootstrap 不参与 claim 判定。有符号差值固定定义为
`Plain - AgentOS`。每个 boot 只有在差值严格大于 10 ms 且相对差值严格大于 5% 时才记为
正向 joint-MCID win；严格小于 -10 ms 且相对差值严格小于 -5% 时才记为反向
joint-MCID loss。等于阈值、缺失相对值或任一阈值未越过都分别记为 non-win/non-loss。
正反两个方向组成同一个 Task6 directional family，族错误率为 `0.05`，Bonferroni 后
每方向阈值为 `0.025`；两边都使用完整 boot 数上的精确二项上尾。七个 boot 时 7/7 的
单方向 p 值为 `1/128`，6/7 为 `1/16`，所以只有前者通过。七次均成功和顺序平衡只证明
功能数据有效；还必须满足 Plain 基线窗口至少 50 ms。正向门通过时状态为 `supported`，
反向门通过时状态为 `regressed`，两边都未通过时才是 `inconclusive`。统计结构缺失、
自相矛盾或被篡改属于 `failed`，不得软化为证据不足。`regressed` 仍保留并允许打包完整
负结果，但对应任务与 `competition_ready` 必须为未就绪。scenario report 首个正式协议
直接使用 schema v2 和 `scenario-report-v2` 绑定域；本分支此前没有 schema v1 formal release。
该场景是 full-stack 对照，不能把结果归因给单一内核机制；宿主页缓存也未受控。

任务六功能通过还要求每个 AgentOS boot 的 extracted state 都包含严格的
`agentos_task6_acceptance_v3`：至少绑定 Context snapshot、结构化 tool、metadata/file query
和 timeline/provenance/ledger 四类真实内核结果。Host 将该文件摘要同时绑定到 state
inventory、challenge receipt、run summary 和样本 raw-source receipt；缺一类、字段
重排、状态自报、零观测或绑定被改写都会使整个 boot fail closed。七次启动成功本身
不再足以推出任务六功能通过。

## 4. 证据流水线

```mermaid
flowchart LR
    S["预注册实验规范"] --> G["QEMU Guest 原始日志"]
    G --> V["独立 Host 合同验证"]
    V --> R["按 boot 配对统计"]
    R --> C["claim 与适用范围"]
    C --> B["版本化 evidence bundle"]
    B --> U["离线评价 Dashboard"]
```

每个样本绑定：

- commit、工作区 clean 状态、run id、boot id 和预注册规范 hash；
- 内核、用户程序、文件系统镜像、工具链与 QEMU 身份；
- 完整命令、目标顺序、cache mode、workload/result fingerprint；
- 原始日志路径、行号、完整日志 SHA256 和 marker SHA256。

采集器把创建时的仓库相对 `artifact_root` 写入 campaign，并要求 micro 与 scenario
清单始终位于该根；日志、内核、输入/输出镜像和场景回执必须等于由它推导出的完整
规范路径，不能用相同文件名或路径后缀代替。预检记录实际执行域中关键工具的绝对
路径、版本和 SHA256；每个 boot 只执行清单内的绝对入口与受限环境，并在运行前后
复核工具身份、clean HEAD 和 commit。复核发生在 Guest 计时窗口之外，避免证据检查
本身污染单核 QEMU 的性能样本。

验证器拒绝未知字段、重复样本、缺失 variant、NaN/Infinity、跨 commit/run 拼接、
指纹不等、固定单序、少于规定 boot 数及被修改的日志。统计和 Dashboard 只能读取
验证后的 summary，不能直接相信 Guest 输出的 `passed` 文本。
定向 `run-agent-tests.sh` 不再内嵌另一份 marker/receipt 解析器，而是调用
`evaluation_contract.py validate-guest`；它与正式 `extract_log` 共用同一套语义、顺序、
索引碰撞工作量和功能回执校验，避免两份验收 ABI 再次漂移。

## 5. 评价入口

实验分支提供以下正式、自检及显式开发入口：

```bash
make evaluation-doctor
make evaluation-smoke
make evaluation-run
make evaluation-verify
make evaluation-kernel-cost
make evaluation-full-verify
make evaluation-dashboard
make evaluation-package
make evaluation-package-development EVALUATION_RUN_DIR=<run-dir> EVALUATION_BUNDLE_DIR=<output-dir>
make evaluation-package-verify EVALUATION_BUNDLE_DIR=evidence/releases/evaluation-<run-id>
```

- `evaluation-doctor`：fail closed 检查正式采集的唯一执行域与全部必要工具；
- `evaluation-smoke`：运行 Host 合同测试并检查 Guest 接线，不发布性能结论；
- `evaluation-run`：按预注册计划执行机制微基准、传统兼容路径和科研场景的独立 QEMU boot，失败样本同样保留；
- `evaluation-verify`：重新读取原始日志、重算指纹与统计，fail closed；
- `evaluation-kernel-cost`：从同一 clean commit 可信重建两个内核并生成可搬运成本 sidecar；
- `evaluation-full-verify`：在同一提交 C 的 clean detached worktree 中执行真实 `make full-verify`，封存原始日志、step summary、完整 raw 工件和工具身份；
- `evaluation-dashboard`：从已验证 summary 生成可离线打开的中文仪表板。
- `evaluation-package`：只有同一 C 的 full-verification stage 也通过原始日志与清单重放后，才把已验证 run 的规范、原始工件、统计和 Dashboard 原子封装为可提交证据包；
- `evaluation-package-development`：生成带永久非正式警告的开发接线包；
- `evaluation-package-verify`：在干净 clone 中逐文件复核 package、重放统计与页面，并验证唯一
  引入证据的 C→E 提交及 append-only `evidence/releases/INDEX.md` 记录。

正式 `evaluation-run` 只接受原生 Linux、Windows Host 中明确指定且可验证的
`EVALUATION_WSL_DISTRO`，或经过完整证明的原生 MSYS2 POSIX 域。Windows/WSL 入口先用
该发行版自己的 `wslpath` 解析仓库，再在其中
验证 Python 3.10+、Bash、Make、gcc、ld、objcopy、objdump、size、QEMU RISC-V `virt`、
timeout、readlink 与 sha256sum，最后把采集、复验、成本、Dashboard 和打包完整重执行
到同一个 WSL 域。Windows 原生 micro 加 WSL 场景的混合方式被机制性拒绝。旧版 inbox
WSL 即使没有 `wsl --version` 也可使用，但 `wsl.exe` 仍以绝对路径和 SHA256 绑定，且
指定发行版必须通过实际命令探测。自定义发行版内工具使用
`EVALUATION_WSL_TOOLPREFIX`、`EVALUATION_WSL_QEMU`、`EVALUATION_WSL_PYTHON` 和
`EVALUATION_WSL_BASH`；普通 `TOOLPREFIX/QEMU/PYTHON_BIN` 不会从 Windows 偷渡进正式域。

原生 MSYS2 是同等严格的第三种正式域，而不是 WSL 失败后的跳过选项。MSYS2 Python
上游实际报告 `os.name=posix`、`sys.platform=cygwin`；预检还必须同时观察到
`MSYSTEM=MSYS` 与严格的 `MSYS_NT-*` uname，因此真正的 `CYGWIN_NT-*`、MINGW shell、
Git Bash 携带的 Windows Python及 native `win32` Python 都会被拒绝。预检记录 uname、
Windows build、`msys-2.0.dll` 和每个关键工具的绝对路径、版本与 SHA256；受信 Host
objdump 必须证明 Bash、env、Git、Make、Python、timeout、readlink、sha256sum、uname 和
cygpath 都导入所绑定的 MSYS runtime，且不导入 `cygwin1.dll`。仓库、临时目录和工具还要
经绑定 cygpath 完成 POSIX 到 Windows 再返回的 same-file 核对。交叉编译器必须真实生成
RV64/LP64 ELF 对象，QEMU 必须提供 `virt`。

MSYS2 的五个正式阶段同样先整体重入绝对 `env -i`、绑定 Bash 的
`--noprofile --norc`。PATH 只由已散列工具目录构成；`PYTHONPATH`、`BASH_ENV`、`ENV`、
`CDPATH`、`MAKEFLAGS`、编译 flags 和 `GIT_*` 等启动污染不会被继承。内层会重新检查完整
allowlist、runtime 和工具哈希，而不是只凭 `AGENTOS_EVALUATION_EXECUTION_DOMAIN`
跳过预检。由于仓库路径可能含中文，MSYS2 固定使用 `C.UTF-8`，并同时绑定 POSIX 与
Windows 形式的临时目录供 native PE 工具使用；Linux/WSL 的 `C` locale 合同不变。
campaign 的 `run.execution_domain` 明确写为 `native-msys2`，场景执行环境写为
`native-msys2-clean-shell`。完整 platform proof（repository namespace、runtime/uname 和
工具身份）同时封入 campaign。platform proof v2 还只从受控的 `/proc/cpuinfo` 与
`/proc/meminfo` 提取稳定、可公开的 Host 硬件身份：CPU model、logical CPU count 与
`MemTotal` 字节数；动态 `cpu MHz` 不进入身份。缺失、重复或畸形的 processor/MemTotal
记录会 fail closed。硬件字段随完整 campaign 一起被 `campaign_sha256` 覆盖，并在每个
Guest boot 前重新采集核对；scenario plan schema v3 复制同一 platform proof、单独记录其
canonical SHA256，并在每轮科研场景 pair 前后执行同样核对。工具文件也会重新散列，
MSYS2 还会重新绑定当前 runtime。
公开 proof 不记录 hostname，MSYS uname 只保留 OS build、kernel release/version 与
machine，避免把个人设备名带入证据包。

正式发布仍沿用项目已有的 clean C 到 evidence E 的原子交付模型。实验阶段不得直接
增加主流水线 job，因为当前远端 attestation 合同精确绑定 1 个 Host 和 8 个 QEMU
job；评价任务应先作为独立或 child pipeline 运行，稳定后整体升级合同。

formal run id 由冻结的源码提交 C 唯一确定为 `formal-<C 的完整 40 位提交号>`。微基准和
科研场景 challenge、AB/BA 顺序由 C 确定性派生；不同 clone 对同一 C 得到相同计划。
失败目录会保留且同一输出根拒绝覆盖。没有受保护的远端 Runner 时，本地 Git 与目录锁
不能证明其他 clone 从未重跑或丢弃一次尝试，因此文档不作这种超出证据的声明。首个
QEMU 启动前生成的 run plan schema v2、scenario plan schema v3 和
`measurement-source-receipt.json` 共同绑定该确定性计划、完整 Guest 测量源码清单和版本化
评价控制面策略清单。

默认 package 位于 `evidence/releases/evaluation-<run-id>/`，与最终验收包共用唯一的
append-only `evidence/releases/INDEX.md`，并采用 `formal` profile。全局 `*.log` ignore
仅在该 release 根下被覆盖；原始工件不会以约 0.75 GiB 的散装目录提交，而是按 micro boot、
scenario boot 控制面及 scenario boot/target 分成彼此独立的确定性 `gzip+USTAR` 分片。
正式与 development profile 使用同一归档机制，不依赖 Git LFS。
正式 profile 强制要求 scenario preflight、封存的 scenario plan/report，以及 Task 1-6
动态功能验收全部通过。题面必做的 `file_query_path_index` 性能 claim 必须有完整有效数据，状态可为
`supported` 或诚实的 `not_supported`；`unavailable`、`failed` 或缺失状态不能形成 formal
包。其 manifest 绑定 source commit、run、suite、campaign、summary、profile 以及
每个 stored payload 的路径、字节数和 SHA256；每个分片还单独记录 stored path/hash/bytes、
压缩协议、成员数和 raw/stored 总量，并逐成员绑定 logical path/raw hash/bytes。包还携带
`measurement-source-receipt.json` 和策略清单覆盖的源码快照；portable verifier 重放两类
Guest 源码合同及控制面清单，committed verifier 再要求快照、receipt 哈希和 C 中对应
Git blob 完全一致。
`checksums.sha256` 再覆盖 manifest 与全部 payload。验证器要求文件清单精确一致，并从
包内分片在私有临时目录安全物化 raw 日志，再运行完整 evaluation contract，并从 summary
确定性重建 Dashboard 后逐字节比较。分片按固定成员顺序写入，gzip header、
`mtime/uid/gid=0`、USTAR 普通文件 mode 与成员元数据均固定；验证器拒绝拼接 gzip member、
绝对路径、`..`、重复成员、链接、设备和非规范类型，以及超出成员数、路径深度、单成员、
总展开量和压缩比预算的输入。它验证 canonical gzip header、USTAR 语义、stored archive
hash 和逐成员 logical hash，不要求当前 zlib 重新压缩后逐字节复现同一 DEFLATE 数据流。
scenario 只允许 plan/report raw-source receipt 明确绑定的 canonical inventory，临时或未知文件
会使打包 fail closed，不能被降级成 generic receipt。formal package 强制要求 config、
build sidecar、report 和 fragment 完整 measured，进行 portable cost 重放并拒绝缺失或
部分组合；只有 development profile 可显式缺省。代码提交本身不构成结果证据；只有真实
QEMU campaign 完成、package 复验通过且
证据目录和 INDEX 精确追加随后形成唯一证据提交 E，相关结论才从“方法就绪”升级为
“本提交有原始证据支持”。验证器从当前 HEAD 历史定位唯一引入该目录的 E，要求 E 以代码
提交 C 为唯一父提交且 C..E 仅含该包普通文件和一行 INDEX 追加；允许后续 D 只修改
`README.md`、`docs/**` 和 `evidence/README.md`，同时要求 INDEX 在 E 后保持完全不变。
任何其他后代代码改动、包字节改写或 INDEX 变化都被拒绝，并在干净 clone 中复验同一合同。
committed delivery 还直接从 Git tree/blob（而非 manifest 或 worktree 声明）强制验证：release
最多 1000 个 tracked 普通文件、单个 stored blob 不超过 64 MiB、总 stored bytes 不超过
256 MiB。任何一项越界都不能成为证据提交 E。

开发接线包必须显式执行：

```bash
scripts/package-evaluation-evidence.sh create <run-dir> <output-dir> --development
```

该 profile 会在 manifest 中永久保存 `DEVELOPMENT EVIDENCE ONLY` 警告，验证时也会再次
显示，不能改名或省略场景后冒充正式竞赛证据。两类包都拒绝路径逃逸、文件或祖先目录中
的 symlink/junction，并逐项重算 Guest、runner、kernel、输入/最终镜像及场景回执。

整轮 `evaluation-run` 由独立 campaign 锁串行化。微基准 boot 在现有 repo 锁内完成
build/QEMU/archive；科研场景则用独立 scenario coordination 锁串行化 manifest 状态、
日志发布和回执归档，Plain 与 AgentOS 的破坏性构建和 QEMU 仍分别由子执行器的
per-target repo 锁保护。场景外层不会跨子进程持有子执行器还要获取的 repo 锁，因而
既避免跨进程自死锁，也不会放弃对并发普通 runner 的 fail-closed 隔离。两类路径都在
对应协调锁内重新读取 pending 状态，并在执行前后复核源码和工具身份。每个微基准
boot 使用 campaign schema 固定并内外一致绑定的 900 秒总期限，
超时会终止进程组、保留部分输出并记录失败，而不是让残留 QEMU 污染下一轮。micro
样本总数由已验证 suite 的 execution schedule、内层 pair 数和双变体合同推导并封入
campaign；实验扩展不会再与 Host 端手写样本常量失配。科研场景
不再把同一个数误作整轮期限：manifest 中的 `timeout_seconds=T` 是每个目标的 runner
基础预算，clean、build、guest 三阶段各取得 `T+30` 秒，目标观察器清理另有 10 秒。
Plain 与 AgentOS 串行执行，外层配对硬期限由同一代码严格派生为
`2 * (3 * (T + 30) + 10) + 60` 秒；默认 `T=600` 时是 3860 秒，最后 60 秒只用于目标间
校验和场景协调。边界测试同时校验 60/3600 秒输入、派生值及真实外层 communicate
期限。科研场景不读取用户的 shell profile，也不继承启动 runner 时的 `MAKEFLAGS`、
`CFLAGS`、`HOME` 或 `PATH`：每一层场景 shell 都由清单绑定的绝对 `env` 启动，以
`env -i` 清空环境，再显式设置 `HOME=/tmp`、固定 `PATH`、`LANG=C`、`LC_ALL=C`、
`TZ=UTC`、空 `MAKEFLAGS/CFLAGS` 以及绑定工具身份的 `MAKE_TOOL`、`TOOLPREFIX`、
`QEMU` 和 `SHELL`，最后通过 `bash --noprofile --norc -c` 执行。环境字典、launcher
argv 和每个 boot 的 Host 环境都进入 manifest 摘要；加入 login flag、注入并行编译
参数或额外 Host 变量即 fail closed。Linux、WSL 与已证明的 native-msys2 使用同一
受控 shell 原则；未证明的 Windows 原生域不属于正式采集路径。科研场景命令还携带每阶段随机身份；Host 在返回后只清理
该身份的后代，并以独立 `/proc` 扫描证明没有残留。清理无法验证时整轮 fail closed。

### 5.1 内核成本证据

ELF、text、data 和 BSS 是系统成本护栏，不是延迟或吞吐量。成本采集器不执行构建，
也不相信孤立的 commit 参数。`evaluation_kernel_build.py` 是其可信生产者：它在仓库锁
内从同一 clean HEAD 依次清理并构建两个目标，逐命令复检 source commit/clean 状态，
验证 RISC-V ELF，绑定固定命令、真实退出码和有界原始输出，再原子保存 environment
manifest 和 build manifest。构建者还要求绝对 `TOOLPREFIX`，在 Windows 接受
`C:/.../riscv64-unknown-elf-`，在 Linux 接受
`/usr/bin/riscv64-linux-gnu-`。它逐一解析并绑定 `gcc`、`ld`、`objcopy`、
`objdump` 的绝对路径、文件 SHA256 和 `--version` 原始输出；每条 Make 命令同时在
环境和命令行固定同一个 `TOOLPREFIX`，任一工具在构建期间变化都会使整次发布失败。
前者 `facts` 按 `name` 排序：

```json
{
  "schema_version": 1,
  "kind": "agentos-evaluation-environment",
  "run_id": "evaluation-20260730",
  "source_commit": "0123456789abcdef0123456789abcdef01234567",
  "environment_id": "kernel-build-<环境摘要前缀>",
  "facts": [
    {"name": "build_environment_sha256", "value": "<64 位小写 SHA256>"},
    {"name": "builder", "value": "evaluation_kernel_build.py/2"},
    {"name": "git", "value": "git version ..."},
    {"name": "make", "value": "GNU Make ..."},
    {"name": "make_path", "value": "/usr/bin/make"},
    {"name": "make_sha256", "value": "<64 位小写 SHA256>"},
    {"name": "platform", "value": "Linux-..."},
    {"name": "python", "value": "3.x.y"},
    {"name": "source_date_epoch", "value": "<commit timestamp>"},
    {"name": "toolchain_identity_sha256", "value": "<工具链身份 SHA256>"},
    {"name": "toolchain_prefix", "value": "/usr/bin/riscv64-linux-gnu-"}
  ]
}
```

build manifest 中的 `environment_sha256` 是上述文件原始字节的 SHA256。所有路径
相对 evidence root，目标顺序和路径由 `ci/evaluation-kernel-cost.json` 固定：

```json
{
  "schema_version": 1,
  "kind": "agentos-kernel-build-manifest",
  "run_id": "evaluation-20260730",
  "source_commit": "0123456789abcdef0123456789abcdef01234567",
  "environment_sha256": "<64 位小写 SHA256>",
  "toolchain_sha256": "<工具链身份 SHA256>",
  "build_config": {
    "path": "kernel-build/kernel-build-config.json",
    "sha256": "<64 位小写 SHA256>"
  },
  "build_log": {
    "path": "kernel-build/raw/kernel-build.log",
    "sha256": "<64 位小写 SHA256>"
  },
  "targets": [
    {
      "id": "baseline",
      "path": "baseline_ucore/build/kernel",
      "sha256": "<64 位小写 SHA256>",
      "command_argv": ["/usr/bin/make", "-C", "baseline_ucore", "TOOLPREFIX=/usr/bin/riscv64-linux-gnu-", "build/kernel"]
    },
    {
      "id": "agentos",
      "path": "build/kernel",
      "sha256": "<64 位小写 SHA256>",
      "command_argv": ["/usr/bin/make", "TOOLPREFIX=/usr/bin/riscv64-linux-gnu-", "build/kernel"]
    }
  ]
}
```

正常入口是 `make evaluation-kernel-cost`。底层构建、采集、可搬运复验、本机重放和
Dashboard 片段使用不同接口：

```bash
cp ci/evaluation-kernel-cost.json results/evaluation/run/kernel-cost-config.json

python3 -I -S scripts/trusted-python-entry.py host_tools/evaluation_kernel_build.py build \
  --config ci/evaluation-kernel-cost.json \
  --repository-root "$PWD" \
  --make-tool /usr/bin/make \
  --toolprefix /usr/bin/riscv64-linux-gnu- \
  --run-id evaluation-20260730 \
  --evidence-root results/evaluation/run \
  --output-dir results/evaluation/run/kernel-build

python3 -I -S scripts/trusted-python-entry.py host_tools/evaluation_kernel_cost.py collect \
  --config results/evaluation/run/kernel-cost-config.json \
  --repository-root "$PWD" \
  --environment-manifest results/evaluation/run/kernel-build/environment.json \
  --build-manifest results/evaluation/run/kernel-build/kernel-build.json \
  --size-tool /usr/bin/riscv64-linux-gnu-size \
  --evidence-root results/evaluation/run \
  --output results/evaluation/run/kernel-cost-report.json

python3 -I -S scripts/trusted-python-entry.py host_tools/evaluation_kernel_cost.py verify \
  --config results/evaluation/run/kernel-cost-config.json \
  --report results/evaluation/run/kernel-cost-report.json \
  --evidence-root results/evaluation/run

python3 -I -S scripts/trusted-python-entry.py host_tools/evaluation_kernel_cost.py verify-local \
  --config results/evaluation/run/kernel-cost-config.json \
  --report results/evaluation/run/kernel-cost-report.json \
  --evidence-root results/evaluation/run \
  --repository-root "$PWD" \
  --size-tool /usr/bin/riscv64-linux-gnu-size

python3 -I -S scripts/trusted-python-entry.py host_tools/evaluation_kernel_cost.py fragment \
  --config results/evaluation/run/kernel-cost-config.json \
  --report results/evaluation/run/kernel-cost-report.json \
  --evidence-root results/evaluation/run \
  --output results/evaluation/run/kernel-cost-fragment.json
```

可信 build config 使用 schema v2，包含六个工具的有序身份表和工具链总摘要；build
log 固定记录 Make 与六个工具的版本命令、两组 clean/build，以及 AgentOS kernel budget
和 user stack 两条 canonical 检查命令，共十三条命令。
`verify` 即使在迁移后没有交叉工具链，也会重算 config/log/manifest 的摘要链，并拒绝
工具路径、版本、哈希、`TOOLPREFIX` 或构建命令被替换。它验证的是随包证据的完整绑定，
不是对工具供应商的外部签名；需要本机重放时仍使用 `verify-local`。

`collect` 核对 clean HEAD、构建配置、原始构建日志及两份 ELF SHA256，并验证
ELF64、小端、RISC-V、EXEC 头和表边界。GNU `size` 输出使用有界捕获；原始输出
随报告保存，text/data/BSS 必须能由它重新解析。`struct proc` 与用户栈值也必须从
build log 中唯一的 canonical checker 行重新解析，不能只信 report 数字。`verify` 只依赖随包 sidecar，
换目录且没有交叉工具链时仍可复验；`verify-local` 再核当前 ELF 和工具哈希并重放
`size`。缺失指标保持 `null + unavailable`。片段只提供成本 benchmark，不自动
生成“AgentOS 更快”的 claim。

## 6. Dashboard

评价页面与原有 40 页科研 Reader 分离，避免继续扩大巨型页面生成器。单页包含：

1. 总览：commit、run、证据等级，并按预注册顺序固定并列四个机制 claim 与 Task6；
2. 性能：多负载下每个独立 boot 的原始配对点/连线、汇总区间、单位、`n`、cache mode 与原始证据链接；
3. 科研场景：cold-start workflow、有符号差值、正反 MCID、胜负数、统计结论、逐程序时间、四类功能模块和预注册关键 outcome；
4. 系统成本：可搬运复验后的 ELF、text、data、BSS 与差值，不混入 CPU 性能 claim；
5. 可信证据：claim 到 raw log 行、SHA256、命令、环境和可点击原件的完整链；
6. 方法学：对照、warmup、顺序、样本数、统计规则和一键复现命令。

总览不再从 `supported` 结果中挑选一条作为唯一 headline。四个机制槽严格跟随
`methodology.multiple_testing.headline_claims`；Task 4 主卡必须标明“逐路径检查 vs 索引”，
固定表扫描卡必须标明“机制消融，不替代题面对照”。Task6 使用独立的 full-stack 槽；
`supported`、`regressed` 与 `inconclusive` 均按原始配对重算后显式展示，缺失项留在原位
显示 `unavailable`，不能由别的 benchmark 补位。资源稳定性与传统兼容路径
成本进入独立的可扩展护栏槽，不参与机制优势、Task6 结论或任何综合总分。

页面离线运行，不依赖 CDN。renderer 会实际读取 evidence 文件并核对 hash、字节数和
marker 行回执，再生成确定性的 `dashboard-verification.json`；成本 sidecar 必须全有
或全无。缺失数据明确显示“本轮未测量”，不以示例 fixture、旧图或静态状态文件填充
首屏结论。

## 7. 不能删除的适用范围

真实评价包可以消除“只有单次文件查询、没有成对重复、没有场景数据”等证据缺口，
但以下边界必须长期保留：

- 单核 QEMU 结果不能外推 SMP 或真实 RISC-V 硬件；
- 突然终止 QEMU 不等于切断真实存储控制器电源；
- checksum 和 hash 不等于密码学身份或供应链认证；
- 有界审计窗口不等于永久、外部不可抵赖日志；
- 没有远端 Runner attestation 时，本地 E3 不能写成远端 E4。

这些是实验适用范围，不是用更漂亮的页面应当掩盖的缺陷。

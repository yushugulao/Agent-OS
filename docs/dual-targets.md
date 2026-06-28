# 双目标 uCore 科研 Agent 平台说明

当前分支同时保留两个可比较目标，目的不是做两个互不相关的演示，而是让同一套科研 Agent 流程分别运行在未改动 uCore 和 AgentOS-uCore 上，直接展示内核支持带来的差异。

## 目标 A：未改动 uCore

仓库根目录是未改动 uCore 目标：

- 内核：`os/`
- 文件系统镜像构建：`nfs/`
- 启动和辅助脚本：`scripts/`
- 用户态科研 Agent 平台：`user/`
- Host Reader、动作运行器、文件系统提取器、LLM Relay：`host_tools/`

这个目标保持 uCore 教学内核不加入 Agent syscall、不加入 Agent Context、不加入内核文件 metadata 服务。科研 Agent 平台通过普通用户进程、普通文件、`fork/exec/wait`、`open/read/write/close` 等机制运行。它用于回答一个问题：只停留在用户态时，一个复杂科研 Agent 平台能做到什么，哪些地方会依赖约定、扫描和文件重建。

常用命令：

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make plain-platform-run TOOLPREFIX=riscv64-linux-gnu-
python host_tools/test_plain_ucore_reader_e2e.py
```

## 目标 B：AgentOS-uCore

增强目标位于 `agentos_ucore/`：

- 内核：`agentos_ucore/os/`
- 文件系统镜像构建：`agentos_ucore/nfs/`
- 启动和辅助脚本：`agentos_ucore/scripts/`
- AgentOS 用户程序和科研平台程序：`agentos_ucore/user/`
- AgentOS 设计、接口和验证文档：`agentos_ucore/docs/`

这个目标在同一 RUN-042 科研流程上使用内核 Agent 服务，包括 Agent 角色和能力、Agent Context、批量工具调用、Context Path、文件 metadata 查询、事件等待和唤醒、heartbeat、timeline、ledger/provenance、文件编辑租约和调度证据。增强目标不能只是在平台旁边跑一组 Agent 测试，而要让主科研流程真正使用这些内核能力。

科研流程的项目名、run id、阶段名称、失败原因和恢复策略由用户态程序写入。结构检查会扫描 `agentos_ucore/os/`，防止这些演示常量变成内核默认业务。

常用命令：

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-run TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

也可以进入 `agentos_ucore/` 后运行：

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
bash scripts/run-agent-tests.sh
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_agentos
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=rp_agentos_orch CHAPTER=platform_agentos
```

## 两个目标必须保持一致的内容

两个目标应使用同一科研场景、同一核心对象名、同一角色名和相近的输出字段。评委应当能看到：

- 未改动 uCore 通过用户态文件和 Host 侧运行器完成科研平台流程。
- AgentOS-uCore 运行等价科研流程，但把可信 Context、metadata 查询、事件通知、失败恢复、权限控制、timeline 和 provenance 交给内核服务。
- 两个目标都输出可比较的 run 记录、artifact 记录、项目评审记录、交付记录、LLM Relay 记录、Agent 协作记录和 AgentCompare 记录。
- 双目标脚本会提取两个镜像中的 `rp_*` 状态文件，并自动对照状态文件集合和成功记录。plain target 已经完成的记录，AgentOS target 必须保留；AgentOS target 额外增加的内核证据单独计入。
- 双目标脚本还会用 Host Reader 渲染两个目标的真实状态文件，并比较渲染摘要。两个目标应生成同一套页面；AgentOS target 可以多出内核证据状态和 API JSON，但不能少于 plain target。
- 增强目标可以增加内核可见证据和更快路径，但不能降低科研流程复杂度。

仓库外的 `research-agent-platform-userland` 是更完整的宿主机科研 Agent 平台原型，仍可能继续增加服务模块、测试和页面。当前分支用 `host_tools/check_host_platform_alignment.py` 读取该目录，并检查 root uCore 与 AgentOS-uCore 是否仍覆盖主要能力族；同时用 `host_tools/check_host_test_alignment.py` 读取宿主平台测试方法，把测试归入功能主题，并要求两个 uCore 目标保留相应证据项；还用 `host_tools/check_host_surface_alignment.py` 直接读取宿主机 `api_server.py`，检查 API/action 路由规模没有被两个 uCore 目标落下；再用 `host_tools/check_host_action_kind_alignment.py` 检查每个宿主机 action 路由都能映射成 seed kind，并且 plain target 与 AgentOS target 的用户态源码里都有对应处理。双目标脚本会运行 `host_tools/check_seeded_action_state.py`，把 44 个预置请求分别送入两个 QEMU 目标，覆盖研究输入、证据处理、artifact、Host workflow、LLM Relay、workbench、数据集、项目生命周期、研究协议、项目评审、workflow 可移植性和 AgentCompare，并检查 `rp_input`、`rp_runner`、`rp_report_text`、`rp_artifact_manifest`、`rp_stage_dag`、`rp_llm_packets`、`rp_wfio`、`rp_usableproj`、`rp_studyproto` 等状态文件是否真正写入同一组关键结果；脚本还会把这 44 个预置请求和宿主机 action 路由总数一起写入 JSON 摘要，让 Compare 页面能区分“已经进 QEMU 实跑的代表性请求”和“已完成 kind 映射但未作为主运行样本的路由”。后续状态文件对照和 Host Reader 渲染直接复用这次运行得到的两个提取目录。这些检查不把宿主机 Python 平台复制进仓库，只把它当作能力参照：如果宿主机平台新增了重要能力、测试主题、API 或 action，而 uCore 迁移层没有对应 `rp_*` 程序、状态文件或展示入口，验证应当暴露这个差距。`make dual-platform-run` 会把这些摘要传给 Host Reader，Compare 页面会展示能力组、测试主题、Web/API/action 规模、预置 action 实际运行结果、plain target 证据和 AgentOS target 证据。

## 当前状态

未改动 uCore 目标已经包含可由 Host Reader 查看的一整套科研平台状态：Web/API 页面数据、动作运行器、artifact 记录、工作流记录、项目评审页、Host LLM Relay、AgentCompare 和端到端 QEMU 路径。

AgentOS-uCore 目标已经把增强内核服务接入同一科研流程。入口 `rp_agentos_orch` 创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，随后运行完整 `rp_orch` 流程。主阶段会向 `rp_agentos_mainflow` 追加十一项内核事实：可信 Context、metadata 索引查询、Agent 事件通知、通用动作提交与工件状态更新、ledger/provenance 观察、sentinel 越权恢复被拒绝、timeline 观察、文件编辑租约、workbench 文件校验、证据包 provenance、真实任务报告与答案审计。

Host Reader 的 Compare 页面会直接渲染 `rp_agentos_mainflow` 和相关 `rp_agentos_*` 文件。评委无需手动翻状态文件，就能在同一浏览器页面上看到 plain target 的用户态成本与 AgentOS target 的内核替代路径。

## 开发约定

根目录只做未改动 uCore 目标。不要把 Agent syscall、Agent Context、内核文件 metadata、Agent 事件队列等增强能力加入根目录 `os/`。

增强内核能力只放在 `agentos_ucore/`。两个目标共享概念时，应在本文档或目标专属设计文档中说明，但不能让 plain target 依赖 AgentOS 内核服务。

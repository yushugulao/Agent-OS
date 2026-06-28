# 双目标完成证据

本文档把当前目标拆成可检查的条目，说明每项能力由哪个脚本、状态文件或输出字段支撑。它不替代 `docs/verification.md` 中的构建和运行说明；实际验收时仍以脚本执行结果为准。

## 总体结论

当前分支同时保留两套可比较目标：

- 根目录 plain uCore 目标保持未改动内核，科研 Agent 平台运行在普通用户态进程和普通文件之上。
- `agentos_ucore/` 目标使用增强版 AgentOS-uCore 内核，运行同一批 seeded 科研请求，并额外生成内核 Agent 参与证据。

最近一次结构检查显示：

```text
[dual-target-check] plain kernel: clean
[dual-target-check] plain kernel base: origin/main
[dual-target-check] AgentOS kernel: present
[dual-target-check] platform source coverage: 73 root rp sources mirrored
[dual-target-check] platform app coverage: 71 build-list apps mirrored
[dual-target-check] platform source sync: identical=30 adapted=43
[dual-target-check] backend evidence coverage: plain=7 agentos=8 preserved_costs=7
[dual-target-check] platform runners: present
[dual-target-check] AgentOS kernel demo constants: absent
[dual-target-check] AgentOS platform legacy tools: security tests only
[dual-target-check] docs: wording scan passed
```

这组输出说明根目录内核仍以 `origin/main` 为基准，AgentOS 只存在于 `agentos_ucore/`，科研平台程序在两个目标中都有对应源码和构建入口。

## 目标要求和证据

| 要求 | 证据来源 | 当前结果 | 说明 |
| --- | --- | --- | --- |
| 根目录保持未改动 uCore 内核 | `scripts/verify-dual-target-structure.sh` 对照 `origin/main` 检查 `os/` 和 `bootloader/` | `plain kernel: clean` | 根目录目标没有加入 Agent syscall、Agent Context、内核 metadata 或 Agent 事件队列。 |
| 增强内核只放在 `agentos_ucore/` | 同一结构检查脚本 | `AgentOS kernel: present` | AgentOS 内核模块、用户态 ABI、专项测试和科研平台增强程序都位于 `agentos_ucore/`。 |
| plain 与 AgentOS 平台程序规模一致 | `PLATFORM_TESTS`、`PLATFORM_SEEDED_TESTS` 构建列表检查 | `platform app coverage: 71 build-list apps mirrored` | 两个目标使用同一批平台程序入口，AgentOS 只对需要接入内核服务的程序做适配。 |
| 科研平台源码保持可比 | 源码同步检查 | `73 root rp sources mirrored`，`identical=30 adapted=43` | 未适配程序保持一致；适配程序集中在 AgentOS 能力接入、状态输出和证据记录。 |
| 两个目标运行同一批 seeded 请求 | `host_tools/check_seeded_action_state.py` | `action_count=44`，`plain=ready`，`agentos=ready` | 44 个请求覆盖研究输入、artifact、Host workflow、LLM Relay、workbench、项目生命周期、研究协议、项目评审和 AgentCompare。 |
| plain 已成功记录在 AgentOS 中保留 | `host_tools/compare_dual_platform_state.py` | `checked_success_records=1244`，`run_result_match=1` | plain target 的成功状态行在 AgentOS target 中保持同一记录标识和成功状态。 |
| AgentOS 增加内核事实而不是替换普通平台 | 同一状态对照脚本 | `agentos_extra_files=13`，`agentos_evidence_checks=32`，`agentos_mainflow_stages=11` | AgentOS 额外输出可信 Context、metadata 查询、事件、权限、ledger/provenance、文件编辑租约、真实任务等证据。 |
| plain target 展示纯用户态成本 | `rp_orch_timing` 和状态对照脚本 | `plain_timing_records=70`，`plain_agent_launches=0`，`plain_fork_launches=70` | plain target 所有平台程序都通过普通 `fork/exec/waitpid` 启动。 |
| AgentOS target 主流程实际使用 Agent | `rp_orch_timing` 和状态对照脚本 | `agentos_timing_records=70`，`agentos_agent_launches=9`，`agentos_fork_launches=61` | AgentOS 将 9 个关键程序绑定到 Agent 创建路径，其余普通支持程序仍用普通进程启动。 |
| Host Reader 能展示两个目标 | `host_tools/plain_ucore_reader.py`、`host_tools/check_reader_output.py`、`host_tools/compare_dual_platform_reader.py` | `plain_pages=40`，`agentos_pages=40` | 两个目标都能渲染同一套页面；AgentOS 额外提供 13 个状态文件和 13 个 API JSON。 |
| 宿主机科研平台能力没有被明显缩小 | `host_tools/check_host_platform_alignment.py` | `host_modules=154`，`groups_ok=13`，`untracked_host_modules=0` | 宿主机平台模块被归入 13 个能力组，两个 uCore 目标都有对应状态文件或展示入口。 |
| 宿主机测试主题有对应证据 | `host_tools/check_host_test_alignment.py` | `host_tests=142`，`themes_ok=7`，`unclassified_tests=0` | 宿主机测试主题在两个 uCore 目标的运行状态中都有对应证据项。 |
| 宿主机 Web/API/action 规模被保留 | `host_tools/check_host_surface_alignment.py` | `api_routes=214`，`action_routes=95`，`download_refs=76` | uCore 目标不复制宿主机 Python 服务，但保留页面、API 摘要和 action 处理规模。 |
| action 路由不是只写在文档里 | `host_tools/check_host_action_kind_alignment.py` | `plain_handler_missing=0`，`agentos_handler_missing=0` | 每个宿主机 action kind 在 plain target 和 AgentOS target 中都有真实运行程序处理。 |
| AgentOS 内核不硬编码科研业务 | `scripts/verify-dual-target-structure.sh` | `AgentOS kernel demo constants: absent` | `RUN-042`、`lab-gene-x`、固定 stage selector 和固定失败原因不出现在内核业务路径中。 |
| 旧演示工具不主导平台主流程 | 同一结构检查脚本 | `AgentOS platform legacy tools: security tests only` | `rerun_stage`、`write_report` 等旧工具只保留在兼容性和权限测试里，主流程使用通用 action、artifact、metadata、event、audit 能力。 |
| AgentOS 专项测试覆盖内核机制 | `agentos_ucore/scripts/run-agent-tests.sh` | `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore` 等入口 | 专项脚本覆盖 Agent 进程、Context、工具调用、文件 metadata、事件等待、heartbeat、权限、LLM relay、timeline、audit 和 provenance。 |

## QEMU 输出中的关键数字

`make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-` 的关键输出应包含：

```text
seeded_action_state: action=/actions/research/rerun action_count=44 host_routes=95 seeded_routes=21 seeded_kinds=21 plain=ready agentos=ready status=ready
dual_platform_state_compare: plain_files=258 agentos_files=271 common_files=258 agentos_extra_files=13 checked_success_records=1244 preserved_plain_costs=7 embedded_action_records=44 run_result_match=1 agentos_evidence_checks=32 agentos_mainflow_stages=11 plain_timing_records=70 plain_agent_launches=0 plain_fork_launches=70 agentos_timing_records=70 agentos_agent_launches=9 agentos_fork_launches=61 status=ready
dual_platform_reader_compare: plain_pages=40 agentos_pages=40 plain_state_files=260 agentos_state_files=273 agentos_extra_state_files=13 plain_api_json=267 agentos_api_json=280 agentos_extra_api_json=13 checked_pages=40 checked_api_json=267 status=ready
```

这些数字对应三类事实：

- 两个目标实际运行同一批 seeded 请求，而不是只比较静态源码。
- AgentOS target 没有丢失 plain target 的成功记录，并额外写出内核参与证据。
- Host Reader 能把两个目标的状态文件渲染成同一套页面，AgentOS 页面额外显示内核能力相关 JSON。

## 完整验证入口

完整验证由以下命令串起：

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

它会按顺序执行：

- 双目标结构检查；
- Host 工具单元测试；
- 宿主机平台能力、测试主题、Web/API/action 规模检查；
- seeded 双目标 QEMU 运行；
- 双目标状态文件对照；
- Host Reader 渲染和页面检查；
- AgentOS 内核专项测试。

如果只改文档，可以先运行快速结构检查和相关 Host 工具测试；如果改动了内核、用户态平台程序、Makefile 或脚本入口，应重新运行完整验证。

## 剩余风险

- `make full-verify` 依赖 RISC-V 工具链、QEMU、Python 和 WSL/Linux shell。公开环境缺少其中任一项时，应按 `docs/verification.md` 分段运行并保留输出。
- 宿主机科研平台目录位于仓库外；公开环境没有该目录时，宿主机能力检查会跳过外部目录读取，但仓库内双目标 QEMU、状态文件对照和 Host Reader 检查仍可独立运行。
- AgentOS target 追求的是同一科研流程中更强的内核支持和可观测状态，不承诺每个程序的绝对运行时间都短于 plain target。性能评价应结合 `rp_orch_timing`、`agentbench_ucore` 和具体内核路径输出判断。

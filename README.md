# 双目标 uCore 科研 Agent 平台：project61-agentOS-happylegend

本分支同时维护两套可比较的科研 Agent 平台实现：

- 根目录目标：未改动 uCore 内核。科研 Agent 平台全部运行在普通用户态进程和普通文件之上。
- `agentos_ucore/` 目标：增强版 AgentOS-uCore 内核。科研 Agent 平台保持同一 RUN-042 场景和输出契约，但关键阶段使用内核 Agent 服务。

这样设计的目的，是让评委能直接看到同一科研 Agent 工作流在两种系统支持程度下的差异：用户态实现能完成什么，内核支持又在哪些环节减少扫描、减少约定、强化恢复、增强可信记录。

## 目录布局

根目录是 plain uCore 目标：

```text
os/          未改动 uCore 内核
nfs/         文件系统镜像构建
scripts/     启动和辅助脚本
user/        用户态科研 Agent 平台程序
host_tools/  Host Reader、动作运行器、镜像提取器、LLM Relay
docs/        双目标设计、验证和后续工作说明
```

增强目标位于：

```text
agentos_ucore/os/       AgentOS-uCore 内核
agentos_ucore/user/     AgentOS 测试程序和科研平台程序
agentos_ucore/docs/     AgentOS 任务、接口、设计和验证文档
agentos_ucore/scripts/  AgentOS 测试脚本
```

根目录 `os/` 必须保持未改动 uCore 对照目标；Agent syscall、Agent Context、内核文件 metadata、Agent 事件队列等增强能力只应出现在 `agentos_ucore/`。

## 文档入口

| 阅读目标 | 文档 |
| --- | --- |
| 快速了解增强内核项目 | `agentos_ucore/README.md` |
| 查看增强内核架构图和关键设计 | `agentos_ucore/docs/design.md` |
| 查看增强内核测试场景和输出证据 | `agentos_ucore/docs/verification.md` |
| 查看双目标对比设计 | `docs/dual-targets.md` |
| 查看双目标验证方式 | `docs/verification.md` |
| 查看当前目标完成证据 | `docs/target-evidence.md` |

## 常用命令

构建和运行未改动 uCore 平台：

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make plain-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

构建和运行 AgentOS-uCore 平台：

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

一次运行两个目标并比较关键输出：

```bash
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

该命令会先检查目录职责、平台程序覆盖、源码同步、backend 证据覆盖和文档措辞，再启动两个 QEMU 目标。源码同步用于确认未适配的科研平台程序保持一致、已接入内核能力的程序有明确记录；backend 证据覆盖用于确认 AgentOS 平台保留 plain 平台的用户态成本项，并在此基础上增加内核事实。两个目标运行结束后，脚本会从 `fs-copy.img` 提取 `rp_*` 状态文件，确认镜像里确实保留了页面查看器所需的运行证据。

完整验证入口：

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

该命令会依次执行双目标结构检查、Host Reader 与宿主工具测试、双目标 QEMU 运行和 AgentOS 专项测试。宿主工具测试覆盖 action runner、文件系统镜像提取器和 LLM relay。默认使用 `python3`、`qemu-system-riscv64` 和 `240s` 单项超时时间；如需调整，可设置 `PYTHON_BIN=...`、`QEMU=...`、`CASE_TIMEOUT=...`。

快速检查双目标目录职责：

```bash
bash scripts/verify-dual-target-structure.sh
```

默认会用 `origin/main` 对照根目录 `os/` 和 `bootloader/`。如果需要使用其他 plain 基准，可以设置 `UCORE_PLAIN_BASE_REF=...`。

Host Reader 相关检查：

```bash
python host_tools/test_plain_ucore_reader.py
python host_tools/test_plain_ucore_reader_e2e.py
```

## 未改动 uCore 目标

未改动 uCore 目标展示的是普通用户态机制可以承载到什么程度。该目标使用普通 C 用户程序、普通文件、`fork`、`exec`、`waitpid`、`open`、`read`、`write`、`close` 等机制运行一个较完整的科研 Agent 平台。

主要程序包括：

```text
rp_plain
rp_orch
rp_seed_orch
rp_catalog
rp_object_store
rp_object_query
rp_lineage
rp_site_export
rp_planner
rp_portability
rp_retriever
rp_analyst
rp_reviewer
rp_lab
rp_governance
rp_writer
rp_repair
rp_auditor
rp_query
rp_evidence
rp_llm_bridge
rp_llm_relay
rp_privacy
rp_runconf
rp_execobs
rp_invoke
rp_complete
rp_artifact_ops
rp_data_pipeline
rp_workflow_runner
rp_workbench
rp_agent_collab
rp_package
rp_calculation
rp_realtask
rp_analysisres
rp_decsupport
rp_usable
rp_usableproject
rp_campaign
rp_delta
rp_release
rp_dossier
rp_service_surface
rp_modelreg
rp_sysreview
rp_expsched
rp_traincomp
rp_startup_doctor
rp_notebook_export
rp_backend
rp_consistency
rp_metrics
rp_ui_export
rp_web_export
rp_revdash
rp_publication
rp_runbooks
rp_projectrel
rp_studyproto
rp_stdesign
rp_opsboard
rp_reviewboard
rp_controlplane
rp_integrityplane
rp_coherenceplane
rp_mature
rp_prov_view
rp_prov_query
rp_test_suite
rp_compare_plain
```

这些程序共同生成 `rp_*` 状态文件。Host Reader 会把这些状态文件渲染成浏览器页面、API JSON、动作记录、对比表、证据包视图和 LLM Relay 视图。

## 增强目标：AgentOS-uCore

AgentOS-uCore 目标保留同一科研流程，但把关键阶段接入内核服务。当前增强目标重点展示：

- Agent 角色和 capability。
- Agent Context 与 Context Path。
- 批量工具调用。
- 文件 metadata 查询和真实文件绑定。
- Agent 事件队列、wait/wake、heartbeat。
- timeline、ledger、provenance snapshot。
- 权限拒绝、失败恢复、文件编辑租约、工作台文件校验、证据包追踪和真实任务 Context。

增强目标入口为 `rp_agentos_orch`。它创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，再运行完整科研平台流程。主流程会向 `rp_agentos_mainflow` 追加十二项事实：

```text
trusted Context
generic dependency graph + dependency-driven prefetch
metadata indexed query
Agent event notification
action_commit + artifact_update recovery
ledger/provenance observation
sentinel permission denial
timeline observation
kernel edit lease
workbench file verification
package provenance
real-task report/audit Context
```

这些事实会被 `rp_backend`、`rp_consistency`、`rp_metrics`、`rp_compare_plain`、`rp_test_suite` 和 Host Reader 读取，因此增强目标不是“旁边跑一组 Agent 测试”，而是同一科研流程实际依赖内核 Agent 服务。

## 页面查看器：Host Reader

`host_tools/plain_ucore_reader.py` 负责把 `rp_*` 状态文件渲染为浏览器可读页面和 API JSON。它提供：

- 首页 `Dual Target Overview`：说明 plain target、AgentOS target、共享 run 和比较入口。
- Run/Workflow/Workbench/Project/Data/Review/Delivery/LLM 等页面。
- Compare 页面：同时展示 plain target 成本和 AgentOS 替代路径。
- `AgentOS Main Flow Kernel Stages`：读取 `rp_agentos_mainflow`。
- `AgentOS Kernel Output Files`：读取相关 `rp_agentos_*` 文件。
- action trace、action output、impact、delta 表。
- Host LLM Relay 模板模式和可选云端模式。

Host Reader 只读取和渲染状态文件，不修改 uCore 内核。
双目标运行时，脚本还会把宿主机科研 Agent 平台的能力对齐摘要、测试主题对齐摘要和 Web/API/action 规模摘要交给 Host Reader；Compare 页面会把这些摘要和两个 uCore 目标的状态文件放在一起展示。

## 与宿主机科研 Agent 平台的关系

宿主机科研 Agent 平台仍在迭代。uCore 迁移目标不是逐行复制 Python 实现，而是在 uCore 环境中保留评委可见的科研 Agent 平台能力：

- Web/API 页面数据。
- 真实输入文件、中间 artifact、报告、日志和图表数据。
- stage DAG、依赖、失败、重试、缓存和日志。
- Host LLM Relay 的请求、路由、guard、fallback、响应和质量记录。
- 多 Agent 协作、决策、确认和审计记录。
- workbench、project、delivery、provenance、review 和 AgentCompare 记录。

当宿主机平台新增可见对象或输出字段时，应优先判断是否需要同步到 plain target 与 AgentOS target。

## 验证材料

主要文档：

- `docs/dual-targets.md`：双目标布局和一致性要求。
- `docs/design.md`：双目标设计、内核机制、状态文件协议。
- `docs/verification.md`：构建、运行、双目标检查和 Host Reader 验证。
- `docs/next-work.md`：后续开发方向。
- `agentos_ucore/docs/`：AgentOS-uCore 任务、接口、设计、测试和验收说明。

典型运行输出应包含：

```text
[dual-platform] checking target structure
[dual-platform] running seeded dual-target research platform
seeded_action_state: plain start chapter=platform_seeded init=rp_seed_orch action_count=44 log=/tmp/agentos-dual-platform/seeded-action-state/plain/ucore-run.log
seeded_action_state: plain done passed=1 extracted_state_files=258 status=ready
seeded_action_state: agentos start chapter=platform_agentos init=rp_agentos_orch action_count=44 log=/tmp/agentos-dual-platform/seeded-action-state/agentos/ucore-run.log
seeded_action_state: agentos done passed=1 extracted_state_files=271 status=ready
seeded_action_state: action=/actions/research/rerun action_count=44 host_routes=95 seeded_routes=21 seeded_kinds=21 plain=ready agentos=ready status=ready
[dual-platform] plain uCore research platform log: /tmp/agentos-dual-platform/seeded-action-state/plain/ucore-run.log
rp_orch: passed
rp_orch: programs_ok=70 programs_total=70
rp_backend: cases=7 executable=7 userland_equivalent=ready exports=1 status=ready
[dual-platform] AgentOS-uCore research platform log: /tmp/agentos-dual-platform/seeded-action-state/agentos/ucore-run.log
rp_agentos_orch: passed
rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready
rp_orch: programs_ok=70 programs_total=70
rp_backend: cases=8 executable=8 agentos=mainflow_bound exports=1 status=ready
[dual-platform] plain extracted state files: 258
[dual-platform] AgentOS extracted state files: 271
host_platform_alignment: host_modules=154 tracked_host_modules=154 plain_sources=73 agentos_sources=74 runtime_state_checked=1 groups_ok=13 groups_total=13 untracked_host_modules=0 status=ready
host_test_alignment: host_tests=142 themes_ok=7 themes_total=7 unclassified_tests=0 runtime_state_checked=1 status=ready
host_action_kind_alignment: action_routes=95 action_kinds=95 generic_routes=0 plain_missing=0 agentos_missing=0 plain_handler_missing=0 agentos_handler_missing=0 status=ready
host_surface_alignment: api_routes=214 action_routes=95 download_refs=76 runtime_state_checked=1 status=ready
dual_platform_state_compare: plain_files=258 agentos_files=271 common_files=258 agentos_extra_files=13 checked_success_records=1244 preserved_plain_costs=7 embedded_action_records=44 run_result_match=1 agentos_evidence_checks=32 agentos_mainflow_stages=11 plain_timing_records=70 plain_agent_launches=0 plain_fork_launches=70 agentos_timing_records=70 agentos_agent_launches=9 agentos_fork_launches=61 status=ready
plain_ucore_reader: pages=40 api_json=267 state_files=260 status=ready
plain_ucore_reader: pages=40 api_json=280 state_files=273 status=ready
reader_output_check: pages=40 api_json=267 state_files=260 required_pages=6 spec_pages=40 agentos_compare_markers=0 status=ready
reader_output_check: pages=40 api_json=280 state_files=273 required_pages=6 spec_pages=40 agentos_compare_markers=13 status=ready
dual_platform_reader_compare: plain_pages=40 agentos_pages=40 plain_state_files=260 agentos_state_files=273 agentos_extra_state_files=13 plain_api_json=267 agentos_api_json=280 agentos_extra_api_json=13 checked_pages=40 checked_api_json=267 status=ready
```

快速结构检查应包含：

```text
[dual-target-check] plain kernel: clean
[dual-target-check] plain kernel base: origin/main
[dual-target-check] AgentOS kernel: present
[dual-target-check] platform source coverage: 73 root rp sources mirrored
[dual-target-check] platform app coverage: 71 build-list apps mirrored
[dual-target-check] platform source sync: identical=30 adapted=43
[dual-target-check] backend evidence coverage: plain=7 agentos=8 preserved_costs=7
[dual-target-check] platform runners: present
[dual-target-check] docs: wording scan passed
```

## 文档语言约定

技术文档以中文为主要语言。命令、文件名、函数名、结构体名、状态字段、程序输出和协议名称保持原文，避免破坏可执行接口和可核验的证据文本。

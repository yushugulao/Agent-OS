# project61-agentOS-happylegend: plain uCore research platform branch

This branch is the plain-kernel baseline for the research Agent platform.

The kernel source under `os/` and the boot/init helper under `scripts/` are restored from the upstream uCore 2025S source. The `nfs/` builder keeps the same disk format and uses a 4096-block image so the plain platform can hold its research state files. The research platform work in this branch is placed in ordinary user space.

## Purpose

The branch answers one specific question: how far the research Agent platform can run on an unchanged uCore kernel before the later Agent-OS enhanced kernel is used.

It is not the Agent-OS kernel-enhanced version. There are no Agent syscalls, Agent Context pages, kernel file metadata indexes, or kernel Agent event queues in `os/`.

## Directory Rule

This branch holds two comparable targets. The repository root keeps the plain uCore target: `os/`, `nfs/`, `scripts/`, `user/`, and `host_tools/` describe the research Agent platform running on ordinary uCore mechanisms. The Agent-OS enhanced uCore target is stored under `agentos_ucore/`, with its own kernel, user programs, tests, and documents. This keeps the plain-kernel comparison runnable while allowing the enhanced target to use new kernel services.

See `docs/dual-targets.md` for the target layout and parity direction.

Useful root commands:

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

## Current User Program

The first native uCore entries are:

```text
user/src/rp_plain.c
user/src/rp_orch.c
user/src/rp_seed_orch.c
user/src/rp_catalog.c
user/src/rp_object_store.c
user/src/rp_object_query.c
user/src/rp_lineage.c
user/src/rp_site_export.c
user/src/rp_planner.c
user/src/rp_portability.c
user/src/rp_retriever.c
user/src/rp_analyst.c
user/src/rp_reviewer.c
user/src/rp_lab.c
user/src/rp_governance.c
user/src/rp_writer.c
user/src/rp_repair.c
user/src/rp_auditor.c
user/src/rp_query.c
user/src/rp_evidence.c
user/src/rp_llm_bridge.c
user/src/rp_llm_relay.c
user/src/rp_privacy.c
user/src/rp_runconf.c
user/src/rp_execobs.c
user/src/rp_invoke.c
user/src/rp_complete.c
user/src/rp_artifact_ops.c
user/src/rp_data_pipeline.c
user/src/rp_workflow_runner.c
user/src/rp_workbench.c
user/src/rp_agent_collab.c
user/src/rp_package.c
user/src/rp_delta.c
user/src/rp_release.c
user/src/rp_dossier.c
user/src/rp_service_surface.c
user/src/rp_startup_doctor.c
user/src/rp_notebook_export.c
user/src/rp_metrics.c
user/src/rp_backend.c
user/src/rp_consistency.c
user/src/rp_ui_export.c
user/src/rp_web_export.c
user/src/rp_review_dashboard.c
user/src/rp_publication.c
user/src/rp_runbooks.c
user/src/rp_projectrel.c
user/src/rp_studyproto.c
user/src/rp_opsboard.c
user/src/rp_reviewboard.c
user/src/rp_controlplane.c
user/src/rp_integrityplane.c
user/src/rp_coherenceplane.c
user/src/rp_test_suite.c
user/src/rp_compare_plain.c
```

`rp_plain` is a normal uCore user process. It embeds the current pure user-space research platform catalog and validates:

- 500 platform object counters.
- 120 service names.
- 28 feature groups.
- 13 platform self-check groups.
- 6 reference research platforms: Galaxy, AiiDA, DVC, MLflow, Nextflow, Snakemake.
- 6 mature capability mappings with a target coverage ratio of at least 30%.
- A plain user-space research run simulation with planning, literature, analysis, review, writing, repair, and audit roles.
- Local catalog search for workflow, Agent, evidence, provenance, and LLM related platform objects.

`rp_orch` runs 52 platform programs as separate uCore user processes:

- catalog,
- object store,
- object query,
- lineage,
- site export,
- planner,
- workflow portability and migration planning,
- retriever,
- analyst,
- reviewer,
- lab evidence service,
- risk and CAPA governance,
- writer,
- repair,
- auditor,
- query,
- evidence,
- LLM bridge,
- file-backed Host LLM Relay protocol,
- privacy review,
- run configuration,
- execution observer,
- workflow invocation,
- workflow completion,
- real artifact operations,
- data ingestion and dataset pipeline,
- workflow runner execution evidence,
- research workbench task state,
- Agent collaboration evidence,
- package,
- release delta review,
- release decision,
- final dossier,
- service surface export for bio, lab resources, publication, knowledge, and runtime records,
- reproducible notebook export and download records,
- backend scenario,
- cross-file consistency check,
- metrics service,
- UI data export,
- Host Web/API export contract,
- review dashboard aggregation,
- reviewer evidence package,
- research operations board,
- formal review board and review operations,
- platform control plane for approvals, notifications, run queue, plugins, workspace access, and saved views,
- integrity plane for evidence traceability, reference integrity, namespace, status semantics, review alignment, report sources, and package trace checks,
- coherence plane for delivery contracts, run-state contracts, lifecycle order, workflow lint, tool protocol checks, report validation, and Agent coordination checks,
- file-backed human review and revision-task actions,
- test suite,
- plain-kernel comparison.

It uses ordinary `fork`, `exec`, and `waitpid`. This provides a plain-kernel baseline for the later Agent-OS multi-Agent version.

`rp_seed_orch` is the Host-action run entry. It runs the seeded program set used by the Host reader path. The action runner keeps the full captured Host action text in the host run package, writes a compact `rp_host_action_seed` file into the uCore image, and each native user program reads that ordinary file through the unchanged uCore file system. The seeded image omits the standalone `rp_test_suite` executable to stay within the teaching file-system image capacity; `rp_compare_plain` publishes the current test count in `rp_agentcmp` before it performs the final comparison checks.

The Host Reader Run and Compare pages consume `rp_backend_exec` and `rp_study` directly, so the visible pages show backend runner cases, their input files, generated artifacts, content checks, attempts, retry reasons, per-case source/requirement/observation/action/review rows, derived case narratives, plain-uCore cost, AgentOS replacement, risk rows, results, plain-uCore study metrics, planned Agent-OS metrics, and the backend scenario handoff status instead of only showing aggregate counts.

The role programs also exchange state through ordinary root-file-system files:

- `rp_plan`
- `rp_mail`
- `rp_ack`
- `rp_tool`, compact tool event names used for comparison and metrics
- `rp_sched`
- `rp_taskrec`
- `rp_budget`
- `rp_wfio`, including workflow import/export and portability records
- `rp_policy`
- `rp_retryq`
- `rp_lit`
- `rp_data`
- `rp_datadic`
- `rp_dataprof`
- `rp_compute`
- `rp_figrec`
- `rp_fail`
- `rp_samples`
- `rp_quality`
- `rp_review`
- `rp_review2`
- `rp_protocol`
- `rp_soplog`
- `rp_exper`
- `rp_trialrec`
- `rp_labops`
- `rp_training`
- `rp_risk`
- `rp_capa`
- `rp_report`
- `rp_revision`
- `rp_fix`
- `rp_retrylog`
- `rp_telemetry`
- `rp_health`
- `rp_audit`
- `rp_status`
- `rp_objects`
- `rp_services`
- `rp_object_records`
- `rp_object_query`
- `rp_lineage`
- `rp_site`
- `rp_query`
- `rp_rank`
- `rp_runview`
- `rp_evidence`
- `rp_claimrec`
- `rp_provpath`
- `rp_knowledge`
- `rp_llm_req`
- `rp_llmq`
- `rp_llm_resp`
- `rp_relay`
- `rp_prompt`
- `rp_llmlog`
- `rp_llmeval`
- `rp_llm_packets`
- `rp_llm_routes`
- `rp_llm_guard`
- `rp_llm_hostreq`
- `rp_llm_fallback`
- `rp_privacy`
- `rp_compliance`
- `rp_params`
- `rp_runconf`
- `rp_configval`
- `rp_configdrift`
- `rp_execplan`
- `rp_worker`
- `rp_timeline`
- `rp_execobs`
- `rp_invocation`
- `rp_steps`
- `rp_attempts`
- `rp_invoke_export`
- `rp_hooks`
- `rp_completion`
- `rp_actions`
- `rp_complete_export`
- `rp_input`
- `rp_input_fastq`
- `rp_stage_dag`
- `rp_stage_log`
- `rp_artifact`
- `rp_report_text`
- `rp_chart_data`
- `rp_ingest_files`
- `rp_dataset_snapshot`
- `rp_data_preview`
- `rp_data_quality`
- `rp_data_transform`
- `rp_dataset_collection`
- `rp_runner`
- `rp_stage_state`
- `rp_cache_index`
- `rp_retry_plan`
- `rp_run_events`
- `rp_artifact_manifest`
- `rp_agents`
- `rp_decisions`
- `rp_handoff`
- `rp_deliberation`
- `rp_agent_run`
- `rp_package`
- `rp_diff`
- `rp_delta`
- `rp_datarel`
- `rp_dataver`
- `rp_repro`
- `rp_release`
- `rp_dossier`
- `rp_reviewops`
- `rp_review_dashboard`
- `rp_runbooks`
- `rp_projectrel`
- `rp_studyproto`
- `rp_opsboard`
- `rp_reviewboard`
- `rp_control`
- `rp_integrity`
- `rp_submit`
- `rp_sreg`
- `rp_ethics`
- `rp_access`
- `rp_cohort`
- `rp_bioop`
- `rp_instr`
- `rp_invent`
- `rp_procure`
- `rp_ressched`
- `rp_labresop`
- `rp_resrev`
- `rp_pubplan`
- `rp_peerresp`
- `rp_fairpkg`
- `rp_pubop`
- `rp_litrev`
- `rp_citegraph`
- `rp_semindex`
- `rp_kanswers`
- `rp_knowop`
- `rp_runenv`
- `rp_nbexec`
- `rp_eln`
- `rp_wpool`
- `rp_runop`
- `rp_agentcmp`
- `rp_backend`
- `rp_backend_exec`
- `rp_study`
- `rp_consistency`
- `rp_ui_home`
- `rp_ui_run`
- `rp_ui_agent`
- `rp_ui_evidence`
- `rp_ui_compare`
- `rp_web_routes`
- `rp_api_home`
- `rp_api_run`
- `rp_api_agents`
- `rp_api_evidence`
- `rp_api_compare`
- `rp_api_artifacts`
- `rp_api_data`
- `rp_api_bio`
- `rp_api_labres`
- `rp_api_pub`
- `rp_api_know`
- `rp_api_runtime`
- `rp_api_action`
- `rp_actionio`
- `rp_uresrun`
- `rp_web_bundle`
- `rp_review_pack`
- `rp_agentcmp`

The standalone `rp_test_suite` program writes `rp_tests` when it is run directly. The main orchestrated path keeps the current 1298-check count and comparison result in `rp_agentcmp` so the full seeded run stays inside the teaching file-system inode budget.

Each program validates the files it depends on before writing its own artifact. The orchestrator reads `rp_status`, `rp_audit`, and `rp_agentcmp` after all children exit, then prints `state_ok=1`.

The program prints:

```text
rp_plain: passed
```

when the built-in checks pass.

## Build And Run

In WSL Ubuntu:

```bash
cd /mnt/e/计算机操作系统能力竞赛/project61-agentOS-happylegend-uCore
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch
timeout 90s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch
```

Expected key output:

```text
rp_plain summary
objects=500 object_total=102790 services=120 features=28 feature_units=299 checks=13 references=6 mappings=6
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
rp_plain: passed
```

Expected orchestrator output:

```text
rp_orch: start programs=53
rp_catalog: objects=500 services=120 features=28 status=ready
rp_state_catalog: keys=573 nonzero=70 zero=503 represented=573 checks=12 status=ready
rp_object_store: records=8 status=ready
rp_object_query: hits=8 ready_hits=7 status=ready
rp_lineage: edges=7 status=ready
rp_site_export: pages=42 status=ready
rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=21 schedule=ready status=planned
rp_portability: imports=5 adapters=6 migration_steps=9 rehearsals=2 status=ready
rp_retriever: literature=3 evidence_links=5 status=ready
rp_analyst: datasets=4 profiles=4 statistics=6 figures=3 failure=tool_output_missing status=ready
rp_reviewer: claims=8 protocol_checks=5 release_checks=4 rounds=2 status=accepted
rp_lab: samples=4 quality_checks=7 protocol_checks=5 trials=4 trial_records=4 status=ready
rp_governance: risks=3 capa=2 deviations=1 status=ready
rp_writer: sections=8 citations=9 revisions=3 status=packaged
rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered
rp_auditor: provenance=verified release=ready package=ready status=passed
rp_query: workflow=34 agent=26 evidence=10 ranked=21 selected=10 search_docs=1385 provenance=406/544 status=ready
rp_evidence: claims=8 links=5 claim_records=8 paths=3 status=ready
rp_llm_bridge: requests=3 responses=3 transcripts=90 routes=4 eval=7 relay=ready status=ready
rp_llm_relay: packets=3 routes=4 guard=ready fallback=1 status=ready
rp_privacy: checked=13 packets=3 redactions=0 compliance=accepted status=ready
rp_runconf: profiles=2 validations=2 drift=1 status=ready
rp_execobs: timeline=9 workers=4 controls=8 observer=ready status=ready
rp_invoke: steps=10 attempts=12 outputs=6 status=recovered
rp_complete: hooks=4 events=1 actions=4 status=ready
rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 custom_requests=3 status=ready
rp_data_pipeline: files=2 snapshots=2 previews=2 quality=passed transforms=2 status=ready
rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 custom_runs=3 status=ready
rp_workbench: tasks=9 workspace_files=4 runs=4 exports=7 workbenches=5 deliveries=6 project_ops=15 status=ready
rp_agent_collab: agents=7 messages=21 decisions=8 handoffs=6 status=ready
rp_package: artifacts=52 checks=75 fair=passed repro=ready status=ready
rp_delta: items=20 reviews=1 decision=accepted status=ready
rp_release: decision=release checks=17 status=ready
rp_dossier: sections=36 review_board=accepted submit=ready status=ready
rp_service_surface: bio=ready lab_resources=ready publication=ready knowledge=ready runtime=ready status=ready
rp_startup_doctor: quickstart=ready doctor=ready checks=14 status=ready
rp_notebook_export: notebooks=2 cells=8 downloads=4 status=ready
rp_backend: cases=4 executable=2 exports=1 status=ready
rp_consistency: checks=420 tasks=21 llm=3 relay=5 workflow=5 portability=6 coherence=9 data=6 services=25 lab_governance=26 products=18 assurance=24 research_ops=28 regulated=32 state_catalog=12 startup_doctor=14 knowledge_index=22 llm_transcripts=3 workbench_delivery=15 portfolio_scale=16 execution_scale=14 operations_scale=12 project_revision_incident=12 reserved_surfaces=21 root_state=10 agentos_reserved=21 backend=4 artifacts=7 agents=7 dynamic=4 status=ready
rp_metrics: telemetry_spans=8 acks=35 tools=115 services=25 lab_governance=26 products=18 assurance=24 research_ops=28 regulated=32 state_catalog=12 startup_doctor=14 knowledge_index=22 llm_transcripts=3 workbench_delivery=15 portfolio_scale=16 execution_scale=14 operations_scale=12 project_revision_incident=12 reserved_surfaces=21 root_state=10 agentos_reserved=21 delta_items=20 dynamic=4 status=ready
rp_ui_export: pages=5 run=RUN-042 custom_runs=3 compare=ready status=ready
rp_web_export: routes=74 api_payloads=14 actions=57 bundle=ready status=ready
rp_review_dashboard: sections=8 gates=6 review_pack=host-materialized status=ready
rp_publication: targets=2 submissions=2 reviews=2 responses=2 items=4 checks=48 status=ready
rp_runbooks: templates=1 steps=7 incidents=1 executions=1 exports=1 status=ready
rp_projectrel: checks=18 release=ready reproducibility=passed intake=accepted status=ready
rp_studyproto: checks=20 protocols=2 launches=2 reproduction=ready status=ready
rp_opsboard: checks=18 pending=1 actions=4 plan_items=5 handoffs=3 status=ready
rp_reviewboard: checks=24 requests=1 votes=4 signoffs=4 assignments=4 decision=approved status=ready
rp_controlplane: checks=30 approvals=4 notifications=4 queue=4 plugins=3 permissions=5 status=ready
rp_integrityplane: checks=36 evidence=8 references=8 namespace=5 status_semantics=5 review_alignment=4 status=ready
rp_coherenceplane: checks=40 delivery=7 run_state=7 lifecycle=6 workflow_lint=5 tool_protocol=5 report_validation=5 status=ready
rp_compare_plain: plain_kernel=passed objects=500 programs=53 state_files=183 acks=53 tools=197 dynamic=4 products=18 assurance=24 research_ops=28 regulated=32 lab_governance=26 state_catalog=12 startup_doctor=14 runbook_service=16 project_delivery=18 study_protocol=20 opsboard=18 review_board=24 control_plane=30 integrity_plane=36 coherence_plane=40 publication=48 knowledge_index=22 llm_transcripts=3 workbench_delivery=15 portfolio_scale=16 execution_scale=14 operations_scale=12 project_revision_incident=12 reserved_surfaces=21 root_state=10 agentos_reserved=21 reader=1 status=ready
rp_orch: programs_ok=53 programs_total=53
rp_orch: state_ok=1
rp_orch: passed
```

The current upstream uCore kernel prints `all app are over!` after the init user program exits. In this branch that message means the plain user program finished and the kernel reached its existing no-more-apps path.

## Kernel Source Check

Use these checks to verify that this branch keeps the kernel source unchanged:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
grep 'FSSIZE 4096' nfs/fs.h
```

The two `diff` commands should print no output. The `grep` command confirms the platform image capacity used by the plain target.

## Host Reader

The `host_tools/plain_ucore_reader.py` utility renders ordinary `rp_*` state files into host-viewable HTML pages and API JSON files. It consumes the `host_plain_ucore_v2` reader contract written by `rp_web_bundle`. The generated pages use a sidebar, page-level summary cards, research-output summaries, report source maps, artifact source maps, review source maps, delivery source maps, a delivery package page, a workflow runner page, a research workbench page, a research Studio page, a project page, a project delivery review page, a data pipeline page with ingest, snapshot, preview, quality, transform, collection, and manifest verification tables, workflow execution view tables, workflow control view tables, workflow evidence link tables, Agent detail summaries, Agent roster and decision-flow tables, evidence-package summaries, evidence detail summaries, review dashboard sections and gates, service operation records, runbook service records, claim/provenance/protocol tables, comparison summaries, compare metric summaries, operations report narrative tables, operations source-file tables, LLM Relay flow/request/response/quality tables, plain-kernel signal tables, state tables, Run/Workflow/Workbench/Studio/Project/Data/Compare/Review/Delivery/LLM Host action trace tables, action-output link and detail tables, action-impact tables, action-delta tables, host-action history, and a batch-action editor for running the same research flow through plain uCore.

```bash
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader --serve --port 8767
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader --serve --port 8767 --auto-run-ucore --repo-dir . --run-root runtime/plain_ucore_auto_runs
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader --serve --port 8767 --auto-run-ucore --auto-run-llm-relay --llm-relay-mode template --repo-dir . --run-root runtime/plain_ucore_auto_runs
python host_tools/plain_ucore_fs_extract.py --image nfs/fs-copy.img --out-dir runtime/plain_ucore_extracted --repo-dir .
python host_tools/plain_ucore_llm_relay.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_relay --mode template
python host_tools/test_plain_ucore_fs_extract.py
python host_tools/test_plain_ucore_reader.py
python host_tools/test_plain_ucore_reader_e2e.py
python host_tools/test_plain_ucore_llm_relay.py
```

With `--serve`, the reader exposes `/api/reader-summary`, `/api/contract`, `/api/state/{name}`, `/api/live`, static pages, `/actions/...` POST capture, and `/actions/batch` for a group of related research actions. Action requests are written to `host-actions.jsonl`; use `--write-state-actions` only when the host should also append an action inbox record beside the `rp_*` state files. With `--auto-run-ucore`, a single POST action or one `/actions/batch` request invokes the action runner, builds and runs `CHAPTER=platform_seeded` with `INIT_PROC=rp_seed_orch`, writes `rp_host_run_result`, extracts the generated state files, publishes the next state package back to the served state directory, and refreshes the generated pages. With `--auto-run-llm-relay`, the reader then runs `plain_ucore_llm_relay.py` over the refreshed `rp_llm*` files; template mode is offline and deterministic, while OpenAI-compatible mode reads endpoint, key, model, and timeout from host environment variables without writing secret values into uCore files. The end-to-end reader test starts the HTTP handler, sends a batch of ninety-three actions covering research run, Studio launch, dataset registration, library source registration, template registration, workspace inspection, workspace import, literature search, evidence review, evidence protocol, host workflow run/export/stage/cache/retry/artifact/report, artifact input/derive/log/chart/package operations, workflow portability run/import/plan/bind/rehearse/review/package, Host LLM Relay request, Host LLM Relay response, Host LLM Relay fallback, human review, revision task, revised run, workbench creation, workbench advance, workbench auto-advance, readiness, answer, answer audit, evidence search, task update, note, notes export, handoff package, brief, evidence dossier, evidence graph, citations, manuscript draft, manuscript audit, manuscript revision plan, manuscript revision task, task board, task-board row update, plan queue, delivery dashboard, quality gate and repair, operations report, operations advance, project space, project handoff audit, project release gate, project snapshot, snapshot comparison, reproducibility audit, provenance graph, project delivery, package intake, research search, runbook, timeline, file manifest, file verification, workbench completion, workbench export, notebook export, evidence bundle export, and AgentCompare. It then runs plain uCore once, extracts state from the file-system image, runs the host LLM relay in template mode, and verifies the refreshed API, summary cards, workflow runner records from `rp_stage_state`, `rp_cache_index`, `rp_retry_plan`, `rp_run_events`, `rp_worker`, and `rp_execobs`, Studio records from `rp_studio`, project delivery review records from `rp_web_bundle`, Agent details, Agent decision-flow tables, evidence details, delivery package records from `rp_package`, `rp_nbexec`, and `rp_uresrun`, service operation records from `rp_bioop`, `rp_labresop`, `rp_pubop`, `rp_knowop`, and `rp_runop`, claim/provenance/protocol tables, compare metrics, LLM relay quality tables, plain-kernel signal tables, action log, workflow portability records, relay response records, and HTML pages.

The Run and Review pages also render report source maps that connect report sections to ordinary state files and source lines; these maps now prefer `report_source` records written by the plain uCore platform itself and resolve each `source_key` to the current evidence line in the named `rp_*` file. The Run, Workflow, Workbench, Project, Data, Compare, Review, Delivery, Artifacts, LLM, and Actions pages also render action-output link, detail, impact, and delta tables. These tables join `host-actions.jsonl` with the refreshed `rp_*` files, so a reader can see which Host action produced or changed `rp_input`, `rp_stage_state`, `rp_artifact_manifest`, `rp_llm_req`, `rp_review2`, `rp_package`, `rp_agentcmp`, and related files, including the matched state-file line that proves the visible result. The impact tables then point the action at report sections, artifact review paths, review gates, LLM packets, LLM quality rows, and package rows where those targets are available. The delta tables compare selected request fields with the observed uCore output fields and mark them as matched, missing, or different.

The artifact page now renders manifest records, the artifact dossier, derived sections, provenance records, artifact review path rows, artifact source maps, dossier checks, archive files, stage logs, review/LLM signals, host artifact actions, and operations source-file links from ordinary `rp_*` files, so generated artifacts are visible as concrete input-output, report, review, and verification records instead of a single status line. `rp_artifact_manifest` writes `artifact_review_path` records for raw input to report, quality metrics to delivery package, and failed-stage recovery to reviewer package; the reader resolves references such as `rp_artifact:rp_align_table` and `rp_run_events:4` to actual state lines, and `rp_consistency` verifies the artifact path by checking the stage state, run event, retry plan, review gate, and LLM-quality source records.

The final comparison step now verifies the review handoff path after all user-space state has been generated. It checks review dashboard sections, gates, decisions, handoffs, review-pack actions, delivery-to-operations/project/workbench bridge records, backend evidence handoff, and publishes `review_handoff_checks=13` in `rp_agentcmp`.

The review page also renders a review source map. It resolves review dashboard sections, gates, handoffs, evidence-pack rows, backend evidence rows, bridge rows, and operations/workbench/project summaries to the current `rp_*` evidence lines, so a reviewer can inspect the concrete ordinary-file state behind the handoff without opening each file manually.

The artifacts and review pages also render a delivery source map. It resolves package, notebook, and usable-run delivery references from `rp_package`, `rp_nbexec`, and `rp_uresrun` to current source lines, so package readiness can be checked against the files that actually produced the delivery records.

The same final comparison step also verifies the LLM delivery path. It checks the ordinary-file queue, packets, matched responses, response file, quality record, packet guard, host-request manifest, package delivery entry, review dashboard LLM section, and workbench citation, then publishes `llm_delivery_checks=16` in `rp_agentcmp`.

It also verifies the workflow portability delivery path. It checks import count, adapter count, migration steps, rehearsal cases, blocking items, package export fields, final migration decision, and Web bundle linkage, then publishes `workflow_portability_checks=14` in `rp_agentcmp`.

The comparison step also checks that workflow portability and backend execution name the same execution plan, backend scenario, and compare profile. It verifies plain-uCore passed cases, planned AgentOS cases, and study records, then publishes `portability_backend_checks=12` in `rp_agentcmp`.

The backend scenario now also writes compact case-runner evidence. It records four cases, the ordinary files checked by the plain-uCore cases, attempt counts, retry reasons, four source/requirement/observation/action/review rows, four cost/replacement/risk rows, the two planned AgentOS cases, and study metrics for plain file scans and future kernel-assisted execution. It also links the same backend evidence into `rp_runner`, `rp_report_text`, and the review handoff state, then publishes `backend_runner_checks=12`, `backend_runner_detail_checks=24`, `runner_detail_rows=4`, `backend_runner_report_checks=20`, `runner_report_rows=4`, and `backend_report_links=2` in `rp_agentcmp`. The Run page derives case narratives from `rp_backend_exec`, so a reviewer can see the plain-uCore cost and planned AgentOS replacement without switching to the comparison page.

The Run and Workflow pages render `Workflow Execution View`, `Workflow Control View`, and `Workflow Evidence Links` from existing workflow state files. These tables join `rp_stage_state`, `rp_cache_index`, `rp_retry_plan`, `rp_run_events`, `rp_worker`, `rp_execobs`, `rp_artifact`, `rp_artifact_manifest`, `rp_stage_log`, `rp_report_text`, `rp_review_dashboard`, and `rp_package` into readable stage, worker, cache, retry, observer, artifact, report, review, and delivery rows, so the failed align-stage retry and the host-triggered workflow run can be reviewed without manually opening every state file.

The Workbench page renders `Research Workbench`, `Workbench Task State`, `Workbench Writing Outputs`, `Workbench File Package`, and `Workbench Review Board` from existing `rp_runner`, `rp_report_text`, `rp_revision`, `rp_package`, `rp_review_pack`, `rp_nbexec`, and `rp_uresrun` files. It exposes the workbench id, title, question, task state, answer audit, notes, manuscript export, citation/manuscript formats, task-board rows, manifest, verification counts, notebook export, handoff package, and bundle output in one page.

The Studio page renders `Research Studio`, Studio sessions, material summaries, links, and Studio action traces from `rp_studio`, `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle`. The host action `/actions/research/studio-launch` stores a compact seed inside uCore with title, goal, and workbench id, while the full host run package keeps the richer payload.

The Data page renders `Data Pipeline`, `Ingested Input Files`, `Dataset Snapshots`, `Data Preview Records`, `Derived Data Preview`, `Data Quality State`, `Data Transform Records`, `Derived Data Products`, `Dataset Collection`, and `Data Manifest Verification` from existing `rp_api_data`, `rp_ingest_files`, `rp_dataset_snapshot`, `rp_data_preview`, `rp_data_quality`, `rp_data_transform`, and `rp_dataset_collection` files. It exposes the concrete input files, row counts, quality decisions, transform inputs/outputs, collection items, manifest name, hash-record count, verified count, missing count, and data-related Host actions without opening each state file manually.

The Project page renders `Project Space`, `Project Handoff`, `Project Evidence Package`, `Project Package Records`, `Project Quality And Repair`, `Project Search And Notes`, and `Project Source Files` from existing `rp_runner`, `rp_package`, `rp_review_pack`, `rp_actionio`, and `rp_web_bundle` files. It exposes the project id, project-space status, note, action item, cited answer, repair action, research search status, quality gate, quality repair result, and source-file links for the project handoff.

The Project Review page renders `Project Delivery Review`, release-gate records, project snapshots, snapshot comparison, reproducibility audit, provenance graph, delivery report, package intake, package index, and related action trace/detail/delta tables from `rp_web_bundle`, `rp_actionio`, `rp_package`, and `rp_review_pack`. It exposes project readiness as concrete ordinary-file records instead of relying on a separate host-only project checklist.

The action runner turns captured host actions into ordinary uCore state files for the next run:

```bash
python host_tools/plain_ucore_action_runner.py --actions runtime/plain_ucore_reader/host-actions.jsonl --state-dir path/to/rp-state --run-dir runtime/plain_ucore_actions
python host_tools/plain_ucore_action_runner.py --actions runtime/plain_ucore_reader/host-actions.jsonl --state-dir path/to/rp-state --run-dir runtime/plain_ucore_actions --run-ucore --repo-dir .
python host_tools/test_plain_ucore_action_runner.py
```

The runner writes `state-next/rp_host_action_queue`, `state-next/rp_host_action_plan`, `state-next/rp_host_action_inbox`, `actions.json`, and `runner-summary.json`. With `--run-ucore`, it keeps the full inbox text in the host run package, writes a compact `kind` plus payload seed to `state-next/rp_host_action_seed`, pads `rp_*` image inputs to the teaching file-system block size, copies the compact seed file into `user/target/bin/` before building `nfs/fs.img`, builds the ordinary user programs, runs `rp_seed_orch`, writes `ucore-run.log`, extracts text `rp_*` state files from `nfs/fs-copy.img`, and records QEMU result markers in `state-next/rp_host_run_result`. The queue and plan files stay in the host run package because the teaching uCore file system keeps limited root-directory capacity. The compact seed file is small enough to fit in the image and avoids passing a large action batch through `exec` arguments. Seeded research, Studio, dataset, library, template, workspace, evidence, comparison, host-workflow, artifact operation, Host LLM Relay, human-review, revision, workbench, operations, project-space, project-review, research-search, notebook-export, and bundle-export actions are reflected in the ordinary state files used by the platform: `rp_input`, data pipeline files, `rp_studio`, `rp_runner`, `rp_review2`, `rp_revision`, `rp_package`, `rp_report_text`, `rp_artifact`, `rp_stage_log`, `rp_chart_data`, `rp_artifact_manifest`, `rp_nbexec`, `rp_uresrun`, `rp_actionio`, `rp_web_bundle`, `rp_agentcmp`, and `rp_llm_*` relay files. Research-run payload fields now change the input, data, report, API, and usable-run summaries: title, question, provider, dataset row count, reference count, workspace file count, CSV filename, and reference filename are carried into `rp_input`, data pipeline files, `rp_report_text`, `rp_api_home`, `rp_api_run`, and `rp_uresrun`. Studio launch payload fields update `rp_studio`, `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle` with session title, goal, workbench id, links, and package-ready markers; the compact seed keeps only title, goal, and workbench id for the ordinary uCore run while the host package keeps the full request. Project review payload fields update `rp_web_bundle` and `rp_actionio` with release-gate decision, snapshot status, reproducibility decision, provenance graph export, delivery bundle, and package-intake label. Host workflow actions update `rp_stage_dag`, `rp_stage_state`, `rp_run_events`, `rp_artifact_manifest`, `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle` with workflow id, run id, engine, stage count, DAG text, export format, export bundle, failed stage, retry stage, retry reason, cache policy, cache-hit stage, worker slots, queue depth, and observer event count. Host workflow stage/cache/retry/artifact/report actions additionally update `rp_stage_state`, `rp_run_events`, `rp_cache_index`, `rp_retry_plan`, `rp_artifact_manifest`, `rp_report_text`, package records, and action summaries with concrete attempt, cache key, retry decision, artifact hash, artifact size, report name, report format, and report section count. Artifact input/derive/log/chart/package actions update `rp_artifact`, `rp_stage_log`, `rp_chart_data`, `rp_artifact_manifest`, `rp_package`, `rp_actionio`, and `rp_web_bundle` with input filename, artifact kind, hash, byte count, source, derived output, operation, stage log, chart data, and package manifest fields. Host LLM Relay actions update `rp_llm_req`, `rp_llmq`, `rp_llm_resp`, `rp_llm_packets`, `rp_llm_hostreq`, `rp_llm_fallback`, `rp_api_runtime`, `rp_actionio`, and `rp_web_bundle` with request id, route, provider, response id, response summary, and fallback case. Writing-oriented workbench actions update `rp_revision`; handoff, manifest, brief, dossier, graph, citation, manuscript, task board, runbook, timeline, readiness, completion, and workbench export actions update `rp_package`; file-manifest and file-verify actions also update `rp_ingest_files`, `rp_data_quality`, `rp_dataset_collection`, `rp_artifact_manifest`, `rp_api_artifacts`, and `rp_api_data` with manifest file count, hash-record count, verified file count, and missing file count. The user programs read action-specific payload fields from the seed file, so `run_id`, workflow id, workflow engine, workflow DAG, workflow export bundle, workflow stage attempt, cache decision, retry decision, artifact record, artifact input, artifact output, artifact log, artifact chart, artifact package, report record, Studio title, Studio goal, workbench id, project id, release-gate decision, snapshot id, provenance graph filename, project delivery bundle, package-intake label, LLM request id, LLM route, LLM provider, LLM response id, LLM summary, LLM fallback case, reviewer, review decision, revision targets, revision task id, workbench title, literature query, workbench question, evidence-search query, workbench task/status, workbench note kind/title/body, notes filter, handoff scope, brief format, dossier format, graph format, citation format, manuscript format, audit scope, revision area, revision task/status, board filter, board-row id/status, runbook format, timeline format, file-manifest name, file counts, hash counts, verification counts, notebook format, bundle name, and comparison profile change the resulting state files instead of being reduced to a fixed action type. The same payload values are also propagated into `rp_report_text`, `rp_artifact_manifest`, `rp_nbexec`, `rp_uresrun`, `rp_api_compare`, `rp_api_runtime`, and the package download summary, so the final reader pages show changed report, evidence, notebook, run, Studio, project review, package, workflow, LLM relay, and comparison content.

The host relay tool consumes the ordinary `rp_llmq`, `rp_llm_req`, and `rp_llm_packets` files and writes refreshed `rp_llm_resp`, `rp_llm_hostreq`, `rp_llm_packets`, `rp_llmlog`, `rp_actionio`, `rp_web_bundle`, and `rp_api_runtime` records. It also writes the selected relay response into `rp_report_text`, `rp_runner`, `rp_revision`, `rp_package`, `rp_api_run`, `rp_api_evidence`, `rp_agent_run`, and `rp_review_dashboard`, then materializes `rp_review_pack` for the host reader from the dashboard, delivery state, and backend case records. The evidence package links delivery manifest, operations report, project-space action items, workbench handoff, backend evidence, backend action/review rows, operations/workbench/project summaries, and the relevant Host action trace into one reviewer-facing handoff view. The LLM result is visible in the research report, workbench answer, writing state, delivery package, review dashboard, reviewer evidence package, and rendered API files. The same run now appends host-side quality records into `rp_llmeval`, packet guard records into `rp_llm_guard`, replay records into `rp_relay`, and route records into `rp_prompt`; `llm.html` renders those files as a request-to-response flow table plus request, response, quality, guard, replay, action trace, action output, action impact, and action delta tables. It writes prompt hashes and response summaries, not host secret values. This keeps the plain uCore branch compatible with template-mode demonstrations and with a host-managed cloud provider.

The reader is a host-side viewer and action-capture service for plain uCore output. It does not modify `os/`, `nfs/`, or `scripts`, and it does not add Agent-OS kernel features.

## Next Work

The current native programs prove that the plain uCore kernel can boot and run a research-platform-shaped catalog process plus a multi-process workflow with ordinary file-backed object storage, task messages, role acknowledgements, tool logs, scheduling records, task records, workflow import/export description, workflow portability records for Snakemake, Galaxy, DVC, CWL, and Nextflow style workflows, adapter summaries, migration plans, rehearsal cases, resource budget, project policy, risk register, CAPA records, failure classification, retry records, query, task ranking, run view, run configuration, workflow invocation, completion actions, execution plan, worker health, execution timeline, observer evidence, concrete input files, three custom research requests and small CSV-style rows carried in `rp_input`, request-form, uploaded-material, reusable-source, local workspace-import, generated workspace-template, workspace-run sections, four dynamic submission records, file-backed validation state, and host UI feed hints inside `rp_input`, data ingestion records, dataset snapshots, data previews, quality results, transform records, dataset collections, stage DAG, stage logs, workflow runner execution state, stage command/output records, dependency checks, normalized FASTQ, alignment table, metrics JSON, gene-count CSV, and archive manifest sections inside `rp_artifact`, three derived custom run summaries and one queued dynamic API run in `rp_runner`, workbench readiness, cited-answer, handoff brief, continuation runbook, timeline, and file-manifest sections inside `rp_runner`, human review records, revision-task records, one revised run record, review thread records, review comment records, action item records, bibliography and citation-plan records, literature search, candidate screening, evidence extraction, registered evidence protocol, PRISMA-style flow record, evidence synthesis record, content-keyed cache records, retry plan with failure reason and rerun input/output fields, run events with retry decisions and report/evidence references, artifact manifest with support links, recovered artifacts, report text, chart data, Agent collaboration evidence, file-backed Host LLM Relay protocol with queue validation, route decisions, packet schema checks, guard checks, fallback decisions, matched request, packet, and response records, host UI data exports with navigation, timeline, artifact preview, Agent decision, evidence preview, dynamic-input queue references, live-update feed records, host reader contract records, comparison metric rows, static review site inventory with 42 routed pages, Host Web/API payload contract, file-backed POST action request and response records, twelve service action records with request validation, precondition checks, idempotency keys, side-effect records, and download-manifest state, package export indexes for report, evidence, provenance, reusable source selection, reviewer delivery, eight delivery file rows, three delivery checks, delivery manifest JSON/Markdown names, evidence bundle zip name, evidence bundle contents, raw artifacts, and review page sections, user-space test suite, object naming checks, surface reachability checks, status semantics checks, reference checks, evidence trace checks, run-state explanation checks, lifecycle-order checks, delivery consistency checks, AgentOS readiness checks, lineage, site export, data dictionary, data profile records, figure records, calculation replay, samples, quality, protocol, SOP, experiment, trial records, lab operations, personnel training, sample registry, ethics review, data access review, instrument registry, inventory, procurement, resource schedule, result review, publication plan, peer review response, FAIR package, literature review, citation graph, semantic index, knowledge answers, runtime environment locks, notebook replay, ELN record, worker pool, telemetry, health summary, evidence, claim records, provenance paths, knowledge, semantic summary, systematic review summary, multi-round review, report revision package, LLM packet queue, host relay request/response handoff, prompt routing, LLM audit log, LLM evaluation, privacy review, compliance record, release delta review, FAIR data release, data product summary, data product versioning, reproduction package, release, dossier, review governance, submission package, backend scenario evidence, cross-file consistency checks, AgentCompare metrics, and comparison services. Further migration work should move more behavior from embedded tables into active user-space services:

- Persistent platform state files in the uCore root file system.
- Expand the planner, retriever, analyst, reviewer, governance, writer, repair, auditor, object query, lineage, export, scheduling, task ranking, workflow import/export, resource budget, project policy, risk register, CAPA records, release delta review, run configuration, workflow invocation, workflow completion, execution observer, backend scenario, failure classification, retry handling, run views, data dictionary, data profile records, figure records, calculation replay, sample, quality, protocol, SOP, experiment, trial records, lab operations, telemetry, health summaries, evidence, claim records, provenance paths, knowledge, FAIR data release, data product versioning, reproduction, review governance, LLM packet queue, host relay description, prompt routing, LLM evaluation, privacy, compliance, release, dossier, submission, and AgentCompare programs beyond the current fixed records.
- A richer user-space coordination protocol using only unchanged uCore syscalls.
- More host relay provider adapters and stricter response scoring over the ordinary LLM request and response files.
- More executable checks for workflow portability, workflow runner execution, Host Web/API payload export, file-backed action handling, release review, and AgentCompare comparison.

The later Agent-OS enhanced kernel version should use the same object names, role names, and output contracts so both kernels can run the same demonstration scenario.

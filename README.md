# project61-agentOS-happylegend: plain uCore research platform branch

This branch is the plain-kernel baseline for the research Agent platform.

The kernel source under `os/`, the file-system builder under `nfs/`, and the boot/init helper under `scripts/` are restored from the upstream uCore 2025S source. The research platform work in this branch is placed in ordinary user space.

## Purpose

The branch answers one specific question: how far the research Agent platform can run on an unchanged uCore kernel before the later Agent-OS enhanced kernel is used.

It is not the Agent-OS kernel-enhanced version. There are no Agent syscalls, Agent Context pages, kernel file metadata indexes, or kernel Agent event queues in `os/`.

## Current User Program

The first native uCore entries are:

```text
user/src/rp_plain.c
user/src/rp_orch.c
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
user/src/rp_notebook_export.c
user/src/rp_metrics.c
user/src/rp_backend.c
user/src/rp_consistency.c
user/src/rp_ui_export.c
user/src/rp_web_export.c
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

`rp_orch` runs forty-two platform programs as separate uCore user processes:

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
- file-backed human review and revision-task actions,
- test suite,
- plain-kernel comparison.

It uses ordinary `fork`, `exec`, and `waitpid`. This provides a plain-kernel baseline for the later Agent-OS multi-Agent version.

The role programs also exchange state through ordinary root-file-system files:

- `rp_plan`
- `rp_mail`
- `rp_ack`
- `rp_tool`
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
- `rp_backend_export`
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
- `rp_tests`
- `rp_agentcmp`

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
rp_orch: start programs=42
rp_catalog: objects=500 services=120 features=28 status=ready
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
rp_query: workflow=34 agent=26 evidence=10 ranked=21 selected=10 status=ready
rp_evidence: claims=8 links=5 claim_records=8 paths=3 status=ready
rp_llm_bridge: requests=3 responses=3 routes=4 eval=7 relay=ready status=ready
rp_llm_relay: packets=3 routes=4 guard=ready fallback=1 status=ready
rp_privacy: checked=13 packets=3 redactions=0 compliance=accepted status=ready
rp_runconf: profiles=2 validations=2 drift=1 status=ready
rp_execobs: timeline=9 workers=4 controls=8 observer=ready status=ready
rp_invoke: steps=10 attempts=12 outputs=6 status=recovered
rp_complete: hooks=4 events=1 actions=4 status=ready
rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 custom_requests=3 status=ready
rp_data_pipeline: files=2 snapshots=2 previews=2 quality=passed transforms=2 status=ready
rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 custom_runs=3 status=ready
rp_workbench: tasks=9 workspace_files=4 runs=4 exports=7 status=ready
rp_agent_collab: agents=7 messages=21 decisions=8 handoffs=6 status=ready
rp_package: artifacts=52 checks=75 fair=passed repro=ready status=ready
rp_delta: items=20 reviews=1 decision=accepted status=ready
rp_release: decision=release checks=17 status=ready
rp_dossier: sections=36 review_board=accepted submit=ready status=ready
rp_service_surface: bio=ready lab_resources=ready publication=ready knowledge=ready runtime=ready status=ready
rp_notebook_export: notebooks=2 cells=8 downloads=4 status=ready
rp_backend: cases=4 executable=2 exports=1 status=ready
rp_consistency: checks=113 tasks=21 llm=3 relay=5 workflow=5 portability=6 coherence=9 data=6 services=25 backend=4 artifacts=4 agents=7 dynamic=4 status=ready
rp_metrics: telemetry_spans=8 acks=35 tools=115 services=25 delta_items=20 dynamic=4 status=ready
rp_ui_export: pages=5 run=RUN-042 custom_runs=3 compare=ready status=ready
rp_web_export: routes=22 api_payloads=14 actions=8 bundle=ready status=ready
rp_test_suite: tests=693 catalog=passed data=passed services=passed actions=passed active_actions=passed custom=passed dynamic=passed workbench=passed notebook=passed portability=passed coherence=passed static_site=passed artifacts=passed workflow=passed collaboration=passed ui=passed web=passed llm=passed compare=passed status=passed
rp_compare_plain: plain_kernel=passed objects=500 programs=42 state_files=168 acks=42 tools=144 dynamic=4 reader=1 status=ready
rp_orch: programs_ok=42 programs_total=42
rp_orch: state_ok=1
rp_orch: passed
```

The current upstream uCore kernel prints `all app are over!` after the init user program exits. In this branch that message means the plain user program finished and the kernel reached its existing no-more-apps path.

## Kernel Source Check

Use these checks to verify that this branch keeps the kernel source unchanged:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/nfs ./nfs
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
```

No output means the directories match.

## Host Reader

The `host_tools/plain_ucore_reader.py` utility renders ordinary `rp_*` state files into host-viewable HTML pages and API JSON files. It consumes the `host_plain_ucore_v2` reader contract written by `rp_web_bundle`. The generated pages use a sidebar, page-level summary cards, research-output summaries, evidence-package summaries, comparison summaries, state tables, host-action history, and a batch-action editor for running the same research flow through plain uCore.

```bash
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader --serve --port 8767
python host_tools/plain_ucore_reader.py --state-dir path/to/rp-state --out-dir runtime/plain_ucore_reader --serve --port 8767 --auto-run-ucore --repo-dir . --run-root runtime/plain_ucore_auto_runs
python host_tools/plain_ucore_fs_extract.py --image nfs/fs-copy.img --out-dir runtime/plain_ucore_extracted --repo-dir .
python host_tools/test_plain_ucore_fs_extract.py
python host_tools/test_plain_ucore_reader.py
python host_tools/test_plain_ucore_reader_e2e.py
```

With `--serve`, the reader exposes `/api/reader-summary`, `/api/contract`, `/api/state/{name}`, `/api/live`, static pages, `/actions/...` POST capture, and `/actions/batch` for a group of related research actions. Action requests are written to `host-actions.jsonl`; use `--write-state-actions` only when the host should also append an action inbox record beside the `rp_*` state files. With `--auto-run-ucore`, a single POST action or one `/actions/batch` request invokes the action runner, builds and runs `rp_orch`, writes `rp_host_run_result`, extracts the generated state files, publishes the next state package back to the served state directory, and refreshes the generated pages. The end-to-end reader test starts the HTTP handler, sends a batch covering research run, human review, revision task, revised run, notebook export, evidence bundle export, and AgentCompare, runs plain uCore once, extracts state from the file-system image, and verifies the refreshed API, summary cards, action log, and HTML pages.

The action runner turns captured host actions into ordinary uCore state files for the next run:

```bash
python host_tools/plain_ucore_action_runner.py --actions runtime/plain_ucore_reader/host-actions.jsonl --state-dir path/to/rp-state --run-dir runtime/plain_ucore_actions
python host_tools/plain_ucore_action_runner.py --actions runtime/plain_ucore_reader/host-actions.jsonl --state-dir path/to/rp-state --run-dir runtime/plain_ucore_actions --run-ucore --repo-dir .
python host_tools/test_plain_ucore_action_runner.py
```

The runner writes `state-next/rp_host_action_queue`, `state-next/rp_host_action_plan`, `state-next/rp_host_action_inbox`, `actions.json`, and `runner-summary.json`. With `--run-ucore`, it writes the inbox text into a generated user-build header, builds the ordinary user programs, runs `rp_orch`, writes `ucore-run.log`, extracts text `rp_*` state files from `nfs/fs-copy.img`, and records QEMU result markers in `state-next/rp_host_run_result`. The queue and plan files stay in the host run package because the upstream uCore teaching file system has tight inode capacity. Seeded research, comparison, human-review, revision, workbench, notebook-export, and bundle-export actions are reflected in the ordinary state files used by the platform: `rp_input`, `rp_runner`, `rp_review2`, `rp_revision`, `rp_package`, `rp_nbexec`, `rp_actionio`, and `rp_agentcmp`. The user programs read action-specific payload fields from the generated seed, so `run_id`, reviewer, review decision, revision targets, revision task id, notebook format, bundle name, and comparison profile change the resulting state files instead of being reduced to a fixed action type. The same payload values are also propagated into `rp_report_text`, `rp_artifact_manifest`, `rp_api_compare`, and the package download summary, so the final reader pages show changed report, evidence, package, and comparison content.

The reader is a host-side viewer and action-capture service for plain uCore output. It does not modify `os/`, `nfs/`, or `scripts`, and it does not add Agent-OS kernel features.

## Next Work

The current native programs prove that the plain uCore kernel can boot and run a research-platform-shaped catalog process plus a multi-process workflow with ordinary file-backed object storage, task messages, role acknowledgements, tool logs, scheduling records, task records, workflow import/export description, workflow portability records for Snakemake, Galaxy, DVC, CWL, and Nextflow style workflows, adapter summaries, migration plans, rehearsal cases, resource budget, project policy, risk register, CAPA records, failure classification, retry records, query, task ranking, run view, run configuration, workflow invocation, completion actions, execution plan, worker health, execution timeline, observer evidence, concrete input files, three custom research requests and small CSV-style rows carried in `rp_input`, request-form, uploaded-material, reusable-source, local workspace-import, generated workspace-template, workspace-run sections, four dynamic submission records, file-backed validation state, and host UI feed hints inside `rp_input`, data ingestion records, dataset snapshots, data previews, quality results, transform records, dataset collections, stage DAG, stage logs, workflow runner execution state, stage command/output records, dependency checks, normalized FASTQ, alignment table, metrics JSON, gene-count CSV, and archive manifest sections inside `rp_artifact`, three derived custom run summaries and one queued dynamic API run in `rp_runner`, workbench readiness, cited-answer, handoff brief, continuation runbook, timeline, and file-manifest sections inside `rp_runner`, human review records, revision-task records, one revised run record, review thread records, review comment records, action item records, bibliography and citation-plan records, literature search, candidate screening, evidence extraction, registered evidence protocol, PRISMA-style flow record, evidence synthesis record, content-keyed cache records, retry plan with failure reason and rerun input/output fields, run events with retry decisions and report/evidence references, artifact manifest with support links, recovered artifacts, report text, chart data, Agent collaboration evidence, file-backed Host LLM Relay protocol with queue validation, route decisions, packet schema checks, guard checks, fallback decisions, matched request, packet, and response records, host UI data exports with navigation, timeline, artifact preview, Agent decision, evidence preview, dynamic-input queue references, live-update feed records, host reader contract records, comparison metric rows, static review site inventory with 42 routed pages, Host Web/API payload contract, file-backed POST action request and response records, twelve service action records with request validation, precondition checks, idempotency keys, side-effect records, and download-manifest state, package export indexes for report, evidence, provenance, reusable source selection, reviewer delivery, eight delivery file rows, three delivery checks, delivery manifest JSON/Markdown names, evidence bundle zip name, evidence bundle contents, raw artifacts, and review page sections, user-space test suite, object naming checks, surface reachability checks, status semantics checks, reference checks, evidence trace checks, run-state explanation checks, lifecycle-order checks, delivery consistency checks, AgentOS readiness checks, lineage, site export, data dictionary, data profile records, figure records, calculation replay, samples, quality, protocol, SOP, experiment, trial records, lab operations, personnel training, sample registry, ethics review, data access review, instrument registry, inventory, procurement, resource schedule, result review, publication plan, peer review response, FAIR package, literature review, citation graph, semantic index, knowledge answers, runtime environment locks, notebook replay, ELN record, worker pool, telemetry, health summary, evidence, claim records, provenance paths, knowledge, semantic summary, systematic review summary, multi-round review, report revision package, LLM packet queue, host relay request/response handoff, prompt routing, LLM audit log, LLM evaluation, privacy review, compliance record, release delta review, FAIR data release, data product summary, data product versioning, reproduction package, release, dossier, review governance, submission package, backend scenario evidence, cross-file consistency checks, AgentCompare metrics, and comparison services. Further migration work should move more behavior from embedded tables into active user-space services:

- Persistent platform state files in the uCore root file system.
- Expand the planner, retriever, analyst, reviewer, governance, writer, repair, auditor, object query, lineage, export, scheduling, task ranking, workflow import/export, resource budget, project policy, risk register, CAPA records, release delta review, run configuration, workflow invocation, workflow completion, execution observer, backend scenario, failure classification, retry handling, run views, data dictionary, data profile records, figure records, calculation replay, sample, quality, protocol, SOP, experiment, trial records, lab operations, telemetry, health summaries, evidence, claim records, provenance paths, knowledge, FAIR data release, data product versioning, reproduction, review governance, LLM packet queue, host relay description, prompt routing, LLM evaluation, privacy, compliance, release, dossier, submission, and AgentCompare programs beyond the current fixed records.
- A richer user-space coordination protocol using only unchanged uCore syscalls.
- A host LLM relay that consumes the existing ordinary request files and writes ordinary response files.
- More executable checks for workflow portability, workflow runner execution, Host Web/API payload export, file-backed action handling, release review, and AgentCompare comparison.

The later Agent-OS enhanced kernel version should use the same object names, role names, and output contracts so both kernels can run the same demonstration scenario.

# Plain uCore Platform Verification

## Build

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch
```

Result:

```text
Build kernel done
```

The user image contains:

```text
usershell
rp_plain
```

The role-process image contains:

```text
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
rp_delta
rp_release
rp_dossier
rp_service_surface
rp_notebook_export
rp_backend
rp_consistency
rp_metrics
rp_ui_export
rp_web_export
rp_test_suite
rp_compare_plain
```

## Run Catalog Program

```bash
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
```

Observed key output:

```text
rp_plain summary
objects=500 object_total=102790 services=120 features=28 feature_units=299 checks=13 references=6 mappings=6
search workflow=34 agent=26 evidence=10 provenance=12 llm=11
reference platforms: Galaxy AiiDA DVC MLflow Nextflow Snakemake
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
rp_plain: passed
```

The upstream kernel prints:

```text
all app are over!
```

after the init program exits. The platform result is taken from the `passed` line before that kernel termination path.

## Run Role Orchestrator

```bash
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
```

Observed key output:

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
rp_consistency: checks=120 tasks=21 llm=3 relay=5 workflow=5 portability=6 coherence=9 data=6 services=25 backend=4 artifacts=7 agents=7 dynamic=4 status=ready
rp_metrics: telemetry_spans=8 acks=35 tools=115 services=25 delta_items=20 dynamic=4 status=ready
rp_ui_export: pages=5 run=RUN-042 custom_runs=3 compare=ready status=ready
rp_web_export: routes=62 api_payloads=14 actions=48 bundle=ready status=ready
rp_review_dashboard: sections=8 gates=6 review_pack=host-materialized status=ready
rp_compare_plain: plain_kernel=passed objects=500 programs=42 state_files=170 acks=44 tools=138 dynamic=4 reader=1 status=ready
rp_orch: programs_ok=42 programs_total=42
rp_orch: state_ok=1
rp_orch: passed
```

`rp_seed_orch` is exercised through the Host reader E2E path below. It uses the Host-action child-program set and reads the compact action seed from the ordinary `rp_host_action_seed` file inside the uCore image. The seeded image omits the standalone `rp_test_suite` executable to stay within the upstream teaching file-system capacity; `rp_compare_plain` publishes the current test count in `rp_agentcmp` before the final comparison checks.

## Kernel Source Check

From the repository root:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/nfs ./nfs
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
```

Expected result: no diff output.

## Host Reader Check

The fs extractor check builds a small synthetic uCore file-system image, extracts text `rp_*` files, restores a long state-file name, and skips a binary program file. The host reader can be tested without booting uCore because it builds a small temporary `rp_*` state set and verifies the `host_plain_ucore_v2` contract. The same check also starts the local HTTP handler, reads `/api/contract` and `/api/state/rp_api_home`, posts to `/actions/research/run`, verifies that the action record is written, exercises the auto-run hook with a fake runner, checks `rp_host_run_result`, and confirms `/api/live` reports the latest run.

```bash
python host_tools/test_plain_ucore_fs_extract.py
python host_tools/test_plain_ucore_reader.py
python host_tools/test_plain_ucore_reader_e2e.py
python host_tools/test_plain_ucore_action_runner.py
python host_tools/test_plain_ucore_llm_relay.py
```

Expected output:

```text
test_plain_ucore_fs_extract: passed
test_plain_ucore_reader: passed
test_plain_ucore_reader_e2e: passed
test_plain_ucore_action_runner: passed
test_plain_ucore_llm_relay: passed
```

The action runner check verifies that captured host actions become `state-next/rp_host_action_queue`, `state-next/rp_host_action_plan`, `state-next/rp_host_action_inbox`, `actions.json`, and `runner-summary.json`. It also checks `rp_host_run_result` generation and state publication. The optional runner path writes a compact `kind` plus payload seed into `state-next/rp_host_action_seed`, pads `rp_*` image inputs to the teaching file-system block size, copies the compact seed file into `user/target/bin/` before building the file-system image, and executes `CHAPTER=platform_seeded` with `INIT_PROC=rp_seed_orch` without changing the kernel source; the full action lines remain in the host run package. In seeded runs, `rp_web_export` prints `host_reader_actions=<n>`, `rp_compare_plain` prints `host_actions=<n> verified`, the fs extractor publishes the generated text state files, and the ordinary user programs write host-action effects into `rp_input`, data pipeline files, `rp_runner`, `rp_review2`, `rp_revision`, `rp_package`, `rp_report_text`, `rp_artifact_manifest`, `rp_nbexec`, `rp_uresrun`, `rp_actionio`, `rp_agentcmp`, and the LLM relay files. The LLM relay check reads `rp_llmq` and `rp_llm_req`, writes refreshed `rp_llm_resp`, `rp_llm_hostreq`, `rp_llm_packets`, `rp_llmlog`, `rp_actionio`, `rp_web_bundle`, and `rp_api_runtime`, appends the selected response into `rp_report_text`, `rp_runner`, `rp_revision`, `rp_package`, `rp_api_run`, `rp_api_evidence`, and `rp_agent_run`, writes quality records into `rp_llmeval`, packet guard records into `rp_llm_guard`, replay records into `rp_relay`, route records into `rp_prompt`, and verifies both offline template mode and missing host cloud configuration mode without writing secret values.

The end-to-end reader check uses the real action runner and QEMU path. It starts the local HTTP handler, sends `/actions/batch` with eighty-four actions: research run, dataset registration, library source registration, template registration, workspace inspection, workspace import, literature search, evidence review, evidence protocol, host workflow run/export/stage/cache/retry/artifact/report, artifact input/derive/log/chart/package operations, workflow portability run/import/plan/bind/rehearse/review/package, Host LLM Relay request, Host LLM Relay response, Host LLM Relay fallback, human review, revision task, revised run, workbench creation, workbench advance, workbench auto-advance, readiness, answer, answer audit, evidence search, task update, note, notes export, handoff package, brief, evidence dossier, evidence graph, citations, manuscript draft, manuscript audit, manuscript revision plan, manuscript revision task, task board, task-board row update, plan queue, delivery dashboard, quality gate and repair, operations report, operations advance, project space, research search, runbook, timeline, file manifest, file verification, workbench completion, workbench export, notebook export, evidence bundle export, and AgentCompare. The check waits for `rp_seed_orch`, extracts generated `rp_*` files from `nfs/fs-copy.img`, runs the host LLM relay in template mode, then verifies `/api/live`, `/api/state/rp_input`, `/api/state/rp_ingest_files`, `/api/state/rp_data_preview`, `/api/state/rp_data_quality`, `/api/state/rp_stage_dag`, `/api/state/rp_stage_state`, `/api/state/rp_run_events`, `/api/state/rp_cache_index`, `/api/state/rp_retry_plan`, `/api/state/rp_wfio`, `/api/state/rp_worker`, `/api/state/rp_execobs`, `/api/state/rp_artifact`, `/api/state/rp_stage_log`, `/api/state/rp_chart_data`, `/api/state/rp_runner`, `/api/state/rp_review2`, `/api/state/rp_review_dashboard`, `/api/state/rp_revision`, `/api/state/rp_package`, `/api/state/rp_report_text`, `/api/state/rp_artifact_manifest`, `/api/state/rp_nbexec`, `/api/state/rp_uresrun`, `/api/state/rp_actionio`, `/api/state/rp_agentcmp`, `/api/state/rp_api_run`, `/api/state/rp_api_compare`, `/api/state/rp_api_artifacts`, `/api/state/rp_api_data`, `/api/state/rp_llm_req`, `/api/state/rp_llm_resp`, `/api/state/rp_llm_packets`, `/api/state/rp_llmeval`, `/api/state/rp_llm_guard`, `/api/state/rp_relay`, `/api/state/rp_prompt`, `/api/state/rp_llm_hostreq`, `/api/state/rp_llm_fallback`, `/api/state/rp_api_runtime`, `/api/state/rp_host_run_result`, `run.html`, `agents.html`, `evidence.html`, `review.html`, `artifacts.html`, `compare.html`, `llm.html`, and `actions.html`. It checks action-specific payload effects, including research title, research question, provider, dataset row count, reference count, workspace file count, CSV filename, reference filename, workflow id, workflow run id, workflow engine, workflow DAG, workflow export format, workflow export bundle, workflow stage attempt, workflow cache decision, workflow retry decision, workflow artifact manifest, workflow report export, artifact input filename, artifact output filename, artifact log, artifact chart, artifact package manifest, artifact dossier, artifact provenance, dossier checks, workflow worker slots, workflow queue depth, workflow observer event count, workflow portability import id, source adapter, migration plan, target runtime, execution plan, compare profile, scenario id, rehearsal id, readiness decision, portability package, LLM request id, LLM route, LLM provider, LLM response id, LLM response summary, LLM fallback case, relay result status, relay response id, relay report summary, relay workbench answer, relay writer summary, relay delivery file, relay grounding record, relay quality status, relay guard status, relay replay record, relay prompt route, review dashboard sections, review dashboard gates, `secret_in_packet=0`, `secret_material=not_written`, reviewer `Wang`, revision targets, revision task id, workbench id, workbench title, literature query, workbench question, evidence-search query, workbench task/status, workbench note kind/title/body, notes filter, handoff scope, brief format, dossier format, graph format, citation format, manuscript format, manuscript audit scope, manuscript revision area, revision task/status, task-board filter, task-board row id/status, runbook format, timeline format, file-manifest name, file count, hash-record count, verified file count, missing file count, notebook format, evidence bundle name, workbench bundle name, comparison profile, project-space actions, research-search actions, operations actions, quality actions, and delivery actions. It also checks that those values reach `rp_input`, data pipeline files, workflow execution files, `rp_wfio`, `rp_revision`, `rp_package`, `rp_review_dashboard`, `rp_report_text`, `rp_artifact`, `rp_stage_log`, `rp_chart_data`, `rp_artifact_manifest`, `rp_nbexec`, `rp_uresrun`, `rp_api_run`, `rp_api_compare`, `rp_api_artifacts`, `rp_api_data`, `rp_api_runtime`, and package download records, and that `rp_actionio` plus `rp_web_bundle` name the workbench, workflow, artifact operation, workflow portability, and LLM relay output files. The HTML checks include the sidebar shell, run summary cards, research-output summary, Agent detail summary, Agent roster, decision flow, handoff flow, evidence-package summary, evidence detail summary, artifact manifest records, artifact dossier, derived artifact sections, artifact provenance records, dossier checks, archive files, stage logs, review/LLM signals, host artifact actions, review dashboard sections, review dashboard gates, claim records, provenance paths, evidence protocol files, comparison summary, compare metric summary, LLM relay quality tables, plain-kernel signals, backend runner case tables with content checks, attempts, and retry reasons, backend study metric tables, backend scenario handoff status, batch-action editor, and host-action history. A representative successful action run reports `embedded_action_records=84`, `run.status=ready`, `relay.status=ready`, and at least one hundred extracted state files.

The same check verifies `/api/state/rp_review_pack`, the `review_pack=ready` AgentCompare record, host relay quality appended to the reviewer evidence package, and the Review Evidence Pack plus Review Pack Bridges tables in `review.html`.

## Current Coverage

This first native uCore version validates:

- catalog scale,
- service inventory,
- feature groups,
- mature reference platform mappings,
- platform self-check status,
- catalog search,
- a complete research run simulation with one failed stage repaired in user space.
- multi-process execution with forty-two ordinary uCore user programs.
- ordinary file-backed state exchange across role programs.
- active cross-file consistency checks across tasks, LLM packets, workflow invocation, completion hooks, backend cases, and runner artifacts.
- host-side LLM relay execution over ordinary state files, with offline template responses, cloud-configuration detection, prompt hashes, response quality checks, packet guard records, replay records, and no secret values written back to uCore state.
- user-space test suite with 806 checks over catalog, data pipeline, bio services, lab resource services, publication services, knowledge services, runtime services, workflow, workflow portability records, workflow portability delivery checks, workflow portability to backend execution checks, backend runner case checks, backend runner detail checks, adapter summaries, migration plans, rehearsal cases, object naming, surface reachability, status semantics, references, evidence trace, run-state explanation, lifecycle order, delivery consistency, AgentOS readiness, static review site pages, artifact operations, derived FASTQ/alignment/metrics/count/archive sections, artifact provenance records, artifact path rebuild checks, dossier checks, workflow runner files, workflow runner detail fields, custom research fields, dynamic input queue fields, workbench task state, workbench readiness, cited answer, handoff brief, continuation runbook, timeline, file manifest, request-form sections, uploaded-material sections, reusable-source sections, workspace-import records, bibliography, citation plan, literature search, screening decisions, evidence extraction, evidence protocol, PRISMA-style flow, package export indexes, delivery file rows, delivery checks, delivery manifest file names, evidence bundle contents, review page, review dashboard, review handoff checks, LLM delivery checks, human review records, revision-task records, review thread records, review comment records, action item records, active action records, action validation records, action side-effect records, Host UI render data, Agent collaboration, UI export, Host Web/API export files, host reader contract fields, file-backed POST action records, live-update feed fields, LLM relay queue validation, route decisions, packet schema checks, guard checks, fallback decisions, request/packet/response matching, AgentCompare, and consistency records.
- object catalog, reusable object records, object query, lineage, site export, task messages, role acknowledgements, tool logs, scheduling records, task records, task ranking, workflow import/export description, resource budget, project policy, risk register, CAPA records, release delta review, run configuration, workflow invocation, workflow completion, execution plan, worker health, execution timeline, observer evidence, concrete input files, three custom research requests, nine small CSV-style rows, request-form, uploaded-material, reusable-source, workspace-import, generated workspace-template, workspace-run sections, four dynamic submission records, file-backed validation state, and host UI feed hints, three derived custom run summaries and one queued API run, human review records, revision-task records, one revised run record, review thread records, review comment records, action item records, bibliography and citation-plan records, literature search, candidate screening, evidence extraction, evidence protocol, PRISMA-style flow, evidence synthesis, input-file scan records, normalized FASTQ, alignment table, metrics JSON, gene-count CSV, archive manifest sections, artifact dossier links, and artifact provenance records inside `rp_artifact`, dataset snapshots, data previews, quality results, transform records, dataset collections, stage DAG, stage logs, workflow runner execution state, stage command/output records, dependency checks, dossier checks, content-keyed cache records, retry plan with failure reason and rerun input/output fields, run events with retry decisions and report/evidence references, artifact manifest with support links, recovered artifacts, report text, chart data, package export indexes for report, evidence, provenance, reusable source selection, reviewer delivery, eight delivery file rows, three delivery checks, delivery manifest JSON/Markdown names, evidence bundle zip name, evidence bundle contents, raw artifacts, and review page sections, UI navigation, timeline rows, artifact previews, Agent decision rows, evidence previews, dynamic-input queue references, live-update feed records, host reader contract records, comparison metric rows, Host Web/API route and payload files, file-backed action request and response files, failure classification, retry records, run views, data dictionary, data profile records, figure records, calculation replay, samples, quality, protocol, SOP, experiment, trial records, lab operations, training, sample registry, ethics review, data access review, cohort view, instrument registry, inventory, procurement, resource scheduling, result review, publication plan, peer review response, FAIR package, literature review, citation graph, semantic index, knowledge answers, runtime environment records, notebook replay records, ELN records, worker pool records, telemetry, health summaries, evidence, claim records, provenance paths, knowledge, multi-round review, report revision package, LLM packet queue, host relay request/response handoff, prompt routing, LLM audit log, LLM evaluation, privacy, compliance record, FAIR data release, data product versioning, reproduction package, package, release, dossier, review governance, submission package, backend scenario evidence, AgentCompare metrics, and plain-kernel comparison files.

It does not use Agent-OS kernel features. That is intentional for this plain-kernel baseline.

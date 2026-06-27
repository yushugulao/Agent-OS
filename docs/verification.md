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
rp_orch: start programs=69
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
rp_calculation: computers=1 codes=1 jobs=1 retrieved=3 parser=1 exports=1 checks=84 errors=0 status=ready
rp_realtask: dataset=palmer-penguins rows=344 numeric=5 checks=96 answer_audit=pass bundle=ready status=ready
rp_analysisres: plans=1 runs=2 tables=2 statistics=2 figures=2 interpretations=2 checks=96 status=ready
rp_campaign: campaigns=1 trials=4 best=04 checks=108 result_review=accept_candidate status=ready
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
rp_web_export: routes=152 api_payloads=15 actions=123 bundle=ready status=ready
rp_review_dashboard: sections=8 gates=6 review_pack=host-materialized status=ready
rp_modelreg: models=1 versions=1 evaluations=1 deployments=1 serving=1 checks=96 status=ready
rp_sysreview: protocols=1 searches=1 screening=9 included=3 extractions=3 prisma=1 checks=104 status=ready
rp_expsched: schedules=1 tasks=3 bookings=4 conflicts=1 executions=2 checks=88 status=ready
rp_traincomp: requirements=4 records=4 competency=4 auth=3 gaps=1 open=0 checks=92 status=ready
rp_publication: targets=2 submissions=2 reviews=2 responses=2 items=4 checks=48 status=ready
rp_runbooks: templates=1 steps=7 incidents=1 executions=1 exports=1 status=ready
rp_projectrel: checks=18 release=ready reproducibility=passed intake=accepted status=ready
rp_studyproto: checks=20 protocols=2 launches=2 reproduction=ready status=ready
rp_stdesign: designs=1 power=underpowered randomization=balanced blinding=ok checks=120 stat_result=approved_with_sample_size_note status=ready
rp_opsboard: checks=18 pending=1 actions=4 plan_items=5 handoffs=3 status=ready
rp_reviewboard: checks=24 requests=1 votes=4 signoffs=4 assignments=4 decision=approved status=ready
rp_controlplane: checks=30 approvals=4 notifications=4 queue=4 plugins=3 permissions=5 status=ready
rp_integrityplane: checks=36 evidence=8 references=8 namespace=5 status_semantics=5 review_alignment=4 status=ready
rp_coherenceplane: checks=40 delivery=7 run_state=7 lifecycle=6 workflow_lint=5 tool_protocol=5 report_validation=5 status=ready
rp_mature: profiles=6 mappings=6 checks=72 errors=0 status=ready
rp_prov_view: timelines=4 subgraphs=3 packets=4 checks=64 errors=0 status=ready
rp_prov_query: specs=3 templates=1 executions=3 comparisons=1 packets=1 checks=72 errors=0 status=ready
rp_reldossier: sections=7 evidence=18 checks=112 decision=ready_for_review status=ready
rp_decsupport: options=3 criteria=5 scores=15 selected=agentos_ucore_hybrid checks=80 status=ready
rp_usable: templates=3 datasets=3 library=3 stages=9 queues=2 handoffs=3 checks=100 status=ready
rp_usableproject: scaffolds=3 launches=2 bundles=2 doctor=10 checks=120 status=ready
rp_compare_plain: plain_kernel=passed objects=500 programs=69 state_files=261 acks=69 tools=328 dynamic=4 products=18 assurance=24 research_ops=28 regulated=32 lab_governance=26 state_catalog=12 startup_doctor=14 model_registry=96 systematic_review=104 experiment_scheduling=88 training_compliance=92 runbook_service=16 project_delivery=18 study_protocol=20 statistical_design=120 opsboard=18 review_board=24 control_plane=30 integrity_plane=36 coherence_plane=40 publication=48 calculation=84 real_task=96 analysis_results=96 decision_support=80 usable_research=100 usable_project=120 campaign=108 release_dossier=112 mature=72 provenance=64 provenance_query=72 knowledge_index=22 llm_transcripts=3 workbench_delivery=15 portfolio_scale=16 execution_scale=14 operations_scale=12 project_revision_incident=12 reserved_surfaces=21 root_state=10 agentos_reserved=21 reader=1 status=ready
rp_orch: programs_ok=69 programs_total=69
rp_orch: state_ok=1
rp_orch: passed
```

The `rp_api_catalog` state file is part of the same run. It records `host_api_routes=214`, `host_page_routes=15`, `api_group_count=14`, and `api_grouped_routes=214`, then maps representative Host API paths such as `/api/analysis-results`, `/api/experiment-scheduling`, `/api/workflow-runner`, `/api/usable-research-workbench-file-catalog`, and `/api/llm-proxy` to ordinary uCore state files. The route catalog also exposes Host page entries such as `/quickstart`, `/research-ops`, `/workbench-plan-queue`, and `/review-inbox`.

`rp_seed_orch` is exercised through the Host reader E2E path below. It uses the Host-action child-program set and reads the compact action seed from the ordinary `rp_host_action_seed` file inside the uCore image. The seeded image omits the standalone `rp_test_suite` executable to stay within the teaching file-system image capacity; `rp_compare_plain` publishes the current test count in `rp_agentcmp` before the final comparison checks.

## Kernel Source Check

From the repository root:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
grep 'FSSIZE 4096' nfs/fs.h
```

Expected result: the two `diff` commands print no output, and `grep` prints the plain target image capacity.

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

The action runner check verifies that captured host actions become `state-next/rp_host_action_queue`, `state-next/rp_host_action_plan`, `state-next/rp_host_action_inbox`, `actions.json`, and `runner-summary.json`. It also checks `rp_host_run_result` generation and state publication. The optional runner path writes a compact `kind` plus payload seed into `state-next/rp_host_action_seed`, pads `rp_*` image inputs to the teaching file-system block size, copies the compact seed file into `user/target/bin/` before building the file-system image, and executes `CHAPTER=platform_seeded` with `INIT_PROC=rp_seed_orch` without changing the kernel source; the full action lines remain in the host run package. In seeded runs, `rp_web_export` prints `host_reader_actions=<n>`, `rp_compare_plain` prints `host_actions=<n> verified`, the fs extractor publishes the generated text state files, and the ordinary user programs write host-action effects into `rp_input`, data pipeline files, `rp_runner`, `rp_studio`, `rp_review2`, `rp_revision`, `rp_package`, `rp_report_text`, `rp_artifact_manifest`, `rp_nbexec`, `rp_uresrun`, `rp_actionio`, `rp_web_bundle`, `rp_agentcmp`, and the LLM relay files. The LLM relay check reads `rp_llmq` and `rp_llm_req`, writes refreshed `rp_llm_resp`, `rp_llm_hostreq`, `rp_llm_packets`, `rp_llmlog`, `rp_actionio`, `rp_web_bundle`, and `rp_api_runtime`, appends the selected response into `rp_report_text`, `rp_runner`, `rp_revision`, `rp_package`, `rp_api_run`, `rp_api_evidence`, and `rp_agent_run`, writes quality records into `rp_llmeval`, packet guard records into `rp_llm_guard`, replay records into `rp_relay`, route records into `rp_prompt`, and verifies both offline template mode and missing host cloud configuration mode without writing secret values.

The end-to-end reader check uses the real action runner and QEMU path. It starts the local HTTP handler, sends `/actions/batch` with one hundred eighteen actions: research run, Studio launch, dataset registration, dataset preview, dataset visualization, dataset card, dataset answer, dataset run, dataset run comparison, dataset portfolio, library source registration, source portfolio, template registration, workspace inspection, workspace import, literature search, evidence review, evidence protocol, study-protocol creation/run/compliance/bundle/launch/rerun/comparison/reproduction-package/review/action-plan/action-execution, host workflow run/export/stage/cache/retry/artifact/report, artifact input/derive/log/chart/package operations, workflow portability run/import/plan/bind/rehearse/review/package, Host LLM Relay request, Host LLM Relay response, Host LLM Relay fallback, human review, revision task, revised run, workbench creation, workbench advance, workbench auto-advance, readiness, answer, answer audit, evidence search, task update, note, notes export, handoff package, brief, evidence dossier, evidence graph, citations, manuscript draft, manuscript audit, manuscript revision plan, manuscript revision task, task board, task-board row update, plan queue, delivery dashboard, quality gate and repair, operations report, operations advance, project scaffold, project launch, project action execution, project space, project review, project task-board row, project handoff audit, project release gate, project snapshot, snapshot comparison, reproducibility audit, provenance graph, project delivery, package intake, research search, runbook, timeline, file manifest, file verification, workbench completion, workbench export, notebook export, evidence bundle export, and AgentCompare. The check waits for `rp_seed_orch`, extracts generated `rp_*` files from `nfs/fs-copy.img`, runs the host LLM relay in template mode, then verifies the refreshed API, page set, project lifecycle state files, and Host action effects. It checks action-specific payload effects, including research title, workflow artifacts, LLM relay packet values, dataset operation records, study-protocol reproduction records, workbench fields, project scaffold template, project launch run id, project action execution result, project-space actions, project-review actions, research-search actions, operations actions, quality actions, and delivery actions. It also checks that those values reach the ordinary state files used by the reader, including `rp_usableds`, `rp_usablelib`, `rp_studyproto`, `rp_usableproj`, `rp_usablescaf`, `rp_usablelaunch`, `rp_usablepack`, `rp_web_bundle`, and `rp_actionio`. A representative successful action run reports `embedded_action_records=118`, `run.status=ready`, `relay.status=ready`, and at least one hundred extracted state files.

The same check verifies `/api/state/rp_review_pack`, the `review_pack=ready` AgentCompare record, host relay quality, backend evidence, backend action/review rows, operations/workbench/project summaries appended to the reviewer evidence package, the operations report narrative derived from `rp_runner`, `rp_package`, and `rp_review_pack`, the operations source-file table that links each narrative section back to concrete `rp_*` records, and the related Host action trace rendered from `host-actions.jsonl`; `run.html`, `workflow.html`, `workbench.html`, `studio.html`, `project.html`, `project-review.html`, `data.html`, `artifacts.html`, `delivery.html`, `compare.html`, `review.html`, and `llm.html` must include the Run Action Trace, Workflow Action Trace, Workbench Action Trace, Studio Action Trace, Data Action Trace, Project Action Trace, Compare Action Trace, Review Evidence Pack, Review Source Map, Delivery Source Map, Review Backend Evidence, Review Backend Actions, Review Pack Bridges, Review Operations Summary, Review Workbench Summary, Review Project Summary, Operations Report Narrative, Operations Source Files, Report Source Map, Artifact Source Map, Delivery Action Trace, Review Action Trace, LLM Relay Flow, and LLM Action Trace tables.

## Current Coverage

This first native uCore version validates:

- catalog scale,
- service inventory,
- feature groups,
- mature reference platform mappings,
- platform self-check status,
- catalog search,
- a complete research run simulation with one failed stage repaired in user space.
- multi-process execution with sixty-two ordinary uCore user programs.
- ordinary file-backed state exchange across role programs.
- active cross-file consistency checks across tasks, LLM packets, workflow invocation, completion hooks, backend cases, and runner artifacts.
- host-side LLM relay execution over ordinary state files, with offline template responses, cloud-configuration detection, prompt hashes, response quality checks, packet guard records, replay records, and no secret values written back to uCore state.
- user-space test suite with 2800 checks over catalog records, service records, Host Reader export files, workflow runner files, artifact files, delivery files, review files, LLM relay files, calculation job records, real task validation records, analysis result records, decision-support records, usable-research records, usable-project records, experiment campaign records, statistical design records, model registry records, systematic review records, experiment scheduling records, training compliance records, release dossier records, mature platform mappings, provenance views, provenance query records, Agent collaboration records, AgentCompare records, and cross-file consistency records.
- object catalog, reusable object records, object query, lineage, site export, task messages, role acknowledgements, tool logs, scheduling records, task records, task ranking, workflow import/export description, resource budget, project policy, risk register, CAPA records, release delta review, run configuration, workflow invocation, workflow completion, execution plan, worker health, execution timeline, observer evidence, concrete input files, three custom research requests, nine small CSV-style rows, request-form, uploaded-material, reusable-source, workspace-import, generated workspace-template, workspace-run sections, four dynamic submission records, file-backed validation state, and host UI feed hints, three derived custom run summaries and one queued API run, human review records, revision-task records, one revised run record, review thread records, review comment records, action item records, bibliography and citation-plan records, literature search, candidate screening, evidence extraction, evidence protocol, PRISMA-style flow, evidence synthesis, input-file scan records, normalized FASTQ, alignment table, metrics JSON, gene-count CSV, archive manifest sections, artifact dossier links, and artifact provenance records inside `rp_artifact`, dataset snapshots, data previews, quality results, transform records, dataset collections, stage DAG, stage logs, workflow runner execution state, stage command/output records, dependency checks, dossier checks, content-keyed cache records, retry plan with failure reason and rerun input/output fields, run events with retry decisions and report/evidence references, artifact manifest with support links, recovered artifacts, report text, chart data, package export indexes for report, evidence, provenance, reusable source selection, reviewer delivery, eight delivery file rows, three delivery checks, delivery manifest JSON/Markdown names, evidence bundle zip name, evidence bundle contents, raw artifacts, and review page sections, UI navigation, timeline rows, artifact previews, Agent decision rows, evidence previews, dynamic-input queue references, live-update feed records, host reader contract records, comparison metric rows, Host Web/API route and payload files, file-backed action request and response files, failure classification, retry records, run views, data dictionary, data profile records, figure records, calculation replay, samples, quality, protocol, SOP, experiment, trial records, lab operations, training, sample registry, ethics review, data access review, cohort view, instrument registry, inventory, procurement, resource scheduling, result review, publication plan, peer review response, FAIR package, literature review, citation graph, semantic index, knowledge answers, runtime environment records, notebook replay records, ELN records, worker pool records, telemetry, health summaries, evidence, claim records, provenance paths, knowledge, multi-round review, report revision package, LLM packet queue, host relay request/response handoff, prompt routing, LLM audit log, LLM evaluation, privacy, compliance record, FAIR data release, data product versioning, reproduction package, package, release, dossier, review governance, submission package, backend scenario evidence, AgentCompare metrics, and plain-kernel comparison files.

It does not use Agent-OS kernel features. That is intentional for this plain-kernel baseline.

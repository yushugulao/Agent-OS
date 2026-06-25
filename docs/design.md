# Plain uCore Platform Design

## Positioning

This branch keeps the uCore kernel unchanged and places the research Agent platform in ordinary user space.

The kernel does not know about Agent roles, Agent Context, tool batches, file metadata indexes, or Agent event queues. Those mechanisms belong to the later Agent-OS enhanced kernel branch.

## Runtime Shape

The current runtime has four parts:

1. Upstream uCore kernel.
2. Restored uCore user library and program build flow.
3. `rp_plain`, a native user process that carries the research platform catalog, feature groups, mature platform mappings, and self-check logic.
4. `rp_orch`, a native user process that runs thirty-eight platform programs through ordinary `fork`, `exec`, and `waitpid`.

The user process deliberately uses ordinary C data structures and ordinary uCore process execution. This makes the result a baseline for later comparison with the Agent-OS kernel-enhanced version.

## Host-Service Split

The host-side research Agent platform is moving toward real HTTP UI, real artifact operations, a small workflow runner, and a host LLM relay. The unchanged uCore branch should mirror those concepts with ordinary files and ordinary user programs:

- UI pages become exported state files and Host Web/API payload files for home, run detail, Agent detail, evidence detail, artifact, and comparison views.
- Artifact operations become reads from input files and writes to intermediate files, reports, logs, and chart-data files.
- Data pipeline behavior becomes ordinary files for input scanning, dataset snapshots, data preview, quality checks, transformations, and dataset collection export.
- Workflow runner behavior becomes stage records with dependencies, failure, retry, cache, and log fields.
- LLM calls become request queue and response files. Template responses can be produced inside uCore; cloud access and secrets stay on the host.
- Agent collaboration becomes explicit role messages, acknowledgements, decisions, recovery actions, and audit records.
- AgentCompare keeps the same result names while showing what is weaker on the plain kernel: file scans, convention-based state, user-space-only permission checks, untrusted context, reconstruction cost, and longer recovery steps.

## Preserved Platform Concepts

The native program preserves the platform vocabulary and scale from the pure user-space platform:

- Workflow templates and invocations.
- Workflow portability and migration planning.
- Execution control and worker operations.
- Agent roles, task assignments, handoffs, and deliberation.
- Data ingestion, dataset snapshots, samples, cohorts, annotations, studies, ethics, lab operations, and analysis results.
- Visualization, publication, FAIR data, literature, knowledge, semantic graph, prompt/model operations, LLM bridge, secrets, collaboration, observability, release governance, protocol compliance, risk handling, provenance, search, dashboard data, and AgentCompare.

The first native version stores these as compact static tables. This is intentionally simple: it proves that a large platform-shaped program can run as a normal process on unchanged uCore before deeper user-space services are added.

The platform programs add an executable multi-process shape:

- `rp_catalog`
- `rp_object_store`
- `rp_object_query`
- `rp_lineage`
- `rp_site_export`
- `rp_planner`
- `rp_retriever`
- `rp_analyst`
- `rp_reviewer`
- `rp_lab`
- `rp_governance`
- `rp_writer`
- `rp_repair`
- `rp_auditor`
- `rp_query`
- `rp_evidence`
- `rp_llm_bridge`
- `rp_llm_relay`
- `rp_privacy`
- `rp_runconf`
- `rp_execobs`
- `rp_invoke`
- `rp_complete`
- `rp_artifact_ops`
- `rp_data_pipeline`
- `rp_workflow_runner`
- `rp_agent_collab`
- `rp_package`
- `rp_delta`
- `rp_release`
- `rp_dossier`
- `rp_backend`
- `rp_consistency`
- `rp_metrics`
- `rp_ui_export`
- `rp_web_export`
- `rp_test_suite`
- `rp_compare_plain`

These programs do not require Agent syscalls. They are ordinary uCore processes that make the plain-kernel baseline closer to the original multi-role research Agent platform.

## User-Space State Protocol

The orchestrator and role programs use ordinary files as their state protocol:

| File | Writer | Reader | Meaning |
| --- | --- | --- | --- |
| `rp_plan` | planner | retriever, analyst, auditor | run id, workflow, assignments, repair policy |
| `rp_mail` | planner | role programs | task messages for role-level user-space coordination |
| `rp_ack` | role programs | metrics, compare, orchestrator | role acknowledgements for completed tasks |
| `rp_tool` | role programs | metrics, compare, orchestrator | tool-level operation log written by ordinary user programs |
| `rp_sched` | planner | metrics, compare, orchestrator | task queue size, priority summary, retry policy, and stage-order deadline model |
| `rp_taskrec` | planner | query, metrics, compare, orchestrator | task-level records with owner, stage, priority, class, and ready state |
| `rp_budget` | planner | query, metrics, compare, orchestrator | token, tick, storage, and worker-slot budget summary |
| `rp_wfio` | planner | package, dossier, metrics, compare, orchestrator | workflow import/export formats, portable step count, and compatibility checks |
| `rp_policy` | planner | privacy, package, dossier, metrics, compare, orchestrator | access profiles, data use rules, LLM outbound rules, license checks, and retention policy |
| `rp_retryq` | planner | repair | pending retry item for the failed align stage |
| `rp_lit` | retriever | reviewer, auditor | literature count and evidence links |
| `rp_data` | analyst | reviewer, repair, auditor | datasets, statistics, figures, failed stage |
| `rp_datadic` | analyst | lab, package, compare | schema fields, controlled terms, transform specs, drift result |
| `rp_dataprof` | analyst | package, dossier, metrics, compare, orchestrator | data profile records with row count, column count, missing cells, outlier checks, and normalization status |
| `rp_compute` | analyst | lab, package, compare | notebook replay, statistics, calculation job, figure summary |
| `rp_figrec` | analyst | package, dossier, metrics, compare, orchestrator | figure-level records with type, source file, export count, and readiness |
| `rp_fail` | analyst | repair, auditor, package, metrics, compare | failure class, severity, recoverability, and recommended action |
| `rp_samples` | samples | quality, SOP | sample sheet, cohort, and custody summary |
| `rp_quality` | quality | protocol, experiment, compare | data quality and schema validation result |
| `rp_review` | reviewer | writer, auditor | claim review and release decision |
| `rp_review2` | reviewer | writer, dossier, metrics, compare, orchestrator | multi-round review threads, comment resolution, and remaining blocker count |
| `rp_protocol` | protocol | SOP, compare | protocol, ethics, analysis plan, and amendment status |
| `rp_soplog` | SOP execution | experiment | controlled SOP execution evidence |
| `rp_exper` | experiment | telemetry | experiment campaign and selected best trial |
| `rp_trialrec` | lab | package, dossier, metrics, compare, orchestrator | trial-level records for parameter sweep, selected trial, and completion state |
| `rp_labops` | lab | package, compare | instrument, reagent, inventory, reservation, and maintenance summary |
| `rp_training` | lab | package, compare | personnel training and competency summary |
| `rp_risk` | governance | auditor, package, release, dossier, metrics, compare, orchestrator | risk register with mitigation status for failed tools, protocol deviation, and LLM outbound control |
| `rp_capa` | governance | auditor, package, release, dossier, metrics, compare, orchestrator | corrective and preventive action records with verification evidence |
| `rp_report` | writer | auditor | report sections, citations, response items |
| `rp_revision` | writer | package, dossier, metrics, compare, orchestrator | report draft versions, review response items, and resolved comment count |
| `rp_fix` | repair | auditor | repaired stage and generated artifact |
| `rp_retrylog` | repair | auditor, package, dossier, compare | retry attempts, dedupe key, backoff ticks, and final result |
| `rp_telemetry` | telemetry | AgentCompare | trace, bottleneck, poll, scan, and tick observations |
| `rp_health` | telemetry | compare, orchestrator | worker health, budget state, failure count, retry count, and view status |
| `rp_audit` | auditor | orchestrator | final provenance, release, package status |
| `rp_status` | all role programs | orchestrator | role-level status summary |
| `rp_objects` | catalog | query, compare | object counts and platform scale |
| `rp_services` | catalog | query | service search counts |
| `rp_object_records` | object store | object query | reusable platform object records |
| `rp_object_query` | object query | lineage, compare | object search result counts |
| `rp_lineage` | lineage | site export, compare | workflow artifact relationships |
| `rp_site` | site export | compare | exported site page summary |
| `rp_query` | query | compare | selected search result counts |
| `rp_rank` | query | metrics, compare, orchestrator | task ranking result derived from `rp_taskrec` |
| `rp_runview` | query | metrics, compare, orchestrator | run-level view that joins query hits, scheduler state, failure count, and budget state |
| `rp_evidence` | evidence | package | claims, links, provenance node count |
| `rp_claimrec` | evidence | LLM bridge, package, dossier, metrics, compare, orchestrator | claim-level support records linked to data, protocol, recovery, privacy, release, and reproduction sources |
| `rp_provpath` | evidence | LLM bridge, package, dossier, metrics, compare, orchestrator | provenance path summary with node count, edge count, claim count, and critical path count |
| `rp_knowledge` | evidence | package, compare | knowledge, semantic, and systematic review summary |
| `rp_llm_req` | LLM bridge | privacy | host LLM request packet without embedded secrets |
| `rp_llmq` | LLM bridge | privacy, package, release, dossier, metrics, compare, orchestrator | host relay request queue, route selection, and per-request secret policy |
| `rp_llm_resp` | LLM bridge | privacy, compare | deterministic template LLM response |
| `rp_relay` | LLM bridge | privacy, package, release, dossier, compare | host-file relay mode, secret location, network ownership, and fallback policy |
| `rp_prompt` | LLM bridge | privacy, package, compare | prompt versions, route policy, token budget, and evaluation cases |
| `rp_llmlog` | LLM bridge | privacy, package, compare | transcript count, packet audit, privacy status, and replay status |
| `rp_llmeval` | LLM bridge | privacy, package, release, dossier, metrics, compare, orchestrator | template response evaluation cases, grounded answer count, route switches, and fallback use |
| `rp_llm_packets` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | packet-level host relay contract for three LLM requests |
| `rp_llm_routes` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | route table for template and optional host cloud execution |
| `rp_llm_guard` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | secret and outbound ownership check for relay packets |
| `rp_llm_hostreq` | LLM relay | privacy, package, consistency, compare, orchestrator | host request handoff contract with no secret material in uCore |
| `rp_llm_fallback` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | fallback handling for missing cloud key, network loss, and privacy rejection |
| `rp_privacy` | privacy | release | outbound packet review result |
| `rp_compliance` | privacy | package, release, dossier, metrics, compare, orchestrator | policy compliance result covering access profiles, data use rules, LLM packets, secret placement, and license checks |
| `rp_params` | run configuration | package, metrics, compare, orchestrator | baseline and candidate parameter set summary |
| `rp_runconf` | run configuration | package, release, dossier, metrics, compare, orchestrator | baseline and candidate run configuration profiles |
| `rp_configval` | run configuration | package, metrics, compare, orchestrator | profile validation result with checked items and warnings |
| `rp_configdrift` | run configuration | metrics, compare, orchestrator | baseline and candidate configuration difference summary |
| `rp_execplan` | execution observer | package, dossier, metrics, compare, orchestrator | plain-kernel execution plan, workflow step count, scheduled task count, worker slots, retry items, and LLM packet count |
| `rp_worker` | execution observer | package, metrics, compare, orchestrator | worker health, heartbeat count, queue actions, and failure handling actions |
| `rp_timeline` | execution observer | release, package, dossier, metrics, compare, orchestrator | run timeline, stage order, tick span, and critical path |
| `rp_execobs` | execution observer | release, package, dossier, metrics, compare, orchestrator | observer packet summary connecting execution plan, timeline, worker health, and evidence readiness |
| `rp_invocation` | workflow invocation | package, release, dossier, metrics, compare, orchestrator | invocation identity, template, step count, output count, cache reuse, and final status |
| `rp_steps` | workflow invocation | metrics, compare, orchestrator | step status counts for completed, cached, failed, and recovered stages |
| `rp_attempts` | workflow invocation | package, metrics, compare, orchestrator | attempt count, retry count, worker, cache actions, and final result |
| `rp_invoke_export` | workflow invocation | package, compare, orchestrator | invocation export record |
| `rp_hooks` | workflow completion | compare, orchestrator | completion hook count by action type |
| `rp_completion` | workflow completion | package, release, dossier, metrics, compare, orchestrator | completion event, invocation status, action count, export count, and final status |
| `rp_actions` | workflow completion | metrics, compare, orchestrator | notification, runbook, evidence export, and audit action results |
| `rp_complete_export` | workflow completion | package, compare, orchestrator | completion event export record |
| `rp_input` | artifact operations | package, consistency, compare, orchestrator | concrete input manifest for RUN-042 |
| `rp_input_fastq` | artifact operations | artifact operations | ordinary input data read by the artifact operation program |
| `rp_stage_dag` | artifact operations | package, consistency, compare, orchestrator | stage dependency, cache, failure, and retry record |
| `rp_stage_log` | artifact operations | package, consistency, compare, orchestrator | per-stage execution log for ingest, align, profile, review, and package stages |
| `rp_artifact` | artifact operations | package, consistency, compare, orchestrator | recovered align-stage artifact tied to the concrete input |
| `rp_report_text` | artifact operations | package, compare, UI export | report text generated from the recovered run |
| `rp_chart_data` | artifact operations | package, compare, UI export | chart-ready stage attempt data for the host UI |
| `rp_runner` | artifact operations | consistency, compare, orchestrator | plain uCore stage runner summary with retries and cache hits |
| `rp_ingest_files` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | concrete input-file scan result for RUN-042 |
| `rp_dataset_snapshot` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | raw and normalized dataset snapshot summary |
| `rp_data_preview` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | preview rows and columns for FASTQ and sample records |
| `rp_data_quality` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | data-quality rule results for the dataset |
| `rp_data_transform` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | transform records for normalization and sample-sheet join |
| `rp_dataset_collection` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | final dataset collection tied to input, sample, count, and artifact sources |
| `rp_stage_state` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | executable stage-state table derived from the DAG and logs |
| `rp_cache_index` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | cache hit and miss records for all five stages |
| `rp_retry_plan` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | retry item for the failed align stage with reason and action |
| `rp_run_events` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | run-level event stream for start, cache, failure, retry, recovery, and package events |
| `rp_artifact_manifest` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | generated artifact manifest for input, intermediate, report, and chart outputs |
| `rp_agents` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | seven role records for orchestrator, retriever, analyst, reviewer, writer, recovery, and auditor |
| `rp_decisions` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | eight concrete decisions tied to plan, evidence, failure, recovery, report, audit, and comparison records |
| `rp_handoff` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | six role-to-role handoff records with source artifacts |
| `rp_deliberation` | Agent collaboration | package, consistency, compare, orchestrator | discussion items for failure recovery, cache reuse, host relay, evidence quality, and release |
| `rp_agent_run` | Agent collaboration | package, consistency, metrics, compare, orchestrator | Agent collaboration summary for RUN-042 |
| `rp_package` | package | compare | packaged artifact and release summary |
| `rp_diff` | delta | release, dossier, metrics, compare, orchestrator | release candidate difference summary across report, data, figures, risk, and reproduction evidence |
| `rp_delta` | delta | release, dossier, metrics, compare, orchestrator | release delta review with accepted item count, blocked count, package, risk, and reproduction status |
| `rp_datarel` | package | release, dossier, compare | FAIR data, data product, DOI, and publication readiness |
| `rp_dataver` | package | release, dossier, metrics, compare, orchestrator | data product versions, snapshots, schema versions, and release candidate |
| `rp_repro` | package | release, dossier, compare | environment locks, notebook replay, reproduction checks, and research object crate |
| `rp_release` | release | dossier, compare | release decision from package, audit, privacy, and LLM packet state |
| `rp_dossier` | dossier | compare | final review material summary |
| `rp_reviewops` | dossier | compare | review board, vote, risk, mitigation, and governance result |
| `rp_submit` | dossier | compare | journal target, cover letter, data availability, and review response package |
| `rp_agentcmp` | AgentCompare metrics | compare | plain-kernel comparison counters |
| `rp_backend` | backend scenario | compare, orchestrator | same-workflow backend scenario case count and planned Agent-OS cases |
| `rp_backend_exec` | backend scenario | compare, orchestrator | backend scenario execution result for executable and planned cases |
| `rp_backend_export` | backend scenario | compare, orchestrator | backend scenario export record |
| `rp_study` | backend scenario | compare, orchestrator | same-workflow backend study summary |
| `rp_consistency` | consistency checker | metrics, compare, orchestrator | derived checks across task records, LLM packets, relay protocol files, workflow invocation, completion hooks, backend cases, runner artifacts, and workflow runner execution files |
| `rp_ui_home` | UI export | compare, orchestrator | home page data for the host web service |
| `rp_ui_run` | UI export | compare, orchestrator | run-detail page data for RUN-042 |
| `rp_ui_agent` | UI export | compare, orchestrator | Agent-detail page data for role messages and decisions |
| `rp_ui_evidence` | UI export | compare, orchestrator | evidence-detail page data with stage log and recovered artifact links |
| `rp_ui_compare` | UI export | compare, orchestrator | comparison page data for plain-kernel pain points |
| `rp_web_routes` | Host Web/API export | test suite, compare, orchestrator | route manifest for host-rendered home, run, Agent, evidence, data, artifact, and comparison views |
| `rp_api_home` | Host Web/API export | test suite, compare, orchestrator | API payload for the host web home page |
| `rp_api_run` | Host Web/API export | test suite, compare, orchestrator | API payload for RUN-042 run detail with runner execution files |
| `rp_api_agents` | Host Web/API export | test suite, compare, orchestrator | API payload for role messages, decisions, and handoffs |
| `rp_api_evidence` | Host Web/API export | test suite, compare, orchestrator | API payload for claims, provenance paths, stage log, artifact, manifest, and LLM guard |
| `rp_api_compare` | Host Web/API export | test suite, compare, orchestrator | API payload for plain-kernel comparison signals |
| `rp_api_artifacts` | Host Web/API export | test suite, compare, orchestrator | API payload for input, stage, manifest, report, chart, and LLM relay artifacts |
| `rp_api_data` | Host Web/API export | test suite, compare, orchestrator | API payload for input-file scan, dataset snapshots, previews, quality, transforms, and collection records |
| `rp_web_bundle` | Host Web/API export | test suite, compare, orchestrator | bundle summary tying routes, API payloads, UI pages, runner files, and relay files together |
| `rp_tests` | test suite | compare, orchestrator | 82 user-space checks over catalog, data pipeline, workflow, artifacts, workflow runner files, Agent collaboration, UI data, Host Web/API export files, LLM relay, AgentCompare, and consistency records |
| `rp_compare` | compare | orchestrator | plain-kernel execution summary |

This is intentionally implemented without new syscalls. It uses only `open`, `read`, `write`, `close`, `fork`, `exec`, and `waitpid`.

## Upstream Kernel Guarantee

The `os`, `nfs`, and `scripts` directories are copied from the upstream uCore 2025S source. Kernel verification uses directory comparison rather than source comments.

The only implementation changes needed for the first native platform step are in ordinary user-space files:

- `user/src/rp_plain.c`
- `user/src/rp_orch.c`
- `user/src/rp_*.c`
- `user/include/research_platform_state.h`
- `user/Makefile`
- `user/src/usershell.c`

`usershell.c` only changes character constants so it builds with the available GNU RISC-V toolchain.

## Compatibility With Later Agent-OS Version

The native program keeps stable object names, role names, capability names, and output lines. The later Agent-OS version can replace in-process tables with kernel-assisted Context Path, tool calls, file metadata, Agent events, and LLM gateway requests while preserving the same demonstration contract.

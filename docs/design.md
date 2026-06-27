# Plain uCore Platform Design

## Positioning

This branch keeps the uCore kernel unchanged and places the research Agent platform in ordinary user space.

The kernel does not know about Agent roles, Agent Context, tool batches, file metadata indexes, or Agent event queues. Those mechanisms belong to the later Agent-OS enhanced kernel branch.

## Directory Rule

The plain uCore target stays in the current root: `os/`, `nfs/`, `scripts/`, `user/`, and `host_tools/`. The later Agent-OS enhanced uCore target should be added as a separate top-level directory, such as `agentos_ucore/`, with its own kernel tree, user programs, tests, and documents. This keeps the plain target runnable for comparison while the enhanced target can introduce kernel services.

## Runtime Shape

The current runtime has four parts:

1. Upstream uCore kernel.
2. Restored uCore user library and program build flow.
3. `rp_plain`, a native user process that carries the research platform catalog, feature groups, mature platform mappings, and self-check logic.
4. `rp_orch`, a native user process that runs sixty platform programs through ordinary `fork`, `exec`, and `waitpid`.
5. `rp_seed_orch`, a Host-action entry that runs the seeded program set after the action runner places a compact `rp_host_action_seed` file in the uCore image.

The user process deliberately uses ordinary C data structures and ordinary uCore process execution. This makes the result a baseline for later comparison with the Agent-OS kernel-enhanced version.

## Host-Service Split

The host-side research Agent platform is moving toward real HTTP UI, real artifact operations, a small workflow runner, and a host LLM relay. The unchanged uCore branch should mirror those concepts with ordinary files and ordinary user programs:

- UI pages become exported state files and Host Web/API payload files for home, run detail, workflow, workbench, Studio, project, data pipeline, Agent detail, evidence detail, artifact, dynamic input, live update, and comparison views.
- HTTP POST actions become ordinary request and response files for Studio launch, host workflow execution, host workflow export, host workflow stage/cache/retry/artifact/report steps, artifact input/derive/log/chart/package operations, workflow portability execution, workflow portability import/plan/bind/rehearse/review/package steps, AgentCompare execution, custom research execution, reusable source lookup, custom research export, human review, revision-task creation, revised-run execution, workbench creation, workbench advance, workbench auto-advance, readiness checks, cited answers, answer audits, evidence search, workbench task updates, workbench notes, handoff packages, briefs, evidence dossiers, evidence graphs, citations, manuscript drafts, manuscript audits, manuscript revision plans, manuscript revision tasks, task boards, task-board row updates, runbooks, timelines, file manifests, workbench file verification, notebook download, and bundle download.
- Host page rendering is driven by explicit state: `rp_web_bundle` names the reader contract, payload files, refresh files, event stream, fallback static site, and state source. `host_tools/plain_ucore_reader.py` consumes those records, renders host-viewable pages with a sidebar, summary cards, research-output summaries, platform-written report source rows whose `source_key` values are resolved to current `rp_*` evidence lines, artifact source rows whose references are resolved to current artifact, event, retry, review, LLM, and package lines, review source rows whose dashboard and evidence-package references are resolved to current handoff evidence lines, a workflow runner page with stage, cache, retry, worker, event, and evidence-link tables, a research workbench page with task, writing, package, review-board, and action tables, a project page with project-space, evidence-package, quality, repair, search, and source-file tables, a delivery package page with file, bundle, notebook, review-pack, and source-line tables, delivery source rows whose package, notebook, and usable-run references are resolved to current delivery evidence lines, a publication workflow page with journal target, submission, peer-review, revision, response-package, response-item, and decision rows, a mature-platform page with reference platform, capability mapping, check, and AgentOS target rows, Agent detail summaries, Agent roster and decision-flow tables, evidence-package summaries, evidence detail summaries, review dashboard sections and gates, service operation records from the bio, lab resource, publication, knowledge, and runtime service files, claim/provenance/protocol tables, comparison summaries, compare metric summaries, plain-kernel signal tables, backend runner case tables with content checks, attempts, retry reasons, source/requirement/observation/action/review rows, plain-cost/AgentOS-replacement/risk rows, derived backend case narratives, operations narrative rows, operations source-file rows, LLM relay flow rows, LLM action trace rows, action-output links, matched state-line details, action-impact rows for report/artifact/review/LLM targets, request-to-output action deltas, backend study metric tables, backend scenario handoff status, state tables, action history, and a batch-action editor, serves contract/API/live endpoints, and captures single or batched POST actions as ordinary host-side records without changing uCore. In auto-run mode, a single action or an `/actions/batch` request invokes the action runner, runs `CHAPTER=platform_seeded` with `INIT_PROC=rp_seed_orch`, extracts text `rp_*` state files from `nfs/fs-copy.img`, publishes `rp_host_run_result`, and refreshes the served state directory.
- The Research Studio surface is represented by `studio.html`, `/research-studio`, `/actions/research/studio-launch`, and `rp_studio`. A Studio launch stores session title, goal, direction, material summary, workbench/run/answer links, package markers, and action traces in `rp_studio`, `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle`; the compact uCore seed keeps only the fields required by the ordinary user programs, while the host package keeps the richer browser request.
- Captured host actions are translated by `host_tools/plain_ucore_action_runner.py` into `rp_host_action_queue`, `rp_host_action_plan`, and `rp_host_action_inbox`. When requested, the runner keeps the full action inbox in the host run package, writes a compact seed to `state-next/rp_host_action_seed`, pads `rp_*` image inputs to the teaching file-system block size, copies that seed file into `user/target/bin/`, and then builds the ordinary uCore file-system image. The compact seed removes action path and status fields that ordinary uCore programs do not read, while preserving one line per action with `kind` and payload fields; this keeps large batched actions inside the uCore teaching file-system image capacity without relying on large `exec` argument vectors. After QEMU finishes, `host_tools/plain_ucore_fs_extract.py` reads the unchanged uCore file-system image format, skips binary programs, restores long `rp_*` names when possible, and publishes the generated state files back to the host state directory. `rp_host_run_result` records whether the seeded run passed, how many host actions were verified, and how many state files were extracted. The ordinary user programs read the seeded text and route each action type into the same state files used by the platform: research runs update `rp_input`, data pipeline files, report text, Web/API state, usable-run summary, and `rp_runner`; dataset, library, template, workspace, and evidence actions update the input, evidence, artifact, report, package, and comparison records; host workflow run/export actions update `rp_stage_dag`, `rp_stage_state`, `rp_run_events`, `rp_cache_index`, `rp_retry_plan`, `rp_worker`, `rp_execobs`, `rp_artifact_manifest`, `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle`; host workflow stage/cache/retry/artifact/report actions additionally update `rp_stage_state`, `rp_run_events`, `rp_cache_index`, `rp_retry_plan`, `rp_artifact_manifest`, `rp_report_text`, and package/action summaries with concrete attempt, cache key, retry decision, artifact hash, and report fields; artifact input/derive/log/chart/package actions update `rp_artifact`, `rp_stage_log`, `rp_chart_data`, `rp_artifact_manifest`, `rp_package`, `rp_actionio`, `rp_web_bundle`, and `rp_agentcmp`; Host LLM Relay request/response/fallback actions update `rp_llm_req`, `rp_llmq`, `rp_llm_resp`, `rp_llm_packets`, `rp_llm_hostreq`, `rp_llm_fallback`, `rp_api_runtime`, `rp_actionio`, and `rp_web_bundle`; comparison runs update `rp_agentcmp`; human review updates `rp_review2`; revision actions update `rp_revision` and `rp_runner`; workbench actions update compact records in `rp_runner`; writing-oriented workbench actions update `rp_revision`; package-oriented workbench actions update `rp_package`; file-manifest and file-verify workbench actions additionally update data and artifact state in `rp_ingest_files`, `rp_data_quality`, `rp_dataset_collection`, `rp_artifact_manifest`, `rp_api_artifacts`, and `rp_api_data`; operations, project-space, project-review, research-search, quality, delivery, and plan-queue actions update `rp_runner`, `rp_package`, `rp_actionio`, and `rp_web_bundle`, with project-review summary records stored in `rp_web_bundle`; notebook export updates `rp_nbexec`; bundle/export actions update `rp_package`; and all of them are summarized in `rp_actionio`, `rp_web_bundle`, and `rp_agentcmp`. The `rp_package` workbench records include handoff, manifest, brief, dossier, graph, citation, manuscript, task board, runbook, timeline, readiness, completion, workbench export, file count, hash-record count, verified count, and missing count. The seed parser reads payload fields on the matching action line, so repeated keys in a batch do not overwrite one another; `run_id`, research title, research question, provider, dataset row count, reference count, workspace file count, CSV filename, reference filename, workflow id, workflow engine, workflow DAG, workflow export bundle, workflow stage, attempt, command, cache key, cache result, retry decision, artifact name, artifact hash, artifact input filename, artifact output filename, artifact log name, artifact chart name, artifact package name, report name, report format, worker slots, queue depth, observer event count, LLM request id, LLM route, LLM provider, LLM response id, LLM summary, LLM fallback case, reviewer, decision, targets, task id, workbench id, workbench title, literature query, workbench question, evidence-search query, workbench task/status, workbench note kind/title/body, notes filter, handoff scope, brief format, dossier format, graph format, citation format, manuscript format, audit scope, revision area, revision task/status, board filter, board-row id/status, runbook format, timeline format, file-manifest name, file counts, hash counts, verification counts, format, bundle, and comparison profile are preserved in the generated state files. The same fields are written into report text, artifact manifest, notebook execution summary, usable-run summary, package download summary, runtime API, relay protocol files, and compare API records so the visible research output changes with the submitted action payload.
- Artifact operations become reads from input files and writes to intermediate files, reports, logs, and chart-data files.
- Custom research tasks are represented as ordinary files: `rp_input` carries the compact dataset, submitted form fields, uploaded CSV/reference summaries, reusable source selection, local workspace-import rows, generated workspace template, workspace-run reference, four dynamic submission records, validation state, and host UI feed hints; `rp_runner` appends three derived custom runs, one queued API run, stages, analysis, bibliography, citation plan, report, review, human-review decision, revision-task state, revised-run reference, export result, workbench readiness, cited answer, handoff brief, continuation runbook, timeline, and file manifest sections. Multi-round review threads, comments, and action items are stored in `rp_review2` and referenced by package, UI, API, and compare records.
- Data pipeline behavior becomes ordinary files for input scanning, dataset snapshots, data preview, quality checks, transformations, and dataset collection export.
- Bio, lab resource, publication, knowledge, and runtime service behavior becomes ordinary files with short names that fit the uCore root directory entry size.
- Workflow runner behavior becomes stage records with dependencies, failure, retry, cache, log, worker, queue, and observer fields.
- Workflow portability actions carry import id, source format, target runtime, execution plan, compare profile, scenario id, rehearsal status, readiness decision, and package name into `rp_wfio`; package, action, Web bundle, and compare records then reference the same values.
- LLM calls become request queue and response files. Template responses can be produced inside uCore, and `host_tools/plain_ucore_llm_relay.py` can refresh those files from the host side in template mode or OpenAI-compatible mode. Cloud access and secrets stay on the host.
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
- `rp_portability`
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
- `rp_workbench`
- `rp_agent_collab`
- `rp_package`
- `rp_calculation`
- `rp_realtask`
- `rp_campaign`
- `rp_reldossier`
- `rp_delta`
- `rp_release`
- `rp_dossier`
- `rp_service_surface`
- `rp_notebook_export`
- `rp_backend`
- `rp_consistency`
- `rp_metrics`
- `rp_ui_export`
- `rp_web_export`
- `rp_compare_plain`

These programs do not require Agent syscalls. They are ordinary uCore processes that make the plain-kernel baseline closer to the original multi-role research Agent platform.

## User-Space State Protocol

The orchestrator and role programs use ordinary files as their state protocol:

| File | Writer | Reader | Meaning |
| --- | --- | --- | --- |
| `rp_plan` | planner | retriever, analyst, auditor | run id, workflow, assignments, repair policy |
| `rp_mail` | planner | role programs | task messages for role-level user-space coordination |
| `rp_ack` | role programs | metrics, compare, orchestrator | role acknowledgements for completed tasks |
| `rp_tool` | role programs | metrics, compare, orchestrator | compact tool event names written by ordinary user programs |
| `rp_sched` | planner | metrics, compare, orchestrator | task queue size, priority summary, retry policy, and stage-order deadline model |
| `rp_taskrec` | planner | query, metrics, compare, orchestrator | task-level records with owner, stage, priority, class, and ready state |
| `rp_budget` | planner | query, metrics, compare, orchestrator | token, tick, storage, and worker-slot budget summary |
| `rp_wfio` | planner, portability | package, dossier, metrics, compare, UI export, Host Web/API export, test suite, orchestrator | workflow import/export formats, portable step count, compatibility checks, workflow adapter summaries, migration plans, rehearsal cases, and Agent-OS migration decision |
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
| `rp_review2` | reviewer | writer, dossier, metrics, package, UI export, Host Web/API export, compare, orchestrator | multi-round review threads, comments, action items, requested changes, revision task reference, resolution state, and remaining blocker count |
| `rp_review_dashboard` | review dashboard | package, Host Web/API export, compare, orchestrator | run review dashboard that joins workflow, artifact, LLM, review, Agent, delivery, and comparison sources with pass/fail gates and reviewer decision records |
| `rp_review_pack` | reviewer evidence package state | Host Web/API export, Host LLM Relay, review dashboard | host-materialized reviewer handoff package with required-file, workflow, artifact, LLM quality, packet guard, human review, revision, delivery, operations, project-space, workbench handoff, backend evidence checks, backend action/review rows, operations/workbench/project summaries, operations report narrative inputs, related Host action trace, and reviewer actions |
| `rp_protocol` | protocol | SOP, compare | protocol, ethics, analysis plan, and amendment status |
| `rp_soplog` | SOP execution | experiment | controlled SOP execution evidence |
| `rp_exper` | experiment | telemetry | experiment campaign and selected best trial |
| `rp_trialrec` | lab | package, dossier, metrics, compare, orchestrator | trial-level records for parameter sweep, selected trial, and completion state |
| `rp_labops` | lab | package, compare | instrument, reagent, inventory, reservation, and maintenance summary |
| `rp_training` | lab | package, compare | personnel training and competency summary |
| `rp_risk` | governance | auditor, package, release, dossier, metrics, compare, orchestrator | risk register with mitigation status for failed tools, protocol deviation, and LLM outbound control |
| `rp_capa` | governance | auditor, package, release, dossier, metrics, compare, orchestrator | corrective and preventive action records with verification evidence |
| `rp_report` | writer | auditor | report sections, citations, response items |
| `rp_revision` | writer and host relay | package, dossier, metrics, compare, orchestrator | report draft versions, relay writer summary, review response items, applied revision changes, revised-run reference, and resolved comment count |
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
| `rp_site` | site export | package, dossier, UI export, Host Web/API export, test suite, compare, orchestrator | static review site inventory with 42 routed pages, route records, JSON payload count, download links, and preview page count |
| `rp_query` | query | compare | selected search result counts plus search document, provenance, event, and context record totals |
| `rp_rank` | query | metrics, compare, orchestrator | task ranking result derived from `rp_taskrec` |
| `rp_runview` | query | metrics, compare, orchestrator | run-level view that joins query hits, scheduler state, failure count, and budget state |
| `rp_evidence` | evidence | package | claims, links, provenance node count |
| `rp_claimrec` | evidence | LLM bridge, package, dossier, metrics, compare, orchestrator | claim-level support records linked to data, protocol, recovery, privacy, release, and reproduction sources |
| `rp_provpath` | evidence | LLM bridge, package, dossier, metrics, compare, orchestrator | provenance path summary with node count, edge count, claim count, and critical path count |
| `rp_knowledge` | evidence | package, compare | knowledge, semantic, literature-search, screening, evidence-extraction, evidence-protocol, PRISMA-style flow, and synthesis summary |
| `rp_llm_req` | LLM bridge | privacy | host LLM request packet without embedded secrets |
| `rp_llmq` | LLM bridge | privacy, package, release, dossier, metrics, compare, orchestrator | host relay request queue, queue validation, route selection, dispatch readiness, and per-request secret policy |
| `rp_llm_resp` | LLM bridge and host relay | privacy, compare | deterministic template LLM responses, host relay response summaries, request-id matching, response hash records, and grounded reference count |
| `rp_relay` | LLM bridge and host relay | privacy, package, release, dossier, compare | host-file relay mode, secret location, network ownership, request validation, response validation, replay records, and fallback policy |
| `rp_prompt` | LLM bridge and host relay | privacy, package, compare | prompt versions, route policy, token budget, evaluation cases, and host relay route records |
| `rp_llmlog` | LLM bridge and host relay | privacy, package, compare | transcript count, bridge request/response totals, packet audit, privacy status, replay status, and host relay execution records |
| `rp_llmeval` | LLM bridge and host relay | privacy, package, release, dossier, metrics, compare, orchestrator | template response evaluation cases, grounded answer count, route switches, fallback use, and host relay response quality checks |
| `rp_llm_packets` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | packet-level host relay contract with request and response ids, packet schema checks, dispatch records, response join state, prompt hashes, and `secret_in_packet=0` records |
| `rp_llm_routes` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | route table, route policy, and route decisions for template and optional host cloud execution |
| `rp_llm_guard` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | secret scan, payload hash, outbound ownership, packet blocking check, and host relay guard records for relay packets |
| `rp_llm_hostreq` | LLM relay | privacy, package, consistency, compare, orchestrator | host request and response handoff contract with request/response manifests, host relay configuration presence flags, and no secret material in uCore |
| `rp_llm_fallback` | LLM relay | privacy, package, consistency, metrics, compare, orchestrator | fallback handling and fallback trace for missing cloud key, network loss, and privacy rejection |
| `rp_privacy` | privacy | release | outbound packet review result |
| `rp_compliance` | privacy | package, release, dossier, metrics, compare, orchestrator | policy compliance result covering access profiles, data use rules, LLM packets, secret placement, and license checks |
| `rp_params` | run configuration | package, metrics, compare, orchestrator | baseline and candidate parameter set summary |
| `rp_runconf` | run configuration | package, release, dossier, metrics, compare, orchestrator | baseline and candidate run configuration profiles |
| `rp_configval` | run configuration | package, metrics, compare, orchestrator | profile validation result with checked items and warnings |
| `rp_configdrift` | run configuration | metrics, compare, orchestrator | baseline and candidate configuration difference summary |
| `rp_execplan` | execution observer | package, dossier, metrics, compare, orchestrator | plain-kernel execution plan, workflow step count, scheduled task count, worker slots, retry items, and LLM packet count |
| `rp_worker` | execution observer | package, metrics, compare, orchestrator | worker health, heartbeat count, queue actions, and failure handling actions |
| `rp_timeline` | execution observer | release, package, dossier, metrics, compare, orchestrator | run timeline, stage order, tick span, and critical path |
| `rp_execobs` | execution observer | release, package, dossier, metrics, compare, orchestrator | observer packet summary connecting execution plan, timeline, worker health, evidence readiness, and host-triggered workflow run observations used by reader-derived workflow execution and control views |
| `rp_invocation` | workflow invocation | package, release, dossier, metrics, compare, orchestrator | invocation identity, template, step count, output count, cache reuse, and final status |
| `rp_steps` | workflow invocation | metrics, compare, orchestrator | step status counts for completed, cached, failed, and recovered stages |
| `rp_attempts` | workflow invocation | package, metrics, compare, orchestrator | attempt count, retry count, worker, cache actions, and final result |
| `rp_invoke_export` | workflow invocation | package, compare, orchestrator | invocation export record |
| `rp_hooks` | workflow completion | compare, orchestrator | completion hook count by action type |
| `rp_completion` | workflow completion | package, release, dossier, metrics, compare, orchestrator | completion event, invocation status, action count, export count, and final status |
| `rp_actions` | workflow completion | metrics, compare, orchestrator | notification, runbook, evidence export, and audit action results |
| `rp_complete_export` | workflow completion | package, compare, orchestrator | completion event export record |
| `rp_input` | artifact operations | package, consistency, UI export, Host Web/API export, test suite, compare, orchestrator | concrete input manifest for RUN-042 plus submitted research-task fields, provider choice, reviewer, uploaded CSV/reference summaries, workspace-import rows, generated workspace template, workspace-run reference, four dynamic submission rows, validation state, and host UI feed hints |
| `rp_input_fastq` | artifact operations | artifact operations | ordinary input data read by the artifact operation program |
| `rp_stage_dag` | artifact operations | package, consistency, compare, orchestrator | stage dependency, cache, failure, and retry record |
| `rp_stage_log` | artifact operations | package, consistency, compare, orchestrator | per-stage execution log for ingest, align, profile, review, and package stages |
| `rp_artifact` | artifact operations | package, consistency, compare, orchestrator | recovered align-stage artifact tied to the concrete input, including normalized FASTQ, alignment table, metrics JSON, gene-count CSV, archive-manifest sections, and provenance rows for workflow, review, and LLM-quality evidence |
| `rp_report_text` | artifact operations and host relay | package, compare, UI export | report text generated from the recovered run plus selected host relay response summary |
| `rp_chart_data` | artifact operations | package, compare, UI export | chart-ready stage attempt data for the host UI |
| `rp_runner` | artifact operations, workflow runner, Studio, workbench, host relay | consistency, compare, orchestrator | plain uCore stage runner summary with retries and cache hits, plus compact research Studio session links, research workbench task state, relay-backed answer summary, workspace inspection/import references, generated template/run references, dynamic input run mapping, human review state, delivery-manifest task state, workbench export reference, workbench/delivery/project-operation scale records, readiness section, cited-answer section, handoff brief section, runbook section, timeline section, and file-manifest section |
| `rp_ingest_files` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | concrete input-file scan result for RUN-042 |
| `rp_dataset_snapshot` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | raw and normalized dataset snapshot summary |
| `rp_data_preview` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | preview rows and columns for FASTQ and sample records |
| `rp_data_quality` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | data-quality rule results for the dataset |
| `rp_data_transform` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | transform records for normalization and sample-sheet join |
| `rp_dataset_collection` | data pipeline | package, consistency, metrics, UI export, compare, orchestrator | final dataset collection tied to input, sample, count, and artifact sources |
| `rp_stage_state` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | executable stage-state table with command, output, dependency-check, and result fields |
| `rp_cache_index` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | cache hit and miss records plus cache policy, reused stage, and refreshed stage |
| `rp_retry_plan` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | retry item for the failed align stage with failure reason, rerun input/output, and skipped stages |
| `rp_run_events` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | run-level event stream with retry decision, report reference, and evidence reference |
| `rp_artifact_manifest` | workflow runner | package, consistency, metrics, UI export, compare, orchestrator | generated artifact manifest for input, intermediate, report, chart, stage-log, package-index, artifact-dossier outputs, and artifact review paths tying raw input, derived artifact, report, review gate, recovery evidence, delivery package, and reader-derived workflow evidence links |
| `rp_agents` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | seven role records for orchestrator, retriever, analyst, reviewer, writer, recovery, and auditor |
| `rp_decisions` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | eight concrete decisions tied to plan, evidence, failure, recovery, report, audit, and comparison records |
| `rp_handoff` | Agent collaboration | package, consistency, metrics, UI export, compare, orchestrator | six role-to-role handoff records with source artifacts |
| `rp_deliberation` | Agent collaboration | package, consistency, compare, orchestrator | discussion items for failure recovery, cache reuse, host relay, evidence quality, and release |
| `rp_agent_run` | Agent collaboration | package, consistency, metrics, compare, orchestrator | Agent collaboration summary for RUN-042 |
| `rp_package` | package and host relay | UI export, Host Web/API export, compare, test suite, orchestrator | packaged artifact summary plus report, evidence, provenance, reviewer delivery, selected relay response file, static review site linkage, workspace-import records, evidence protocol records, PRISMA-style flow records, eight delivery file rows, three delivery checks, delivery manifest JSON/Markdown names, evidence bundle contents, review threads, applied revision changes, action items, and review-page sections |
| `rp_diff` | delta | release, dossier, metrics, compare, orchestrator | release candidate difference summary across report, data, figures, risk, and reproduction evidence |
| `rp_delta` | delta | release, dossier, metrics, compare, orchestrator | release delta review with accepted item count, blocked count, package, risk, and reproduction status |
| `rp_datarel` | package | release, dossier, compare | FAIR data, data product, DOI, and publication readiness |
| `rp_dataver` | package | release, dossier, metrics, compare, orchestrator | data product versions, snapshots, schema versions, and release candidate |
| `rp_repro` | package | release, dossier, compare | environment locks, notebook replay, reproduction checks, and research object crate |
| `rp_release` | release | dossier, compare | release decision from package, audit, privacy, and LLM packet state |
| `rp_dossier` | dossier | compare | final review material summary |
| `rp_reviewboard` | review operations, review dashboard, dossier, package, operations board | compare | formal review board request, votes, signoffs, assignments, workloads, and release decision |
| `rp_control` | approval, notification, queue, plugin, workspace, and access services | compare, reader | platform control plane records for approvals, notifications, run queue, plugin calls, workspace access, saved views, API token references, and AgentOS replacement targets |
| `rp_integrity` | evidence traceability, references, naming, status, and review alignment | compare, reader | integrity plane records for evidence contracts, reference checks, namespace checks, status semantics, review alignment, report sources, package trace, and AgentOS replacement targets |
| `rp_coherence` | delivery, run-state, lifecycle, workflow lint, tool protocol, report validation, and Agent coordination | compare, reader | coherence plane records for delivery contracts, run-state checks, lifecycle checks, workflow lint, tool protocol checks, report validation checks, Agent coordination checks, and AgentOS replacement targets |
| `rp_reviewops` | dossier | compare | review board, vote, risk, mitigation, and governance result |
| `rp_submit` | dossier | compare | journal target, cover letter, data availability, and review response package |
| `rp_sreg` | service surface | consistency, metrics, UI export, Web/API export, compare, orchestrator | sample registry with sample, aliquot, cohort, and custody counts |
| `rp_ethics` | service surface | consistency, test suite, compare, orchestrator | ethics, consent, privacy, and deidentification review record |
| `rp_access` | service surface | consistency, test suite, compare, orchestrator | data access request result with approved and denied requests |
| `rp_cohort` | service surface | consistency, test suite, compare, orchestrator | cohort balance and annotation summary |
| `rp_bioop` | service surface | consistency, test suite, orchestrator | bio service operation record |
| `rp_instr` | service surface | consistency, metrics, UI export, Web/API export, compare, orchestrator | instrument registry with readiness and maintenance count |
| `rp_invent` | service surface | consistency, compare, orchestrator | inventory item, reservation, reagent, and transaction summary |
| `rp_procure` | service surface | consistency, compare, orchestrator | procurement requests, vendors, orders, receipts, and budget state |
| `rp_ressched` | service surface | consistency, compare, orchestrator | resource booking, conflict, training, and ready-slot summary |
| `rp_labresop` | service surface | consistency, test suite, orchestrator | lab resource operation record |
| `rp_resrev` | service surface | consistency, metrics, UI export, Web/API export, compare, orchestrator | result review item count and acceptance status |
| `rp_publication` | publication workflow | test suite, compare, Host reader, orchestrator | journal targets, submissions, peer review rounds, revision tasks, response packages, response items, and publication decisions |
| `rp_mature` | mature platform mapping | test suite, compare, Host reader, orchestrator | Galaxy, AiiDA, DVC, MLflow, Nextflow, and Snakemake capability profiles mapped onto local platform state and AgentOS target services |
| `rp_mature_refs` | mature platform mapping | test suite, compare, Host reader | reference profile rows for the six mature research platforms |
| `rp_mature_map` | mature platform mapping | test suite, compare, Host reader | capability mapping rows and planned AgentOS target mechanisms such as kernel context, metadata index, event queue, batch runner, and capability contract table |
| `rp_mature_checks` | mature platform mapping | test suite, compare, Host reader | profile, state-store, surface, ratio, and AgentOS adaptation checks for the mature-platform mapping |
| `rp_pubplan` | service surface | consistency, compare, orchestrator | publication target, figure, section, data availability, and code availability plan |
| `rp_peerresp` | service surface | consistency, compare, orchestrator | peer-review response package summary |
| `rp_fairpkg` | service surface | consistency, compare, orchestrator | FAIR package checks and DOI record |
| `rp_pubop` | service surface | consistency, test suite, orchestrator | publication operation record |
| `rp_litrev` | service surface | consistency, compare, orchestrator | systematic review search strategy, screening decisions, evidence extractions, protocol reference, PRISMA-style flow, synthesis, inclusion, and risk-of-bias summary |
| `rp_citegraph` | service surface | consistency, compare, orchestrator | citation graph and BibTeX integrity summary |
| `rp_semindex` | service surface | consistency, metrics, UI export, Web/API export, compare, orchestrator | semantic index with document, chunk, entity, relation, and tag counts |
| `rp_kanswers` | service surface | consistency, compare, orchestrator | grounded knowledge answer records |
| `rp_knowop` | service surface | consistency, test suite, orchestrator | knowledge operation record |
| `rp_runenv` | service surface | consistency, metrics, UI export, Web/API export, compare, orchestrator | runtime environment locks, validation, host relay mode, and secret-value count |
| `rp_nbexec` | service surface and notebook export | consistency, metrics, UI export, Web/API export, test suite, compare, orchestrator | executable notebook replay summary plus compact notebook cell, execution, output, and export records |
| `rp_repro` | package and notebook export | release, dossier, compare, test suite, orchestrator | reproduction checks plus reproducible-notebook download reference |
| `rp_eln` | service surface | consistency, compare, orchestrator | ELN entries, signatures, attachments, and integrity checks |
| `rp_wpool` | service surface | consistency, compare, orchestrator | worker pool, worker, heartbeat, slot, and queue-depth summary |
| `rp_runop` | service surface | consistency, test suite, orchestrator | runtime operation record |
| `rp_agentcmp` | AgentCompare metrics | compare | plain-kernel comparison counters |
| `rp_backend` | backend scenario | compare, orchestrator | same-workflow backend scenario case count and planned Agent-OS cases |
| `rp_backend_exec` | backend scenario | compare, orchestrator | backend scenario execution result for executable and planned cases, including content checks, attempts, retry reasons, tick observations, source/requirement/observation/action/review rows, and plain-cost/AgentOS-replacement/risk rows |
| `rp_study` | backend scenario | compare, orchestrator | same-workflow backend study summary with plain-uCore detail checks and planned Agent-OS kernel verification |
| `rp_consistency` | consistency checker | metrics, compare, orchestrator | derived checks across task records, LLM packets, relay protocol files, workflow invocation, completion hooks, backend cases, runner artifacts, dynamic input records, workbench handoff sections, workflow runner execution files, and service surface records |
| `rp_ui_home` | UI export | compare, orchestrator | home page data for the host web service with navigation and primary cards |
| `rp_ui_run` | UI export | compare, orchestrator | run-detail page data for RUN-042 with timeline rows, artifact preview entries, and revision-delta reference |
| `rp_ui_agent` | UI export | compare, orchestrator | Agent-detail page data for role messages, decisions, and decision rows |
| `rp_ui_evidence` | UI export | compare, orchestrator | evidence-detail page data with stage log, recovered artifact links, and preview files |
| `rp_ui_compare` | UI export | compare, orchestrator | comparison page data for plain-kernel pain points and metric rows |
| `rp_web_routes` | Host Web/API export | test suite, compare, orchestrator | route manifest for sixteen host-rendered GET views and forty-nine POST action entries |
| `rp_api_home` | Host Web/API export | test suite, compare, orchestrator | API payload for the host web home page, including dynamic input queue references and reader contract reference |
| `rp_api_run` | Host Web/API export and host relay | test suite, compare, orchestrator | API payload for RUN-042 run detail with runner execution files, request form, uploaded files, dynamic input queue, live-update feed, reader contract reference, relay report summary, workbench state, workspace-import reference, reusable source selection, bibliography, citation plan, evidence protocol summary, delivery manifest details, evidence bundle link, review page, human review records, revision-task records, revision-delta reference, and custom research run reference |
| `rp_api_agents` | Host Web/API export | test suite, compare, orchestrator | API payload for role messages, decisions, and handoffs |
| `rp_api_evidence` | Host Web/API export | test suite, compare, orchestrator | API payload for claims, provenance paths, literature search, screening decisions, evidence protocol, PRISMA-style flow, synthesis, stage log, artifact, manifest, and LLM guard |
| `rp_api_compare` | Host Web/API export | test suite, compare, orchestrator | API payload for plain-kernel comparison signals |
| `rp_api_artifacts` | Host Web/API export | test suite, compare, orchestrator | API payload for input, stage, manifest, report, chart, LLM relay, delivery file rows, delivery checks, evidence bundle contents, review page, and raw download records |
| `rp_api_data` | Host Web/API export | test suite, compare, orchestrator | API payload for input-file scan, dynamic input queue, dataset snapshots, previews, quality, transforms, and collection records |
| `rp_api_bio` | Host Web/API export | test suite, compare, orchestrator | API payload for sample registry, ethics, data access, and cohort service files |
| `rp_api_labres` | Host Web/API export | test suite, compare, orchestrator | API payload for instruments, inventory, procurement, and resource scheduling |
| `rp_api_pub` | Host Web/API export | test suite, compare, orchestrator | API payload for result review, publication plan, peer response, and FAIR package |
| `rp_api_know` | Host Web/API export | test suite, compare, orchestrator | API payload for literature review, citation graph, semantic index, knowledge answers, evidence protocols, evidence extractions, and PRISMA-style flow records |
| `rp_api_runtime` | Host Web/API export | test suite, compare, orchestrator | API payload for runtime environment, notebook replay, ELN, and worker pool files |
| `rp_api_action` | Host Web/API export | test suite, compare, orchestrator | action contract for Studio launch, host workflow run/export/stage/cache/retry/artifact/report, Host LLM Relay request/response/fallback, AgentCompare run, custom research run, dynamic research submission, reusable source lookup, custom research export, human review, revision-task creation, and revised-run execution |
| `rp_actionio` | Host Web/API export | test suite, compare, orchestrator | compact request, response, redirect, host export, human-review, revision-task, applied-change, revised-run, AgentCompare, workbench advance, notebook download, bundle download, review-page action records, and reader action-output links |
| `rp_uresrun` | Host Web/API export | test suite, compare, orchestrator | usable research run, revised-run, workbench reference, and export result record derived from the request form, uploaded files, compact dataset, and runner output |
| `rp_studio` | Host Web/API export and Studio | test suite, compare, orchestrator | usable research Studio sessions, material summaries, workbench/project/download links, and seeded host Studio action results |
| `rp_web_bundle` | Host Web/API export | test suite, compare, orchestrator | bundle summary tying routes, API payloads, POST action payloads, action validation, side-effect records, static review site records, UI pages, UI render sections, artifact preview entries, dynamic input queue, live-update feed, reader contract, host refresh file order, Studio state, workbench state, project delivery review records, workspace-import records, reusable source selection, evidence protocol records, delivery file rows, delivery checks, evidence bundle entries, review page, package export indexes, revision-delta reference, runner files, custom research fields, research service files, and relay files together |
| `rp_calculation` | calculation service | test suite, compare, Host reader, orchestrator | AiiDA-style computer, code, calculation job, scheduler record, output snapshot, check count, and AgentOS replacement fields for the RUN-042 quality-control calculation |
| `rp_calc_files` | calculation service | test suite, compare, Host reader, orchestrator | retrieved stdout, result, and provenance files for the calculation job |
| `rp_calc_parse` | calculation service | test suite, compare, Host reader, orchestrator | parser result, extracted metrics, ready ratio, and parser status for the retrieved calculation outputs |
| `rp_calc_export` | calculation service | test suite, compare, Host reader, orchestrator | calculation export package, checksum, and Host Reader calculation page linkage |
| `rp_realtask` | real task validation | test suite, compare, Host reader, orchestrator | Palmer Penguins research task summary, source files, DeepSeek-backed workbench delivery, answer audit, and project bundle readiness |
| `rp_realdata` | real task validation | test suite, compare, Host reader, orchestrator | 344-row Palmer Penguins CSV shape, numeric field count, group summaries, categorical fields, and data-quality result |
| `rp_realreport` | real task validation | test suite, compare, Host reader, orchestrator | report source, provider trace, claim audit, answer audit, limitations, and citation count for the real task |
| `rp_realbundle` | real task validation | test suite, compare, Host reader, orchestrator | project bundle, duplicate-entry check, package file list, offline review readiness, and HTTP check count |
| `rp_campaign` | experiment campaign service | test suite, compare, Host reader, orchestrator | RUN-042 parameter-grid campaign summary, trial count, selected trial, and result-review decision |
| `rp_trials` | experiment campaign service | test suite, compare, Host reader, orchestrator | four materialized trial rows for memory/thread parameters, trial metrics, and selected-trial status |
| `rp_camp_rank` | experiment campaign service | test suite, compare, Host reader, orchestrator | ranked trial comparison, best trial, metric delta, and selection decision |
| `rp_resreview` | experiment campaign service | test suite, compare, Host reader, orchestrator | baseline-vs-candidate result review, parameter changes, artifact links, and accept/reject decision |
| `rp_reldossier` | release dossier service | test suite, compare, Host reader, orchestrator | final review package summary that collects research package, governance, publication, data release, experiment campaign, execution evidence, and AgentOS-readiness sections |
| `rp_reldsec` | release dossier service | test suite, compare, Host reader, orchestrator | seven section rows for the final dossier, including experiment campaign and AgentOS-readiness evidence |
| `rp_relattest` | release dossier service | test suite, compare, Host reader, orchestrator | four final attestation rows covering package, governance, execution, and release-readiness checks |
| `rp_relpack` | release dossier service | test suite, compare, Host reader, orchestrator | package download handle and release dossier file count for the Host Reader |
| `rp_prov_view` | provenance view | compare, test suite, Host reader, orchestrator | timeline views, subgraphs, evidence packets, and AgentOS replacement targets for kernel timeline, kernel provenance edges, kernel ledger, and Context detail |
| `rp_prov_query` | provenance query | compare, test suite, Host reader, orchestrator | saved provenance queries, reusable template, execution records, comparison, export, evidence packet, and AgentOS replacement targets for kernel timeline query, provenance snapshot, ledger snapshot, and Context detail |
| `rp_prov_specs` | provenance query | compare, test suite, Host reader, orchestrator | query template and three saved query specifications for RUN-042 lineage review |
| `rp_prov_exec` | provenance query | compare, test suite, Host reader, orchestrator | three provenance query executions plus rendered/direct comparison and export rows |
| `rp_prov_query_pkg` | provenance query | compare, test suite, Host reader, orchestrator | reviewer packet linking query comparison, executions, graph size, checksum, and package readiness |
| `rp_prov_edges` | provenance view | compare, test suite, Host reader, orchestrator | compact provenance subgraph linking execution, artifact, package, and Agent records to the reader view |
| `rp_evidence_packet` | provenance view | compare, test suite, Host reader, orchestrator | reviewer-facing evidence packet rows for report, artifact, agent decision, and kernel timeline evidence |
| `rp_timeline_view` | provenance view | compare, test suite, Host reader, orchestrator | run, workflow, agent, and kernel timeline slices exposed to the reader |
| `rp_tests` | standalone test suite | direct `rp_test_suite` run | 1904 user-space checks over catalog, data pipeline, service surface records, calculation job records, real task validation records, experiment campaign records, release dossier records, mature platform mapping records, provenance view, provenance query records, evidence packet records, lab governance operations, workflow, workflow portability records, workflow portability delivery checks, workflow portability to backend execution checks, backend runner case checks, backend runner detail checks, source/requirement/observation/action/review rows, plain-cost/AgentOS-replacement/risk rows, backend evidence links into `rp_runner`, `rp_report_text`, and review handoff state, migration plans, rehearsal cases, object naming, surface reachability, status semantics, references, evidence trace, integrity plane checks, coherence plane checks, publication workflow checks, run-state explanation, lifecycle order, delivery consistency, AgentOS readiness, static review site records, derived FASTQ/alignment/metrics/count/archive files, artifact dossier records, artifact provenance records, artifact path rebuild checks, dossier checks, artifacts, package export indexes, delivery file rows, delivery checks, delivery manifest names, evidence bundle contents, review page, review dashboard, review handoff checks, reviewer evidence package, human review records, requested-change records, applied revision changes, revision-task records, review thread records, review comment records, action item records, UI render data, workflow runner files, workflow runner detail fields, custom research fields, dynamic input records, host UI feed records, host reader contract records, workbench task state, workbench readiness, cited answer, handoff brief, continuation runbook, timeline, file manifest, workspace-import records, reusable source selection, bibliography, citation plan, literature search, screening decisions, evidence extraction, evidence protocol, PRISMA-style flow, Agent collaboration, UI data, Host Web/API export files, POST action records, active action records, action validation records, action side-effect records, LLM relay queue validation, LLM delivery checks, LLM transcript scale records, workbench delivery records, route decisions, packet schema checks, guard checks, fallback decisions, request/packet/response matching, knowledge-index records, AgentCompare, and consistency records. The main orchestrated run publishes the same test count through `rp_agentcmp` to avoid consuming another inode in the teaching file-system image. |
| `rp_agentcmp` | AgentCompare metrics and compare | compare, orchestrator | plain-kernel execution summary, relay quality evidence, and comparison counters |

This is intentionally implemented without new syscalls. It uses only `open`, `read`, `write`, `close`, `fork`, `exec`, and `waitpid`.

## Upstream Kernel Guarantee

The `os` and `scripts` directories are copied from the upstream uCore 2025S source. The `nfs` builder keeps the same disk format and uses a 4096-block image for platform state files. Kernel verification uses directory comparison on `os` and `scripts` rather than source comments.

The only implementation changes needed for the first native platform step are in ordinary user-space files:

- `user/src/rp_plain.c`
- `user/src/rp_orch.c`
- `user/src/rp_seed_orch.c`
- `user/src/rp_*.c`
- `user/include/research_platform_state.h`
- `user/Makefile`
- `user/src/usershell.c`

`usershell.c` only changes character constants so it builds with the available GNU RISC-V toolchain.

## Compatibility With Later Agent-OS Version

The native program keeps stable object names, role names, capability names, and output lines. The later Agent-OS version can replace in-process tables with kernel-assisted Context Path, tool calls, file metadata, Agent events, and LLM gateway requests while preserving the same demonstration contract.

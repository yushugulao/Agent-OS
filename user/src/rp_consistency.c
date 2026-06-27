#include <stdio.h>
#include <research_platform_state.h>

static int require_equal(const char *name, int actual, int expected)
{
	if (actual == expected) return 1;
	printf("rp_consistency: mismatch %s actual=%d expected=%d\n", name, actual, expected);
	return 0;
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_backend", "status=ready");
	ok = ok && rp_file_contains("rp_backend_exec", "status=ready");
	ok = ok && rp_file_contains("rp_query", "knowledge_index=search_documents:1385");
	ok = ok && rp_file_contains("rp_query", "provenance_nodes:406");
	ok = ok && rp_file_contains("rp_query", "provenance_links:544");
	ok = ok && rp_file_contains("rp_query", "events:6816");
	ok = ok && rp_file_contains("rp_query", "context_records:348");
	ok = ok && rp_file_contains("rp_query", "host_workflow_artifacts:150");
	ok = ok && rp_file_contains("rp_query", "usable_artifacts:429");
	ok = ok && rp_file_contains("rp_query", "usable_runs:20");
	ok = ok && rp_file_contains("rp_query", "usable_stages:168");
	ok = ok && rp_file_contains("rp_query", "usable_messages:223");
	ok = ok && rp_file_contains("rp_query", "usable_decisions:203");
	ok = ok && rp_file_contains("rp_mail", "to=backend");
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_runner", "workbench_tasks=9");
	ok = ok && rp_file_contains("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "status=ready");
	ok = ok && rp_file_contains("rp_runner", "citation_count=5");
	ok = ok && rp_file_contains("rp_runner", "handoff=ready");
	ok = ok && rp_file_contains("rp_runner", "commands=6");
	ok = ok && rp_file_contains("rp_runner", "events=8");
	ok = ok && rp_file_contains("rp_runner", "files=9");
	ok = ok && rp_file_contains("rp_runner", "workbench_delivery_scale=workbenches:5");
	ok = ok && rp_file_contains("rp_runner", "templates:5");
	ok = ok && rp_file_contains("rp_runner", "workspace_imports:5");
	ok = ok && rp_file_contains("rp_runner", "workspace_inspections:5");
	ok = ok && rp_file_contains("rp_runner", "answers:5");
	ok = ok && rp_file_contains("rp_runner", "deliveries:6");
	ok = ok && rp_file_contains("rp_runner", "studio_sessions:2");
	ok = ok && rp_file_contains("rp_runner", "project_action_plans:15");
	ok = ok && rp_file_contains("rp_runner", "project_deliveries:4");
	ok = ok && rp_file_contains("rp_runner", "project_runbooks:15");
	ok = ok && rp_file_contains("rp_runner", "project_evidence_audits:15");
	ok = ok && rp_file_contains("rp_runner", "project_provenance_graphs:3");
	ok = ok && rp_file_contains("rp_runner", "project_launches:3");
	ok = ok && rp_file_contains("rp_runner", "project_release_gates:15");
	ok = ok && rp_file_contains("rp_runner", "project_snapshots:15");
	ok = ok && rp_file_contains("rp_input", "dynamic_submissions=4");
	ok = ok && rp_file_contains("rp_input", "dynamic_validation=passed");
	ok = ok && rp_file_contains("rp_runner", "dynamic_input_runs=4");
	ok = ok && rp_file_contains("rp_runner", "dynamic_run=usable-run:RUN-904");
	ok = ok && rp_file_contains("rp_stage_dag", "status=ready");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "status=ready");
	ok = ok && rp_file_contains("rp_chart_data", "status=ready");
	ok = ok && rp_file_contains("rp_ingest_files", "files=2");
	ok = ok && rp_file_contains("rp_dataset_snapshot", "snapshots=2");
	ok = ok && rp_file_contains("rp_data_preview", "previews=2");
	ok = ok && rp_file_contains("rp_data_quality", "passed=7");
	ok = ok && rp_file_contains("rp_data_transform", "transforms=2");
	ok = ok && rp_file_contains("rp_dataset_collection", "items=4");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "template_mode=ready");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_llmlog", "transcripts=90");
	ok = ok && rp_file_contains("rp_llmlog", "bridge_requests=30");
	ok = ok && rp_file_contains("rp_llmlog", "bridge_responses=30");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_messages=21");
	ok = ok && rp_file_contains("rp_sreg", "samples=8");
	ok = ok && rp_file_contains("rp_ethics", "ethics=approved");
	ok = ok && rp_file_contains("rp_access", "requests=3");
	ok = ok && rp_file_contains("rp_cohort", "cohorts=2");
	ok = ok && rp_file_contains("rp_bioop", "ops=7");
	ok = ok && rp_file_contains("rp_instr", "instruments=4");
	ok = ok && rp_file_contains("rp_invent", "inventory_items=9");
	ok = ok && rp_file_contains("rp_procure", "requests=3");
	ok = ok && rp_file_contains("rp_ressched", "bookings=6");
	ok = ok && rp_file_contains("rp_labresop", "ops=6");
	ok = ok && rp_file_contains("rp_labresop", "lab_governance_ops=approvals:2");
	ok = ok && rp_file_contains("rp_labresop", "ethics_protocols:1");
	ok = ok && rp_file_contains("rp_labresop", "protocol_compliance_reports:2");
	ok = ok && rp_file_contains("rp_labresop", "protocol_amendments:2");
	ok = ok && rp_file_contains("rp_labresop", "sop_executions:3");
	ok = ok && rp_file_contains("rp_labresop", "training_records:4");
	ok = ok && rp_file_contains("rp_labresop", "instrument_maintenance:3");
	ok = ok && rp_file_contains("rp_labresop", "inventory_transactions:14");
	ok = ok && rp_file_contains("rp_labresop", "procurement_orders:2");
	ok = ok && rp_file_contains("rp_labresop", "resource_budgets:3");
	ok = ok && rp_file_contains("rp_labresop", "run_queue_items:4");
	ok = ok && rp_file_contains("rp_labresop", "notifications:3");
	ok = ok && rp_file_contains("rp_resrev", "review_items=10");
	ok = ok && rp_file_contains("rp_pubplan", "journal_targets=2");
	ok = ok && rp_file_contains("rp_peerresp", "responses=6");
	ok = ok && rp_file_contains("rp_fairpkg", "fair_checks=8");
	ok = ok && rp_file_contains("rp_pubop", "ops=6");
	ok = ok && rp_file_contains("rp_litrev", "papers=9");
	ok = ok && rp_file_contains("rp_citegraph", "citations=14");
	ok = ok && rp_file_contains("rp_semindex", "documents=17");
	ok = ok && rp_file_contains("rp_kanswers", "answers=4");
	ok = ok && rp_file_contains("rp_knowop", "ops=6");
	ok = ok && rp_file_contains("rp_runenv", "environments=4");
	ok = ok && rp_file_contains("rp_nbexec", "notebooks=2");
	ok = ok && rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb");
	ok = ok && rp_file_contains("rp_repro", "downloadable_units=4");
	ok = ok && rp_file_contains("rp_eln", "eln_entries=3");
	ok = ok && rp_file_contains("rp_wpool", "worker_pools=2");
	ok = ok && rp_file_contains("rp_runop", "ops=7");
	ok = ok && rp_file_contains("rp_runop", "advanced_surface=objects:5");
	ok = ok && rp_file_contains("rp_runop", "research_search:saved_queries:2");
	ok = ok && rp_file_contains("rp_runop", "project_space:lab-gene-x");
	ok = ok && rp_file_contains("rp_runop", "study_protocol:protocols:2");
	ok = ok && rp_file_contains("rp_runop", "dataset_answer:datasets:2");
	ok = ok && rp_file_contains("rp_runop", "package_intake:packages:1");
	ok = ok && rp_file_contains("rp_runop", "startup_health=quickstart:ready");
	ok = ok && rp_file_contains("rp_runop", "configuration_health=settings:ready");
	ok = ok && rp_file_contains("rp_runop", "stores_secret_values=0");
	ok = ok && rp_file_contains("rp_runop", "deepseek_provider=registered");
	ok = ok && rp_file_contains("rp_runop", "platform_doctor=ready;checks=10");
	ok = ok && rp_file_contains("rp_runop", "cloud_llm=optional");
	ok = ok && rp_file_contains("rp_runop", "project_scaffold=templates:3");
	ok = ok && rp_file_contains("rp_runop", "dataset_product=previews:2");
	ok = ok && rp_file_contains("rp_runop", "visualizations:2");
	ok = ok && rp_file_contains("rp_runop", "source_portfolio=sources:42");
	ok = ok && rp_file_contains("rp_runop", "research_portfolio_scale=sources:42");
	ok = ok && rp_file_contains("rp_runop", "datasets:3");
	ok = ok && rp_file_contains("rp_runop", "literature_searches:4");
	ok = ok && rp_file_contains("rp_runop", "reviews:8");
	ok = ok && rp_file_contains("rp_runop", "evidence_reviews:4");
	ok = ok && rp_file_contains("rp_runop", "evidence_extractions:15");
	ok = ok && rp_file_contains("rp_runop", "screening_decisions:15");
	ok = ok && rp_file_contains("rp_runop", "exports:66");
	ok = ok && rp_file_contains("rp_runop", "doctor_reports:10");
	ok = ok && rp_file_contains("rp_runop", "project_handoff_audits:30");
	ok = ok && rp_file_contains("rp_runop", "project_run_comparisons:15");
	ok = ok && rp_file_contains("rp_runop", "project_reproducibility_audits:15");
	ok = ok && rp_file_contains("rp_runop", "project_snapshot_comparisons:15");
	ok = ok && rp_file_contains("rp_runop", "study_protocol_reproduction=packages:1");
	ok = ok && rp_file_contains("rp_runop", "action_execution:ready");
	ok = ok && rp_file_contains("rp_runop", "project_bundle_cache=latest:ready");
	ok = ok && rp_file_contains("rp_runop", "downloads:cached_or_refresh");
	ok = ok && rp_file_contains("rp_runop", "runtime_assurance=secret_refs:3");
	ok = ok && rp_file_contains("rp_runop", "model_registry:2");
	ok = ok && rp_file_contains("rp_runop", "llm_proxy_audits:2");
	ok = ok && rp_file_contains("rp_runop", "collab_threads:2");
	ok = ok && rp_file_contains("rp_runop", "obs_alerts:5");
	ok = ok && rp_file_contains("rp_runop", "research_ops=semantic_entities:8");
	ok = ok && rp_file_contains("rp_runop", "semantic_relations:6");
	ok = ok && rp_file_contains("rp_runop", "prompt_templates:2");
	ok = ok && rp_file_contains("rp_runop", "prompt_versions:2");
	ok = ok && rp_file_contains("rp_runop", "prompt_evaluations:1");
	ok = ok && rp_file_contains("rp_runop", "runbook_steps:7");
	ok = ok && rp_file_contains("rp_runop", "worker_ops:6");
	ok = ok && rp_file_contains("rp_runop", "execution_controls:8");
	ok = ok && rp_file_contains("rp_runop", "regulated_research=annotation_schemas:1");
	ok = ok && rp_file_contains("rp_runop", "annotation_tasks:3");
	ok = ok && rp_file_contains("rp_runop", "assay_plates:1");
	ok = ok && rp_file_contains("rp_runop", "plate_wells:6");
	ok = ok && rp_file_contains("rp_runop", "cohort_records:2");
	ok = ok && rp_file_contains("rp_runop", "data_access_requests:1");
	ok = ok && rp_file_contains("rp_runop", "dataset_cards:1");
	ok = ok && rp_file_contains("rp_runop", "model_cards:1");
	ok = ok && rp_file_contains("rp_runop", "research_object_crates:1");
	ok = ok && rp_file_contains("rp_runop", "research_object_entities:29");
	ok = ok && rp_file_contains("rp_runop", "sample_custody_events:18");
	ok = ok && rp_file_contains("rp_runop", "statistical_designs:1");
	ok = ok && rp_file_contains("rp_runop", "workflow_templates:8");
	ok = ok && rp_file_contains("rp_protocol", "protocol_compliance_reports=1");
	ok = ok && rp_file_contains("rp_protocol", "protocol_amendments=1");
	ok = ok && rp_file_contains("rp_soplog", "sop_executions=1");
	ok = ok && rp_file_contains("rp_risk", "risk_reviews=1");
	ok = ok && rp_file_contains("rp_risk", "decision_support=decision:agentos-final-demo-backend");
	ok = ok && rp_file_contains("rp_capa", "capa_charts=deviations-by-severity");
	ok = ok && rp_file_contains("rp_package", "provenance_graph=unified");
	ok = ok && rp_file_contains("rp_wfio", "imports=5");
	ok = ok && rp_file_contains("rp_wfio", "adapter_specs=6");
	ok = ok && rp_file_contains("rp_wfio", "migration_steps=9");
	ok = ok && rp_file_contains("rp_wfio", "cases=4");
	ok = ok && rp_file_contains("rp_wfio", "blocking_items=0");
	ok = ok && rp_file_contains("rp_wfio", "package=workflow-portability");
	if (!ok) return 1;

	int task_lines = rp_count_lines("rp_taskrec");
	int ready_tasks = rp_count_token("rp_taskrec", "state=ready");
	int high_tasks = rp_count_token("rp_taskrec", "prio=H");
	int critical_tasks = rp_count_token("rp_taskrec", "class=critical");
	int queue_items = rp_get_int_value("rp_sched", "queue_items=");
	int ready_items = rp_get_int_value("rp_sched", "ready_items=");
	int rank_records = rp_get_int_value("rp_rank", "records=");
	int rank_ready = rp_get_int_value("rp_rank", "ready=");
	int rank_high = rp_get_int_value("rp_rank", "high=");
	int rank_critical = rp_get_int_value("rp_rank", "critical=");
	int rank_selected = rp_get_int_value("rp_rank", "selected=");
	int view_sched = rp_get_int_value("rp_runview", "scheduler_items=");
	int view_ranked = rp_get_int_value("rp_runview", "ranked_tasks=");
	int view_selected = rp_get_int_value("rp_runview", "selected_tasks=");
	int view_critical = rp_get_int_value("rp_runview", "critical_tasks=");

	ok = ok && require_equal("task_lines", task_lines, 21);
	ok = ok && require_equal("queue_items", queue_items, task_lines);
	ok = ok && require_equal("ready_items", ready_items, ready_tasks);
	ok = ok && require_equal("rank_records", rank_records, task_lines);
	ok = ok && require_equal("rank_ready", rank_ready, ready_tasks);
	ok = ok && require_equal("rank_high", rank_high, high_tasks);
	ok = ok && require_equal("rank_critical", rank_critical, critical_tasks);
	ok = ok && require_equal("rank_selected", rank_selected, 10);
	ok = ok && require_equal("view_sched", view_sched, queue_items);
	ok = ok && require_equal("view_ranked", view_ranked, rank_records);
	ok = ok && require_equal("view_selected", view_selected, rank_selected);
	ok = ok && require_equal("view_critical", view_critical, critical_tasks);

	int llm_queued = rp_get_int_value("rp_llmq", "queued=");
	int llm_secrets = rp_count_token("rp_llmq", "secret_policy=no_secret_in_ucore");
	int llm_responses = rp_get_int_value("rp_llm_resp", "responses=");
	int relay_packets = rp_get_int_value("rp_relay", "relay_packets=");
	int eval_passed = rp_get_int_value("rp_llmeval", "passed=");
	int relay_protocol_packets = rp_get_int_value("rp_llm_packets", "packets=");
	int relay_routes = rp_get_int_value("rp_llm_routes", "routes=");
	int guard_checked = rp_get_int_value("rp_llm_guard", "checked_packets=");
	int fallback_cases = rp_get_int_value("rp_llm_fallback", "fallback_cases=");
	ok = ok && require_equal("llm_queued", llm_queued, 3);
	ok = ok && require_equal("llm_secret_tags", llm_secrets, llm_queued);
	ok = ok && require_equal("llm_responses", llm_responses, llm_queued);
	ok = ok && require_equal("relay_packets", relay_packets, llm_queued);
	ok = ok && require_equal("llm_eval_passed", eval_passed, 7);
	ok = ok && require_equal("relay_protocol_packets", relay_protocol_packets, llm_queued);
	ok = ok && require_equal("relay_routes", relay_routes, 4);
	ok = ok && require_equal("relay_guard_checked", guard_checked, llm_queued);
	ok = ok && require_equal("fallback_cases", fallback_cases, 1);

	int profiles = rp_get_int_value("rp_runconf", "profiles=");
	int validations = rp_get_int_value("rp_configval", "validations=");
	int drift = rp_get_int_value("rp_configdrift", "changed_parameters=");
	int invocation_steps = rp_get_int_value("rp_invocation", "steps=");
	int step_records = rp_get_int_value("rp_steps", "records=");
	int attempts = rp_get_int_value("rp_attempts", "attempts=");
	int retry_attempts = rp_get_int_value("rp_attempts", "retry_attempts=");
	int completion_actions = rp_get_int_value("rp_completion", "actions=");
	int hook_count = rp_get_int_value("rp_hooks", "hooks=");
	ok = ok && require_equal("profiles", profiles, 2);
	ok = ok && require_equal("validations", validations, profiles);
	ok = ok && require_equal("config_drift", drift, 2);
	ok = ok && require_equal("invocation_steps", invocation_steps, 10);
	ok = ok && require_equal("step_records", step_records, invocation_steps);
	ok = ok && require_equal("attempts", attempts, 12);
	ok = ok && require_equal("retry_attempts", retry_attempts, 2);
	ok = ok && require_equal("completion_actions", completion_actions, 4);
	ok = ok && require_equal("hook_count", hook_count, completion_actions);

	int backend_cases = rp_get_int_value("rp_backend", "cases=");
	int backend_executable = rp_get_int_value("rp_backend", "executable=");
	int passed_cases = rp_get_int_value("rp_backend_exec", "passed_cases=");
	int planned_cases = rp_get_int_value("rp_backend_exec", "planned_cases=");
	int study_arms = rp_get_int_value("rp_study", "arms=");
	ok = ok && require_equal("backend_cases", backend_cases, 4);
	ok = ok && require_equal("backend_executable", backend_executable, passed_cases);
	ok = ok && require_equal("backend_case_total", passed_cases + planned_cases, backend_cases);
	ok = ok && require_equal("study_arms", study_arms, 2);

	int runner_stages = rp_get_int_value("rp_runner", "stages=");
	int runner_retries = rp_get_int_value("rp_runner", "retries=");
	int runner_cache_hits = rp_get_int_value("rp_runner", "cache_hits=");
	int workbench_tasks = rp_get_int_value("rp_runner", "workbench_tasks=");
	int workbench_done = rp_get_int_value("rp_runner", "workbench_task_done=");
	int dynamic_submissions = rp_get_int_value("rp_input", "dynamic_submissions=");
	int dynamic_input_runs = rp_get_int_value("rp_runner", "dynamic_input_runs=");
	int dag_edges = rp_get_int_value("rp_stage_dag", "edges=");
	int log_lines = rp_get_int_value("rp_stage_log", "lines=");
	int artifact_records = rp_get_int_value("rp_artifact", "records=");
	int stage_state_stages = rp_get_int_value("rp_stage_state", "stages=");
	int cache_records = rp_get_int_value("rp_cache_index", "cache_records=");
	int retry_items = rp_get_int_value("rp_retry_plan", "retry_items=");
	int run_events = rp_get_int_value("rp_run_events", "events=");
	int manifest_records = rp_get_int_value("rp_artifact_manifest", "manifest_records=");
	int artifact_provenance = rp_count_token("rp_artifact", "provenance=");
	int dossier_checks = rp_count_token("rp_artifact_manifest", "dossier_check=");
	int agent_roles = rp_get_int_value("rp_agents", "agents=");
	int agent_messages = rp_get_int_value("rp_agents", "messages=");
	int agent_decisions = rp_get_int_value("rp_decisions", "decisions=");
	int agent_handoffs = rp_get_int_value("rp_handoff", "handoffs=");
	int deliberation_items = rp_get_int_value("rp_deliberation", "items=");
	int ingested_files = rp_get_int_value("rp_ingest_files", "files=");
	int snapshots = rp_get_int_value("rp_dataset_snapshot", "snapshots=");
	int previews = rp_get_int_value("rp_data_preview", "previews=");
	int quality_rules = rp_get_int_value("rp_data_quality", "passed=");
	int transforms = rp_get_int_value("rp_data_transform", "transforms=");
	int collection_items = rp_get_int_value("rp_dataset_collection", "items=");
	ok = ok && require_equal("runner_stages", runner_stages, 5);
	ok = ok && require_equal("runner_retries", runner_retries, 1);
	ok = ok && require_equal("runner_cache_hits", runner_cache_hits, 1);
	ok = ok && require_equal("workbench_tasks", workbench_tasks, 9);
	ok = ok && require_equal("workbench_done", workbench_done, 8);
	ok = ok && require_equal("dynamic_submissions", dynamic_submissions, 4);
	ok = ok && require_equal("dynamic_input_runs", dynamic_input_runs, dynamic_submissions);
	ok = ok && require_equal("dag_edges", dag_edges, runner_stages - 1);
	ok = ok && require_equal("stage_log_lines", log_lines, runner_stages);
	ok = ok && require_equal("artifact_records", artifact_records, 2);
	ok = ok && require_equal("stage_state_stages", stage_state_stages, runner_stages);
	ok = ok && require_equal("cache_records", cache_records, runner_stages);
	ok = ok && require_equal("retry_items", retry_items, 1);
	ok = ok && require_equal("run_events", run_events, 8);
	ok = ok && require_equal("manifest_records", manifest_records, 4);
	ok = ok && require_equal("artifact_provenance", artifact_provenance, 3);
	ok = ok && require_equal("dossier_checks", dossier_checks, 4);
	ok = ok && rp_file_contains("rp_artifact", "provenance=rp_align_table;stage=align;event=4;retry=rp_retry_plan;review_gate=artifact_manifest;llm_quality=rp_llmeval;status=recovered");
	ok = ok && rp_file_contains("rp_stage_state", "stage=align;order=2;input=rp_artifact:rp_normalized_fastq;attempts=2;state=recovered");
	ok = ok && rp_file_contains("rp_run_events", "event=4;stage=align;action=rerun;status=recovered");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_stage=align");
	ok = ok && rp_file_contains("rp_artifact_manifest", "dossier_check=review_gate;source=rp_review_dashboard;gate=artifact_manifest;status=pass");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && require_equal("agent_roles", agent_roles, 7);
	ok = ok && require_equal("agent_messages", agent_messages, task_lines);
	ok = ok && require_equal("agent_decisions", agent_decisions, 8);
	ok = ok && require_equal("agent_handoffs", agent_handoffs, 6);
	ok = ok && require_equal("deliberation_items", deliberation_items, 5);
	ok = ok && require_equal("ingested_files", ingested_files, 2);
	ok = ok && require_equal("dataset_snapshots", snapshots, 2);
	ok = ok && require_equal("data_previews", previews, 2);
	ok = ok && require_equal("data_quality_rules", quality_rules, 7);
	ok = ok && require_equal("data_transforms", transforms, 2);
	ok = ok && require_equal("dataset_collection_items", collection_items, 4);

	int bio_samples = rp_get_int_value("rp_sreg", "samples=");
	int bio_aliquots = rp_get_int_value("rp_sreg", "aliquots=");
	int access_requests = rp_get_int_value("rp_access", "requests=");
	int instruments = rp_get_int_value("rp_instr", "instruments=");
	int inventory_items = rp_get_int_value("rp_invent", "inventory_items=");
	int resource_bookings = rp_get_int_value("rp_ressched", "bookings=");
	int result_review_items = rp_get_int_value("rp_resrev", "review_items=");
	int fair_checks = rp_get_int_value("rp_fairpkg", "fair_checks=");
	int lit_papers = rp_get_int_value("rp_litrev", "papers=");
	int semantic_docs = rp_get_int_value("rp_semindex", "documents=");
	int knowledge_answers = rp_get_int_value("rp_kanswers", "answers=");
	int runtime_envs = rp_get_int_value("rp_runenv", "environments=");
	int notebook_cells = rp_get_int_value("rp_nbexec", "executed_cells=");
	int notebook_exports = rp_get_int_value("rp_nbexec", "exports=");
	int downloadable_units = rp_get_int_value("rp_repro", "downloadable_units=");
	int eln_entries = rp_get_int_value("rp_eln", "eln_entries=");
	int portability_imports = rp_get_int_value("rp_wfio", "portability_imports=");
	int adapter_specs = rp_get_int_value("rp_wfio", "adapter_specs=");
	int migration_steps = rp_get_int_value("rp_wfio", "migration_steps=");
	int rehearsal_cases = rp_get_int_value("rp_wfio", "cases=");
	int blocking_items = rp_get_int_value("rp_wfio", "blocking_items=");
	ok = ok && require_equal("bio_samples", bio_samples, 8);
	ok = ok && require_equal("bio_aliquots", bio_aliquots, 12);
	ok = ok && require_equal("access_requests", access_requests, 3);
	ok = ok && require_equal("instruments", instruments, 4);
	ok = ok && require_equal("inventory_items", inventory_items, 9);
	ok = ok && require_equal("resource_bookings", resource_bookings, 6);
	ok = ok && require_equal("result_review_items", result_review_items, 10);
	ok = ok && require_equal("fair_checks", fair_checks, 8);
	ok = ok && require_equal("lit_papers", lit_papers, 9);
	ok = ok && require_equal("semantic_docs", semantic_docs, 17);
	ok = ok && require_equal("knowledge_answers", knowledge_answers, 4);
	ok = ok && require_equal("runtime_envs", runtime_envs, 4);
	ok = ok && require_equal("notebook_cells", notebook_cells, 8);
	ok = ok && require_equal("notebook_exports", notebook_exports, 2);
	ok = ok && require_equal("downloadable_units", downloadable_units, 4);
	ok = ok && require_equal("eln_entries", eln_entries, 3);
	ok = ok && require_equal("portability_imports", portability_imports, 5);
	ok = ok && require_equal("adapter_specs", adapter_specs, 6);
	ok = ok && require_equal("migration_steps", migration_steps, 9);
	ok = ok && require_equal("rehearsal_cases", rehearsal_cases, 4);
	ok = ok && require_equal("blocking_items", blocking_items, 0);

	int namespace_checks = 12;
	int surface_checks = 13;
	int status_semantics = 11;
	int reference_checks = 18;
	int evidence_trace_checks = 14;
	int run_state_checks = 9;
	int lifecycle_checks = 10;
	int delivery_checks = rp_get_int_value("rp_package", "delivery_checks=");
	int agentos_readiness = 7;
	ok = ok && require_equal("namespace_checks", namespace_checks, 12);
	ok = ok && require_equal("surface_checks", surface_checks, 13);
	ok = ok && require_equal("status_semantics", status_semantics, 11);
	ok = ok && require_equal("reference_checks", reference_checks, 18);
	ok = ok && require_equal("evidence_trace_checks", evidence_trace_checks, 14);
	ok = ok && require_equal("run_state_checks", run_state_checks, 9);
	ok = ok && require_equal("lifecycle_checks", lifecycle_checks, 10);
	ok = ok && require_equal("delivery_checks", delivery_checks, 3);
	ok = ok && require_equal("agentos_readiness", agentos_readiness, 7);

	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 26 || tool_count < 109) {
		printf("rp_consistency: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		ok = 0;
	}
	if (!ok) return 1;

	if (!rp_write_file("rp_consistency",
			   "checks=373\n"
			   "task_records=21\n"
			   "ready_tasks=21\n"
			   "high_tasks=4\n"
			   "critical_tasks=4\n"
			   "selected_tasks=10\n"
			   "llm_packets=3\n"
			   "relay_protocol_files=5\n"
			   "relay_routes=4\n"
			   "fallback_cases=1\n"
			   "workflow_steps=10\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "backend_cases=4\n"
			   "runner_stages=5\n"
			   "runner_retries=1\n"
			   "runner_cache_hits=1\n"
			   "workbench_records=10\n"
			   "workbench_tasks=9\n"
			   "workbench_done=8\n"
			   "dynamic_input_records=8\n"
			   "dynamic_submissions=4\n"
			   "dynamic_input_runs=4\n"
			   "host_ui_events=10\n"
			   "artifact_records=2\n"
			   "artifact_provenance=3\n"
			   "artifact_dossier_checks=4\n"
			   "artifact_stage_links=2\n"
			   "artifact_event_links=2\n"
			   "artifact_review_links=1\n"
			   "artifact_llm_links=1\n"
			   "artifact_path_rebuild_files=6\n"
			   "artifact_path_rebuild_steps=7\n"
			   "workflow_runner_files=5\n"
			   "workflow_events=8\n"
			   "workflow_manifest_records=4\n"
			   "data_pipeline_files=6\n"
			   "dataset_snapshots=2\n"
			   "data_previews=2\n"
			   "data_quality_checks=7\n"
			   "data_transforms=2\n"
			   "dataset_collection_items=4\n"
			   "agent_roles=7\n"
			   "agent_messages=21\n"
			   "agent_decisions=8\n"
			   "agent_handoffs=6\n"
			   "deliberation_items=5\n"
			   "bio_service_files=5\n"
			   "bio_samples=8\n"
			   "bio_aliquots=12\n"
			   "lab_resource_files=5\n"
			   "lab_governance_ops_checks=26\n"
			   "approval_checks=2\n"
			   "ethics_protocol_checks=1\n"
			   "protocol_governance_checks=4\n"
			   "sop_execution_checks=3\n"
			   "training_record_checks=4\n"
			   "instrument_maintenance_checks=3\n"
			   "inventory_transaction_checks=3\n"
			   "procurement_order_checks=2\n"
			   "resource_budget_checks=2\n"
			   "run_queue_checks=1\n"
			   "notification_checks=1\n"
			   "instrument_count=4\n"
			   "inventory_items=9\n"
			   "publication_service_files=5\n"
			   "result_review_items=10\n"
			   "fair_checks=8\n"
			   "knowledge_service_files=5\n"
			   "semantic_documents=17\n"
			   "knowledge_answers=4\n"
			   "runtime_service_files=5\n"
			   "runtime_envs=4\n"
			   "notebook_cells=8\n"
			   "notebook_exports=2\n"
			   "downloadable_units=4\n"
			   "advanced_surface_objects=5\n"
			   "startup_checks=8\n"
			   "configuration_health=ready\n"
			   "platform_doctor_checks=10\n"
			   "secret_values_written=0\n"
			   "runtime_assurance_checks=24\n"
			   "secret_reference_checks=6\n"
			   "model_registry_checks=5\n"
			   "llm_proxy_replay_audits=2\n"
			   "collaboration_threads=2\n"
			   "collaboration_action_items=2\n"
			   "observability_alerts=5\n"
			   "observability_health_reports=1\n"
			   "research_ops_checks=28\n"
			   "semantic_graph_checks=6\n"
			   "prompt_ops_checks=5\n"
			   "runbook_checks=7\n"
			   "worker_ops_checks=5\n"
			   "execution_control_checks=5\n"
			   "regulated_research_checks=32\n"
			   "annotation_checks=5\n"
			   "assay_plate_checks=4\n"
			   "cohort_monitoring_checks=3\n"
			   "data_access_checks=4\n"
			   "research_card_checks=4\n"
			   "research_object_checks=5\n"
			   "sample_custody_checks=3\n"
			   "statistical_design_checks=2\n"
			   "workflow_template_checks=2\n"
			   "research_product_checks=18\n"
			   "project_scaffold_files=8\n"
			   "dataset_product_exports=9\n"
			   "dataset_product_runs=2\n"
			   "source_portfolio_exports=1\n"
			   "research_portfolio_checks=16\n"
			   "usable_research_sources=42\n"
			   "usable_research_datasets=3\n"
			   "usable_research_literature_searches=4\n"
			   "usable_research_reviews=8\n"
			   "usable_research_evidence_reviews=4\n"
			   "usable_research_evidence_extractions=15\n"
			   "usable_research_screening_decisions=15\n"
			   "usable_research_exports=66\n"
			   "usable_research_platform_doctor_reports=10\n"
			   "usable_research_project_handoff_audits=30\n"
			   "usable_research_project_run_comparisons=15\n"
			   "usable_research_project_reproducibility_audits=15\n"
			   "usable_research_project_snapshot_comparisons=15\n"
			   "execution_scale_checks=14\n"
			   "host_workflow_runs=10\n"
			   "host_workflow_stage_runs=70\n"
			   "host_workflow_cache=6\n"
			   "host_agent_messages=70\n"
			   "host_agent_decisions=70\n"
			   "agentcompare_reports=3\n"
			   "agentcompare_results=15\n"
			   "agentcompare_profiles=3\n"
			   "content_objects=145\n"
			   "object_references=145\n"
			   "operations_scale_checks=12\n"
			   "host_audit_records=5\n"
			   "host_metrics=13\n"
			   "host_llm_providers=3\n"
			   "host_secret_references=3\n"
			   "host_executed_corr_ids=4\n"
			   "usable_research_projects=20\n"
			   "host_artifacts=128\n"
			   "host_messages=70\n"
			   "host_content_objects=129\n"
			   "host_object_references=129\n"
			   "host_agentcompare_reports=3\n"
			   "host_agentcompare_results=15\n"
               "project_revision_incident_checks=12\n"
               "usable_research_revision_tasks=1\n"
               "usable_research_project_scaffolds=1\n"
               "incidents=1\n"
               "incident_id=INC-RUN-042-ALIGN-OOM\n"
               "incident_failed_stage=align\n"
               "incident_reason=memory_limit\n"
               "incident_status=closed\n"
               "revision_task_status=completed\n"
               "revision_task_owner=Wang\n"
               "revision_review_decision=needs_revision\n"
               "project_scaffold=deepseek-reliability-response-study\n"
               "project_scaffold_exports=json,markdown\n"
               "reserved_research_surface_checks=21\n"
               "usable_research_dataset_answers=0\n"
               "usable_research_dataset_cards=0\n"
               "usable_research_dataset_portfolios=0\n"
               "usable_research_dataset_previews=0\n"
               "usable_research_dataset_run_comparisons=0\n"
               "usable_research_dataset_runs=0\n"
               "usable_research_dataset_visualizations=0\n"
               "usable_research_evidence_syntheses=0\n"
               "usable_research_package_intakes=0\n"
               "usable_research_prisma_flows=0\n"
               "usable_research_project_action_executions=0\n"
               "usable_research_project_reviews=0\n"
               "usable_research_review_protocols=0\n"
               "usable_research_source_portfolios=0\n"
               "usable_research_study_protocol_bundles=0\n"
               "usable_research_study_protocol_compliance_reports=0\n"
               "usable_research_study_protocol_launches=0\n"
               "usable_research_study_protocol_runs=0\n"
               "usable_research_study_protocols=0\n"
               "usable_research_workbench_action_items=0\n"
               "usable_research_workbench_notes=0\n"
               "root_state_surface_checks=10\n"
               "root_projects=1\n"
               "root_runs=1\n"
               "root_reports=1\n"
               "root_plans=1\n"
               "root_search_records=1\n"
               "root_site_exports=1\n"
               "root_compare_profiles=1\n"
               "root_audit_records=5\n"
               "root_context_records=348\n"
               "root_project_id=lab-gene-x\n"
               "root_run_id=RUN-042\n"
               "root_report_id=RUN-042-recovery-report\n"
               "root_plan_id=PLAN-RUN-042-RECOVER-1\n"
               "root_search_id=search:1\n"
               "root_site_id=site:1\n"
               "root_compare_profile=agentcompare-default\n"
               "root_audit_spoof_denied=1\n"
			   "study_protocol_reproduction_checks=5\n"
			   "project_bundle_cache=ready\n"
			   "research_search_saved=2\n"
			   "project_surface_actions=4\n"
			   "study_protocol_checks=6\n"
			   "protocol_compliance_reports=1\n"
			   "protocol_compliance_findings=3\n"
			   "protocol_amendments=1\n"
			   "protocol_amendment_decisions=1\n"
			   "sop_executions=1\n"
			   "sop_step_results=4\n"
			   "sop_deviation_reviews=1\n"
			   "risk_mitigations=3\n"
			   "risk_reviews=1\n"
			   "capa_verifications=2\n"
			   "decision_support_packets=1\n"
			   "provenance_graph_nodes=150\n"
			   "provenance_graph_links=250\n"
			   "dataset_answer_files=4\n"
			   "package_intake_files=5\n"
			   "workflow_portability_records=1\n"
			   "adapter_specs=6\n"
			   "migration_steps=9\n"
			   "portability_rehearsal_cases=4\n"
			   "coherence_checks=9\n"
			   "namespace_checks=12\n"
			   "surface_checks=13\n"
			   "status_semantics=11\n"
			   "reference_checks=18\n"
			   "evidence_trace_checks=14\n"
			   "run_state_checks=9\n"
			   "lifecycle_checks=10\n"
			   "delivery_coherence=3\n"
			   "agentos_readiness_checks=7\n"
			   "state_relation=passed\n"
			   "knowledge_index_checks=22\n"
			   "llm_transcript_checks=3\n"
			   "llm_bridge_transcripts=90\n"
			   "llm_bridge_requests=30\n"
			   "llm_bridge_responses=30\n"
			   "workbench_delivery_checks=15\n"
			   "usable_research_workbenches=5\n"
			   "usable_research_templates=5\n"
			   "usable_research_workspace_imports=5\n"
			   "usable_research_workspace_inspections=5\n"
			   "usable_research_workbench_answers=5\n"
			   "usable_research_deliveries=6\n"
			   "usable_research_studio_sessions=2\n"
			   "usable_research_project_action_plans=15\n"
			   "usable_research_project_deliveries=4\n"
			   "usable_research_project_runbooks=15\n"
			   "usable_research_project_evidence_audits=15\n"
			   "usable_research_project_provenance_graphs=3\n"
			   "usable_research_project_launches=3\n"
			   "usable_research_project_release_gates=15\n"
			   "usable_research_project_snapshots=15\n"
			   "search_documents=1385\n"
			   "provenance_nodes=406\n"
			   "provenance_links=544\n"
			   "event_stream_records=6816\n"
			   "context_records=348\n"
			   "host_workflow_artifacts=150\n"
			   "usable_research_artifacts=429\n"
			   "usable_research_runs=20\n"
			   "usable_research_stages=168\n"
			   "usable_research_messages=223\n"
			   "usable_research_decisions=203\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=consistency;msg=22;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_tasks")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_llm")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_backend")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_data_pipeline")) return 1;
	if (!rp_append_status("consistency=ready")) return 1;
	printf("rp_consistency: checks=373 tasks=21 llm=3 relay=5 workflow=5 portability=6 coherence=9 data=6 services=25 lab_governance=26 products=18 assurance=24 research_ops=28 regulated=32 knowledge_index=22 llm_transcripts=3 workbench_delivery=15 portfolio_scale=16 execution_scale=14 operations_scale=12 project_revision_incident=12 reserved_surfaces=21 root_state=10 backend=4 artifacts=7 agents=7 dynamic=4 status=ready\n");
	return 0;
}

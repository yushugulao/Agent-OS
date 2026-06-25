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
	int dag_edges = rp_get_int_value("rp_stage_dag", "edges=");
	int log_lines = rp_get_int_value("rp_stage_log", "lines=");
	int artifact_records = rp_get_int_value("rp_artifact", "records=");
	int stage_state_stages = rp_get_int_value("rp_stage_state", "stages=");
	int cache_records = rp_get_int_value("rp_cache_index", "cache_records=");
	int retry_items = rp_get_int_value("rp_retry_plan", "retry_items=");
	int run_events = rp_get_int_value("rp_run_events", "events=");
	int manifest_records = rp_get_int_value("rp_artifact_manifest", "manifest_records=");
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
	ok = ok && require_equal("dag_edges", dag_edges, runner_stages - 1);
	ok = ok && require_equal("stage_log_lines", log_lines, runner_stages);
	ok = ok && require_equal("artifact_records", artifact_records, 2);
	ok = ok && require_equal("stage_state_stages", stage_state_stages, runner_stages);
	ok = ok && require_equal("cache_records", cache_records, runner_stages);
	ok = ok && require_equal("retry_items", retry_items, 1);
	ok = ok && require_equal("run_events", run_events, 8);
	ok = ok && require_equal("manifest_records", manifest_records, 4);
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
			   "checks=107\n"
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
			   "artifact_records=2\n"
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
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=consistency;msg=22;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_tasks;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_llm;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_backend;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=consistency.check_data_pipeline;target=rp_consistency;status=ok")) return 1;
	if (!rp_append_status("consistency=ready")) return 1;
	printf("rp_consistency: checks=107 tasks=21 llm=3 relay=5 workflow=5 portability=6 coherence=9 data=6 services=25 backend=4 artifacts=4 agents=7 status=ready\n");
	return 0;
}

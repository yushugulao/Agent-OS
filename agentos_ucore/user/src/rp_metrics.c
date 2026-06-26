#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_fix", "status=recovered")) return 1;
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_wfio", "compatibility_checks=6")) return 1;
	if (!rp_file_contains("rp_wfio", "package=workflow-portability")) return 1;
	if (!rp_file_contains("rp_wfio", "migration_steps=9")) return 1;
	if (!rp_file_contains("rp_review2", "rounds=2")) return 1;
	if (!rp_file_contains("rp_review2", "review_threads=2")) return 1;
	if (!rp_file_contains("rp_review2", "action_items=2")) return 1;
	if (!rp_file_contains("rp_revision", "draft_versions=3")) return 1;
	if (!rp_file_contains("rp_dataver", "release_candidate=v2")) return 1;
	if (!rp_file_contains("rp_repro", "status=ready")) return 1;
	if (!rp_file_contains("rp_claimrec", "claim=8")) return 1;
	if (!rp_file_contains("rp_provpath", "critical_paths=3")) return 1;
	if (!rp_file_contains("rp_dataprof", "profiles=4")) return 1;
	if (!rp_file_contains("rp_figrec", "exported=3")) return 1;
	if (!rp_file_contains("rp_trialrec", "selected=trial-3")) return 1;
	if (!rp_file_contains("rp_risk", "open_risks=0")) return 1;
	if (!rp_file_contains("rp_capa", "verifications=2")) return 1;
	if (!rp_file_contains("rp_risk", "decision_support=decision:agentos-final-demo-backend")) return 1;
	if (!rp_file_contains("rp_protocol", "protocol_compliance_reports=1")) return 1;
	if (!rp_file_contains("rp_protocol", "protocol_amendments=1")) return 1;
	if (!rp_file_contains("rp_soplog", "sop_executions=1")) return 1;
	if (!rp_file_contains("rp_sched", "queue_items=21")) return 1;
	if (!rp_file_contains("rp_taskrec", "class=critical")) return 1;
	if (!rp_file_contains("rp_rank", "selected=10")) return 1;
	if (!rp_file_contains("rp_budget", "decision=within_budget")) return 1;
	if (!rp_file_contains("rp_retrylog", "attempts=2")) return 1;
	if (!rp_file_contains("rp_runview", "status=ready")) return 1;
	if (!rp_file_contains("rp_fail", "recoverable=1")) return 1;
	if (!rp_file_contains("rp_relay", "status=ready")) return 1;
	if (!rp_file_contains("rp_relay", "relay_packets=3")) return 1;
	if (!rp_file_contains("rp_llm_packets", "packets=3")) return 1;
	if (!rp_file_contains("rp_llm_routes", "routes=4")) return 1;
	if (!rp_file_contains("rp_llm_guard", "secrets_in_ucore=0")) return 1;
	if (!rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side")) return 1;
	if (!rp_file_contains("rp_llm_fallback", "fallback_cases=1")) return 1;
	if (!rp_file_contains("rp_prompt", "routes=4")) return 1;
	if (!rp_file_contains("rp_llmq", "queued=3")) return 1;
	if (!rp_file_contains("rp_llmeval", "passed=7")) return 1;
	if (!rp_file_contains("rp_llmlog", "request_packets=3")) return 1;
	if (!rp_file_contains("rp_policy", "access_profiles=4")) return 1;
	if (!rp_file_contains("rp_compliance", "relay_protocol_files=5")) return 1;
	if (!rp_file_contains("rp_compliance", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_delta", "items=20")) return 1;
	if (!rp_file_contains("rp_diff", "changed_items=20")) return 1;
	if (!rp_file_contains("rp_execobs", "timeline_events=9")) return 1;
	if (!rp_file_contains("rp_worker", "heartbeats=4")) return 1;
	if (!rp_file_contains("rp_runconf", "profiles=2")) return 1;
	if (!rp_file_contains("rp_configdrift", "changed_parameters=2")) return 1;
	if (!rp_file_contains("rp_invocation", "steps=10")) return 1;
	if (!rp_file_contains("rp_completion", "actions=4")) return 1;
	if (!rp_file_contains("rp_runner", "stages=5")) return 1;
	if (!rp_file_contains("rp_runner", "workbench_tasks=9")) return 1;
	if (!rp_file_contains("rp_input", "dynamic_submissions=4")) return 1;
	if (!rp_file_contains("rp_runner", "dynamic_input_runs=4")) return 1;
	if (!rp_file_contains("rp_stage_state", "stages=5")) return 1;
	if (!rp_file_contains("rp_cache_index", "cache_hits=1")) return 1;
	if (!rp_file_contains("rp_retry_plan", "retry_items=1")) return 1;
	if (!rp_file_contains("rp_run_events", "events=8")) return 1;
	if (!rp_file_contains("rp_artifact_manifest", "manifest_records=4")) return 1;
	if (!rp_file_contains("rp_artifact", "status=recovered")) return 1;
	if (!rp_file_contains("rp_chart_data", "status=ready")) return 1;
	if (!rp_file_contains("rp_ingest_files", "files=2")) return 1;
	if (!rp_file_contains("rp_dataset_snapshot", "snapshots=2")) return 1;
	if (!rp_file_contains("rp_data_preview", "previews=2")) return 1;
	if (!rp_file_contains("rp_data_quality", "passed=7")) return 1;
	if (!rp_file_contains("rp_data_transform", "transforms=2")) return 1;
	if (!rp_file_contains("rp_dataset_collection", "items=4")) return 1;
	if (!rp_file_contains("rp_agents", "agents=7")) return 1;
	if (!rp_file_contains("rp_decisions", "decisions=8")) return 1;
	if (!rp_file_contains("rp_handoff", "handoffs=6")) return 1;
	if (!rp_file_contains("rp_agent_run", "agent_messages=21")) return 1;
	if (!rp_file_contains("rp_backend", "cases=4")) return 1;
	if (!rp_file_contains("rp_consistency", "state_relation=passed")) return 1;
	if (!rp_file_contains("rp_consistency", "coherence_checks=9")) return 1;
	if (!rp_file_contains("rp_sreg", "samples=8")) return 1;
	if (!rp_file_contains("rp_instr", "instruments=4")) return 1;
	if (!rp_file_contains("rp_resrev", "review_items=10")) return 1;
	if (!rp_file_contains("rp_semindex", "documents=17")) return 1;
	if (!rp_file_contains("rp_runenv", "environments=4")) return 1;
	if (!rp_file_contains("rp_mail", "to=metrics")) return 1;
	if (!rp_file_contains("rp_nbexec", "notebook=reproducible-analysis.ipynb")) return 1;
	if (!rp_file_contains("rp_repro", "downloadable_units=4")) return 1;
	if (!rp_file_contains("rp_runop", "advanced_surface=objects:5")) return 1;
	if (!rp_file_contains("rp_runop", "research_search:saved_queries:2")) return 1;
	if (!rp_file_contains("rp_runop", "project_space:lab-gene-x")) return 1;
	if (!rp_file_contains("rp_runop", "study_protocol:protocols:2")) return 1;
	if (!rp_file_contains("rp_runop", "dataset_answer:datasets:2")) return 1;
	if (!rp_file_contains("rp_runop", "package_intake:packages:1")) return 1;
	if (!rp_file_contains("rp_runop", "agentos_advanced_surface=kernel_bound")) return 1;
	if (!rp_file_contains("rp_runop", "agentos_advanced_surface_detail=tool:echo,tool:read_context")) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 27 || tool_count < 113) return 1;
	if (!rp_write_file("rp_telemetry",
			   "run_id=RUN-042\n"
			   "trace_spans=8\n"
			   "bottlenecks=1\n"
			   "message_acks=35\n"
			   "tool_events=115\n"
			   "scheduler_items=21\n"
			   "ranked_tasks=21\n"
			   "selected_tasks=10\n"
			   "policy_checks=8\n"
			   "compliance=accepted\n"
			   "risk_items=3\n"
			   "capa_actions=2\n"
			   "risk_mitigations=3\n"
			   "risk_reviews=1\n"
			   "capa_verifications=2\n"
			   "protocol_compliance_reports=1\n"
			   "protocol_compliance_findings=3\n"
			   "protocol_amendments=1\n"
			   "sop_executions=1\n"
			   "decision_support_packets=1\n"
			   "delta_items=20\n"
			   "diff_records=1\n"
			   "timeline_events=9\n"
			   "worker_heartbeats=4\n"
			   "control_actions=8\n"
			   "run_config_profiles=2\n"
			   "workflow_invocations=1\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "consistency_checks=120\n"
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
			   "runner_stages=5\n"
			   "runner_retries=1\n"
			   "runner_cache_hits=1\n"
			   "workflow_runner_files=5\n"
			   "workflow_events=8\n"
			   "workflow_manifest_records=4\n"
			   "workbench_records=10\n"
			   "workbench_tasks=9\n"
			   "dynamic_input_records=8\n"
			   "dynamic_submissions=4\n"
			   "host_ui_events=10\n"
			   "artifact_records=2\n"
			   "data_pipeline_files=6\n"
			   "dataset_snapshots=2\n"
			   "data_previews=2\n"
			   "data_quality_checks=7\n"
			   "data_transforms=2\n"
			   "dataset_collection_items=4\n"
			   "relay_protocol_files=5\n"
			   "relay_routes=4\n"
			   "host_request_files=1\n"
			   "fallback_cases=1\n"
			   "agent_roles=7\n"
			   "collaboration_decisions=8\n"
			   "handoffs=6\n"
			   "deliberation_items=5\n"
			   "backend_cases=4\n"
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
			   "advanced_surface_objects=5\n"
			   "research_search_saved=2\n"
			   "project_surface_actions=4\n"
			   "study_protocol_checks=6\n"
			   "dataset_answer_files=4\n"
			   "package_intake_files=5\n"
			   "claim_records=8\n"
			   "provenance_paths=3\n"
			   "data_profiles=4\n"
			   "figure_records=3\n"
			   "trial_records=4\n"
			   "workflow_exports=5\n"
			   "workflow_portability_records=1\n"
			   "migration_steps=9\n"
			   "portability_rehearsal_cases=4\n"
			   "review_rounds=2\n"
			   "review_threads=2\n"
			   "review_action_items=2\n"
			   "data_versions=2\n"
			   "retry_attempts=2\n"
			   "relay_packets=3\n"
			   "llm_requests=3\n"
			   "llm_eval_passed=7\n"
			   "run_views=1\n"
			   "failure_items=1\n"
			   "poll_rounds=18\n"
			   "scanned_records=128\n"
			   "metric_files=151\n"
			   "ticks=42\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_health",
			   "run_id=RUN-042\n"
			   "workers=4\n"
			   "healthy=4\n"
			   "budget=within_budget\n"
			   "failure_items=1\n"
			   "retry_attempts=2\n"
			   "view=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_agentcmp",
			   "case=user_on_plain_ucore\n"
			   "scanned_records=128\n"
			   "poll_rounds=18\n"
			   "syscall_count=0\n"
			   "spoof_denied=0\n"
			   "context_trusted=0\n"
			   "audit_events=1\n"
			   "report_ok=1\n"
			   "repro_ok=1\n"
			   "llm_guarded=1\n"
			   "message_acks=35\n"
			   "tool_events=115\n"
			   "scheduler_items=21\n"
			   "ranked_tasks=21\n"
			   "selected_tasks=10\n"
			   "policy_checks=8\n"
			   "compliance=accepted\n"
			   "risk_items=3\n"
			   "capa_actions=2\n"
			   "risk_reviews=1\n"
			   "protocol_compliance_reports=1\n"
			   "protocol_amendments=1\n"
			   "sop_executions=1\n"
			   "decision_support_packets=1\n"
			   "delta_items=20\n"
			   "diff_records=1\n"
			   "timeline_events=9\n"
			   "worker_heartbeats=4\n"
			   "control_actions=8\n"
			   "run_config_profiles=2\n"
			   "workflow_invocations=1\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "consistency_checks=120\n"
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
			   "runner_stages=5\n"
			   "runner_retries=1\n"
			   "runner_cache_hits=1\n"
			   "workflow_runner_files=5\n"
			   "workflow_events=8\n"
			   "workflow_manifest_records=4\n"
			   "workbench_records=10\n"
			   "workbench_tasks=9\n"
			   "dynamic_input_records=8\n"
			   "dynamic_submissions=4\n"
			   "host_ui_events=10\n"
			   "artifact_records=2\n"
			   "data_pipeline_files=6\n"
			   "dataset_snapshots=2\n"
			   "data_previews=2\n"
			   "data_quality_checks=7\n"
			   "data_transforms=2\n"
			   "dataset_collection_items=4\n"
			   "relay_protocol_files=5\n"
			   "relay_routes=4\n"
			   "host_request_files=1\n"
			   "fallback_cases=1\n"
			   "agent_roles=7\n"
			   "collaboration_decisions=8\n"
			   "handoffs=6\n"
			   "deliberation_items=5\n"
			   "backend_cases=4\n"
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
			   "claim_records=8\n"
			   "provenance_paths=3\n"
			   "data_profiles=4\n"
			   "figure_records=3\n"
			   "trial_records=4\n"
			   "workflow_exports=5\n"
			   "workflow_portability_records=1\n"
			   "migration_steps=9\n"
			   "portability_rehearsal_cases=4\n"
			   "review_rounds=2\n"
			   "review_threads=2\n"
			   "review_action_items=2\n"
			   "data_versions=2\n"
			   "retry_attempts=2\n"
			   "relay_packets=3\n"
			   "llm_requests=3\n"
			   "llm_eval_passed=7\n"
			   "run_views=1\n"
			   "health_ok=1\n"
			   "ticks=42\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_agentcmp", "host_action_research_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_agentcmp", "host_action_research_input=ready")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		char profile[48];
		if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
			rp_copy_text(profile, sizeof(profile), "plain_ucore");
		}
		if (!rp_append_file("rp_agentcmp", "host_action_compare_requested=1")) return 1;
		if (!rp_append_host_action_line("rp_agentcmp", "host_action_compare_profile=", profile)) return 1;
	}
	if (rp_host_seed_has("kind=human_review")) {
		if (!rp_append_file("rp_agentcmp", "host_action_review_requested=1")) return 1;
	}
	if (rp_host_seed_has("kind=revision_task") || rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_agentcmp", "host_action_revision_requested=1")) return 1;
	}
	if (rp_host_seed_has("kind=workbench") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_advance") ||
	    rp_host_seed_has("kind=workbench_auto_advance") ||
	    rp_host_seed_has("kind=workbench_task") ||
	    rp_host_seed_has("kind=workbench_note") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_evidence_search") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_export")) {
		if (!rp_append_file("rp_agentcmp", "host_action_workbench_requested=1")) return 1;
	}
	if (rp_host_seed_has("kind=bundle_export") ||
	    rp_host_seed_has("kind=research_export") ||
	    rp_host_seed_has("kind=notebook_export")) {
		if (!rp_append_file("rp_agentcmp", "host_action_export_requested=1")) return 1;
	}
	if (!rp_append_file("rp_ack", "ack=metrics;msg=14;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=metrics.measure_plain")) return 1;
	if (!rp_append_file("rp_tool", "tool=metrics.write_health")) return 1;
	if (!rp_append_status("telemetry=ready")) return 1;
	if (!rp_append_status("agentcmp=ready")) return 1;
	if (!rp_append_status("health=ready")) return 1;
	printf("rp_metrics: telemetry_spans=8 acks=35 tools=115 services=25 delta_items=20 dynamic=4 status=ready\n");
	return 0;
}

#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_object_query", "hits=8");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_site", "pages=6");
	ok = ok && rp_file_contains("rp_llm_resp", "responses=3");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=36");
	ok = ok && rp_file_contains("rp_knowledge", "semantic_relations=6");
	ok = ok && rp_file_contains("rp_claimrec", "claim=8");
	ok = ok && rp_file_contains("rp_provpath", "critical_paths=3");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_figrec", "exported=3");
	ok = ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_risk", "open_risks=0");
	ok = ok && rp_file_contains("rp_capa", "verifications=2");
	ok = ok && rp_file_contains("rp_delta", "decision=accepted");
	ok = ok && rp_file_contains("rp_diff", "changed_items=20");
	ok = ok && rp_file_contains("rp_wfio", "compatibility_checks=6");
	ok = ok && rp_file_contains("rp_review2", "rounds=2");
	ok = ok && rp_file_contains("rp_revision", "draft_versions=3");
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	ok = ok && rp_file_contains("rp_fail", "failure_class=tool_output_missing");
	ok = ok && rp_file_contains("rp_runview", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_taskrec", "msg=21");
	ok = ok && rp_file_contains("rp_rank", "selected=10");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_health", "healthy=4");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_prompt", "routes=4");
	ok = ok && rp_file_contains("rp_policy", "access_profiles=4");
	ok = ok && rp_file_contains("rp_compliance", "checks=8");
	ok = ok && rp_file_contains("rp_llmq", "queued=3");
	ok = ok && rp_file_contains("rp_llmeval", "passed=7");
	ok = ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	ok = ok && rp_file_contains("rp_llmlog", "request_packets=3");
	ok = ok && rp_file_contains("rp_sched", "queue_items=21");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_llm_packets", "packets=3");
	ok = ok && rp_file_contains("rp_llm_routes", "routes=4");
	ok = ok && rp_file_contains("rp_llm_guard", "secrets_in_ucore=0");
	ok = ok && rp_file_contains("rp_llm_hostreq", "cloud_mode=optional_host_side");
	ok = ok && rp_file_contains("rp_llm_fallback", "fallback_cases=1");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=27");
	ok = ok && rp_file_contains("rp_agentcmp", "tool_events=106");
	ok = ok && rp_file_contains("rp_agentcmp", "scheduler_items=21");
	ok = ok && rp_file_contains("rp_agentcmp", "ranked_tasks=21");
	ok = ok && rp_file_contains("rp_agentcmp", "selected_tasks=10");
	ok = ok && rp_file_contains("rp_agentcmp", "policy_checks=8");
	ok = ok && rp_file_contains("rp_agentcmp", "compliance=accepted");
	ok = ok && rp_file_contains("rp_agentcmp", "risk_items=3");
	ok = ok && rp_file_contains("rp_agentcmp", "capa_actions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "delta_items=20");
	ok = ok && rp_file_contains("rp_agentcmp", "diff_records=1");
	ok = ok && rp_file_contains("rp_agentcmp", "claim_records=8");
	ok = ok && rp_file_contains("rp_agentcmp", "provenance_paths=3");
	ok = ok && rp_file_contains("rp_agentcmp", "data_profiles=4");
	ok = ok && rp_file_contains("rp_agentcmp", "figure_records=3");
	ok = ok && rp_file_contains("rp_agentcmp", "trial_records=4");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_exports=2");
	ok = ok && rp_file_contains("rp_agentcmp", "review_rounds=2");
	ok = ok && rp_file_contains("rp_agentcmp", "data_versions=2");
	ok = ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_packets=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_requests=3");
	ok = ok && rp_file_contains("rp_agentcmp", "llm_eval_passed=7");
	ok = ok && rp_file_contains("rp_agentcmp", "run_views=1");
	ok = ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "agent_roles=7");
	ok = ok && rp_file_contains("rp_agentcmp", "collaboration_decisions=8");
	ok = ok && rp_file_contains("rp_agentcmp", "handoffs=6");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_protocol_files=5");
	ok = ok && rp_file_contains("rp_agentcmp", "workflow_runner_files=5");
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "artifacts=42");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_execplan", "scheduled_tasks=21");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "status=ready");
	ok = ok && rp_file_contains("rp_stage_state", "stages=5");
	ok = ok && rp_file_contains("rp_cache_index", "cache_hits=1");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_items=1");
	ok = ok && rp_file_contains("rp_run_events", "events=8");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_report_text", "RUN-042 Recovery Report");
	ok = ok && rp_file_contains("rp_chart_data", "chart=stage_attempts");
	ok = ok && rp_file_contains("rp_agents", "agents=7");
	ok = ok && rp_file_contains("rp_decisions", "decisions=8");
	ok = ok && rp_file_contains("rp_handoff", "handoffs=6");
	ok = ok && rp_file_contains("rp_deliberation", "items=5");
	ok = ok && rp_file_contains("rp_agent_run", "agent_decisions=8");
	ok = ok && rp_file_contains("rp_runconf", "profiles=2");
	ok = ok && rp_file_contains("rp_invocation", "steps=10");
	ok = ok && rp_file_contains("rp_completion", "actions=4");
	ok = ok && rp_file_contains("rp_backend", "cases=4");
	ok = ok && rp_file_contains("rp_backend_exec", "passed_cases=2");
	ok = ok && rp_file_contains("rp_study", "arms=2");
	ok = ok && rp_file_contains("rp_consistency", "state_relation=passed");
	ok = ok && rp_file_contains("rp_consistency", "task_records=21");
	ok = ok && rp_file_contains("rp_consistency", "checks=50");
	ok = ok && rp_file_contains("rp_consistency", "runner_stages=5");
	ok = ok && rp_file_contains("rp_ui_home", "page=home");
	ok = ok && rp_file_contains("rp_ui_run", "page=run-detail");
	ok = ok && rp_file_contains("rp_ui_agent", "page=agent-detail");
	ok = ok && rp_file_contains("rp_ui_evidence", "page=evidence-detail");
	ok = ok && rp_file_contains("rp_ui_compare", "page=compare-metrics");
	ok = ok && rp_file_contains("rp_tests", "tests=64");
	ok = ok && rp_file_contains("rp_tests", "status=passed");
	ok = ok && rp_file_contains("rp_ack", "ack=consistency;msg=22;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=consistency.check_backend");
	ok = ok && rp_file_contains("rp_ack", "ack=ui_export;msg=ui;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=ui_export.write_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=test_suite;msg=test;status=passed");
	ok = ok && rp_file_contains("rp_tool", "tool=test_suite.check_compare");
	ok = ok && rp_file_contains("rp_ack", "ack=agent_collab;msg=agents;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=agent_collab.write_decisions");
	ok = ok && rp_file_contains("rp_ack", "ack=llm_relay;msg=relay;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=llm_relay.write_packets");
	ok = ok && rp_file_contains("rp_ack", "ack=workflow_runner;msg=runner;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=workflow_runner.write_manifest");
	if (!ok) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 29 || tool_count < 119) {
		printf("rp_compare_plain: bad_event_counts acks=%d tools=%d\n", ack_count, tool_count);
		return 1;
	}
	if (!rp_write_file("rp_compare",
			   "profile=plain_ucore\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "objects=500\n"
			   "programs=36\n"
			   "state_files=121\n"
			   "message_acks=29\n"
			   "tool_events=119\n"
			   "consistency_checks=50\n"
			   "runner_stages=5\n"
			   "workflow_runner_files=5\n"
			   "relay_protocol_files=5\n"
			   "agent_roles=7\n"
			   "collaboration_decisions=8\n"
			   "ui_pages=5\n"
			   "test_cases=64\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=36 state_files=121 acks=29 tools=119 status=ready\n");
	return 0;
}

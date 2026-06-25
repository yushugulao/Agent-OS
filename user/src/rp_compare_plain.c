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
	ok = ok && rp_file_contains("rp_dossier", "sections=33");
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
	ok = ok && rp_file_contains("rp_runview", "scheduler_items=17");
	ok = ok && rp_file_contains("rp_taskrec", "msg=17");
	ok = ok && rp_file_contains("rp_rank", "selected=8");
	ok = ok && rp_file_contains("rp_runview", "ranked_tasks=17");
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
	ok = ok && rp_file_contains("rp_sched", "queue_items=17");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=17");
	ok = ok && rp_file_contains("rp_agentcmp", "tool_events=56");
	ok = ok && rp_file_contains("rp_agentcmp", "scheduler_items=17");
	ok = ok && rp_file_contains("rp_agentcmp", "ranked_tasks=17");
	ok = ok && rp_file_contains("rp_agentcmp", "selected_tasks=8");
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
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "artifacts=16");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_timeline", "events=9");
	ok = ok && rp_file_contains("rp_execplan", "scheduled_tasks=17");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	if (!ok) return 1;
	if (!rp_write_file("rp_compare",
			   "profile=plain_ucore\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "objects=500\n"
			   "programs=25\n"
			   "state_files=75\n"
			   "message_acks=18\n"
			   "tool_events=58\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=25 state_files=75 acks=18 tools=58 status=ready\n");
	return 0;
}

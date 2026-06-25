#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_fix", "status=recovered")) return 1;
	if (!rp_file_contains("rp_release", "decision=release")) return 1;
	if (!rp_file_contains("rp_wfio", "compatibility_checks=6")) return 1;
	if (!rp_file_contains("rp_review2", "rounds=2")) return 1;
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
	if (!rp_file_contains("rp_sched", "queue_items=21")) return 1;
	if (!rp_file_contains("rp_taskrec", "class=critical")) return 1;
	if (!rp_file_contains("rp_rank", "selected=10")) return 1;
	if (!rp_file_contains("rp_budget", "decision=within_budget")) return 1;
	if (!rp_file_contains("rp_retrylog", "attempts=2")) return 1;
	if (!rp_file_contains("rp_runview", "status=ready")) return 1;
	if (!rp_file_contains("rp_fail", "recoverable=1")) return 1;
	if (!rp_file_contains("rp_relay", "status=ready")) return 1;
	if (!rp_file_contains("rp_relay", "relay_packets=3")) return 1;
	if (!rp_file_contains("rp_prompt", "routes=4")) return 1;
	if (!rp_file_contains("rp_llmq", "queued=3")) return 1;
	if (!rp_file_contains("rp_llmeval", "passed=7")) return 1;
	if (!rp_file_contains("rp_llmlog", "request_packets=3")) return 1;
	if (!rp_file_contains("rp_policy", "access_profiles=4")) return 1;
	if (!rp_file_contains("rp_compliance", "decision=accepted")) return 1;
	if (!rp_file_contains("rp_delta", "items=20")) return 1;
	if (!rp_file_contains("rp_diff", "changed_items=20")) return 1;
	if (!rp_file_contains("rp_execobs", "timeline_events=9")) return 1;
	if (!rp_file_contains("rp_worker", "heartbeats=4")) return 1;
	if (!rp_file_contains("rp_runconf", "profiles=2")) return 1;
	if (!rp_file_contains("rp_configdrift", "changed_parameters=2")) return 1;
	if (!rp_file_contains("rp_invocation", "steps=10")) return 1;
	if (!rp_file_contains("rp_completion", "actions=4")) return 1;
	if (!rp_file_contains("rp_mail", "to=metrics")) return 1;
	int ack_count = rp_count_lines("rp_ack");
	int tool_count = rp_count_lines("rp_tool");
	if (ack_count < 20 || tool_count < 70) return 1;
	if (!rp_write_file("rp_telemetry",
			   "run_id=RUN-042\n"
			   "trace_spans=8\n"
			   "bottlenecks=1\n"
			   "message_acks=20\n"
			   "tool_events=70\n"
			   "scheduler_items=21\n"
			   "ranked_tasks=21\n"
			   "selected_tasks=10\n"
			   "policy_checks=8\n"
			   "compliance=accepted\n"
			   "risk_items=3\n"
			   "capa_actions=2\n"
			   "delta_items=20\n"
			   "diff_records=1\n"
			   "timeline_events=9\n"
			   "worker_heartbeats=4\n"
			   "control_actions=5\n"
			   "run_config_profiles=2\n"
			   "workflow_invocations=1\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "claim_records=8\n"
			   "provenance_paths=3\n"
			   "data_profiles=4\n"
			   "figure_records=3\n"
			   "trial_records=4\n"
			   "workflow_exports=2\n"
			   "review_rounds=2\n"
			   "data_versions=2\n"
			   "retry_attempts=2\n"
			   "relay_packets=3\n"
			   "llm_requests=3\n"
			   "llm_eval_passed=7\n"
			   "run_views=1\n"
			   "failure_items=1\n"
			   "poll_rounds=18\n"
			   "scanned_records=128\n"
			   "state_files=91\n"
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
			   "message_acks=20\n"
			   "tool_events=70\n"
			   "scheduler_items=21\n"
			   "ranked_tasks=21\n"
			   "selected_tasks=10\n"
			   "policy_checks=8\n"
			   "compliance=accepted\n"
			   "risk_items=3\n"
			   "capa_actions=2\n"
			   "delta_items=20\n"
			   "diff_records=1\n"
			   "timeline_events=9\n"
			   "worker_heartbeats=4\n"
			   "control_actions=5\n"
			   "run_config_profiles=2\n"
			   "workflow_invocations=1\n"
			   "workflow_attempts=12\n"
			   "completion_actions=4\n"
			   "claim_records=8\n"
			   "provenance_paths=3\n"
			   "data_profiles=4\n"
			   "figure_records=3\n"
			   "trial_records=4\n"
			   "workflow_exports=2\n"
			   "review_rounds=2\n"
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
	if (!rp_append_file("rp_ack", "ack=metrics;msg=14;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=metrics.measure_plain;target=rp_agentcmp;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=metrics.write_health;target=rp_health;status=ok")) return 1;
	if (!rp_append_status("telemetry=ready")) return 1;
	if (!rp_append_status("agentcmp=ready")) return 1;
	if (!rp_append_status("health=ready")) return 1;
	printf("rp_metrics: telemetry_spans=8 acks=20 tools=70 delta_items=20 status=ready\n");
	return 0;
}

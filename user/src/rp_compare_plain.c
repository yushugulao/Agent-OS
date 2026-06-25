#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_objects", "objects=500");
	ok = ok && rp_file_contains("rp_object_query", "hits=8");
	ok = ok && rp_file_contains("rp_lineage", "edges=7");
	ok = ok && rp_file_contains("rp_site", "pages=6");
	ok = ok && rp_file_contains("rp_llm_resp", "status=ready");
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "sections=14");
	ok = ok && rp_file_contains("rp_knowledge", "semantic_relations=6");
	ok = ok && rp_file_contains("rp_datarel", "fair=passed");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_datadic", "schema_drift=0");
	ok = ok && rp_file_contains("rp_compute", "replay=ready");
	ok = ok && rp_file_contains("rp_budget", "decision=within_budget");
	ok = ok && rp_file_contains("rp_fail", "failure_class=tool_output_missing");
	ok = ok && rp_file_contains("rp_runview", "scheduler_items=14");
	ok = ok && rp_file_contains("rp_health", "healthy=4");
	ok = ok && rp_file_contains("rp_labops", "maintenance=passed");
	ok = ok && rp_file_contains("rp_training", "gaps=0");
	ok = ok && rp_file_contains("rp_prompt", "provider_policy=host_relay");
	ok = ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	ok = ok && rp_file_contains("rp_sched", "queue_items=14");
	ok = ok && rp_file_contains("rp_retrylog", "attempts=2");
	ok = ok && rp_file_contains("rp_relay", "network_stack=host_only");
	ok = ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	ok = ok && rp_file_contains("rp_submit", "data_availability=ready");
	ok = ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	ok = ok && rp_file_contains("rp_agentcmp", "message_acks=14");
	ok = ok && rp_file_contains("rp_agentcmp", "tool_events=26");
	ok = ok && rp_file_contains("rp_agentcmp", "scheduler_items=14");
	ok = ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	ok = ok && rp_file_contains("rp_agentcmp", "relay_packets=2");
	ok = ok && rp_file_contains("rp_agentcmp", "run_views=1");
	ok = ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	ok = ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	ok = ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	ok = ok && rp_file_contains("rp_protocol", "ethics=approved");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_query", "workflow_hits=34");
	if (!ok) return 1;
	if (!rp_write_file("rp_compare",
			   "profile=plain_ucore\n"
			   "plain_kernel=passed\n"
			   "agentos_kernel=pending\n"
			   "objects=500\n"
			   "programs=22\n"
			   "state_files=52\n"
			   "message_acks=15\n"
			   "tool_events=28\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("compare=ready")) return 1;
	printf("rp_compare_plain: plain_kernel=passed objects=500 programs=22 state_files=52 acks=15 tools=28 status=ready\n");
	return 0;
}

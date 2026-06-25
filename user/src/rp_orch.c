#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <research_platform_state.h>

static const char *PROGRAMS[] = {
	"rp_catalog",
	"rp_object_store",
	"rp_object_query",
	"rp_lineage",
	"rp_site_export",
	"rp_planner",
	"rp_retriever",
	"rp_analyst",
	"rp_reviewer",
	"rp_lab",
	"rp_writer",
	"rp_repair",
	"rp_auditor",
	"rp_query",
	"rp_evidence",
	"rp_llm_bridge",
	"rp_privacy",
	"rp_package",
	"rp_release",
	"rp_dossier",
	"rp_metrics",
	"rp_compare_plain",
};

static int run_child(const char *program)
{
	int pid = fork();
	if (pid == 0) {
		char *argv[] = { (char *)program, 0 };
		if (exec(program, argv) < 0) {
			printf("rp_orch: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		printf("rp_orch: wait_failed program=%s\n", program);
		return 0;
	}
	if (code != 0) {
		printf("rp_orch: child_failed program=%s code=%d\n", program, code);
		return 0;
	}
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	printf("rp_orch: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
	}
	printf("rp_orch: programs_ok=%d programs_total=%d\n", ok, total);
	if (ok != total) {
		printf("rp_orch: failed\n");
		return 1;
	}
	int state_ok = 1;
	state_ok = state_ok && rp_file_contains("rp_status", "catalog=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "object_store=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "object_query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "lineage=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "site_export=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "planner=planned");
	state_ok = state_ok && rp_file_contains("rp_status", "mail=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "schedule=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "taskrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "budget=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "wfio=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "policy=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "retriever=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "analyst=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datadict=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataprof=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compute=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "figrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "failure=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "samples=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "quality=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewer=accepted");
	state_ok = state_ok && rp_file_contains("rp_status", "review2=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "protocol=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "sop=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "experiment=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "trialrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "labops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "training=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "writer=packaged");
	state_ok = state_ok && rp_file_contains("rp_status", "revision=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "repair=recovered");
	state_ok = state_ok && rp_file_contains("rp_status", "retry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "telemetry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "auditor=passed");
	state_ok = state_ok && rp_file_contains("rp_status", "query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "rank=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "runview=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "claimrec=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "provpath=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "knowledge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llm_bridge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmqueue=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "relay=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "promptops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmtrace=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmeval=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "privacy=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compliance=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datarel=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dataver=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "repro=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "release=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dossier=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "submit=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "agentcmp=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "health=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compare=ready");
	state_ok = state_ok && rp_file_contains("rp_audit", "status=passed");
	state_ok = state_ok && rp_file_contains("rp_compare", "plain_kernel=passed");
	state_ok = state_ok && rp_file_contains("rp_object_query", "hits=8");
	state_ok = state_ok && rp_file_contains("rp_lineage", "edges=7");
	state_ok = state_ok && rp_file_contains("rp_site", "pages=6");
	state_ok = state_ok && rp_file_contains("rp_llm_resp", "responses=3");
	state_ok = state_ok && rp_file_contains("rp_release", "decision=release");
	state_ok = state_ok && rp_file_contains("rp_dossier", "sections=27");
	state_ok = state_ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	state_ok = state_ok && rp_file_contains("rp_claimrec", "claim=8");
	state_ok = state_ok && rp_file_contains("rp_provpath", "critical_paths=3");
	state_ok = state_ok && rp_file_contains("rp_datarel", "fair=passed");
	state_ok = state_ok && rp_file_contains("rp_dataver", "release_candidate=v2");
	state_ok = state_ok && rp_file_contains("rp_reviewops", "governance=passed");
	state_ok = state_ok && rp_file_contains("rp_wfio", "portable_steps=10");
	state_ok = state_ok && rp_file_contains("rp_policy", "access_profiles=4");
	state_ok = state_ok && rp_file_contains("rp_compliance", "decision=accepted");
	state_ok = state_ok && rp_file_contains("rp_review2", "remaining_blockers=0");
	state_ok = state_ok && rp_file_contains("rp_revision", "draft_versions=3");
	state_ok = state_ok && rp_file_contains("rp_sched", "queue_items=14");
	state_ok = state_ok && rp_file_contains("rp_taskrec", "msg=14");
	state_ok = state_ok && rp_file_contains("rp_rank", "selected=5");
	state_ok = state_ok && rp_file_contains("rp_runview", "ranked_tasks=14");
	state_ok = state_ok && rp_file_contains("rp_budget", "decision=within_budget");
	state_ok = state_ok && rp_file_contains("rp_fail", "recoverable=1");
	state_ok = state_ok && rp_file_contains("rp_retrylog", "attempts=2");
	state_ok = state_ok && rp_file_contains("rp_relay", "network_stack=host_only");
	state_ok = state_ok && rp_file_contains("rp_relay", "relay_packets=3");
	state_ok = state_ok && rp_file_contains("rp_runview", "budget_state=within_budget");
	state_ok = state_ok && rp_file_contains("rp_health", "healthy=4");
	state_ok = state_ok && rp_file_contains("rp_datadic", "schema_drift=0");
	state_ok = state_ok && rp_file_contains("rp_dataprof", "profiles=4");
	state_ok = state_ok && rp_file_contains("rp_compute", "replay=ready");
	state_ok = state_ok && rp_file_contains("rp_figrec", "exported=3");
	state_ok = state_ok && rp_file_contains("rp_trialrec", "selected=trial-3");
	state_ok = state_ok && rp_file_contains("rp_labops", "maintenance=passed");
	state_ok = state_ok && rp_file_contains("rp_training", "competency_checks=3");
	state_ok = state_ok && rp_file_contains("rp_prompt", "routes=4");
	state_ok = state_ok && rp_file_contains("rp_llmq", "queued=3");
	state_ok = state_ok && rp_file_contains("rp_llmeval", "passed=7");
	state_ok = state_ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	state_ok = state_ok && rp_file_contains("rp_llmlog", "request_packets=3");
	state_ok = state_ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	state_ok = state_ok && rp_file_contains("rp_submit", "review_response=ready");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "message_acks=14");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "tool_events=45");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "scheduler_items=14");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "ranked_tasks=14");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "selected_tasks=5");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "policy_checks=8");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "compliance=accepted");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "claim_records=8");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "provenance_paths=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_profiles=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "figure_records=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "trial_records=4");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "workflow_exports=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "review_rounds=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "data_versions=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "retry_attempts=2");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "relay_packets=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "llm_requests=3");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "llm_eval_passed=7");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "run_views=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "health_ok=1");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	state_ok = state_ok && (rp_count_lines("rp_ack") >= 15);
	state_ok = state_ok && (rp_count_lines("rp_tool") >= 47);
	state_ok = state_ok && rp_file_contains("rp_protocol", "ethics=approved");
	printf("rp_orch: state_ok=%d\n", state_ok);
	if (!state_ok) {
		printf("rp_orch: failed\n");
		return 1;
	}
	printf("rp_orch: passed\n");
	return 0;
}

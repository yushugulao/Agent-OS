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
	state_ok = state_ok && rp_file_contains("rp_status", "retriever=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "analyst=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datadict=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compute=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "samples=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "quality=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewer=accepted");
	state_ok = state_ok && rp_file_contains("rp_status", "protocol=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "sop=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "experiment=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "labops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "training=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "writer=packaged");
	state_ok = state_ok && rp_file_contains("rp_status", "repair=recovered");
	state_ok = state_ok && rp_file_contains("rp_status", "telemetry=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "auditor=passed");
	state_ok = state_ok && rp_file_contains("rp_status", "query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "knowledge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llm_bridge=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "promptops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "llmtrace=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "privacy=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "datarel=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "repro=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "release=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "dossier=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewops=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "submit=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "agentcmp=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compare=ready");
	state_ok = state_ok && rp_file_contains("rp_audit", "status=passed");
	state_ok = state_ok && rp_file_contains("rp_compare", "plain_kernel=passed");
	state_ok = state_ok && rp_file_contains("rp_object_query", "hits=8");
	state_ok = state_ok && rp_file_contains("rp_lineage", "edges=7");
	state_ok = state_ok && rp_file_contains("rp_site", "pages=6");
	state_ok = state_ok && rp_file_contains("rp_llm_resp", "status=ready");
	state_ok = state_ok && rp_file_contains("rp_release", "decision=release");
	state_ok = state_ok && rp_file_contains("rp_dossier", "sections=12");
	state_ok = state_ok && rp_file_contains("rp_knowledge", "synthesis=ready");
	state_ok = state_ok && rp_file_contains("rp_datarel", "fair=passed");
	state_ok = state_ok && rp_file_contains("rp_reviewops", "governance=passed");
	state_ok = state_ok && rp_file_contains("rp_datadic", "schema_drift=0");
	state_ok = state_ok && rp_file_contains("rp_compute", "replay=ready");
	state_ok = state_ok && rp_file_contains("rp_labops", "maintenance=passed");
	state_ok = state_ok && rp_file_contains("rp_training", "competency_checks=3");
	state_ok = state_ok && rp_file_contains("rp_prompt", "routes=3");
	state_ok = state_ok && rp_file_contains("rp_llmlog", "privacy_checked=1");
	state_ok = state_ok && rp_file_contains("rp_repro", "notebook_replay=passed");
	state_ok = state_ok && rp_file_contains("rp_submit", "review_response=ready");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "report_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "repro_ok=1");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "message_acks=14");
	state_ok = state_ok && rp_file_contains("rp_agentcmp", "tool_events=20");
	state_ok = state_ok && rp_file_contains("rp_ack", "ack=metrics;msg=14;status=ready");
	state_ok = state_ok && rp_file_contains("rp_tool", "tool=metrics.measure_plain");
	state_ok = state_ok && (rp_count_lines("rp_ack") >= 15);
	state_ok = state_ok && (rp_count_lines("rp_tool") >= 21);
	state_ok = state_ok && rp_file_contains("rp_protocol", "ethics=approved");
	printf("rp_orch: state_ok=%d\n", state_ok);
	if (!state_ok) {
		printf("rp_orch: failed\n");
		return 1;
	}
	printf("rp_orch: passed\n");
	return 0;
}

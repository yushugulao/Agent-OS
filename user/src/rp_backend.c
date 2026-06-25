#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_runconf", "candidate=agentos-ucore");
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_mail", "to=backend");
	if (!ok) return 1;
	if (!rp_write_file("rp_backend",
			   "scenario=backend-scenario:RUN-042:agentcompare\n"
			   "cases=4\n"
			   "executable=2\n"
			   "planned=2\n"
			   "plain_ucore=ready\n"
			   "agentos_ucore=planned\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_exec",
			   "executions=1\n"
			   "passed_cases=2\n"
			   "planned_cases=2\n"
			   "indexed_candidate=ready\n"
			   "decision=baseline_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_export",
			   "exports=1\n"
			   "format=markdown\n"
			   "package=ready\n"
			   "telemetry=attached\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "arms=2\n"
			   "metrics=6\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=pending\n"
			   "conclusion=baseline_recorded\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario;target=rp_backend;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution;target=rp_backend_exec;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario;target=rp_backend_export;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study;target=rp_study;status=ok")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("backend_export=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: cases=4 executable=2 exports=1 status=ready\n");
	return 0;
}

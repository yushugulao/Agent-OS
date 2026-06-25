#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <research_platform_state.h>

static const char *PROGRAMS[] = {
	"rp_catalog",
	"rp_planner",
	"rp_retriever",
	"rp_analyst",
	"rp_reviewer",
	"rp_writer",
	"rp_repair",
	"rp_auditor",
	"rp_query",
	"rp_evidence",
	"rp_package",
	"rp_compare_plain",
};

static int run_child(const char *program)
{
	int pid = fork();
	if (pid == 0) {
		char *argv[] = { (char *)program, 0 };
		if (exec(program, argv) < 0) {
			printf("research_platform_orchestrator: exec_failed program=%s\n", program);
			exit(1);
		}
		exit(1);
	}
	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		printf("research_platform_orchestrator: wait_failed program=%s\n", program);
		return 0;
	}
	if (code != 0) {
		printf("research_platform_orchestrator: child_failed program=%s code=%d\n", program, code);
		return 0;
	}
	return 1;
}

int main(void)
{
	int total = (int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0]));
	int ok = 0;
	printf("research_platform_orchestrator: start programs=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
	}
	printf("research_platform_orchestrator: programs_ok=%d programs_total=%d\n", ok, total);
	if (ok != total) {
		printf("research_platform_orchestrator: failed\n");
		return 1;
	}
	int state_ok = 1;
	state_ok = state_ok && rp_file_contains("rp_status", "catalog=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "planner=planned");
	state_ok = state_ok && rp_file_contains("rp_status", "retriever=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "analyst=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "reviewer=accepted");
	state_ok = state_ok && rp_file_contains("rp_status", "writer=packaged");
	state_ok = state_ok && rp_file_contains("rp_status", "repair=recovered");
	state_ok = state_ok && rp_file_contains("rp_status", "auditor=passed");
	state_ok = state_ok && rp_file_contains("rp_status", "query=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "evidence=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "package=ready");
	state_ok = state_ok && rp_file_contains("rp_status", "compare=ready");
	state_ok = state_ok && rp_file_contains("rp_audit", "status=passed");
	state_ok = state_ok && rp_file_contains("rp_compare", "plain_kernel=passed");
	printf("research_platform_orchestrator: state_ok=%d\n", state_ok);
	if (!state_ok) {
		printf("research_platform_orchestrator: failed\n");
		return 1;
	}
	printf("research_platform_orchestrator: passed\n");
	return 0;
}

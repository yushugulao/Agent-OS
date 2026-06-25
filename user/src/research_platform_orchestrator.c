#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static const char *PROGRAMS[] = {
	"rp_planner",
	"rp_retriever",
	"rp_analyst",
	"rp_reviewer",
	"rp_writer",
	"rp_repair",
	"rp_auditor",
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
	printf("research_platform_orchestrator: start roles=%d\n", total);
	for (int i = 0; i < total; i++) {
		ok += run_child(PROGRAMS[i]);
	}
	printf("research_platform_orchestrator: roles_ok=%d roles_total=%d\n", ok, total);
	if (ok != total) {
		printf("research_platform_orchestrator: failed\n");
		return 1;
	}
	printf("research_platform_orchestrator: passed\n");
	return 0;
}

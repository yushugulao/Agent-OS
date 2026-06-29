#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("labbench_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

int main(void)
{
	char *argv[] = { "agentbench_ucore", 0 };
	int pid;
	int status = 0;

	printf("labbench_ucore: Agent-OS laboratory benchmark entry\n");
	pid = fork();
	check(pid >= 0, "fork agentbench");
	if (pid == 0) {
		exec("agentbench_ucore", argv);
		printf("labbench_ucore: exec failed\n");
		exit(1);
	}
	check(waitpid(pid, &status) == pid, "wait agentbench");
	check(status == 0, "agentbench status");
	printf("labbench_ucore: parent passed\n");
	return 0;
}

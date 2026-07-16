#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define CHILD_FIRST_ROUNDS 160
#define PARENT_FIRST_ROUNDS 160
#define ORPHAN_RESOURCE_ROUNDS 136
#define ORPHAN_WAIT_ROUNDS  4096
#define RELEASE_BLOCK_SIZE  1024

static char release_block[RELEASE_BLOCK_SIZE];

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("procreap_ucore: check failed: %s\n", message);
	exit(1);
}

static void wait_success(int pid, const char *message)
{
	int status = -1;

	check(pid > 0, message);
	check(waitpid(pid, &status) == pid, "wait direct child");
	check(status == 0, "direct child exit status");
}

// The pipe reaches EOF only after the leaf has closed its last write reference
// in exit. The intermediate parent then exits without waiting for that zombie.
static void child_first_round(void)
{
	int helper = fork();

	check(helper >= 0, "fork child-first parent");
	if (helper == 0) {
		int death_pipe[2];
		int leaf;
		char byte;

		if (pipe(death_pipe) < 0)
			exit(10);
		leaf = fork();
		if (leaf < 0)
			exit(11);
		if (leaf == 0) {
			close(death_pipe[0]);
			exit(0);
		}
		close(death_pipe[1]);
		if (read(death_pipe[0], &byte, 1) != -1)
			exit(12);
		close(death_pipe[0]);
		exit(0);
	}
	wait_success(helper, "child-first parent created");
}

// A live child becomes kernel-owned when its parent exits. It reports after
// getppid() exposes that ownership, then exits without a user-space reaper.
static void parent_first_round(void)
{
	int report_pipe[2];
	int helper;
	char byte = 0;

	check(pipe(report_pipe) == 0, "create parent-first report pipe");
	helper = fork();
	check(helper >= 0, "fork parent-first parent");
	if (helper == 0) {
		int leaf;

		close(report_pipe[0]);
		leaf = fork();
		if (leaf < 0)
			exit(20);
		if (leaf == 0) {
			int rounds = 0;

			while (getppid() != 0 && rounds++ < ORPHAN_WAIT_ROUNDS)
				sched_yield();
			if (getppid() != 0)
				exit(21);
			byte = 'k';
			if (write(report_pipe[1], &byte, 1) != 1)
				exit(22);
			exit(0);
		}
		exit(0);
	}
	close(report_pipe[1]);
	wait_success(helper, "parent-first parent created");
	check(read(report_pipe[0], &byte, 1) == 1 && byte == 'k',
	      "kernel-owned child report");
	check(read(report_pipe[0], &byte, 1) == -1,
	      "kernel-owned child exit closes pipe");
	check(close(report_pipe[0]) == 0, "close parent-first report pipe");
}

// The leaf wakes its parent immediately before exit. The kernel teardown
// checkpoint lets that parent transfer ownership after exit has begun, while
// descriptor order lets the root observe resource release before continuing.
static void orphan_resource_round(void)
{
	int done_pipe[2];
	int helper;
	char byte = 0;

	check(pipe(done_pipe) == 0, "create release completion pipe");
	helper = fork();
	check(helper >= 0, "fork release-race parent");
	if (helper == 0) {
		int ready_pipe[2];
		int leaf;

		if (pipe(ready_pipe) < 0)
			exit(30);
		leaf = fork();
		if (leaf < 0)
			exit(31);
		if (leaf == 0) {
			int fd;

			close(done_pipe[0]);
			close(ready_pipe[0]);
			fd = open("reap-race.tmp", O_CREATE | O_WRONLY | O_TRUNC);
			if (fd < 0 || fd >= done_pipe[1])
				exit(32);
			if (write(fd, release_block, sizeof(release_block)) !=
			    sizeof(release_block))
				exit(33);
			if (unlink("reap-race.tmp") < 0)
				exit(34);
			byte = 'r';
			if (write(ready_pipe[1], &byte, 1) != 1)
				exit(35);
			exit(0);
		}
		close(done_pipe[0]);
		close(ready_pipe[1]);
		if (read(ready_pipe[0], &byte, 1) != 1 || byte != 'r')
			exit(36);
		close(ready_pipe[0]);
		exit(0);
	}
	close(done_pipe[1]);
	wait_success(helper, "release-race parent created");
	check(read(done_pipe[0], &byte, 1) == -1,
	      "release-race orphan completed");
	check(close(done_pipe[0]) == 0, "close release completion pipe");
}

static void direct_wait_probe(void)
{
	int child = fork();

	check(child >= 0, "fork final probe");
	if (child == 0)
		exit(0);
	wait_success(child, "final probe created");
}

int main(void)
{
	printf("procreap_ucore: process lifecycle verification\n");
	for (int i = 0; i < CHILD_FIRST_ROUNDS; i++)
		child_first_round();
	printf("procreap_ucore: child-first=%d\n", CHILD_FIRST_ROUNDS);
	for (int i = 0; i < PARENT_FIRST_ROUNDS; i++)
		parent_first_round();
	printf("procreap_ucore: parent-first=%d\n", PARENT_FIRST_ROUNDS);
	for (int i = 0; i < ORPHAN_RESOURCE_ROUNDS; i++)
		orphan_resource_round();
	printf("procreap_ucore: orphan-resource=%d\n",
	       ORPHAN_RESOURCE_ROUNDS);
	direct_wait_probe();
	printf("procreap_ucore: parent passed\n");
	return 0;
}

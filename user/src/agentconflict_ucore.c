#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TARGET_FILE "edtarget"
#define READY_FILE  "edready"
#define DONE_FILE   "eddone"

static struct agent_file_edit_state conflict_state;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentconflict_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void remove_if_exists(const char *name)
{
	unlink(name);
}

static void write_marker(const char *name, const char *text)
{
	int fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "marker open");
	check(write(fd, text, strlen(text)) == (ssize_t)strlen(text),
	      "marker write");
	close(fd);
}

static int file_exists(const char *name)
{
	int fd = open(name, O_RDONLY);

	if (fd < 0)
		return 0;
	close(fd);
	return 1;
}

static void wait_for_file(const char *name)
{
	for (int i = 0; i < 20000; i++) {
		if (file_exists(name))
			return;
		sched_yield();
	}
	check(0, "wait marker");
}

static void write_target_initial(void)
{
	int fd;

	remove_if_exists(TARGET_FILE);
	remove_if_exists(READY_FILE);
	remove_if_exists(DONE_FILE);
	fd = open(TARGET_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "target create");
	check(write(fd, "initial", 7) == 7, "target initial write");
	close(fd);
}

static void agent_a_holder(void)
{
	struct agent_file_edit_state state;
	int fd;
	int rc;

	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(TARGET_FILE, 0, 200, &state);
	check(rc == 0, "holder begin");
	check(state.active == 1, "holder active");
	check(state.owner_pid == getpid(), "holder owner");
	write_marker(READY_FILE, "ready");
	wait_for_file(DONE_FILE);
	fd = open(TARGET_FILE, O_WRONLY);
	check(fd >= 0, "holder open");
	check(write(fd, "A", 1) == 1, "holder write");
	close(fd);
	rc = agent_file_edit_commit(state.lease_id, state.base_version, &state);
	check(rc == 0, "holder commit");
	check(state.active == 0, "holder released");
	check(state.current_version == state.base_version + 1,
	      "holder version");
	printf("agentconflict_ucore: owner_commit=1 version=%d\n",
	       (int)state.current_version);
	exit(0);
}

static void agent_b_conflicter(void)
{
	struct agent_file_edit_state state;
	int fd;
	int rc;
	ssize_t n;

	wait_for_file(READY_FILE);
	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(TARGET_FILE, 0, 200, &state);
	check(rc == AGENT_STATUS_CONFLICT, "conflict begin status");
	check(state.active == 1, "conflict state active");
	check(state.owner_pid > 0 && state.owner_pid != getpid(),
	      "conflict owner");
	fd = open(TARGET_FILE, O_WRONLY);
	check(fd >= 0, "conflict open");
	n = write(fd, "B", 1);
	close(fd);
	check(n == -1, "conflict direct write denied");
	fd = open(TARGET_FILE, O_WRONLY | O_TRUNC);
	check(fd < 0, "conflict truncate denied");
	check(unlink(TARGET_FILE) < 0, "conflict unlink denied");
	check(agent_file_edit_state(TARGET_FILE, &state) == 0,
	      "conflict state query");
	check(state.active == 1, "state query active");
	check(state.conflict_count >= 3, "conflict count");
	printf("agentconflict_ucore: conflict_denied=1 direct_write_denied=1 owner=%d conflicts=%d\n",
	       state.owner_pid, (int)state.conflict_count);
	write_marker(DONE_FILE, "done");
	exit(0);
}

static void agent_c_version_check(void)
{
	struct agent_file_edit_state state;
	uint64 base;
	int fd;
	int rc;

	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(TARGET_FILE, 0, 200, &state);
	check(rc == 0, "version begin");
	base = state.base_version;
	rc = agent_file_edit_commit(state.lease_id, base + 99, &state);
	check(rc == AGENT_STATUS_STALE, "stale commit status");
	check(state.active == 1, "stale keeps lease");
	check(agent_file_edit_abort(state.lease_id) == 0, "abort stale lease");
	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(TARGET_FILE, 0, 200, &state);
	check(rc == 0, "version begin second");
	base = state.base_version;
	fd = open(TARGET_FILE, O_WRONLY);
	check(fd >= 0, "version open");
	check(write(fd, "C", 1) == 1, "version write");
	close(fd);
	rc = agent_file_edit_commit(state.lease_id, base, &state);
	check(rc == 0, "version commit");
	check(state.current_version == base + 1, "version commit bump");
	printf("agentconflict_ucore: stale_commit=1 versioned_commit=1 base=%d current=%d\n",
	       (int)base, (int)state.current_version);
	exit(0);
}

static void run_parent(void)
{
	int pid_a;
	int pid_b;
	int pid_c;
	int status = 0;

	write_target_initial();
	check(agent_file_edit_begin(TARGET_FILE, 0, 10, &conflict_state) == -1,
	      "plain begin denied");
	printf("agentconflict_ucore: plain_process_denied=1\n");

	pid_a = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid_a >= 0, "create holder");
	if (pid_a == 0)
		agent_a_holder();
	pid_b = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid_b >= 0, "create conflicter");
	if (pid_b == 0)
		agent_b_conflicter();
	check(waitpid(pid_b, &status) == pid_b, "wait conflicter");
	check(status == 0, "conflicter status");
	check(waitpid(pid_a, &status) == pid_a, "wait holder");
	check(status == 0, "holder status");

	pid_c = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid_c >= 0, "create version checker");
	if (pid_c == 0)
		agent_c_version_check();
	check(waitpid(pid_c, &status) == pid_c, "wait version checker");
	check(status == 0, "version status");
	printf("agentconflict_ucore: passed\n");
}

int main(void)
{
	printf("agentconflict_ucore: Agent file edit conflict test\n");
	run_parent();
	printf("agentconflict_ucore: parent passed\n");
	return 0;
}

#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TRUSTED_TARGET "at_orch"
#define WRONG_ROLE_TARGET "at_sentinel"
#define UNTRUSTED_COPY "evilcopy"

static char copy_buffer[512];
static volatile int mutable_state;

__attribute__((noinline)) static void code_write_target(void)
{
	asm volatile("" ::: "memory");
}

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agenttrust_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void copy_file(const char *source, const char *target)
{
	int in = open(source, O_RDONLY);
	int out;
	ssize_t n;

	check(in >= 0, "open copy source");
	out = open(target, O_CREATE | O_WRONLY | O_TRUNC);
	check(out >= 0, "open copy target");
	while ((n = read(in, copy_buffer, sizeof(copy_buffer))) > 0) {
		ssize_t written = 0;

		while (written < n) {
			ssize_t step = write(out, copy_buffer + written,
					     (size_t)(n - written));
			check(step > 0, "copy image bytes");
			written += step;
		}
	}
	check(n == 0, "read copy source");
	check(close(in) == 0, "close copy source");
	check(close(out) == 0, "close copy target");
}

static void check_wx_image(void)
{
	int fd;

	mutable_state = 73;
	check(mutable_state == 73, "writable data segment");
	check(thread_create(copy_buffer, 0) == -1,
	      "non-executable data segment");
	fd = open("agenttrust_ucore", O_RDONLY);
	check(fd >= 0, "open code write probe source");
	check(read(fd, (void *)code_write_target, 1) == -1,
	      "read-only code segment");
	check(close(fd) == 0, "close code write probe source");
	printf("agenttrust_ucore: wx_image=1\n");
}

static void check_immutable_image(void)
{
	char before[8];
	char after[8];
	int fd = open(TRUSTED_TARGET, O_RDONLY);

	check(fd >= 0, "trusted image readable");
	check(read(fd, before, sizeof(before)) == sizeof(before),
	      "read trusted image");
	check(close(fd) == 0, "close trusted image");
	check(open(TRUSTED_TARGET, O_WRONLY) == -1,
	      "deny trusted write open");
	check(open(TRUSTED_TARGET, O_RDWR) == -1,
	      "deny trusted read-write open");
	check(open(TRUSTED_TARGET, O_WRONLY | O_TRUNC) == -1,
	      "deny trusted truncate");
	check(open(TRUSTED_TARGET, O_CREATE | O_WRONLY | O_TRUNC) == -1,
	      "deny trusted create-existing");
	check(unlink(TRUSTED_TARGET) == -1, "deny trusted unlink");

	fd = open(TRUSTED_TARGET, O_RDONLY);
	check(fd >= 0, "trusted image remains readable");
	check(read(fd, after, sizeof(after)) == sizeof(after),
	      "reread trusted image");
	check(close(fd) == 0, "close trusted image again");
	for (int i = 0; i < (int)sizeof(before); i++)
		check(before[i] == after[i], "trusted image unchanged");
	printf("agenttrust_ucore: immutable_image=1\n");
}

static void run_exec_probe(int role, const char *image, const char *argument,
			   int should_succeed)
{
	int pid = agent_create_role(role);
	int status = -1;

	check(pid >= 0, "create exec probe agent");
	if (pid == 0) {
		char *argv[] = { (char *)image, (char *)argument, 0 };

		if (exec(image, argv) < 0)
			exit(should_succeed ? 2 : 0);
		exit(3);
	}
	check(waitpid(pid, &status) == pid, "wait exec probe");
	check(status == 0, should_succeed ? "trusted agent exec" :
					      "untrusted agent exec denied");
}

static void check_exec_cache(void)
{
	struct agent_performance_snapshot before;
	struct agent_performance_snapshot after;

	memset(&before, 0, sizeof(before));
	memset(&after, 0, sizeof(after));
	check(agent_performance_snapshot(&before) == AGENT_STATUS_OK,
	      "read exec cache counters");
	check(before.version == AGENT_PERFORMANCE_SNAPSHOT_VERSION &&
	      before.struct_size == sizeof(before), "exec cache counter ABI");
	run_exec_probe(AGENT_ROLE_ORCHESTRATOR, TRUSTED_TARGET,
		       "--trusted-probe", 1);
	run_exec_probe(AGENT_ROLE_ORCHESTRATOR, TRUSTED_TARGET,
		       "--trusted-probe", 1);
	check(agent_performance_snapshot(&after) == AGENT_STATUS_OK,
	      "reread exec cache counters");
	check(after.exec_cache_misses > before.exec_cache_misses,
	      "cold exec records misses");
	check(after.exec_cache_hits > before.exec_cache_hits,
	      "warm exec reuses RX pages");
	check(after.exec_cache_shared_pages > before.exec_cache_shared_pages,
	      "warm exec maps shared RX pages");
	printf("agenttrust_ucore: exec_cache hits=%llu misses=%llu shared=%llu\n",
	       after.exec_cache_hits - before.exec_cache_hits,
	       after.exec_cache_misses - before.exec_cache_misses,
	       after.exec_cache_shared_pages - before.exec_cache_shared_pages);
}

int main(int argc, char **argv)
{
	struct agent_info info;

	if (argc > 1 && strcmp(argv[1], "--trusted-probe") == 0) {
		check(agent_info(&info) == 0, "trusted probe info");
		check(info.is_agent &&
		      info.agent_role == AGENT_ROLE_ORCHESTRATOR,
		      "trusted probe role");
		printf("agenttrust_ucore: trusted_agent_exec=1\n");
		return 0;
	}
	if (argc > 1 && strcmp(argv[1], "--untrusted-probe") == 0) {
		printf("agenttrust_ucore: untrusted image executed\n");
		return 9;
	}

	printf("agenttrust_ucore: executable trust test\n");
	check_wx_image();
	check_immutable_image();
	check(agent_create_role(AGENT_ROLE_SENTINEL) == AGENT_STATUS_DENIED,
	      "bootstrap role boundary");
	printf("agenttrust_ucore: bootstrap_role_boundary=1\n");
	unlink(UNTRUSTED_COPY);
	copy_file("agenttrust_ucore", UNTRUSTED_COPY);
	check_exec_cache();
	run_exec_probe(AGENT_ROLE_ORCHESTRATOR, WRONG_ROLE_TARGET,
		       "--untrusted-probe", 0);
	run_exec_probe(AGENT_ROLE_ORCHESTRATOR, UNTRUSTED_COPY,
		       "--untrusted-probe", 0);
	check(unlink(UNTRUSTED_COPY) == 0, "remove untrusted copy");
	printf("agenttrust_ucore: role_image_binding=1\n");
	printf("agenttrust_ucore: parent passed\n");
	return 0;
}

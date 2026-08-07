#include <stddef.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <user_stack_policy.h>

#define EXEC_ARG_LIMIT 32
#define NON_NUL_PATH_SIZE 320
#define TEST_PAGE_SIZE 4096
#define WAIT_WAKE_ROUNDS 32
#define EXEC_LAYOUT_BOUNDARY_MODE "exec-argv-layout-boundary"
#define EXEC_LAYOUT_OVERFLOW_MODE "exec-argv-layout-overflow"
#define EXEC_LAYOUT_BOUNDARY_ARG_BYTES 945
#define EXEC_LAYOUT_OVERFLOW_ARG_BYTES 961

static char non_nul_path[NON_NUL_PATH_SIZE];
static char *too_many_argv[EXEC_ARG_LIMIT + 2];
static char exec_layout_boundary_arg[EXEC_LAYOUT_BOUNDARY_ARG_BYTES];
static char exec_layout_overflow_arg[EXEC_LAYOUT_OVERFLOW_ARG_BYTES];
static char *exec_layout_boundary_argv[] = {
	EXEC_LAYOUT_BOUNDARY_MODE,
	exec_layout_boundary_arg,
	NULL,
};
static char *exec_layout_overflow_argv[] = {
	EXEC_LAYOUT_OVERFLOW_MODE,
	exec_layout_overflow_arg,
	NULL,
};
static int initial_pid;
static int concurrent_pipe[2];
static int wait_mutex;
static volatile int wait_worker_started;
static volatile int wait_worker_release;
static volatile int wait_worker_spurious;

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("usersafety_ucore: check failed: %s\n", message);
	exit(1);
}

static void check_live(const char *group)
{
	TimeVal now;

	check(getpid() == initial_pid, "process identity changed");
	check(sys_get_time(&now, 0) == 0, "valid time copyout");
	printf("usersafety_ucore: live after %s\n", group);
}

static void test_pointer_bounds(void)
{
	char stack_byte;
	const void *low_invalid = (const void *)(uintptr_t)8;
	const void *overflow = (const void *)(uintptr_t)(ULONG_MAX - 3);
	const void *cross_page = (const void *)(((uintptr_t)&stack_byte &
					       ~(TEST_PAGE_SIZE - 1)) +
					      TEST_PAGE_SIZE - 4);

	check(write(stdout, low_invalid, 1) == -1,
	      "unmapped console source");
	check(write(stdout, overflow, 8) == -1,
	      "overflowing console source");
	check(open((const char *)low_invalid, O_RDONLY) == -1,
	      "unmapped path source");
	check(open("/", O_RDONLY) == -1, "reject non-file inode");
	check(exec("/", NULL) == -1, "reject non-file exec image");
	check(read(16, (void *)low_invalid, 1) == -1,
	      "read fd boundary");
	check(write(16, low_invalid, 1) == -1,
	      "write fd boundary");
	check(close(16) == -1, "close fd boundary");
	check(write(stdout, cross_page, 8) == -1,
	      "cross-page console source");
	check(sys_get_time((TimeVal *)cross_page, 0) == -1,
	      "cross-page time destination");
	check_live("pointer bounds");
}

static void test_string_bounds(void)
{
	memset(non_nul_path, 'p', sizeof(non_nul_path));
	check(open(non_nul_path, O_RDONLY) == -1,
	      "unterminated open path");
	check(exec(non_nul_path, NULL) == -1,
	      "unterminated exec path");
	check_live("string bounds");
}

static void test_exec_argv_bounds(void)
{
	static char arg[] = "x";
	int pid;
	int status;

	for (int i = 0; i <= EXEC_ARG_LIMIT; i++)
		too_many_argv[i] = arg;
	too_many_argv[EXEC_ARG_LIMIT + 1] = NULL;
	check(exec("usersafety_ucore", too_many_argv) == -1,
	      "too many exec arguments");
	check(exec("usersafety_ucore", (char **)(uintptr_t)8) == -1,
	      "unmapped exec argv");
	check_live("exec argv bounds");

	memset(exec_layout_boundary_arg, 'x', sizeof(exec_layout_boundary_arg));
	exec_layout_boundary_arg[sizeof(exec_layout_boundary_arg) - 1] = 0;
	pid = fork();
	check(pid >= 0, "fork exact argv layout child");
	if (pid == 0) {
		exec("usersafety_ucore", exec_layout_boundary_argv);
		exit(1);
	}
	check(waitpid(pid, &status) == pid && status == 0,
	      "exact exec argv layout budget");

	/* 原始字节仍可容纳；逐字符串对齐和指针使总量达到 1040 字节。 */
	memset(exec_layout_overflow_arg, 'x', sizeof(exec_layout_overflow_arg));
	exec_layout_overflow_arg[sizeof(exec_layout_overflow_arg) - 1] = 0;
	check(exec("usersafety_ucore", exec_layout_overflow_argv) == -1,
	      "complete exec argv layout budget");
	check_live("exec argv layout budget");
	printf("usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 over_limit_rejected=1 caller_live=1\n");
}

static void secondary_exec(void *arg)
{
	(void)arg;
	exit(exec("usersafety_ucore", NULL) == -1 ? 0 : 1);
}

static void blocked_pipe_read(void *arg)
{
	char byte;

	(void)arg;
	exit(read(concurrent_pipe[0], &byte, 1) == -1 ? 0 : 1);
}

static void test_thread_boundaries(void)
{
	int tid;

	check(thread_create((void *)(uintptr_t)8, NULL) == -1,
	      "unmapped thread entry");
	tid = thread_create(secondary_exec, NULL);
	check(tid > 0, "create secondary exec thread");
	check(waittid(tid) == 0, "secondary exec must be rejected");

	check(pipe(concurrent_pipe) == 0, "create concurrent close pipe");
	tid = thread_create(blocked_pipe_read, NULL);
	check(tid > 0, "create blocked pipe reader");
	sched_yield();
	check(close(concurrent_pipe[0]) == 0 &&
	      close(concurrent_pipe[1]) == 0,
	      "close pipe during active read");
	check(waittid(tid) == 0, "active pipe read keeps file reference");
	check_live("thread boundaries");
}

static void blocked_mutex_wait(void *arg)
{
	(void)arg;
	wait_worker_started = 1;
	for (;;) {
		if (mutex_lock(wait_mutex) < 0)
			exit(2);
		if (wait_worker_release) {
			mutex_unlock(wait_mutex);
			exit(0);
		}
		wait_worker_spurious++;
	}
}

static void test_directed_wakeup(void)
{
	int tid;
	int pid;
	int status;

	wait_mutex = mutex_blocking_create();
	check(wait_mutex >= 0 && mutex_lock(wait_mutex) == 0,
	      "hold directed wake mutex");
	wait_worker_started = 0;
	wait_worker_release = 0;
	wait_worker_spurious = 0;
	tid = thread_create(blocked_mutex_wait, NULL);
	check(tid > 0, "create directed wake worker");
	while (!wait_worker_started)
		sched_yield();
	sched_yield();

	for (int i = 0; i < WAIT_WAKE_ROUNDS; i++) {
		status = -1;
		pid = fork();
		check(pid >= 0, "fork directed wake child");
		if (pid == 0)
			exit(0);
		check(waitpid(pid, &status) == pid && status == 0,
		      "wait directed wake child");
		sched_yield();
	}
	check(wait_worker_spurious == 0, "mutex waiter woke for child exit");
	wait_worker_release = 1;
	check(mutex_unlock(wait_mutex) == 0, "release directed wake mutex");
	check(waittid(tid) == 0, "join directed wake worker");
	check_live("directed wakeup");
}

static void test_exec_transaction(void)
{
	static char empty_path[] = "usersafety.empty";
	static char *child_argv[] = { "usersafety_ucore", "exec-child", NULL };
	int fd;
	int pid;
	int status = -1;

	fd = open(empty_path, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0 && close(fd) == 0, "create empty exec image");
	check(exec(empty_path, NULL) == -1, "reject empty exec image");
	check_live("failed exec transaction");

	pid = fork();
	check(pid >= 0, "fork successful exec child");
	if (pid == 0) {
		exec("usersafety_ucore", child_argv);
		exit(1);
	}
	check(waitpid(pid, &status) == pid && status == 0,
	      "commit successful exec image");
	check_live("successful exec transaction");
}

static void test_pipe_buffers(void)
{
	int fds[2];
	char byte = 'q';
	char out = 0;
	void *invalid = (void *)(uintptr_t)8;
	void *overflow = (void *)(uintptr_t)(ULONG_MAX - 3);

	check(pipe(invalid) == -1, "unmapped pipe descriptor array");
	check(pipe(fds) == 0, "create buffer pipe");
	check(write(fds[1], invalid, 0) == 0, "zero pipe write");
	check(read(fds[0], invalid, 0) == 0, "zero pipe read");
	check(write(fds[1], invalid, 1) == -1,
	      "unmapped pipe source");
	check(write(fds[1], overflow, 8) == -1,
	      "overflowing pipe source");
	check(write(fds[1], &byte, ULONG_MAX) == -1,
	      "oversized pipe write");
	check(read(fds[0], &out, ULONG_MAX) == -1,
	      "oversized pipe read");
	check(write(fds[1], &byte, 1) == 1, "seed buffer pipe");
	check(read(fds[0], invalid, 1) == -1,
	      "unmapped pipe destination");
	check(read(fds[0], &out, 1) == 1 && out == byte,
	      "failed pipe copy preserves data");
	check(close(fds[0]) == 0 && close(fds[1]) == 0,
	      "close buffer pipe");
	check_live("pipe buffers");
}

static void test_wait_copyout(void)
{
	int pid;
	int status = 0;

	pid = fork();
	check(pid >= 0, "fork wait child");
	if (pid == 0)
		exit(37);
	check(waitpid(pid, (int *)(uintptr_t)8) == -1,
	      "reject wait status destination");
	check(waitpid(pid, &status) == pid,
	      "failed wait copy must not reap child");
	check(status == 37, "preserve child status");
	check_live("wait copyout");
}

static void test_time_copyout(void)
{
	check(sys_get_time((TimeVal *)(uintptr_t)8, 0) == -1,
	      "unmapped time destination");
	check(sys_get_time((TimeVal *)(uintptr_t)(ULONG_MAX - 7), 0) == -1,
	      "overflowing time destination");
	check_live("time copyout");
}

static void test_fd_directions(void)
{
	static const char path[] = "usersafety.tmp";
	int fds[2];
	int fd;
	char first = 'a';
	char second = 'b';
	char out = 0;

	check(write(stdin, &first, 1) == -1, "stdin is not writable");
	check(read(stdout, &out, 1) == -1, "stdout is not readable");

	check(pipe(fds) == 0, "create direction pipe");
	check(write(fds[1], &first, 1) == 1, "seed direction pipe");
	check(read(fds[1], &out, 1) == -1,
	      "pipe write end is not readable");
	check(write(fds[0], &second, 1) == -1,
	      "pipe read end is not writable");
	check(read(fds[0], &out, 1) == 1 && out == first,
	      "direction failures preserve pipe data");
	check(close(fds[0]) == 0 && close(fds[1]) == 0,
	      "close direction pipe");

	fd = open(path, O_CREATE | O_WRONLY);
	check(fd >= 0, "create write-only file");
	check(write(fd, &first, 1) == 1, "write write-only file");
	check(read(fd, &out, 1) == -1, "write-only file is not readable");
	check(close(fd) == 0, "close write-only file");

	fd = open(path, O_RDONLY);
	check(fd >= 0, "open read-only file");
	check(write(fd, &second, 1) == -1,
	      "read-only file is not writable");
	check(read(fd, &out, 1) == 1 && out == first,
	      "read-only file contents");
	check(close(fd) == 0, "close read-only file");
	check_live("fd directions");
}

static void test_file_rollback(void)
{
	static const char path[] = "usersafety.pool";
	int held[13];
	int fds[2];
	int seed;

	seed = open(path, O_CREATE | O_RDWR);
	check(seed >= 0 && close(seed) == 0, "create rollback file");

	for (int i = 0; i < 12; i++) {
		held[i] = open(path, O_RDWR);
		check(held[i] >= 0, "reserve descriptor for pipe rollback");
	}
	check(pipe(fds) == -1, "pipe rollback with one descriptor left");
	for (int i = 0; i < 12; i++)
		check(close(held[i]) == 0, "release rollback descriptor");
	check(pipe(fds) == 0, "pipe allocation after rollback");
	check(close(fds[0]) == 0 && close(fds[1]) == 0,
	      "close rollback pipe");

	for (int i = 0; i < 13; i++) {
		held[i] = open(path, O_RDWR);
		check(held[i] >= 0, "fill descriptor table");
	}
	check(open(path, O_RDWR) == -1, "open rollback on full table");
	for (int i = 0; i < 13; i++)
		check(close(held[i]) == 0, "release full descriptor table");
	check_live("file rollback");
}

static void test_semaphore_inputs(void)
{
	int sid;

	check(semaphore_create(-1) == -1, "negative semaphore count");
	check(semaphore_up(-1) == -1, "negative semaphore id for up");
	check(semaphore_down(-1) == -1, "negative semaphore id for down");
	sid = semaphore_create(0x7fffffff);
	check(sid >= 0, "maximum semaphore count");
	check(semaphore_up(sid) == -1, "semaphore count overflow");
	check_live("semaphore inputs");
}

int main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[0], EXEC_LAYOUT_BOUNDARY_MODE) == 0) {
		printf("usersafety_ucore: exec argv boundary child passed\n");
		return 0;
	}
	if (argc == 2 && strcmp(argv[0], EXEC_LAYOUT_OVERFLOW_MODE) == 0) {
		printf("usersafety_ucore: check failed: exec argv layout overflow accepted\n");
		return 1;
	}
	if (argc == 2 && strcmp(argv[1], "exec-child") == 0) {
		printf("usersafety_ucore: exec child passed\n");
		return 0;
	}
	if (argc != 1) {
		printf("usersafety_ucore: check failed: exec argv overflow accepted\n");
		return 1;
	}

	initial_pid = getpid();
	printf("usersafety_ucore: syscall boundary verification\n");
	test_pointer_bounds();
	test_string_bounds();
	test_exec_argv_bounds();
	test_thread_boundaries();
	test_directed_wakeup();
	test_pipe_buffers();
	test_wait_copyout();
	test_time_copyout();
	test_fd_directions();
	test_file_rollback();
	test_semaphore_inputs();
	test_exec_transaction();
	printf("usersafety_ucore: parent passed\n");
	return 0;
}

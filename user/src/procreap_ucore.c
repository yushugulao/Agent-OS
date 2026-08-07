#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define CHILD_FIRST_ROUNDS 160
#define PARENT_FIRST_ROUNDS 160
#define ORPHAN_RESOURCE_ROUNDS 136
#define ORPHAN_WAIT_ROUNDS  4096
#define BLOCKED_SYSCALL_ROUNDS 384
#define BLOCKED_READERS 6
#define BLOCKED_SETTLE_ROUNDS 32
#define RELEASE_BLOCK_SIZE  1024
#define DELAYED_WAIT_CHILDREN 8
#define CHILD_RECORD_PRESSURE_ROUNDS 256
#define LIVE_DOMAIN_PRESSURE_ROUNDS 256

static char release_block[RELEASE_BLOCK_SIZE];
static int blocked_pipes[BLOCKED_READERS][2];
static int blocked_ready;
static int exit_wait_ready;
static int exit_wait_sem;
static int exit_wait_mutex;
static int exit_wait_cond;
static int exit_wait_cond_mutex;
static volatile int exit_wait_unexpected;

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

// 叶进程在 exit 中关闭最后一个写端后，管道才到 EOF；中间父进程不等待僵尸子进程便退出。
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

// 父进程退出后，存活子进程由内核接管；它在 getppid() 显示该归属后报告并退出。
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

// 叶进程退出前唤醒父进程；清理检查点允许随后转移归属，描述符顺序保证根进程先观察资源释放。
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

static void blocked_reader(void *arg)
{
	int *pipe_fds = arg;
	char byte;

	if (semaphore_up(blocked_ready) < 0)
		exit(40);
	exit(read(pipe_fds[0], &byte, 1) == -1 ? 0 : 40);
}

// 每个阻塞 read() 都持有临时文件引用；主线程不 join，内核须先取消并展开系统调用再释放共享资源。
static void blocked_syscall_round(void)
{
	int helper = fork();

	check(helper >= 0, "fork blocked-syscall helper");
	if (helper == 0) {
		blocked_ready = semaphore_create(0);
		if (blocked_ready < 0)
			exit(41);
		for (int i = 0; i < BLOCKED_READERS; i++) {
			if (pipe(blocked_pipes[i]) < 0)
				exit(42);
			if (thread_create(blocked_reader, blocked_pipes[i]) < 0)
				exit(43);
		}
		for (int i = 0; i < BLOCKED_READERS; i++)
			if (semaphore_down(blocked_ready) < 0)
				exit(44);
		for (int i = 0; i < BLOCKED_SETTLE_ROUNDS; i++)
			sched_yield();
		exit(0);
	}
	wait_success(helper, "blocked-syscall helper created");
}

static void resource_probe(void)
{
	int probe[2];

	check(pipe(probe) == 0, "create final resource probe");
	check(close(probe[0]) == 0, "close resource probe reader");
	check(close(probe[1]) == 0, "close resource probe writer");
}

static void semaphore_waiter(void *arg)
{
	(void)arg;
	if (semaphore_up(exit_wait_ready) < 0)
		exit(50);
	semaphore_down(exit_wait_sem);
	exit_wait_unexpected = 1;
	exit(0);
}

static void mutex_waiter(void *arg)
{
	(void)arg;
	if (semaphore_up(exit_wait_ready) < 0)
		exit(51);
	mutex_lock(exit_wait_mutex);
	exit_wait_unexpected = 1;
	exit(0);
}

static void condvar_waiter(void *arg)
{
	(void)arg;
	if (mutex_lock(exit_wait_cond_mutex) < 0 ||
	    semaphore_up(exit_wait_ready) < 0)
		exit(52);
	condvar_wait(exit_wait_cond, exit_wait_cond_mutex);
	exit_wait_unexpected = 1;
	exit(0);
}

static void wait_queue_exit_probe(void)
{
	int helper = fork();

	check(helper >= 0, "fork wait-queue helper");
	if (helper == 0) {
		exit_wait_ready = semaphore_create(0);
		exit_wait_sem = semaphore_create(0);
		exit_wait_mutex = mutex_blocking_create();
		exit_wait_cond = condvar_create();
		exit_wait_cond_mutex = mutex_blocking_create();
		exit_wait_unexpected = 0;
		if (exit_wait_ready < 0 || exit_wait_sem < 0 ||
		    exit_wait_mutex < 0 || exit_wait_cond < 0 ||
		    exit_wait_cond_mutex < 0 || mutex_lock(exit_wait_mutex) < 0)
			exit(53);
		if (thread_create(semaphore_waiter, 0) < 0 ||
		    thread_create(mutex_waiter, 0) < 0 ||
		    thread_create(condvar_waiter, 0) < 0)
			exit(54);
		for (int i = 0; i < 3; i++)
			if (semaphore_down(exit_wait_ready) < 0)
				exit(55);
		for (int i = 0; i < BLOCKED_SETTLE_ROUNDS; i++)
			sched_yield();
		if (exit_wait_unexpected)
			exit(56);
		exit(0);
	}
	wait_success(helper, "wait-queue helper created");
}

static void direct_wait_probe(void)
{
	int child = fork();

	check(child >= 0, "fork final probe");
	if (child == 0)
		exit(0);
	wait_success(child, "final probe created");
}

// 退出凭据须跨 proc 槽复用保存，并支持乱序 waitpid，无需让死进程常驻进程表。
static void delayed_wait_probe(void)
{
	int children[DELAYED_WAIT_CHILDREN];
	int status = -1;
	int child;

	for (int i = 0; i < DELAYED_WAIT_CHILDREN; i++) {
		children[i] = fork();
		check(children[i] >= 0, "fork delayed-wait child");
		if (children[i] == 0)
			exit(70 + i);
		// 子进程已在 yield 前入队，下一次分配前可复用其 proc 槽。
		sched_yield();
	}
	child = wait(&status);
	check(child == children[0], "wait oldest delayed child");
	check(status == 70, "oldest delayed child exit status");
	for (int i = 4; i <= 6; i += 2) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "delete middle delayed child by pid");
		check(status == 70 + i, "middle delayed child exit status");
	}
	int remaining[] = { 1, 2, 3, 5, 7 };
	for (uint i = 0; i < sizeof(remaining) / sizeof(remaining[0]); i++) {
		status = -1;
		child = wait(&status);
		check(child == children[remaining[i]],
		      "preserve delayed completion FIFO");
		check(status == 70 + remaining[i],
		      "FIFO delayed child exit status");
	}
	check(waitpid(children[0], &status) == -1,
	      "consume delayed status once");
	check(wait(&status) == -1, "all delayed statuses consumed");
}

// 子状态配额可停止违规父进程，但已保留结果不得占用无关进程所需的可执行槽。
static void unreaped_parent_pressure(void)
{
	int ready_pipe[2];
	int release_pipe[2];
	int holder;
	int probe;
	int status = -1;
	char token = 0;

	check(pipe(ready_pipe) == 0, "create pressure ready pipe");
	check(pipe(release_pipe) == 0, "create pressure release pipe");
	holder = fork();
	check(holder >= 0, "fork unreaped pressure parent");
	if (holder == 0) {
		int denied = 0;

		close(ready_pipe[0]);
		close(release_pipe[1]);
		for (int i = 0; i < CHILD_RECORD_PRESSURE_ROUNDS; i++) {
			int child = fork();

			if (child == 0)
				exit(i & 0x3f);
			if (child < 0)
				denied++;
			else
				sched_yield();
		}
		if (denied == 0)
			exit(80);
		token = 'R';
		if (write(ready_pipe[1], &token, 1) != 1)
			exit(81);
		close(ready_pipe[1]);
		if (read(release_pipe[0], &token, 1) != 1 || token != 'X')
			exit(82);
		close(release_pipe[0]);
		exit(0);
	}
	close(ready_pipe[1]);
	close(release_pipe[0]);
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'R',
	      "unreaped parent reached private quota");
	close(ready_pipe[0]);
	probe = fork();
	check(probe >= 0, "unrelated fork survives unreaped pressure");
	if (probe == 0) {
		close(release_pipe[1]);
		exit(91);
	}
	check(waitpid(probe, &status) == probe && status == 91,
	      "wait unrelated pressure probe");
	token = 'X';
	check(write(release_pipe[1], &token, 1) == 1,
	      "release unreaped pressure parent");
	close(release_pipe[1]);
	status = -1;
	check(waitpid(holder, &status) == holder && status == 0,
	      "reap pressure parent");
}

// 所有后代固定在同一资源域；保持叶进程存活可验证配额无法跨代逃逸，启动进程仍能创建隔离任务。
static void live_domain_pressure(void)
{
	int ready_pipe[2];
	int release_pipe[2];
	int holder;
	int probe;
	int status = -1;
	char token = 0;

	check(pipe(ready_pipe) == 0, "create live-domain ready pipe");
	check(pipe(release_pipe) == 0, "create live-domain release pipe");
	holder = fork();
	check(holder >= 0, "fork live-domain holder");
	if (holder == 0) {
		int expander;

		close(ready_pipe[0]);
		close(release_pipe[1]);
		expander = fork();
		if (expander < 0)
			exit(83);
		if (expander == 0) {
			int denied = 0;
			int reaped = 0;

			for (int i = 0; i < LIVE_DOMAIN_PRESSURE_ROUNDS; i++) {
				int child = fork();

				if (child == 0) {
					close(ready_pipe[1]);
					if (read(release_pipe[0], &token, 1) != -1)
						exit(84);
					close(release_pipe[0]);
					exit(0);
				}
				if (child < 0) {
					denied = 1;
					break;
				}
				sched_yield();
			}
			if (!denied)
				exit(85);
			token = 'D';
			if (write(ready_pipe[1], &token, 1) != 1)
				exit(86);
			close(ready_pipe[1]);
			if (read(release_pipe[0], &token, 1) != -1)
				exit(87);
			close(release_pipe[0]);
			for (;;) {
				int child = wait(&status);

				if (child < 0)
					break;
				if (status != 0)
					exit(88);
				reaped++;
			}
			if (reaped == 0)
				exit(89);
			{
				int replacement = fork();

				if (replacement < 0)
					exit(90);
				if (replacement == 0)
					exit(73);
				status = -1;
				if (waitpid(replacement, &status) != replacement ||
				    status != 73)
					exit(92);
			}
			exit(0);
		}
		close(ready_pipe[1]);
		if (read(release_pipe[0], &token, 1) != -1)
			exit(93);
		close(release_pipe[0]);
		status = -1;
		if (waitpid(expander, &status) != expander || status != 0)
			exit(94);
		exit(0);
	}
	close(ready_pipe[1]);
	close(release_pipe[0]);
	check(read(ready_pipe[0], &token, 1) == 1 && token == 'D',
	      "descendant reaches live-domain quota");
	close(ready_pipe[0]);
	probe = fork();
	check(probe >= 0, "peer domain survives live pressure");
	if (probe == 0) {
		close(release_pipe[1]);
		exit(91);
	}
	check(waitpid(probe, &status) == probe && status == 91,
	      "wait live-domain peer probe");
	close(release_pipe[1]);
	status = -1;
	check(waitpid(holder, &status) == holder && status == 0,
	      "reap live-domain holder");
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
	for (int i = 0; i < BLOCKED_SYSCALL_ROUNDS; i++)
		blocked_syscall_round();
	printf("procreap_ucore: blocked-syscall=%d\n",
	       BLOCKED_SYSCALL_ROUNDS);
	wait_queue_exit_probe();
	printf("procreap_ucore: wait-queue cancellation passed\n");
	resource_probe();
	delayed_wait_probe();
	printf("procreap_ucore: detached-wait=%d\n",
	       DELAYED_WAIT_CHILDREN);
	unreaped_parent_pressure();
	printf("procreap_ucore: unreaped-parent-isolated=1\n");
	live_domain_pressure();
	printf("procreap_ucore: live-domain-limit=1\n");
	printf("procreap_ucore: lineage-bypass-denied=1\n");
	printf("procreap_ucore: live-quota-returned=1\n");
	printf("procreap_ucore: peer-domain-isolated=1\n");
	direct_wait_probe();
	printf("procreap_ucore: parent passed\n");
	return 0;
}

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define SOURCE_FILE "frsource"
#define DOMAIN_LIMIT 16
#define GLOBAL_DOMAINS 3
#define HIDDEN_HOLDER_FILES 12

static int hidden_pipe[2];
static int hidden_ready;

static void fail_child(int code)
{
	exit(code);
}

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("fileresource_ucore: check failed: %s\n", message);
	exit(1);
}

static void wait_success(int pid, const char *message)
{
	int status = -1;

	check(pid > 0, message);
	check(waitpid(pid, &status) == pid, "wait direct child");
	check(status == 0, "direct child exit status");
}

static void close_stdio(void)
{
	close(0);
	close(1);
	close(2);
}

static void open_sources(int *fds, int count, int failure_code)
{
	for (int i = 0; i < count; i++) {
		fds[i] = open(SOURCE_FILE, O_RDONLY);
		if (fds[i] < 0)
			fail_child(failure_code);
	}
}

static void close_sources(int *fds, int count, int failure_code)
{
	for (int i = 0; i < count; i++)
		if (close(fds[i]) < 0)
			fail_child(failure_code);
}

static void hidden_reader(void *arg)
{
	char byte;

	(void)arg;
	if (semaphore_up(hidden_ready) < 0)
		fail_child(20);
	if (read(hidden_pipe[0], &byte, 1) != -1)
		fail_child(21);
	// 所属进程在系统调用阻塞时退出；若在清理前返回，说明写端关闭过早。
	fail_child(22);
}

// 阻塞 read 持有临时引用时，关闭描述符不能销毁文件对象；worker 的空闲 FD 槽应仍受该隐藏引用计费。
static void blocking_exit_phase(void)
{
	int report[2];
	int gate[2];
	int holder;
	char token = 0;

	check(pipe(report) == 0, "create blocking report pipe");
	check(pipe(gate) == 0, "create blocking gate pipe");
	holder = fork();
	check(holder >= 0, "fork blocking holder");
	if (holder == 0) {
		int files[HIDDEN_HOLDER_FILES];
		int worker;

		close(report[0]);
		close_stdio();
		if (pipe(hidden_pipe) < 0)
			fail_child(23);
		worker = fork();
		if (worker < 0)
			fail_child(24);
		if (worker == 0) {
			int first;
			int second;

			close(gate[1]);
			close(hidden_pipe[0]);
			close(hidden_pipe[1]);
			if (read(gate[0], &token, 1) != 1 || token != 'X')
				fail_child(25);
			first = open(SOURCE_FILE, O_RDONLY);
			second = open(SOURCE_FILE, O_RDONLY);
			if (first < 0 || second < 0)
				fail_child(26);
			if (open(SOURCE_FILE, O_RDONLY) != -1)
				fail_child(27);
			token = 'H';
			if (write(report[1], &token, 1) != 1)
				fail_child(28);
			exit(0);
		}
		close(gate[0]);
		hidden_ready = semaphore_create(0);
		if (hidden_ready < 0 || thread_create(hidden_reader, 0) < 0)
			fail_child(29);
		if (semaphore_down(hidden_ready) < 0)
			fail_child(30);
		for (int i = 0; i < 32; i++)
			sched_yield();
		if (close(hidden_pipe[0]) < 0)
			fail_child(31);
		open_sources(files, HIDDEN_HOLDER_FILES, 32);
		token = 'X';
		if (write(gate[1], &token, 1) != 1 || close(gate[1]) < 0)
			fail_child(33);
		wait_success(worker, "wait blocking worker");
		// 写端、源描述符和阻塞线程留给进程清理，其最终引用必须原子退款。
		exit(0);
	}
	close(report[1]);
	close(gate[0]);
	close(gate[1]);
	check(read(report[0], &token, 1) == 1 && token == 'H',
	      "blocked temporary reference is charged");
	check(close(report[0]) == 0, "close blocking report pipe");
	wait_success(holder, "blocking holder completed");
	printf("fileresource_ucore: blocking_pin_bounded=1\n");
}

// holder 保持 14 个对象；同域子进程关闭继承描述符后，本地 FD 充足但资源域仅余一个对象槽。
// 双端 pipe 失败须回滚两个 FD 预留和首端点，释放一个计费后完整 pipe 才能成功。
static void domain_and_rollback_phase(void)
{
	int report[2];
	int holder;
	char token = 0;

	check(pipe(report) == 0, "create domain report pipe");
	holder = fork();
	check(holder >= 0, "fork domain holder");
	if (holder == 0) {
		int files[DOMAIN_LIMIT - 2];
		int worker;

		close(report[0]);
		close_stdio();
		open_sources(files, DOMAIN_LIMIT - 2, 40);
		worker = fork();
		if (worker < 0)
			fail_child(41);
		if (worker == 0) {
			int pipe_fds[2];
			int first;
			int last;
			int final;

			close_sources(files, DOMAIN_LIMIT - 2, 42);
			first = open(SOURCE_FILE, O_RDONLY);
			if (first < 0)
				fail_child(43);
			for (int i = 0; i < 16; i++)
				if (pipe(pipe_fds) != -1)
					fail_child(44);
			if (close(first) < 0)
				fail_child(45);
			// 任一预留泄漏都会耗尽剩余 FD；释放一个对象计费后，双端 pipe 必须恢复可用。
			if (pipe(pipe_fds) < 0)
				fail_child(46);
			if (close(pipe_fds[0]) < 0 || close(pipe_fds[1]) < 0)
				fail_child(47);
			last = open(SOURCE_FILE, O_RDONLY);
			if (last < 0)
				fail_child(48);
			final = open(SOURCE_FILE, O_RDONLY);
			if (final < 0)
				fail_child(49);
			if (open(SOURCE_FILE, O_RDONLY) != -1)
				fail_child(50);
			token = 'R';
			if (write(report[1], &token, 1) != 1)
				fail_child(51);
			exit(0);
		}
		wait_success(worker, "wait domain worker");
		exit(0);
	}
	close(report[1]);
	check(read(report[0], &token, 1) == 1 && token == 'R',
	      "domain limit and pipe rollback");
	check(close(report[0]) == 0, "close domain report pipe");
	wait_success(holder, "domain holder completed");
	printf("fileresource_ucore: exit_reuse=1\n");
	printf("fileresource_ucore: pipe_rollback=1\n");
	printf("fileresource_ucore: domain_limit=1\n");
}

static void global_holder(int ready[2], int release[2], int probe[2],
			  char marker)
{
	int files[DOMAIN_LIMIT - 3];
	int worker;
	char token = 0;

	close(ready[0]);
	close(release[1]);
	close(probe[0]);
	close(probe[1]);
	close_stdio();
	open_sources(files, DOMAIN_LIMIT - 3, 50);
	worker = fork();
	if (worker < 0)
		fail_child(51);
	if (worker == 0) {
		int owned[3];

		close_sources(files, DOMAIN_LIMIT - 3, 52);
		open_sources(owned, 3, 53);
		if (open(SOURCE_FILE, O_RDONLY) != -1)
			fail_child(54);
		if (write(ready[1], &marker, 1) != 1)
			fail_child(55);
		if (read(release[0], &token, 1) != 1 || token != 'X')
			fail_child(56);
		exit(0);
	}
	close(ready[1]);
	close(release[0]);
	wait_success(worker, "wait global worker");
	exit(0);
}

// 三个普通域耗尽普通水位；第四个域应被拒绝，同时启动保留仍可用。
static void global_waterline_phase(void)
{
	int ready[2];
	int release[2];
	int probe[2];
	int holders[GLOBAL_DOMAINS];
	int probe_pid;
	int reserved_fd;
	int reserved_pipe[2];
	int seen = 0;
	char token;

	check(pipe(ready) == 0, "create global ready pipe");
	check(pipe(release) == 0, "create global release pipe");
	check(pipe(probe) == 0, "create global probe pipe");
	for (int i = 0; i < GLOBAL_DOMAINS; i++) {
		holders[i] = fork();
		check(holders[i] >= 0, "fork global holder");
		if (holders[i] == 0)
			global_holder(ready, release, probe, 'A' + i);
	}
	close(ready[1]);
	close(release[0]);
	for (int i = 0; i < GLOBAL_DOMAINS; i++) {
		check(read(ready[0], &token, 1) == 1,
		      "ordinary domain reached limit");
		check(token >= 'A' && token < 'A' + GLOBAL_DOMAINS,
		      "ordinary domain marker");
		seen |= 1 << (token - 'A');
	}
	check(seen == (1 << GLOBAL_DOMAINS) - 1,
	      "all ordinary domains reached limit");

	probe_pid = fork();
	check(probe_pid >= 0, "fork ordinary waterline probe");
	if (probe_pid == 0) {
		close(probe[0]);
		close(ready[0]);
		close(release[1]);
		if (open(SOURCE_FILE, O_RDONLY) != -1)
			fail_child(60);
		token = 'G';
		if (write(probe[1], &token, 1) != 1)
			fail_child(61);
		exit(0);
	}
	close(probe[1]);
	check(read(probe[0], &token, 1) == 1 && token == 'G',
	      "ordinary global waterline enforced");
	check(close(probe[0]) == 0, "close global probe pipe");
	wait_success(probe_pid, "ordinary waterline probe completed");
	printf("fileresource_ucore: ordinary_waterline=1\n");

	reserved_fd = open(SOURCE_FILE, O_RDONLY);
	check(reserved_fd >= 0, "reserved process opens at waterline");
	check(pipe(reserved_pipe) == 0,
	      "reserved process creates pipe at waterline");
	check(close(reserved_pipe[0]) == 0 && close(reserved_pipe[1]) == 0,
	      "close reserved progress pipe");
	check(close(reserved_fd) == 0, "close reserved progress file");
	printf("fileresource_ucore: reserved_progress=1\n");

	for (int i = 0; i < GLOBAL_DOMAINS; i++) {
		token = 'X';
		check(write(release[1], &token, 1) == 1,
		      "release ordinary domain");
	}
	check(close(release[1]) == 0, "close global release pipe");
	check(close(ready[0]) == 0, "close global ready pipe");
	for (int i = 0; i < GLOBAL_DOMAINS; i++)
		wait_success(holders[i], "global holder completed");
}

int main(void)
{
	int fixture = open(SOURCE_FILE, O_RDONLY);

	check(fixture >= 0, "open host fixture");
	check(close(fixture) == 0, "close host fixture");
	blocking_exit_phase();
	domain_and_rollback_phase();
	global_waterline_phase();
	printf("fileresource_ucore: parent passed\n");
	return 0;
}

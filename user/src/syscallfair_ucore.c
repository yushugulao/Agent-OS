#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define STRESS_BYTES (64 * 1024)
#define TRUNCATE_BYTES (192 * 1024)
#define OFFSET_BLOCK_BYTES 1024
#define OFFSET_BLOCKS 32
#define OFFSET_READERS 2

static const char console_begin[] = "SYSCALLFAIR_CONSOLE_BEGIN\n";
static const char console_peer[] = "SYSCALLFAIR_CONSOLE_PEER\n";
static const char console_end[] = "SYSCALLFAIR_CONSOLE_END\n";
static const char inode_begin[] = "SYSCALLFAIR_INODE_BEGIN\n";
static const char inode_peer[] = "SYSCALLFAIR_INODE_PEER\n";
static const char inode_short[] = "SYSCALLFAIR_INODE_SHORT\n";
static const char inode_end[] = "SYSCALLFAIR_INODE_END\n";
static const char trunc_begin[] = "SYSCALLFAIR_TRUNC_BEGIN\n";
static const char trunc_peer[] = "SYSCALLFAIR_TRUNC_PEER\n";
static const char trunc_end[] = "SYSCALLFAIR_TRUNC_END\n";
static const char passed[] = "syscallfair_ucore: parent passed\n";
static char stress[STRESS_BYTES];
static volatile int inode_observer_ready;
static volatile int inode_observed;
static volatile int trunc_observer_ready;
static volatile int trunc_started;
static volatile int trunc_done;
static volatile int trunc_observed;
static volatile int offset_ready[OFFSET_READERS];
static volatile int offset_start;
static volatile int offset_error;
static int offset_fd;
static unsigned char offset_tags[OFFSET_READERS][OFFSET_BLOCKS / 2];

static void write_stdout(const char *data, int length)
{
	int done = 0;

	while (done < length) {
		int n = write(stdout, data + done, length - done);

		if (n <= 0)
			exit(3);
		done += n;
	}
}

static void fail(const char *message)
{
	static const char prefix[] = "syscallfair_ucore: check failed: ";
	static const char newline[] = "\n";

	write_stdout(prefix, sizeof(prefix) - 1);
	write_stdout(message, strlen(message));
	write_stdout(newline, sizeof(newline) - 1);
	exit(1);
}

static void check(int ok, const char *message)
{
	if (!ok)
		fail(message);
}

static int start_peer(int gate[2], const char *marker, int marker_length,
		      int inherited_fd)
{
	char token;
	int pid;

	check(pipe(gate) == 0, "create phase gate");
	pid = fork();
	check(pid >= 0, "fork phase peer");
	if (pid == 0) {
		close(gate[1]);
		if (inherited_fd >= 0)
			close(inherited_fd);
		if (read(gate[0], &token, 1) != 1)
			exit(4);
		if (sched_yield() != 0)
			exit(5);
		if (write(stdout, marker, marker_length) != marker_length)
			exit(6);
		close(gate[0]);
		exit(0);
	}

	close(gate[0]);
	check(sched_yield() == 0, "park phase peer on gate");
	return pid;
}

static void finish_peer(int gate[2], int pid)
{
	int status = -1;

	close(gate[1]);
	check(waitpid(pid, &status) == pid, "wait phase peer");
	check(status == 0, "phase peer completed");
}

static void release_peer(int gate[2])
{
	char token = 'G';

	check(write(gate[1], &token, 1) == 1, "release phase peer");
}

static void run_console_phase(void)
{
	int gate[2];
	int pid;
	int n;

	pid = start_peer(gate, console_peer, sizeof(console_peer) - 1, -1);
	release_peer(gate);
	n = write(stdout, stress, sizeof(stress));
	check(n == sizeof(stress), "single long console write completed");
	finish_peer(gate, pid);
}

static void inode_observer(void *arg)
{
	const char *path = arg;
	char byte;

	inode_observer_ready = 1;
	for (;;) {
		int fd = open(path, O_RDONLY);

		if (fd >= 0) {
			int n = read(fd, &byte, 1);

			close(fd);
			if (n > 0) {
				inode_observed = 1;
				if (write(stdout, inode_peer,
					  sizeof(inode_peer) - 1) !=
				    sizeof(inode_peer) - 1)
					exit(6);
				exit(0);
			}
		}
		if (sched_yield() != 0)
			exit(5);
	}
}

static void run_inode_phase(void)
{
	static const char path[] = "fairdata";
	int fd;
	int first;
	int status;
	int tid;
	int total;

	unlink(path);
	fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create inode data file");
	inode_observer_ready = 0;
	inode_observed = 0;
	tid = thread_create(inode_observer, (void *)path);
	check(tid >= 0, "create inode observer");
	while (!inode_observer_ready)
		check(sched_yield() == 0, "start inode observer");
	write_stdout(inode_begin, sizeof(inode_begin) - 1);
	first = write(fd, stress, sizeof(stress));
	check(first > 0, "first inode write made progress");
	check(first < sizeof(stress), "inode budget produced a short write");
	check(kernel_work_last_preemptions() > 0,
	      "first inode write crossed a kernel scheduling boundary");
	while ((status = waittid(tid)) == -2)
		check(sched_yield() == 0, "finish inode observer");
	check(status == 0 && inode_observed, "inode observer made progress");
	write_stdout(inode_short, sizeof(inode_short) - 1);
	total = first;
	while (total < sizeof(stress)) {
		int n = write(fd, stress + total, sizeof(stress) - total);

		check(n > 0, "continued inode write made progress");
		total += n;
	}
	write_stdout(inode_end, sizeof(inode_end) - 1);
	check(close(fd) == 0, "close inode data file");
	check(unlink(path) == 0, "remove inode data file");
}

static void fill_file(const char *path)
{
	int fd;
	int total = 0;

	unlink(path);
	fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create truncate data file");
	while (total < TRUNCATE_BYTES) {
		int remaining = TRUNCATE_BYTES - total;
		int chunk = remaining < STRESS_BYTES ? remaining : STRESS_BYTES;
		int n = write(fd, stress, chunk);

		check(n > 0, "fill truncate data file");
		total += n;
	}
	check(close(fd) == 0, "close truncate data file");
}

static void truncate_observer(void *arg)
{
	const char *path = arg;
	char byte;
	int empty_rounds = 0;

	trunc_observer_ready = 1;
	while (!trunc_started)
		sched_yield();
	while (!trunc_done) {
		int fd = open(path, O_RDONLY);

		if (fd >= 0) {
			int n = read(fd, &byte, 1);

			if (n == 0 && !trunc_done) {
				close(fd);
				empty_rounds++;
				if (empty_rounds >= 2 && !trunc_done) {
					trunc_observed = 1;
					if (write(stdout, trunc_peer,
						  sizeof(trunc_peer) - 1) !=
						    sizeof(trunc_peer) - 1)
						exit(8);
					exit(0);
				}
				check(sched_yield() == 0,
				      "separate truncate observations");
				continue;
			}
			close(fd);
			empty_rounds = 0;
		}
		sched_yield();
	}
	exit(7);
}

static void run_truncate_phase(void)
{
	static const char path[] = "fairtrunc";
	int fd;
	long preemptions;
	int peer_progress;
	int status;
	int tid;

	fill_file(path);
	trunc_observer_ready = 0;
	trunc_started = 0;
	trunc_done = 0;
	trunc_observed = 0;
	tid = thread_create(truncate_observer, (void *)path);
	check(tid >= 0, "create truncate observer");
	while (!trunc_observer_ready)
		check(sched_yield() == 0, "start truncate observer");
	write_stdout(trunc_begin, sizeof(trunc_begin) - 1);
	fd = open(path, O_WRONLY | O_TRUNC);
	check(fd >= 0, "truncate populated file");
	/* 截断只发布回收令牌，观察窗口覆盖后续有界批次排空。 */
	trunc_started = 1;
	check(sync() == 0, "drain deferred truncate reclaim");
	trunc_done = 1;
	peer_progress = trunc_observed;
	preemptions = kernel_work_last_preemptions();
	status = waittid(tid);
	check(status == 0, "truncate observer completed during reclaim drain");
	check(peer_progress > 0,
	      "truncate observer progressed before reclaim drain returned");
	check(preemptions > 0,
	      "truncate reclaim crossed a kernel scheduling boundary");
	printf("syscallfair_ucore: truncate_preemptions=%lld peer_progress=%d\n",
	       (long long)preemptions, peer_progress);
	write_stdout(trunc_end, sizeof(trunc_end) - 1);
	check(close(fd) == 0, "close truncated file");
	check(unlink(path) == 0, "remove truncated file");
}

static void offset_reader(void *arg)
{
	int reader = (int)(long)arg;
	char block[OFFSET_BLOCK_BYTES];

	offset_ready[reader] = 1;
	while (!offset_start)
		sched_yield();
	for (int i = 0; i < OFFSET_BLOCKS / OFFSET_READERS; i++) {
		int n = read(offset_fd, block, sizeof(block));

		if (n != sizeof(block)) {
			offset_error = 1;
			exit(9);
		}
		offset_tags[reader][i] = (unsigned char)block[0];
	}
	exit(0);
}

static void run_shared_offset_phase(void)
{
	static const char path[] = "fairoffset";
	static const char pressure_path[] = "fairoffsetpressure";
	char block[OFFSET_BLOCK_BYTES];
	int seen[OFFSET_BLOCKS];
	int tids[OFFSET_READERS];
	int pressure;

	unlink(path);
	unlink(pressure_path);
	offset_fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	check(offset_fd >= 0, "create shared-offset file");
	for (int i = 0; i < OFFSET_BLOCKS; i++) {
		memset(block, i + 1, sizeof(block));
		check(write(offset_fd, block, sizeof(block)) == sizeof(block),
		      "populate shared-offset file");
	}
	check(close(offset_fd) == 0, "close shared-offset writer");

	/* 驱逐目标块，确保并发读取覆盖冷缓存路径。 */
	pressure = open(pressure_path, O_CREATE | O_WRONLY | O_TRUNC);
	check(pressure >= 0, "create offset cache pressure file");
	memset(block, 0x5a, sizeof(block));
	for (int i = 0; i < 64; i++)
		check(write(pressure, block, sizeof(block)) == sizeof(block),
		      "populate offset cache pressure file");
	check(close(pressure) == 0, "close offset cache pressure file");

	offset_fd = open(path, O_RDONLY);
	check(offset_fd >= 0, "open shared-offset reader");
	for (int i = 0; i < OFFSET_READERS; i++)
		offset_ready[i] = 0;
	offset_start = 0;
	offset_error = 0;
	memset(offset_tags, 0, sizeof(offset_tags));
	for (int i = 0; i < OFFSET_READERS; i++) {
		tids[i] = thread_create(offset_reader, (void *)(long)i);
		check(tids[i] >= 0, "create shared-offset reader");
	}
	for (int i = 0; i < OFFSET_READERS; i++)
		while (!offset_ready[i])
			check(sched_yield() == 0, "start shared-offset readers");
	offset_start = 1;
	for (int i = 0; i < OFFSET_READERS; i++) {
		int status;

		while ((status = waittid(tids[i])) == -2)
			check(sched_yield() == 0, "finish shared-offset reader");
		check(status == 0, "shared-offset reader exited cleanly");
	}
	check(!offset_error, "shared-offset reader completed every block");
	memset(seen, 0, sizeof(seen));
	for (int reader = 0; reader < OFFSET_READERS; reader++)
		for (int i = 0; i < OFFSET_BLOCKS / OFFSET_READERS; i++) {
			int tag = offset_tags[reader][i];

			check(tag >= 1 && tag <= OFFSET_BLOCKS,
			      "shared offset returned a valid block identity");
			seen[tag - 1]++;
		}
	for (int i = 0; i < OFFSET_BLOCKS; i++)
		check(seen[i] == 1, "shared offset consumed each block once");
	check(close(offset_fd) == 0, "close shared-offset reader");
	check(unlink(path) == 0, "remove shared-offset file");
	check(unlink(pressure_path) == 0, "remove offset pressure file");
}

int main(void)
{
	int pid;
	int status = -1;

	memset(stress, '.', sizeof(stress));
	memcpy(stress, console_begin, sizeof(console_begin) - 1);
	memcpy(stress + sizeof(stress) - (sizeof(console_end) - 1),
	       console_end, sizeof(console_end) - 1);
	write_stdout("syscallfair_ucore: kernel work budget verification\n",
		     sizeof("syscallfair_ucore: kernel work budget verification\n") - 1);
	pid = fork();
	check(pid >= 0, "fork fairness worker");
	if (pid == 0) {
		run_console_phase();
		memset(stress, '.', sizeof(stress));
		run_inode_phase();
		run_truncate_phase();
		run_shared_offset_phase();
		exit(0);
	}
	check(waitpid(pid, &status) == pid, "wait fairness worker");
	check(status == 0, "fairness worker exited cleanly");
	write_stdout(passed, sizeof(passed) - 1);
	return 0;
}

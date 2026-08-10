#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BLOCK_SIZE 1024
#define BLOCK_LIMIT 18
#define INODE_LIMIT 8
#define SPONSOR_DATA_BLOCKS 13
#define SPONSOR_CHARGED_BLOCKS (SPONSOR_DATA_BLOCKS + 1)
#define FILL_BLOCKS (BLOCK_LIMIT - SPONSOR_CHARGED_BLOCKS - 1)
#define EMPTY_FILLERS (INODE_LIMIT - 4)

#define SPONSOR_FILE "pqsponsor"
#define ONE_FILE "pqone"
#define GROW_FILE "pqgrow"
#define FILL_FILE "pqfill"
#define EXTRA_FILE "pqextra"
#define DENIED_FILE "pqdenied"
#define AGAIN_FILE "pqagain"
#define CRASH_PHASE_FILE "pqphase"
#define CRASH_ORPHAN_FILE "pqorphan"

#define PHASE_SEEDED 41
#define PHASE_REBUILT 42

static char block_buf[BLOCK_SIZE];
static char read_buf[BLOCK_SIZE];

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("fspquota_ucore: check failed: %s\n", message);
	exit(1);
}

static void make_filler_name(char name[12], int index)
{
	static const char hex[] = "0123456789abcdef";

	name[0] = 'p';
	name[1] = 'q';
	name[2] = 'i';
	for (int i = 0; i < 8; i++) {
		name[10 - i] = hex[index & 15];
		index >>= 4;
	}
	name[11] = 0;
}

static void write_fully(int fd, const char *buf, int size,
			const char *message)
{
	int done = 0;

	while (done < size) {
		int n = write(fd, buf + done, size - done);

		check(n > 0, message);
		done += n;
	}
}

static void read_fully(int fd, char *buf, int size, const char *message)
{
	int done = 0;

	while (done < size) {
		int n = read(fd, buf + done, size - done);

		check(n > 0, message);
		done += n;
	}
}

static int buffers_equal(const char *left, const char *right, int size)
{
	for (int i = 0; i < size; i++)
		if (left[i] != right[i])
			return 0;
	return 1;
}

static int path_exists(const char *path)
{
	int fd = open(path, O_RDONLY);

	if (fd < 0)
		return 0;
	check(close(fd) == 0, "close existence probe");
	return 1;
}

static void create_empty(const char *path)
{
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create empty quota file");
	check(close(fd) == 0, "close empty quota file");
}

static void write_new_blocks(const char *path, int blocks)
{
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create quota block file");
	for (int i = 0; i < blocks; i++)
		write_fully(fd, block_buf, sizeof(block_buf),
			    "write quota block");
	check(close(fd) == 0, "close quota block file");
}

static void adopt_sponsored_blocks(void)
{
	int fd = open(SPONSOR_FILE, O_WRONLY);

	check(fd >= 0, "open sponsored quota file");
	for (int i = 0; i < SPONSOR_DATA_BLOCKS; i++)
		write_fully(fd, block_buf, sizeof(block_buf),
			    "overwrite sponsored quota block");
	check(close(fd) == 0, "close sponsored quota file");
}

static void verify_file_blocks(const char *path, int blocks)
{
	int fd = open(path, O_RDONLY);

	check(fd >= 0, "open persistent quota file");
	for (int i = 0; i < blocks; i++) {
		memset(read_buf, 0, sizeof(read_buf));
		read_fully(fd, read_buf, sizeof(read_buf),
			   "read persistent quota block");
		check(buffers_equal(read_buf, block_buf, sizeof(read_buf)),
		      "persistent quota contents");
	}
	check(read(fd, read_buf, 1) == 0, "persistent quota file size");
	check(close(fd) == 0, "close persistent quota file");
}

static void verify_empty(const char *path)
{
	int fd = open(path, O_RDONLY);

	check(fd >= 0, "open persistent empty file");
	check(read(fd, read_buf, 1) == 0, "persistent empty file size");
	check(close(fd) == 0, "close persistent empty file");
}

static void verify_initial_state(void)
{
	char name[12];

	verify_file_blocks(SPONSOR_FILE, SPONSOR_DATA_BLOCKS);
	verify_file_blocks(ONE_FILE, 1);
	verify_empty(GROW_FILE);
	verify_file_blocks(FILL_FILE, FILL_BLOCKS);
	for (int i = 0; i < EMPTY_FILLERS; i++) {
		make_filler_name(name, i);
		verify_empty(name);
	}
}

static void verify_reused_state(void)
{
	char name[12];

	verify_file_blocks(SPONSOR_FILE, SPONSOR_DATA_BLOCKS);
	verify_empty(ONE_FILE);
	verify_file_blocks(GROW_FILE, 1);
	verify_file_blocks(FILL_FILE, FILL_BLOCKS);
	for (int i = 1; i < EMPTY_FILLERS; i++) {
		make_filler_name(name, i);
		verify_empty(name);
	}
	verify_empty(EXTRA_FILE);
}

static void expect_block_denied(const char *path, int existing_blocks)
{
	int fd = open(path, O_RDWR);

	check(fd >= 0, "open quota growth probe");
	for (int i = 0; i < existing_blocks; i++)
		read_fully(fd, read_buf, sizeof(read_buf),
			   "seek quota growth probe to eof");
	check(read(fd, read_buf, 1) == 0, "quota growth probe eof");
	check(write(fd, block_buf, sizeof(block_buf)) == -1,
	      "persistent block charge denies growth");
	check(write(fd, block_buf, sizeof(block_buf)) == -1,
	      "persistent block denial remains stable");
	check(close(fd) == 0, "close denied growth probe");
}

static void expect_inode_denied(const char *path)
{
	check(open(path, O_CREATE | O_WRONLY) == -1,
	      "persistent inode charge denies create");
}

static void fill_initial_state(void)
{
	char name[12];

	if (path_exists(SPONSOR_FILE))
		adopt_sponsored_blocks();
	else
		write_new_blocks(SPONSOR_FILE, SPONSOR_DATA_BLOCKS);
	write_new_blocks(ONE_FILE, 1);
	create_empty(GROW_FILE);
	write_new_blocks(FILL_FILE, FILL_BLOCKS);
	for (int i = 0; i < EMPTY_FILLERS; i++) {
		make_filler_name(name, i);
		create_empty(name);
	}
	verify_initial_state();
}

static void release_and_reuse(void)
{
	char name[12];
	int fd;

	fd = open(ONE_FILE, O_WRONLY | O_TRUNC);
	check(fd >= 0, "truncate one charged block");
	check(close(fd) == 0, "close truncated block file");
	check(sync() == 0, "drain released block charge");

	fd = open(GROW_FILE, O_WRONLY);
	check(fd >= 0, "open released block consumer");
	write_fully(fd, block_buf, sizeof(block_buf),
		    "reuse released block charge");
	check(close(fd) == 0, "close released block consumer");

	make_filler_name(name, 0);
	check(unlink(name) == 0, "release one charged inode");
	check(sync() == 0, "drain released inode charge");
	create_empty(EXTRA_FILE);
	verify_reused_state();
	expect_block_denied(GROW_FILE, 1);
	expect_inode_denied(AGAIN_FILE);
}

static void cleanup_initial_state(void)
{
	char name[12];

	check(unlink(SPONSOR_FILE) == 0, "remove sponsored quota file");
	check(unlink(ONE_FILE) == 0, "remove initial one-block file");
	check(unlink(GROW_FILE) == 0, "remove initial growth file");
	check(unlink(FILL_FILE) == 0, "remove initial fill file");
	for (int i = 0; i < EMPTY_FILLERS; i++) {
		make_filler_name(name, i);
		check(unlink(name) == 0, "remove initial inode filler");
	}
	check(sync() == 0, "drain initial quota cleanup");
}

static void leave_crash_orphan(void)
{
	int fd;

	create_empty(CRASH_PHASE_FILE);
	fd = open(CRASH_ORPHAN_FILE, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "create crash orphan");
	write_fully(fd, block_buf, sizeof(block_buf), "write crash orphan");
	check(unlink(CRASH_ORPHAN_FILE) == 0, "unlink open crash orphan");
	printf("fspquota_ucore: crash_orphan_ready=1\n");
	for (;;)
		sched_yield();
}

static void cleanup_reused_state(void)
{
	char name[12];

	check(unlink(SPONSOR_FILE) == 0, "remove reused sponsor file");
	check(unlink(ONE_FILE) == 0, "remove reused empty file");
	check(unlink(GROW_FILE) == 0, "remove reused block file");
	check(unlink(FILL_FILE) == 0, "remove reused fill file");
	for (int i = 1; i < EMPTY_FILLERS; i++) {
		make_filler_name(name, i);
		check(unlink(name) == 0, "remove reused inode filler");
	}
	check(unlink(EXTRA_FILE) == 0, "remove reused extra inode");
	check(sync() == 0, "drain reused quota cleanup");
}

static void seed_or_verify(void)
{
	if (path_exists(CRASH_PHASE_FILE)) {
		check(unlink(CRASH_PHASE_FILE) == 0,
		      "finish crash orphan phase");
		check(sync() == 0, "drain crash phase cleanup");
	}
	if (!path_exists(ONE_FILE)) {
		fill_initial_state();
		expect_block_denied(GROW_FILE, 0);
		expect_inode_denied(DENIED_FILE);
		exit(PHASE_SEEDED);
	}

	verify_initial_state();
	expect_block_denied(GROW_FILE, 0);
	expect_inode_denied(DENIED_FILE);
	release_and_reuse();
	exit(PHASE_REBUILT);
}

static void verify_relaunch_and_cleanup(void)
{
	verify_reused_state();
	expect_block_denied(GROW_FILE, 1);
	expect_inode_denied(AGAIN_FILE);
	cleanup_reused_state();
	exit(0);
}

static void verify_cleanup_reuse(void)
{
	fill_initial_state();
	expect_block_denied(GROW_FILE, 0);
	expect_inode_denied(DENIED_FILE);
	cleanup_initial_state();
	exit(0);
}

static int run_child(void (*entry)(void), const char *create_message,
		     const char *wait_message)
{
	int pid = fork();
	int status = -1;

	check(pid >= 0, create_message);
	if (pid == 0) {
		entry();
		exit(99);
	}
	check(waitpid(pid, &status) == pid, wait_message);
	return status;
}

int main(void)
{
	int status;

	memset(block_buf, 0x5a, sizeof(block_buf));
	if (!path_exists(ONE_FILE) && !path_exists(CRASH_PHASE_FILE))
		(void)run_child(leave_crash_orphan,
				"create crash orphan worker",
				"wait crash orphan worker");
	status = run_child(seed_or_verify, "create quota phase worker",
			   "wait quota phase worker");
	if (status == PHASE_SEEDED) {
		printf("fspquota_ucore: sponsored_object_charged=1 blocks=%d\n",
		       SPONSOR_CHARGED_BLOCKS);
		printf("fspquota_ucore: durable_fixture=1 blocks=%d inodes=%d owner_exited=1\n",
		       BLOCK_LIMIT, INODE_LIMIT);
		for (;;)
			sched_yield();
	}
	check(status == PHASE_REBUILT, "persistent quota rebuild phase");
	printf("fspquota_ucore: reboot_charge_persisted=1\n");
	printf("fspquota_ucore: deletion_reuse=1\n");

	status = run_child(verify_relaunch_and_cleanup,
			   "create persistent quota relaunch",
			   "wait persistent quota relaunch");
	check(status == 0, "persistent quota relaunch status");
	printf("fspquota_ucore: relaunch_charge_persisted=1 launches=2\n");

	status = run_child(verify_cleanup_reuse,
			   "create cleanup reuse worker",
			   "wait cleanup reuse worker");
	check(status == 0, "cleanup reuse worker status");
	printf("fspquota_ucore: cleanup_reuse=1\n");
	printf("fspquota_ucore: parent passed\n");
	return 0;
}

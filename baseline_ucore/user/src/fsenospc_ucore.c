#include <stddef.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BLOCK_SIZE 1024
#define INODE_TEST_LIMIT 32
#define CACHE_TEST_LIMIT 8

static char write_buf[BLOCK_SIZE * 2];
static char read_buf[BLOCK_SIZE * 2];
static int initial_pid;

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("fsenospc_ucore: check failed: %s\n", message);
	exit(1);
}

static void make_inode_name(char *name, int index)
{
	static const char hex[] = "0123456789abcdef";

	name[0] = 'e';
	name[1] = 'i';
	for (int i = 0; i < 8; i++) {
		name[9 - i] = hex[index & 15];
		index >>= 4;
	}
	name[10] = 0;
}

static void create_empty(const char *path)
{
	int fd = open(path, O_CREATE | O_RDWR | O_TRUNC);

	check(fd >= 0, "create fixture");
	check(close(fd) == 0, "close fixture");
}

static int buffer_equal(const char *left, const char *right, int size)
{
	for (int i = 0; i < size; i++)
		if (left[i] != right[i])
			return 0;
	return 1;
}

static void test_inode_exhaustion(void)
{
	char name[11];
	int created = 0;
	int fd;

	for (; created < INODE_TEST_LIMIT; created++) {
		make_inode_name(name, created);
		fd = open(name, O_CREATE | O_WRONLY);
		if (fd < 0)
			break;
		check(close(fd) == 0, "close inode filler");
	}
	check(created > 0 && created < INODE_TEST_LIMIT,
	      "inode exhaustion returns failure");
	make_inode_name(name, created);
	check(open(name, O_RDONLY) == -1, "failed create leaves no entry");
	check(open(name, O_CREATE | O_WRONLY) == -1,
	      "inode exhaustion remains recoverable");
	check(getpid() == initial_pid, "kernel survives inode exhaustion");

	make_inode_name(name, 0);
	check(unlink(name) == 0, "release one inode");
	fd = open(name, O_CREATE | O_WRONLY);
	check(fd >= 0 && close(fd) == 0, "reuse released inode");
	make_inode_name(name, created);
	check(open(name, O_CREATE | O_WRONLY) == -1,
	      "reused inode restores full state");

	for (int i = 0; i < created; i++) {
		make_inode_name(name, i);
		check(unlink(name) == 0, "cleanup inode filler");
	}
	printf("fsenospc_ucore: inode exhaustion survived\n");
}

static void test_inode_cache_exhaustion(void)
{
	char name[11];
	int fds[CACHE_TEST_LIMIT];
	int held = 0;
	int fd;

	for (int i = 0; i < CACHE_TEST_LIMIT; i++) {
		make_inode_name(name, i);
		name[1] = 'c';
		create_empty(name);
	}
	for (; held < CACHE_TEST_LIMIT; held++) {
		make_inode_name(name, held);
		name[1] = 'c';
		fds[held] = open(name, O_RDONLY);
		if (fds[held] < 0)
			break;
	}
	check(held > 0 && held < CACHE_TEST_LIMIT,
	      "inode cache exhaustion returns failure");
	check(getpid() == initial_pid, "kernel survives inode cache exhaustion");

	check(close(fds[--held]) == 0, "release one inode cache entry");
	make_inode_name(name, held + 1);
	name[1] = 'c';
	fd = open(name, O_RDONLY);
	check(fd >= 0 && close(fd) == 0, "reuse inode cache entry");
	while (held > 0)
		check(close(fds[--held]) == 0, "close inode cache filler");
	for (int i = 0; i < CACHE_TEST_LIMIT; i++) {
		make_inode_name(name, i);
		name[1] = 'c';
		check(unlink(name) == 0, "cleanup inode cache filler");
	}
	printf("fsenospc_ucore: inode cache exhaustion survived\n");
}

static void check_file_blocks(const char *path, int expected_blocks)
{
	int fd = open(path, O_RDONLY);
	int blocks = 0;
	int n;

	check(fd >= 0, "open filled file");
	for (;;) {
		n = read(fd, read_buf, BLOCK_SIZE);
		if (n == 0)
			break;
		check(n == BLOCK_SIZE, "read complete persisted block");
		check(buffer_equal(read_buf, write_buf, BLOCK_SIZE),
		      "persisted block contents");
		blocks++;
	}
	check(blocks == expected_blocks, "short write size is consistent");
	check(close(fd) == 0, "close filled file");
}

static void test_block_exhaustion(void)
{
	static const char reserve[] = "fs.reserve";
	static const char partial[] = "fs.partial";
	static const char fill[] = "fs.fill";
	int fd;
	int blocks = 0;
	int n;

	fd = open(fill, O_WRONLY | O_TRUNC);
	check(fd >= 0, "open block filler");
	for (;;) {
		n = write(fd, write_buf, BLOCK_SIZE);
		if (n < 0)
			break;
		check(n == BLOCK_SIZE, "single block write is complete");
		blocks++;
	}
	check(blocks > 0, "block filler made progress");
	check(write(fd, write_buf, 1) == -1,
	      "full disk write returns failure");
	check(close(fd) == 0, "close block filler");
	check(getpid() == initial_pid, "kernel survives block exhaustion");
	check_file_blocks(fill, blocks);

	fd = open(reserve, O_WRONLY | O_TRUNC);
	check(fd >= 0 && close(fd) == 0, "release one reserved block");
	fd = open(partial, O_WRONLY);
	check(fd >= 0, "open partial write file");
	check(write(fd, write_buf, sizeof(write_buf)) == BLOCK_SIZE,
	      "full disk reports a consistent short write");
	check(close(fd) == 0, "close partial write file");
	check_file_blocks(partial, 1);

	fd = open(fill, O_WRONLY | O_TRUNC);
	check(fd >= 0 && close(fd) == 0, "release filled blocks");
	fd = open(partial, O_WRONLY | O_TRUNC);
	check(fd >= 0, "truncate partial file");
	check(write(fd, write_buf, sizeof(write_buf)) == sizeof(write_buf),
	      "write succeeds after block reclamation");
	check(close(fd) == 0, "close recovered file");
	check_file_blocks(partial, 2);
	printf("fsenospc_ucore: block exhaustion survived\n");
}

static void test_unlink_lifetime(void)
{
	static const char path[] = "fs.open";
	char byte = 'u';
	int fd = open(path, O_CREATE | O_RDWR | O_TRUNC);

	check(fd >= 0, "create open-unlink file");
	check(write(fd, &byte, 1) == 1, "seed open-unlink file");
	check(unlink(path) == 0, "unlink open file");
	check(write(fd, &byte, 1) == 1, "old descriptor remains usable");
	check(open(path, O_RDONLY) == -1, "unlinked name is absent");
	check(close(fd) == 0, "close unlinked descriptor");
	fd = open(path, O_CREATE | O_WRONLY);
	check(fd >= 0 && close(fd) == 0, "recreate after last close");
	check(unlink(path) == 0, "cleanup recreated file");
}

int main(void)
{
	int fd;

	initial_pid = getpid();
	memset(write_buf, 0x5a, sizeof(write_buf));
	create_empty("fs.partial");
	create_empty("fs.fill");
	fd = open("fs.reserve", O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create block reserve");
	check(write(fd, write_buf, BLOCK_SIZE) == BLOCK_SIZE,
	      "seed block reserve");
	check(close(fd) == 0, "close block reserve");

	test_inode_exhaustion();
	test_inode_cache_exhaustion();
	test_block_exhaustion();
	test_unlink_lifetime();
	check(unlink("fs.reserve") == 0, "cleanup reserve");
	check(unlink("fs.partial") == 0, "cleanup partial");
	check(unlink("fs.fill") == 0, "cleanup fill");
	printf("fsenospc_ucore: parent passed\n");
	return 0;
}

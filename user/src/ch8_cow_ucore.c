#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define PAGE_BYTES 4096
#define ARENA_PAGES 12
#define FORK_ROUNDS 32

static unsigned char arena[ARENA_PAGES * PAGE_BYTES]
	__attribute__((aligned(PAGE_BYTES)));

static void fail(const char *message)
{
	printf("ch8_cow_ucore: check failed: %s\n", message);
	exit(1);
}

static unsigned char pattern(int page)
{
	return (unsigned char)(0x31U + (unsigned int)page * 13U);
}

static void check_parent_image(void)
{
	for (int page = 0; page < ARENA_PAGES; page++) {
		if (arena[page * PAGE_BYTES] != pattern(page) ||
		    arena[(page + 1) * PAGE_BYTES - 1] !=
			    (unsigned char)(pattern(page) ^ 0x5aU))
			fail("parent image changed");
	}
}

int main(void)
{
	int channel[2];
	int status = -1;
	unsigned char delivered = 0xa7;

	for (int page = 0; page < ARENA_PAGES; page++) {
		arena[page * PAGE_BYTES] = pattern(page);
		arena[(page + 1) * PAGE_BYTES - 1] =
			(unsigned char)(pattern(page) ^ 0x5aU);
	}
	if (pipe(channel) < 0)
		fail("pipe");
	int pid = fork();
	if (pid < 0)
		fail("fork");
	if (pid == 0) {
		close(channel[1]);
		if (read(channel[0], &arena[5 * PAGE_BYTES + 17], 1) != 1)
			exit(2);
		if (arena[5 * PAGE_BYTES + 17] != delivered)
			exit(3);
		arena[0] ^= 0xffU;
		arena[ARENA_PAGES * PAGE_BYTES - 1] ^= 0xffU;
		if (arena[0] == pattern(0) ||
		    arena[ARENA_PAGES * PAGE_BYTES - 1] ==
			    (unsigned char)(pattern(ARENA_PAGES - 1) ^ 0x5aU))
			exit(4);
		close(channel[0]);
		exit(0);
	}
	close(channel[0]);
	if (write(channel[1], &delivered, 1) != 1)
		fail("pipe write");
	close(channel[1]);
	if (waitpid(pid, &status) != pid || status != 0)
		fail("child isolation");
	check_parent_image();

	long long start = get_mtime();
	for (int round = 0; round < FORK_ROUNDS; round++) {
		pid = fork();
		if (pid < 0)
			fail("fork churn");
		if (pid == 0)
			exit(0);
		status = -1;
		if (waitpid(pid, &status) != pid || status != 0)
			fail("fork churn wait");
	}
	long long elapsed = get_mtime() - start;
	check_parent_image();
	printf("ch8_cow_ucore: pages=%d forks=%d elapsed_ms=%d\n",
	       ARENA_PAGES, FORK_ROUNDS, (int)elapsed);
	printf("ch8_cow_ucore: passed\n");
	return 0;
}

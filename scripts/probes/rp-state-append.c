#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#ifndef O_CREATE
#define O_CREATE O_CREAT
#endif
#ifndef RP_STATE_HEADER
#define RP_STATE_HEADER "../../user/include/research_platform_state.h"
#endif
#include RP_STATE_HEADER

static int write_fixture(const char *path, const char *data, size_t size)
{
	int fd = open(path, O_CREAT | O_WRONLY | O_TRUNC, 0600);
	size_t written = 0;

	if (fd < 0)
		return 0;
	while (written < size) {
		ssize_t n = write(fd, data + written, size - written);

		if (n <= 0) {
			close(fd);
			return 0;
		}
		written += (size_t)n;
	}
	return close(fd) == 0;
}

static int expect_file(const char *path, const char *expected)
{
	char actual[128];
	int fd = open(path, O_RDONLY);
	ssize_t size;

	if (fd < 0)
		return 0;
	size = read(fd, actual, sizeof(actual));
	close(fd);
	if (size < 0 || (size_t)size != strlen(expected) ||
	    memcmp(actual, expected, (size_t)size) != 0) {
		fprintf(stderr, "append probe mismatch: %s\n", path);
		return 0;
	}
	return 1;
}

int main(void)
{
	static const char corrupt[] = "a=1\n\nb=2\n";

	if (!write_fixture("empty", "", 0) ||
	    !rp_append_file("empty", "a=1\n") ||
	    !expect_file("empty", "a=1\n"))
		return 1;
	if (!rp_append_file("empty", "b=2\n") ||
	    !expect_file("empty", "a=1\nb=2\n"))
		return 2;

	if (!write_fixture("unterminated", "a=1", sizeof("a=1") - 1) ||
	    !rp_append_file("unterminated", "b=2\nc=3\n\n") ||
	    !expect_file("unterminated", "a=1\nb=2\nc=3\n"))
		return 3;

	if (!write_fixture("empty-payload", "a=1\n", sizeof("a=1\n") - 1) ||
	    rp_append_file("empty-payload", "") ||
	    rp_append_file("empty-payload", "\n\n") ||
	    !expect_file("empty-payload", "a=1\n"))
		return 4;

	if (!write_fixture("corrupt", "a=1\n\n", sizeof("a=1\n\n") - 1) ||
	    !rp_append_file("corrupt", "b=2") ||
	    !expect_file("corrupt", corrupt))
		return 5;

	return 0;
}

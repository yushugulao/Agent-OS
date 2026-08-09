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

static int rp_probe_open(const char *path, int flags)
{
	return open(path, flags, 0600);
}

#define open rp_probe_open
#include RP_STATE_HEADER
#undef open

#ifdef RP_STATE_SCRATCH_EXTERN
char rp_state_buf[RP_STATE_BUFFER_SIZE];
#endif

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

static int expect_bytes(const char *path, const char *expected, size_t length)
{
	char actual[RP_STATE_BUFFER_SIZE + 2];
	int fd = open(path, O_RDONLY);
	ssize_t size;

	if (fd < 0 || length > sizeof(actual))
		return 0;
	size = read(fd, actual, sizeof(actual));
	close(fd);
	return size == (ssize_t)length && memcmp(actual, expected, length) == 0;
}

static int append_fixture(const char *path, const char *data)
{
	char existing[128];
	int fd = open(path, O_RDWR);
	ssize_t size;

	if (fd < 0)
		return 0;
	do {
		size = read(fd, existing, sizeof(existing));
	} while (size > 0);
	if (size < 0 || write(fd, data, strlen(data)) != (ssize_t)strlen(data)) {
		close(fd);
		return 0;
	}
	return close(fd) == 0;
}

int main(void)
{
	static const char corrupt[] = "a=1\n\nb=2\n";
	static struct rp_state_buffer state;
	char boundary[RP_STATE_BUFFER_SIZE + 1];
	static const char binary[] = {'a', 0, 'b'};

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

	if (!write_fixture("buffer", "a=1\n", sizeof("a=1\n") - 1) ||
	    !rp_state_buffer_begin_append(&state, "buffer") ||
	    !rp_state_buffer_append(&state, "b=2") ||
	    !rp_state_buffer_append(&state, "c=3\n") ||
	    !rp_state_buffer_commit(&state) ||
	    !expect_file("buffer", "a=1\nb=2\nc=3\n"))
		return 6;

	if (!rp_state_buffer_begin_append(&state, "buffer") ||
	    !rp_state_buffer_append(&state, "stale=1") ||
	    !append_fixture("buffer", "external=1\n") ||
	    rp_state_buffer_commit(&state) ||
	    !expect_file("buffer", "a=1\nb=2\nc=3\nexternal=1\n"))
		return 7;

	unlink("created");
	if (!rp_append_file("created", "new=1") ||
	    !expect_file("created", "new=1\n"))
		return 8;

	memset(boundary, 'x', sizeof(boundary));
	if (!write_fixture("capacity-minus-one", boundary,
	                   RP_STATE_BUFFER_SIZE - 1) ||
	    rp_append_file("capacity-minus-one", "y=1") ||
	    !expect_bytes("capacity-minus-one", boundary,
	                  RP_STATE_BUFFER_SIZE - 1))
		return 9;
	if (!write_fixture("capacity", boundary, RP_STATE_BUFFER_SIZE) ||
	    rp_append_file("capacity", "y=1") ||
	    !expect_bytes("capacity", boundary, RP_STATE_BUFFER_SIZE))
		return 10;
	if (!write_fixture("capacity-plus-one", boundary,
	                   RP_STATE_BUFFER_SIZE + 1) ||
	    rp_append_file("capacity-plus-one", "y=1") ||
	    !expect_bytes("capacity-plus-one", boundary,
	                  RP_STATE_BUFFER_SIZE + 1))
		return 11;

	if (!write_fixture("binary", binary, sizeof(binary)) ||
	    rp_append_file("binary", "y=1") ||
	    !expect_bytes("binary", binary, sizeof(binary)))
		return 12;

	return 0;
}

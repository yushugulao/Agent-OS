#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define __RESEARCH_PLATFORM_STATE_H__
#define RP_UNUSED __attribute__((unused))
#define RP_STATE_BUFFER_SIZE 16

static char rp_state_buf[RP_STATE_BUFFER_SIZE];

static void rp_append_text(char *dst, int cap, const char *src)
{
	int used = (int)strlen(dst);

	while (*src != 0 && used + 1 < cap)
		dst[used++] = *src++;
	dst[used] = 0;
}

static void rp_append_uint_text(char *dst, int cap, unsigned long long value)
{
	char reversed[32];
	int digits = 0;

	do {
		reversed[digits++] = (char)('0' + value % 10);
		value /= 10;
	} while (value != 0);
	while (digits > 0) {
		char digit[2] = {reversed[--digits], 0};

		rp_append_text(dst, cap, digit);
	}
}

static int rp_read_file(const char *path, char *buf, int cap)
{
	(void)path;
	(void)buf;
	(void)cap;
	return -1;
}

#include "../../user/include/rp_evidence.h"

#define LONG_UNRELATED_SIZE 4093
#define LONG_TARGET_KEY_SIZE 257

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

static int measure(const char *path, const char *key, const char *value,
		   int expected, const char *data, size_t size, int lines)
{
	struct rp_evidence_file_measurement measured = {
		.bytes = 0x1111111111111111ULL,
		.hash = 0x2222222222222222ULL,
		.lines = 0x33333333,
	};
	int actual = rp_evidence_measure_file_field(path, key, value, &measured);

	if (actual != expected) {
		fprintf(stderr, "field probe mismatch: %s expected=%d actual=%d\n",
			path, expected, actual);
		return 0;
	}
	if (!actual)
		return measured.bytes == 0x1111111111111111ULL &&
		       measured.hash == 0x2222222222222222ULL &&
		       measured.lines == 0x33333333;
	if (measured.bytes != size || measured.lines != lines ||
	    measured.hash != rp_evidence_hash_bytes(
				RP_EVIDENCE_FNV_OFFSET, data, (int)size)) {
		fprintf(stderr, "measurement mismatch: %s bytes=%llu lines=%d hash=%llu\n",
			path, measured.bytes, measured.lines, measured.hash);
		return 0;
	}
	return 1;
}

int main(void)
{
	static const char suffix[] = ";wanted=present\n";
	static const char duplicate[] =
		"wanted=present;unrelated=value;wanted=present\n";
	static const char cr_field[] = "unrelated=value\r;wanted=present\n";
	static const char nul_field[] = {
		'u', 'n', 'r', 'e', 'l', 'a', 't', 'e', 'd', '=', 'v', '\0',
		';', 'w', 'a', 'n', 't', 'e', 'd', '=', 'p', 'r', 'e', 's',
		'e', 'n', 't', '\n',
	};
	static const char inexact[] =
		"xwanted=present;wanted=present-extra\n";
	static const char empty_separators[] =
		"\n;;unrelated=value;;wanted=present\n\n";
	static const char target_at_eof[] =
		"unrelated=value;wanted=present";
	static const char duplicate_at_eof[] =
		"wanted=present;unrelated=value;wanted=present";
	char long_fixture[LONG_UNRELATED_SIZE + sizeof(suffix)];
	char long_key[LONG_TARGET_KEY_SIZE + 1];
	char long_target[LONG_TARGET_KEY_SIZE + sizeof("=value\n")];
	char boundary_target[RP_EVIDENCE_READ_CHUNK_SIZE + sizeof("\n")];
	char long_suffix[LONG_UNRELATED_SIZE + sizeof("wanted=present") - 1];

	memset(long_fixture, 'x', LONG_UNRELATED_SIZE);
	memcpy(long_fixture + LONG_UNRELATED_SIZE, suffix, sizeof(suffix) - 1);
	if (!write_fixture("long-unrelated", long_fixture,
			   LONG_UNRELATED_SIZE + sizeof(suffix) - 1) ||
	    !measure("long-unrelated", "wanted", "present", 1,
		     long_fixture, LONG_UNRELATED_SIZE + sizeof(suffix) - 1, 1))
		return 1;

	memset(long_key, 'k', LONG_TARGET_KEY_SIZE);
	long_key[LONG_TARGET_KEY_SIZE] = 0;
	memcpy(long_target, long_key, LONG_TARGET_KEY_SIZE);
	memcpy(long_target + LONG_TARGET_KEY_SIZE, "=value\n",
	       sizeof("=value\n"));
	if (!write_fixture("long-target", long_target,
			   LONG_TARGET_KEY_SIZE + sizeof("=value\n") - 1) ||
	    !measure("long-target", long_key, "value", 1, long_target,
		     LONG_TARGET_KEY_SIZE + sizeof("=value\n") - 1, 1))
		return 2;

	if (!write_fixture("duplicate", duplicate, sizeof(duplicate) - 1) ||
	    !measure("duplicate", "wanted", "present", 0,
		     duplicate, sizeof(duplicate) - 1, 1))
		return 3;
	if (!write_fixture("carriage-return", cr_field, sizeof(cr_field) - 1) ||
	    !measure("carriage-return", "wanted", "present", 0,
		     cr_field, sizeof(cr_field) - 1, 1))
		return 4;
	if (!write_fixture("nul", nul_field, sizeof(nul_field)) ||
	    !measure("nul", "wanted", "present", 0,
		     nul_field, sizeof(nul_field), 1))
		return 5;
	if (!write_fixture("inexact", inexact, sizeof(inexact) - 1) ||
	    !measure("inexact", "wanted", "present", 0,
		     inexact, sizeof(inexact) - 1, 1))
		return 6;
	if (!write_fixture("empty-separators", empty_separators,
			   sizeof(empty_separators) - 1) ||
	    !measure("empty-separators", "wanted", "present", 1,
		     empty_separators, sizeof(empty_separators) - 1, 3))
		return 7;
	if (!measure("long-unrelated", "", "present", 0,
		     long_fixture, LONG_UNRELATED_SIZE + sizeof(suffix) - 1, 1) ||
	    !measure("long-unrelated", "wanted", "", 0,
		     long_fixture, LONG_UNRELATED_SIZE + sizeof(suffix) - 1, 1))
		return 8;

	if (!write_fixture("target-at-eof", target_at_eof,
			   sizeof(target_at_eof) - 1) ||
	    !measure("target-at-eof", "wanted", "present", 1,
		     target_at_eof, sizeof(target_at_eof) - 1, 1))
		return 9;
	if (!write_fixture("duplicate-at-eof", duplicate_at_eof,
			   sizeof(duplicate_at_eof) - 1) ||
	    !measure("duplicate-at-eof", "wanted", "present", 0,
		     duplicate_at_eof, sizeof(duplicate_at_eof) - 1, 1))
		return 10;

	memset(boundary_target, 'x', 113);
	boundary_target[113] = ';';
	memcpy(boundary_target + 114, "wanted=present\n",
	       sizeof("wanted=present\n"));
	if (!write_fixture("chunk-boundary", boundary_target,
			   sizeof(boundary_target) - 1) ||
	    !measure("chunk-boundary", "wanted", "present", 1,
		     boundary_target, sizeof(boundary_target) - 1, 1))
		return 11;

	memcpy(long_suffix, "wanted=present", sizeof("wanted=present") - 1);
	memset(long_suffix + sizeof("wanted=present") - 1, 'z',
	       LONG_UNRELATED_SIZE);
	if (!write_fixture("long-suffix", long_suffix, sizeof(long_suffix)) ||
	    !measure("long-suffix", "wanted", "present", 0,
		     long_suffix, sizeof(long_suffix), 1))
		return 12;

	return 0;
}

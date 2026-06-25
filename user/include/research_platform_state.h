#ifndef __RESEARCH_PLATFORM_STATE_H__
#define __RESEARCH_PLATFORM_STATE_H__

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define RP_UNUSED __attribute__((unused))
#define RP_STATE_BUFFER_SIZE 8192

static RP_UNUSED char rp_state_buf[RP_STATE_BUFFER_SIZE];

static RP_UNUSED int rp_write_file(const char *path, const char *body)
{
	int fd = open(path, O_WRONLY | O_TRUNC);
	if (fd < 0) {
		fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	}
	if (fd < 0) {
		printf("rp_state: open_write_failed path=%s\n", path);
		return 0;
	}
	int len = (int)strlen(body);
	int wrote = write(fd, body, len);
	close(fd);
	if (wrote != len) {
		printf("rp_state: write_failed path=%s expected=%d actual=%d\n", path, len, wrote);
		return 0;
	}
	return 1;
}

static RP_UNUSED int rp_read_file(const char *path, char *buf, int cap)
{
	if (cap <= 0) return -1;
	int fd = open(path, O_RDONLY);
	if (fd < 0) return -1;
	int n = read(fd, buf, cap - 1);
	close(fd);
	if (n < 0) return -1;
	buf[n] = 0;
	return n;
}

static RP_UNUSED int rp_file_contains(const char *path, const char *needle)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) {
		printf("rp_state: missing path=%s\n", path);
		return 0;
	}
	int needle_len = (int)strlen(needle);
	int buf_len = (int)strlen(buf);
	if (needle_len > buf_len) {
		printf("rp_state: token_missing path=%s token=%s\n", path, needle);
		return 0;
	}
	for (int i = 0; i <= buf_len - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (buf[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) return 1;
	}
	printf("rp_state: token_missing path=%s token=%s\n", path, needle);
	return 0;
}

static RP_UNUSED int rp_count_lines(const char *path)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return -1;
	int count = 0;
	for (int i = 0; i < n; i++) {
		if (buf[i] == '\n') count++;
	}
	return count;
}

static RP_UNUSED int rp_count_token(const char *path, const char *needle)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return -1;
	int count = 0;
	int needle_len = (int)strlen(needle);
	for (int i = 0; i <= n - needle_len; i++) {
		int same = 1;
		for (int j = 0; j < needle_len; j++) {
			if (buf[i + j] != needle[j]) {
				same = 0;
				break;
			}
		}
		if (same) count++;
	}
	return count;
}

static RP_UNUSED int rp_append_file(const char *path, const char *line)
{
	char *buf = rp_state_buf;
	int n = rp_read_file(path, buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) {
		buf[0] = 0;
	}
	int used = (int)strlen(buf);
	int add = (int)strlen(line);
	if (used + add + 2 >= RP_STATE_BUFFER_SIZE) {
		printf("rp_state: append_full path=%s\n", path);
		return 0;
	}
	for (int i = 0; i < add; i++) {
		buf[used + i] = line[i];
	}
	buf[used + add] = '\n';
	buf[used + add + 1] = 0;
	return rp_write_file(path, buf);
}

static RP_UNUSED int rp_append_status(const char *line)
{
	return rp_append_file("rp_status", line);
}

#endif

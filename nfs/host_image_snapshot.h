#ifndef __HOST_IMAGE_SNAPSHOT_H__
#define __HOST_IMAGE_SNAPSHOT_H__

#include <stddef.h>
#include <stdint.h>

enum host_snapshot_status {
	HOST_SNAPSHOT_OK = 0,
	HOST_SNAPSHOT_BAD_ARGUMENT,
	HOST_SNAPSHOT_NOT_FOUND,
	HOST_SNAPSHOT_OPEN_ERROR,
	HOST_SNAPSHOT_NOT_REGULAR,
	HOST_SNAPSHOT_EMPTY,
	HOST_SNAPSHOT_TOO_LARGE,
	HOST_SNAPSHOT_NO_MEMORY,
	HOST_SNAPSHOT_READ_ERROR,
	HOST_SNAPSHOT_CHANGED,
	HOST_SNAPSHOT_CLOSE_ERROR,
};

struct host_snapshot_fingerprint {
	uint64_t device;
	uint64_t inode;
	uint64_t size;
	uint64_t mtime_seconds;
	uint64_t ctime_seconds;
	uint32_t mode;
	uint32_t links;
	uint32_t uid;
	uint32_t gid;
	uint32_t mtime_nanoseconds;
	uint32_t ctime_nanoseconds;
};

struct host_file_snapshot {
	unsigned char *data;
	size_t size;
	struct host_snapshot_fingerprint fingerprint;
};

/* A zero limit means that only the host size_t limit applies. */
enum host_snapshot_status host_snapshot_read(
	const char *path, size_t limit, struct host_file_snapshot *snapshot,
	int *host_error);
enum host_snapshot_status host_snapshot_validate_path(
	const char *path, const struct host_file_snapshot *snapshot,
	int *host_error);
void host_snapshot_release(struct host_file_snapshot *snapshot);
const char *host_snapshot_status_string(enum host_snapshot_status status);

#endif

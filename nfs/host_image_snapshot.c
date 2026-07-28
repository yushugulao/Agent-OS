#define _POSIX_C_SOURCE 200809L

#include "host_image_snapshot.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif

static void snapshot_set_error(int *host_error, int value)
{
	if (host_error != 0)
		*host_error = value;
}

static void snapshot_fingerprint(const struct stat *st,
				 struct host_snapshot_fingerprint *fingerprint)
{
	fingerprint->device = (uint64_t)st->st_dev;
	fingerprint->inode = (uint64_t)st->st_ino;
	fingerprint->size = (uint64_t)st->st_size;
	fingerprint->mtime_seconds = (uint64_t)st->st_mtim.tv_sec;
	fingerprint->ctime_seconds = (uint64_t)st->st_ctim.tv_sec;
	fingerprint->mode = (uint32_t)st->st_mode;
	fingerprint->links = (uint32_t)st->st_nlink;
	fingerprint->uid = (uint32_t)st->st_uid;
	fingerprint->gid = (uint32_t)st->st_gid;
	fingerprint->mtime_nanoseconds = (uint32_t)st->st_mtim.tv_nsec;
	fingerprint->ctime_nanoseconds = (uint32_t)st->st_ctim.tv_nsec;
}

static int snapshot_fingerprint_same(
	const struct host_snapshot_fingerprint *left,
	const struct host_snapshot_fingerprint *right)
{
	return left->device == right->device && left->inode == right->inode &&
	       left->size == right->size && left->mode == right->mode &&
	       left->links == right->links && left->uid == right->uid &&
	       left->gid == right->gid &&
	       left->mtime_seconds == right->mtime_seconds &&
	       left->mtime_nanoseconds == right->mtime_nanoseconds &&
	       left->ctime_seconds == right->ctime_seconds &&
	       left->ctime_nanoseconds == right->ctime_nanoseconds;
}

static enum host_snapshot_status snapshot_path_fingerprint(
	const char *path, struct host_snapshot_fingerprint *fingerprint,
	int *host_error)
{
	struct stat st;

	if (lstat(path, &st) < 0) {
		int error = errno;

		snapshot_set_error(host_error, error);
		return error == ENOENT ? HOST_SNAPSHOT_NOT_FOUND :
					 HOST_SNAPSHOT_OPEN_ERROR;
	}
	if (!S_ISREG(st.st_mode))
		return HOST_SNAPSHOT_NOT_REGULAR;
	snapshot_fingerprint(&st, fingerprint);
	return HOST_SNAPSHOT_OK;
}

static enum host_snapshot_status snapshot_read_exact(
	int fd, unsigned char *data, size_t size, int *host_error)
{
	size_t done = 0;

	while (done < size) {
		ssize_t count = pread(fd, data + done, size - done, (off_t)done);

		if (count < 0 && errno == EINTR)
			continue;
		if (count < 0) {
			snapshot_set_error(host_error, errno);
			return HOST_SNAPSHOT_READ_ERROR;
		}
		if (count == 0)
			return HOST_SNAPSHOT_CHANGED;
		done += (size_t)count;
	}
	return HOST_SNAPSHOT_OK;
}

#ifdef HOST_SNAPSHOT_TESTING
/* The Host regression harness mutates the open inode between both reads. */
extern void host_snapshot_test_between_reads(int fd, const char *path);
#endif

enum host_snapshot_status host_snapshot_read(
	const char *path, size_t limit, struct host_file_snapshot *snapshot,
	int *host_error)
{
	struct host_snapshot_fingerprint path_before;
	struct host_snapshot_fingerprint fd_before;
	struct host_snapshot_fingerprint fd_middle;
	struct host_snapshot_fingerprint fd_after;
	struct host_snapshot_fingerprint path_after;
	struct stat st;
	unsigned char *first = 0;
	unsigned char *second = 0;
	enum host_snapshot_status status;
	int fd = -1;

	if (snapshot != 0)
		memset(snapshot, 0, sizeof(*snapshot));
	snapshot_set_error(host_error, 0);
	if (path == 0 || path[0] == 0 || snapshot == 0)
		return HOST_SNAPSHOT_BAD_ARGUMENT;
	status = snapshot_path_fingerprint(path, &path_before, host_error);
	if (status != HOST_SNAPSHOT_OK)
		return status;
	fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0) {
		int error = errno;

		snapshot_set_error(host_error, error);
		return error == ENOENT || error == ELOOP ? HOST_SNAPSHOT_CHANGED :
						      HOST_SNAPSHOT_OPEN_ERROR;
	}
	if (fstat(fd, &st) < 0) {
		snapshot_set_error(host_error, errno);
		status = HOST_SNAPSHOT_OPEN_ERROR;
		goto out;
	}
	if (!S_ISREG(st.st_mode)) {
		status = HOST_SNAPSHOT_NOT_REGULAR;
		goto out;
	}
	snapshot_fingerprint(&st, &fd_before);
	if (!snapshot_fingerprint_same(&path_before, &fd_before)) {
		status = HOST_SNAPSHOT_CHANGED;
		goto out;
	}
	if (st.st_size <= 0) {
		status = HOST_SNAPSHOT_EMPTY;
		goto out;
	}
	if ((uintmax_t)st.st_size > (uintmax_t)SIZE_MAX) {
		status = HOST_SNAPSHOT_TOO_LARGE;
		goto out;
	}
	snapshot->size = (size_t)st.st_size;
	if (limit != 0 && snapshot->size > limit) {
		status = HOST_SNAPSHOT_TOO_LARGE;
		goto out;
	}
	first = malloc(snapshot->size);
	second = malloc(snapshot->size);
	if (first == 0 || second == 0) {
		status = HOST_SNAPSHOT_NO_MEMORY;
		goto out;
	}
	status = snapshot_read_exact(fd, first, snapshot->size, host_error);
	if (status != HOST_SNAPSHOT_OK)
		goto out;
#ifdef HOST_SNAPSHOT_TESTING
	host_snapshot_test_between_reads(fd, path);
#endif
	if (fstat(fd, &st) < 0) {
		snapshot_set_error(host_error, errno);
		status = HOST_SNAPSHOT_READ_ERROR;
		goto out;
	}
	snapshot_fingerprint(&st, &fd_middle);
	status = snapshot_read_exact(fd, second, snapshot->size, host_error);
	if (status != HOST_SNAPSHOT_OK)
		goto out;
	if (fstat(fd, &st) < 0) {
		snapshot_set_error(host_error, errno);
		status = HOST_SNAPSHOT_READ_ERROR;
		goto out;
	}
	snapshot_fingerprint(&st, &fd_after);
	status = snapshot_path_fingerprint(path, &path_after, host_error);
	if (status != HOST_SNAPSHOT_OK) {
		if (status == HOST_SNAPSHOT_NOT_FOUND ||
		    status == HOST_SNAPSHOT_NOT_REGULAR)
			status = HOST_SNAPSHOT_CHANGED;
		goto out;
	}
	/* HOST_SNAPSHOT_STABILITY_GUARD */
	if (!snapshot_fingerprint_same(&fd_before, &fd_middle) ||
	    !snapshot_fingerprint_same(&fd_before, &fd_after) ||
	    !snapshot_fingerprint_same(&fd_before, &path_after) ||
	    memcmp(first, second, snapshot->size) != 0) {
		status = HOST_SNAPSHOT_CHANGED;
		goto out;
	}
	status = HOST_SNAPSHOT_OK;
	snapshot->data = first;
	snapshot->fingerprint = fd_before;
	first = 0;

out:
	free(first);
	free(second);
	if (close(fd) < 0 && status == HOST_SNAPSHOT_OK) {
		snapshot_set_error(host_error, errno);
		host_snapshot_release(snapshot);
		return HOST_SNAPSHOT_CLOSE_ERROR;
	}
	if (status != HOST_SNAPSHOT_OK)
		snapshot->data = 0;
	return status;
}

enum host_snapshot_status host_snapshot_validate_path(
	const char *path, const struct host_file_snapshot *snapshot,
	int *host_error)
{
	struct host_file_snapshot current = { 0 };
	enum host_snapshot_status status;
	int unchanged;

	snapshot_set_error(host_error, 0);
	if (path == 0 || path[0] == 0 || snapshot == 0 ||
	    snapshot->data == 0 || snapshot->size == 0)
		return HOST_SNAPSHOT_BAD_ARGUMENT;
	/*
	 * Metadata timestamps may be coarser than a build.  Re-snapshot the path
	 * so a same-size rewrite cannot masquerade as the verified input.
	 */
	status = host_snapshot_read(path, snapshot->size, &current, host_error);
	if (status != HOST_SNAPSHOT_OK) {
		if (status == HOST_SNAPSHOT_NOT_FOUND ||
		    status == HOST_SNAPSHOT_NOT_REGULAR ||
		    status == HOST_SNAPSHOT_EMPTY ||
		    status == HOST_SNAPSHOT_TOO_LARGE)
			return HOST_SNAPSHOT_CHANGED;
		return status;
	}
	unchanged = snapshot_fingerprint_same(&snapshot->fingerprint,
					      &current.fingerprint) &&
		    snapshot->size == current.size &&
		    memcmp(snapshot->data, current.data, snapshot->size) == 0;
	host_snapshot_release(&current);
	return unchanged ? HOST_SNAPSHOT_OK : HOST_SNAPSHOT_CHANGED;
}

void host_snapshot_release(struct host_file_snapshot *snapshot)
{
	if (snapshot == 0)
		return;
	free(snapshot->data);
	memset(snapshot, 0, sizeof(*snapshot));
}

const char *host_snapshot_status_string(enum host_snapshot_status status)
{
	switch (status) {
	case HOST_SNAPSHOT_OK:
		return "ok";
	case HOST_SNAPSHOT_BAD_ARGUMENT:
		return "invalid argument";
	case HOST_SNAPSHOT_NOT_FOUND:
		return "not found";
	case HOST_SNAPSHOT_OPEN_ERROR:
		return "open or stat failed";
	case HOST_SNAPSHOT_NOT_REGULAR:
		return "not a regular file";
	case HOST_SNAPSHOT_EMPTY:
		return "empty file";
	case HOST_SNAPSHOT_TOO_LARGE:
		return "size limit exceeded";
	case HOST_SNAPSHOT_NO_MEMORY:
		return "out of host memory";
	case HOST_SNAPSHOT_READ_ERROR:
		return "read failed";
	case HOST_SNAPSHOT_CHANGED:
		return "path or contents changed while reading";
	case HOST_SNAPSHOT_CLOSE_ERROR:
		return "close failed";
	default:
		return "unknown error";
	}
}

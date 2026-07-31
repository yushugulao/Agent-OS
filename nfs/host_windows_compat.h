#ifndef __NFS_HOST_WINDOWS_COMPAT_H__
#define __NFS_HOST_WINDOWS_COMPAT_H__

#ifdef _WIN32

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <errno.h>
#include <fcntl.h>
#include <io.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <windows.h>

#ifndef S_IFLNK
#define S_IFLNK 0120000
#endif

static inline int host_windows_errno(DWORD error)
{
	switch (error) {
	case ERROR_FILE_NOT_FOUND:
	case ERROR_PATH_NOT_FOUND:
		return ENOENT;
	case ERROR_ACCESS_DENIED:
		return EACCES;
	default:
		return EIO;
	}
}

static inline int host_windows_lstat(const char *path, struct stat *st)
{
	DWORD attributes = GetFileAttributesA(path);

	if (attributes == INVALID_FILE_ATTRIBUTES) {
		errno = host_windows_errno(GetLastError());
		return -1;
	}
	if ((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
		memset(st, 0, sizeof(*st));
		st->st_mode = S_IFLNK;
		return 0;
	}
	return stat(path, st);
}

static inline ssize_t host_windows_pread(int fd, void *data, size_t size,
					 off_t offset)
{
	__int64 saved = _lseeki64(fd, 0, SEEK_CUR);
	int result;
	int saved_errno;

	if (saved < 0 || _lseeki64(fd, (__int64)offset, SEEK_SET) < 0)
		return -1;
	result = _read(fd, data, size > INT_MAX ? INT_MAX : (unsigned int)size);
	saved_errno = errno;
	if (_lseeki64(fd, saved, SEEK_SET) < 0 && result >= 0)
		return -1;
	errno = saved_errno;
	return result;
}

static inline ssize_t host_windows_pwrite(int fd, const void *data, size_t size,
					  off_t offset)
{
	__int64 saved = _lseeki64(fd, 0, SEEK_CUR);
	int result;
	int saved_errno;

	if (saved < 0 || _lseeki64(fd, (__int64)offset, SEEK_SET) < 0)
		return -1;
	result = _write(fd, data, size > INT_MAX ? INT_MAX : (unsigned int)size);
	saved_errno = errno;
	if (_lseeki64(fd, saved, SEEK_SET) < 0 && result >= 0)
		return -1;
	errno = saved_errno;
	return result;
}

#define lstat host_windows_lstat
#define pread host_windows_pread
#define pwrite host_windows_pwrite
#define fsync _commit
#define bzero(data, size) memset((data), 0, (size))
#define bcopy(source, target, size) memmove((target), (source), (size))
#define index strchr

#endif

#ifndef O_BINARY
#define O_BINARY 0
#endif

#endif

#define __STDDEF_H__
#define __STDIO_H__
#define __STRING_H__
#define __UNISTD_H__
#define __STDLIB_H__

typedef __SIZE_TYPE__ size_t;
typedef long long ssize_t;
typedef unsigned long long uint64;
typedef __builtin_va_list va_list;

#define va_start(ap, last) __builtin_va_start(ap, last)
#define va_arg(ap, type) __builtin_va_arg(ap, type)
#define va_end(ap) __builtin_va_end(ap)
#define stdin 0
#define stdout 1
#define EOF (-1)
#define assert(condition) ((void)sizeof(condition))

static ssize_t probe_read(int fd, void *data, size_t size);
static ssize_t probe_write(int fd, const void *data, size_t size);
static int probe_mutex_create(void);
static int probe_mutex_lock(int mutex);
static int probe_mutex_unlock(int mutex);
static size_t probe_strlen(const char *text);
static size_t probe_strnlen(const char *text, size_t limit);

#define read probe_read
#define write probe_write
#define mutex_create probe_mutex_create
#define mutex_lock probe_mutex_lock
#define mutex_unlock probe_mutex_unlock
#define strlen probe_strlen
#define strnlen probe_strnlen
#define getchar user_getchar
#define putchar user_putchar
#define puts user_puts
#define printf user_printf
#define fflush user_fflush
#define enable_thread_io_buffer user_enable_thread_io_buffer
#define buffer_lock_enabled user_buffer_lock_enabled
#define __write_buffer user_write_buffer
#define __clear_buffer user_clear_buffer
#define __fflush user_internal_fflush

#include "../../user/lib/stdio.c"

_Static_assert(sizeof(int) == 4, "printf probe requires 32-bit int");
_Static_assert(sizeof(long) == 8, "printf probe requires LP64 long");
_Static_assert(sizeof(long long) == 8, "printf probe requires 64-bit long long");

static char captured[2048];
static size_t captured_count;
static int failed;
static int write_calls;
static int write_fail_call;
static size_t write_limit;
static char full_input[256];

static ssize_t probe_read(int fd, void *data, size_t size)
{
	(void)fd;
	(void)data;
	(void)size;
	return 0;
}

static ssize_t probe_write(int fd, const void *data, size_t size)
{
	const char *bytes = data;
	size_t accepted = size;

	write_calls++;
	if (write_calls == write_fail_call)
		return -1;
	if (write_limit != 0 && accepted > write_limit)
		accepted = write_limit;
	if (fd != stdout || accepted > sizeof(captured) - captured_count) {
		failed = 1;
		return -1;
	}
	for (size_t i = 0; i < accepted; i++)
		captured[captured_count++] = bytes[i];
	return accepted;
}

static int probe_mutex_create(void) { return 1; }
static int probe_mutex_lock(int mutex) { return mutex == 1 ? 0 : -1; }
static int probe_mutex_unlock(int mutex) { return mutex == 1 ? 0 : -1; }

static size_t probe_strlen(const char *text)
{
	size_t length = 0;
	while (text[length] != 0)
		length++;
	return length;
}

static size_t probe_strnlen(const char *text, size_t limit)
{
	size_t length = 0;
	while (length < limit && text[length] != 0)
		length++;
	return length;
}

static int same(const char *expected)
{
	size_t i = 0;

	if (user_fflush(stdout) < 0)
		return 0;
	while (expected[i] != 0) {
		if (i >= captured_count || captured[i] != expected[i])
			return 0;
		i++;
	}
	return i == captured_count && !failed;
}

static void reset(void)
{
	(void)user_fflush(stdout);
	captured_count = 0;
	failed = 0;
	write_calls = 0;
	write_fail_call = 0;
	write_limit = 0;
}

int main(void)
{
	reset();
	user_printf("%d|%u|%x|%ld|%lu|%lx|%lld|%llu|%llx|%p|%s|%%",
		    (-2147483647 - 1), ~0U, ~0U,
		    (-9223372036854775807L - 1L), ~0UL, ~0UL,
		    (-9223372036854775807LL - 1LL), ~0ULL, ~0ULL,
		    (uint64)0x123, "ok");
	if (!same("-2147483648|4294967295|ffffffff|"
		  "-9223372036854775808|18446744073709551615|"
		  "ffffffffffffffff|-9223372036854775808|"
		  "18446744073709551615|ffffffffffffffff|"
		  "0x0000000000000123|ok|%"))
		return 1;

	reset();
	user_printf("%u|%llu|%d|%x|%s", 17U, 4294967296ULL, -3,
		    0xfeedU, (char *)0);
	if (!same("17|4294967296|-3|feed|(null)"))
		return 2;

	reset();
	user_printf("%q|%d|%l%s|%llq|end%", 7, "ok");
	if (!same("%q|7|%lok|%llq|end"))
		return 3;

	reset();
	write_limit = 3;
	write_fail_call = 2;
	user_printf("partial\n");
	if (captured_count != 3 || captured[0] != 'p' || captured[1] != 'a' ||
	    captured[2] != 'r' || buffer_len != 5 || buffer[0] != 't' ||
	    buffer[1] != 'i' || buffer[2] != 'a' || buffer[3] != 'l' ||
	    buffer[4] != '\n')
		return 4;
	write_fail_call = 0;
	write_limit = 2;
	if (user_fflush(stdout) < 0 || !same("partial\n") || buffer_len != 0)
		return 5;

	reset();
	for (size_t i = 0; i < sizeof(full_input); i++)
		full_input[i] = 'A';
	write_fail_call = 1;
	if (out_unlocked(full_input, sizeof(full_input)) >= 0 ||
	    buffer_len != (int)sizeof(full_input))
		return 6;
	write_fail_call = 0;
	write_limit = 64;
	if (out_unlocked("B", 1) < 0 || buffer_len != 1 || buffer[0] != 'B' ||
	    user_fflush(stdout) < 0 || captured_count != sizeof(full_input) + 1)
		return 7;
	for (size_t i = 0; i < sizeof(full_input); i++)
		if (captured[i] != 'A')
			return 8;
	if (captured[sizeof(full_input)] != 'B')
		return 9;
	return 0;
}
